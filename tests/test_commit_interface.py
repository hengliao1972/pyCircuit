"""Gates for the standardized commit/retire trace interface (TODO B1).

Covers the whole vertical slice:

- **frontend**: ``m.commit_interface({...})`` records a canonical
  ``pyc.commit_iface`` func attr and exposes ``commit_<field>`` output ports;
- **MLIR gate**: ``CheckFrontendContractPass`` enforces the contract via a
  generic, data-driven engine -- ``required`` fields and validity gating
  (Decision 0146: a group data field requires its ``valid`` strobe) come from the
  declaration itself, not from any framework-baked vocabulary. Enforcement lives
  in MLIR (gate-first), so the frontend records even malformed declarations and
  pycc rejects them;
- **runtime**: a self-contained loop compiles PyCircuit's own C++ collector,
  emits a commit-bundle JSONL for the demo schema, and validates it here -- no
  external CPU/ISA tooling involved.

Backend (pycc / C++) assertions skip when the toolchain is unavailable, so the
file stays green in frontend-only environments.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from _toolchain import stamp_contract, usable_pycc
from pycircuit import cas, compile_cycle_aware

_REPO = Path(__file__).resolve().parents[1]
_DESIGN = _REPO / "tests" / "designs" / "commit_demo.py"


def _load_build(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem + "_design", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build


# ── frontend contract (pure Python) ────────────────────────────────────────


def test_commit_iface_attr_and_ports_emitted() -> None:
    build = _load_build(_DESIGN)
    mlir = compile_cycle_aware(build, name="commit_demo", eager=True).emit_mlir()
    # The attribute is a native MLIR dictionary (structured, no escaped JSON).
    assert "pyc.commit_iface = {" in mlir
    assert 'schema = "pyc-commit-demo-v1"' in mlir
    assert 'stage = "commit"' in mlir
    assert 'valid = "commit_valid"' in mlir
    assert 'wb_data = "commit_wb_data"' in mlir
    # The schema-specific contract rides along as data (not framework-baked).
    assert 'required = ["valid", "pc", "insn", "wb_valid"]' in mlir
    assert 'groups = {wb = {members = ["wb_rd", "wb_data"], valid = "wb_valid"}}' in mlir
    # No JSON-in-string escaping should appear in the commit-iface attribute.
    seg = mlir[mlir.index("pyc.commit_iface") :]
    seg = seg[: seg.index("}}") + 2]
    assert "\\" not in seg
    # Each declared field must surface as a canonical observable output port.
    for field in ("valid", "pc", "insn", "wb_valid", "wb_rd", "wb_data"):
        assert f'"commit_{field}"' in mlir


def test_commit_interface_requires_nonempty_mapping() -> None:
    def bad(m, dom):
        m.commit_interface({})

    with pytest.raises(ValueError, match="non-empty"):
        compile_cycle_aware(bad, name="bad", eager=True)


def test_commit_interface_rejects_bad_field_name() -> None:
    def bad(m, dom):
        v = cas(dom, m.input("v", width=1))
        m.commit_interface({"1bad": v})

    with pytest.raises(ValueError, match="identifier"):
        compile_cycle_aware(bad, name="bad", eager=True)


# ── MLIR gate (requires pycc) ──────────────────────────────────────────────


def _run_pycc_verilog(mlir_top: str, mlir: str, tmp_path: Path):
    usable = usable_pycc()
    if usable is None:
        pytest.skip("pycc backend not available (build it via `make tools`)")
    pycc, env = usable
    pyc_path = tmp_path / f"{mlir_top}.pyc"
    pyc_path.write_text(stamp_contract(mlir, top=mlir_top), encoding="utf-8")
    import subprocess

    return subprocess.run(
        [pycc, str(pyc_path), "--emit=verilog", "-o", str(tmp_path / "out.v")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_valid_commit_iface_passes_pycc(tmp_path: Path) -> None:
    build = _load_build(_DESIGN)
    mlir = compile_cycle_aware(build, name="commit_demo", eager=True).emit_mlir()
    proc = _run_pycc_verilog("commit_demo", mlir, tmp_path)
    assert proc.returncode == 0, f"pycc unexpectedly failed:\n{proc.stdout}\n{proc.stderr}"


def test_group_member_without_strobe_rejected_by_gate(tmp_path: Path) -> None:
    # Generic validity gating (Decision 0146): a group with a member field
    # present but no `valid` strobe is rejected. Group names/membership are the
    # caller's data (neutral schema) -- the framework has no built-in `wb`.
    def bad(m, dom):
        pc = cas(dom, m.input("pc", width=32))
        rd = cas(dom, m.input("rd", width=5))
        data = cas(dom, m.input("data", width=32))
        v = cas(dom, m.input("v", width=1))
        m.commit_interface(
            {"valid": v, "pc": pc, "insn": pc, "wb_rd": rd, "wb_data": data},
            groups={"wb": {"valid": "wb_valid", "members": ["wb_rd", "wb_data"]}},
        )

    mlir = compile_cycle_aware(bad, name="bad_gate", eager=True).emit_mlir()
    proc = _run_pycc_verilog("bad_gate", mlir, tmp_path)
    assert proc.returncode != 0, "expected pycc to reject missing validity strobe"
    assert "PYC1006" in proc.stderr, proc.stderr


def test_missing_required_field_rejected_by_gate(tmp_path: Path) -> None:
    # Data-driven required set: `pc` is declared required but not provided.
    def bad(m, dom):
        v = cas(dom, m.input("v", width=1))
        insn = cas(dom, m.input("insn", width=32))
        m.commit_interface({"valid": v, "insn": insn}, required=["valid", "pc", "insn"])

    mlir = compile_cycle_aware(bad, name="bad_base", eager=True).emit_mlir()
    proc = _run_pycc_verilog("bad_base", mlir, tmp_path)
    assert proc.returncode != 0, "expected pycc to reject missing required field"
    assert "PYC1004" in proc.stderr, proc.stderr


# ── runtime closed loop (self-contained; no external differ) ────────────────

_DRIVER = _REPO / "tests" / "designs" / "commit_trace_main.cpp"
_RUNTIME_INC = _REPO / "runtime"


def _read_jsonl(path: Path) -> tuple[dict, list[dict]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    header = json.loads(lines[0])
    rows = [json.loads(ln) for ln in lines[1:]]
    return header, rows


def test_runtime_commit_trace_roundtrip(tmp_path: Path) -> None:
    """Full B1 loop with no external tooling: PyCircuit's own C++ collector emits
    a commit-bundle JSONL matching the demo schema, and we validate it here."""
    import shutil
    import subprocess

    gpp = shutil.which("g++") or shutil.which("clang++")
    if gpp is None:
        pytest.skip("no C++ compiler available to build the commit-trace driver")

    exe = tmp_path / "commit_trace"
    build = subprocess.run(
        [gpp, "-std=c++17", "-O2", "-I", str(_RUNTIME_INC), str(_DRIVER), "-o", str(exe)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert build.returncode == 0, f"driver compile failed:\n{build.stderr}"

    jsonl = tmp_path / "commit.jsonl"
    run = subprocess.run([str(exe), str(jsonl)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, f"driver run failed:\n{run.stderr}"

    header, rows = _read_jsonl(jsonl)
    # start header carries the design's own schema id (Decision 0142 versioning)
    assert header["type"] == "start"
    assert header["commit_schema_id"] == "pyc-commit-demo-v1"

    # 3 sampled cycles, 1 bubble (valid==0) -> exactly 2 committed rows
    assert len(rows) == 2, rows

    r0, r1 = rows
    assert r0["cycle"] == 0 and r0["stage"] == "commit"
    assert r0["pc"] == 0x100 and r0["insn"] == 0x13
    assert r0["wb_valid"] == 1 and r0["wb_rd"] == 3 and r0["wb_data"] == 0xDEAD
    assert r1["cycle"] == 2 and r1["wb_valid"] == 0

    # the retire strobe gates row emission; it must not leak in as a bundle field
    for r in rows:
        assert "valid" not in r


_SIM_DRIVER = _REPO / "tests" / "designs" / "commit_trace_sim_main.cpp"


def test_commit_trace_from_design_sim(tmp_path: Path) -> None:
    """The real closed loop: emit the design's C++ model with pycc, drive it, and
    let the collector observe the DESIGN's own commit_* ports -- proving the trace
    is sourced from the simulated design, not hand-fed testbench values."""
    import shutil
    import subprocess

    gpp = shutil.which("g++") or shutil.which("clang++")
    if gpp is None:
        pytest.skip("no C++ compiler available")
    usable = usable_pycc()
    if usable is None:
        pytest.skip("pycc backend not available")
    pycc, env = usable

    # 1) frontend -> stamped MLIR
    build = _load_build(_DESIGN)
    mlir = compile_cycle_aware(build, name="commit_demo", eager=True).emit_mlir()
    pyc_path = tmp_path / "commit_demo.pyc"
    pyc_path.write_text(stamp_contract(mlir, top="commit_demo"), encoding="utf-8")

    # 2) pycc -> C++ design model
    model_dir = tmp_path / "cpp"
    model_dir.mkdir()
    model_hpp = model_dir / "commit_demo.hpp"
    emit = subprocess.run(
        [pycc, str(pyc_path), "--emit=cpp", "-o", str(model_hpp)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert emit.returncode == 0, f"pycc --emit=cpp failed:\n{emit.stderr}"

    # 3) compile the design-connected driver against the generated model
    exe = tmp_path / "commit_trace_sim"
    comp = subprocess.run(
        [gpp, "-std=c++17", "-O2", "-I", str(model_dir), "-I", str(_RUNTIME_INC),
         str(_SIM_DRIVER), "-o", str(exe)],
        capture_output=True, text=True, timeout=120,
    )
    assert comp.returncode == 0, f"sim driver compile failed:\n{comp.stderr}"

    # 4) run -> JSONL sourced from the simulated design
    jsonl = tmp_path / "commit.sim.jsonl"
    run = subprocess.run([str(exe), str(jsonl)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, f"sim run failed:\n{run.stderr}"

    header, rows = _read_jsonl(jsonl)
    assert header["commit_schema_id"] == "pyc-commit-demo-v1"
    assert len(rows) == 2, rows  # bubble cycle produced no commit
    r0, r1 = rows
    assert r0["cycle"] == 0 and r0["pc"] == 0x100 and r0["wb_valid"] == 1 and r0["wb_data"] == 0xDEAD
    assert r1["cycle"] == 2 and r1["pc"] == 0x104 and r1["wb_valid"] == 0
    for r in rows:
        assert "valid" not in r


_AUTO_DRIVER = _REPO / "tests" / "designs" / "commit_trace_auto_main.cpp"


def test_commit_trace_auto_mounted_by_cpp_emitter(tmp_path: Path) -> None:
    """CppEmitter weaves the commit-trace sensor into the generated model straight
    from `pyc.commit_iface`. The testbench binds NOTHING -- it only drives step()
    and sets PYC_COMMIT_TRACE. The auto-produced stream must match the hand-bound
    one byte for byte, proving the auto-mount is a faithful, zero-effort path."""
    import os
    import shutil
    import subprocess

    gpp = shutil.which("g++") or shutil.which("clang++")
    if gpp is None:
        pytest.skip("no C++ compiler available")
    usable = usable_pycc()
    if usable is None:
        pytest.skip("pycc backend not available")
    pycc, env = usable

    build = _load_build(_DESIGN)
    mlir = compile_cycle_aware(build, name="commit_demo", eager=True).emit_mlir()
    pyc_path = tmp_path / "commit_demo.pyc"
    pyc_path.write_text(stamp_contract(mlir, top="commit_demo"), encoding="utf-8")

    model_dir = tmp_path / "cpp"
    model_dir.mkdir()
    model_hpp = model_dir / "commit_demo.hpp"
    emit = subprocess.run(
        [pycc, str(pyc_path), "--emit=cpp", "-o", str(model_hpp)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert emit.returncode == 0, f"pycc --emit=cpp failed:\n{emit.stderr}"

    # the generated model must carry the auto-mounted sensor and honor the env knob
    hpp_text = model_hpp.read_text(encoding="utf-8")
    assert "PycCommitTraceWriter" in hpp_text
    assert "PYC_COMMIT_TRACE" in hpp_text
    assert '#include <cpp/pyc_commit_trace.hpp>' in hpp_text

    # the auto driver contains no trace wiring whatsoever (ignore comment lines)
    driver_code = "\n".join(
        ln for ln in _AUTO_DRIVER.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("//")
    )
    assert "PycCommitTraceWriter" not in driver_code
    assert ".bind(" not in driver_code
    assert ".sample(" not in driver_code

    exe = tmp_path / "commit_trace_auto"
    comp = subprocess.run(
        [gpp, "-std=c++17", "-O2", "-I", str(model_dir), "-I", str(_RUNTIME_INC),
         str(_AUTO_DRIVER), "-o", str(exe)],
        capture_output=True, text=True, timeout=120,
    )
    assert comp.returncode == 0, f"auto driver compile failed:\n{comp.stderr}"

    jsonl = tmp_path / "commit.auto.jsonl"
    run = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PYC_COMMIT_TRACE=str(jsonl)),
    )
    assert run.returncode == 0, f"auto run failed:\n{run.stderr}"

    header, rows = _read_jsonl(jsonl)
    assert header["commit_schema_id"] == "pyc-commit-demo-v1"
    assert len(rows) == 2, rows
    r0, r1 = rows
    assert r0["cycle"] == 0 and r0["pc"] == 0x100 and r0["wb_valid"] == 1 and r0["wb_data"] == 0xDEAD
    assert r1["cycle"] == 2 and r1["pc"] == 0x104 and r1["wb_valid"] == 0
    for r in rows:
        assert "valid" not in r
