"""Lockstep co-simulation harness (TODO B2; agentic optimizer §3.4 level-1).

The equivalence "referee" of the optimization loop: feed the same instruction
stream to the design-under-test (DUT) and to a golden ISA model, then compare
their commit/retire streams *in retire order*. The first differing commit is the
minimal counterexample handed to the agent.

Design principles (mirroring B1):

* Dependency inversion -- the harness depends only on the abstract
  :class:`GoldenModel` protocol, never on a concrete interpreter. A placeholder
  reference model stands in today; a real ASL/ISA interpreter becomes a drop-in
  replacement by implementing the same protocol, with zero harness changes.
* Schema-agnostic -- which fields must match and which are validity-gated is
  supplied as *data* (the B1 commit profile: ``required`` + ``groups``). The
  comparator hard-codes no CPU/ISA vocabulary.
* Commit-order alignment -- the k-th retired instruction of the DUT is compared
  against the k-th step of the golden model. This naturally absorbs pipeline
  bubbles, multi-cycle stalls and (in-order) retirement without any cycle
  bookkeeping; the ISA model has no notion of cycles.

The DUT commit stream is exactly the B1 commit-bundle JSONL
(``{"type":"start",...}`` header followed by one object per retired
instruction). See :func:`load_commit_jsonl`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable

# The per-instruction commit strobe. Its assertion is what makes a row *exist*
# in the commit stream, so it is never itself a comparable bundle field
# (identical convention to runtime/cpp/pyc_commit_trace.hpp::kValidField).
ROW_STROBE = "valid"


# --------------------------------------------------------------------------- #
# Commit profile (schema-agnostic contract carried as data, from B1).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CommitProfile:
    """The schema-specific contract, supplied as data (never hard-coded).

    ``required`` -- fields every commit record must carry / that must match.
    ``groups``   -- validity-gated groups: ``{name: {"valid": strobe,
                    "members": [fields...]}}``. A group's member fields are only
                    compared on records where the group's ``valid`` strobe is
                    asserted (Decision 0146).
    """

    schema: str
    required: tuple[str, ...] = ()
    groups: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "CommitProfile":
        groups = {
            name: {"valid": spec["valid"], "members": tuple(spec.get("members", ()))}
            for name, spec in dict(d.get("groups", {})).items()
        }
        return CommitProfile(
            schema=str(d["schema"]),
            required=tuple(d.get("required", ())),
            groups=groups,
        )

    def comparable_fields(self) -> list[str]:
        """All fields that participate in comparison, minus the row strobe.

        Order is deterministic: required first (in declared order), then any
        group members / group strobes not already covered.
        """
        seen: dict[str, None] = {}
        for f in self.required:
            if f != ROW_STROBE:
                seen.setdefault(f, None)
        for spec in self.groups.values():
            v = spec["valid"]
            if v != ROW_STROBE:
                seen.setdefault(v, None)
            for m in spec["members"]:
                if m != ROW_STROBE:
                    seen.setdefault(m, None)
        return list(seen.keys())

    def _member_to_group_valid(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for spec in self.groups.values():
            for m in spec["members"]:
                out[m] = spec["valid"]
        return out


# --------------------------------------------------------------------------- #
# Commit stream (DUT side): the B1 commit-bundle JSONL.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CommitRow:
    """One retired instruction as observed on the commit interface."""

    fields: Mapping[str, int]
    cycle: int | None = None
    stage: str | None = None

    def get(self, name: str, default: int = 0) -> int:
        return int(self.fields.get(name, default))


@dataclass(frozen=True)
class CommitTrace:
    schema: str
    rows: tuple[CommitRow, ...]

    def __len__(self) -> int:
        return len(self.rows)


def _row_from_obj(obj: Mapping[str, Any]) -> CommitRow:
    cycle = obj.get("cycle")
    stage = obj.get("stage")
    fields = {
        k: int(v)
        for k, v in obj.items()
        if k not in ("cycle", "stage", "type") and isinstance(v, (int, bool))
    }
    return CommitRow(fields=fields, cycle=None if cycle is None else int(cycle), stage=stage)


def load_commit_jsonl(path: str | Path) -> CommitTrace:
    """Parse a B1 commit-bundle JSONL file into a :class:`CommitTrace`."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_commit_jsonl(text)


def parse_commit_jsonl(text: str) -> CommitTrace:
    schema = ""
    rows: list[CommitRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("type") == "start":
            schema = str(obj.get("commit_schema_id", ""))
            continue
        rows.append(_row_from_obj(obj))
    return CommitTrace(schema=schema, rows=tuple(rows))


# --------------------------------------------------------------------------- #
# Golden model protocol (the abstract reference end).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Instr:
    """One entry of the shared instruction stream driven into both ends.

    ``retire`` is the architectural commit strobe: the golden model produces a
    commit record for this instruction iff ``retire`` is truthy (matching how
    the DUT only emits a row when its ``valid`` strobe fires).
    ``pc`` / ``insn`` identify the instruction; ``operands`` carries any extra
    inputs a real ISA model would need (register values, immediates, ...).
    """

    pc: int
    insn: int
    retire: bool = True
    operands: Mapping[str, int] = field(default_factory=dict)


@runtime_checkable
class GoldenModel(Protocol):
    """Abstract golden reference. A real ASL/ISA interpreter implements this."""

    def reset(self) -> None:
        ...

    def step(self, instr: Instr) -> Mapping[str, int] | None:
        """Execute one instruction; return its commit record, or ``None`` if the
        instruction does not retire (bubble / squashed)."""
        ...

    def run(self, program: Iterable[Instr]) -> list[Mapping[str, int]]:
        ...


# --------------------------------------------------------------------------- #
# Lockstep comparison + structured report.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Mismatch:
    index: int  # retire-order index of the differing commit
    kind: str  # "field" | "missing_dut" | "missing_golden"
    field: str | None
    dut: int | None
    golden: int | None
    cycle: int | None = None  # DUT-side cycle for locating the failure
    pc: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"index": self.index, "kind": self.kind}
        if self.field is not None:
            d["field"] = self.field
        if self.cycle is not None:
            d["cycle"] = self.cycle
        if self.pc is not None:
            d["pc"] = self.pc
        if self.dut is not None or self.golden is not None:
            d["dut"] = self.dut
            d["golden"] = self.golden
        return d


@dataclass(frozen=True)
class LockstepReport:
    status: str  # "MATCH" | "MISMATCH"
    matched: int
    total: int
    schema: str
    mismatches: tuple[Mismatch, ...]

    @property
    def ok(self) -> bool:
        return self.status == "MATCH"

    @property
    def first_mismatch(self) -> Mismatch | None:
        return self.mismatches[0] if self.mismatches else None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status,
            "matched": self.matched,
            "total": self.total,
            "schema": self.schema,
        }
        if self.first_mismatch is not None:
            d["first_mismatch"] = self.first_mismatch.to_dict()
        return d

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


class LockstepComparator:
    """Compares a DUT commit stream against a golden one in retire order.

    All matching rules come from the :class:`CommitProfile` data, so the
    comparator carries no ISA-specific knowledge.
    """

    def __init__(self, profile: CommitProfile, *, stop_on_first: bool = True) -> None:
        self._profile = profile
        self._stop_on_first = stop_on_first
        self._fields = profile.comparable_fields()
        self._member_gate = profile._member_to_group_valid()

    def compare(
        self,
        dut: CommitTrace | Sequence[CommitRow],
        golden: Sequence[Mapping[str, int]],
    ) -> LockstepReport:
        dut_rows = dut.rows if isinstance(dut, CommitTrace) else tuple(dut)
        schema = dut.schema if isinstance(dut, CommitTrace) else self._profile.schema

        mismatches: list[Mismatch] = []
        matched = 0
        total = max(len(dut_rows), len(golden))

        for i in range(total):
            d = dut_rows[i] if i < len(dut_rows) else None
            g = golden[i] if i < len(golden) else None

            if d is None:
                mismatches.append(Mismatch(i, "missing_dut", None, None, None,
                                           pc=_maybe_int(g, "pc")))
                if self._stop_on_first:
                    break
                continue
            if g is None:
                mismatches.append(Mismatch(i, "missing_golden", None, None, None,
                                           cycle=d.cycle, pc=d.get("pc")))
                if self._stop_on_first:
                    break
                continue

            row_mismatch = self._compare_row(i, d, g)
            if row_mismatch is None:
                matched += 1
            else:
                mismatches.append(row_mismatch)
                if self._stop_on_first:
                    break

        status = "MATCH" if not mismatches else "MISMATCH"
        return LockstepReport(
            status=status,
            matched=matched,
            total=total,
            schema=schema,
            mismatches=tuple(mismatches),
        )

    def _compare_row(
        self, index: int, dut: CommitRow, golden: Mapping[str, int]
    ) -> Mismatch | None:
        for f in self._fields:
            gate = self._member_gate.get(f)
            if gate is not None and int(golden.get(gate, 0)) == 0:
                # group strobe deasserted on the golden side -> member is don't-care
                continue
            dv = dut.get(f)
            gv = int(golden.get(f, 0))
            if dv != gv:
                return Mismatch(
                    index=index,
                    kind="field",
                    field=f,
                    dut=dv,
                    golden=gv,
                    cycle=dut.cycle,
                    pc=dut.get("pc"),
                )
        return None


def _maybe_int(m: Mapping[str, int] | None, key: str) -> int | None:
    if m is None:
        return None
    v = m.get(key)
    return None if v is None else int(v)


def run_lockstep(
    dut_jsonl: str | Path,
    golden: GoldenModel,
    program: Iterable[Instr],
    profile: CommitProfile | Mapping[str, Any],
    *,
    stop_on_first: bool = True,
) -> LockstepReport:
    """One-shot convenience: load the DUT stream, run the golden model over the
    shared program, and compare. Returns the structured report."""
    prof = profile if isinstance(profile, CommitProfile) else CommitProfile.from_dict(profile)
    trace = load_commit_jsonl(dut_jsonl)
    golden.reset()
    golden_rows = golden.run(program)
    return LockstepComparator(prof, stop_on_first=stop_on_first).compare(trace, golden_rows)
