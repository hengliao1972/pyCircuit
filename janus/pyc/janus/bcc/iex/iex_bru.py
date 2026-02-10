from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class BruOut:
    redirect_valid: Wire
    redirect_pc: Wire


def run_bru(m: Circuit, *, cond: Wire, base_pc: Wire, off: Wire) -> BruOut:
    c = m.const
    cond = m.wire(cond)
    base_pc = m.wire(base_pc)
    off = m.wire(off)
    target = base_pc + off
    return BruOut(redirect_valid=cond, redirect_pc=cond.select(target, c(0, width=64)))
