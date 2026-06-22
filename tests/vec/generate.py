from __future__ import annotations

from pathlib import Path

from .cases import VecCase


BINARY_EXPR: dict[str, str] = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "and": "&",
    "or": "|",
    "xor": "^",
    "eq": "==",
    "ne": "!=",
    "lt": "<",
    "gt": ">",
    "le": "<=",
    "ge": ">=",
    "slt": "<",
    "udiv": "//",
    "sdiv": "//",
    "urem": "%",
    "srem": "%",
}


def _vec_input(name: str, width: int, lanes: int, *, signed: bool = False) -> str:
    signed_arg = ", signed=True" if signed else ""
    return f'{name} = Vec([m.input(f"{name}{{i}}", width={width}{signed_arg}) for i in range({lanes})])'


def _binary_case_expr(case: VecCase, *, width: int, lanes: int, signed: bool) -> tuple[list[str], str, bool] | None:
    try:
        op, direction = case.kind.rsplit("_", 1)
    except ValueError:
        return None
    if op not in BINARY_EXPR or direction not in {"vv", "vs", "sv"}:
        return None

    expr_op = BINARY_EXPR[op]
    if direction == "vv":
        lines = [_vec_input("a", width, lanes, signed=signed), _vec_input("b", width, lanes, signed=signed)]
        expr = f"a {expr_op} b"
    elif direction == "vs":
        lines = [_vec_input("a", width, lanes, signed=signed), f'scalar = m.input("scalar", width={width})']
        expr = f"a {expr_op} scalar"
    else:
        lines = [_vec_input("a", width, lanes, signed=signed), f'scalar = m.input("scalar", width={width})']
        expr = f"scalar {expr_op} a"
    return lines, expr, True


def _case_expr(case: VecCase) -> tuple[list[str], str, bool]:
    """Return setup lines, expression, and whether expression is vector-valued."""
    w = case.width
    n = case.lanes
    signed = case.signed

    binary = _binary_case_expr(case, width=w, lanes=n, signed=signed)
    if binary is not None:
        return binary

    if case.kind == "invert":
        return [_vec_input("a", w, n, signed=signed)], "~a", True

    if case.kind == "select_vv":
        return [
            _vec_input("cond", 1, n),
            _vec_input("a", w, n),
            _vec_input("b", w, n),
        ], "cond.select(a, b)", True

    if case.kind == "select_vs":
        return [
            _vec_input("cond", 1, n),
            _vec_input("a", w, n),
            f'scalar = m.input("scalar", width={w})',
        ], "cond.select(a, scalar)", True

    if case.kind == "select_sv":
        return [
            _vec_input("cond", 1, n),
            _vec_input("a", w, n),
            f'scalar = m.input("scalar", width={w})',
        ], "cond.select(scalar, a)", True

    if case.kind in {"zext", "sext", "trunc", "slice", "shl_imm", "lshr_imm", "ashr_imm"}:
        lines = [_vec_input("a", w, n, signed=signed)]
        exprs = {
            "zext": "a.zext(width=6)",
            "sext": "a.sext(width=6)",
            "trunc": "a.trunc(width=3)",
            "slice": "a.slice(lsb=1, width=2)",
            "shl_imm": "a << 1",
            "lshr_imm": "a >> 1",
            "ashr_imm": "a >> 1",
        }
        return lines, exprs[case.kind], True

    if case.kind in {"or_reduce", "and_reduce", "reduce_sum", "reduce_sum_signed"}:
        lines = [_vec_input("a", w, n, signed=signed)]
        exprs = {
            "or_reduce": "a.or_reduce()",
            "and_reduce": "a.and_reduce()",
            "reduce_sum": "a.reduce_sum()",
            "reduce_sum_signed": "a.reduce_sum(width=6, signed=True)",
        }
        return lines, exprs[case.kind], False

    raise ValueError(f"unsupported Vec test case kind: {case.kind}")


def render_case_source(case: VecCase) -> str:
    if not case.samples:
        raise ValueError(f"case {case.name} has no samples")
    if case.expected is None:
        raise ValueError(f"case {case.name} has no oracle")

    sample = case.samples[0]
    expected = case.expected(sample)
    setup_lines, expr, is_vector = _case_expr(case)
    lines: list[str] = [
        "from __future__ import annotations",
        "",
        "from pycircuit import Circuit, Tb, Vec, module, testbench",
        "",
        "",
        "@module",
        "def build(m: Circuit) -> None:",
    ]
    lines.extend(f"    {line}" for line in setup_lines)
    lines.append(f"    out = {expr}")
    if is_vector:
        lines.append(f"    for i in range({case.lanes}):")
        lines.append('        m.output(f"out{i}", out[i])')
    else:
        lines.append('    m.output("out", out)')
    lines.extend([
        "",
        "",
        "@testbench",
        "def tb(t: Tb) -> None:",
        "    t.timeout(1)",
    ])

    def drive_scalar(name: str, value: int) -> None:
        lines.append(f'    t.drive("{name}", {int(value)}, at=0)')

    for key, value in sample.items():
        if isinstance(value, list):
            for i, lane in enumerate(value):
                drive_scalar(f"{key}{i}", int(lane))
        else:
            drive_scalar(key, int(value))

    if isinstance(expected, list):
        for i, lane in enumerate(expected):
            lines.append(f'    t.expect("out{i}", {int(lane)}, at=0, msg="{case.name}.out{i}")')
    else:
        lines.append(f'    t.expect("out", {int(expected)}, at=0, msg="{case.name}.out")')
    lines.append("    t.finish(at=0)")
    lines.append("")
    return "\n".join(lines)


def write_case_source(case: VecCase, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    src = out_dir / f"tb_{case.name}.py"
    src.write_text(render_case_source(case), encoding="utf-8")
    return src


def render_vector_ir_source(case: VecCase) -> str:
    if case.kind not in {"or_reduce", "and_reduce", "reduce_sum", "reduce_sum_signed"}:
        raise ValueError(f"{case.name} does not have a vector-IR-only source")
    signed_arg = ", signed=True" if case.signed else ""
    exprs = {
        "or_reduce": "a.or_reduce()",
        "and_reduce": "a.and_reduce()",
        "reduce_sum": "a.reduce_sum()",
        "reduce_sum_signed": "a.reduce_sum(width=6, signed=True)",
    }
    return "\n".join(
        [
            "from __future__ import annotations",
            "",
            "from pycircuit import Circuit, module",
            "",
            "",
            "@module",
            "def build(m: Circuit) -> None:",
            f'    a = m.input("a", width={case.width}{signed_arg}, shape={case.lanes})',
            f"    out = {exprs[case.kind]}",
            '    m.output("out", out)',
            "",
        ]
    )


def write_vector_ir_source(case: VecCase, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    src = out_dir / f"vecir_{case.name}.py"
    src.write_text(render_vector_ir_source(case), encoding="utf-8")
    return src
