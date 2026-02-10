from __future__ import annotations

from pycircuit import Circuit

# This design is written in "JIT mode": `pycircuit.cli emit` will compile
# `build(m: Circuit, ...)` via the AST/SCF frontend.

from janus.bcc.isa import BK_FALL, OP_EBREAK, OP_INVALID, REG_INVALID, ST_EX, ST_ID, ST_IF, ST_MEM, ST_WB
from janus.bcc.memory import build_byte_mem
from janus.bcc.pipeline import CoreState, ExMemRegs, IdExRegs, IfIdRegs, MemWbRegs, RegFiles
from janus.bcc.regfile import make_gpr, make_regs
from janus.bcc.stages.ex_stage import build_ex_stage
from janus.bcc.stages.id_stage import build_id_stage
from janus.bcc.stages.if_stage import build_if_stage
from janus.bcc.stages.mem_stage import build_mem_stage
from janus.bcc.stages.wb_stage import build_wb_stage
from janus.bcc.util import make_consts


def build(m: Circuit, *, mem_bytes: int = (1 << 20)) -> None:
    # --- ports ---
    clk = m.clock("clk")
    rst = m.reset("rst")

    boot_pc = m.input("boot_pc", width=64)
    boot_sp = m.input("boot_sp", width=64)

    c = m.const
    consts = make_consts(m)

    # --- core state regs (named) ---
    with m.scope("state"):
        state = CoreState(
            stage=m.out("stage", clk=clk, rst=rst, width=3, init=c(ST_IF, width=3), en=consts.one1),
            pc=m.out("pc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1),
            br_kind=m.out("br_kind", clk=clk, rst=rst, width=2, init=c(BK_FALL, width=2), en=consts.one1),
            br_base_pc=m.out("br_base_pc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1),
            br_off=m.out("br_off", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            commit_cond=m.out("commit_cond", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1),
            commit_tgt=m.out("commit_tgt", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            cycles=m.out("cycles", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            halted=m.out("halted", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1),
        )

    with m.scope("ifid"):
        pipe_ifid = IfIdRegs(window=m.out("window", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1))

    with m.scope("idex"):
        pipe_idex = IdExRegs(
            op=m.out("op", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=consts.zero3, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=c(REG_INVALID, width=6), en=consts.one1),
            srcl=m.out("srcl", clk=clk, rst=rst, width=6, init=c(REG_INVALID, width=6), en=consts.one1),
            srcr=m.out("srcr", clk=clk, rst=rst, width=6, init=c(REG_INVALID, width=6), en=consts.one1),
            srcp=m.out("srcp", clk=clk, rst=rst, width=6, init=c(REG_INVALID, width=6), en=consts.one1),
            imm=m.out("imm", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            srcl_val=m.out("srcl_val", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            srcr_val=m.out("srcr_val", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            srcp_val=m.out("srcp_val", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
        )

    with m.scope("exmem"):
        pipe_exmem = ExMemRegs(
            op=m.out("op", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=consts.zero3, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=c(REG_INVALID, width=6), en=consts.one1),
            alu=m.out("alu", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            is_load=m.out("is_load", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1),
            is_store=m.out("is_store", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1),
            size=m.out("size", clk=clk, rst=rst, width=3, init=consts.zero3, en=consts.one1),
            addr=m.out("addr", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
            wdata=m.out("wdata", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
        )

    with m.scope("memwb"):
        pipe_memwb = MemWbRegs(
            op=m.out("op", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1),
            len_bytes=m.out("len_bytes", clk=clk, rst=rst, width=3, init=consts.zero3, en=consts.one1),
            regdst=m.out("regdst", clk=clk, rst=rst, width=6, init=c(REG_INVALID, width=6), en=consts.one1),
            value=m.out("value", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1),
        )

    # --- register files ---
    with m.scope("gpr"):
        gpr = make_gpr(m, clk, rst, boot_sp=boot_sp, en=consts.one1)
    with m.scope("t"):
        t = make_regs(m, clk, rst, count=4, width=64, init=consts.zero64, en=consts.one1)
    with m.scope("u"):
        u = make_regs(m, clk, rst, count=4, width=64, init=consts.zero64, en=consts.one1)

    rf = RegFiles(gpr=gpr, t=t, u=u)

    # --- stage control ---
    stage_is_if = state.stage.eq(c(ST_IF, width=3))
    stage_is_id = state.stage.eq(c(ST_ID, width=3))
    stage_is_ex = state.stage.eq(c(ST_EX, width=3))
    stage_is_mem = state.stage.eq(c(ST_MEM, width=3))
    stage_is_wb = state.stage.eq(c(ST_WB, width=3))

    halt_set = stage_is_wb & (~state.halted) & (pipe_memwb.op.eq(c(OP_EBREAK, width=6)) | pipe_memwb.op.eq(c(OP_INVALID, width=6)))
    stop = state.halted | halt_set
    active = ~stop

    do_if = stage_is_if & active
    do_id = stage_is_id & active
    do_ex = stage_is_ex & active
    do_mem = stage_is_mem & active
    do_wb = stage_is_wb & active

    # --- unified byte memory (instruction + data) ---
    mem_raddr = do_if.select(state.pc, (stage_is_mem & active & pipe_exmem.is_load).select(pipe_exmem.addr, consts.zero64))
    mem_wvalid = stage_is_mem & active & pipe_exmem.is_store
    mem_waddr = pipe_exmem.addr
    mem_wdata = pipe_exmem.wdata
    mem_wstrb = pipe_exmem.size.eq(c(8, width=3)).select(c(0xFF, width=8), consts.zero8)
    mem_wstrb = pipe_exmem.size.eq(c(4, width=3)).select(c(0x0F, width=8), mem_wstrb)

    mem_rdata = build_byte_mem(
        m,
        clk,
        rst,
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
    build_ex_stage(m, do_ex=do_ex, pc=state.pc, idex=pipe_idex, exmem=pipe_exmem, consts=consts)
    build_mem_stage(m, do_mem=do_mem, exmem=pipe_exmem, memwb=pipe_memwb, mem_rdata=mem_rdata, consts=consts)
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
        consts=consts,
    )

    # --- outputs ---
    m.output("halted", state.halted)
    m.output("pc", state.pc)
    m.output("stage", state.stage)
    m.output("cycles", state.cycles)
    m.output("a0", rf.gpr[2])
    m.output("a1", rf.gpr[3])
    m.output("ra", rf.gpr[10])
    m.output("sp", rf.gpr[1])
    m.output("br_kind", state.br_kind)
    # Debug/trace hooks (stable, optional consumers).
    m.output("if_window", pipe_ifid.window)
    m.output("wb_op", pipe_memwb.op)
    m.output("wb_regdst", pipe_memwb.regdst)
    m.output("wb_value", pipe_memwb.value)
    m.output("commit_cond", state.commit_cond)
    m.output("commit_tgt", state.commit_tgt)


build.__pycircuit_name__ = "janus_bcc_pyc"
