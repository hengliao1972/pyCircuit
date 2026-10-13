from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareDomain, mux, wire_of


def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    enable = m.input("enable", width=1)
    state = domain.signal(width=8, reset_value=0, name="state")
    current = wire_of(state)
    m.output("current", current)
    domain.next()
    state <<= mux(enable, current + 1, current)


build.__pycircuit_name__ = "comb_dirty_state"
