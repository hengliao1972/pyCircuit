"""LinxCore commit/retire bundle profile (CPU-specific, Decision 0142/0146).

PyCircuit's ``m.commit_interface(...)`` is schema-agnostic: the concrete field
vocabulary, the mandatory-field set and the validity groups are *data* supplied
by the design, not baked into the framework. This module holds the LinxCore
profile so a Linx CPU design can declare its commit interface with::

    from commit_schema import LC_COMMIT_BUNDLE_V2
    m.commit_interface(fields, **LC_COMMIT_BUNDLE_V2)

The field set and gating mirror what the cosim differ
(``linx_trace_diff.py``) requires, so emitted streams are directly diff-able.
"""

from __future__ import annotations

# ``valid`` is the per-cycle retire strobe used by the runtime collector to
# decide when to emit a row; the rest are the LC-COMMIT-BUNDLE-V2 architectural
# base plus writeback/memory/trap group strobes.
LC_COMMIT_BUNDLE_V2: dict = {
    "schema": "LC-COMMIT-BUNDLE-V2",
    "required": [
        "valid",
        "pc",
        "insn",
        "len",
        "next_pc",
        "wb_valid",
        "mem_valid",
        "trap_valid",
    ],
    "groups": {
        "wb": {"valid": "wb_valid", "members": ["wb_rd", "wb_data"]},
        "mem": {
            "valid": "mem_valid",
            "members": ["mem_is_store", "mem_addr", "mem_wdata", "mem_rdata", "mem_size"],
        },
        "trap": {"valid": "trap_valid", "members": ["trap_cause", "traparg0"]},
    },
}
