from __future__ import annotations

from pycircuit import Circuit

# This design is written in "JIT mode": `pycircuit.cli emit` will compile
# `build(m: Circuit, ...)` via the AST/SCF frontend.

from examples.linx_cpu_pyc.isa import (
    BK_FALL,
    OP_BSTART_STD_CALL,
    OP_BSTART_STD_COND,
    OP_BSTART_STD_DIRECT,
    OP_BSTART_STD_FALL,
    OP_C_BSTOP,
    OP_C_BSTART_COND,
    OP_C_BSTART_DIRECT,
    OP_C_BSTART_STD,
    OP_C_LDI,
    OP_C_LWI,
    OP_C_SDI,
    OP_C_SETC_EQ,
    OP_C_SETC_NE,
    OP_C_SETC_TGT,
    OP_C_SWI,
    OP_EBREAK,
    OP_FENTRY,
    OP_FEXIT,
    OP_FRET_RA,
    OP_FRET_STK,
    OP_HL_LB_PCR,
    OP_HL_LBU_PCR,
    OP_HL_LD_PCR,
    OP_HL_LH_PCR,
    OP_HL_LHU_PCR,
    OP_HL_LW_PCR,
    OP_HL_LWU_PCR,
    OP_HL_SB_PCR,
    OP_HL_SD_PCR,
    OP_HL_SH_PCR,
    OP_HL_SW_PCR,
    OP_INVALID,
    OP_LB,
    OP_LBI,
    OP_LBU,
    OP_LBUI,
    OP_LD,
    OP_LDI,
    OP_LH,
    OP_LHI,
    OP_LHU,
    OP_LHUI,
    OP_LW,
    OP_LWI,
    OP_LWU,
    OP_LWUI,
    OP_SB,
    OP_SBI,
    OP_SD,
    OP_SDI,
    OP_SH,
    OP_SHI,
    OP_SETC_AND,
    OP_SETC_ANDI,
    OP_SETC_EQ,
    OP_SETC_EQI,
    OP_SETC_GE,
    OP_SETC_GEI,
    OP_SETC_GEU,
    OP_SETC_GEUI,
    OP_SETC_LT,
    OP_SETC_LTI,
    OP_SETC_LTU,
    OP_SETC_LTUI,
    OP_SETC_NE,
    OP_SETC_NEI,
    OP_SETC_OR,
    OP_SETC_ORI,
    OP_SW,
    OP_SWI,
    REG_INVALID,
    ST_IF,
    ST_WB,
)
from examples.linx_cpu_pyc.decode import decode_window
from examples.linx_cpu_pyc.memory import build_byte_mem
from examples.linx_cpu_pyc.pipeline import CoreState, ExMemRegs, IdExRegs, IfIdRegs, MemWbRegs, RegFiles
from examples.linx_cpu_pyc.regfile import commit_gpr, commit_stack, make_gpr, make_regs, read_reg, stack_next
from examples.linx_cpu_pyc.stages.ex_stage import build_ex_stage
from examples.linx_cpu_pyc.stages.id_stage import build_id_stage
from examples.linx_cpu_pyc.stages.if_stage import build_if_stage
from examples.linx_cpu_pyc.stages.mem_stage import build_mem_stage
from examples.linx_cpu_pyc.stages.wb_stage import build_wb_stage
from examples.linx_cpu_pyc.util import lshr_var, make_bp_table, make_consts, mux_read


def build(
    m: Circuit,
    *,
    mem_bytes: int = (1 << 20),
    icache_bytes: int = (16 << 10),
    dcache_bytes: int = (32 << 10),
) -> None:
    # --- ports ---
    clk = m.clock("clk")
    rst = m.reset("rst")

    boot_pc = m.input("boot_pc", width=64)
    boot_sp = m.input("boot_sp", width=64)
    # Interrupts (optional; default TB drives low).
    irq = m.input("irq", width=1)
    irq_vector = m.input("irq_vector", width=64)

    # Host loader write port (used by PS/PL FPGA bring-up). This is intended to
    # be driven while the core is held in reset.
    host_wvalid = m.input("host_wvalid", width=1)
    host_waddr = m.input("host_waddr", width=64)
    host_wdata = m.input("host_wdata", width=64)
    host_wstrb = m.input("host_wstrb", width=8)

    consts = make_consts(m)

    # QEMU test framework MMIO.
    mmio_uart = m.const(0x1000_0000, width=64)
    mmio_exit = m.const(0x1000_0004, width=64)

    # --- core state regs (named) ---
    with m.scope("state"):
        state = CoreState(
            pc=m.out("pc_fetch", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1),
            br_kind=m.out("br_kind", clk=clk, rst=rst, width=3, init=BK_FALL, en=consts.one1),
            br_base_pc=m.out("br_base_pc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1),
            br_off=m.out("br_off", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            commit_cond=m.out("commit_cond", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            commit_tgt=m.out("commit_tgt", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            exit_code=m.out("exit_code", clk=clk, rst=rst, width=32, init=0, en=consts.one1),
            cycles=m.out("cycles", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            halted=m.out("halted", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
        )

    # --- branch predictor (prototype) ---
    # Tiny direct-mapped BTB + 2-bit counters. Starts "predict not-taken" and
    # learns at runtime. This is only used for boundary markers (BlockISA).
    bp_entries = 64
    with m.scope("bp"):
        bp = make_bp_table(m, clk, rst, entries=bp_entries, en=consts.one1)
        bp_valid = bp[0]
        bp_tag = bp[1]
        bp_target = bp[2]
        bp_ctr = bp[3]

    with m.scope("ifid0"):
        pipe_ifid0 = IfIdRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    with m.scope("ifid1"):
        pipe_ifid1 = IfIdRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    with m.scope("idex0"):
        pipe_idex0 = IdExRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            op=m.out("op", clk=clk, rst=rst, width=12, init=0, en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=0, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcl=m.out("srcl", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr=m.out("srcr", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr_type=m.out("srcr_type", clk=clk, rst=rst, width=2, init=0, en=consts.one1),
            shamt=m.out("shamt", clk=clk, rst=rst, width=6, init=0, en=consts.one1),
            srcp=m.out("srcp", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            imm=m.out("imm", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            srcl_val=m.out("srcl_val", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            srcr_val=m.out("srcr_val", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            srcp_val=m.out("srcp_val", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    with m.scope("idex1"):
        pipe_idex1 = IdExRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            op=m.out("op", clk=clk, rst=rst, width=12, init=0, en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=0, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcl=m.out("srcl", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr=m.out("srcr", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr_type=m.out("srcr_type", clk=clk, rst=rst, width=2, init=0, en=consts.one1),
            shamt=m.out("shamt", clk=clk, rst=rst, width=6, init=0, en=consts.one1),
            srcp=m.out("srcp", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            imm=m.out("imm", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            srcl_val=m.out("srcl_val", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            srcr_val=m.out("srcr_val", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            srcp_val=m.out("srcp_val", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    with m.scope("exmem0"):
        pipe_exmem0 = ExMemRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            op=m.out("op", clk=clk, rst=rst, width=12, init=0, en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=0, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcl=m.out("srcl", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr=m.out("srcr", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            imm=m.out("imm", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            alu=m.out("alu", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            is_load=m.out("is_load", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            is_store=m.out("is_store", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            size=m.out("size", clk=clk, rst=rst, width=4, init=0, en=consts.one1),
            addr=m.out("addr", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            wdata=m.out("wdata", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    with m.scope("exmem1"):
        pipe_exmem1 = ExMemRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            op=m.out("op", clk=clk, rst=rst, width=12, init=0, en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=0, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcl=m.out("srcl", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr=m.out("srcr", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            imm=m.out("imm", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            alu=m.out("alu", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            is_load=m.out("is_load", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            is_store=m.out("is_store", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            size=m.out("size", clk=clk, rst=rst, width=4, init=0, en=consts.one1),
            addr=m.out("addr", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            wdata=m.out("wdata", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    with m.scope("memwb0"):
        pipe_memwb0 = MemWbRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            op=m.out("op", clk=clk, rst=rst, width=12, init=0, en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=0, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcl=m.out("srcl", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr=m.out("srcr", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            imm=m.out("imm", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            value=m.out("value", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            is_store=m.out("is_store", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            size=m.out("size", clk=clk, rst=rst, width=4, init=0, en=consts.one1),
            addr=m.out("addr", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            wdata=m.out("wdata", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    with m.scope("memwb1"):
        pipe_memwb1 = MemWbRegs(
            valid=m.out("valid", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            window=m.out("window", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            pred_next_pc=m.out("pred_next_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            op=m.out("op", clk=clk, rst=rst, width=12, init=0, en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=0, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcl=m.out("srcl", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            srcr=m.out("srcr", clk=clk, rst=rst, width=6, init=REG_INVALID, en=consts.one1),
            imm=m.out("imm", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            value=m.out("value", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            is_store=m.out("is_store", clk=clk, rst=rst, width=1, init=0, en=consts.one1),
            size=m.out("size", clk=clk, rst=rst, width=4, init=0, en=consts.one1),
            addr=m.out("addr", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
            wdata=m.out("wdata", clk=clk, rst=rst, width=64, init=0, en=consts.one1),
        )

    # --- register files ---
    with m.scope("gpr"):
        gpr = make_gpr(m, clk, rst, boot_sp=boot_sp, en=consts.one1)
    with m.scope("t"):
        t = make_regs(m, clk, rst, count=4, width=64, init=consts.zero64, en=consts.one1)
    with m.scope("u"):
        u = make_regs(m, clk, rst, count=4, width=64, init=consts.zero64, en=consts.one1)

    rf = RegFiles(gpr=gpr, t=t, u=u)

    # --- macro instruction engine (multi-cycle) ---
    # Implements LinxISA bring-up macro instructions:
    #   - FENTRY  (save regs + SP -= stacksize)
    #   - FEXIT   (restore regs + SP += stacksize)
    #   - FRET.*  (restore regs + SP += stacksize + PC <- RA)
    #
    # These are treated as single retiring instructions that start a small
    # micro-sequence while stalling the pipeline.
    with m.scope("macro"):
        macro_active = m.out("active", clk=clk, rst=rst, width=1, init=0, en=consts.one1)
        macro_kind = m.out("kind", clk=clk, rst=rst, width=2, init=0, en=consts.one1)  # 0=fentry,1=fexit,2=fret_ra,3=fret_stk
        macro_phase = m.out("phase", clk=clk, rst=rst, width=2, init=0, en=consts.one1)  # 0=pro,1=loop,2=epi
        macro_begin = m.out("begin", clk=clk, rst=rst, width=6, init=0, en=consts.one1)
        macro_end = m.out("end", clk=clk, rst=rst, width=6, init=0, en=consts.one1)
        macro_stacksize = m.out("stacksize", clk=clk, rst=rst, width=64, init=0, en=consts.one1)
        macro_resume_pc = m.out("resume_pc", clk=clk, rst=rst, width=64, init=0, en=consts.one1)
        macro_idx = m.out("idx", clk=clk, rst=rst, width=64, init=0, en=consts.one1)
        macro_reg = m.out("reg", clk=clk, rst=rst, width=6, init=0, en=consts.one1)
        macro_retaddr = m.out("retaddr", clk=clk, rst=rst, width=64, init=0, en=consts.one1)

    macro_on = macro_active.out()

    # --- pipeline control ---
    # MMIO writes commit in WB (same point as memory writes).
    wb0_retire = pipe_memwb0.valid.out() & (~state.halted)
    wb0_store_valid = wb0_retire & pipe_memwb0.is_store.out()
    wb0_store_addr = pipe_memwb0.addr.out()
    wb0_store_size = pipe_memwb0.size.out()
    wb0_store_data = pipe_memwb0.wdata.out()

    # LinxISA libc uses byte stores for UART (`__linx_putchar`), but keep
    # compatibility with older word-store bring-up tests.
    mmio_uart_wr0 = wb0_store_valid & (wb0_store_addr == mmio_uart) & ((wb0_store_size == 1) | (wb0_store_size == 4))
    mmio_exit_wr0 = wb0_store_valid & (wb0_store_addr == mmio_exit) & (wb0_store_size == 4)

    # Lane0 do_wb is suppressed when the retiring op halts the core.
    wb0_halt = (wb0_retire & ((pipe_memwb0.op == OP_EBREAK) | (pipe_memwb0.op == OP_INVALID))) | mmio_exit_wr0
    do_wb0 = wb0_retire & (~wb0_halt)

    wb = build_wb_stage(m, do_wb=do_wb0, state=state, memwb=pipe_memwb0)

    wb_op = pipe_memwb0.op.out()
    wb_is_macro = (wb_op == OP_FENTRY) | (wb_op == OP_FEXIT) | (wb_op == OP_FRET_RA) | (wb_op == OP_FRET_STK)
    wb_take_event = wb.boundary_valid & wb.br_take
    macro_start = do_wb0 & wb_is_macro & (~wb_take_event)

    # Minimal precise interrupt model: take IRQ between retired instructions.
    # (No ISA-visible CSRs yet; handler can be used for bring-up / future work.)
    irq_take = irq & do_wb0 & (~wb_take_event) & (~wb_is_macro)

    # Branch prediction check: compare predicted vs actual next PC for boundaries.
    wb_pred_next_pc = pipe_memwb0.pred_next_pc.out()
    mispredict = wb.boundary_valid & (wb_pred_next_pc != wb.next_pc)

    # If the older lane redirects / starts a macro / halts, the younger lane
    # must not retire in the same cycle.
    wb0_kill_young = wb_take_event | mispredict | macro_start | wb0_halt | irq_take

    wb1_retire = pipe_memwb1.valid.out() & (~state.halted) & (~wb0_kill_young)
    wb1_store_valid = wb1_retire & pipe_memwb1.is_store.out()
    wb1_store_addr = pipe_memwb1.addr.out()
    wb1_store_size = pipe_memwb1.size.out()
    wb1_store_data = pipe_memwb1.wdata.out()

    mmio_uart_wr1 = wb1_store_valid & (wb1_store_addr == mmio_uart) & ((wb1_store_size == 1) | (wb1_store_size == 4))
    mmio_exit_wr1 = wb1_store_valid & (wb1_store_addr == mmio_exit) & (wb1_store_size == 4)

    wb1_halt = (wb1_retire & ((pipe_memwb1.op == OP_EBREAK) | (pipe_memwb1.op == OP_INVALID))) | mmio_exit_wr1
    do_wb1 = wb1_retire & (~wb1_halt)

    halt_set = wb0_halt | wb1_halt
    stop = state.halted | halt_set
    active = ~stop

    flush = mispredict | macro_start | irq_take

    # --- regfile commit (WB) ---
    wb_regdst = pipe_memwb0.regdst.out()
    wb_value = pipe_memwb0.value.out()
    wb_is_store = pipe_memwb0.is_store.out()

    wb_do_reg_write = do_wb0 & (~wb_is_store) & (wb_regdst != REG_INVALID)
    wb_do_reg_write_eff = wb_do_reg_write | wb.ra_write_valid

    wb_regdst_eff = wb_regdst
    wb_value_eff = wb_value
    if wb.ra_write_valid:
        wb_regdst_eff = m.const(10, width=6)  # ra
        wb_value_eff = wb.ra_write_value

    wb1_regdst = pipe_memwb1.regdst.out()
    wb1_value = pipe_memwb1.value.out()
    wb1_is_store = pipe_memwb1.is_store.out()
    wb1_do_reg_write = do_wb1 & (~wb1_is_store) & (wb1_regdst != REG_INVALID)

    wb_is_start = (
        (wb_op == OP_C_BSTART_STD)
        | (wb_op == OP_C_BSTART_COND)
        | (wb_op == OP_C_BSTART_DIRECT)
        | (wb_op == OP_BSTART_STD_FALL)
        | (wb_op == OP_BSTART_STD_DIRECT)
        | (wb_op == OP_BSTART_STD_COND)
        | (wb_op == OP_BSTART_STD_CALL)
        | (wb_op == OP_FENTRY)
        | (wb_op == OP_FEXIT)
        | (wb_op == OP_FRET_RA)
        | (wb_op == OP_FRET_STK)
    )

    wb_clear = do_wb0 & wb_is_start
    wb_push_t = do_wb0 & ((wb_op == OP_C_LWI) | (wb_do_reg_write & (wb_regdst == 31)))
    wb_push_u = do_wb0 & (wb_do_reg_write & (wb_regdst == 30))

    t_next = stack_next(m, rf.t, do_push=wb_push_t, do_clear=wb_clear, value=wb_value)
    u_next = stack_next(m, rf.u, do_push=wb_push_u, do_clear=wb_clear, value=wb_value)
    commit_stack(m, rf.t, t_next)
    commit_stack(m, rf.u, u_next)

    # --- memory system (prototype) ---
    # In the bring-up model, I$ and D$ are modeled as two independent byte-mem
    # instances with mirrored writes (keeps I/D coherent for self-modifying code
    # in simple tests). This avoids stalling fetch for data loads.

    # Pipeline data requests (disabled while the macro engine runs).
    mem_load0 = active & (~flush) & (~macro_on) & pipe_exmem0.valid.out() & pipe_exmem0.is_load.out()
    mem_load1 = active & (~flush) & (~macro_on) & pipe_exmem1.valid.out() & pipe_exmem1.is_load.out() & (~mem_load0)
    mem_load = mem_load0 | mem_load1

    dmem_raddr = consts.zero64
    if mem_load0:
        dmem_raddr = pipe_exmem0.addr.out()
    if mem_load1:
        dmem_raddr = pipe_exmem1.addr.out()

    # Only one store can commit per cycle (lane0).
    pipe_mem_wvalid = do_wb0 & pipe_memwb0.is_store.out()
    pipe_mem_waddr = pipe_memwb0.addr.out()
    pipe_mem_wdata = pipe_memwb0.wdata.out()
    pipe_mem_wstrb = consts.zero8
    if pipe_memwb0.size.out() == 8:
        pipe_mem_wstrb = 0xFF
    if pipe_memwb0.size.out() == 4:
        pipe_mem_wstrb = 0x0F
    if pipe_memwb0.size.out() == 2:
        pipe_mem_wstrb = 0x03
    if pipe_memwb0.size.out() == 1:
        pipe_mem_wstrb = 0x01

    # Do not write RAM on MMIO commits.
    pipe_mem_wvalid = pipe_mem_wvalid & (~(mmio_uart_wr0 | mmio_exit_wr0 | mmio_uart_wr1 | mmio_exit_wr1))

    # Macro requests: use D$ to save/restore registers.
    macro_mem_raddr = consts.zero64
    macro_mem_wvalid = consts.zero1
    macro_mem_waddr = consts.zero64
    macro_mem_wdata = consts.zero64
    macro_mem_wstrb = consts.zero8

    macro_k = macro_kind.out()
    macro_p = macro_phase.out()
    macro_i = macro_idx.out()
    macro_r = macro_reg.out()
    macro_ss = macro_stacksize.out()

    # Iterations: min(reg_count(begin..end), stacksize/8).
    slots = macro_ss.lshr(amount=3)
    b64 = macro_begin.out().zext(width=64)
    e64 = macro_end.out().zext(width=64)
    reg_count = (e64 - b64) + 1
    if macro_begin.out().ugt(macro_end.out()):
        reg_count = (e64 - b64) + 23
    iters = reg_count
    if slots.ult(reg_count):
        iters = slots

    loop_active = macro_on & (macro_p == 1) & macro_i.ult(iters)
    reg_valid = (macro_r != 0) & macro_r.ult(24)

    idx1 = macro_i + 1
    off = macro_ss - idx1.shl(amount=3)
    addr = rf.gpr[1].out() + off

    save_val = read_reg(m, macro_r, gpr=rf.gpr, t=rf.t, u=rf.u, default=consts.zero64)

    if loop_active:
        macro_mem_raddr = addr
    if loop_active & (macro_k == 0) & reg_valid:
        macro_mem_wvalid = 1
        macro_mem_waddr = addr
        macro_mem_wdata = save_val
        macro_mem_wstrb = 0xFF

    # Select which client owns the D$ port.
    dmem_raddr_eff = dmem_raddr
    dmem_wvalid = pipe_mem_wvalid
    dmem_waddr = pipe_mem_waddr
    dmem_wdata = pipe_mem_wdata
    dmem_wstrb = pipe_mem_wstrb
    if macro_on:
        dmem_raddr_eff = macro_mem_raddr
        dmem_wvalid = macro_mem_wvalid
        dmem_waddr = macro_mem_waddr
        dmem_wdata = macro_mem_wdata
        dmem_wstrb = macro_mem_wstrb

    # Merge in host writes (priority) for FPGA bring-up loaders.
    mem_wvalid = dmem_wvalid | host_wvalid
    mem_waddr = host_wvalid.select(host_waddr, dmem_waddr)
    mem_wdata = host_wvalid.select(host_wdata, dmem_wdata)
    mem_wstrb = host_wvalid.select(host_wstrb, dmem_wstrb)

    # Instruction fetch port (I$): always reads at the fetch PC.
    imem_raddr = state.pc.out()

    # Build the two cache ports. Writes are mirrored (prototype coherence).
    imem_rdata = build_byte_mem(
        m,
        clk,
        rst,
        raddr=imem_raddr,
        wvalid=mem_wvalid,
        waddr=mem_waddr,
        wdata=mem_wdata,
        wstrb=mem_wstrb,
        depth_bytes=mem_bytes,
        name="imem",
    )
    dmem_rdata = build_byte_mem(
        m,
        clk,
        rst,
        raddr=dmem_raddr_eff,
        wvalid=mem_wvalid,
        waddr=mem_waddr,
        wdata=mem_wdata,
        wstrb=mem_wstrb,
        depth_bytes=mem_bytes,
        name="dmem",
    )

    # --- macro engine state update + macro regfile write port ---
    macro_do_reg_write = consts.zero1
    macro_regdst = consts.zero6
    macro_wdata = consts.zero64
    macro_pc_set_valid = consts.zero1
    macro_pc_set = consts.zero64

    macro_active_next = macro_active.out()
    macro_kind_next = macro_kind.out()
    macro_phase_next = macro_phase.out()
    macro_begin_next = macro_begin.out()
    macro_end_next = macro_end.out()
    macro_stacksize_next = macro_stacksize.out()
    macro_resume_pc_next = macro_resume_pc.out()
    macro_idx_next = macro_idx.out()
    macro_reg_next = macro_reg.out()
    macro_retaddr_next = macro_retaddr.out()

    # Start: latch params from the retiring instruction and flush younger in-flight work.
    if macro_start:
        macro_active_next = 1
        macro_phase_next = 0
        macro_idx_next = 0
        macro_begin_next = pipe_memwb0.srcl.out()
        macro_end_next = pipe_memwb0.srcr.out()
        macro_stacksize_next = pipe_memwb0.imm.out()
        macro_resume_pc_next = pipe_memwb0.pc.out() + pipe_memwb0.len_bytes.out().zext(width=64)
        macro_reg_next = pipe_memwb0.srcl.out()
        macro_retaddr_next = rf.gpr[10].out()

        # NOTE: Use explicit-width constants here; otherwise nested dynamic-ifs
        # can infer too-narrow integer types and truncate 2/3.
        macro_kind_next = m.const(0, width=2)
        if wb_op == OP_FEXIT:
            macro_kind_next = m.const(1, width=2)
        if wb_op == OP_FRET_RA:
            macro_kind_next = m.const(2, width=2)
        if wb_op == OP_FRET_STK:
            macro_kind_next = m.const(3, width=2)

    # Run: simple 3-phase micro-sequence.
    if macro_on:
        # Phase 0: prologue (FENTRY: SP -= stacksize).
        if macro_p == 0:
            macro_phase_next = 1
            if (macro_k == 0) & (macro_ss != 0):
                macro_do_reg_write = consts.one1
                macro_regdst = m.const(1, width=6)  # SP
                macro_wdata = rf.gpr[1].out() - macro_ss

        # Phase 1: loop over regs, one per cycle.
        if macro_p == 1:
            if ~macro_i.ult(iters):
                macro_phase_next = 2
            else:
                # Restore path (FEXIT/FRET.*): load and write GPRs.
                if (macro_k != 0) & reg_valid:
                    macro_do_reg_write = consts.one1
                    macro_regdst = macro_r
                    macro_wdata = dmem_rdata

                    # Capture RA after restore for FRET.*.
                    if ((macro_k == 2) | (macro_k == 3)) & (macro_r == 10):
                        macro_retaddr_next = dmem_rdata

                # Advance loop state (even if this reg is skipped).
                macro_idx_next = macro_i + 1
                nxt = macro_r + 1
                if macro_r == 23:
                    nxt = 2
                macro_reg_next = nxt

                if (macro_i + 1) == iters:
                    macro_phase_next = 2

        # Phase 2: epilogue + finish (FEXIT/FRET.*: SP += stacksize; PC update).
        if macro_p == 2:
            if (macro_k != 0) & (macro_ss != 0):
                macro_do_reg_write = consts.one1
                macro_regdst = m.const(1, width=6)  # SP
                macro_wdata = rf.gpr[1].out() + macro_ss

            macro_pc_set_valid = consts.one1
            macro_pc_set = macro_resume_pc.out()
            if (macro_k == 2) | (macro_k == 3):
                macro_pc_set = macro_retaddr.out()

            macro_active_next = 0
            macro_phase_next = 0
            macro_idx_next = 0
            macro_reg_next = macro_begin.out()

    macro_active.set(macro_active_next)
    macro_kind.set(macro_kind_next)
    macro_phase.set(macro_phase_next)
    macro_begin.set(macro_begin_next)
    macro_end.set(macro_end_next)
    macro_stacksize.set(macro_stacksize_next)
    macro_resume_pc.set(macro_resume_pc_next)
    macro_idx.set(macro_idx_next)
    macro_reg.set(macro_reg_next)
    macro_retaddr.set(macro_retaddr_next)

    # --- regfile commit (WB + macro) ---
    commit_gpr(
        m,
        rf.gpr,
        do_reg_write0=wb_do_reg_write_eff,
        regdst0=wb_regdst_eff,
        value0=wb_value_eff,
        do_reg_write1=wb1_do_reg_write,
        regdst1=wb1_regdst,
        value1=wb1_value,
        macro_do_reg_write=macro_do_reg_write,
        macro_regdst=macro_regdst,
        macro_value=macro_wdata,
    )

    # --- stages ---
    do_if = active & (~flush) & (~macro_on)

    # Dual-issue selection from the 8-byte fetch window.
    fetch_pc = state.pc.out()
    win0 = imem_rdata
    dec0 = decode_window(m, win0)
    len0 = dec0.len_bytes
    shift0 = len0.zext(width=64).shl(amount=3)
    win1 = lshr_var(m, win0, shift0)
    dec1 = decode_window(m, win1)
    len1 = dec1.len_bytes

    len0_4 = len0.zext(width=4)
    len1_4 = len1.zext(width=4)
    avail_4 = m.const(8, width=4) - len0_4
    inst1_header_ok = avail_4.uge(2)
    inst1_fits = len1_4.ule(avail_4)

    op0 = dec0.op
    op1 = dec1.op

    is_boundary0 = (
        (op0 == OP_C_BSTART_STD)
        | (op0 == OP_C_BSTOP)
        | (op0 == OP_C_BSTART_COND)
        | (op0 == OP_C_BSTART_DIRECT)
        | (op0 == OP_BSTART_STD_FALL)
        | (op0 == OP_BSTART_STD_DIRECT)
        | (op0 == OP_BSTART_STD_COND)
        | (op0 == OP_BSTART_STD_CALL)
    )
    is_boundary1 = (
        (op1 == OP_C_BSTART_STD)
        | (op1 == OP_C_BSTOP)
        | (op1 == OP_C_BSTART_COND)
        | (op1 == OP_C_BSTART_DIRECT)
        | (op1 == OP_BSTART_STD_FALL)
        | (op1 == OP_BSTART_STD_DIRECT)
        | (op1 == OP_BSTART_STD_COND)
        | (op1 == OP_BSTART_STD_CALL)
    )
    is_macro0 = (op0 == OP_FENTRY) | (op0 == OP_FEXIT) | (op0 == OP_FRET_RA) | (op0 == OP_FRET_STK)
    is_macro1 = (op1 == OP_FENTRY) | (op1 == OP_FEXIT) | (op1 == OP_FRET_RA) | (op1 == OP_FRET_STK)
    is_mem1 = (
        (op1 == OP_C_LWI)
        | (op1 == OP_C_SWI)
        | (op1 == OP_C_LDI)
        | (op1 == OP_C_SDI)
        | (op1 == OP_LWI)
        | (op1 == OP_SWI)
        | (op1 == OP_SDI)
        | (op1 == OP_SBI)
        | (op1 == OP_SHI)
        | (op1 == OP_SB)
        | (op1 == OP_SH)
        | (op1 == OP_SW)
        | (op1 == OP_SD)
        | (op1 == OP_LBUI)
        | (op1 == OP_LBI)
        | (op1 == OP_LB)
        | (op1 == OP_LBU)
        | (op1 == OP_LH)
        | (op1 == OP_LHU)
        | (op1 == OP_LW)
        | (op1 == OP_LWU)
        | (op1 == OP_LDI)
        | (op1 == OP_LHI)
        | (op1 == OP_LHUI)
        | (op1 == OP_LWUI)
        | (op1 == OP_LD)
        | (op1 == OP_HL_LB_PCR)
        | (op1 == OP_HL_LBU_PCR)
        | (op1 == OP_HL_LH_PCR)
        | (op1 == OP_HL_LHU_PCR)
        | (op1 == OP_HL_LW_PCR)
        | (op1 == OP_HL_LWU_PCR)
        | (op1 == OP_HL_LD_PCR)
        | (op1 == OP_HL_SB_PCR)
        | (op1 == OP_HL_SH_PCR)
        | (op1 == OP_HL_SW_PCR)
        | (op1 == OP_HL_SD_PCR)
    )
    is_ctrl1 = (
        (op1 == OP_C_SETC_EQ)
        | (op1 == OP_C_SETC_NE)
        | (op1 == OP_C_SETC_TGT)
        | (op1 == OP_SETC_GEUI)
        | (op1 == OP_SETC_EQ)
        | (op1 == OP_SETC_NE)
        | (op1 == OP_SETC_AND)
        | (op1 == OP_SETC_OR)
        | (op1 == OP_SETC_LT)
        | (op1 == OP_SETC_LTU)
        | (op1 == OP_SETC_GE)
        | (op1 == OP_SETC_GEU)
        | (op1 == OP_SETC_EQI)
        | (op1 == OP_SETC_NEI)
        | (op1 == OP_SETC_ANDI)
        | (op1 == OP_SETC_ORI)
        | (op1 == OP_SETC_LTI)
        | (op1 == OP_SETC_GEI)
        | (op1 == OP_SETC_LTUI)
    )
    is_halt1 = (op1 == OP_EBREAK) | (op1 == OP_INVALID)

    # Slot1 restriction: GPR-only, no memory/control/boundary/macro.
    srcl1_ok = dec1.srcl.ult(24) | (dec1.srcl == REG_INVALID)
    srcr1_ok = dec1.srcr.ult(24) | (dec1.srcr == REG_INVALID)
    srcp1_ok = dec1.srcp.ult(24) | (dec1.srcp == REG_INVALID)
    regdst1_ok = dec1.regdst.ult(24) | (dec1.regdst == REG_INVALID)
    slot1_gpr_ok = srcl1_ok & srcr1_ok & srcp1_ok & regdst1_ok

    # No same-cycle RAW: slot1 cannot read slot0's destination.
    wdst0 = dec0.regdst
    wdst0_writes_gpr = wdst0.ult(24) & (wdst0 != 0) & (wdst0 != REG_INVALID)
    raw01 = wdst0_writes_gpr & ((dec1.srcl == wdst0) | (dec1.srcr == wdst0) | (dec1.srcp == wdst0))

    do_if1 = (
        do_if
        & inst1_header_ok
        & inst1_fits
        & slot1_gpr_ok
        & (~raw01)
        & (~is_boundary0)
        & (~is_macro0)
        & (~is_boundary1)
        & (~is_macro1)
        & (~is_mem1)
        & (~is_ctrl1)
        & (~is_halt1)
    )

    # --- branch prediction (prototype) ---
    # Only slot0 can carry a boundary marker (slot1 is restricted to GPR-only),
    # so we only predict/redirect on boundary0.
    bp_idx = fetch_pc[2:8]  # 64-entry direct-mapped
    bp_v = mux_read(m, bp_idx, bp_valid)
    bp_tag_r = mux_read(m, bp_idx, bp_tag)
    bp_target_r = mux_read(m, bp_idx, bp_target)
    bp_ctr_r = mux_read(m, bp_idx, bp_ctr)
    btb_hit = bp_v & (bp_tag_r == fetch_pc)
    pred_taken = bp_ctr_r[1]

    fetch_incr = len0.zext(width=64)
    if do_if1:
        fetch_incr = fetch_incr + len1.zext(width=64)

    pc_fetch_next = fetch_pc + fetch_incr
    if is_boundary0 & pred_taken & btb_hit:
        pc_fetch_next = bp_target_r

    with m.scope("lane0"):
        build_if_stage(m, do_if=do_if, ifid=pipe_ifid0, fetch_pc=fetch_pc, mem_rdata=win0, pred_next_pc=pc_fetch_next)
    with m.scope("lane1"):
        build_if_stage(
            m,
            do_if=do_if1,
            ifid=pipe_ifid1,
            fetch_pc=fetch_pc + len0.zext(width=64),
            mem_rdata=win1,
            pred_next_pc=pc_fetch_next,
        )

    do_id0 = active & (~flush) & (~macro_on) & pipe_ifid0.valid.out()
    do_id1 = active & (~flush) & (~macro_on) & pipe_ifid1.valid.out()
    do_ex0 = active & (~flush) & (~macro_on) & pipe_idex0.valid.out()
    do_ex1 = active & (~flush) & (~macro_on) & pipe_idex1.valid.out()
    do_mem0 = active & (~flush) & (~macro_on) & pipe_exmem0.valid.out()
    do_mem1 = active & (~flush) & (~macro_on) & pipe_exmem1.valid.out()

    wb0_fwd_valid = do_wb0 & (~pipe_memwb0.is_store.out())
    wb0_fwd_regdst = pipe_memwb0.regdst.out()
    wb0_fwd_value = pipe_memwb0.value.out()
    wb1_fwd_valid = do_wb1 & (~pipe_memwb1.is_store.out())
    wb1_fwd_regdst = pipe_memwb1.regdst.out()
    wb1_fwd_value = pipe_memwb1.value.out()

    with m.scope("lane0"):
        build_id_stage(
            m,
            do_id=do_id0,
            ifid=pipe_ifid0,
            idex=pipe_idex0,
            rf=rf,
            consts=consts,
            wb0_fwd_valid=wb0_fwd_valid,
            wb0_fwd_regdst=wb0_fwd_regdst,
            wb0_fwd_value=wb0_fwd_value,
            wb1_fwd_valid=wb1_fwd_valid,
            wb1_fwd_regdst=wb1_fwd_regdst,
            wb1_fwd_value=wb1_fwd_value,
        )
    with m.scope("lane1"):
        build_id_stage(
            m,
            do_id=do_id1,
            ifid=pipe_ifid1,
            idex=pipe_idex1,
            rf=rf,
            consts=consts,
            wb0_fwd_valid=wb0_fwd_valid,
            wb0_fwd_regdst=wb0_fwd_regdst,
            wb0_fwd_value=wb0_fwd_value,
            wb1_fwd_valid=wb1_fwd_valid,
            wb1_fwd_regdst=wb1_fwd_regdst,
            wb1_fwd_value=wb1_fwd_value,
        )

    # Store->load forwarding from retiring stores in WB (lane0 only).
    wb_store_fwd = wb0_store_valid & (~(mmio_uart_wr0 | mmio_exit_wr0))
    mem_fwd_value0 = build_mem_stage(
        m,
        do_mem=do_mem0,
        exmem=pipe_exmem0,
        memwb=pipe_memwb0,
        mem_rdata=dmem_rdata,
        wb_store_valid=wb_store_fwd,
        wb_store_addr=wb0_store_addr,
        wb_store_size=wb0_store_size,
        wb_store_wdata=wb0_store_data,
    )
    mem_fwd_value1 = build_mem_stage(
        m,
        do_mem=do_mem1,
        exmem=pipe_exmem1,
        memwb=pipe_memwb1,
        mem_rdata=dmem_rdata,
        wb_store_valid=wb_store_fwd,
        wb_store_addr=wb0_store_addr,
        wb_store_size=wb0_store_size,
        wb_store_wdata=wb0_store_data,
    )

    # --- T/U stack bypass for EX stage ---
    # The ISA models T/U as small stacks (shift registers). Updates commit in WB,
    # but younger instructions must see the effects of older in-flight pushes.
    #
    # This computes a forwarded view of the stacks after applying pending
    # clear/push effects from the WB and MEM stages (oldest -> youngest).
    t0 = rf.t[0].out()
    t1 = rf.t[1].out()
    t2 = rf.t[2].out()
    t3 = rf.t[3].out()
    u0 = rf.u[0].out()
    u1 = rf.u[1].out()
    u2 = rf.u[2].out()
    u3 = rf.u[3].out()

    t0_fwd = t0
    t1_fwd = t1
    t2_fwd = t2
    t3_fwd = t3
    u0_fwd = u0
    u1_fwd = u1
    u2_fwd = u2
    u3_fwd = u3

    if wb_clear:
        t0_fwd = 0
        t1_fwd = 0
        t2_fwd = 0
        t3_fwd = 0
        u0_fwd = 0
        u1_fwd = 0
        u2_fwd = 0
        u3_fwd = 0

    if wb_push_t:
        t3_fwd = t2_fwd
        t2_fwd = t1_fwd
        t1_fwd = t0_fwd
        t0_fwd = wb_value

    if wb_push_u:
        u3_fwd = u2_fwd
        u2_fwd = u1_fwd
        u1_fwd = u0_fwd
        u0_fwd = wb_value

    mem_op = pipe_exmem0.op.out()
    mem_regdst = pipe_exmem0.regdst.out()
    mem_is_store = pipe_exmem0.is_store.out()
    mem_pending = active & pipe_exmem0.valid.out()
    mem_do_reg_write = mem_pending & (~mem_is_store) & (mem_regdst != REG_INVALID)
    mem_is_start = (
        (mem_op == OP_C_BSTART_STD)
        | (mem_op == OP_C_BSTART_COND)
        | (mem_op == OP_C_BSTART_DIRECT)
        | (mem_op == OP_BSTART_STD_FALL)
        | (mem_op == OP_BSTART_STD_DIRECT)
        | (mem_op == OP_BSTART_STD_COND)
        | (mem_op == OP_BSTART_STD_CALL)
        | (mem_op == OP_FENTRY)
        | (mem_op == OP_FEXIT)
        | (mem_op == OP_FRET_RA)
        | (mem_op == OP_FRET_STK)
    )
    mem_clear = mem_pending & mem_is_start
    mem_push_t = mem_pending & ((mem_op == OP_C_LWI) | (mem_do_reg_write & (mem_regdst == 31)))
    mem_push_u = mem_pending & (mem_do_reg_write & (mem_regdst == 30))
    mem_value = mem_fwd_value0

    if mem_clear:
        t0_fwd = 0
        t1_fwd = 0
        t2_fwd = 0
        t3_fwd = 0
        u0_fwd = 0
        u1_fwd = 0
        u2_fwd = 0
        u3_fwd = 0

    if mem_push_t:
        t3_fwd = t2_fwd
        t2_fwd = t1_fwd
        t1_fwd = t0_fwd
        t0_fwd = mem_value

    if mem_push_u:
        u3_fwd = u2_fwd
        u2_fwd = u1_fwd
        u1_fwd = u0_fwd
        u0_fwd = mem_value

    with m.scope("lane0"):
        build_ex_stage(
            m,
            do_ex=do_ex0,
            idex=pipe_idex0,
            exmem=pipe_exmem0,
            consts=consts,
            mem0_fwd_valid=pipe_exmem0.valid.out() & (~pipe_exmem0.is_store.out()),
            mem0_fwd_regdst=pipe_exmem0.regdst.out(),
            mem0_fwd_value=mem_fwd_value0,
            mem1_fwd_valid=pipe_exmem1.valid.out() & (~pipe_exmem1.is_store.out()),
            mem1_fwd_regdst=pipe_exmem1.regdst.out(),
            mem1_fwd_value=mem_fwd_value1,
            wb0_fwd_valid=pipe_memwb0.valid.out() & (~pipe_memwb0.is_store.out()),
            wb0_fwd_regdst=pipe_memwb0.regdst.out(),
            wb0_fwd_value=pipe_memwb0.value.out(),
            wb1_fwd_valid=pipe_memwb1.valid.out() & (~pipe_memwb1.is_store.out()),
            wb1_fwd_regdst=pipe_memwb1.regdst.out(),
            wb1_fwd_value=pipe_memwb1.value.out(),
            t0_fwd=t0_fwd,
            t1_fwd=t1_fwd,
            t2_fwd=t2_fwd,
            t3_fwd=t3_fwd,
            u0_fwd=u0_fwd,
            u1_fwd=u1_fwd,
            u2_fwd=u2_fwd,
            u3_fwd=u3_fwd,
        )
    with m.scope("lane1"):
        build_ex_stage(
            m,
            do_ex=do_ex1,
            idex=pipe_idex1,
            exmem=pipe_exmem1,
            consts=consts,
            mem0_fwd_valid=pipe_exmem0.valid.out() & (~pipe_exmem0.is_store.out()),
            mem0_fwd_regdst=pipe_exmem0.regdst.out(),
            mem0_fwd_value=mem_fwd_value0,
            mem1_fwd_valid=pipe_exmem1.valid.out() & (~pipe_exmem1.is_store.out()),
            mem1_fwd_regdst=pipe_exmem1.regdst.out(),
            mem1_fwd_value=mem_fwd_value1,
            wb0_fwd_valid=pipe_memwb0.valid.out() & (~pipe_memwb0.is_store.out()),
            wb0_fwd_regdst=pipe_memwb0.regdst.out(),
            wb0_fwd_value=pipe_memwb0.value.out(),
            wb1_fwd_valid=pipe_memwb1.valid.out() & (~pipe_memwb1.is_store.out()),
            wb1_fwd_regdst=pipe_memwb1.regdst.out(),
            wb1_fwd_value=pipe_memwb1.value.out(),
            t0_fwd=t0_fwd,
            t1_fwd=t1_fwd,
            t2_fwd=t2_fwd,
            t3_fwd=t3_fwd,
            u0_fwd=u0_fwd,
            u1_fwd=u1_fwd,
            u2_fwd=u2_fwd,
            u3_fwd=u3_fwd,
        )

    # --- pipeline valid shift + fetch PC ---
    ifid0_v_next = do_if
    ifid1_v_next = do_if1
    idex0_v_next = pipe_ifid0.valid.out()
    idex1_v_next = pipe_ifid1.valid.out()
    exmem0_v_next = pipe_idex0.valid.out()
    exmem1_v_next = pipe_idex1.valid.out()
    memwb0_v_next = pipe_exmem0.valid.out()
    memwb1_v_next = pipe_exmem1.valid.out()
    if flush:
        ifid0_v_next = 0
        ifid1_v_next = 0
        idex0_v_next = 0
        idex1_v_next = 0
        exmem0_v_next = 0
        exmem1_v_next = 0
        memwb0_v_next = 0
        memwb1_v_next = 0

    pipe_ifid0.valid.set(ifid0_v_next, when=~stop)
    pipe_ifid1.valid.set(ifid1_v_next, when=~stop)
    pipe_idex0.valid.set(idex0_v_next, when=~stop)
    pipe_idex1.valid.set(idex1_v_next, when=~stop)
    pipe_exmem0.valid.set(exmem0_v_next, when=~stop)
    pipe_exmem1.valid.set(exmem1_v_next, when=~stop)
    pipe_memwb0.valid.set(memwb0_v_next, when=~stop)
    pipe_memwb1.valid.set(memwb1_v_next, when=~stop)

    # Fetch PC update.
    fetch_incr = len0.zext(width=64)
    if do_if1:
        fetch_incr = fetch_incr + len1.zext(width=64)

    pc_next = fetch_pc
    if mispredict:
        pc_next = wb.next_pc
    if irq_take:
        pc_next = irq_vector
    if macro_pc_set_valid:
        pc_next = macro_pc_set
    if do_if:
        pc_next = fetch_pc + fetch_incr
    state.pc.set(pc_next, when=~stop)

    # Halt latch + cycle counter (always increments; TB stops on halt).
    state.halted.set(1, when=halt_set)
    exit_wdata = wb0_store_data.trunc(width=32)
    if mmio_exit_wr1:
        exit_wdata = wb1_store_data.trunc(width=32)
    state.exit_code.set(exit_wdata, when=(mmio_exit_wr0 | mmio_exit_wr1))
    state.cycles.set(state.cycles.out() + 1)

    # --- outputs ---
    stage = m.const(ST_IF, width=3)
    if pipe_memwb0.valid.out() | pipe_memwb1.valid.out():
        stage = ST_WB
    m.output("halted", state.halted)
    m.output("exit_code", state.exit_code)
    uart_wdata = wb0_store_data.trunc(width=8)
    if mmio_uart_wr1:
        uart_wdata = wb1_store_data.trunc(width=8)
    m.output("uart_valid", mmio_uart_wr0 | mmio_uart_wr1)
    m.output("uart_byte", uart_wdata)

    pc_out = state.pc.out()
    if stage == ST_WB:
        pc_out = pipe_memwb0.pc.out()
        if ~pipe_memwb0.valid.out():
            pc_out = pipe_memwb1.pc.out()
    m.output("pc", pc_out)
    m.output("stage", stage)
    m.output("cycles", state.cycles)
    m.output("a0", rf.gpr[2])
    m.output("a1", rf.gpr[3])
    m.output("ra", rf.gpr[10])
    m.output("sp", rf.gpr[1])
    m.output("br_kind", state.br_kind)
    # Debug/trace hooks (stable, optional consumers).
    #
    # These signals are intentionally redundant with internal stage registers
    # (and with some existing `wb*` outputs) so external tooling can build
    # pipeline views without relying on internal name mangling.
    #
    # Stage naming convention for viewers (recommended):
    # - IF  : if*_valid/if*_pc
    # - ID  : ifid*_valid/ifid*_pc
    # - EX  : idex*_valid/idex*_pc
    # - MEM : exmem*_valid/exmem*_pc
    # - WB  : wb*_valid/wb*_pc
    m.output("if0_valid", do_if & (~stop))
    m.output("if0_pc", fetch_pc)
    m.output("if1_valid", do_if1 & (~stop))
    m.output("if1_pc", fetch_pc + len0.zext(width=64))
    m.output("ifid0_valid", pipe_ifid0.valid)
    m.output("ifid0_pc", pipe_ifid0.pc)
    m.output("ifid1_valid", pipe_ifid1.valid)
    m.output("ifid1_pc", pipe_ifid1.pc)
    m.output("idex0_valid", pipe_idex0.valid)
    m.output("idex0_pc", pipe_idex0.pc)
    m.output("idex1_valid", pipe_idex1.valid)
    m.output("idex1_pc", pipe_idex1.pc)
    m.output("exmem0_valid", pipe_exmem0.valid)
    m.output("exmem0_pc", pipe_exmem0.pc)
    m.output("exmem1_valid", pipe_exmem1.valid)
    m.output("exmem1_pc", pipe_exmem1.pc)
    m.output("wb0_valid", pipe_memwb0.valid)
    m.output("wb1_valid", pipe_memwb1.valid)
    m.output("wb0_pc", pipe_memwb0.pc)
    m.output("wb1_pc", pipe_memwb1.pc)
    m.output("wb0_op", pipe_memwb0.op)
    m.output("wb1_op", pipe_memwb1.op)
    wb_window = pipe_memwb0.window.out()
    wb_op_out = pipe_memwb0.op.out()
    wb_regdst_out = pipe_memwb0.regdst.out()
    wb_value_out = pipe_memwb0.value.out()
    if ~pipe_memwb0.valid.out() & pipe_memwb1.valid.out():
        wb_window = pipe_memwb1.window.out()
        wb_op_out = pipe_memwb1.op.out()
        wb_regdst_out = pipe_memwb1.regdst.out()
        wb_value_out = pipe_memwb1.value.out()
    m.output("if_window", wb_window)
    m.output("wb_op", wb_op_out)
    m.output("wb_regdst", wb_regdst_out)
    m.output("wb_value", wb_value_out)
    m.output("commit_cond", state.commit_cond)
    m.output("commit_tgt", state.commit_tgt)


# Preserve the historical top/module name expected by existing testbenches.
build.__pycircuit_name__ = "linx_cpu_pyc"
