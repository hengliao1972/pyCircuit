from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class StdOut:
    valid: Wire
    data: Wire


def run_std(m: Circuit, *, fire: Wire, src: Wire) -> StdOut:
    fire = m.wire(fire)
    src = m.wire(src)
    return StdOut(valid=fire, data=src)
