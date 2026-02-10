from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class FsuOut:
    valid: Wire
    value: Wire


def run_fsu(m: Circuit, *, fire: Wire, src: Wire, imm: Wire) -> FsuOut:
    fire = m.wire(fire)
    src = m.wire(src)
    imm = m.wire(imm)
    # Bring-up behavior: system lane applies additive immediate transformation.
    return FsuOut(valid=fire, value=src + imm)
