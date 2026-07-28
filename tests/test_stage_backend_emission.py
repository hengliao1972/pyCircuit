"""Backend gate for A3 pipeline-stage attribution (frontend -> pycc -> RTL/C++).

Complements ``test_stage_naming.py`` (which is a pure-Python frontend gate) by
driving the full toolchain: the named-pipeline design in
``tests/designs/stage_demo.py`` is compiled to MLIR (carrying ``pyc.stage`` on
each ``pyc.reg``), then lowered by ``pycc`` to both Verilog and C++. We assert
that the stage tags survive lowering as:

- Verilog synthesis attributes ``(* pyc_stage = "<stage>" *)`` on reg instances;
- C++ DFX comments ``// pyc_stage: <stage>`` at reg construction.

The test is skipped (not failed) when a usable ``pycc`` backend is not available,
so it stays green in frontend-only environments while acting as a real gate
wherever the toolchain is built (locally or in CI, which installs LLVM/MLIR 19).
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pycircuit import compile_cycle_aware

_REPO = Path(__file__).resolve().parents[1]
_DESIGN = _REPO / "tests" / "designs" / "stage_demo.py"
_TOP = "stage_demo"

# Required frontend-contract func attrs that pycc's CheckFrontendContractPass
# enforces. The cycle-aware `build(m, domain)` entry is not stamped by the plain
# `compile_cycle_aware(...).emit_mlir()` path (that is a separate frontend
# concern), so we stamp the minimal, canonical set here -- mirroring the inline
# MLIR fixtures used by flows/scripts/run_examples.sh -- to exercise the backend.
_STRUCT_METRICS = (
    '{\\"source_loc\\":0,\\"ast_node_count\\":0,\\"hardware_call_count\\":0,'
    '\\"loop_count\\":0,\\"module_call_count\\":0,\\"state_call_count\\":0,'
    '\\"estimated_inline_cost\\":0,\\"instance_count\\":0,\\"state_alloc_count\\":0,'
    '\\"collection_count\\":0,\\"collection_instance_count\\":0,'
    '\\"module_family_collection_count\\":0,\\"repeated_body_clusters\\":[]}'
)


def _load_design():
    spec = importlib.util.spec_from_file_location("stage_demo_design", _DESIGN)
    assert spec and spec.loader, f"cannot load design: {_DESIGN}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build


def _stamp_contract(mlir: str, *, top: str) -> str:
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


def _find_pycc() -> str | None:
    env = os.environ.get("PYCC")
    candidates = []
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


def _pycc_env() -> dict:
    """Env for running pycc, augmenting LD_LIBRARY_PATH with any local LLVM libs."""
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


def _usable_pycc() -> tuple[str, dict] | None:
    pycc = _find_pycc()
    if pycc is None:
        return None
    env = _pycc_env()
    try:
        proc = subprocess.run(
            [pycc, "--help"], capture_output=True, text=True, env=env, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return pycc, env


@pytest.fixture(scope="module")
def stamped_pyc(tmp_path_factory) -> Path:
    build = _load_design()
    mlir = compile_cycle_aware(build, name=_TOP, eager=True).emit_mlir()
    # Sanity: the frontend must have produced the stage metadata we rely on.
    assert 'pyc.stage_names = ' in mlir
    assert 'pyc.stage = "decode"' in mlir
    assert 'pyc.stage = "execute"' in mlir
    stamped = _stamp_contract(mlir, top=_TOP)
    out = tmp_path_factory.mktemp("stage_be") / f"{_TOP}.pyc"
    out.write_text(stamped, encoding="utf-8")
    return out


def test_stage_attr_in_verilog(stamped_pyc: Path, tmp_path: Path) -> None:
    usable = _usable_pycc()
    if usable is None:
        pytest.skip("pycc backend not available (build it via `make tools`)")
    pycc, env = usable

    vpath = tmp_path / "stage_demo.v"
    proc = subprocess.run(
        [pycc, str(stamped_pyc), "--emit=verilog", "-o", str(vpath)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"pycc verilog failed:\n{proc.stdout}\n{proc.stderr}"
    v = vpath.read_text(encoding="utf-8")

    # Stage tags lower to Verilog synthesis attributes on reg instances. The
    # decode/execute pipeline regs are live; the fetch pc reg is DCE'd (feedback
    # only), so we assert on the two surviving stages.
    assert '(* pyc_stage = "decode" *)' in v
    assert '(* pyc_stage = "execute" *)' in v
    # Each attribute must immediately precede a reg instance.
    for stage in ("decode", "execute"):
        assert re.search(
            rf'\(\* pyc_stage = "{stage}" \*\)\s*\n\s*pyc_reg\b', v
        ), f"pyc_stage={stage!r} not attached to a pyc_reg instance"


def test_stage_attr_in_cpp(stamped_pyc: Path, tmp_path: Path) -> None:
    usable = _usable_pycc()
    if usable is None:
        pytest.skip("pycc backend not available (build it via `make tools`)")
    pycc, env = usable

    cpp_dir = tmp_path / "cpp"
    cpp_dir.mkdir()
    proc = subprocess.run(
        [pycc, str(stamped_pyc), "--emit=cpp", "--out-dir", str(cpp_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, f"pycc cpp failed:\n{proc.stdout}\n{proc.stderr}"

    hdr = cpp_dir / f"{_TOP}.hpp"
    assert hdr.is_file(), f"missing generated header: {hdr}"
    text = hdr.read_text(encoding="utf-8")
    assert "// pyc_stage: decode" in text
    assert "// pyc_stage: execute" in text
