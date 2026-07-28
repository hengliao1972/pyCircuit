#!/usr/bin/env python3
"""End-to-end runner for the B1 commit-trace demo (self-contained, no Linx).

Runs the whole commit-interface loop and drops every artifact into an output
directory so you can inspect them by hand:

  <out>/commit_demo.mlir : frontend MLIR (commit_* ports + pyc.commit_iface attr)
  <out>/commit.jsonl     : commit stream from the standalone collector (hand-fed)
  <out>/commit_demo.v    : Verilog, ONLY if a pycc backend is found (optional)
  <out>/cpp/commit_demo.hpp : pycc-generated C++ design model (optional)
  <out>/commit.sim.jsonl : commit stream SOURCED FROM THE SIMULATED DESIGN via a
                           hand-bound collector (pycc + C++ compiler required)
  <out>/commit.auto.jsonl: commit stream from the collector the CppEmitter wove
                           into the model itself -- the testbench binds NOTHING,
                           it just sets PYC_COMMIT_TRACE and drives step()

Usage:
  python3 tests/designs/run_commit_e2e.py [OUT_DIR]

Default OUT_DIR is <repo>/.pycircuit_out/b1_e2e . A C++ compiler (g++/clang++)
is needed for the runtime stream; the pycc/Verilog step is optional and simply
skipped when the backend is absent -- the frontend+runtime loop is complete
without it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "compiler" / "frontend"))
sys.path.insert(0, str(_REPO / "tests"))

_DESIGN = _REPO / "tests" / "designs" / "commit_demo.py"
_DRIVER = _REPO / "tests" / "designs" / "commit_trace_main.cpp"
_RUNTIME_INC = _REPO / "runtime"


def _load_build():
    spec = importlib.util.spec_from_file_location("commit_demo_e2e", _DESIGN)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _REPO / ".pycircuit_out" / "b1_e2e"
    out.mkdir(parents=True, exist_ok=True)
    print(f"[e2e] output dir : {out}")

    from pycircuit import compile_cycle_aware

    # 1) frontend -> MLIR
    mlir = compile_cycle_aware(_load_build(), name="commit_demo", eager=True).emit_mlir()
    mlir_path = out / "commit_demo.mlir"
    mlir_path.write_text(mlir, encoding="utf-8")
    print(f"[e2e] wrote MLIR  -> {mlir_path}")

    # 2) build + run the C++ commit-trace collector -> JSONL
    cxx = shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        print("[e2e] WARN: no C++ compiler (g++/clang++); skipping runtime stream")
    else:
        exe = out / "commit_trace"
        subprocess.run(
            [cxx, "-std=c++17", "-O2", "-I", str(_RUNTIME_INC), str(_DRIVER), "-o", str(exe)],
            check=True,
        )
        jsonl_path = out / "commit.jsonl"
        subprocess.run([str(exe), str(jsonl_path)], check=True)
        print(f"[e2e] wrote JSONL -> {jsonl_path}")
        lines = [ln for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        hdr = json.loads(lines[0])
        rows = [json.loads(ln) for ln in lines[1:]]
        print(
            f"[e2e]   schema={hdr.get('commit_schema_id')} commits={len(rows)}"
            f" (bubble cycles skipped)"
        )

    # 3) optional: pycc backend -> Verilog (proves the gate accepts the design)
    u = None
    try:
        from _toolchain import stamp_contract, usable_pycc

        u = usable_pycc()
    except Exception as e:  # pragma: no cover - defensive
        print(f"[e2e] note: could not probe pycc ({e})")
    if u is None:
        print("[e2e] note: pycc backend not found; skipped Verilog + design-sourced trace")
        print("[e2e]       (frontend + collector loop above is complete without it)")
    else:
        pycc, env = u
        pyc_path = out / "commit_demo.pyc"
        pyc_path.write_text(stamp_contract(mlir, top="commit_demo"), encoding="utf-8")

        v_path = out / "commit_demo.v"
        proc = subprocess.run(
            [pycc, str(pyc_path), "--emit=verilog", "-o", str(v_path)],
            capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0:
            print(f"[e2e] pycc --emit=verilog failed:\n{proc.stderr}")
            return 1
        print(f"[e2e] wrote Verilog-> {v_path}")

        # True closed loop: emit the design's C++ model, then drive it and let the
        # collector observe the DESIGN's own commit_* ports (not hand values).
        model_dir = out / "cpp"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_hpp = model_dir / "commit_demo.hpp"
        proc = subprocess.run(
            [pycc, str(pyc_path), "--emit=cpp", "-o", str(model_hpp)],
            capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0:
            print(f"[e2e] pycc --emit=cpp failed:\n{proc.stderr}")
            return 1
        print(f"[e2e] wrote model  -> {model_hpp}")

        if cxx is None:
            print("[e2e] note: no C++ compiler; skipped design-sourced trace")
        else:
            sim_src = _REPO / "tests" / "designs" / "commit_trace_sim_main.cpp"
            sim_exe = out / "commit_trace_sim"
            subprocess.run(
                [cxx, "-std=c++17", "-O2", "-I", str(model_dir), "-I", str(_RUNTIME_INC),
                 str(sim_src), "-o", str(sim_exe)],
                check=True,
            )
            sim_jsonl = out / "commit.sim.jsonl"
            subprocess.run([str(sim_exe), str(sim_jsonl)], check=True)
            print(f"[e2e] wrote JSONL  -> {sim_jsonl}   (SOURCED FROM THE SIMULATED DESIGN)")

            # Zero-binding path: the CppEmitter auto-mounted the collector inside
            # the model. The testbench binds nothing -- it only drives step() and
            # PYC_COMMIT_TRACE tells the model where to flush its own commit stream.
            auto_src = _REPO / "tests" / "designs" / "commit_trace_auto_main.cpp"
            auto_exe = out / "commit_trace_auto"
            subprocess.run(
                [cxx, "-std=c++17", "-O2", "-I", str(model_dir), "-I", str(_RUNTIME_INC),
                 str(auto_src), "-o", str(auto_exe)],
                check=True,
            )
            auto_jsonl = out / "commit.auto.jsonl"
            auto_env = dict(os.environ, PYC_COMMIT_TRACE=str(auto_jsonl))
            subprocess.run([str(auto_exe)], check=True, env=auto_env)
            print(f"[e2e] wrote JSONL  -> {auto_jsonl}   (AUTO-MOUNTED, NO TESTBENCH BINDING)")

    print("[e2e] done. Inspect the files listed above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
