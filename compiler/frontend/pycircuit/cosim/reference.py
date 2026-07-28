"""Placeholder golden ISA model for the B2 lockstep harness.

This stands in for the not-yet-available ASL/ISA interpreter. It implements the
:class:`~pycircuit.cosim.lockstep.GoldenModel` protocol, so swapping in a real
interpreter later is a drop-in replacement -- the harness only ever sees the
protocol.

It is a genuine *executor*, not a replay of the DUT stream: it walks the shared
instruction stream, maintains an architectural register file, applies each
instruction's semantics, and emits a canonical commit record. Fed the same
program as the DUT, an honest DUT matches it commit-for-commit; a DUT that
mis-forwards or mis-computes a writeback diverges at the first offending commit.

The default semantics are deliberately minimal (immediate-writeback, i.e. the
NOP/`li`-style behavior of the passthrough B1 demo design), but semantics are
pluggable so richer toy ISAs (ADDI, etc.) can be modeled without touching the
harness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from .lockstep import Instr

# effect := {"wb_valid": int, "wb_rd": int, "wb_data": int}
Semantics = Callable[["list[int]", Instr], Mapping[str, int]]


def immediate_writeback(regs: list[int], instr: Instr) -> Mapping[str, int]:
    """`li rd, imm` style: write the immediate operand into rd.

    Operands consumed: ``wb`` (1 to write back), ``rd`` (dest reg), ``imm``
    (value). Independent of prior register state, but exercises the architectural
    write path so state-carrying ISAs slot in unchanged.
    """
    ops = instr.operands
    wb = int(ops.get("wb", 0))
    rd = int(ops.get("rd", 0))
    imm = int(ops.get("imm", 0))
    return {"wb_valid": wb, "wb_rd": rd, "wb_data": imm}


def addi(regs: list[int], instr: Instr) -> Mapping[str, int]:
    """`addi rd, rs1, imm`: rd <- regs[rs1] + imm. Demonstrates real state use."""
    ops = instr.operands
    wb = int(ops.get("wb", 1))
    rd = int(ops.get("rd", 0))
    rs1 = int(ops.get("rs1", 0))
    imm = int(ops.get("imm", 0))
    return {"wb_valid": wb, "wb_rd": rd, "wb_data": regs[rs1] + imm}


@dataclass
class ReferenceModel:
    """Minimal architectural golden model (GoldenModel protocol impl)."""

    nregs: int = 32
    data_mask: int = (1 << 32) - 1
    semantics: Semantics = immediate_writeback
    # If True, writes to x0 are discarded (RISC-V-style hardwired zero). The
    # passthrough demo does not rely on this, so it defaults off to stay neutral.
    x0_hardwired: bool = False

    def __post_init__(self) -> None:
        self.regs: list[int] = [0] * self.nregs

    def reset(self) -> None:
        self.regs = [0] * self.nregs

    def step(self, instr: Instr) -> Mapping[str, int] | None:
        if not instr.retire:
            return None  # bubble / squashed -> no commit record
        eff = self.semantics(self.regs, instr)
        wb_valid = int(eff.get("wb_valid", 0))
        wb_rd = int(eff.get("wb_rd", 0))
        wb_data = int(eff.get("wb_data", 0)) & self.data_mask
        if wb_valid and not (self.x0_hardwired and wb_rd == 0):
            if 0 <= wb_rd < self.nregs:
                self.regs[wb_rd] = wb_data
        return {
            "pc": int(instr.pc),
            "insn": int(instr.insn),
            "wb_valid": wb_valid,
            "wb_rd": wb_rd,
            "wb_data": wb_data,
        }

    def run(self, program: Iterable[Instr]) -> list[Mapping[str, int]]:
        out: list[Mapping[str, int]] = []
        for instr in program:
            rec = self.step(instr)
            if rec is not None:
                out.append(rec)
        return out
