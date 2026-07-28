"""Lockstep co-simulation harness (TODO B2).

Compares a design's B1 commit/retire stream against an abstract golden model in
retire order and reports the first differing commit (the minimal counterexample
for the optimization loop). The harness depends only on the :class:`GoldenModel`
protocol; :class:`ReferenceModel` is a placeholder standing in for a real
ASL/ISA interpreter.
"""

from .lockstep import (
    CommitProfile,
    CommitRow,
    CommitTrace,
    GoldenModel,
    Instr,
    LockstepComparator,
    LockstepReport,
    Mismatch,
    load_commit_jsonl,
    parse_commit_jsonl,
    run_lockstep,
)
from .mincex import (
    FailureSignature,
    MinimizeResult,
    ddmin,
    minimize_lockstep,
)
from .reference import ReferenceModel, addi, immediate_writeback

__all__ = [
    "CommitProfile",
    "CommitRow",
    "CommitTrace",
    "GoldenModel",
    "Instr",
    "LockstepComparator",
    "LockstepReport",
    "Mismatch",
    "load_commit_jsonl",
    "parse_commit_jsonl",
    "run_lockstep",
    "ReferenceModel",
    "addi",
    "immediate_writeback",
    "FailureSignature",
    "MinimizeResult",
    "ddmin",
    "minimize_lockstep",
]
