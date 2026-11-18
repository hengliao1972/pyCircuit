from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tool(name: str, repo_candidate: Path | None = None) -> str | None:
    configured = os.environ.get(name.upper())
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which(name)
    if found:
        return found
    if repo_candidate is not None and repo_candidate.is_file():
        return str(repo_candidate)
    return None


def _run_build(
    *,
    source: Path,
    out_dir: Path,
    pycc: str,
    trace_config: Path | None,
    target: str,
    run_verilator: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "pycircuit.cli",
        "build",
        str(source),
        "--out-dir",
        str(out_dir),
        "--target",
        target,
        "--jobs",
        "2",
        "--logic-depth",
        "64",
        "--profile",
        "dev",
    ]
    if run_verilator:
        cmd.append("--run-verilator")
    if trace_config is not None:
        cmd.extend(["--trace-config", str(trace_config)])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "compiler" / "frontend")
    env["PYCC"] = pycc
    return subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def _run_cpp(out_dir: Path, trace_dir: Path) -> None:
    manifest = json.loads(
        (out_dir / "project_manifest.json").read_text(encoding="utf-8")
    )
    env = os.environ.copy()
    env["PYC_TRACE_DIR"] = str(trace_dir)
    subprocess.run(
        [manifest["cpp_executable"]],
        cwd=out_dir,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def _vcd_signal_changes(path: Path, signal_path: str) -> list[tuple[int, int]]:
    timescale_fs = 1_000_000
    scopes: list[str] = []
    signal_ids: set[str] = set()
    widths: dict[str, int] = {}
    in_definitions = True
    time_fs = 0
    changes: dict[int, int] = {}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if in_definitions:
            if line.startswith("$timescale"):
                token = line.split()[1]
                scale = int(token[:-2] if token[-2:] in {"fs", "ps", "ns"} else 1)
                unit = token[-2:]
                timescale_fs = scale * {"fs": 1, "ps": 1_000, "ns": 1_000_000}[unit]
            elif line.startswith("$scope "):
                scopes.append(line.split()[2])
            elif line.startswith("$upscope"):
                scopes.pop()
            elif line.startswith("$var "):
                parts = line.split()
                width = int(parts[2])
                code = parts[3]
                reference = parts[4]
                if ".".join([*scopes, reference]) == signal_path:
                    signal_ids.add(code)
                    widths[code] = width
            elif line.startswith("$enddefinitions"):
                in_definitions = False
            continue

        if line.startswith("#"):
            time_fs = int(line[1:]) * timescale_fs
            continue
        if line.startswith("b"):
            bits, code = line[1:].split()
        elif line[:1] in {"0", "1"}:
            bits, code = line[0], line[1:]
        else:
            continue
        if code not in signal_ids or any(bit not in "01" for bit in bits):
            continue
        width = widths[code]
        changes[time_fs] = int(bits.zfill(width), 2)

    if not signal_ids:
        raise AssertionError(f"missing VCD signal {signal_path!r} in {path}")
    return list(changes.items())


def _placement_summary(out_dir: Path) -> dict[str, int]:
    manifest_path = (
        out_dir
        / "device"
        / "cpp"
        / "alias_name_check"
        / "cpp_compile_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest["profile_summary"]["cpp_placement"]


def test_trace_config_named_alias_matches_cpp_and_verilator(
    tmp_path: Path,
) -> None:
    pycc = _tool(
        "pycc",
        ROOT / ".pycircuit_out" / "toolchain" / "install" / "bin" / "pycc",
    )
    verilator = _tool("verilator")
    if pycc is None or verilator is None:
        pytest.skip("requires built pycc and Verilator")

    source = tmp_path / "alias_name_check.py"
    source.write_text(
        textwrap.dedent(
            """
            from pycircuit import (
                CycleAwareCircuit,
                CycleAwareDomain,
                CycleAwareTb,
                Tb,
                cas,
                compile_cycle_aware,
                testbench,
                wire_of,
            )


            def build(
                m: CycleAwareCircuit,
                domain: CycleAwareDomain,
                width: int = 8,
            ) -> None:
                a = cas(domain, m.input("a", width=width), cycle=0)
                b = cas(domain, m.input("b", width=width), cycle=0)
                debug_sum = (a + b).named("debug_sum_alias")
                m.output("out", wire_of(debug_sum))


            build.__pycircuit_name__ = "alias_name_check"


            @testbench
            def tb(t: Tb) -> None:
                bench = CycleAwareTb(t)
                bench.clock("clk")
                bench.reset("rst", cycles_asserted=1, cycles_deasserted=1)
                bench.timeout(8)
                bench.drive("a", 3)
                bench.drive("b", 5)
                bench.expect("out", 8)
                bench.next()
                bench.drive("a", 10)
                bench.drive("b", 7)
                bench.expect("out", 17)
                bench.finish(at=2)


            if __name__ == "__main__":
                print(
                    compile_cycle_aware(
                        build,
                        name="alias_name_check",
                        eager=True,
                        width=8,
                    ).emit_mlir()
                )
            """
        ).lstrip(),
        encoding="utf-8",
    )
    selected_config = tmp_path / "selected_trace.json"
    selected_config.write_text(
        json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "instances": ["dut"],
                        "ports": ["clk", "rst", "a", "b", "out"],
                        "probes": {
                            "families": [],
                            "stages": [],
                            "lanes": [],
                            "at": [],
                        },
                    }
                ],
                "window": {"begin_cycle": 0, "end_cycle": 1},
            }
        ),
        encoding="utf-8",
    )

    selected_out = tmp_path / "selected"
    _run_build(
        source=source,
        out_dir=selected_out,
        pycc=pycc,
        trace_config=selected_config,
        target="both",
        run_verilator=True,
    )
    cpp_trace_dir = selected_out / "cpp-traces"
    _run_cpp(selected_out, cpp_trace_dir)

    codegen_plan = json.loads(
        (selected_out / "trace_codegen_plan.json").read_text(encoding="utf-8")
    )
    assert codegen_plan == {
        "version": 1,
        "modules": {"alias_name_check": ["debug_sum_alias"]},
    }
    invalid_plan = tmp_path / "invalid_trace_codegen_plan.json"
    invalid_plan.write_text(
        json.dumps(
            {
                "version": 1,
                "modules": {"alias_name_check": ["missing_alias"]},
            }
        ),
        encoding="utf-8",
    )
    invalid_codegen = subprocess.run(
        [
            pycc,
            str(
                selected_out
                / "device"
                / "modules"
                / "alias_name_check.pyc"
            ),
            "--emit=cpp",
            "--out-dir",
            str(tmp_path / "invalid-codegen"),
            "--trace-codegen-plan",
            str(invalid_plan),
            "--logic-depth=64",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert invalid_codegen.returncode != 0
    assert "field not found in module: missing_alias" in (
        invalid_codegen.stdout + invalid_codegen.stderr
    )
    cpp_header = (
        selected_out
        / "device"
        / "cpp"
        / "alias_name_check"
        / "alias_name_check.hpp"
    ).read_text(encoding="utf-8")
    assert "Wire<8> debug_sum_alias{};" in cpp_header
    assert 'trace_port(debug_sum_alias, "debug_sum_alias");' in cpp_header
    selected_placement = _placement_summary(selected_out)
    verilog_device = (
        selected_out
        / "device"
        / "verilog"
        / "alias_name_check"
        / "alias_name_check.v"
    ).read_text(encoding="utf-8")
    sv_tb = (selected_out / "tb" / "tb_alias_name_check.sv").read_text(
        encoding="utf-8"
    )
    cpp_tb = (selected_out / "tb" / "tb_alias_name_check.cpp").read_text(
        encoding="utf-8"
    )
    assert "$dumpon" in sv_tb and "$dumpoff" in sv_tb
    assert "setVcdWindow" in cpp_tb

    cpp_vcd = (
        cpp_trace_dir / "tb_alias_name_check" / "tb_alias_name_check.vcd"
    )
    verilator_vcd = selected_out / "tb_alias_name_check.vcd"
    cpp_changes = _vcd_signal_changes(
        cpp_vcd,
        "tb_alias_name_check.dut:debug_sum_alias",
    )
    verilator_changes = _vcd_signal_changes(
        verilator_vcd,
        "tb_alias_name_check.dut.debug_sum_alias",
    )
    expected_changes = [
        (0, 0),
        (4_000_000, 8),
        (6_000_000, 17),
    ]
    assert cpp_changes == expected_changes
    assert verilator_changes == expected_changes

    second = _run_build(
        source=source,
        out_dir=selected_out,
        pycc=pycc,
        trace_config=selected_config,
        target="both",
        run_verilator=False,
    )
    assert "jit-cache: hit" in f"{second.stdout}\n{second.stderr}"
    cache = json.loads(
        (selected_out / ".build_cache.json").read_text(encoding="utf-8")
    )
    assert cache["last_pycc_jobs"] == 0

    ports_only_config = tmp_path / "ports_only_trace.json"
    ports_only_config.write_text(
        json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "instances": ["dut"],
                        "ports": ["clk", "rst", "a", "b", "out"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _run_build(
        source=source,
        out_dir=selected_out,
        pycc=pycc,
        trace_config=ports_only_config,
        target="both",
        run_verilator=False,
    )
    cache = json.loads(
        (selected_out / ".build_cache.json").read_text(encoding="utf-8")
    )
    # The selected C++ module and both trace-config-dependent testbenches
    # rebuild; the Verilog device module remains cacheable.
    assert cache["last_pycc_jobs"] == 3
    assert json.loads(
        (selected_out / "trace_codegen_plan.json").read_text(encoding="utf-8")
    ) == {"version": 1, "modules": {}}
    cpp_header = (
        selected_out
        / "device"
        / "cpp"
        / "alias_name_check"
        / "alias_name_check.hpp"
    ).read_text(encoding="utf-8")
    assert "debug_sum_alias" not in cpp_header
    assert (
        selected_out
        / "device"
        / "verilog"
        / "alias_name_check"
        / "alias_name_check.v"
    ).read_text(encoding="utf-8") == verilog_device
    ports_only_placement = _placement_summary(selected_out)

    no_config_out = tmp_path / "no-config"
    _run_build(
        source=source,
        out_dir=no_config_out,
        pycc=pycc,
        trace_config=None,
        target="cpp",
    )
    manifest = json.loads(
        (no_config_out / "project_manifest.json").read_text(encoding="utf-8")
    )
    assert "trace_codegen_plan" not in manifest
    no_config_header = (
        no_config_out
        / "device"
        / "cpp"
        / "alias_name_check"
        / "alias_name_check.hpp"
    ).read_text(encoding="utf-8")
    assert "debug_sum_alias" not in no_config_header
    no_config_placement = _placement_summary(no_config_out)
    assert (
        selected_placement["probe_pinned_struct"]
        == no_config_placement["probe_pinned_struct"] + 1
    )
    assert (
        ports_only_placement["probe_pinned_struct"]
        == no_config_placement["probe_pinned_struct"]
    )

    no_config_trace_dir = no_config_out / "cpp-traces"
    _run_cpp(no_config_out, no_config_trace_dir)
    no_config_vcd = (
        no_config_trace_dir / "tb_alias_name_check" / "tb_alias_name_check.vcd"
    )
    assert "debug_sum_alias" not in no_config_vcd.read_text(encoding="utf-8")
