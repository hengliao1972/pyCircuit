from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class F2Out:
    window: Wire
    pred_taken: Wire
    pred_target: Wire


def run_f2(m: Circuit, *, icache_data: Wire, early_taken: Wire, early_target: Wire, tage_taken: Wire, tage_target: Wire) -> F2Out:
    icache_data = m.wire(icache_data)
    early_taken = m.wire(early_taken)
    early_target = m.wire(early_target)
    tage_taken = m.wire(tage_taken)
    tage_target = m.wire(tage_target)
    pred_taken = tage_taken.select(tage_taken, early_taken)
    pred_target = tage_taken.select(tage_target, early_target)
    return F2Out(window=icache_data, pred_taken=pred_taken, pred_target=pred_target)
