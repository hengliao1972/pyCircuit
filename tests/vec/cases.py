from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .oracle import ashr, lshr, reduce_sum, sext, trunc, u, vector_binop, zext


Sample = dict[str, int | list[int]]
ExpectedFn = Callable[[Sample], int | list[int]]


@dataclass(frozen=True)
class VecCase:
    name: str
    kind: str
    width: int = 4
    lanes: int = 4
    signed: bool = False
    out_width: int | None = None
    samples: tuple[Sample, ...] = ()
    expected: ExpectedFn | None = None
    ir_tokens: tuple[str, ...] = ("vector<",)
    allow_scalarized: bool = False
    verilator: bool = True
    full_backend: bool = True

    @property
    def result_width(self) -> int:
        return int(self.out_width if self.out_width is not None else self.width)


BASE_A = [1, 2, 7, 12]
BASE_B = [3, 5, 2, 4]
SIGNED_A = [0b1111, 0b0010, 0b1100, 0b0111]  # -1, 2, -4, 7 in i4
SIGNED_B = [0b0011, 0b1110, 0b0010, 0b1111]  # 3, -2, 2, -1 in i4


@dataclass(frozen=True)
class BinarySpec:
    op: str
    ir_tokens: tuple[str, ...]
    samples_by_direction: dict[str, Sample]
    oracle_op: str | None = None
    directions: tuple[str, ...] = ("vv", "vs", "sv")
    signed: bool = False
    out_width: int | None = None
    verilator: bool = True


def _binary_operands(sample: Sample, direction: str) -> tuple[list[int], list[int]]:
    if direction == "vv":
        return sample["a"], sample["b"]  # type: ignore[return-value]
    if direction == "vs":
        return sample["a"], [int(sample["scalar"])] * 4  # type: ignore[return-value]
    if direction == "sv":
        return [int(sample["scalar"])] * 4, sample["a"]  # type: ignore[return-value]
    raise ValueError(f"unsupported binary direction: {direction}")


def _invert_bits(values: list[int]) -> list[int]:
    return [1 - int(v) for v in values]


def _binary_expected(spec: BinarySpec, direction: str) -> ExpectedFn:
    oracle_op = spec.oracle_op or spec.op

    def expected(sample: Sample) -> list[int]:
        lhs, rhs = _binary_operands(sample, direction)
        if oracle_op == "ne":
            return _invert_bits(vector_binop("eq", lhs, rhs, 4, signed=spec.signed))
        if oracle_op == "gt":
            return vector_binop("lt", rhs, lhs, 4, signed=spec.signed)
        if oracle_op == "le":
            return _invert_bits(vector_binop("lt", rhs, lhs, 4, signed=spec.signed))
        if oracle_op == "ge":
            return _invert_bits(vector_binop("lt", lhs, rhs, 4, signed=spec.signed))
        return vector_binop(oracle_op, lhs, rhs, 4, signed=spec.signed)

    return expected


def _select(sample: Sample) -> list[int]:
    cond = sample["cond"]  # type: ignore[assignment]
    a = sample["a"]  # type: ignore[assignment]
    b = sample["b"]  # type: ignore[assignment]
    return [u(av if cv else bv, 4) for cv, av, bv in zip(cond, a, b)]  # type: ignore[arg-type]


def _select_vs(sample: Sample) -> list[int]:
    cond = sample["cond"]  # type: ignore[assignment]
    a = sample["a"]  # type: ignore[assignment]
    scalar = int(sample["scalar"])
    return [u(av if cv else scalar, 4) for cv, av in zip(cond, a)]  # type: ignore[arg-type]


def _select_sv(sample: Sample) -> list[int]:
    cond = sample["cond"]  # type: ignore[assignment]
    a = sample["a"]  # type: ignore[assignment]
    scalar = int(sample["scalar"])
    return [u(scalar if cv else av, 4) for cv, av in zip(cond, a)]  # type: ignore[arg-type]


def _invert(sample: Sample) -> list[int]:
    return [u(~v, 4) for v in sample["a"]]  # type: ignore[index]


def _zext(sample: Sample) -> list[int]:
    return [zext(v, 4, 6) for v in sample["a"]]  # type: ignore[index]


def _sext(sample: Sample) -> list[int]:
    return [sext(v, 4, 6) for v in sample["a"]]  # type: ignore[index]


def _trunc(sample: Sample) -> list[int]:
    return [trunc(v, 3) for v in sample["a"]]  # type: ignore[index]


def _slice(sample: Sample) -> list[int]:
    return [(u(v, 4) >> 1) & 0b11 for v in sample["a"]]  # type: ignore[index]


def _shl(sample: Sample) -> list[int]:
    return [u(v << 1, 4) for v in sample["a"]]  # type: ignore[index]


def _lshr(sample: Sample) -> list[int]:
    return [lshr(v, 4, 1) for v in sample["a"]]  # type: ignore[index]


def _ashr(sample: Sample) -> list[int]:
    return [ashr(v, 4, 1) for v in sample["a"]]  # type: ignore[index]


def _or_reduce(sample: Sample) -> int:
    return 1 if any(int(v) & 1 for v in sample["a"]) else 0  # type: ignore[index]


def _and_reduce(sample: Sample) -> int:
    return 1 if all(int(v) & 1 for v in sample["a"]) else 0  # type: ignore[index]


def _sum(sample: Sample) -> int:
    return reduce_sum(sample["a"], 1, 3)  # type: ignore[arg-type]


def _signed_sum(sample: Sample) -> int:
    return reduce_sum(sample["a"], 4, 6, signed=True)  # type: ignore[arg-type]


def _samples(vv: Sample, vs: Sample, sv: Sample) -> dict[str, Sample]:
    return {"vv": vv, "vs": vs, "sv": sv}


DIV_A = [8, 9, 14, 15]
DIV_B = [2, 3, 4, 5]
SDIV_A = [0b1110, 0b0110, 0b1001, 0b0111]
SDIV_B = [0b0010, 0b1110, 0b0011, 0b1111]


BINARY_SPECS: tuple[BinarySpec, ...] = (
    BinarySpec("add", ("vector<", "pyc.add"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 3}, {"a": BASE_A, "scalar": 3})),
    BinarySpec("sub", ("vector<", "pyc.sub"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 9}, {"a": BASE_A, "scalar": 9})),
    BinarySpec("mul", ("vector<", "pyc.mul"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 3}, {"a": BASE_A, "scalar": 3})),
    BinarySpec("and", ("vector<", "pyc.and"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 10}, {"a": BASE_A, "scalar": 10})),
    BinarySpec("or", ("vector<", "pyc.or"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 8}, {"a": BASE_A, "scalar": 8})),
    BinarySpec("xor", ("vector<", "pyc.xor"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 15}, {"a": BASE_A, "scalar": 15})),
    BinarySpec("eq", ("vector<", "pyc.eq"), _samples({"a": BASE_A, "b": [1, 0, 7, 3]}, {"a": BASE_A, "scalar": 7}, {"a": BASE_A, "scalar": 7}), out_width=1),
    BinarySpec("ne", ("vector<", "pyc.eq", "pyc.not"), _samples({"a": BASE_A, "b": [1, 0, 7, 3]}, {"a": BASE_A, "scalar": 7}, {"a": BASE_A, "scalar": 7}), out_width=1),
    BinarySpec("lt", ("vector<", "pyc.ult"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 7}, {"a": BASE_A, "scalar": 7}), out_width=1),
    BinarySpec("gt", ("vector<", "pyc.ult"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 7}, {"a": BASE_A, "scalar": 7}), out_width=1),
    BinarySpec("le", ("vector<", "pyc.ult", "pyc.not"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 7}, {"a": BASE_A, "scalar": 7}), out_width=1),
    BinarySpec("ge", ("vector<", "pyc.ult", "pyc.not"), _samples({"a": BASE_A, "b": BASE_B}, {"a": BASE_A, "scalar": 7}, {"a": BASE_A, "scalar": 7}), out_width=1),
    BinarySpec("slt", ("vector<", "pyc.slt"), _samples({"a": SIGNED_A, "b": SIGNED_B}, {"a": SIGNED_A, "scalar": 0b1110}, {"a": SIGNED_A, "scalar": 0b1110}), oracle_op="lt", signed=True, out_width=1),
    BinarySpec("udiv", ("vector<", "pyc.udiv"), _samples({"a": DIV_A, "b": DIV_B}, {"a": DIV_A, "scalar": 3}, {"a": DIV_B, "scalar": 15}), oracle_op="div"),
    BinarySpec("urem", ("vector<", "pyc.urem"), _samples({"a": DIV_A, "b": DIV_B}, {"a": DIV_A, "scalar": 3}, {"a": DIV_B, "scalar": 15}), oracle_op="rem"),
    BinarySpec("sdiv", ("vector<", "pyc.sdiv"), _samples({"a": SDIV_A, "b": SDIV_B}, {"a": SDIV_A, "scalar": 0b1110}, {"a": SDIV_B, "scalar": 0b0110}), oracle_op="div", signed=True, verilator=False),
    BinarySpec("srem", ("vector<", "pyc.srem"), _samples({"a": SDIV_A, "b": SDIV_B}, {"a": SDIV_A, "scalar": 0b1110}, {"a": SDIV_B, "scalar": 0b0110}), oracle_op="rem", signed=True, verilator=False),
)


def _binary_cases() -> tuple[VecCase, ...]:
    cases: list[VecCase] = []
    for spec in BINARY_SPECS:
        for direction in spec.directions:
            cases.append(
                VecCase(
                    f"{spec.op}_{direction}",
                    f"{spec.op}_{direction}",
                    signed=spec.signed,
                    out_width=spec.out_width,
                    samples=(spec.samples_by_direction[direction],),
                    expected=_binary_expected(spec, direction),
                    ir_tokens=spec.ir_tokens,
                    verilator=spec.verilator,
                )
            )
    return tuple(cases)


VEC_CASES: tuple[VecCase, ...] = (
    *_binary_cases(),
    VecCase("invert", "invert", samples=({"a": BASE_A},), expected=_invert, ir_tokens=("vector<", "pyc.not")),
    VecCase("select_vv", "select_vv", samples=({"cond": [1, 0, 1, 0], "a": BASE_A, "b": BASE_B},), expected=_select, ir_tokens=("vector<", "pyc.mux"), full_backend=False),
    VecCase("select_vs", "select_vs", samples=({"cond": [1, 0, 1, 0], "a": BASE_A, "scalar": 9},), expected=_select_vs, ir_tokens=("vector<", "pyc.mux", "pyc.v_broadcast")),
    VecCase("select_sv", "select_sv", samples=({"cond": [1, 0, 1, 0], "a": BASE_A, "scalar": 9},), expected=_select_sv, ir_tokens=("vector<", "pyc.mux", "pyc.v_broadcast")),
    VecCase("zext", "zext", out_width=6, samples=({"a": SIGNED_A},), expected=_zext, ir_tokens=("vector<", "pyc.zext"), full_backend=False),
    VecCase("sext", "sext", signed=True, out_width=6, samples=({"a": SIGNED_A},), expected=_sext, ir_tokens=("vector<", "pyc.sext"), full_backend=False),
    VecCase("trunc", "trunc", out_width=3, samples=({"a": BASE_A},), expected=_trunc, ir_tokens=("vector<", "pyc.trunc"), full_backend=False),
    VecCase("slice", "slice", out_width=2, samples=({"a": BASE_A},), expected=_slice, ir_tokens=("pyc.extract",), allow_scalarized=True, full_backend=False),
    VecCase("shl_imm", "shl_imm", samples=({"a": BASE_A},), expected=_shl, ir_tokens=("vector<", "pyc.shli")),
    VecCase("lshr_imm", "lshr_imm", samples=({"a": BASE_A},), expected=_lshr, ir_tokens=("vector<", "pyc.lshri")),
    VecCase("ashr_imm", "ashr_imm", signed=True, samples=({"a": SIGNED_A},), expected=_ashr, ir_tokens=("vector<", "pyc.ashri")),
    VecCase("or_reduce", "or_reduce", width=1, out_width=1, samples=({"a": [0, 0, 1, 0]},), expected=_or_reduce, ir_tokens=("vector<", "pyc.v_or_reduce")),
    VecCase("and_reduce", "and_reduce", width=1, out_width=1, samples=({"a": [1, 1, 1, 1]},), expected=_and_reduce, ir_tokens=("vector<", "pyc.v_and_reduce")),
    VecCase("reduce_sum", "reduce_sum", width=1, out_width=3, samples=({"a": [1, 0, 1, 1]},), expected=_sum, ir_tokens=("vector<", "pyc.zext", "pyc.v_add_reduce")),
    VecCase("reduce_sum_signed", "reduce_sum_signed", signed=True, out_width=6, samples=({"a": SIGNED_A},), expected=_signed_sum, ir_tokens=("vector<", "pyc.sext", "pyc.v_add_reduce")),
)

FULL_BACKEND_CASES: tuple[VecCase, ...] = tuple(case for case in VEC_CASES if case.full_backend)
FRONTEND_ONLY_CASES: tuple[VecCase, ...] = tuple(case for case in VEC_CASES if not case.full_backend)
