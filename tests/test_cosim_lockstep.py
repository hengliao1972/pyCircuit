"""Regression gate for the B2 lockstep co-simulation harness.

Exercises the whole referee loop with no external tooling (no ASL, no pycc):
a placeholder golden model produces the reference commit stream from a shared
program, and the comparator aligns it against a DUT commit stream in retire
order using only the schema-agnostic B1 profile (required + validity groups).

Covered:
  * MATCH   : honest DUT stream equals the golden stream commit-for-commit.
  * GATED   : divergence on group members is a don't-care when the group's
              validity strobe is deasserted (Decision 0146) -> still MATCH.
  * MISMATCH: a corrupted writeback is pinpointed to the exact commit index,
              field, cycle, pc and both values (the minimal counterexample).
  * SHAPE   : missing / extra commits are reported.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from pycircuit.cosim import (
    CommitProfile,
    Instr,
    LockstepComparator,
    ReferenceModel,
    parse_commit_jsonl,
    run_lockstep,
)

_REPO = Path(__file__).resolve().parents[1]
_DESIGN = _REPO / "tests" / "designs" / "commit_demo.py"


def _demo_profile() -> dict:
    """Load the demo's own neutral commit profile (avoids drift from the design)."""
    spec = importlib.util.spec_from_file_location("commit_demo_cosim", _DESIGN)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.DEMO_COMMIT_PROFILE)


def _demo_program() -> list[Instr]:
    """The shared instruction stream, mirroring the auto-driver stimulus of the
    B1 commit_demo (retire, pc, insn, wb_en, wb_rd, wb_data)."""
    return [
        Instr(pc=0x100, insn=0x13, retire=True,
              operands={"wb": 1, "rd": 3, "imm": 0xDEAD}),
        Instr(pc=0, insn=0, retire=False),  # bubble -> no commit
        Instr(pc=0x104, insn=0x63, retire=True,
              operands={"wb": 0, "rd": 0, "imm": 0}),
    ]


def _golden_to_jsonl(schema: str, rows: list[dict], *, stage: str = "commit") -> str:
    """Serialize golden commit records into the B1 commit-bundle JSONL form so we
    can feed them back through the DUT parser (simulates a passing run)."""
    lines = [json.dumps({"type": "start", "commit_schema_id": schema})]
    for cyc, r in zip((0, 2), rows):  # cycles 0 and 2 retired; cycle 1 was a bubble
        obj = {"cycle": cyc, "stage": stage}
        obj.update({k: int(v) for k, v in r.items()})
        lines.append(json.dumps(obj))
    return "\n".join(lines) + "\n"


def test_golden_reproduces_b1_demo_stream() -> None:
    """The placeholder golden model, fed the shared program, reproduces exactly
    the two committed rows seen in the B1 commit.auto.jsonl stream."""
    golden = ReferenceModel()
    rows = golden.run(_demo_program())
    assert rows == [
        {"pc": 0x100, "insn": 0x13, "wb_valid": 1, "wb_rd": 3, "wb_data": 0xDEAD},
        {"pc": 0x104, "insn": 0x63, "wb_valid": 0, "wb_rd": 0, "wb_data": 0},
    ]


def test_lockstep_match() -> None:
    profile = _demo_profile()
    golden = ReferenceModel()
    grows = golden.run(_demo_program())
    dut = parse_commit_jsonl(_golden_to_jsonl(profile["schema"], grows))

    report = LockstepComparator(CommitProfile.from_dict(profile)).compare(dut, grows)
    assert report.ok, report.to_dict()
    assert report.status == "MATCH"
    assert report.matched == 2 and report.total == 2
    assert report.first_mismatch is None


def test_gated_member_is_dont_care_when_strobe_low() -> None:
    """On the second commit wb_valid==0, so a differing wb_rd/wb_data must be
    ignored -> the streams still match."""
    profile = _demo_profile()
    golden = ReferenceModel()
    grows = golden.run(_demo_program())

    # Corrupt the *gated* members on the wb_valid==0 commit.
    dut_json = _golden_to_jsonl(profile["schema"], grows)
    dut = parse_commit_jsonl(dut_json)
    tampered = list(dut.rows)
    r1 = tampered[1]
    tampered[1] = type(r1)(fields={**r1.fields, "wb_rd": 31, "wb_data": 0xBEEF},
                           cycle=r1.cycle, stage=r1.stage)

    report = LockstepComparator(CommitProfile.from_dict(profile)).compare(
        type(dut)(schema=dut.schema, rows=tuple(tampered)), grows
    )
    assert report.ok, report.to_dict()


def test_lockstep_mismatch_pinpoints_first_diff() -> None:
    """A corrupted writeback on an active (wb_valid==1) commit is flagged at the
    exact index/field/values."""
    profile = _demo_profile()
    golden = ReferenceModel()
    grows = golden.run(_demo_program())

    dut = parse_commit_jsonl(_golden_to_jsonl(profile["schema"], grows))
    tampered = list(dut.rows)
    r0 = tampered[0]
    tampered[0] = type(r0)(fields={**r0.fields, "wb_data": 0xC0DE},
                           cycle=r0.cycle, stage=r0.stage)

    report = LockstepComparator(CommitProfile.from_dict(profile)).compare(
        type(dut)(schema=dut.schema, rows=tuple(tampered)), grows
    )
    assert not report.ok
    mm = report.first_mismatch
    assert mm is not None
    assert mm.kind == "field" and mm.field == "wb_data"
    assert mm.index == 0
    assert mm.dut == 0xC0DE and mm.golden == 0xDEAD
    assert mm.cycle == 0 and mm.pc == 0x100


def test_lockstep_reports_missing_commit() -> None:
    """A DUT that drops a retiring instruction (golden has more commits) is
    reported as a shape mismatch."""
    profile = _demo_profile()
    golden = ReferenceModel()
    grows = golden.run(_demo_program())

    dut = parse_commit_jsonl(_golden_to_jsonl(profile["schema"], grows))
    short = type(dut)(schema=dut.schema, rows=(dut.rows[0],))  # drop the 2nd commit

    report = LockstepComparator(CommitProfile.from_dict(profile)).compare(short, grows)
    assert not report.ok
    mm = report.first_mismatch
    assert mm is not None and mm.kind == "missing_dut" and mm.index == 1


def test_run_lockstep_convenience(tmp_path: Path) -> None:
    """End-to-end helper: load a JSONL file + run the golden over the program."""
    profile = _demo_profile()
    golden = ReferenceModel()
    grows = golden.run(_demo_program())
    jsonl = tmp_path / "commit.jsonl"
    jsonl.write_text(_golden_to_jsonl(profile["schema"], grows), encoding="utf-8")

    report = run_lockstep(jsonl, ReferenceModel(), _demo_program(), profile)
    assert report.ok, report.to_dict()
    assert json.loads(report.to_json())["status"] == "MATCH"
