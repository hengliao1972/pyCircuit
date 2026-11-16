"""Yosys/ABC synthesis adapter for the research-only Nangate45 library."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pycircuit.eda import (
    EdaError,
    EdaResult,
    EdaRunRequest,
    stage_verilog_project,
)

_TOP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class _Config:
    path: Path
    liberty: Path
    yosys: Path
    flatten: bool


def _config_path(options: Mapping[str, Any]) -> Path:
    explicit = options.get("config")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()
    env = os.environ.get("PYC_NANGATE45_CONFIG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (Path.cwd() / "eda" / "nangate45" / "config.local.json").resolve()


def _load_config(options: Mapping[str, Any]) -> _Config:
    path = _config_path(options)
    if not path.is_file():
        raise EdaError(
            f"Nangate45 config does not exist: {path}; copy config.example.json "
            "to config.local.json"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EdaError(f"cannot read Nangate45 config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EdaError(f"Nangate45 config must contain a JSON object: {path}")
    allowed = {"liberty", "yosys", "flatten"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise EdaError(
            f"unknown Nangate45 config field(s): {', '.join(unknown)}"
        )

    liberty_raw = value.get("liberty")
    if not isinstance(liberty_raw, str) or not liberty_raw.strip():
        raise EdaError("Nangate45 config field 'liberty' must be a path string")
    liberty = Path(liberty_raw).expanduser()
    if not liberty.is_absolute():
        liberty = path.parent / liberty
    liberty = liberty.resolve()
    if not liberty.is_file():
        raise EdaError(
            f"Nangate45 Liberty does not exist: {liberty}; run "
            "eda/nangate45/scripts/setup.sh"
        )

    yosys_raw = value.get("yosys", "yosys")
    if not isinstance(yosys_raw, str) or not yosys_raw.strip():
        raise EdaError("Nangate45 config field 'yosys' must be an executable")
    if "/" in yosys_raw:
        yosys_path = Path(yosys_raw).expanduser()
        if not yosys_path.is_absolute():
            yosys_path = path.parent / yosys_path
        yosys = yosys_path.resolve()
        if not yosys.is_file() or not os.access(yosys, os.X_OK):
            raise EdaError(f"configured Yosys is not executable: {yosys}")
    else:
        found = shutil.which(yosys_raw)
        if found is None:
            raise EdaError(f"Yosys executable was not found in PATH: {yosys_raw}")
        yosys = Path(found).resolve()

    flatten = value.get("flatten", True)
    if not isinstance(flatten, bool):
        raise EdaError("Nangate45 config field 'flatten' must be boolean")
    return _Config(path=path, liberty=liberty, yosys=yosys, flatten=flatten)


def _yosys_quote(value: Path | str) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise EdaError("Yosys paths and identifiers must not contain newlines")
    return json.dumps(text)


def _render_script(
    *,
    top: str,
    verilog_files: list[Path],
    config: _Config,
    output_dir: Path,
    report_dir: Path,
) -> str:
    if not _TOP_RE.fullmatch(top):
        raise EdaError(f"invalid Verilog top for Nangate45 synthesis: {top!r}")
    lines = [
        *(f"read_verilog -sv -DSYNTHESIS {_yosys_quote(path)}" for path in verilog_files),
        f"hierarchy -check -top {top}",
        "proc",
    ]
    if config.flatten:
        lines.append("flatten")
    lines.extend(
        [
            "opt",
            "memory",
            "opt",
            "techmap",
            "opt",
            f"dfflibmap -liberty {_yosys_quote(config.liberty)}",
            f"abc -liberty {_yosys_quote(config.liberty)}",
            "clean",
            (
                f"tee -o {_yosys_quote(report_dir / 'stat.rpt')} "
                f"stat -liberty {_yosys_quote(config.liberty)}"
            ),
            (
                f"tee -o {_yosys_quote(report_dir / 'stat.json')} "
                f"stat -json -liberty {_yosys_quote(config.liberty)}"
            ),
            (
                f"write_verilog -noattr "
                f"{_yosys_quote(output_dir / f'{top}.mapped.v')}"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Nangate45Flow:
    """Map pyCircuit RTL to Nangate45 cells with local Yosys and ABC."""

    name: str = "nangate45"

    def run(
        self,
        request: EdaRunRequest,
        options: Mapping[str, Any],
    ) -> EdaResult:
        config = _load_config(options)
        root = request.build_dir.resolve() / "eda" / self.name
        input_dir = root / "input"
        output_dir = root / "output"
        report_dir = root / "rpt"
        log_dir = root / "log"
        for directory in (output_dir, report_dir, log_dir):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)

        filelist = stage_verilog_project(
            verilog_files=request.verilog_files,
            sdc=None,
            staging_dir=input_dir,
        )
        staged_verilog = [
            input_dir / line
            for line in filelist.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        script = root / "synth.ys"
        script.write_text(
            _render_script(
                top=request.top,
                verilog_files=staged_verilog,
                config=config,
                output_dir=output_dir,
                report_dir=report_dir,
            ),
            encoding="utf-8",
        )

        process = subprocess.run(
            [str(config.yosys), "-s", str(script)],
            cwd=root,
            text=True,
            capture_output=True,
        )
        log = log_dir / "yosys.log"
        log.write_text(process.stdout + process.stderr, encoding="utf-8")
        netlist = output_dir / f"{request.top}.mapped.v"
        report = report_dir / "stat.rpt"
        metrics = report_dir / "stat.json"

        exit_code = process.returncode
        if exit_code == 0:
            if not netlist.is_file() or not report.is_file() or not metrics.is_file():
                exit_code = 8
            else:
                try:
                    parsed = json.loads(metrics.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    exit_code = 8
                else:
                    if not isinstance(parsed, dict):
                        exit_code = 8

        result = EdaResult(
            flow=self.name,
            exit_code=exit_code,
            result_dir=root,
            netlist=netlist if netlist.is_file() else None,
            qor_report=report if report.is_file() else None,
            metrics=metrics if metrics.is_file() else None,
            log=log,
            stdout=process.stdout,
            stderr=process.stderr,
        )
        (root / "result.json").write_text(
            json.dumps(
                result.as_manifest(relative_to=request.build_dir),
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return result
