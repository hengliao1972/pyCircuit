"""Named-pipeline demo used by the A3 stage-attribution regression gates.

Three explicitly named pipeline stages (``fetch`` / ``decode`` / ``execute``)
exercised end-to-end:

- frontend records the cycle->name table (module attr ``pyc.stage_names``) and
  tags each emitted ``pyc.reg`` with ``pyc.stage``;
- ``pycc`` lowers those tags into Verilog ``(* pyc_stage = "..." *)`` synthesis
  attributes and C++ ``// pyc_stage: ...`` DFX comments.

Note: the ``fetch``-stage ``pc`` register is intentionally a feedback register
that does not feed the output, so DCE removes it (together with its
``pyc.stage``) in the backend -- this documents that stage tags live on real
netlist registers only.
"""

from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    cas,
    wire_of,
)


def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    domain.name_stage("fetch")
    a = cas(domain, m.input("a", width=8))
    pc = domain.signal(width=8, name="pc")  # feedback reg declared in fetch
    d = a + 1

    domain.next(stage="decode")
    r = d + 2  # auto-balanced into decode

    domain.next(stage="execute")
    e = r + 3  # auto-balanced into execute
    pc <<= e[0:8]
    m.output("y", wire_of(e))


build.__pycircuit_name__ = "stage_demo"
