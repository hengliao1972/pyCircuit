"""Shared helpers for backend (pycc) regression gates.

Locates a usable ``pycc`` and stamps the minimal frontend-contract func attrs
that ``CheckFrontendContractPass`` requires, so cycle-aware designs (whose bare
``compile_cycle_aware(...).emit_mlir()`` is not contract-stamped) can be driven
through the real backend. Environment-configuration concerns (LLVM libs) are
handled here so individual tests stay focused on behavior.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_STRUCT_METRICS = (
    '{\\"source_loc\\":0,\\"ast_node_count\\":0,\\"hardware_call_count\\":0,'
    '\\"loop_count\\":0,\\"module_call_count\\":0,\\"state_call_count\\":0,'
    '\\"estimated_inline_cost\\":0,\\"instance_count\\":0,\\"state_alloc_count\\":0,'
    '\\"collection_count\\":0,\\"collection_instance_count\\":0,'
    '\\"module_family_collection_count\\":0,\\"repeated_body_clusters\\":[]}'
)


def stamp_contract(mlir: str, *, top: str) -> str:
    """Add the mandatory frontend-contract func attrs to a bare cycle-aware MLIR."""
    extra = (
        'pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", '
        f'pyc.base = "{top}", '
        f'pyc.struct.metrics = "{_STRUCT_METRICS}", pyc.struct.collections = "[]"'
    )
    mlir = mlir.replace(
        "module {",
        f'module attributes {{pyc.top = @{top}, pyc.frontend.contract = "pycircuit"}} {{',
        1,
    )
    m = re.search(rf"(func\.func @{re.escape(top)}\b[^\n]*?attributes \{{)", mlir)
    assert m, f"could not find @{top} attributes block to stamp"
    return mlir[: m.end()] + extra + ", " + mlir[m.end() :]


def find_pycc() -> str | None:
    candidates = []
    env = os.environ.get("PYCC")
    if env:
        candidates.append(Path(env))
    candidates += [
        _REPO / ".pycircuit_out" / "toolchain" / "install" / "bin" / "pycc",
        _REPO / ".pycircuit_out" / "toolchain" / "build" / "bin" / "pycc",
    ]
    which = shutil.which("pycc")
    if which:
        candidates.append(Path(which))
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def pycc_env() -> dict:
    env = os.environ.copy()
    extra_libs = [
        _REPO / ".pycircuit_out" / "llvm19" / "usr" / "lib" / "llvm-19" / "lib",
        _REPO / ".pycircuit_out" / "llvm19" / "usr" / "lib" / "x86_64-linux-gnu",
        Path("/usr/lib/llvm-19/lib"),
    ]
    present = [str(p) for p in extra_libs if p.is_dir()]
    if present:
        cur = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join([*present, cur]) if cur else ":".join(present)
    return env


def usable_pycc() -> tuple[str, dict] | None:
    """Return (pycc_path, env) if a runnable pycc is found, else None."""
    pycc = find_pycc()
    if pycc is None:
        return None
    env = pycc_env()
    try:
        proc = subprocess.run(
            [pycc, "--help"], capture_output=True, text=True, env=env, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return pycc, env
