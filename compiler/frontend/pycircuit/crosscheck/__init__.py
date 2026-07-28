"""Dual-backend cross-check (TODO B3).

Compares the per-cycle observation traces of two backends (e.g. the C++ model
vs the Verilog/Verilator model built from the same ``.pyc``) and produces an
agent-consumable structured diff report with first-divergence localization
(cycle / phase / signal / both values). Backend-neutral and X-aware; any
testbench that emits the :mod:`.obstrace` JSONL form can be cross-checked.
"""

from .diff import BackendDiffer, DiffReport, Divergence, cross_check
from .obstrace import (
    ObsKey,
    ObsSample,
    ObsTrace,
    X,
    dump_obs_jsonl,
    load_obs_jsonl,
    normalize_phase,
    parse_obs_jsonl,
    trace_from_records,
    write_obs_jsonl,
)

__all__ = [
    "BackendDiffer",
    "DiffReport",
    "Divergence",
    "cross_check",
    "ObsKey",
    "ObsSample",
    "ObsTrace",
    "X",
    "dump_obs_jsonl",
    "load_obs_jsonl",
    "normalize_phase",
    "parse_obs_jsonl",
    "trace_from_records",
    "write_obs_jsonl",
]
