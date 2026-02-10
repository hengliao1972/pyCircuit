from __future__ import annotations

from pycircuit import Circuit, Wire, jit_inline

from ..decode import decode_window
from ..isa import REG_INVALID
from ..pipeline import IdExRegs, IfIdRegs, RegFiles
from ..regfile import read_reg
from ..util import Consts


@jit_inline
def build_id_stage(
    m: Circuit,
    *,
    do_id: Wire,
    ifid: IfIdRegs,
    idex: IdExRegs,
    rf: RegFiles,
    consts: Consts,
    # WB->ID bypass (regfile read vs same-cycle writeback hazard).
    wb0_fwd_valid: Wire,
    wb0_fwd_regdst: Wire,
    wb0_fwd_value: Wire,
    wb1_fwd_valid: Wire,
    wb1_fwd_regdst: Wire,
    wb1_fwd_value: Wire,
) -> None:
    with m.scope("ID"):
        # Stage inputs.
        pc = ifid.pc.out()
        window = ifid.window.out()
        pred_next_pc = ifid.pred_next_pc.out()

        # Combinational decode.
        dec = decode_window(m, window)

        # Pipeline regs: ID/EX.
        idex.pc.set(pc, when=do_id)
        idex.window.set(window, when=do_id)
        idex.pred_next_pc.set(pred_next_pc, when=do_id)

        op = dec.op
        len_bytes = dec.len_bytes
        regdst = dec.regdst
        srcl = dec.srcl
        srcr = dec.srcr
        srcr_type = dec.srcr_type
        shamt = dec.shamt
        srcp = dec.srcp
        imm = dec.imm

        idex.op.set(op, when=do_id)
        idex.len_bytes.set(len_bytes, when=do_id)
        idex.regdst.set(regdst, when=do_id)
        idex.srcl.set(srcl, when=do_id)
        idex.srcr.set(srcr, when=do_id)
        idex.srcr_type.set(srcr_type, when=do_id)
        idex.shamt.set(shamt, when=do_id)
        idex.srcp.set(srcp, when=do_id)
        idex.imm.set(imm, when=do_id)

        # Read register file values (mux-based, strict defaulting).
        srcl_val = read_reg(m, srcl, gpr=rf.gpr, t=rf.t, u=rf.u, default=consts.zero64)
        srcr_val = read_reg(m, srcr, gpr=rf.gpr, t=rf.t, u=rf.u, default=consts.zero64)
        srcp_val = read_reg(m, srcp, gpr=rf.gpr, t=rf.t, u=rf.u, default=consts.zero64)

        # WB->ID bypass for GPR codes (0..23). This avoids capturing a stale
        # regfile value into ID/EX when the same GPR is written back in WB
        # during this cycle.
        can_bypass_wb0 = (
            wb0_fwd_valid
            & (wb0_fwd_regdst != REG_INVALID)
            & (wb0_fwd_regdst != 0)
            & wb0_fwd_regdst.ult(24)
        )
        can_bypass_wb1 = (
            wb1_fwd_valid
            & (wb1_fwd_regdst != REG_INVALID)
            & (wb1_fwd_regdst != 0)
            & wb1_fwd_regdst.ult(24)
        )

        # Priority: younger WB lane (wb1) overrides older (wb0) on matches.
        if can_bypass_wb0 & (wb0_fwd_regdst == srcl):
            srcl_val = wb0_fwd_value
        if can_bypass_wb1 & (wb1_fwd_regdst == srcl):
            srcl_val = wb1_fwd_value

        if can_bypass_wb0 & (wb0_fwd_regdst == srcr):
            srcr_val = wb0_fwd_value
        if can_bypass_wb1 & (wb1_fwd_regdst == srcr):
            srcr_val = wb1_fwd_value

        if can_bypass_wb0 & (wb0_fwd_regdst == srcp):
            srcp_val = wb0_fwd_value
        if can_bypass_wb1 & (wb1_fwd_regdst == srcp):
            srcp_val = wb1_fwd_value

        idex.srcl_val.set(srcl_val, when=do_id)
        idex.srcr_val.set(srcr_val, when=do_id)
        idex.srcp_val.set(srcp_val, when=do_id)
