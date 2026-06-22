from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Callable

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _split_top_level_vector_parts(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(body):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "x" and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return [p.strip() for p in parts]


def _vector_shape_elem_type(ty: str) -> tuple[list[int], str]:
    raw = str(ty).strip()
    if not (raw.startswith("vector<") and raw.endswith(">")):
        raise ValueError(f"expected vector type, got {ty!r}")
    body = raw[len("vector<") : -1]
    parts = _split_top_level_vector_parts(body)
    if len(parts) < 2:
        raise ValueError(f"invalid vector type body: {body!r}")
    dims: list[int] = []
    for p in parts[:-1]:
        lanes = int(p)
        if lanes <= 0:
            raise ValueError(f"vector lanes must be > 0, got {lanes}")
        dims.append(lanes)
    return dims, parts[-1]


def _format_vector_type(shape: list[int], elem_ty: str) -> str:
    if not shape:
        return elem_ty
    return "vector<" + "x".join(str(d) for d in shape) + "x" + elem_ty + ">"


def _vector_elem_type(ty: str) -> tuple[int, str]:
    shape, elem_ty = _vector_shape_elem_type(ty)
    if len(shape) == 1:
        return shape[0], elem_ty
    return shape[0], _format_vector_type(shape[1:], elem_ty)


def _vector_map_to_i1(ty: str) -> str:
    shape, elem_ty = _vector_shape_elem_type(ty)
    if elem_ty.startswith("i"):
        return _format_vector_type(shape, "i1")
    raise ValueError(f"unsupported vector element type for compare: {elem_ty!r}")


def _leaf_int_type(ty: str) -> str:
    raw = str(ty).strip()
    if raw.startswith("vector<"):
        _shape, elem_ty = _vector_shape_elem_type(raw)
        if not elem_ty.startswith("i"):
            raise TypeError(f"expected vector-of-integer type, got {ty!r}")
        return elem_ty
    if raw.startswith("i"):
        return raw
    raise TypeError(f"expected integer or vector-of-integer type, got {ty!r}")


def _is_integer_or_vector_of_integer(ty: str) -> bool:
    try:
        _leaf_int_type(ty)
    except TypeError:
        return False
    return True


def _map_vector_leaf_type(ty: str, leaf_ty: str) -> str:
    raw = str(ty).strip()
    if raw.startswith("vector<"):
        shape, _old_leaf = _vector_shape_elem_type(raw)
        return _format_vector_type(shape, leaf_ty)
    _leaf_int_type(raw)
    return leaf_ty


def _elementwise_binary_result_type(a_ty: str, b_ty: str, op: str) -> str:
    a_leaf = _leaf_int_type(a_ty)
    b_leaf = _leaf_int_type(b_ty)
    if a_leaf != b_leaf:
        raise TypeError(f"{op} operand leaf types must match: {a_ty} vs {b_ty}")
    a_vec = a_ty.startswith("vector<")
    b_vec = b_ty.startswith("vector<")
    if a_vec and b_vec:
        a_shape, _ = _vector_shape_elem_type(a_ty)
        b_shape, _ = _vector_shape_elem_type(b_ty)
        if a_shape != b_shape:
            raise TypeError(f"{op} vector shapes must match: {a_ty} vs {b_ty}")
    if a_vec:
        return a_ty
    if b_vec:
        return b_ty
    return a_ty


def _elementwise_compare_result_type(a_ty: str, b_ty: str, op: str) -> str:
    value_ty = _elementwise_binary_result_type(a_ty, b_ty, op)
    if value_ty.startswith("vector<"):
        return _vector_map_to_i1(value_ty)
    return "i1"


@dataclass(frozen=True)
class Signal:
    ref: str
    ty: str

    def __str__(self) -> str:
        return self.ref


class Module:
    def __init__(self, name: str) -> None:
        self.name = name
        self._args: list[tuple[str, Signal]] = []
        self._results: list[tuple[str, Signal]] = []
        self._lines: list[str] = []
        self._next_tmp = 0
        self._indent_level = 1
        self._finalizers: list[Callable[[], None]] = []
        self._finalized = False
        # Extra `func.func` attributes emitted by `emit_func_mlir()`.
        # Values are stored as MLIR attribute literals (e.g. `"foo"`).
        self._func_attrs: dict[str, str] = {}
        # Vector lane provenance for frontend peephole optimizations.
        # Maps lane Signal.ref -> (source vector ref, lane index).
        self._vec_get_map: dict[str, tuple[str, int]] = {}

    def _set_func_attr_impl(self, key: str, value_literal: str) -> None:
        if self._finalized:
            raise RuntimeError("cannot set func attributes after emit_mlir()")
        k = str(key).strip()
        if not k:
            raise ValueError("func attribute key must be non-empty")
        v = str(value_literal).strip()
        if not v:
            raise ValueError("func attribute literal must be non-empty")
        self._func_attrs[k] = v

    def set_func_attr(self, key: str, value: str) -> None:
        """Set a `func.func` string attribute.

        This is intended for attaching debug/metadata attributes such as:
        - `pyc.base = "Core"`
        - `pyc.params = "{\"WIDTH\":32}"`
        """
        # MLIR string attributes use double quotes; reuse JSON escaping.
        self._set_func_attr_impl(key, json.dumps(str(value), ensure_ascii=False))

    def set_func_attr_literal(self, key: str, value_literal: str) -> None:
        """Set a `func.func` attribute using a raw MLIR attribute literal."""
        self._set_func_attr_impl(key, value_literal)

    def set_func_attr_json(self, key: str, value: object) -> None:
        """Set a `func.func` attribute using JSON-compatible MLIR literal syntax."""
        self._set_func_attr_impl(key, json.dumps(value, ensure_ascii=False))

    # --- types ---
    def clock(self, name: str) -> Signal:
        return self._arg(name, "!pyc.clock")

    def reset(self, name: str) -> Signal:
        return self._arg(name, "!pyc.reset")

    def reset_active(self, rst: Signal) -> Signal:
        """Return i1 where **1** means reset is asserted (same convention as ``Tb.reset`` / SV TB)."""
        if rst.ty != "!pyc.reset":
            raise TypeError("reset_active expects a !pyc.reset signal (use m.reset(...))")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.reset_active {rst.ref} : i1")
        return Signal(ref=tmp, ty="i1")

    def i(self, width: int) -> str:
        if width <= 0:
            raise ValueError("width must be > 0")
        return f"i{int(width)}"

    def input(
        self,
        name: str,
        *,
        shape: list[int] | None = None,
        width: int | None = None,
    ) -> Signal:
        if width is None:
            raise TypeError("input() requires width=...")
        width_i = int(width)
        if width_i <= 0:
            raise ValueError("width must be > 0")

        dims = list(shape or [])
        if not all(isinstance(d, int) and d > 0 for d in dims):
            raise ValueError("shape entries must be all int and all > 0")

        if not dims:
            ty = f"i{width_i}"
        else:
            lanes = "x".join(str(d) for d in dims)
            ty = f"vector<{lanes}xi{width_i}>"
        return self._arg(name, ty)

    def output(self, name: str, value: Signal) -> None:
        self._results.append((name, value))

    # --- builders ---
    def const(self, value: int, *, width: int) -> Signal:
        ty = self.i(width)
        if width <= 0:
            raise ValueError("width must be > 0")
        # Represent negative literals in two's complement at the requested width.
        value = int(value) & ((1 << int(width)) - 1)
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.constant {value} : {ty}")
        return Signal(ref=tmp, ty=ty)

    def _emit_elementwise_binary(self, op: str, a: Signal, b: Signal, *, compare: bool = False) -> Signal:
        tmp = self._tmp()
        result_fn = _elementwise_compare_result_type if compare else _elementwise_binary_result_type
        out_ty = result_fn(a.ty, b.ty, op)
        self._emit(f"{tmp} = pyc.{op} {a.ref}, {b.ref} : {a.ty}, {b.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def add(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("add", a, b)

    def sub(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("sub", a, b)

    def mul(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("mul", a, b)

    def udiv(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("udiv", a, b)

    def urem(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("urem", a, b)

    def sdiv(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("sdiv", a, b)

    def srem(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("srem", a, b)

    def mux(self, sel: Signal, a: Signal, b: Signal) -> Signal:
        # Allow one vector + one scalar arm (scalar implicitly broadcasts).
        a_is_vec = a.ty.startswith("vector<")
        b_is_vec = b.ty.startswith("vector<")
        if a_is_vec and b_is_vec:
            self._require_same_ty(a, b, "mux")
        elif a_is_vec and not b_is_vec:
            _av, _ae = _vector_shape_elem_type(a.ty)
            if _ae != b.ty:
                raise TypeError(f"mux scalar arm width must match vector element: {b.ty} vs {_ae}")
        elif b_is_vec and not a_is_vec:
            _bv, _be = _vector_shape_elem_type(b.ty)
            if _be != a.ty:
                raise TypeError(f"mux scalar arm width must match vector element: {a.ty} vs {_be}")
        else:
            self._require_same_ty(a, b, "mux")
        if sel.ty == "i1":
            pass
        elif sel.ty.startswith("vector<") and (a_is_vec or b_is_vec):
            sel_shape, sel_elem_ty = _vector_shape_elem_type(sel.ty)
            vec_ty = a.ty if a_is_vec else b.ty
            vec_shape, _vec_elem_ty = _vector_shape_elem_type(vec_ty)
            if sel_elem_ty != "i1" or sel_shape != vec_shape:
                raise TypeError(f"mux vector sel must be vector<...xi1> with a/b shape: {sel.ty} vs {vec_ty}")
        else:
            raise TypeError("mux sel must be i1 or same-shape vector<...xi1>")
        result_ty = a.ty if a_is_vec else b.ty
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.mux {sel.ref}, {a.ref}, {b.ref} : {sel.ty}, {a.ty}, {b.ty} -> {result_ty}")
        return Signal(ref=tmp, ty=result_ty)

    def and_(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("and", a, b)

    def or_(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("or", a, b)

    def xor(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("xor", a, b)

    def not_(self, a: Signal) -> Signal:
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.not {a.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def eq(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("eq", a, b, compare=True)

    def ult(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("ult", a, b, compare=True)

    def slt(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("slt", a, b, compare=True)

    def trunc(self, a: Signal, *, width: int) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty):
            raise TypeError("trunc requires an integer or vector-of-integer input")
        out_ty = _map_vector_leaf_type(a.ty, self.i(width))
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.trunc {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def zext(self, a: Signal, *, width: int) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty):
            raise TypeError("zext requires an integer or vector-of-integer input")
        out_ty = _map_vector_leaf_type(a.ty, self.i(width))
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.zext {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def sext(self, a: Signal, *, width: int) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty):
            raise TypeError("sext requires an integer or vector-of-integer input")
        out_ty = _map_vector_leaf_type(a.ty, self.i(width))
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.sext {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def extract(self, a: Signal, *, lsb: int, width: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("extract requires an integer input")
        if lsb < 0:
            raise ValueError("extract lsb must be >= 0")
        out_ty = self.i(width)
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.extract {a.ref} {{lsb = {int(lsb)}}} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def shli(self, a: Signal, *, amount: int) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty):
            raise TypeError("shli requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("shli amount must be >= 0")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.shli {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def lshri(self, a: Signal, *, amount: int) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty):
            raise TypeError("lshri requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("lshri amount must be >= 0")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.lshri {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def ashri(self, a: Signal, *, amount: int) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty):
            raise TypeError("ashri requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("ashri amount must be >= 0")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.ashri {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def shl(self, a: Signal, amount: Signal) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty) or not amount.ty.startswith("i"):
            raise TypeError("shl requires integer or vector-of-integer input and scalar integer amount")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.shl {a.ref}, {amount.ref} : {a.ty}, {amount.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def lshr(self, a: Signal, amount: Signal) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty) or not amount.ty.startswith("i"):
            raise TypeError("lshr requires integer or vector-of-integer input and scalar integer amount")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.lshr {a.ref}, {amount.ref} : {a.ty}, {amount.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def ashr(self, a: Signal, amount: Signal) -> Signal:
        if not _is_integer_or_vector_of_integer(a.ty) or not amount.ty.startswith("i"):
            raise TypeError("ashr requires integer or vector-of-integer input and scalar integer amount")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.ashr {a.ref}, {amount.ref} : {a.ty}, {amount.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def concat(self, *inputs: Signal) -> Signal:
        """Concatenate integer signals into a packed bus (MSB-first)."""
        if not inputs:
            raise ValueError("concat requires at least one input")

        def w(ty: str) -> int:
            if not ty.startswith("i"):
                raise TypeError("concat only supports integer types")
            try:
                return int(ty[1:])
            except ValueError as e:
                raise TypeError(f"invalid integer type: {ty!r}") from e

        out_w = sum(w(s.ty) for s in inputs)
        out_ty = self.i(out_w)
        tmp = self._tmp()
        op_list = ", ".join(s.ref for s in inputs)
        ty_list = ", ".join(s.ty for s in inputs)
        self._emit(f"{tmp} = pyc.concat ({op_list}) : ({ty_list}) -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_create(self, elements: list[Signal]) -> Signal:
        if not elements:
            raise ValueError("v_create requires at least one element")
        elem_ty = str(elements[0].ty)
        for e in elements[1:]:
            if str(e.ty) != elem_ty:
                raise TypeError(f"v_create requires same element type, got {elem_ty} vs {e.ty}")
        if elem_ty.startswith("vector<"):
            shape, leaf_ty = _vector_shape_elem_type(elem_ty)
            out_ty = _format_vector_type([len(elements), *shape], leaf_ty)
        else:
            out_ty = f"vector<{len(elements)}x{elem_ty}>"
        tmp = self._tmp()
        op_list = ", ".join(s.ref for s in elements)
        ty_list = ", ".join(s.ty for s in elements)
        self._emit(f"{tmp} = pyc.v_create ({op_list}) : ({ty_list}) -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_broadcast(self, scalar: Signal, *, size: int) -> Signal:
        lanes = int(size)
        if lanes <= 0:
            raise ValueError("v_broadcast size must be > 0")
        out_ty = f"vector<{lanes}x{scalar.ty}>"
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.v_broadcast {scalar.ref} to {lanes} : {scalar.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_get(self, vec: Signal, *, index: int) -> Signal:
        lanes, elem_ty = _vector_elem_type(vec.ty)
        idx = int(index)
        if idx < 0 or idx >= lanes:
            raise ValueError(f"v_get index out of range: {idx} for {vec.ty}")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.v_get {vec.ref} [{idx}] : {vec.ty} -> {elem_ty}")
        self._vec_get_map[tmp] = (str(vec.ref), idx)
        return Signal(ref=tmp, ty=elem_ty)

    def _v_reduce(self, op: str, vec: Signal, *, dim: int | None = None) -> Signal:
        shape, elem_ty = _vector_shape_elem_type(vec.ty)
        reduce_dim = 0 if dim is None else int(dim)
        if reduce_dim < 0 or reduce_dim >= len(shape):
            raise ValueError(f"{op} dim out of range: {reduce_dim} for {vec.ty}")
        out_shape = [d for i, d in enumerate(shape) if i != reduce_dim]
        out_ty = _format_vector_type(out_shape, elem_ty)
        tmp = self._tmp()
        attrs = f" {{dim = {reduce_dim}}}" if dim is not None else ""
        self._emit(f"{tmp} = pyc.{op} {vec.ref}{attrs} : {vec.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_or_reduce(self, vec: Signal, *, dim: int | None = None) -> Signal:
        return self._v_reduce("v_or_reduce", vec, dim=dim)

    def v_and_reduce(self, vec: Signal, *, dim: int | None = None) -> Signal:
        return self._v_reduce("v_and_reduce", vec, dim=dim)

    def v_add_reduce(self, vec: Signal, *, dim: int | None = None) -> Signal:
        return self._v_reduce("v_add_reduce", vec, dim=dim)

    def instance_op(
        self,
        callee: str,
        *inputs: Signal,
        result_types: list[str],
        name: str | None = None,
        short_name: str | None = None,
        keep: bool = False,
    ) -> list[Signal]:
        """Instantiate a sub-module by symbol (pyc.instance).

        `callee` is the referenced `func.func` symbol name.
        """
        callee = str(callee).strip()
        if not callee:
            raise ValueError("instance_op callee must be non-empty")

        out: list[Signal] = []
        for ty in result_types:
            tmp = self._tmp()
            out.append(Signal(ref=tmp, ty=str(ty)))

        lhs = ""
        if out:
            if len(out) == 1:
                lhs = f"{out[0].ref} = "
            else:
                lhs = f"{', '.join(s.ref for s in out)} = "

        ops = ", ".join(s.ref for s in inputs)
        attrs = f"{{callee = @{callee}"
        if name is not None:
            attrs += f', name = {json.dumps(str(name), ensure_ascii=False)}'
        if short_name is not None:
            attrs += f', short_name = {json.dumps(str(short_name), ensure_ascii=False)}'
        if keep:
            attrs += ", pyc.debug_keep = true"
        attrs += "}"

        in_ty_sig = ", ".join(s.ty for s in inputs)
        in_sig = f"({in_ty_sig})"
        if len(out) == 0:
            out_sig = "()"
        elif len(out) == 1:
            out_sig = out[0].ty
        else:
            out_ty_sig = ", ".join(s.ty for s in out)
            out_sig = f"({out_ty_sig})"

        if ops:
            self._emit(f"{lhs}pyc.instance {ops} {attrs} : {in_sig} -> {out_sig}")
        else:
            self._emit(f"{lhs}pyc.instance {attrs} : {in_sig} -> {out_sig}")
        return out

    def alias(self, a: Signal, *, name: str | None = None) -> Signal:
        """Alias a value (pure) to attach a debug name in codegen."""
        tmp = self._tmp()
        if name is None:
            self._emit(f"{tmp} = pyc.alias {a.ref} : {a.ty}")
        else:
            self._emit(f'{tmp} = pyc.alias {a.ref} {{pyc.name = "{name}"}} : {a.ty}')
        return Signal(ref=tmp, ty=a.ty)

    def new_wire(self, *, width: int, name: str | None = None) -> Signal:
        ty = self.i(width)
        tmp = self._tmp()
        if name is None:
            self._emit(f"{tmp} = pyc.wire : {ty}")
        else:
            self._emit(f'{tmp} = pyc.wire {{pyc.name = "{name}"}} : {ty}')
        return Signal(ref=tmp, ty=ty)

    def assign(self, dst: Signal, src: Signal) -> None:
        self._require_same_ty(dst, src, "assign")
        self._emit(f"pyc.assign {dst.ref}, {src.ref} : {dst.ty}")

    def assert_(self, cond: Signal, *, msg: str | None = None) -> None:
        """Simulation-only assertion (prototype)."""
        if cond.ty != "i1":
            raise TypeError("assert_ cond must be i1")
        if msg is None:
            self._emit(f"pyc.assert {cond.ref}")
            return
        s = str(msg)
        if not s:
            self._emit(f"pyc.assert {cond.ref}")
            return
        self._emit(f"pyc.assert {cond.ref} {{msg = {json.dumps(s, ensure_ascii=False)}}}")

    def reg(self, clk: Signal, rst: Signal, en: Signal, next_: Signal, init: Signal) -> Signal:
        if clk.ty != "!pyc.clock":
            raise TypeError("reg clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("reg rst must be !pyc.reset")
        if en.ty != "i1":
            raise TypeError("reg en must be i1")
        self._require_same_ty(next_, init, "reg")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.reg {clk.ref}, {rst.ref}, {en.ref}, {next_.ref}, {init.ref} : {next_.ty}")
        return Signal(ref=tmp, ty=next_.ty)

    def fifo(
        self,
        clk: Signal,
        rst: Signal,
        in_valid: Signal,
        in_data: Signal,
        out_ready: Signal,
        *,
        depth: int,
    ) -> tuple[Signal, Signal, Signal]:
        if clk.ty != "!pyc.clock":
            raise TypeError("fifo clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("fifo rst must be !pyc.reset")
        if in_valid.ty != "i1":
            raise TypeError("fifo in_valid must be i1")
        if out_ready.ty != "i1":
            raise TypeError("fifo out_ready must be i1")
        if depth <= 0:
            raise ValueError("fifo depth must be > 0")
        in_ready = self._tmp()
        out_valid = self._tmp()
        out_data = self._tmp()
        self._emit(
            f"{in_ready}, {out_valid}, {out_data} = pyc.fifo {clk.ref}, {rst.ref}, {in_valid.ref}, {in_data.ref}, {out_ready.ref} "
            + f'{{depth = {int(depth)}}} : {in_data.ty}'
        )
        return Signal(in_ready, "i1"), Signal(out_valid, "i1"), Signal(out_data, in_data.ty)

    def byte_mem(
        self,
        clk: Signal,
        rst: Signal,
        raddr: Signal,
        wvalid: Signal,
        waddr: Signal,
        wdata: Signal,
        wstrb: Signal,
        *,
        depth: int,
        name: str,
    ) -> Signal:
        """Byte-addressed memory (async read + sync write, prototype)."""
        if clk.ty != "!pyc.clock":
            raise TypeError("byte_mem clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("byte_mem rst must be !pyc.reset")
        if wvalid.ty != "i1":
            raise TypeError("byte_mem wvalid must be i1")
        if raddr.ty != waddr.ty:
            raise TypeError("byte_mem raddr/waddr must have the same type")
        if wdata.ty != "i64" and not wdata.ty.startswith("i"):
            raise TypeError("byte_mem wdata must be an integer type")
        if wstrb.ty != "i8" and not wstrb.ty.startswith("i"):
            raise TypeError("byte_mem wstrb must be an integer type")
        if depth <= 0:
            raise ValueError("byte_mem depth must be > 0")
        if not isinstance(name, str) or not name.strip() or not _IDENT_RE.match(name):
            raise ValueError("byte_mem name must match [A-Za-z_][A-Za-z0-9_]* (Decision 0025)")

        tmp = self._tmp()
        attrs = f'{{depth = {int(depth)}, name = "{name}"}}'
        self._emit(
            f"{tmp} = pyc.byte_mem {clk.ref}, {rst.ref}, {raddr.ref}, {wvalid.ref}, {waddr.ref}, {wdata.ref}, {wstrb.ref} "
            + f"{attrs} : {raddr.ty}, {wdata.ty}, {wstrb.ty}"
        )
        return Signal(ref=tmp, ty=wdata.ty)

    def sync_mem(
        self,
        clk: Signal,
        rst: Signal,
        ren: Signal,
        raddr: Signal,
        wvalid: Signal,
        waddr: Signal,
        wdata: Signal,
        wstrb: Signal,
        *,
        depth: int,
        name: str,
    ) -> Signal:
        """Synchronous 1R1W memory (registered read data, prototype)."""
        if clk.ty != "!pyc.clock":
            raise TypeError("sync_mem clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("sync_mem rst must be !pyc.reset")
        if ren.ty != "i1":
            raise TypeError("sync_mem ren must be i1")
        if wvalid.ty != "i1":
            raise TypeError("sync_mem wvalid must be i1")
        if raddr.ty != waddr.ty:
            raise TypeError("sync_mem raddr/waddr must have the same type")
        if depth <= 0:
            raise ValueError("sync_mem depth must be > 0")
        if not isinstance(name, str) or not name.strip() or not _IDENT_RE.match(name):
            raise ValueError("sync_mem name must match [A-Za-z_][A-Za-z0-9_]* (Decision 0025)")

        tmp = self._tmp()
        attrs = f'{{depth = {int(depth)}, name = "{name}"}}'
        self._emit(
            f"{tmp} = pyc.sync_mem {clk.ref}, {rst.ref}, {ren.ref}, {raddr.ref}, {wvalid.ref}, {waddr.ref}, {wdata.ref}, {wstrb.ref} "
            + f"{attrs} : {raddr.ty}, {wdata.ty}, {wstrb.ty}"
        )
        return Signal(ref=tmp, ty=wdata.ty)

    def sync_mem_dp(
        self,
        clk: Signal,
        rst: Signal,
        ren0: Signal,
        raddr0: Signal,
        ren1: Signal,
        raddr1: Signal,
        wvalid: Signal,
        waddr: Signal,
        wdata: Signal,
        wstrb: Signal,
        *,
        depth: int,
        name: str,
    ) -> tuple[Signal, Signal]:
        """Synchronous 2R1W memory (registered outputs, prototype)."""
        if clk.ty != "!pyc.clock":
            raise TypeError("sync_mem_dp clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("sync_mem_dp rst must be !pyc.reset")
        if ren0.ty != "i1" or ren1.ty != "i1":
            raise TypeError("sync_mem_dp ren0/ren1 must be i1")
        if wvalid.ty != "i1":
            raise TypeError("sync_mem_dp wvalid must be i1")
        if raddr0.ty != raddr1.ty or raddr0.ty != waddr.ty:
            raise TypeError("sync_mem_dp raddr0/raddr1/waddr must have the same type")
        if depth <= 0:
            raise ValueError("sync_mem_dp depth must be > 0")
        if not isinstance(name, str) or not name.strip() or not _IDENT_RE.match(name):
            raise ValueError("sync_mem_dp name must match [A-Za-z_][A-Za-z0-9_]* (Decision 0025)")

        out0 = self._tmp()
        out1 = self._tmp()
        attrs = f'{{depth = {int(depth)}, name = "{name}"}}'
        self._emit(
            f"{out0}, {out1} = pyc.sync_mem_dp {clk.ref}, {rst.ref}, {ren0.ref}, {raddr0.ref}, {ren1.ref}, {raddr1.ref}, "
            + f"{wvalid.ref}, {waddr.ref}, {wdata.ref}, {wstrb.ref} {attrs} : {raddr0.ty}, {wdata.ty}, {wstrb.ty}"
        )
        return Signal(ref=out0, ty=wdata.ty), Signal(ref=out1, ty=wdata.ty)

    def async_fifo(
        self,
        in_clk: Signal,
        in_rst: Signal,
        out_clk: Signal,
        out_rst: Signal,
        in_valid: Signal,
        in_data: Signal,
        out_ready: Signal,
        *,
        depth: int,
    ) -> tuple[Signal, Signal, Signal]:
        if in_clk.ty != "!pyc.clock" or out_clk.ty != "!pyc.clock":
            raise TypeError("async_fifo clk must be !pyc.clock")
        if in_rst.ty != "!pyc.reset" or out_rst.ty != "!pyc.reset":
            raise TypeError("async_fifo rst must be !pyc.reset")
        if in_valid.ty != "i1":
            raise TypeError("async_fifo in_valid must be i1")
        if out_ready.ty != "i1":
            raise TypeError("async_fifo out_ready must be i1")
        if depth <= 0:
            raise ValueError("async_fifo depth must be > 0")
        in_ready = self._tmp()
        out_valid = self._tmp()
        out_data = self._tmp()
        self._emit(
            f"{in_ready}, {out_valid}, {out_data} = pyc.async_fifo {in_clk.ref}, {in_rst.ref}, {out_clk.ref}, {out_rst.ref}, "
            + f"{in_valid.ref}, {in_data.ref}, {out_ready.ref} {{depth = {int(depth)}}} : {in_data.ty}"
        )
        return Signal(in_ready, "i1"), Signal(out_valid, "i1"), Signal(out_data, in_data.ty)

    def cdc_sync(self, clk: Signal, rst: Signal, a: Signal, *, stages: int | None = None) -> Signal:
        if clk.ty != "!pyc.clock":
            raise TypeError("cdc_sync clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("cdc_sync rst must be !pyc.reset")
        tmp = self._tmp()
        if stages is None:
            self._emit(f"{tmp} = pyc.cdc_sync {clk.ref}, {rst.ref}, {a.ref} : {a.ty}")
        else:
            self._emit(f"{tmp} = pyc.cdc_sync {clk.ref}, {rst.ref}, {a.ref} {{stages = {int(stages)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    # --- structured emission helpers (for AST/JIT frontends) ---
    def emit_line(self, line: str) -> None:
        """Emit a raw line at the current indentation level (inside func body)."""
        self._emit(line)

    def push_indent(self) -> None:
        self._indent_level += 1

    def pop_indent(self) -> None:
        if self._indent_level <= 1:
            raise RuntimeError("indent underflow")
        self._indent_level -= 1

    def index_const(self, value: int) -> Signal:
        tmp = self._tmp()
        self._emit(f"{tmp} = arith.constant {int(value)} : index")
        return Signal(ref=tmp, ty="index")

    # --- emission ---
    def emit_func_mlir(self) -> str:
        if not self._finalized:
            self._finalized = True
            for fn in list(self._finalizers):
                fn()

        arg_sig = ", ".join(f"{sig.ref}: {sig.ty}" for _, sig in self._args)
        res_types = [v.ty for _, v in self._results]
        if len(res_types) == 0:
            res_sig = "-> ()"
            ret_ty = ""
        elif len(res_types) == 1:
            res_sig = f"-> {res_types[0]}"
            ret_ty = res_types[0]
        else:
            res_sig = f"-> ({', '.join(res_types)})"
            ret_ty = ", ".join(res_types)
        in_names = ", ".join(f"\"{n}\"" for n, _ in self._args)
        out_names = ", ".join(f"\"{n}\"" for n, _ in self._results)
        extra = ""
        if self._func_attrs:
            extra = ", " + ", ".join(f"{k} = {v}" for k, v in self._func_attrs.items())
        header = (
            f"func.func @{self.name}({arg_sig}) {res_sig} "
            f"attributes {{arg_names = [{in_names}], result_names = [{out_names}]{extra}}} {{\n"
        )
        body = "\n".join(self._lines)
        outs = ", ".join(v.ref for _, v in self._results)
        if outs:
            tail = f"\n  func.return {outs} : {ret_ty}\n}}\n"
        else:
            tail = "\n  func.return\n}\n"
        return header + body + tail

    def emit_mlir(self) -> str:
        return "module {\n" + self.emit_func_mlir() + "}\n"

    # --- finalizers ---
    def add_finalizer(self, fn: Callable[[], None]) -> None:
        if self._finalized:
            raise RuntimeError("cannot add finalizers after emit_mlir()")
        self._finalizers.append(fn)

    # --- internals ---
    def _arg(self, name: str, ty: str) -> Signal:
        ref = f"%{name}"
        s = Signal(ref=ref, ty=ty)
        self._args.append((name, s))
        return s

    def _tmp(self) -> str:
        self._next_tmp += 1
        return f"%v{self._next_tmp}"

    def _emit(self, line: str) -> None:
        self._lines.append(("  " * self._indent_level) + line)

    @staticmethod
    def _require_same_ty(a: Signal, b: Signal, op: str) -> None:
        if a.ty != b.ty:
            raise TypeError(f"{op} requires same types, got {a.ty} and {b.ty}")
