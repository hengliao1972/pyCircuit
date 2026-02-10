from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.dsl import Signal


@dataclass(frozen=True)
class RingPipeRegs:
    valid: Reg
    brob: Reg
    payload: Reg


@dataclass(frozen=True)
class RingPipeOut:
    valid: Wire
    brob: Wire
    payload: Wire


def build_ring_pipe(
    m: Circuit,
    *,
    clk: Signal,
    rst: Signal,
    in_valid: Wire,
    in_brob: Wire,
    in_payload: Wire,
    name: str,
) -> RingPipeOut:
    in_valid = m.wire(in_valid)
    in_brob = m.wire(in_brob)
    in_payload = m.wire(in_payload)

    with m.scope(name):
        regs = RingPipeRegs(
            valid=m.out("v", clk=clk, rst=rst, width=1, init=0, en=1),
            brob=m.out("bid", clk=clk, rst=rst, width=8, init=0, en=1),
            payload=m.out("pl", clk=clk, rst=rst, width=64, init=0, en=1),
        )

    regs.valid.set(in_valid)
    regs.brob.set(in_brob)
    regs.payload.set(in_payload)
    return RingPipeOut(valid=regs.valid.out(), brob=regs.brob.out(), payload=regs.payload.out())
