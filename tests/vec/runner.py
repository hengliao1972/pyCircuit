from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .cases import VecCase
from .generate import write_case_source, write_vector_ir_source


VECTOR_IR_ONLY_KINDS = {"or_reduce", "and_reduce", "reduce_sum", "reduce_sum_signed"}


def run_cmd(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        joined = " ".join(cmd)
        raise AssertionError(
            f"command failed ({proc.returncode}): {joined}\n"
            f"cwd: {cwd}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def merged_env(*, pythonpath: str, pycc: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = pythonpath
    env["PYCC"] = str(pycc)
    if pycc.parent.name == "bin":
        env.setdefault("PYC_TOOLCHAIN_ROOT", str(pycc.parent.parent))
    return env


def build_case(
    case: VecCase,
    *,
    repo_root: Path,
    out_root: Path,
    pythonpath: str,
    pycc: Path,
    run_verilator: bool,
) -> Path:
    case_dir = out_root / case.name
    src_dir = case_dir / "src"
    out_dir = case_dir / "build"
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir)
    src = write_case_source(case, src_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = merged_env(pythonpath=pythonpath, pycc=pycc)
    cmd = [
        sys.executable,
        "-m",
        "pycircuit.cli",
        "build",
        str(src),
        "--out-dir",
        str(out_dir),
        "--target",
        "both" if run_verilator else "cpp",
        "--jobs",
        os.environ.get("PYC_VEC_TEST_JOBS", "2"),
        "--logic-depth",
        os.environ.get("PYC_VEC_TEST_LOGIC_DEPTH", "64"),
        "--profile",
        "dev",
    ]
    if run_verilator:
        cmd.append("--run-verilator")
    run_cmd(cmd, cwd=repo_root, env=env)
    return out_dir


def read_design_pyc(out_dir: Path) -> str:
    pyc = out_dir / "device" / "design.pyc"
    if not pyc.is_file():
        raise AssertionError(f"missing emitted .pyc: {pyc}")
    return pyc.read_text(encoding="utf-8")


def check_ir(case: VecCase, mlir: str) -> None:
    missing = [tok for tok in case.ir_tokens if tok not in mlir]
    if missing:
        raise AssertionError(f"{case.name}: missing expected IR tokens {missing}")
    if "vector<" not in mlir:
        raise AssertionError(f"{case.name}: expected vector type in .pyc")


def run_cpp_binary(out_dir: Path) -> None:
    manifest = json.loads((out_dir / "project_manifest.json").read_text(encoding="utf-8"))
    exe = manifest.get("cpp_executable")
    if not exe:
        raise AssertionError("project_manifest.json missing cpp_executable")
    path = Path(exe)
    if not path.is_file():
        raise AssertionError(f"missing cpp executable: {path}")
    run_cmd([str(path)], cwd=out_dir)


def check_cpp_manifest_syntax(out_dir: Path, *, repo_root: Path) -> None:
    manifests = sorted(out_dir.rglob("cpp_compile_manifest.json"))
    if not manifests:
        # The canonical CMake build already compiled the generated C++; keep this
        # check optional for layouts that only preserve cpp_project_manifest.json.
        return
    for manifest_path in manifests:
        cpp_dir = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        include_dirs: list[Path] = [
            cpp_dir,
            repo_root / "runtime",
            repo_root / "runtime" / "cpp",
        ]
        include_dirs.extend(Path(p) for p in manifest.get("include_dirs", []))
        include_dirs.extend(Path(p) for p in manifest.get("runtime", {}).get("include_dirs", []))
        seen: list[Path] = []
        for inc in include_dirs:
            if inc not in seen:
                seen.append(inc)
        cmd_base = ["g++", "-std=c++17", "-fsyntax-only", *[f"-I{p}" for p in seen]]
        for src in manifest.get("sources", []):
            rel = src.get("path")
            if not rel:
                continue
            run_cmd([*cmd_base, str(cpp_dir / rel)], cwd=cpp_dir)


def emit_vector_ir_case(
    case: VecCase,
    *,
    repo_root: Path,
    case_root: Path,
    pythonpath: str,
    pycc: Path,
) -> Path:
    src = write_vector_ir_source(case, case_root / "src_vecir")
    out_dir = case_root / "vecir"
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = merged_env(pythonpath=pythonpath, pycc=pycc)
    pyc = out_dir / f"{case.name}.pyc"
    run_cmd(
        [sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)],
        cwd=repo_root,
        env=env,
    )
    check_ir(case, pyc.read_text(encoding="utf-8"))
    cpp_dir = out_dir / "cpp"
    run_cmd(
        [
            str(pycc),
            str(pyc),
            "--emit=cpp",
            "--cpp-split=module",
            "--out-dir",
            str(cpp_dir),
            "--build-profile=dev-fast",
        ],
        cwd=repo_root,
        env=env,
    )
    verilog = out_dir / f"{case.name}.v"
    run_cmd(
        [str(pycc), str(pyc), "--emit=verilog", "-o", str(verilog), "--build-profile=dev-fast"],
        cwd=repo_root,
        env=env,
    )
    check_cpp_manifest_syntax(out_dir, repo_root=repo_root)
    return out_dir


def assert_verilator_ran(out_dir: Path) -> None:
    manifest = json.loads((out_dir / "project_manifest.json").read_text(encoding="utf-8"))
    vbin = manifest.get("verilator_binary")
    if not vbin:
        raise AssertionError("project_manifest.json missing verilator_binary")
    if not Path(vbin).is_file():
        raise AssertionError(f"missing verilator binary: {vbin}")


def run_vec_case(
    case: VecCase,
    *,
    repo_root: Path,
    out_root: Path,
    pythonpath: str,
    pycc: Path,
    verilator: str | None,
) -> None:
    use_verilator = bool(verilator and case.verilator)
    case_root = out_root / case.name
    out_dir = build_case(
        case,
        repo_root=repo_root,
        out_root=out_root,
        pythonpath=pythonpath,
        pycc=pycc,
        run_verilator=use_verilator,
    )
    if case.kind in VECTOR_IR_ONLY_KINDS:
        emit_vector_ir_case(case, repo_root=repo_root, case_root=case_root, pythonpath=pythonpath, pycc=pycc)
    else:
        check_ir(case, read_design_pyc(out_dir))
    run_cpp_binary(out_dir)
    check_cpp_manifest_syntax(out_dir, repo_root=repo_root)
    if use_verilator:
        assert_verilator_ran(out_dir)
