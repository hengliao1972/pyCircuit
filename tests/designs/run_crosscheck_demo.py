#!/usr/bin/env python3
"""Observable B3 demo: structured dual-backend cross-check diff report.

Simulates the §3.4 level-4 scenario -- the same design emitted to two backends
(C++ vs Verilog) -- by fabricating two per-cycle observation traces that agree
everywhere except one injected divergence, then prints the agent-consumable
diff report locating the first mismatch (cycle / phase / signal / both values).

In a real flow each backend testbench would emit its own observation JSONL
(the C++ .pyctrace or an SV writer targeting the same obstrace form); this demo
stands in for those producers so the referee is observable without a toolchain.

Usage:
  python3 tests/designs/run_crosscheck_demo.py [A_OBS_JSONL B_OBS_JSONL]
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "compiler" / "frontend"))

from pycircuit.crosscheck import (  # noqa: E402
    BackendDiffer,
    load_obs_jsonl,
    trace_from_records,
)


def _cpp_records():
    return [
        {"cycle": 0, "phase": "tick", "signal": "top.pc", "value": 0},
        {"cycle": 0, "phase": "commit", "signal": "top.retire", "value": 0},
        {"cycle": 1, "phase": "tick", "signal": "top.pc", "value": 4},
        {"cycle": 1, "phase": "commit", "signal": "top.retire", "value": 1},
        {"cycle": 2, "phase": "tick", "signal": "top.pc", "value": 8},
        {"cycle": 2, "phase": "tick", "signal": "top.alu_result", "value": 42},
        {"cycle": 2, "phase": "commit", "signal": "top.retire", "value": 1},
    ]


def _verilog_records():
    recs = _cpp_records()
    # inject a design/compiler drift: ALU result differs at cycle 2 (TICK-OBS)
    for r in recs:
        if r["cycle"] == 2 and r["signal"] == "top.alu_result":
            r["value"] = 41
    return recs


def main() -> int:
    if len(sys.argv) >= 3:
        a = load_obs_jsonl(sys.argv[1], backend="cpp")
        b = load_obs_jsonl(sys.argv[2], backend="verilog")
    else:
        a = trace_from_records("cpp", _cpp_records())
        b = trace_from_records("verilog", _verilog_records())

    report = BackendDiffer().diff(a, b)
    print(f"[xcheck] backends    : {report.backend_a} vs {report.backend_b}")
    print(f"[xcheck] signals     : {list(report.signals)}")
    print(f"[xcheck] compared    : {report.compared} keys "
          f"(union {report.total_keys})")
    print(f"[xcheck] report      : {report.to_json(indent=2)}")
    if report.first is not None:
        f = report.first
        where = f"cycle {f.cycle} / {f.phase.upper()}-OBS / {f.signal}"
        print(f"[xcheck] FIRST DRIFT : {where}: {report.backend_a}={f.a} "
              f"vs {report.backend_b}={f.b} ({f.kind})")
    print(f"[xcheck] result      : {report.status}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
