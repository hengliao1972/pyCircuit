"""Self-contained commit/retire trace interface demo (TODO B1, Decision 0142).

A minimal, framework-only closed loop: this design defines its *own* neutral
commit-bundle profile (schema id + required fields + validity group) inline --
no dependency on any CPU/ISA ecosystem (e.g. LinxCore). It exercises the generic
``m.commit_interface(...)`` mechanism and the data-driven MLIR gate end to end.

The paired C++ driver ``commit_trace_main.cpp`` binds the same fields to a
``PycCommitTraceWriter`` and emits a commit-bundle JSONL, which the regression
test reads back and validates itself -- so the whole loop is PyCircuit-internal.
"""

from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    cas,
)

# Neutral, self-owned profile. Field names use plain RVFI-style vocabulary but
# the schema id and contract are the demo's own -- nothing is imported.
DEMO_COMMIT_PROFILE: dict = {
    "schema": "pyc-commit-demo-v1",
    "required": ["valid", "pc", "insn", "wb_valid"],
    "groups": {"wb": {"valid": "wb_valid", "members": ["wb_rd", "wb_data"]}},
}


def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    pc = cas(domain, m.input("pc", width=32))
    insn = cas(domain, m.input("insn", width=32))
    retire = cas(domain, m.input("retire", width=1))
    wb_en = cas(domain, m.input("wb_en", width=1))
    wb_rd = cas(domain, m.input("wb_rd", width=5))
    wb_data = cas(domain, m.input("wb_data", width=32))

    m.commit_interface(
        {
            "valid": retire,
            "pc": pc,
            "insn": insn,
            "wb_valid": wb_en,
            "wb_rd": wb_rd,
            "wb_data": wb_data,
        },
        stage="commit",
        **DEMO_COMMIT_PROFILE,
    )


build.__pycircuit_name__ = "commit_demo"
