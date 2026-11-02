from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pycircuit.eda import (
    EdaError,
    EdaResult,
    XingtianOptions,
    available_eda_flows,
    register_eda_flow,
    run_eda_flow,
    run_xingtian,
    stage_xingtian_project,
    xingtian_runner_path,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MOCK_RUNNER = _REPO_ROOT / "tests" / "fixtures" / "eda" / "mock_xingtian_runner.py"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _generated_verilog(root: Path) -> list[Path]:
    primitive = "module pyc_reg; endmodule\n"
    return [
        _write(root / "a" / "a.v", "module a; endmodule\n"),
        _write(root / "a" / "pyc_primitives.v", primitive),
        _write(root / "b" / "b.v", "module b; endmodule\n"),
        _write(root / "b" / "pyc_primitives.v", primitive),
    ]


def test_stage_xingtian_project_is_deterministic_and_deduplicates_primitives(
    tmp_path: Path,
) -> None:
    sources = _generated_verilog(tmp_path / "generated")
    sdc = _write(tmp_path / "top.sdc", "create_clock -period 2 clk\n")
    staging = tmp_path / "stage"

    filelist = stage_xingtian_project(
        verilog_files=reversed(sources),
        sdc=sdc,
        staging_dir=staging,
    )

    entries = filelist.read_text(encoding="utf-8").splitlines()
    assert entries == [
        "rtl/a/a.v",
        "rtl/pyc_primitives.v",
        "rtl/b/b.v",
    ]
    assert (staging / "constraints.sdc.tcl").read_text() == sdc.read_text()
    assert all(not (staging / entry).is_symlink() for entry in entries)


def test_stage_rejects_mismatched_primitive_bundles(tmp_path: Path) -> None:
    sources = _generated_verilog(tmp_path / "generated")
    sources[-1].write_text("module different; endmodule\n", encoding="utf-8")
    sdc = _write(tmp_path / "top.sdc", "create_clock -period 2 clk\n")

    with pytest.raises(EdaError, match="differ"):
        stage_xingtian_project(
            verilog_files=sources,
            sdc=sdc,
            staging_dir=tmp_path / "stage",
        )


def test_run_xingtian_records_success_artifacts(tmp_path: Path) -> None:
    sources = _generated_verilog(tmp_path / "generated")
    sdc = _write(tmp_path / "top.sdc", "create_clock -period 2 clk\n")
    build_dir = tmp_path / "build"

    result = run_xingtian(
        top="top",
        verilog_files=sources,
        build_dir=build_dir,
        options=XingtianOptions(
            sdc=sdc,
            runner=_MOCK_RUNNER,
            host="test-host",
        ),
    )

    assert result.success
    assert result.exit_code == 0
    assert result.netlist is not None and result.netlist.name == "top.mapped.v"
    assert result.qor_report is not None
    assert result.metrics is not None
    assert result.log is not None
    assert (build_dir / "eda" / "xingtian" / "result.json").is_file()


def test_run_xingtian_loads_relative_local_config(tmp_path: Path) -> None:
    sources = _generated_verilog(tmp_path / "generated")
    build_dir = tmp_path / "build"
    config_dir = tmp_path / "local"
    sdc = _write(config_dir / "timing.sdc", "create_clock -period 2 clk\n")
    ssh_config = _write(config_dir / "ssh-config", "Host xiekp\n")
    identity = _write(config_dir / "identity", "local-test-key\n")
    config = _write(
        config_dir / "xingtian.json",
        json.dumps(
            {
                "host": "test-host",
                "sdc": sdc.name,
                "ssh_config": ssh_config.name,
                "identity_file": identity.name,
            }
        ),
    )

    result = run_xingtian(
        top="top",
        verilog_files=sources,
        build_dir=build_dir,
        options=XingtianOptions(config=config, runner=_MOCK_RUNNER),
    )

    assert result.success
    assert (
        build_dir / "eda" / "xingtian" / "input" / "constraints.sdc.tcl"
    ).read_text() == sdc.read_text()
    manifest_text = (
        build_dir / "eda" / "xingtian" / "result.json"
    ).read_text(encoding="utf-8")
    assert "identity" not in manifest_text
    assert "ssh-config" not in manifest_text


def test_explicit_sdc_overrides_local_config(tmp_path: Path) -> None:
    sources = _generated_verilog(tmp_path / "generated")
    explicit_sdc = _write(tmp_path / "explicit.sdc", "explicit\n")
    config = _write(
        tmp_path / "xingtian.json",
        json.dumps({"host": "test-host", "sdc": "missing-default.sdc"}),
    )

    result = run_xingtian(
        top="top",
        verilog_files=sources,
        build_dir=tmp_path / "build",
        options=XingtianOptions(
            sdc=explicit_sdc,
            config=config,
            runner=_MOCK_RUNNER,
        ),
    )

    assert result.success
    staged = (
        tmp_path
        / "build"
        / "eda"
        / "xingtian"
        / "input"
        / "constraints.sdc.tcl"
    )
    assert staged.read_text() == "explicit\n"


def test_xingtian_config_rejects_unknown_fields(tmp_path: Path) -> None:
    config = _write(
        tmp_path / "xingtian.json",
        json.dumps({"sdc": "top.sdc", "password": "must-not-be-supported"}),
    )

    with pytest.raises(EdaError, match="unknown XingTian config field"):
        run_xingtian(
            top="top",
            verilog_files=[],
            build_dir=tmp_path / "build",
            options=XingtianOptions(config=config, runner=_MOCK_RUNNER),
        )


def test_xingtian_config_requires_sdc(tmp_path: Path) -> None:
    config = _write(tmp_path / "xingtian.json", "{}")

    with pytest.raises(EdaError, match="SDC is not configured"):
        run_xingtian(
            top="top",
            verilog_files=[],
            build_dir=tmp_path / "build",
            options=XingtianOptions(config=config, runner=_MOCK_RUNNER),
        )


def test_xingtian_config_requires_explicit_host(tmp_path: Path) -> None:
    sdc = _write(tmp_path / "top.sdc", "create_clock -period 2 clk\n")
    config = _write(
        tmp_path / "xingtian.json",
        json.dumps({"sdc": str(sdc)}),
    )

    with pytest.raises(EdaError, match="SSH host is not configured"):
        run_xingtian(
            top="top",
            verilog_files=[],
            build_dir=tmp_path / "build",
            options=XingtianOptions(config=config, runner=_MOCK_RUNNER),
        )


def test_run_xingtian_preserves_failure_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = _generated_verilog(tmp_path / "generated")
    sdc = _write(tmp_path / "top.sdc", "create_clock -period 2 clk\n")
    monkeypatch.setenv("PYC_MOCK_XINGTIAN_EXIT", "6")

    result = run_xingtian(
        top="top",
        verilog_files=sources,
        build_dir=tmp_path / "build",
        options=XingtianOptions(
            sdc=sdc,
            runner=_MOCK_RUNNER,
            host="test-host",
        ),
    )

    assert not result.success
    assert result.exit_code == 6
    assert result.netlist is None
    assert result.log is not None and result.log.read_text() == "mock log\n"


def test_run_xingtian_rejects_missing_runner_before_staging(tmp_path: Path) -> None:
    sdc = _write(tmp_path / "top.sdc", "create_clock -period 2 clk\n")
    with pytest.raises(EdaError, match="runner"):
        run_xingtian(
            top="top",
            verilog_files=[],
            build_dir=tmp_path / "build",
            options=XingtianOptions(
                sdc=sdc,
                runner=tmp_path / "missing_runner",
                host="test-host",
            ),
        )


def test_packaged_runner_is_executable() -> None:
    runner = (
        _REPO_ROOT
        / "compiler"
        / "frontend"
        / "pycircuit"
        / "tools"
        / "xingtian"
        / "xt_remote_syn.sh"
    )
    assert runner.is_file()
    assert os.access(runner, os.X_OK)
    assert xingtian_runner_path() == runner


def test_registry_dispatches_typed_fake_backend(tmp_path: Path) -> None:
    class FakeFlow:
        name = "test_fake"

        def run(self, request, options):
            assert request.top == "top"
            assert options == {"mode": "smoke"}
            netlist = _write(
                request.build_dir / "eda" / self.name / "top.mapped.v",
                "module top; endmodule\n",
            )
            return EdaResult(
                flow=self.name,
                exit_code=0,
                result_dir=netlist.parent,
                netlist=netlist,
                qor_report=None,
                metrics=None,
                log=None,
                stdout="",
                stderr="",
            )

    register_eda_flow(FakeFlow(), replace=True)
    result = run_eda_flow(
        "test_fake",
        top="top",
        verilog_files=[],
        build_dir=tmp_path,
        options={"mode": "smoke"},
    )

    assert result.success
    assert result.flow == "test_fake"
    assert "test_fake" in available_eda_flows()


def test_registry_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(EdaError, match="unknown EDA flow"):
        run_eda_flow(
            "not_registered",
            top="top",
            verilog_files=[],
            build_dir=tmp_path,
        )
