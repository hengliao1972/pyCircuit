from __future__ import annotations

from dataclasses import dataclass

from pycircuit import CycleAwareSignal


@dataclass(frozen=True)
class CoreState:
    stage: CycleAwareSignal
    pc: CycleAwareSignal
    br_kind: CycleAwareSignal
    br_base_pc: CycleAwareSignal
    br_off: CycleAwareSignal
    commit_cond: CycleAwareSignal
    commit_tgt: CycleAwareSignal
    cycles: CycleAwareSignal
    halted: CycleAwareSignal


@dataclass(frozen=True)
class RegFiles:
    gpr: list[CycleAwareSignal]
    t: list[CycleAwareSignal]
    u: list[CycleAwareSignal]
