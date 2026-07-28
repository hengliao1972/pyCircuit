"""Counterexample minimization via delta-debugging (TODO B4; §5.1).

When the B2 lockstep referee flags a divergence, the raw failing stimulus can be
huge. This module shrinks it to a *minimal* instruction stream that still
reproduces the *same* failure, so the agent is handed "these 3 instructions, and
the 3rd commit's wb_data disagrees" instead of a 2 GB waveform.

Two pieces, mirroring the B1/B2 decoupling:

* :func:`ddmin` -- a generic, backend-agnostic Zeller delta-debugging engine over
  any sequence, driven by an opaque oracle predicate ``oracle(subseq) -> bool``
  (``True`` == the subsequence still reproduces the target failure). It knows
  nothing about instructions or lockstep.
* :func:`minimize_lockstep` -- wires that engine to the B2 harness: it reruns the
  golden and DUT models over each candidate subsequence, compares in retire
  order, and only counts a subsequence as reproducing when its first divergence
  matches the *original* failure signature (kind, field and offending pc). This
  signature lock keeps minimization from drifting onto a different bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .lockstep import (
    CommitProfile,
    CommitRow,
    GoldenModel,
    Instr,
    LockstepComparator,
    LockstepReport,
    Mismatch,
)

# oracle(subseq) -> True iff the subsequence still reproduces the target failure.
Oracle = Callable[[Sequence["Instr"]], bool]


# --------------------------------------------------------------------------- #
# Generic delta-debugging engine (Zeller ddmin) -> 1-minimal subsequence.
# --------------------------------------------------------------------------- #
def ddmin(items: Sequence, oracle: Callable[[Sequence], bool]) -> list:
    """Return a 1-minimal sub-sequence of ``items`` for which ``oracle`` is True.

    Precondition: ``oracle(items)`` is True. If it is not, the full input is
    returned unchanged (nothing to minimize against).
    """
    c = list(items)
    if not c or not oracle(c):
        return c

    n = 2
    while len(c) >= 2:
        chunk = max(1, len(c) // n)
        parts = [c[i:i + chunk] for i in range(0, len(c), chunk)]

        # 1) can a single chunk alone reproduce? (reduce to subset)
        for p in parts:
            if p and len(p) < len(c) and oracle(p):
                c = p
                n = 2
                break
        else:
            # 2) does removing a single chunk still reproduce? (reduce complement)
            for i in range(len(parts)):
                comp = [x for j, part in enumerate(parts) if j != i for x in part]
                if comp and len(comp) < len(c) and oracle(comp):
                    c = comp
                    n = max(n - 1, 2)
                    break
            else:
                # 3) increase granularity, or stop when already at unit chunks
                if n >= len(c):
                    break
                n = min(len(c), n * 2)
    return c


# --------------------------------------------------------------------------- #
# Lockstep-backed minimization (wires ddmin to the B2 referee).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FailureSignature:
    """Identity of a failure, robust to the instruction's position in the stream."""

    kind: str
    field: str | None
    pc: int | None

    @staticmethod
    def of(report: LockstepReport) -> "FailureSignature | None":
        mm = report.first_mismatch
        if mm is None:
            return None
        return FailureSignature(kind=mm.kind, field=mm.field, pc=mm.pc)


@dataclass
class MinimizeResult:
    reproduced: bool  # did the original stimulus reproduce a failure at all?
    minimal: list[Instr]
    original_len: int
    minimal_len: int
    oracle_calls: int
    signature: FailureSignature | None
    report: LockstepReport | None  # lockstep report for the minimal stimulus

    @property
    def first_mismatch(self) -> Mismatch | None:
        return self.report.first_mismatch if self.report else None

    def to_dict(self) -> dict:
        d: dict = {
            "reproduced": self.reproduced,
            "original_len": self.original_len,
            "minimal_len": self.minimal_len,
            "oracle_calls": self.oracle_calls,
        }
        if self.signature is not None:
            d["signature"] = {
                "kind": self.signature.kind,
                "field": self.signature.field,
                "pc": self.signature.pc,
            }
        if self.report is not None and self.report.first_mismatch is not None:
            d["first_mismatch"] = self.report.first_mismatch.to_dict()
        d["minimal_program"] = [
            {"pc": i.pc, "insn": i.insn, "retire": bool(i.retire),
             "operands": dict(i.operands)}
            for i in self.minimal
        ]
        return d


def _run_pair(
    golden: GoldenModel, dut: GoldenModel, profile: CommitProfile,
    program: Sequence[Instr],
) -> LockstepReport:
    golden.reset()
    dut.reset()
    grows = golden.run(program)
    drows = [CommitRow(fields=dict(r)) for r in dut.run(program)]
    return LockstepComparator(profile).compare(drows, grows)


def minimize_lockstep(
    program: Sequence[Instr],
    golden: GoldenModel,
    dut: GoldenModel,
    profile: CommitProfile | dict,
    *,
    match_signature: bool = True,
) -> MinimizeResult:
    """Shrink ``program`` to a minimal stream still reproducing the DUT-vs-golden
    failure. ``golden``/``dut`` are stateful models (reset between runs)."""
    prof = profile if isinstance(profile, CommitProfile) else CommitProfile.from_dict(profile)

    full_report = _run_pair(golden, dut, prof, program)
    if full_report.ok:
        return MinimizeResult(
            reproduced=False,
            minimal=list(program),
            original_len=len(program),
            minimal_len=len(program),
            oracle_calls=1,
            signature=None,
            report=full_report,
        )

    target = FailureSignature.of(full_report)
    calls = 1  # counting the full-program run above

    def oracle(subseq: Sequence[Instr]) -> bool:
        nonlocal calls
        calls += 1
        rep = _run_pair(golden, dut, prof, subseq)
        if rep.ok:
            return False
        if not match_signature:
            return True
        return FailureSignature.of(rep) == target

    minimal = ddmin(list(program), oracle)
    final_report = _run_pair(golden, dut, prof, minimal)

    return MinimizeResult(
        reproduced=True,
        minimal=minimal,
        original_len=len(program),
        minimal_len=len(minimal),
        oracle_calls=calls,
        signature=target,
        report=final_report,
    )
