from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class FlushOut:
    flush_valid: Wire
    flush_pc: Wire


def arbitrate_flush(
    m: Circuit,
    *,
    bru_redirect_valid: Wire,
    bru_redirect_pc: Wire,
    lsu_nuke_valid: Wire,
    lsu_nuke_pc: Wire,
    exc_valid: Wire,
    exc_pc: Wire,
) -> FlushOut:
    c = m.const
    bru_redirect_valid = m.wire(bru_redirect_valid)
    bru_redirect_pc = m.wire(bru_redirect_pc)
    lsu_nuke_valid = m.wire(lsu_nuke_valid)
    lsu_nuke_pc = m.wire(lsu_nuke_pc)
    exc_valid = m.wire(exc_valid)
    exc_pc = m.wire(exc_pc)

    flush_pc = c(0, width=64)
    flush_pc = bru_redirect_valid.select(bru_redirect_pc, flush_pc)
    flush_pc = lsu_nuke_valid.select(lsu_nuke_pc, flush_pc)
    flush_pc = exc_valid.select(exc_pc, flush_pc)
    flush_valid = bru_redirect_valid | lsu_nuke_valid | exc_valid
    return FlushOut(flush_valid=flush_valid, flush_pc=flush_pc)
