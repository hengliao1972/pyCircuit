from __future__ import annotations

from pycircuit import Circuit, Wire


def liq_can_accept(m: Circuit, *, count: Wire, depth: int) -> Wire:
    count = m.wire(count)
    return count.ult(m.const(depth, width=count.width))
