from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class AguOut:
    addr: Wire


def run_agu(m: Circuit, *, base: Wire, offset: Wire, scale_shift: int = 0) -> AguOut:
    base = m.wire(base)
    offset = m.wire(offset)
    scaled = offset.shl(amount=scale_shift) if scale_shift > 0 else offset
    return AguOut(addr=base + scaled)
