from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pycircuit.eda import EdaError, available_eda_flows, run_eda_flow

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MOCK_YOSYS = _REPO_ROOT / "tests" / "fixtures" / "eda" / "mock_yosys.py"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _config(
    tmp_path: Path,
    *,
    liberty: str = "vendor/nangate.lib",
    yosys: str | None = None,
    extra: dict[str, object] | None = None,
) -> Path:
    value: dict[str, object] = {
        "liberty": liberty,
        "yosys": str(_MOCK_YOSYS if yosys is None else yosys),
        "flatten": True,
    }
    if extra:
        value.update(extra)
    return _write(
        tmp_path / "nangate45" / "config.local.json",
        json.dumps(value),
    )


def _rtl(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "generated" / "top.v",
        "module top(input a, input b, output y); assign y = a & b; endmodule\n",
    )


def test_nangate45_is_registered() -> None:
    assert "nangate45" in available_eda_flows()


def test_nangate45_mock_synthesis_records_normalized_outputs(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write(config.parent / "vendor" / "nangate.lib", "library(nangate) {}\n")
    build_dir = tmp_path / "build"

    result = run_eda_flow(
        "nangate45",
        top="top",
        verilog_files=[_rtl(tmp_path)],
        build_dir=build_dir,
        options={"config": config},
    )

    assert result.success
    assert result.netlist is not None
    assert "NAND2_X1" in result.netlist.read_text(encoding="utf-8")
    assert result.metrics is not None
    metrics = json.loads(result.metrics.read_text(encoding="utf-8"))
    assert metrics["modules"]["\\top"]["num_cells"] == 1
    manifest = (build_dir / "eda" / "nangate45" / "result.json").read_text()
    assert str(config.parent) not in manifest
    script = (build_dir / "eda" / "nangate45" / "synth.ys").read_text()
    assert "dfflibmap -liberty" in script
    assert "abc -liberty" in script


def test_nangate45_reports_missing_liberty(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with pytest.raises(EdaError, match="Liberty does not exist"):
        run_eda_flow(
            "nangate45",
            top="top",
            verilog_files=[_rtl(tmp_path)],
            build_dir=tmp_path / "build",
            options={"config": config},
        )


def test_nangate45_reports_missing_yosys(tmp_path: Path) -> None:
    config = _config(tmp_path, yosys="definitely-not-a-yosys-command")
    _write(config.parent / "vendor" / "nangate.lib", "library(nangate) {}\n")

    with pytest.raises(EdaError, match="not found in PATH"):
        run_eda_flow(
            "nangate45",
            top="top",
            verilog_files=[_rtl(tmp_path)],
            build_dir=tmp_path / "build",
            options={"config": config},
        )


def test_nangate45_rejects_unknown_config_field(tmp_path: Path) -> None:
    config = _config(tmp_path, extra={"command": "arbitrary shell"})
    _write(config.parent / "vendor" / "nangate.lib", "library(nangate) {}\n")

    with pytest.raises(EdaError, match="unknown Nangate45 config field"):
        run_eda_flow(
            "nangate45",
            top="top",
            verilog_files=[_rtl(tmp_path)],
            build_dir=tmp_path / "build",
            options={"config": config},
        )


def test_nangate45_preserves_yosys_failure_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    _write(config.parent / "vendor" / "nangate.lib", "library(nangate) {}\n")
    monkeypatch.setenv("PYC_MOCK_YOSYS_EXIT", "7")

    result = run_eda_flow(
        "nangate45",
        top="top",
        verilog_files=[_rtl(tmp_path)],
        build_dir=tmp_path / "build",
        options={"config": config},
    )

    assert not result.success
    assert result.exit_code == 7
    assert result.log is not None
    assert "mock Yosys failure" in result.log.read_text(encoding="utf-8")


def test_nangate45_setup_and_mock_yosys_are_executable() -> None:
    setup = _REPO_ROOT / "eda" / "nangate45" / "scripts" / "setup.sh"
    assert setup.is_file() and os.access(setup, os.X_OK)
    assert _MOCK_YOSYS.is_file() and os.access(_MOCK_YOSYS, os.X_OK)
