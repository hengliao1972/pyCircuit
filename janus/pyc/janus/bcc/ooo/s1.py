from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire

from .helpers import mask_bit


@dataclass(frozen=True)
class S1Out:
    src_ready: Wire


def s1_ready_check(m: Circuit, *, ready_mask: Wire, sl: Wire, sr: Wire, sp: Wire, preg_count: int) -> S1Out:
    sl = m.wire(sl)
    sr = m.wire(sr)
    sp = m.wire(sp)
    ready_mask = m.wire(ready_mask)
    sl_rdy = mask_bit(m, mask=ready_mask, idx=sl, width=preg_count)
    sr_rdy = mask_bit(m, mask=ready_mask, idx=sr, width=preg_count)
    sp_rdy = mask_bit(m, mask=ready_mask, idx=sp, width=preg_count)
    return S1Out(src_ready=sl_rdy & sr_rdy & sp_rdy)
