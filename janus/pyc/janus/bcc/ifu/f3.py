from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class F3Out:
    window: Wire
    intra_flush: Wire
    cut_mask: Wire


def run_f3(m: Circuit, *, window: Wire, pred_taken: Wire) -> F3Out:
    window = m.wire(window)
    pred_taken = m.wire(pred_taken)
    # Bring-up mask: if we predict taken, expose only the lower half of the 64b window.
    cut_mask = pred_taken.select(m.const(0x0000_0000_FFFF_FFFF, width=64), m.const(0xFFFF_FFFF_FFFF_FFFF, width=64))
    return F3Out(window=window & cut_mask, intra_flush=pred_taken, cut_mask=cut_mask)
