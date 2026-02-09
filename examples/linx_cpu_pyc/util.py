from __future__ import annotations

from dataclasses import dataclass

from pycircuit import CycleAwareCircuit, CycleAwareDomain, CycleAwareSignal


@dataclass(frozen=True)
class Consts:
    one1: CycleAwareSignal
    zero1: CycleAwareSignal
    zero3: CycleAwareSignal
    zero6: CycleAwareSignal
    zero8: CycleAwareSignal
    zero32: CycleAwareSignal
    zero64: CycleAwareSignal
    one64: CycleAwareSignal


def make_consts(m: CycleAwareCircuit, domain: CycleAwareDomain) -> Consts:
    c = domain.const
    return Consts(
        one1=c(1, width=1),
        zero1=c(0, width=1),
        zero3=c(0, width=3),
        zero6=c(0, width=6),
        zero8=c(0, width=8),
        zero32=c(0, width=32),
        zero64=c(0, width=64),
        one64=c(1, width=64),
    )


def masked_eq(x: CycleAwareSignal, *, mask: int, match: int) -> CycleAwareSignal:
    return (x & int(mask)).eq(int(match))
