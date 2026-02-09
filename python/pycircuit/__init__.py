from __future__ import annotations

__all__ = [
    # Legacy API
    "Bundle",
    "Circuit",
    "ClockDomain",
    "JitError",
    "Module",
    "Pop",
    "Queue",
    "Reg",
    "Signal",
    "Vec",
    "Wire",
    "cat",
    "jit_inline",
    "jit_compile",
    # Cycle-Aware API
    "CycleAwareCircuit",
    "CycleAwareDomain",
    "CycleAwareModule",
    "CycleAwareSignal",
    "CycleAwareQueue",
    "CycleAwareByteMem",
    "CycleAwareBundle",
    "CycleAwarePop",
    "SignalFactory",
    "ca_cat",
    "ca_bundle",
    "mux",
    "compile_cycle_aware",
]

from .dsl import Module, Signal
from .hw import (
    Bundle,
    Circuit,
    ClockDomain,
    CycleAwareBundle,
    CycleAwareByteMem,
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareModule,
    CycleAwarePop,
    CycleAwareQueue,
    CycleAwareSignal,
    Pop,
    Queue,
    Reg,
    SignalFactory,
    Vec,
    Wire,
    ca_bundle,
    ca_cat,
    cat,
    mux,
)
from .jit import (
    JitError,
    compile as jit_compile,
    compile_cycle_aware,
    jit_inline,
)
