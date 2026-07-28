"""Regression gate for the B3 dual-backend cross-check differ.

Self-contained (no pycc / Verilator): builds two backend observation traces
in-memory and asserts the structured diff report behaves as a referee that can
tell a "compiler bug" from a "design bug".

Covered:
  * MATCH   : identical per-cycle observations across backends.
  * VALUE   : a differing value is pinpointed to the exact cycle/phase/signal
              and both values, and it is the *first* in canonical order
              (cycle, then comb<tick<commit, then signal).
  * X       : X==X matches; X-vs-value is flagged as an ``x`` divergence.
  * SHAPE   : a key present in only one backend -> missing_a / missing_b.
  * SUMMARY : per-signal divergence counts + JSONL round-trip.
"""

from __future__ import annotations

import json

from pycircuit.crosscheck import (
    BackendDiffer,
    ObsTrace,
    X,
    cross_check,
    dump_obs_jsonl,
    parse_obs_jsonl,
    trace_from_records,
)


def _base_records():
    # two cycles, tick + commit phases, two signals
    return [
        {"cycle": 0, "phase": "tick", "signal": "top.pc", "value": 0},
        {"cycle": 0, "phase": "commit", "signal": "top.valid", "value": 1},
        {"cycle": 1, "phase": "tick", "signal": "top.pc", "value": 4},
        {"cycle": 1, "phase": "commit", "signal": "top.valid", "value": 1},
    ]


def test_match_identical_traces() -> None:
    a = trace_from_records("cpp", _base_records())
    b = trace_from_records("verilog", _base_records())
    report = BackendDiffer().diff(a, b)
    assert report.ok and report.status == "MATCH"
    assert report.compared == 4 and report.total_keys == 4
    assert report.first is None
    assert report.signals == ("top.pc", "top.valid")


def test_value_divergence_is_first_in_canonical_order() -> None:
    a = trace_from_records("cpp", _base_records())
    b_rec = _base_records()
    # corrupt cycle 1 tick pc (later than an untouched cycle 0) AND cycle 0
    # commit valid; the FIRST reported must be cycle 0 / commit / top.valid.
    b_rec[1]["value"] = 0  # cycle 0, commit, top.valid : 1 -> 0
    b_rec[2]["value"] = 7  # cycle 1, tick,  top.pc    : 4 -> 7
    b = trace_from_records("verilog", b_rec)

    report = BackendDiffer().diff(a, b)
    assert not report.ok
    first = report.first
    assert first is not None
    assert (first.cycle, first.phase, first.signal) == (0, "commit", "top.valid")
    assert first.kind == "value" and first.a == 1 and first.b == 0
    # both divergences collected, per-signal tally correct
    assert len(report.divergences) == 2
    assert report.per_signal == {"top.valid": 1, "top.pc": 1}


def test_x_handling() -> None:
    a = trace_from_records("cpp", [
        {"cycle": 0, "phase": "tick", "signal": "s", "x": True},
        {"cycle": 1, "phase": "tick", "signal": "s", "value": 3},
    ])
    # X==X on cycle 0 (match); X-vs-value on cycle 1 (mismatch)
    b = trace_from_records("verilog", [
        {"cycle": 0, "phase": "tick", "signal": "s", "x": True},
        {"cycle": 1, "phase": "tick", "signal": "s", "x": True},
    ])
    report = BackendDiffer().diff(a, b)
    assert not report.ok
    assert report.first is not None
    assert report.first.kind == "x"
    assert (report.first.cycle, report.first.signal) == (1, "s")
    assert report.first.a == 3 and report.first.b == "X"


def test_shape_missing_signal() -> None:
    a = trace_from_records("cpp", _base_records())
    b_rec = [r for r in _base_records() if not (r["cycle"] == 1 and r["signal"] == "top.pc")]
    b = trace_from_records("verilog", b_rec)
    report = BackendDiffer().diff(a, b)
    assert not report.ok
    assert report.first is not None
    assert report.first.kind == "missing_b"
    assert (report.first.cycle, report.first.phase, report.first.signal) == (1, "tick", "top.pc")
    assert report.first.a == 4 and report.first.b is None


def test_signal_and_phase_filter() -> None:
    a = trace_from_records("cpp", _base_records())
    b_rec = _base_records()
    b_rec[2]["value"] = 7  # cycle 1 tick top.pc
    b = trace_from_records("verilog", b_rec)

    # only compare top.valid -> the top.pc drift is out of scope -> MATCH
    r1 = BackendDiffer(signals=["top.valid"]).diff(a, b)
    assert r1.ok, r1.to_dict()
    # only compare commit phase -> tick drift out of scope -> MATCH
    r2 = BackendDiffer(phases=["commit"]).diff(a, b)
    assert r2.ok, r2.to_dict()


def test_jsonl_roundtrip_and_cross_check(tmp_path) -> None:
    a = trace_from_records("cpp", _base_records())
    b_rec = _base_records()
    b_rec[0]["value"] = 99  # cycle 0 tick top.pc
    b = trace_from_records("verilog", b_rec)

    pa = tmp_path / "a.obs.jsonl"
    pb = tmp_path / "b.obs.jsonl"
    pa.write_text(dump_obs_jsonl(a, phases=["tick", "commit"]), encoding="utf-8")
    pb.write_text(dump_obs_jsonl(b), encoding="utf-8")

    # round-trip preserves observations
    a2 = parse_obs_jsonl(pa.read_text(encoding="utf-8"))
    assert a2.get(1, "tick", "top.pc").value == 4

    report = cross_check(pa, pb)
    assert not report.ok
    assert report.backend_a == "cpp" and report.backend_b == "verilog"
    assert report.first is not None
    assert (report.first.cycle, report.first.phase, report.first.signal) == (0, "tick", "top.pc")
    assert report.first.a == 0 and report.first.b == 99
    # JSON report is agent-consumable
    payload = json.loads(report.to_json())
    assert payload["status"] == "MISMATCH"
    assert payload["first_divergence"]["signal"] == "top.pc"


def test_xfer_alias_normalizes_to_commit() -> None:
    a = ObsTrace("cpp")
    a.record(0, "xfer", "s", 1)
    b = ObsTrace("verilog")
    b.record(0, "commit", "s", 1)
    assert BackendDiffer().diff(a, b).ok
