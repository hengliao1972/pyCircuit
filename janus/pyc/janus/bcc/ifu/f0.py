from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class F0Out:
    pc: Wire
    bubble: Wire


def select_pc(
    m: Circuit,
    *,
    seq_pc: Wire,
    redirect_valid: Wire,
    redirect_pc: Wire,
    flush_valid: Wire,
    flush_pc: Wire,
) -> F0Out:
    seq_pc = m.wire(seq_pc)
    redirect_valid = m.wire(redirect_valid)
    redirect_pc = m.wire(redirect_pc)
    flush_valid = m.wire(flush_valid)
    flush_pc = m.wire(flush_pc)
    pc = redirect_valid.select(redirect_pc, seq_pc)
    pc = flush_valid.select(flush_pc, pc)
    bubble = redirect_valid | flush_valid
    return F0Out(pc=pc, bubble=bubble)
