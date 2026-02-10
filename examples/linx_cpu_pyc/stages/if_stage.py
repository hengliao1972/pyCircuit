from __future__ import annotations

from pycircuit import Circuit, Wire, jit_inline

from ..pipeline import IfIdRegs


@jit_inline
def build_if_stage(m: Circuit, *, do_if: Wire, ifid: IfIdRegs, fetch_pc: Wire, mem_rdata: Wire, pred_next_pc: Wire) -> None:
    # IF stage: latch instruction window + PC.
    with m.scope("IF"):
        ifid.pc.set(fetch_pc, when=do_if)
        ifid.window.set(mem_rdata, when=do_if)
        ifid.pred_next_pc.set(pred_next_pc, when=do_if)
