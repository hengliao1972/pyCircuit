"""Backend-neutral observation trace model for dual-backend cross-check (TODO B3).

The agentic optimizer §3.4 level-4 asks for the same ``.pyc`` to be emitted to
both the C++ and the Verilog backends, then compared cycle-by-cycle across all
ports and observation points, so a "compiler bug" can be told apart from a
"design bug". This module defines the *interchange* both backends target: a
canonical, per-cycle observation trace keyed by ``(cycle, phase, signal)``.

Design (mirroring B1/B2):

* Backend-neutral -- the model and its JSONL form fix no toolchain specifics.
  Any testbench (the C++ ``PycTraceBinWriter``, the SV TB, a golden model, ...)
  can emit it with a handful of lines; the differ then consumes two such traces.
* Phase-aware -- observations are tagged with the two-phase discipline
  (Decision 0113/0140): ``comb`` (settled combinational), ``tick`` (post
  state-update, TICK-OBS) and ``commit`` (transfer boundary, XFER-OBS). ``xfer``
  is accepted as an alias of ``commit``.
* X-aware -- a sample may be invalid/unknown (``X``), e.g. under reset or before
  a value is produced; ``X`` compares equal only to ``X``.

JSONL form::

    {"type":"start","backend":"cpp","phases":["tick","commit"]}
    {"cycle":0,"phase":"tick","signal":"top.pc","value":4}
    {"cycle":0,"phase":"commit","signal":"top.valid","x":true}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

# Canonical phase vocabulary and a deterministic ordering for "first" detection.
PHASE_ORDER: dict[str, int] = {"comb": 0, "tick": 1, "commit": 2}
_PHASE_ALIASES: dict[str, str] = {"xfer": "commit", "transfer": "commit"}


def normalize_phase(phase: str) -> str:
    p = str(phase).strip().lower()
    p = _PHASE_ALIASES.get(p, p)
    if p not in PHASE_ORDER:
        raise ValueError(f"unknown observation phase: {phase!r} (expected one of "
                         f"{sorted(PHASE_ORDER)} or alias 'xfer')")
    return p


# Sentinel for an invalid/unknown (X) observation. Distinct from any int value.
class _XType:
    _inst: "_XType | None" = None

    def __new__(cls) -> "_XType":
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "X"


X = _XType()
Value = "int | _XType"


@dataclass(frozen=True)
class ObsKey:
    cycle: int
    phase: str  # normalized
    signal: str

    def order(self) -> tuple[int, int, str]:
        return (self.cycle, PHASE_ORDER[self.phase], self.signal)


@dataclass(frozen=True)
class ObsSample:
    cycle: int
    phase: str
    signal: str
    value: "int | _XType"  # X for invalid/unknown

    @property
    def is_x(self) -> bool:
        return isinstance(self.value, _XType)

    def key(self) -> ObsKey:
        return ObsKey(self.cycle, self.phase, self.signal)


class ObsTrace:
    """An ordered set of observations from one backend/testbench run."""

    def __init__(self, backend: str = "", samples: Iterable[ObsSample] = ()) -> None:
        self.backend = backend
        self._by_key: dict[ObsKey, ObsSample] = {}
        for s in samples:
            self.add(s)

    def add(self, sample: ObsSample) -> None:
        self._by_key[sample.key()] = sample

    def record(self, cycle: int, phase: str, signal: str,
               value: "int | _XType | None") -> None:
        val: "int | _XType" = X if value is None else value
        self.add(ObsSample(int(cycle), normalize_phase(phase), str(signal), val))

    def get(self, cycle: int, phase: str, signal: str) -> ObsSample | None:
        return self._by_key.get(ObsKey(int(cycle), normalize_phase(phase), str(signal)))

    def keys(self) -> list[ObsKey]:
        return list(self._by_key.keys())

    def samples(self) -> list[ObsSample]:
        return [self._by_key[k] for k in sorted(self._by_key, key=ObsKey.order)]

    def signals(self) -> list[str]:
        return sorted({k.signal for k in self._by_key})

    def cycles(self) -> list[int]:
        return sorted({k.cycle for k in self._by_key})

    def __len__(self) -> int:
        return len(self._by_key)


# --------------------------------------------------------------------------- #
# JSONL interchange.
# --------------------------------------------------------------------------- #
def parse_obs_jsonl(text: str, *, backend: str | None = None) -> ObsTrace:
    trace = ObsTrace(backend=backend or "")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("type") == "start":
            if backend is None:
                trace.backend = str(obj.get("backend", ""))
            continue
        value: "int | _XType"
        if obj.get("x") is True or obj.get("value", None) is None and "value" in obj:
            value = X
        else:
            value = int(obj["value"])
        trace.record(int(obj["cycle"]), str(obj["phase"]), str(obj["signal"]), value)
    return trace


def load_obs_jsonl(path: str | Path, *, backend: str | None = None) -> ObsTrace:
    return parse_obs_jsonl(Path(path).read_text(encoding="utf-8"), backend=backend)


def dump_obs_jsonl(trace: ObsTrace, *, phases: Iterable[str] | None = None) -> str:
    hdr: dict[str, Any] = {"type": "start", "backend": trace.backend}
    if phases is not None:
        hdr["phases"] = [normalize_phase(p) for p in phases]
    lines = [json.dumps(hdr)]
    for s in trace.samples():
        row: dict[str, Any] = {"cycle": s.cycle, "phase": s.phase, "signal": s.signal}
        if s.is_x:
            row["x"] = True
        else:
            row["value"] = int(s.value)  # type: ignore[arg-type]
        lines.append(json.dumps(row))
    return "\n".join(lines) + "\n"


def write_obs_jsonl(trace: ObsTrace, path: str | Path,
                    *, phases: Iterable[str] | None = None) -> None:
    Path(path).write_text(dump_obs_jsonl(trace, phases=phases), encoding="utf-8")


def trace_from_records(backend: str,
                       records: Iterable[Mapping[str, Any]]) -> ObsTrace:
    """Build a trace from dict records ``{cycle, phase, signal, value|x}``."""
    trace = ObsTrace(backend=backend)
    for r in records:
        val = X if r.get("x") is True or r.get("value") is None else int(r["value"])
        trace.record(int(r["cycle"]), str(r["phase"]), str(r["signal"]), val)
    return trace
