#!/usr/bin/env python3
"""Shared plumbing for generated-model equivalence checks.

Both check_state_*_models.py scripts emit a C++ model with and without the
state optimization, compile the same driver against each header, and compare
the driver's cycles/checksum line.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEFAULT_BUILD = ROOT / ".pycircuit_out/toolchain/build/bin"
RESULT_RE = re.compile(r"cycles=(\d+) checksum=(\d+)")


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        command, cwd=ROOT, check=True, text=True, capture_output=True, env=env
    ).stdout


def result(command: list[str]) -> tuple[int, int]:
    match = RESULT_RE.search(run(command))
    if not match:
        raise RuntimeError(f"missing model result: {command}")
    return int(match.group(1)), int(match.group(2))


def emit(
    pycc: Path, source: Path, output: Path, *extra: str
) -> dict[str, object]:
    run([
        str(pycc), str(source), "--emit=cpp", *extra,
        "-o", str(output),
    ])
    return json.loads(Path(f"{output}.stats.json").read_text(encoding="utf-8"))


def compile_cpp(cxx: str, header: Path, binary: Path, driver: Path) -> None:
    run([
        cxx, "-std=c++17", "-O2", f"-I{ROOT / 'runtime'}",
        f'-DMODEL_HEADER="{header}"', str(driver), "-o", str(binary),
    ])
