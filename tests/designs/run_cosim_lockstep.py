#!/usr/bin/env python3
"""Observable B2 lockstep demo: compare the B1 DUT commit stream against the
placeholder golden model and print the structured report.

The golden model here stands in for a real ASL/ISA interpreter (drop-in via the
GoldenModel protocol). Fed the same instruction stream that drove the B1 demo,
it reproduces the reference commit records; the harness aligns them against the
DUT's commit.auto.jsonl in retire order.

Usage:
  # first produce the DUT stream (B1), then run the referee:
  python3 tests/designs/run_commit_e2e.py
  python3 tests/designs/run_cosim_lockstep.py [DUT_JSONL]

Default DUT_JSONL is <repo>/.pycircuit_out/b1_e2e/commit.auto.jsonl . If it is
absent, the golden stream is self-compared so the demo still runs standalone.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "compiler" / "frontend"))

from pycircuit.cosim import (  # noqa: E402
    CommitProfile,
    Instr,
    LockstepComparator,
    ReferenceModel,
    load_commit_jsonl,
    parse_commit_jsonl,
)

_DESIGN = _REPO / "tests" / "designs" / "commit_demo.py"
_DEFAULT_DUT = _REPO / ".pycircuit_out" / "b1_e2e" / "commit.auto.jsonl"


def _load_profile() -> dict:
    spec = importlib.util.spec_from_file_location("commit_demo_cosim_demo", _DESIGN)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.DEMO_COMMIT_PROFILE)


def _program() -> list[Instr]:
    return [
        Instr(pc=0x100, insn=0x13, retire=True, operands={"wb": 1, "rd": 3, "imm": 0xDEAD}),
        Instr(pc=0, insn=0, retire=False),
        Instr(pc=0x104, insn=0x63, retire=True, operands={"wb": 0, "rd": 0, "imm": 0}),
    ]


def main() -> int:
    profile = _load_profile()
    program = _program()

    golden = ReferenceModel()
    grows = golden.run(program)
    print(f"[cosim] golden model : {len(grows)} reference commits")
    for i, r in enumerate(grows):
        print(f"[cosim]   golden[{i}] = {json.dumps(r)}")

    dut_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_DUT
    if dut_path.exists():
        dut = load_commit_jsonl(dut_path)
        print(f"[cosim] DUT stream   : {dut_path}  ({len(dut)} commits, schema={dut.schema})")
    else:
        # standalone fallback: fabricate a passing DUT stream from the golden
        lines = [json.dumps({"type": "start", "commit_schema_id": profile["schema"]})]
        for cyc, r in zip((0, 2), grows):
            lines.append(json.dumps({"cycle": cyc, "stage": "commit", **r}))
        dut = parse_commit_jsonl("\n".join(lines) + "\n")
        print(f"[cosim] DUT stream   : (fabricated; run run_commit_e2e.py for the real one)")

    report = LockstepComparator(CommitProfile.from_dict(profile)).compare(dut, grows)
    print(f"[cosim] report       : {report.to_json(indent=2)}")
    print(f"[cosim] result       : {report.status} "
          f"({report.matched}/{report.total} commits matched)")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
