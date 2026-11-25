from __future__ import annotations

import sys
from pathlib import Path

import pytest

from .cases import FULL_BACKEND_CASES, VEC_CASES, VecCase
from .generate import render_case_source
from .runner import assert_verilator_ran, check_cpp_manifest_syntax, check_ir, merged_env, run_cmd, run_cpp_binary, run_vec_case


def _run_yosys_smoke(verilog: Path, *, top: str, repo_root: Path) -> None:
    import shutil

    yosys = shutil.which("yosys")
    if yosys is None:
        pytest.skip("yosys not found")
    script = verilog.with_suffix(".ys")
    script.write_text(
        "\n".join(
            [
                f"read_verilog -I{repo_root / 'runtime' / 'verilog'} {verilog}",
                f"hierarchy -top {top}",
                "proc",
                "opt",
                "stat",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run_cmd([yosys, "-q", "-s", str(script)], cwd=repo_root)


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

    from pycircuit import Circuit

    m = Circuit("vec_true_division_rejected")
    a = m.vec([m.input(f"a{i}", width=4) for i in range(4)])
    b = m.vec([m.input(f"b{i}", width=4) for i in range(4)])

    with pytest.raises(TypeError, match="use `//`"):
        _ = a / b
    with pytest.raises(TypeError, match="use `//`"):
        _ = a / 3
    with pytest.raises(TypeError, match="use `//`"):
        _ = 12 / a
    with pytest.raises(TypeError, match="use `//`"):
        _ = a[0] / b[0]
        compile(build, name="vec_true_division_jit_rejected")


@pytest.mark.vec
def test_vec_accepts_wire_list_only(repo_root: Path) -> None:
    import sys

    frontend = repo_root / "compiler" / "frontend"
    if str(frontend) not in sys.path:
        sys.path.insert(0, str(frontend))

    from pycircuit import Circuit

    m = Circuit("vec_wire_list_only")
    lanes = [m.input(f"a{i}", width=1) for i in range(2)]
    assert m.vec(lanes).ty == "vector<2xi1>"
    with pytest.raises(TypeError, match="expects list\\[Wire\\], got tuple"):
        m.vec(tuple(lanes))  # type: ignore[arg-type]


@pytest.mark.vec
def test_constant_vector_elementwise_and_reductions_emit(repo_root: Path) -> None:
    """Exercise rank-1/rank-2 constant Vec forms accepted by dialect folders."""
    import sys

    frontend = repo_root / "compiler" / "frontend"
    if str(frontend) not in sys.path:
        sys.path.insert(0, str(frontend))

    from pycircuit import Circuit

    m = Circuit("constant_vector_folds")
    known = m.const([1, 2, 3, 4], width=4)
    mixed = m.vec(
        [m.const(1, width=4), m.input("unknown", width=4), m.const(3, width=4), m.const(4, width=4)]
    )
    matrix = m.const([[1, 2], [3, 4]], width=4)

    m.output("add_known", known + known)
    m.output("add_mixed", mixed + known)
    m.output("get_known", (known + known)[2])
    m.output("sum_all", matrix.reduce_sum())
    m.output("or_dim0", matrix.reduce_or(dim=0))
    m.output("and_dim1", matrix.reduce_and(dim=1))

    mlir = m.emit_mlir()
    # Constant Vec forms fold per lane; only scalar ops and packed concats remain.
    assert "vector<" not in mlir
    assert "pyc.v_" not in mlir
    assert "pyc.add" in mlir
    assert "pyc.or" in mlir
    assert "pyc.and" in mlir
    assert "pyc.concat" in mlir
    assert mlir.count("-> i16") == 2


@pytest.mark.vec
def test_circuit_priority_mux_lowers_chain_and_tree_forms(repo_root: Path) -> None:
    import sys

    frontend = repo_root / "compiler" / "frontend"
    if str(frontend) not in sys.path:
        sys.path.insert(0, str(frontend))

    from pycircuit import Circuit

    m = Circuit("circuit_priority_mux")
    sel = m.vec([m.input(f"sel{i}", width=1) for i in range(3)])
    vals = m.vec([m.input(f"val{i}", width=4) for i in range(3)])
    m.output("priority", m.priority_mux(sel, vals, default=m.const(0, width=4)))
    m.output("tree", m.priority_mux(sel, vals, mode="tree"))
    mlir = m.emit_mlir()

    assert mlir.count("pyc.mux") == 6
    assert mlir.count("pyc.or") == 2
    assert "vector<" not in mlir
    assert "pyc.v_" not in mlir


@pytest.mark.vec
def test_vector_api_functional_gaps_run_cpp(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    """Exercise vector APIs that are not represented by the generated case matrix."""
    case_root = vec_test_root / "vector_api_functional_gaps"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "vector_api_functional_gaps.py"
    src.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pycircuit import Circuit, Tb, module, testbench",
                "",
                "",
                "@module",
                "def build(m: Circuit) -> None:",
                '    a = m.input("a", width=2, shape=[2])',
                '    m.output("bcast0", a.broadcast(size=2, dim=0))',
                '    m.output("bcast1", a.broadcast(size=2, dim=1))',
                '    m.output("index", a[1])',
                '    m.output("cat", m.cat(a[0], a[1]))',
                '    rank2 = m.input("rank2", width=4, shape=[2, 2])',
                '    m.output("slice_rank2", rank2.slice(lsb=1, width=2))',
                '    shift = m.input("shift", width=2)',
                # << / >> with a Wire amount is rejected by the JIT (constant
                # only); the method form is the dynamic-shift API.
                '    m.output("shifted", a.shl(amount=shift))',
                '    m.output("shifted_right", a.lshr(amount=shift))',
                '    sels = m.input("sels", width=1, shape=[3])',
                '    no_sels = m.input("no_sels", width=1, shape=[3])',
                '    vals = m.input("vals", width=4, shape=[3])',
                '    fallback = m.input("fallback", width=4)',
                '    m.output("priority", m.priority_mux(sels, vals, default=fallback))',
                '    m.output("priority_default", m.priority_mux(no_sels, vals, default=fallback))',
                '    m.output("priority_last", m.priority_mux(no_sels, vals))',
                '    a2 = m.input("a2", width=2, shape=[2, 2])',
                '    b2 = m.input("b2", width=2, shape=[2, 2])',
                '    m.output("add2", a2 + b2)',
                '    red = m.input("red", width=1, shape=[3])',
                '    m.output("or_all", red.reduce_or(dim=None))',
                '    m.output("and_all", red.reduce_and(dim=None))',
                '    m.output("sum_all", red.reduce_sum())',
                '    sa = m.input("sa", width=4, signed=True, shape=[2])',
                '    sb = m.input("sb", width=4, signed=True, shape=[2])',
                '    m.output("signed_add", sa + sb)',
                '    m.output("signed_shifted_right", sa.ashr(amount=shift))',
                "",
                "",
                "@testbench",
                "def tb(t: Tb) -> None:",
                "    t.timeout(1)",
                '    t.drive("a", 0x9, at=0)',
                '    t.drive("shift", 1, at=0)',
                '    t.drive("sels", 0x6, at=0)',
                '    t.drive("no_sels", 0, at=0)',
                '    t.drive("vals", 0x961, at=0)',
                '    t.drive("fallback", 9, at=0)',
                '    t.drive("a2", 0x39, at=0)',
                '    t.drive("b2", 0x5B, at=0)',
                '    t.drive("red", 0x3, at=0)',
                '    t.drive("sa", 0x2F, at=0)',
                '    t.drive("sb", 0xF1, at=0)',
                '    t.expect("bcast0", 0x99, at=0)',
                '    t.expect("bcast1", 0xA5, at=0)',
                '    t.expect("index", 2, at=0)',
                '    t.expect("cat", 0x6, at=0)',
                '    t.expect("shifted", 0x42, at=0)',
                '    t.expect("shifted_right", 0x4, at=0)',
                '    t.expect("priority", 6, at=0)',
                '    t.expect("priority_default", 9, at=0)',
                '    t.expect("priority_last", 9, at=0)',
                '    t.expect("add2", 0x40, at=0)',
                '    t.expect("or_all", 1, at=0)',
                '    t.expect("and_all", 0, at=0)',
                '    t.expect("sum_all", 0, at=0)',
                '    t.expect("signed_add", 0x10, at=0)',
                '    t.expect("signed_shifted_right", 0x1F, at=0)',
                "    t.finish(at=0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    run_cmd(
        [
            sys.executable,
            "-m",
            "pycircuit.cli",
            "build",
            str(src),
            "--out-dir",
            str(out_dir),
            "--target",
            "cpp",
            "--jobs",
            "2",
            "--logic-depth",
            "64",
            "--profile",
            "dev",
        ],
        cwd=repo_root,
        env=env,
    )
    run_cpp_binary(out_dir)


@pytest.mark.vec
def test_vector_api_rejects_invalid_shapes_and_priority_inputs(repo_root: Path) -> None:
    import sys

    frontend = repo_root / "compiler" / "frontend"
    if str(frontend) not in sys.path:
        sys.path.insert(0, str(frontend))

    from pycircuit import Circuit

    m = Circuit("vector_api_invalid")
    scalar = m.input("scalar", width=1)
    sels = m.input("sels", width=1, shape=[2])
    vals = m.input("vals", width=4, shape=[2])
    wrong_vals = m.input("wrong_vals", width=4, shape=[3])
    wrong_default = m.input("wrong_default", width=3)
    rank2 = m.input("rank2", width=1, shape=[2, 2])

    assert len(sels) == 2
    assert [lane.ty for lane in sels] == [sels.ty.elem, sels.ty.elem]

    with pytest.raises(TypeError, match="broadcast requires Vector"):
        scalar.broadcast(size=2, dim=0)
    with pytest.raises(ValueError, match="dim out of range"):
        sels.broadcast(size=2, dim=2)
    with pytest.raises(TypeError, match="sels must be vector<Nxi1>"):
        m.priority_mux(vals, vals)
    with pytest.raises(TypeError, match="sels length must equal"):
        m.priority_mux(sels, wrong_vals)
    with pytest.raises(TypeError, match="default shape/type"):
        m.priority_mux(sels, vals, default=wrong_default)
    with pytest.raises(ValueError, match="mode must be"):
        m.priority_mux(sels, vals, mode="flat")
    with pytest.raises(ValueError, match="dim out of range"):
        rank2.reduce_or(dim=2)

    m.output("or_rank2_all", rank2.reduce_or())
    m.output("and_rank2_all", rank2.reduce_and())
    m.output("sum_rank2_all", rank2.reduce_sum())
    mlir = m.emit_mlir()
    assert "vector<" not in mlir
    assert "pyc.v_" not in mlir
    assert mlir.count("pyc.or ") == 3
    assert mlir.count("pyc.and ") == 3
    assert mlir.count("pyc.add ") == 3
    assert "-> (i1, i1, i1)" in mlir


@pytest.mark.vec
def test_scalar_lane_pipeline_precedes_wire_and_state_cleanup(repo_root: Path) -> None:
    """Vector lanes are already scalar in the frontend-emitted IR.

    pycc therefore has no Vector-unroll / SLP-pack stage; the scalar cleanup
    passes simply run after static SCF lowering.
    """
    pipeline = (repo_root / "compiler" / "mlir" / "tools" / "pycc.cpp").read_text(encoding="utf-8")
    assert "createVectorUnrollPass" not in pipeline
    assert "createSLPPackWiresPass" not in pipeline
    assert "unroll-vector" not in pipeline
    lower_scf = pipeline.index("pm.addNestedPass<func::FuncOp>(pyc::createLowerSCFToPYCStaticPass());")
    eliminate_wires = pipeline.index("pm.addNestedPass<func::FuncOp>(pyc::createEliminateWiresPass());")
    eliminate_state = pipeline.index("pm.addNestedPass<func::FuncOp>(pyc::createEliminateDeadStatePass());")

    assert lower_scf < eliminate_wires < eliminate_state


@pytest.mark.vec
def test_vector_state_assign_runs_cpp(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    """Vector wire→assign→reg lowers to per-lane scalar state in the .pyc."""
    case_root = vec_test_root / "vector_state_assign"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "vector_state_assign.py"
    src.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pycircuit import Circuit, Tb, module, testbench",
                "",
                "",
                "@module",
                "def build(m: Circuit) -> None:",
                '    clk = m.clock("clk")',
                '    rst = m.reset("rst")',
                '    r = m.out("cnt", clk=clk, rst=rst, width=4, shape=[2], init=0)',
                "    r.set(r + 1)",
                '    m.output("cnt", r)',
                "",
                "",
                "@testbench",
                "def tb(t: Tb) -> None:",
                '    t.clock("clk")',
                '    t.reset("rst", cycles_asserted=2, cycles_deasserted=1)',
                "    t.timeout(8)",
                "    t.finish(at=4)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "vector_state_assign.pyc"
    run_cmd([sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    mlir = pyc.read_text(encoding="utf-8")
    assert "vector<" not in mlir
    assert "pyc.v_" not in mlir
    assert mlir.count("pyc.reg") == 2
    assert "pyc.assign" in mlir
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
    # Vector state must become scalar regs/wires, not keep the vector runtime.
    cpp_text = "\n".join(p.read_text(encoding="utf-8") for p in cpp_dir.rglob("*.cpp"))
    cpp_headers = "\n".join(p.read_text(encoding="utf-8") for p in cpp_dir.rglob("*.hpp"))
    assert "pycVecReg" not in cpp_text + cpp_headers
    assert "pycVecDelayLine" not in cpp_text + cpp_headers
    assert "pyc::cpp::Vec<" not in cpp_text + cpp_headers
    # Scalarized state may lower to individual regs or to a RegLoop bank.
    assert any(tok in cpp_text + cpp_headers for tok in ("pycReg", "RegLoop", "pycDelayLine"))
    verilog = out_dir / "vector_state_assign.v"
    run_cmd(
        [
            str(pycc),
            str(pyc),
            "--emit=verilog",
            "-o",
            str(verilog),
            "--build-profile=dev-fast",
        ],
        cwd=repo_root,
        env=env,
    )
    verilog_text = verilog.read_text(encoding="utf-8")
    assert "pycReg" in verilog_text or "reg " in verilog_text
    # The full CLI build must still compile and link a TB (smoke).
    run_cmd(
        [
            sys.executable,
            "-m",
            "pycircuit.cli",
            "build",
            str(src),
            "--out-dir",
            str(out_dir / "sim"),
            "--target",
            "cpp",
            "--jobs",
            "2",
            "--logic-depth",
            "64",
            "--profile",
            "dev",
        ],
        cwd=repo_root,
        env=env,
    )
    run_cpp_binary(out_dir / "sim")


@pytest.mark.vec
def test_assign_dst_must_be_wire_verifier(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    """AssignOp verifier must keep rejecting non-wire destinations."""
    case_root = vec_test_root / "bad_assign_dst"
    case_root.mkdir(parents=True, exist_ok=True)
    pyc = case_root / "bad_assign_dst.pyc"
    pyc.write_text(
        "\n".join(
            [
                'module attributes {pyc.frontend.contract = "pycircuit"} {',
                'func.func @bad_assign_dst(%a: i4) -> (i4) attributes {arg_names = ["a"], result_names = ["o"]} {',
                "  %v0 = pyc.constant 1 : i4",
                "  %v1 = pyc.constant 2 : i4",
                "  pyc.assign %v0, %v1 : i4",
                "  func.return %v0 : i4",
                "}",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    import subprocess

    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    proc = subprocess.run(
        [str(pycc), str(pyc), "--emit=verilog", "-o", str(case_root / "out.v"), "--build-profile=dev-fast"],
        cwd=str(repo_root),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "dst must be defined by pyc.wire" in combined


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
                '    a = m.input("a", width=4, shape=[4])',
                "    out = a + a",
                '    m.output("out", out)',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "vector_io.pyc"
    run_cmd([sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    mlir = pyc.read_text(encoding="utf-8")
    check_ir(VecCase("vector_io", "vector_io", ir_tokens=("pyc.extract", "pyc.add", "pyc.concat")), mlir)
    assert "%a: i16" in mlir
    cpp_dir = out_dir / "cpp"
    run_cmd(
        [str(pycc), str(pyc), "--emit=cpp", "--cpp-split=module", "--out-dir", str(cpp_dir), "--build-profile=dev-fast"],
        cwd=repo_root,
        env=env,
    )
    verilog = out_dir / "vector_io.v"
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(verilog), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    verilog_text = verilog.read_text(encoding="utf-8")
    assert "input [15:0] a" in verilog_text
    assert "output [15:0] out" in verilog_text
    assert "input [3:0] a [0:3]" not in verilog_text
    assert "output [3:0] out [0:3]" not in verilog_text
    check_cpp_manifest_syntax(out_dir, repo_root=repo_root)


@pytest.mark.vec
def test_rank2_vector_io_verilog_uses_packed_ports_and_yosys(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    case_root = vec_test_root / "rank2_vector_io"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "rank2_vector_io.py"
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
                '    a = m.input("a", width=2, shape=[2, 3])',
                "    out = a + a",
                '    m.output("out", out)',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "rank2_vector_io.pyc"
    run_cmd([sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    verilog = out_dir / "rank2_vector_io.v"
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(verilog), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    verilog_text = verilog.read_text(encoding="utf-8")
    assert "input [11:0] a" in verilog_text
    assert "output [11:0] out" in verilog_text
    assert "input [1:0] a [0:1][0:2]" not in verilog_text
    assert "output [1:0] out [0:1][0:2]" not in verilog_text
    _run_yosys_smoke(verilog, top="Rank2VectorIo", repo_root=repo_root)


@pytest.mark.vec
def test_vector_port_cli_build_runs_tb(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
    verilator: str | None,
) -> None:
    case_root = vec_test_root / "vector_port_tb"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "vector_port_tb.py"
    src.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pycircuit import Circuit, Tb, module, testbench",
                "",
                "",
                "@module",
                "def build(m: Circuit) -> None:",
                '    a = m.input("a", width=4, shape=[4])',
                "    out = a + 1",
                '    m.output("out", out)',
                "",
                "",
                "@testbench",
                "def tb(t: Tb) -> None:",
                "    t.timeout(1)",
                '    t.drive("a", 0x4321, at=0)',
                '    t.expect("out", 0x5432, at=0, msg="vector out")',
                "    t.finish(at=0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    run_cmd(
        [
            sys.executable,
            "-m",
            "pycircuit.cli",
            "build",
            str(src),
            "--out-dir",
            str(out_dir),
            "--target",
            "cpp",
            "--jobs",
            "2",
            "--logic-depth",
            "64",
            "--profile",
            "dev",
        ],
        cwd=repo_root,
        env=env,
    )
    run_cpp_binary(out_dir)
    if verilator:
        verilator_out = case_root / "build_verilator"
        run_cmd(
            [
                sys.executable,
                "-m",
                "pycircuit.cli",
                "build",
                str(src),
                "--out-dir",
                str(verilator_out),
                "--target",
                "both",
                "--jobs",
                "2",
                "--logic-depth",
                "64",
                "--profile",
                "dev",
                "--run-verilator",
            ],
            cwd=repo_root,
            env=env,
        )
        assert_verilator_ran(verilator_out)


@pytest.mark.vec
def test_eager_vec_broadcast_emit_and_pycc(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    case_root = vec_test_root / "eager_broadcast"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "eager_broadcast.py"
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
                '    a = m.vec([m.input(f"a{i}", width=4) for i in range(4)])',
                "    out = a.broadcast(dim=1, size=2)",
                '    m.output("out", out)',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "eager_broadcast.pyc"
    run_cmd([sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    mlir = pyc.read_text(encoding="utf-8")
    check_ir(VecCase("eager_broadcast", "eager_broadcast", ir_tokens=("pyc.concat",)), mlir)
    assert "-> i32" in mlir
    cpp_dir = out_dir / "cpp"
    run_cmd(
        [str(pycc), str(pyc), "--emit=cpp", "--cpp-split=module", "--out-dir", str(cpp_dir), "--build-profile=dev-fast"],
        cwd=repo_root,
        env=env,
    )
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(out_dir / "eager_broadcast.v"), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    check_cpp_manifest_syntax(out_dir, repo_root=repo_root)


@pytest.mark.vec
def test_jit_instance_vec_port_emit_and_pycc(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    case_root = vec_test_root / "jit_instance_vec_port"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "jit_instance_vec_port.py"
    src.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pycircuit import Circuit, module",
                "",
                "",
                "@module",
                "def child(m: Circuit, a):",
                "    return a + a",
                "",
                "",
                "@module",
                "def build(m: Circuit) -> None:",
                '    a = m.vec([m.input(f"a{i}", width=4) for i in range(4)])',
                '    y = m.instance(child, name="u_child", a=a).read()',
                '    m.output("out", y)',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "jit_instance_vec_port.pyc"
    run_cmd([sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    mlir = pyc.read_text(encoding="utf-8")
    check_ir(
        VecCase("jit_instance_vec_port", "jit_instance_vec_port", ir_tokens=("pyc.instance", "pyc.concat", "pyc.extract")),
        mlir,
    )
    # The instance boundary carries one packed i16 bus per vector port.
    assert "%a: i16" in mlir
    cpp_dir = out_dir / "cpp"
    run_cmd(
        [str(pycc), str(pyc), "--emit=cpp", "--cpp-split=module", "--out-dir", str(cpp_dir), "--build-profile=dev-fast"],
        cwd=repo_root,
        env=env,
    )
    verilog = out_dir / "jit_instance_vec_port.v"
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(verilog), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    verilog_text = verilog.read_text(encoding="utf-8")
    assert "input [15:0] a" in verilog_text
    # The instance boundary hands over one packed bus (no per-lane bridge).
    assert "__flat" not in verilog_text
    assert ".a(pyc_comb_2)," in verilog_text
    assert "input [3:0] a [0:3]" not in verilog_text
    _run_yosys_smoke(verilog, top="JitInstanceVecPort", repo_root=repo_root)
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
                '    a = m.input("a", width=1, shape=[2, 3])',
                '    m.output("or0", a.reduce_or(dim=0))',
                '    m.output("or1", a.reduce_or(dim=1))',
                '    m.output("and0", a.reduce_and(dim=0))',
                '    m.output("and1", a.reduce_and(dim=1))',
                '    m.output("sum0", a.reduce_sum(dim=0))',
                '    m.output("sum1", a.reduce_sum(dim=1))',
                '    m.output("sum_all", a.reduce_sum())',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "dim_reduce.pyc"
    run_cmd([sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    mlir = pyc.read_text(encoding="utf-8")
    check_ir(
        VecCase("dim_reduce", "dim_reduce", ir_tokens=("pyc.or", "pyc.and", "pyc.add")),
        mlir,
    )
    assert "pyc.concat" in mlir
    assert "-> (i3, i2, i3, i2, i3, i2, i1)" in mlir
    cpp_dir = out_dir / "cpp"
    run_cmd(
        [str(pycc), str(pyc), "--emit=cpp", "--cpp-split=module", "--out-dir", str(cpp_dir), "--build-profile=dev-fast"],
        cwd=repo_root,
        env=env,
    )
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(out_dir / "dim_reduce.v"), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    check_cpp_manifest_syntax(out_dir, repo_root=repo_root)


@pytest.mark.vec
def test_reduce_mode_attr_and_pycc(
    *,
    repo_root: Path,
    vec_test_root: Path,
    pyc_pythonpath: str,
    pycc: Path,
) -> None:
    import sys

    frontend = repo_root / "compiler" / "frontend"
    if str(frontend) not in sys.path:
        sys.path.insert(0, str(frontend))

    from pycircuit import Circuit

    m = Circuit("reduce_mode_invalid")
    bad = m.vec([m.input(f"bad{i}", width=1) for i in range(2)])
    with pytest.raises(ValueError, match="reduce mode"):
        bad.reduce_or(mode="flat")
    with pytest.raises(TypeError, match="unexpected keyword argument 'width'"):
        bad.reduce_sum(width=2)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="unexpected keyword argument 'signed'"):
        bad.reduce_sum(signed=True)  # type: ignore[call-arg]

    case_root = vec_test_root / "reduce_modes"
    src_dir = case_root / "src"
    out_dir = case_root / "build"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "reduce_modes.py"
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
                '    a = m.input("a", width=1, shape=[4])',
                '    b = m.input("b", width=2, shape=[4])',
                '    m.output("chain_or", a.reduce_or())',
                '    m.output("tree_or", a.reduce_or(mode="tree"))',
                '    m.output("tree_sum", b.reduce_sum(mode="tree"))',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = merged_env(pythonpath=pyc_pythonpath, pycc=pycc)
    pyc = out_dir / "reduce_modes.pyc"
    run_cmd([sys.executable, "-m", "pycircuit.cli", "emit", str(src), "-o", str(pyc)], cwd=repo_root, env=env)
    mlir = pyc.read_text(encoding="utf-8")
    assert "vector<" not in mlir
    assert "pyc.v_" not in mlir
    # chain_or + tree_or (3 lane ORs each) and tree_sum (3 lane ADDs): the
    # reduce mode is a frontend choice realized in the scalar lane topology,
    # not an MLIR attribute.
    assert mlir.count("pyc.or ") == 6
    assert mlir.count("pyc.add ") == 3
    assert "mode = " not in mlir

    direct_verilog = out_dir / "reduce_modes.v"
    run_cmd([str(pycc), str(pyc), "--emit=verilog", "-o", str(direct_verilog), "--build-profile=dev-fast"], cwd=repo_root, env=env)
    direct_text = direct_verilog.read_text(encoding="utf-8")
    assert "chain_or" in direct_text
    assert "tree_or" in direct_text
    # Lane ops consume the packed-port extract view; chain reduces left to
    # right, tree reduces balanced pairs.
    assert "assign pyc_or_7 = (pyc_extract_3 | pyc_extract_4);" in direct_text
    assert "assign pyc_or_8 = (pyc_or_7 | pyc_extract_5);" in direct_text
    assert "assign pyc_or_9 = (pyc_or_8 | pyc_extract_6);" in direct_text
    assert "assign pyc_or_10 = (pyc_extract_5 | pyc_extract_6);" in direct_text
    assert "assign pyc_or_11 = (pyc_or_7 | pyc_or_10);" in direct_text
    assert "v_or_reduce" not in direct_text
    assert "v_add_reduce" not in direct_text
