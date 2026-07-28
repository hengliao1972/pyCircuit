"""Structured dual-backend cross-check differ (TODO B3).

Aligns two backend observation traces by ``(cycle, phase, signal)`` and reports
the first divergence in canonical order (earliest cycle, then phase
comb->tick->commit, then signal name) plus a per-signal summary. The report is
JSON-serializable so it can be handed straight to the agent: "at cycle 7, TICK
observation top.alu.result differs: cpp=5 vs verilog=4" is a far better
counterexample than a 2 GB waveform.

Backend-neutral and X-aware, consistent with :mod:`.obstrace`:
* A value differs from a different value -> ``value`` divergence.
* ``X`` compares equal only to ``X``; ``X`` vs a concrete value -> ``x``
  divergence (typically reset/settle discipline drift between backends).
* A key present in only one trace -> ``missing_a`` / ``missing_b`` (shape drift,
  e.g. one backend emitted an extra cycle or dropped a port).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .obstrace import ObsKey, ObsTrace, X, load_obs_jsonl


def _fmt(v: "int | Any") -> "int | str":
    return "X" if v is X else int(v)


@dataclass(frozen=True)
class Divergence:
    cycle: int
    phase: str
    signal: str
    kind: str  # "value" | "x" | "missing_a" | "missing_b"
    a: "int | str | None"  # backend A value ("X" for invalid, None if absent)
    b: "int | str | None"

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "phase": self.phase,
            "signal": self.signal,
            "kind": self.kind,
            "a": self.a,
            "b": self.b,
        }


@dataclass(frozen=True)
class DiffReport:
    status: str  # "MATCH" | "MISMATCH"
    backend_a: str
    backend_b: str
    compared: int  # number of aligned (cycle,phase,signal) keys both had
    total_keys: int  # size of the union of keys
    signals: tuple[str, ...]
    divergences: tuple[Divergence, ...]
    per_signal: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "MATCH"

    @property
    def first(self) -> Divergence | None:
        return self.divergences[0] if self.divergences else None

    def to_dict(self, *, max_divergences: int = 10) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status,
            "backend_a": self.backend_a,
            "backend_b": self.backend_b,
            "compared": self.compared,
            "total_keys": self.total_keys,
            "signals": list(self.signals),
        }
        if self.first is not None:
            d["first_divergence"] = self.first.to_dict()
            d["divergence_count"] = len(self.divergences)
            d["per_signal"] = dict(self.per_signal)
            if max_divergences:
                d["divergences"] = [x.to_dict() for x in self.divergences[:max_divergences]]
        return d

    def to_json(self, *, indent: int | None = None, max_divergences: int = 10) -> str:
        return json.dumps(self.to_dict(max_divergences=max_divergences),
                          indent=indent, sort_keys=True)


class BackendDiffer:
    """Compares two :class:`ObsTrace` objects and localizes the first drift."""

    def __init__(
        self,
        *,
        signals: Iterable[str] | None = None,
        phases: Iterable[str] | None = None,
        max_divergences: int = 0,  # 0 = collect all
    ) -> None:
        self._signal_filter = set(signals) if signals is not None else None
        self._phase_filter = set(phases) if phases is not None else None
        self._max = max_divergences

    def diff(self, a: ObsTrace, b: ObsTrace) -> DiffReport:
        keys = set(a.keys()) | set(b.keys())
        if self._signal_filter is not None:
            keys = {k for k in keys if k.signal in self._signal_filter}
        if self._phase_filter is not None:
            keys = {k for k in keys if k.phase in self._phase_filter}

        ordered = sorted(keys, key=ObsKey.order)
        divergences: list[Divergence] = []
        per_signal: dict[str, int] = {}
        compared = 0

        for k in ordered:
            sa = a.get(k.cycle, k.phase, k.signal)
            sb = b.get(k.cycle, k.phase, k.signal)

            if sa is None or sb is None:
                kind = "missing_a" if sa is None else "missing_b"
                dv = Divergence(k.cycle, k.phase, k.signal, kind,
                                None if sa is None else _fmt(sa.value),
                                None if sb is None else _fmt(sb.value))
                self._push(divergences, per_signal, dv)
                if self._max and len(divergences) >= self._max:
                    break
                continue

            compared += 1
            ax, bx = sa.is_x, sb.is_x
            if ax and bx:
                continue  # X == X
            if ax != bx:
                dv = Divergence(k.cycle, k.phase, k.signal, "x",
                                _fmt(sa.value), _fmt(sb.value))
                self._push(divergences, per_signal, dv)
            elif int(sa.value) != int(sb.value):  # type: ignore[arg-type]
                dv = Divergence(k.cycle, k.phase, k.signal, "value",
                                int(sa.value), int(sb.value))  # type: ignore[arg-type]
                self._push(divergences, per_signal, dv)
            if self._max and len(divergences) >= self._max:
                break

        status = "MATCH" if not divergences else "MISMATCH"
        return DiffReport(
            status=status,
            backend_a=a.backend or "a",
            backend_b=b.backend or "b",
            compared=compared,
            total_keys=len(ordered),
            signals=tuple(sorted({k.signal for k in ordered})),
            divergences=tuple(divergences),
            per_signal=per_signal,
        )

    @staticmethod
    def _push(divs: list[Divergence], per_signal: dict[str, int], dv: Divergence) -> None:
        divs.append(dv)
        per_signal[dv.signal] = per_signal.get(dv.signal, 0) + 1


def cross_check(
    a: str | Path | ObsTrace,
    b: str | Path | ObsTrace,
    *,
    signals: Iterable[str] | None = None,
    phases: Iterable[str] | None = None,
    label_a: str | None = None,
    label_b: str | None = None,
) -> DiffReport:
    """Convenience: load two observation traces (paths or objects) and diff them."""
    ta = a if isinstance(a, ObsTrace) else load_obs_jsonl(a, backend=label_a)
    tb = b if isinstance(b, ObsTrace) else load_obs_jsonl(b, backend=label_b)
    if label_a:
        ta.backend = label_a
    if label_b:
        tb.backend = label_b
    return BackendDiffer(signals=signals, phases=phases).diff(ta, tb)
