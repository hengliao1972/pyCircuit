from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareSignal

from examples.linx_cpu_pyc_cycle_aware.decode import decode_window
from examples.linx_cpu_pyc_cycle_aware.pipeline import IdExRegs, IfIdRegs, RegFiles
from examples.linx_cpu_pyc_cycle_aware.regfile import read_reg
from examples.linx_cpu_pyc_cycle_aware.util import Consts


def build_id_stage(
    m: CycleAwareCircuit,
    *,
    do_id: CycleAwareSignal,
    ifid: IfIdRegs,
    idex: IdExRegs,
    rf: RegFiles,
    consts: Consts,
    fetch_pc_reg=None,
) -> None:
    # Stage inputs.
    window = ifid.window.out()
    ifid_pc = ifid.pc.out()

    # Combinational decode.
    dec = decode_window(m, window)

    # Pipeline regs: ID/EX.
    op = dec.op
    len_bytes = dec.len_bytes
    regdst = dec.regdst
    srcl = dec.srcl
    srcr = dec.srcr
    srcp = dec.srcp
    imm = dec.imm

    idex.op.set(op, when=do_id)
    idex.len_bytes.set(len_bytes, when=do_id)
    idex.pc.set(ifid_pc, when=do_id)
    idex.regdst.set(regdst, when=do_id)
    idex.srcl.set(srcl, when=do_id)
    idex.srcr.set(srcr, when=do_id)
    idex.srcp.set(srcp, when=do_id)
    idex.imm.set(imm, when=do_id)

    # Read register file values (mux-based, strict defaulting).
    srcl_val = read_reg(m, srcl, gpr=rf.gpr, t=rf.t, u=rf.u, default=consts.zero64)
    srcr_val = read_reg(m, srcr, gpr=rf.gpr, t=rf.t, u=rf.u, default=consts.zero64)
    srcp_val = read_reg(m, srcp, gpr=rf.gpr, t=rf.t, u=rf.u, default=consts.zero64)

    idex.srcl_val.set(srcl_val, when=do_id)
    idex.srcr_val.set(srcr_val, when=do_id)
    idex.srcp_val.set(srcp_val, when=do_id)
    if fetch_pc_reg is not None:
        next_fetch_pc = ifid_pc + len_bytes.zext(width=64)
        fetch_pc_reg.set(next_fetch_pc, when=do_id)
