# -*- coding: utf-8 -*-
"""LinxISA CPU implementation using Cycle-Aware API.

A 5-stage pipeline CPU (IF/ID/EX/MEM/WB) demonstrating:
- CycleAwareReg for pipeline registers and state machines
- CycleAwareByteMem for unified instruction/data memory
- Complex control flow with branch handling
"""
from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    compile_cycle_aware,
    mux,
)

from .isa import BK_FALL, OP_EBREAK, OP_INVALID, REG_INVALID, ST_EX, ST_ID, ST_IF, ST_MEM, ST_WB
from .memory import build_byte_mem
from .pipeline import CoreState, ExMemRegs, IdExRegs, IfIdRegs, MemWbRegs, RegFiles
from .regfile import make_gpr, make_regs
from .stages.ex_stage import build_ex_stage
from .stages.id_stage import build_id_stage
from .stages.if_stage import build_if_stage
from .stages.mem_stage import build_mem_stage
from .stages.wb_stage import build_wb_stage
from .util import make_consts


def _linx_cpu_impl(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    mem_bytes: int,
) -> None:
    # --- input signals ---
    boot_pc = domain.create_signal("boot_pc", width=64)
    boot_sp = domain.create_signal("boot_sp", width=64)

    consts = make_consts(m, domain)

    # --- core state regs ---
    state = CoreState(
        stage=m.ca_reg("state_stage", domain=domain, width=3, init=ST_IF),
        pc=m.ca_reg("state_pc", domain=domain, width=64, init=0),
        br_kind=m.ca_reg("state_br_kind", domain=domain, width=2, init=BK_FALL),
        br_base_pc=m.ca_reg("state_br_base_pc", domain=domain, width=64, init=0),
        br_off=m.ca_reg("state_br_off", domain=domain, width=64, init=0),
        commit_cond=m.ca_reg("state_commit_cond", domain=domain, width=1, init=0),
        commit_tgt=m.ca_reg("state_commit_tgt", domain=domain, width=64, init=0),
        cycles=m.ca_reg("state_cycles", domain=domain, width=64, init=0),
        halted=m.ca_reg("state_halted", domain=domain, width=1, init=0),
    )

    # Initialize PC with boot_pc on first cycle (when cycles == 0)
    is_first = state.cycles.out().eq(0)
    state.pc.set(boot_pc, when=is_first)

    pipe_ifid = IfIdRegs(window=m.ca_reg("ifid_window", domain=domain, width=64, init=0))

    pipe_idex = IdExRegs(
        op=m.ca_reg("idex_op", domain=domain, width=6, init=0),
        len_bytes=m.ca_reg("idex_len_bytes", domain=domain, width=3, init=0),
        regdst=m.ca_reg("idex_regdst", domain=domain, width=6, init=REG_INVALID),
        srcl=m.ca_reg("idex_srcl", domain=domain, width=6, init=REG_INVALID),
        srcr=m.ca_reg("idex_srcr", domain=domain, width=6, init=REG_INVALID),
        srcp=m.ca_reg("idex_srcp", domain=domain, width=6, init=REG_INVALID),
        imm=m.ca_reg("idex_imm", domain=domain, width=64, init=0),
        srcl_val=m.ca_reg("idex_srcl_val", domain=domain, width=64, init=0),
        srcr_val=m.ca_reg("idex_srcr_val", domain=domain, width=64, init=0),
        srcp_val=m.ca_reg("idex_srcp_val", domain=domain, width=64, init=0),
    )

    pipe_exmem = ExMemRegs(
        op=m.ca_reg("exmem_op", domain=domain, width=6, init=0),
        len_bytes=m.ca_reg("exmem_len_bytes", domain=domain, width=3, init=0),
        regdst=m.ca_reg("exmem_regdst", domain=domain, width=6, init=REG_INVALID),
        alu=m.ca_reg("exmem_alu", domain=domain, width=64, init=0),
        is_load=m.ca_reg("exmem_is_load", domain=domain, width=1, init=0),
        is_store=m.ca_reg("exmem_is_store", domain=domain, width=1, init=0),
        size=m.ca_reg("exmem_size", domain=domain, width=3, init=0),
        addr=m.ca_reg("exmem_addr", domain=domain, width=64, init=0),
        wdata=m.ca_reg("exmem_wdata", domain=domain, width=64, init=0),
    )

    pipe_memwb = MemWbRegs(
        op=m.ca_reg("memwb_op", domain=domain, width=6, init=0),
        len_bytes=m.ca_reg("memwb_len_bytes", domain=domain, width=3, init=0),
        regdst=m.ca_reg("memwb_regdst", domain=domain, width=6, init=REG_INVALID),
        value=m.ca_reg("memwb_value", domain=domain, width=64, init=0),
    )

    # --- register files ---
    gpr = make_gpr(m, domain, boot_sp=boot_sp)
    # Initialize r1 (sp) with boot_sp on first cycle
    gpr[1].set(boot_sp, when=is_first)
    
    t = make_regs(m, domain, count=4, width=64, init=0)
    u = make_regs(m, domain, count=4, width=64, init=0)

    rf = RegFiles(gpr=gpr, t=t, u=u)

    # --- stage control ---
    stage_is_if = state.stage.out().eq(ST_IF)
    stage_is_id = state.stage.out().eq(ST_ID)
    stage_is_ex = state.stage.out().eq(ST_EX)
    stage_is_mem = state.stage.out().eq(ST_MEM)
    stage_is_wb = state.stage.out().eq(ST_WB)

    halt_set = stage_is_wb & (~state.halted.out()) & (pipe_memwb.op.out().eq(OP_EBREAK) | pipe_memwb.op.out().eq(OP_INVALID))
    stop = state.halted.out() | halt_set
    active = ~stop

    do_if = stage_is_if & active
    do_id = stage_is_id & active
    do_ex = stage_is_ex & active
    do_mem = stage_is_mem & active
    do_wb = stage_is_wb & active

    # --- unified byte memory (instruction + data) ---
    zero64 = consts.zero64
    zero8 = consts.zero8
    zero1 = consts.zero1

    # Memory read address: IF stage reads PC, MEM stage reads load address
    mem_raddr = mux(do_if, state.pc.out(), zero64)
    mem_raddr = mux(stage_is_mem & active & pipe_exmem.is_load.out(), pipe_exmem.addr.out(), mem_raddr)

    mem_wvalid = stage_is_mem & active & pipe_exmem.is_store.out()
    mem_waddr = pipe_exmem.addr.out()
    mem_wdata = pipe_exmem.wdata.out()
    
    # Write strobe based on size
    mem_wstrb = mux(pipe_exmem.size.out().eq(8), m.ca_const(0xFF, width=8), zero8)
    mem_wstrb = mux(pipe_exmem.size.out().eq(4), m.ca_const(0x0F, width=8), mem_wstrb)

    mem_rdata = build_byte_mem(
        m,
        domain,
        raddr=mem_raddr,
        wvalid=mem_wvalid,
        waddr=mem_waddr,
        wdata=mem_wdata,
        wstrb=mem_wstrb,
        depth_bytes=mem_bytes,
        name="mem",
    )

    # --- stages ---
    build_if_stage(m, do_if=do_if, ifid_window=pipe_ifid.window, mem_rdata=mem_rdata)
    build_id_stage(m, do_id=do_id, ifid=pipe_ifid, idex=pipe_idex, rf=rf, consts=consts)
    build_ex_stage(m, do_ex=do_ex, pc=state.pc.out(), idex=pipe_idex, exmem=pipe_exmem, consts=consts)
    build_mem_stage(m, do_mem=do_mem, exmem=pipe_exmem, memwb=pipe_memwb, mem_rdata=mem_rdata)
    build_wb_stage(
        m,
        do_wb=do_wb,
        stage_is_if=stage_is_if,
        stage_is_id=stage_is_id,
        stage_is_ex=stage_is_ex,
        stage_is_mem=stage_is_mem,
        stage_is_wb=stage_is_wb,
        stop=stop,
        halt_set=halt_set,
        state=state,
        memwb=pipe_memwb,
        rf=rf,
    )

    # --- outputs ---
    m.output("halted", state.halted.out().sig)
    m.output("pc", state.pc.out().sig)
    m.output("stage", state.stage.out().sig)
    m.output("cycles", state.cycles.out().sig)
    m.output("a0", rf.gpr[2].out().sig)
    m.output("a1", rf.gpr[3].out().sig)
    m.output("ra", rf.gpr[10].out().sig)
    m.output("sp", rf.gpr[1].out().sig)
    m.output("br_kind", state.br_kind.out().sig)
    # Debug/trace hooks
    m.output("if_window", pipe_ifid.window.out().sig)
    m.output("wb_op", pipe_memwb.op.out().sig)
    m.output("wb_regdst", pipe_memwb.regdst.out().sig)
    m.output("wb_value", pipe_memwb.value.out().sig)
    m.output("commit_cond", state.commit_cond.out().sig)
    m.output("commit_tgt", state.commit_tgt.out().sig)


def linx_cpu_pyc(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    """LinxISA CPU with default memory size."""
    _linx_cpu_impl(m, domain, mem_bytes=(1 << 20))


if __name__ == "__main__":
    circuit = compile_cycle_aware(linx_cpu_pyc, name="linx_cpu_pyc")
    print(circuit.emit_mlir())
