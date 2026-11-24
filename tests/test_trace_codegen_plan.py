from __future__ import annotations

from pycircuit.cli import _derive_trace_codegen_plan
from pycircuit.trace_dsl import TracePlan


def _trace_plan(*enabled_signals: str) -> TracePlan:
    return TracePlan(
        version=1,
        enabled_signals=tuple(enabled_signals),
        enabled_instances=("dut",),
    )


def test_trace_codegen_plan_selects_only_enabled_internal_fields() -> None:
    probe_manifest = {
        "probes": [
            {
                "canonical_path": "dut:debug_sum_alias",
                "field_path": "debug_sum_alias",
                "module": "alias_name_check",
                "dir": "internal",
            },
            {
                "canonical_path": "dut:a",
                "field_path": "a",
                "module": "alias_name_check",
                "dir": "input",
            },
            {
                "canonical_path": "dut:other_alias",
                "field_path": "other_alias",
                "module": "alias_name_check",
                "dir": "internal",
            },
            {
                "canonical_path": "dut:probe.bundle.value",
                "field_path": "probe.bundle.value",
                "module": "alias_name_check",
                "dir": "probe",
            },
        ]
    }

    plan = _derive_trace_codegen_plan(
        trace_plan=_trace_plan(
            "dut:debug_sum_alias",
            "dut:a",
            "dut:probe.bundle.value",
        ),
        probe_manifest=probe_manifest,
    )

    assert plan == {
        "version": 1,
        "modules": {"alias_name_check": ["debug_sum_alias"]},
    }


def test_trace_codegen_plan_is_deterministic_across_instances() -> None:
    probe_manifest = {
        "probes": [
            {
                "canonical_path": "dut.z:late",
                "field_path": "late",
                "module": "z_module",
                "dir": "internal",
            },
            {
                "canonical_path": "dut.left:beta",
                "field_path": "beta",
                "module": "shared_module",
                "dir": "internal",
            },
            {
                "canonical_path": "dut.right:alpha",
                "field_path": "alpha",
                "module": "shared_module",
                "dir": "internal",
            },
            {
                "canonical_path": "dut.right:beta",
                "field_path": "beta",
                "module": "shared_module",
                "dir": "internal",
            },
        ]
    }

    plan = _derive_trace_codegen_plan(
        trace_plan=_trace_plan(
            "dut.right:beta",
            "dut.z:late",
            "dut.left:beta",
            "dut.right:alpha",
        ),
        probe_manifest=probe_manifest,
    )

    assert plan == {
        "version": 1,
        "modules": {
            "shared_module": ["alpha", "beta"],
            "z_module": ["late"],
        },
    }


def test_trace_codegen_plan_is_empty_without_trace_selection() -> None:
    probe_manifest = {
        "probes": [
            {
                "canonical_path": "dut:debug_sum_alias",
                "field_path": "debug_sum_alias",
                "module": "alias_name_check",
                "dir": "internal",
            }
        ]
    }

    assert _derive_trace_codegen_plan(
        trace_plan=None,
        probe_manifest=probe_manifest,
    ) == {"version": 1, "modules": {}}
