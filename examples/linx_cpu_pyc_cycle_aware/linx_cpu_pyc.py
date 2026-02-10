# -*- coding: utf-8 -*-
"""LinxISA CPU — true 5-stage parallel pipeline with unified signal model.

所有信号通过 domain.signal() 创建，通过 .set() 驱动。
- reset=N → flop（DFF），声明时为 Q 输出
- reset=None → wire（组合逻辑 / 反馈占位）
无需 named_wire、CycleAwareReg、m.assign。

Pipeline stages:
  cycle 0: IF  — instruction fetch
  cycle 1: ID  — instruction decode, register read, forwarding, hazard detection
  cycle 2: EX  — execute ALU / address compute
  cycle 3: MEM — data memory access
  cycle 4: WB  — writeback, branch resolution

反馈信号：后级信号（cycle > current）在前级使用时，
框架的 _balance_cycles 自动识别为 feedback 并直接引用（不插 DFF）。
"""
from __future__ import annotations

import os

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareSignal,
    compile_cycle_aware,
    mux,
)

from examples.linx_cpu_pyc.isa import (
    BK_FALL,
    OP_BSTART_STD_CALL,
    OP_C_BSTART_COND,
    OP_C_BSTART_STD,
    OP_C_LWI,
    OP_EBREAK,
    OP_INVALID,
    OP_LWI,
    REG_INVALID,
)
from examples.linx_cpu_pyc_cycle_aware.decode import decode_window
from examples.linx_cpu_pyc_cycle_aware.memory import build_byte_mem
from examples.linx_cpu_pyc_cycle_aware.pipeline import CoreState, RegFiles
from examples.linx_cpu_pyc_cycle_aware.regfile import make_gpr, make_regs, read_reg
from examples.linx_cpu_pyc_cycle_aware.stages.ex_stage import ex_stage_logic
from examples.linx_cpu_pyc_cycle_aware.stages.mem_stage import mem_stage_logic
from examples.linx_cpu_pyc_cycle_aware.stages.wb_stage import wb_stage_updates
from examples.linx_cpu_pyc_cycle_aware.util import make_consts


def _linx_cpu_impl(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    mem_bytes: int,
) -> None:
    # ==================================================================
    # Cycle 0 — 输入信号
    # ==================================================================
    boot_pc = domain.input("boot_pc", width=64)
    boot_sp = domain.input("boot_sp", width=64)

    # ==================================================================
    # 声明所有 flop 信号（pipeline regs + architectural state）
    #   domain.signal(reset=N) → flop，声明时为 Q 输出
    # ==================================================================
    # --- Cycle 0: fetch_pc ---
    fetch_pc = domain.signal("fetch_pc", width=64, reset=0)

    domain.push()  # save cycle 0

    domain.next()  # → cycle 1
    # --- IF/ID pipeline regs (Q at cycle 1, consumed by ID stage) ---
    ifid_window_r = domain.signal("ifid_window", width=64, reset=0)
    ifid_pc_r = domain.signal("ifid_pc", width=64, reset=0)
    valid_id_r = domain.signal("valid_id", width=1, reset=0)

    domain.next()  # → cycle 2
    # --- ID/EX pipeline regs (Q at cycle 2, consumed by EX stage) ---
    idex_pc_r = domain.signal("idex_pc", width=64, reset=0)
    idex_op_r = domain.signal("idex_op", width=6, reset=OP_INVALID)
    idex_len_bytes_r = domain.signal("idex_len_bytes", width=3, reset=0)
    idex_regdst_r = domain.signal("idex_regdst", width=6, reset=REG_INVALID)
    idex_srcl_val_r = domain.signal("idex_srcl_val", width=64, reset=0)
    idex_srcr_val_r = domain.signal("idex_srcr_val", width=64, reset=0)
    idex_srcp_val_r = domain.signal("idex_srcp_val", width=64, reset=0)
    idex_imm_r = domain.signal("idex_imm", width=64, reset=0)
    valid_ex_r = domain.signal("valid_ex", width=1, reset=0)

    domain.next()  # → cycle 3
    # --- EX/MEM pipeline regs (Q at cycle 3, consumed by MEM stage) ---
    exmem_op_r = domain.signal("exmem_op", width=6, reset=OP_INVALID)
    exmem_len_bytes_r = domain.signal("exmem_len_bytes", width=3, reset=0)
    exmem_pc_r = domain.signal("exmem_pc", width=64, reset=0)
    exmem_regdst_r = domain.signal("exmem_regdst", width=6, reset=REG_INVALID)
    exmem_alu_r = domain.signal("exmem_alu", width=64, reset=0)
    exmem_is_load_r = domain.signal("exmem_is_load", width=1, reset=0)
    exmem_is_store_r = domain.signal("exmem_is_store", width=1, reset=0)
    exmem_size_r = domain.signal("exmem_size", width=3, reset=0)
    exmem_addr_r = domain.signal("exmem_addr", width=64, reset=0)
    exmem_wdata_r = domain.signal("exmem_wdata", width=64, reset=0)
    valid_mem_r = domain.signal("valid_mem", width=1, reset=0)

    domain.next()  # → cycle 4
    # --- MEM/WB pipeline regs (Q at cycle 4, consumed by WB stage) ---
    memwb_op_r = domain.signal("memwb_op", width=6, reset=OP_INVALID)
    memwb_len_bytes_r = domain.signal("memwb_len_bytes", width=3, reset=0)
    memwb_pc_r = domain.signal("memwb_pc", width=64, reset=0)
    memwb_regdst_r = domain.signal("memwb_regdst", width=6, reset=REG_INVALID)
    memwb_value_r = domain.signal("memwb_value", width=64, reset=0)
    valid_wb_r = domain.signal("valid_wb", width=1, reset=0)

    # --- CoreState (cycle=4, read/written in WB stage) ---
    state = CoreState(
        stage=domain.signal("state_stage", width=3, reset=0),
        pc=domain.signal("state_pc", width=64, reset=0),
        br_kind=domain.signal("state_br_kind", width=2, reset=BK_FALL),
        br_base_pc=domain.signal("state_br_base_pc", width=64, reset=0),
        br_off=domain.signal("state_br_off", width=64, reset=0),
        commit_cond=domain.signal("state_commit_cond", width=1, reset=0),
        commit_tgt=domain.signal("state_commit_tgt", width=64, reset=0),
        cycles=domain.signal("state_cycles", width=64, reset=0),
        halted=domain.signal("state_halted", width=1, reset=0),
    )

    # --- GPR / T / U (cycle=4: written in WB, read in ID via feedback) ---
    gpr = make_gpr(m, domain, boot_sp=boot_sp)
    t = make_regs(m, domain, count=4, width=64, init=0)
    u = make_regs(m, domain, count=4, width=64, init=0)
    rf = RegFiles(gpr=gpr, t=t, u=u)

    domain.pop()  # → back to cycle 0

    # ==================================================================
    # 反馈信号声明 (wire, 用 domain.signal() 在目标 cycle 创建)
    #   cycle 标注 = 信号被生产的逻辑 cycle
    #   framework: signal.cycle >= domain.current_cycle → 直接引用
    # ==================================================================

    def _fb(name: str, width: int, cycle: int) -> CycleAwareSignal:
        """创建一个 feedback wire 信号。"""
        domain.push()
        domain._current_cycle = cycle
        s = domain.signal(name, width=width)
        domain.pop()
        return s

    # Control (WB → all, cycle=4)
    fb_flush = _fb("fb_flush", 1, cycle=4)
    fb_redirect_pc = _fb("fb_redirect_pc", 64, cycle=4)
    fb_stop = _fb("fb_stop", 1, cycle=4)
    fb_do_wb_arch = _fb("fb_do_wb_arch", 1, cycle=4)

    # Forwarding data (EX → ID, MEM → ID)
    fb_ex_alu = _fb("fb_ex_alu", 64, cycle=2)
    fb_mem_value = _fb("fb_mem_value", 64, cycle=3)

    # Stall sources (各级 → stall chain at cycle 0)
    fb_stall_id = _fb("fb_stall_id", 1, cycle=1)
    fb_stall_ex = _fb("fb_stall_ex", 1, cycle=2)
    fb_stall_mem = _fb("fb_stall_mem", 1, cycle=3)
    fb_stall_wb = _fb("fb_stall_wb", 1, cycle=4)

    # Freeze chain (stall chain → 各前级)
    fb_freeze_if = _fb("fb_freeze_if", 1, cycle=0)
    fb_freeze_id = _fb("fb_freeze_id", 1, cycle=1)
    fb_freeze_ex = _fb("fb_freeze_ex", 1, cycle=2)
    fb_freeze_mem = _fb("fb_freeze_mem", 1, cycle=3)

    # ==================================================================
    # Boot 初始化 (.set 内部用 raw sig，不触发 balance)
    # ==================================================================
    is_first = state.cycles.eq(0)
    state.pc.set(boot_pc, when=is_first)
    state.br_base_pc.set(boot_pc, when=is_first)
    gpr[1].set(boot_sp, when=is_first)

    # ==================================================================
    # Cycle 0 — Stall chain + IF stage
    # ==================================================================
    c = lambda v, w: domain.const(v, width=w)

    # 驱动暂未使用的 stall 源为 0
    fb_stall_ex.set(0)
    fb_stall_mem.set(0)
    fb_stall_wb.set(0)

    # 累积停顿链（所有 fb_stall_* cycle > 0 → feedback at cycle 0）
    cum_stall_mem = fb_stall_wb
    cum_stall_ex = fb_stall_mem | cum_stall_mem
    cum_stall_id = fb_stall_ex | cum_stall_ex
    cum_stall_if = fb_stall_id | cum_stall_id
    fb_freeze_if.set(cum_stall_if & ~fb_flush)
    fb_freeze_id.set(cum_stall_id & ~fb_flush)
    fb_freeze_ex.set(cum_stall_ex & ~fb_flush)
    fb_freeze_mem.set(cum_stall_mem & ~fb_flush)

    # --- IF stage ---
    current_fetch_pc = mux(is_first, boot_pc, fetch_pc)
    imem_rdata = build_byte_mem(
        m, domain,
        raddr=current_fetch_pc,
        wvalid=c(0, 1), waddr=c(0, 64), wdata=c(0, 64), wstrb=c(0, 8),
        depth_bytes=mem_bytes, name="imem",
    )
    window = imem_rdata

    # 快速指令长度解码
    low4 = window.trunc(width=4)
    is_hl = low4.eq(0xE)
    bit0 = window[0]
    quick_len = mux(is_hl, c(6, 3), mux(bit0 & ~is_hl, c(4, 3), c(2, 3)))

    next_pc_seq = current_fetch_pc + quick_len.zext(width=64)
    next_pc = mux(fb_flush, fb_redirect_pc, next_pc_seq)
    valid_if = ~fb_stop & ~fb_flush & ~fb_freeze_if

    # --- 写 IF 级流水线寄存器 ---
    fetch_pc.set(fetch_pc)                                   # 默认：保持
    fetch_pc.set(next_pc, when=~fb_stop & ~fb_freeze_if)     # 正常推进

    ifid_window_r.set(ifid_window_r)                         # 默认：保持
    ifid_window_r.set(window, when=~fb_freeze_if)
    ifid_pc_r.set(ifid_pc_r)
    ifid_pc_r.set(current_fetch_pc, when=~fb_freeze_if)
    valid_id_r.set(valid_id_r)
    valid_id_r.set(valid_if, when=~fb_freeze_if)

    # ==================================================================
    domain.next()  # → cycle 1
    # ==================================================================
    # Cycle 1 — ID stage
    # ==================================================================
    c = lambda v, w: domain.const(v, width=w)

    # IF/ID reg Q at cycle 1 (current → no DFF insertion)
    ifid_window = ifid_window_r
    ifid_pc = ifid_pc_r
    valid_id_raw = valid_id_r
    valid_id = valid_id_raw & ~fb_flush   # fb_flush cycle=4 → feedback

    # --- 译码 ---
    dec = decode_window(m, ifid_window)
    op_id = dec.op
    len_bytes_id = dec.len_bytes
    regdst_id = dec.regdst
    srcl, srcr, srcp = dec.srcl, dec.srcr, dec.srcp
    imm_id = dec.imm

    # --- 寄存器堆读取 (gpr/t/u cycle=4 → feedback at cycle 1) ---
    srcl_val_rf = read_reg(m, srcl, gpr=rf.gpr, t=rf.t, u=rf.u, default=c(0, 64))
    srcr_val_rf = read_reg(m, srcr, gpr=rf.gpr, t=rf.t, u=rf.u, default=c(0, 64))
    srcp_val_rf = read_reg(m, srcp, gpr=rf.gpr, t=rf.t, u=rf.u, default=c(0, 64))

    # --- 前递（优先级：EX > MEM > WB > RF）---
    valid_ex_eff = valid_ex_r & ~fb_flush
    valid_mem_eff = valid_mem_r & ~fb_flush

    ex_is_load = idex_op_r.eq(c(OP_LWI, 6)) | idex_op_r.eq(c(OP_C_LWI, 6))
    fwd_ex_ok = valid_ex_eff & idex_regdst_r.ne(c(REG_INVALID, 6)) & (~ex_is_load)
    fwd_mem_ok = valid_mem_eff & exmem_regdst_r.ne(c(REG_INVALID, 6))

    # WB forwarding: pipeline reg Q at cycle 4 → feedback
    wb_op_fb = memwb_op_r
    wb_pc_fb = memwb_pc_r
    wb_regdst_fb = memwb_regdst_r
    wb_value_fb = memwb_value_r
    valid_wb_fb = valid_wb_r
    wb_valid_fb = wb_op_fb.ne(c(OP_INVALID, 6)) & wb_pc_fb.ne(c(0, 64))
    do_wb_arch_local = valid_wb_fb & wb_valid_fb
    fwd_wb_ok = do_wb_arch_local & wb_regdst_fb.ne(c(REG_INVALID, 6))

    def fwd(src, rf_val):
        """对单个源寄存器应用前递链。"""
        v = rf_val
        v = mux(fwd_wb_ok & src.eq(wb_regdst_fb), wb_value_fb, v)
        v = mux(fwd_mem_ok & src.eq(exmem_regdst_r), fb_mem_value, v)
        v = mux(fwd_ex_ok & src.eq(idex_regdst_r), fb_ex_alu, v)
        return v

    srcl_val = fwd(srcl, srcl_val_rf)
    srcr_val = fwd(srcr, srcr_val_rf)
    srcp_val = fwd(srcp, srcp_val_rf)

    # --- Load-use 互锁：EX 为 load 且 ID 需要其结果 → stall 1 拍 ---
    load_use = valid_ex_eff & ex_is_load & idex_regdst_r.ne(c(REG_INVALID, 6)) & (
        srcl.eq(idex_regdst_r) | srcr.eq(idex_regdst_r) | srcp.eq(idex_regdst_r))

    # --- T/U 栈 hazard ---
    def _writes_tu(rd, op):
        return (rd.eq(c(30, 6)) | rd.eq(c(31, 6)) |
                op.eq(c(OP_C_LWI, 6)) |
                op.eq(c(OP_C_BSTART_STD, 6)) |
                op.eq(c(OP_C_BSTART_COND, 6)) |
                op.eq(c(OP_BSTART_STD_CALL, 6)))

    inflight_tu_write = (
        (valid_ex_eff & _writes_tu(idex_regdst_r, idex_op_r)) |
        (valid_mem_eff & _writes_tu(exmem_regdst_r, exmem_op_r)) |
        (do_wb_arch_local & _writes_tu(wb_regdst_fb, wb_op_fb))
    )

    def _is_tu(s):
        return (s.eq(c(24, 6)) | s.eq(c(25, 6)) |
                s.eq(c(26, 6)) | s.eq(c(27, 6)) |
                s.eq(c(28, 6)) | s.eq(c(29, 6)) |
                s.eq(c(30, 6)) | s.eq(c(31, 6)))

    id_reads_tu = _is_tu(srcl) | _is_tu(srcr) | _is_tu(srcp)
    tu_hazard = inflight_tu_write & id_reads_tu

    stall_id = (load_use | tu_hazard) & valid_id
    fb_stall_id.set(stall_id)

    # --- 写 ID/EX 流水线寄存器 ---
    id_to_ex_valid = valid_id & ~stall_id & ~fb_flush
    idex_pc_r.set(idex_pc_r)
    idex_pc_r.set(ifid_pc, when=~fb_freeze_id)
    idex_op_r.set(idex_op_r)
    idex_op_r.set(mux(id_to_ex_valid, op_id, c(OP_INVALID, 6)), when=~fb_freeze_id)
    idex_len_bytes_r.set(idex_len_bytes_r)
    idex_len_bytes_r.set(mux(id_to_ex_valid, len_bytes_id, c(0, 3)), when=~fb_freeze_id)
    idex_regdst_r.set(idex_regdst_r)
    idex_regdst_r.set(mux(id_to_ex_valid, regdst_id, c(REG_INVALID, 6)), when=~fb_freeze_id)
    idex_srcl_val_r.set(idex_srcl_val_r)
    idex_srcl_val_r.set(srcl_val, when=~fb_freeze_id)
    idex_srcr_val_r.set(idex_srcr_val_r)
    idex_srcr_val_r.set(srcr_val, when=~fb_freeze_id)
    idex_srcp_val_r.set(idex_srcp_val_r)
    idex_srcp_val_r.set(srcp_val, when=~fb_freeze_id)
    idex_imm_r.set(idex_imm_r)
    idex_imm_r.set(imm_id, when=~fb_freeze_id)
    valid_ex_r.set(valid_ex_r)
    valid_ex_r.set(id_to_ex_valid, when=~fb_freeze_id)

    # ==================================================================
    domain.next()  # → cycle 2
    # ==================================================================
    # Cycle 2 — EX stage
    # ==================================================================
    c = lambda v, w: domain.const(v, width=w)
    consts_ex = make_consts(m, domain)

    # ID/EX reg Q at cycle 2 (current → no DFF insertion)
    idex_pc = idex_pc_r
    idex_op = idex_op_r
    idex_len_bytes = idex_len_bytes_r
    idex_regdst = idex_regdst_r
    idex_srcl_val = idex_srcl_val_r
    idex_srcr_val = idex_srcr_val_r
    idex_srcp_val = idex_srcp_val_r
    idex_imm = idex_imm_r

    ex_out = ex_stage_logic(
        m, domain,
        pc=idex_pc, op=idex_op, len_bytes=idex_len_bytes, regdst=idex_regdst,
        srcl_val=idex_srcl_val, srcr_val=idex_srcr_val, srcp_val=idex_srcp_val,
        imm=idex_imm, consts=consts_ex,
    )

    # 驱动 fb_ex_alu feedback
    fb_ex_alu.set(ex_out["alu"])

    # --- 写 EX/MEM 流水线寄存器 ---
    ex_to_mem_valid = valid_ex_r & ~fb_flush
    exmem_op_r.set(exmem_op_r)
    exmem_op_r.set(ex_out["op"], when=~fb_freeze_ex)
    exmem_len_bytes_r.set(exmem_len_bytes_r)
    exmem_len_bytes_r.set(ex_out["len_bytes"], when=~fb_freeze_ex)
    exmem_pc_r.set(exmem_pc_r)
    exmem_pc_r.set(ex_out["pc"], when=~fb_freeze_ex)
    exmem_regdst_r.set(exmem_regdst_r)
    exmem_regdst_r.set(ex_out["regdst"], when=~fb_freeze_ex)
    exmem_alu_r.set(exmem_alu_r)
    exmem_alu_r.set(ex_out["alu"], when=~fb_freeze_ex)
    exmem_is_load_r.set(exmem_is_load_r)
    exmem_is_load_r.set(ex_out["is_load"], when=~fb_freeze_ex)
    exmem_is_store_r.set(exmem_is_store_r)
    exmem_is_store_r.set(ex_out["is_store"], when=~fb_freeze_ex)
    exmem_size_r.set(exmem_size_r)
    exmem_size_r.set(ex_out["size"], when=~fb_freeze_ex)
    exmem_addr_r.set(exmem_addr_r)
    exmem_addr_r.set(ex_out["addr"], when=~fb_freeze_ex)
    exmem_wdata_r.set(exmem_wdata_r)
    exmem_wdata_r.set(ex_out["wdata"], when=~fb_freeze_ex)
    valid_mem_r.set(valid_mem_r)
    valid_mem_r.set(ex_to_mem_valid, when=~fb_freeze_ex)

    # ==================================================================
    domain.next()  # → cycle 3
    # ==================================================================
    # Cycle 3 — MEM stage
    # ==================================================================
    c = lambda v, w: domain.const(v, width=w)

    # EX/MEM reg Q at cycle 3 (current → no DFF insertion)
    exmem_op = exmem_op_r
    exmem_len_bytes = exmem_len_bytes_r
    exmem_pc = exmem_pc_r
    exmem_regdst = exmem_regdst_r
    exmem_alu = exmem_alu_r
    exmem_is_load = exmem_is_load_r
    exmem_is_store = exmem_is_store_r
    exmem_size = exmem_size_r
    exmem_addr = exmem_addr_r
    exmem_wdata = exmem_wdata_r
    valid_mem_now = valid_mem_r & ~fb_flush

    ex_out_d = {
        "op": exmem_op, "len_bytes": exmem_len_bytes, "pc": exmem_pc,
        "regdst": exmem_regdst, "alu": exmem_alu,
        "is_load": exmem_is_load, "is_store": exmem_is_store,
        "size": exmem_size, "addr": exmem_addr, "wdata": exmem_wdata,
    }

    # Data memory access
    dmem_raddr = mux(exmem_is_load, exmem_addr, c(0, 64))
    dmem_wvalid = exmem_is_store & valid_mem_now
    wstrb = mux(exmem_size.eq(8), c(0xFF, 8), c(0, 8))
    wstrb = mux(exmem_size.eq(4), c(0x0F, 8), wstrb)
    mem_rdata = build_byte_mem(
        m, domain,
        raddr=dmem_raddr, wvalid=dmem_wvalid, waddr=exmem_addr,
        wdata=exmem_wdata, wstrb=wstrb,
        depth_bytes=mem_bytes, name="mem",
    )
    mem_out = mem_stage_logic(m, domain, ex_out_d, mem_rdata)

    # 驱动 fb_mem_value feedback
    fb_mem_value.set(mem_out["value"])

    # --- 写 MEM/WB 流水线寄存器 ---
    mem_to_wb_valid = valid_mem_now
    memwb_op_r.set(memwb_op_r)
    memwb_op_r.set(mem_out["op"], when=~fb_freeze_mem)
    memwb_len_bytes_r.set(memwb_len_bytes_r)
    memwb_len_bytes_r.set(mem_out["len_bytes"], when=~fb_freeze_mem)
    memwb_pc_r.set(memwb_pc_r)
    memwb_pc_r.set(mem_out["pc"], when=~fb_freeze_mem)
    memwb_regdst_r.set(memwb_regdst_r)
    memwb_regdst_r.set(mem_out["regdst"], when=~fb_freeze_mem)
    memwb_value_r.set(memwb_value_r)
    memwb_value_r.set(mem_out["value"], when=~fb_freeze_mem)
    valid_wb_r.set(valid_wb_r)
    valid_wb_r.set(mem_to_wb_valid, when=~fb_freeze_mem)

    # ==================================================================
    domain.next()  # → cycle 4
    # ==================================================================
    # Cycle 4 — WB stage
    # ==================================================================
    c = lambda v, w: domain.const(v, width=w)

    # MEM/WB reg Q at cycle 4 (current → no DFF insertion)
    wb_op = memwb_op_r
    wb_len_bytes = memwb_len_bytes_r
    wb_pc = memwb_pc_r
    wb_regdst = memwb_regdst_r
    wb_value = memwb_value_r
    valid_wb = valid_wb_r

    wb_valid = wb_op.ne(c(OP_INVALID, 6)) & wb_pc.ne(c(0, 64))
    do_wb_arch = valid_wb & wb_valid

    halt_set = (~state.halted) & do_wb_arch & wb_op.eq(c(OP_EBREAK, 6))
    state.halted.set(c(1, 1), when=halt_set)
    stop = state.halted | halt_set

    wb_result = wb_stage_updates(
        m, state=state, rf=rf, domain=domain,
        op=wb_op, len_bytes=wb_len_bytes, pc=wb_pc,
        regdst=wb_regdst, value=wb_value,
        do_wb_arch=do_wb_arch,
    )

    # 驱动 WB 反馈信号
    fb_flush.set(wb_result["flush"])
    fb_redirect_pc.set(wb_result["redirect_pc"])
    fb_stop.set(stop)
    fb_do_wb_arch.set(do_wb_arch)

    # 周期计数器
    state.cycles.set(state.cycles + 1)

    # ==================================================================
    # 输出 — 直接传 CycleAwareSignal，无需 .sig
    # ==================================================================
    active = ~stop
    m.output("halted", state.halted)
    m.output("pc", state.pc)
    m.output("stage", state.stage)
    m.output("cycles", state.cycles)
    m.output("br_kind", state.br_kind)
    m.output("br_base_pc", state.br_base_pc)
    m.output("br_off", state.br_off)
    m.output("commit_cond", state.commit_cond)
    m.output("commit_tgt", state.commit_tgt)
    m.output("active", active)
    m.output("pc_if", current_fetch_pc)
    m.output("pc_id", ifid_pc)
    m.output("pc_ex", idex_pc)
    m.output("pc_mem", exmem_pc)
    m.output("pc_wb", wb_pc)
    m.output("if_window", ifid_window)
    m.output("a0", rf.gpr[2])
    m.output("a1", rf.gpr[3])
    m.output("ra", rf.gpr[10])
    m.output("sp", rf.gpr[1])
    m.output("flush", wb_result["flush"])
    m.output("valid_wb", valid_wb)
    # DEBUG: pipeline internals
    m.output("dbg_idex_srcl", idex_srcl_val)
    m.output("dbg_idex_op", idex_op)
    m.output("dbg_ex_alu", ex_out["alu"])
    m.output("dbg_stall", stall_id)
    m.output("dbg_valid_id", valid_id)
    m.output("dbg_valid_ex", valid_ex_eff)
    m.output("dbg_valid_mem", valid_mem_now)


def linx_cpu_pyc_cycle_aware(m: CycleAwareCircuit, domain: CycleAwareDomain, *, mem_bytes: int = (1 << 20)) -> None:
    _linx_cpu_impl(m, domain, mem_bytes=mem_bytes)


def build():
    mem_bytes = int(os.environ.get("PYC_MEM_BYTES", str(1 << 20)), 0)
    return compile_cycle_aware(linx_cpu_pyc_cycle_aware, name="linx_cpu_pyc_cycle_aware", mem_bytes=mem_bytes)


if __name__ == "__main__":
    print(build().emit_mlir())
