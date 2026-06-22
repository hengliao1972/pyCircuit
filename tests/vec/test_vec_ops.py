from __future__ import annotations

from pathlib import Path

import pytest

from .cases import FRONTEND_ONLY_CASES, FULL_BACKEND_CASES, VEC_CASES, VecCase
from .generate import render_case_source
from .runner import check_cpp_manifest_syntax, check_ir, merged_env, run_cmd, run_vec_case


@pytest.mark.vec
@pytest.mark.slow
@pytest.mark.parametrize("case", FULL_BACKEND_CASES, ids=[case.name for case in FULL_BACKEND_CASES])
def test_vec_operator_case(
    case: VecCase,
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
    verilator: str | None,
) -> None:
    run_vec_case(
        case,
        repo_root=repo_root,
        out_root=vec_test_root,
        pythonpath=pyc_pythonpath,
        pycc=pycc,
        verilator=verilator,
    )


@pytest.mark.vec
def test_case_matrix_has_minimum_coverage() -> None:
    kinds = {case.kind for case in VEC_CASES}
    required = {
        "add_vv",
        "add_vs",
        "add_sv",
        "eq_vv",
        "eq_vs",
        "eq_sv",
        "sub_vs",
        "sub_sv",
        "or_reduce",
        "reduce_sum",
        "reduce_sum_signed",
        "select_vv",
        "select_vs",
        "select_sv",
        "zext",
        "sext",
        "slice",
    }
    assert required <= kinds


@pytest.mark.vec
def test_true_division_is_rejected(repo_root: Path) -> None:
    import sys

    frontend = repo_root / "compiler" / "frontend"
    if str(frontend) not in sys.path:
        sys.path.insert(0, str(frontend))

    from pycircuit import Circuit, Vec, compile, module

    m = Circuit("vec_true_division_rejected")
    a = Vec([m.input(f"a{i}", width=4) for i in range(4)])
    b = Vec([m.input(f"b{i}", width=4) for i in range(4)])

    with pytest.raises(TypeError, match="use `//`"):
        _ = a / b
    with pytest.raises(TypeError, match="use `//`"):
        _ = a / 3
    with pytest.raises(TypeError, match="use `//`"):
        _ = 12 / a
    with pytest.raises(TypeError, match="use `//`"):
        _ = a[0] / b[0]

    @module
    def build(m: Circuit) -> None:
        lhs = Vec([m.input(f"lhs{i}", width=4) for i in range(4)])
        rhs = Vec([m.input(f"rhs{i}", width=4) for i in range(4)])
        out = lhs / rhs
        m.output("out0", out[0])

    with pytest.raises(Exception, match="use `//`"):
        compile(build, name="vec_true_division_jit_rejected")


@pytest.mark.vec
@pytest.mark.parametrize("case", FRONTEND_ONLY_CASES, ids=[case.name for case in FRONTEND_ONLY_CASES])
def test_frontend_only_case_generation(case: VecCase) -> None:
    source = render_case_source(case)
    assert "Vec([" in source
    assert case.kind in source or case.name in source


@pytest.mark.vec
def test_vector_io_emit_and_pycc(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    case_root = vec_test_root / "vector_io"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "vector_io.py"
    src.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pycircuit import Circuit, module",
                "",
                "",
                "@module",
                "def build(m: Circuit) -> None:",
                '    a = m.input("a", width=4, shape=4)',
                "    out = a + a",
                '    m.output("out", out)',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "vector_io.pyc"
    run_cmd(["python3", "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    mlir = pyc.read_text(encoding="utf-8")
    check_ir(VecCase("vector_io", "vector_io", ir_tokens=("vector<", "pyc.add")), mlir)
    cpp_dir = out_dir / "cpp"
    run_cmd(
        [str(pycc), str(pyc), "--emit=cpp", "--cpp-split=module", "--out-dir", str(cpp_dir), "--build-profile=dev-fast"],
        cwd=repo_root,
        env=env,
    )
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(out_dir / "vector_io.v"), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    check_cpp_manifest_syntax(out_dir, repo_root=repo_root)


@pytest.mark.vec
def test_dim_reduce_emit_and_pycc(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    case_root = vec_test_root / "dim_reduce"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "dim_reduce.py"
    src.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pycircuit import Circuit, module",
                "",
                "",
                "@module",
                "def build(m: Circuit) -> None:",
                '    a = m.input("a", width=1, shape=(2, 3))',
                '    m.output("or0", a.or_reduce(dim=0))',
                '    m.output("or1", a.or_reduce(dim=1))',
                '    m.output("and0", a.and_reduce(dim=0))',
                '    m.output("and1", a.and_reduce(dim=1))',
                '    m.output("sum0", a.reduce_sum(dim=0))',
                '    m.output("sum1", a.reduce_sum(dim=1))',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "dim_reduce.pyc"
    run_cmd(["python3", "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    check_ir(
        VecCase("dim_reduce", "dim_reduce", ir_tokens=("vector<", "pyc.v_or_reduce", "pyc.v_and_reduce", "pyc.v_add_reduce")),
        pyc.read_text(encoding="utf-8"),
    )
    cpp_dir = out_dir / "cpp"
    run_cmd(
        [str(pycc), str(pyc), "--emit=cpp", "--cpp-split=module", "--out-dir", str(cpp_dir), "--build-profile=dev-fast"],
        cwd=repo_root,
        env=env,
    )
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(out_dir / "dim_reduce.v"), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    check_cpp_manifest_syntax(out_dir, repo_root=repo_root)
