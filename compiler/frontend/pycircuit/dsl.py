from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeGuard, Union, overload

from .connectors import Connector
from .data import Bits, Clock, DT, Data, Reset, Vector

if TYPE_CHECKING:
    from .hw import Module, Reg, Wire

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Signal(Generic[DT]):
    ref: str
    ty: DT

    def __post_init__(self) -> None:
        if not isinstance(self.ty, (Bits, Vector, Clock, Reset)):
            raise TypeError(f"Signal.ty must be Bits/Vector/Clock/Reset, got {type(self.ty).__name__}")

    def __str__(self) -> str:
        return self.ref
    
    @property
    def width(self) -> int:
        return self.ty.width
    
    @classmethod
    def as_sig(cls, v: Union[Connector, Wire, Reg, Signal]) -> Signal:
        from .hw import Reg, Wire
        if isinstance(v, Connector):
            v = v.read().sig
        if isinstance(v, Reg):
            v = v.q.sig
        if isinstance(v, Wire):
            return v.sig
        if isinstance(v, Signal):
            return v
        raise TypeError(f"cannot convert {type(v).__name__} to Signal")

def is_bits_signal(signal: Signal[Data]) -> TypeGuard[Signal[Bits]]:
    """Return whether ``signal`` carries a scalar ``Bits`` type."""
    return isinstance(signal.ty, Bits)


def is_vector_signal(signal: Signal[Data]) -> TypeGuard[Signal[Vector[Data]]]:
    """Return whether ``signal`` carries a ``Vector`` type."""
    return isinstance(signal.ty, Vector)


class Module:
    def __init__(self, name: str) -> None:
        self.name = name
        self._args: list[tuple[str, Signal]] = []
        self._results: list[tuple[str, Signal]] = []
        self._lines: list[str] = []
        self._temp_var_index = 0
        self._indent_level = 1
        # finalize callbacks to run after emit_mlir() but before returning the final MLIR string.
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
        return self._arg(name, Clock())

    def reset(self, name: str) -> Signal:
        return self._arg(name, Reset())

    def reset_active(self, rst: Signal) -> Signal:
        """Return i1 where **1** means reset is asserted (same convention as ``Tb.reset`` / SV TB)."""
        if not isinstance(rst.ty, Reset):
            raise TypeError("reset_active expects a !pyc.reset signal (use m.reset(...))")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.reset_active {rst.ref} : i1")
        return Signal(ref=tmp, ty=Bits(1))

    @overload
    def input(self, name:str, *, width: int, shape: None = None) -> Signal[Bits]: ...
    
    @overload
    def input(self, name:str, *, width: int, shape: list[int]) -> Signal[Vector[Data]]: ...     

    def input(
        self,
        name: str,
        *,
        width: int,
        shape: list[int] | None = None,
    ) -> Signal:
        shape = [] if shape is None else list(shape)
        if width <= 0:
            raise ValueError("width must be > 0")
        if not all(isinstance(d, int) and d > 0 for d in shape):
            raise ValueError("shape entries must be all int and all > 0")

        if not shape:
            ty = Bits(width)
        else:
            ty = Vector.from_shape(shape, Bits(width))
            
        return self._arg(name, ty)

    def output(self, name: str, value: Signal) -> None:
        self._results.append((name, value))

    # --- builders ---
    @overload
    def const(self, value: int, *, width: int) -> Signal[Bits]: ...
    @overload
    def const(self, value: list[int], *, width: int) -> Signal[Vector[Bits]]: ...

    @overload
    def const(self, value: list[list[int]], *, width: int) -> Signal[Vector[Vector[Bits]]]: ...

    @overload
    def const(self, value: list[Any], *, width: int) -> Signal[Vector[Vector[Vector[Data]]]]: ...

    def const(
        self,
        value: int | list[Any],
        *,
        width: int,
    ) -> Signal[Bits | Vector[Any]]:
        """Emit ``pyc.constant`` (scalar) or nested ``pyc.v_create`` (list)."""
        if width <= 0:
            raise ValueError("width must be > 0")
        ty = Bits(width)

        def reduce_const(v: int | list) -> Signal:
            if isinstance(v, int):
                # Two's complement at the requested width.
                imm = int(v) & ((1 << int(width)) - 1)
                tmp = self._get_next_temp_var()
                self._emit(f"{tmp} = pyc.constant {imm} : {ty}")
                return Signal(ref=tmp, ty=ty)
            if isinstance(v, list):
                if not v:
                    raise ValueError("const() list value must be non-empty")
                elems = [reduce_const(e) for e in v]
                return self.v_create(elems)
            raise TypeError(f"const() value must be int or list, got {type(v).__name__}")

        return reduce_const(value)

    def _emit_elementwise_binary(self, op: str, a: Signal, b: Signal, *, compare: bool = False) -> Signal:
        a_ty, b_ty = a.ty, b.ty
        a_is_vec = isinstance(a_ty, Vector)
        b_is_vec = isinstance(b_ty, Vector)
        any_is_vec = a_is_vec or b_is_vec
        a_leaf = a_ty.datatype() if a_is_vec else a_ty
        b_leaf = b_ty.datatype() if b_is_vec else b_ty
        # Signedness is a frontend intent bit; MLIR leaf types are width-only (`iN`).
        if any_is_vec and str(a_leaf) != str(b_leaf):
            raise TypeError(f"{op} operand leaf types must match: {a_ty} vs {b_ty}")
        if a_is_vec and b_is_vec and a_ty.shape() != b_ty.shape():
            raise TypeError(f"{op} vector shapes must match: {a_ty} vs {b_ty}")
        
        result_ty = a_ty if a_is_vec else b_ty

        if compare:
            if any_is_vec:
                result_ty = Vector.from_shape(result_ty.shape(), Bits(1))
            else:
                result_ty = Bits(1)
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.{op} {a.ref}, {b.ref} : {a_ty}, {b_ty} -> {result_ty}")
        return Signal(ref=tmp, ty=result_ty)

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

    @overload
    def mux(self, sel: Signal[Bits], a: Signal[DT], b: Signal[DT]) -> Signal[DT]: ...

    @overload
    def mux(self, sel: Signal, a: Signal, b: Signal) -> Signal: ...

    def mux(self, sel: Signal, a: Signal, b: Signal) -> Signal:
        sel_is_vec = isinstance(sel.ty, Vector)
        a_is_vec = isinstance(a.ty, Vector)
        b_is_vec = isinstance(b.ty, Vector)
        any_is_vec = sel_is_vec or a_is_vec or b_is_vec

        if sel.width != 1:
            raise TypeError(f"mux sel must be i1, got {sel.ty}")
        if a.width != b.width:
            raise TypeError(f"mux a and b must have same width, got {a.ty} vs {b.ty}")

        if any_is_vec:
            # either Bits or Vector with matching shapes
            if a_is_vec and b_is_vec and a.ty.shape() != b.ty.shape():
                raise TypeError(f"mux a and b must have same vector shape, got {a.ty} vs {b.ty}")
            if a_is_vec and sel_is_vec and a.ty.shape() != sel.ty.shape():
                raise TypeError(f"mux sel and a must have same vector shape, got {sel.ty} vs {a.ty}")
            if b_is_vec and sel_is_vec and b.ty.shape() != sel.ty.shape():
                raise TypeError(f"mux sel and b must have same vector shape, got {sel.ty} vs {b.ty}")
            result_ty = a.ty if a_is_vec else b.ty if b_is_vec else Vector.from_shape(sel.ty.shape(), a.ty)
        else:
            result_ty = a.ty

        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.mux {sel.ref}, {a.ref}, {b.ref} : {sel.ty}, {a.ty}, {b.ty} -> {result_ty}")
        return Signal(ref=tmp, ty=result_ty)

    def and_(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("and", a, b)

    def or_(self, a: Signal[DT], b: Signal[DT]) -> Signal[DT]:
        return self._emit_elementwise_binary("or", a, b)

    def xor(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("xor", a, b)

    def not_(self, a: Signal) -> Signal:
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.not {a.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def eq(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("eq", a, b, compare=True)

    def ult(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("ult", a, b, compare=True)

    def slt(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("slt", a, b, compare=True)

    def trunc(self, a: Signal, *, width: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("trunc requires an integer or vector-of-integer input")
        # Align with the MLIR trunc verifier (PYCOps.cpp verifyIntCast): it only
        # rejects out > in; out == in is a legal identity cast. Short-circuit the
        # equal-width case here so parameterized code that computes width from
        # runtime values does not need to special-case the no-op, and downstream
        # IR stays free of redundant pyc.trunc nodes.
        if width == a.width:
            return a
        if width > a.width:
            raise ValueError(f"trunc width must be <= input width, got {width} > {a.width}")
        out_ty = Vector.from_shape(a.ty.shape(), Bits(width)) if isinstance(a.ty, Vector) else Bits(width)
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.trunc {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def zext(self, a: Signal, *, width: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("zext requires an integer or vector-of-integer input")
        out_ty = Vector.from_shape(a.ty.shape(), Bits(width)) if isinstance(a.ty, Vector) else Bits(width)
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.zext {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def sext(self, a: Signal, *, width: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("sext requires an integer or vector-of-integer input")
        out_ty = Vector.from_shape(a.ty.shape(), Bits(width)) if isinstance(a.ty, Vector) else Bits(width)
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.sext {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    @overload
    def extract(self, a: Signal[Bits], *, lsb: int, width: int) -> Signal[Bits]: ...
    
    @overload
    def extract(self, a: Signal[Vector], *, lsb: int, width: int) -> Signal[Vector]: ...
    
    @overload
    def extract(self, a: Signal[Data], *, lsb: int, width: int) -> Signal[Data]: ...
    
    def extract(self, a: Signal, *, lsb: int, width: int) -> Signal:
        if lsb < 0:
            raise ValueError("extract lsb must be >= 0")
        if width <= 0:
            raise ValueError("extract width must be > 0")
        in_width = a.ty.width
        if lsb + width > in_width:
            raise ValueError("extract slice out of range for input width")
        tmp = self._get_next_temp_var()
        if isinstance(a.ty, Vector):
            # Element-wise vector slice: no ``msb`` self-consistency attr (the
            # scalar ASL bitfield/lane gate is the only consumer of ``msb``).
            out_ty = Vector.from_shape(a.ty.shape(), Bits(width))
            self._emit(f"{tmp} = pyc.extract {a.ref} {{lsb = {int(lsb)}}} : {a.ty} -> {out_ty}")
        else:
            # Scalar slice: also emit the optional ``msb`` attribute so the MLIR
            # verifier checks ``msb == lsb + width - 1`` (ASL T1 self-consistency).
            out_ty = Bits(width)
            msb = int(lsb) + int(width) - 1
            self._emit(
                f"{tmp} = pyc.extract {a.ref} {{lsb = {int(lsb)}, msb = {int(msb)}}} : {a.ty} -> {out_ty}"
            )
        return Signal(ref=tmp, ty=out_ty)

    def shli(self, a: Signal, *, amount: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("shli requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("shli amount must be >= 0")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.shli {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def lshri(self, a: Signal, *, amount: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("lshri requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("lshri amount must be >= 0")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.lshri {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def ashri(self, a: Signal, *, amount: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("ashri requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("ashri amount must be >= 0")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.ashri {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def shl(self, a: Signal, amount: Signal) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)) or not isinstance(amount.ty, Bits):
            raise TypeError("shl requires integer or vector-of-integer input and scalar integer amount")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.shl {a.ref}, {amount.ref} : {a.ty}, {amount.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def lshr(self, a: Signal, amount: Signal) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)) or not isinstance(amount.ty, Bits):
            raise TypeError("lshr requires integer or vector-of-integer input and scalar integer amount")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.lshr {a.ref}, {amount.ref} : {a.ty}, {amount.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def ashr(self, a: Signal, amount: Signal) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)) or not isinstance(amount.ty, Bits):
            raise TypeError("ashr requires integer or vector-of-integer input and scalar integer amount")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.ashr {a.ref}, {amount.ref} : {a.ty}, {amount.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def concat(self, *inputs: Signal) -> Signal:
        """Concatenate integer signals into a packed bus (MSB-first)."""
        if not inputs:
            raise ValueError("concat requires at least one input")

        def w(ty: Data) -> int:
            if not isinstance(ty, Bits):
                raise TypeError("concat only supports integer types")
            return ty.width

        out_w = sum(w(s.ty) for s in inputs)
        out_ty = Bits(out_w)
        tmp = self._get_next_temp_var()
        op_list = ", ".join(s.ref for s in inputs)
        ty_list = ", ".join(str(s.ty) for s in inputs)
        self._emit(f"{tmp} = pyc.concat ({op_list}) : ({ty_list}) -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_create(self, elements: list[Signal[DT]]) -> Signal[Vector[DT]]:
        if not elements:
            raise ValueError("v_create requires at least one element")
        first_ty = elements[0].ty
        for e in elements[1:]:
            if e.ty != first_ty:
                raise TypeError(f"v_create requires same element type, got {first_ty} vs {e.ty}")
        out_ty = Vector(len(elements), first_ty)
        tmp = self._get_next_temp_var()
        op_list = ", ".join(s.ref for s in elements)
        ty_list = ", ".join(str(s.ty) for s in elements)
        self._emit(f"{tmp} = pyc.v_create ({op_list}) : ({ty_list}) -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_broadcast(self, scalar: Signal[Bits], *, size: int) -> Signal[Vector[Bits]]:
        lanes = int(size)
        if lanes <= 0:
            raise ValueError("v_broadcast size must be > 0")
        out_ty = Vector(lanes, scalar.ty)
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.v_broadcast {scalar.ref} to {lanes} : {scalar.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_broadcast_dim(self, vec: Signal, *, size: int, dim: int) -> Signal[Vector]:
        """Broadcast a vector by repeating along a new dimension."""
        lanes = int(size)
        d = int(dim)
        if lanes <= 0:
            raise ValueError("v_broadcast_dim size must be > 0")
        if not isinstance(vec.ty, Vector):
            raise TypeError(f"v_broadcast_dim expects a vector, got {vec.ty}")
        shape = vec.ty.shape()
        elem_ty = vec.ty.datatype()
        if d < 0 or d > len(shape):
            raise ValueError(f"v_broadcast_dim dim out of range: {d} for {vec.ty}")
        new_shape = list(shape)
        new_shape.insert(d, lanes)
        out_ty = Vector.from_shape(new_shape, elem_ty)
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.v_broadcast_dim {vec.ref} to {lanes}, {d} : {vec.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_get(self, vec: Signal[Vector[DT]], *, index: int) -> Signal[DT]:
        if not isinstance(vec.ty, Vector):
            raise TypeError(f"v_get expects a vector, got {vec.ty}")
        idx = int(index)
        if idx < 0 or idx >= vec.ty.length:
            raise ValueError(f"v_get index out of range: {idx} for {vec.ty}")
        tmp = self._get_next_temp_var()
        self._emit(f"{tmp} = pyc.v_get {vec.ref} [{idx}] : {vec.ty} -> {vec.ty.elem}")
        self._vec_get_map[tmp] = (str(vec.ref), idx)
        return Signal(ref=tmp, ty=vec.ty.elem)

    @overload
    def priority_mux(
        self,
        sels: Signal[Vector[Bits]],
        vals: Signal[Vector[DT]],
        *,
        mode: str = "chain",
        default: Signal[DT] | None = None,
    ) -> Signal[DT]: ...

    @overload
    def priority_mux(
        self,
        sels: Signal[Vector[Data]],
        vals: Signal[Vector[Data]],
        *,
        mode: str = "chain",
        default: Signal[Data] | None = None,
    ) -> Signal[Data]: ...

    def priority_mux(
        self,
        sels: Signal[Vector[Data]],
        vals: Signal[Vector[Data]],
        *,
        mode: str = "chain",
        default: Signal[Data] | None = None,
    ) -> Signal[Data]:
        """Select the first asserted lane from a rank-1 selector vector.

        ``sels`` must be ``vector<Nxi1>``. ``vals`` must have shape
        ``[N, ...]``; the result and optional ``default`` have shape ``[...]``.
        When ``default`` is omitted, the final value lane is used.
        """
        if not isinstance(sels, Signal) or not isinstance(sels.ty, Vector):
            raise TypeError("priority_mux sels must be a vector Signal")
        if len(sels.ty.shape()) != 1 or sels.width != 1:
            raise TypeError(f"priority_mux sels must be vector<Nxi1>, got {sels.ty}")
        if not isinstance(vals, Signal) or not isinstance(vals.ty, Vector):
            raise TypeError("priority_mux vals must be a vector Signal")
        if vals.ty.shape()[0] != sels.ty.shape()[0]:
            raise TypeError(
                "priority_mux sels length must equal vals.shape[0]: "
                f"{sels.ty.shape()[0]} vs {vals.ty.shape()[0]}"
            )
        if mode not in {"chain", "tree"}:
            raise ValueError(f"priority_mux mode must be 'chain' or 'tree', got {mode!r}")

        value_ty = vals.ty.elem
        if default is None:
            default = self.v_get(vals, index=sels.ty.shape()[0] - 1)
        elif not isinstance(default, Signal):
            raise TypeError("priority_mux default must be a Signal or None")
        if default.ty != value_ty:
            raise TypeError(
                "priority_mux default shape/type must match vals[1:]: "
                f"expected {value_ty}, got {default.ty}"
            )

        if mode == "chain":
            selected = default
            for i in range(sels.ty.shape()[0] - 1, -1, -1):
                selected = self.mux(
                    self.v_get(sels, index=i),
                    self.v_get(vals, index=i),
                    selected,
                )
            return selected

        def select_tree(begin: int, end: int) -> tuple[Signal[Data], Signal[Data]]:
            if end - begin == 1:
                return self.v_get(sels, index=begin), self.v_get(vals, index=begin)
            mid = begin + (end - begin) // 2
            left_any, left_value = select_tree(begin, mid)
            right_any, right_value = select_tree(mid, end)
            return self.or_(left_any, right_any), self.mux(left_any, left_value, right_value)

        any_selected, selected = select_tree(0, sels.ty.shape()[0])
        return self.mux(any_selected, selected, default)

    def _v_reduce(self, op: str, vec: Signal, *, dim: int | None = None, mode: str = "chain") -> Signal:
        if mode not in ("chain", "tree"):
            raise ValueError(f"reduce mode must be 'chain' or 'tree', got {mode!r}")
        if not isinstance(vec.ty, Vector):
            raise TypeError(f"{op} expects a vector, got {vec.ty}")
        shape = vec.ty.shape()
        elem_ty = vec.ty.datatype()
        if dim is None:
            out_ty = elem_ty
        else:
            reduce_dim = int(dim)
            if reduce_dim < 0 or reduce_dim >= len(shape):
                raise ValueError(f"{op} dim out of range: {reduce_dim} for {vec.ty}")
            out_shape = [d for i, d in enumerate(shape) if i != reduce_dim]
            out_ty = elem_ty if not out_shape else Vector.from_shape(out_shape, elem_ty)
        tmp = self._get_next_temp_var()
        attr_parts = [f'mode = "{mode}"']
        if dim is not None:
            attr_parts.append(f"dim = {int(dim)}")
        attrs = " {" + ", ".join(attr_parts) + "}"
        self._emit(f"{tmp} = pyc.{op} {vec.ref}{attrs} : {vec.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def v_or_reduce(self, vec: Signal, *, dim: int | None = None, mode: str = "chain") -> Signal:
        return self._v_reduce("v_or_reduce", vec, dim=dim, mode=mode)

    def v_and_reduce(self, vec: Signal, *, dim: int | None = None, mode: str = "chain") -> Signal:
        return self._v_reduce("v_and_reduce", vec, dim=dim, mode=mode)

    def v_add_reduce(self, vec: Signal, *, dim: int | None = None, mode: str = "chain") -> Signal:
        return self._v_reduce("v_add_reduce", vec, dim=dim, mode=mode)

    def instance_op(
        self,
        callee: str,
        *inputs: Signal,
        result_types: list[Data | str],
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
            tmp = self._get_next_temp_var()
            dt = ty if isinstance(ty, Data) else Data.from_str(ty)
            out.append(Signal(ref=tmp, ty=dt))

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

        in_ty_sig = ", ".join(str(s.ty) for s in inputs)
        in_sig = f"({in_ty_sig})"
        if len(out) == 0:
            out_sig = "()"
        elif len(out) == 1:
            out_sig = str(out[0].ty)
        else:
            out_ty_sig = ", ".join(str(s.ty) for s in out)
            out_sig = f"({out_ty_sig})"

        if ops:
            self._emit(f"{lhs}pyc.instance {ops} {attrs} : {in_sig} -> {out_sig}")
        else:
            self._emit(f"{lhs}pyc.instance {attrs} : {in_sig} -> {out_sig}")
        return out

    def alias(self, a: Signal, *, name: str | None = None) -> Signal:
        """Alias a value (pure) to attach a debug name in codegen."""
        tmp = self._get_next_temp_var()
        if name is None:
            self._emit(f"{tmp} = pyc.alias {a.ref} : {a.ty}")
        else:
            self._emit(f'{tmp} = pyc.alias {a.ref} {{pyc.name = "{name}"}} : {a.ty}')
        return Signal(ref=tmp, ty=a.ty)

    def new_wire(self, *, width: int, shape: list[int] | None = None, name: str | None = None) -> Signal:
        return self.new_signal(width=width, shape=shape, name=name)
        
    def new_signal(self, *, width: int, shape: list[int] | None = None, name: str | None = None) -> Signal:
        if width <= 0:
            raise ValueError("width must be > 0")
        if shape:
            ty = Vector.from_shape(shape, Bits(width))
        else: 
            ty = Bits(width)
        tmp = self._get_next_temp_var()
        if name is None:
            self._emit(f"{tmp} = pyc.wire : {ty}")
        else:
            self._emit(f'{tmp} = pyc.wire {{pyc.name = "{name}"}} : {ty}')
        return Signal(ref=tmp, ty=ty)

    def assign(self, dst: Signal, src: Signal) -> None:
        self._require_same_ty(dst.ty, src.ty, "assign")
        self._emit(f"pyc.assign {dst.ref}, {src.ref} : {dst.ty}")

    def assert_(self, cond: Signal, *, msg: str | None = None) -> None:
        """Simulation-only assertion (prototype)."""
        if cond.ty != Bits(1):
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
        if not isinstance(clk.ty, Clock):
            raise TypeError("reg clk must be !pyc.clock")
        if not isinstance(rst.ty, Reset):
            raise TypeError("reg rst must be !pyc.reset")
        if en.ty != Bits(1):
            raise TypeError("reg en must be i1")
        self._require_same_ty(next_.ty, init.ty, "reg")
        tmp = self._get_next_temp_var()
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
        if not isinstance(clk.ty, Clock):
            raise TypeError("fifo clk must be !pyc.clock")
        if not isinstance(rst.ty, Reset):
            raise TypeError("fifo rst must be !pyc.reset")
        if in_valid.ty != Bits(1):
            raise TypeError("fifo in_valid must be i1")
        if out_ready.ty != Bits(1):
            raise TypeError("fifo out_ready must be i1")
        if depth <= 0:
            raise ValueError("fifo depth must be > 0")
        in_ready = self._get_next_temp_var()
        out_valid = self._get_next_temp_var()
        out_data = self._get_next_temp_var()
        self._emit(
            f"{in_ready}, {out_valid}, {out_data} = pyc.fifo {clk.ref}, {rst.ref}, {in_valid.ref}, {in_data.ref}, {out_ready.ref} "
            + f'{{depth = {int(depth)}}} : {in_data.ty}'
        )
        return Signal(in_ready, Bits(1)), Signal(out_valid, Bits(1)), Signal(out_data, in_data.ty)

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
        if not isinstance(clk.ty, Clock):
            raise TypeError("byte_mem clk must be !pyc.clock")
        if not isinstance(rst.ty, Reset):
            raise TypeError("byte_mem rst must be !pyc.reset")
        if wvalid.ty != Bits(1):
            raise TypeError("byte_mem wvalid must be i1")
        if raddr.ty != waddr.ty:
            raise TypeError("byte_mem raddr/waddr must have the same type")
        if wdata.ty != Bits(64):
            raise TypeError("byte_mem wdata must be Bits(64)")
        if wstrb.ty != Bits(8):
            raise TypeError("byte_mem wstrb must be Bits(8)")
        if depth <= 0:
            raise ValueError("byte_mem depth must be > 0")
        if not isinstance(name, str) or not name.strip() or not _IDENT_RE.match(name):
            raise ValueError("byte_mem name must match [A-Za-z_][A-Za-z0-9_]* (Decision 0025)")

        tmp = self._get_next_temp_var()
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
        if not isinstance(clk.ty, Clock):
            raise TypeError("sync_mem clk must be !pyc.clock")
        if not isinstance(rst.ty, Reset):
            raise TypeError("sync_mem rst must be !pyc.reset")
        if ren.ty != Bits(1):
            raise TypeError("sync_mem ren must be i1")
        if wvalid.ty != Bits(1):
            raise TypeError("sync_mem wvalid must be i1")
        if raddr.ty != waddr.ty:
            raise TypeError("sync_mem raddr/waddr must have the same type")
        if depth <= 0:
            raise ValueError("sync_mem depth must be > 0")
        if not isinstance(name, str) or not name.strip() or not _IDENT_RE.match(name):
            raise ValueError("sync_mem name must match [A-Za-z_][A-Za-z0-9_]* (Decision 0025)")

        tmp = self._get_next_temp_var()
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
        if not isinstance(clk.ty, Clock):
            raise TypeError("sync_mem_dp clk must be !pyc.clock")
        if not isinstance(rst.ty, Reset):
            raise TypeError("sync_mem_dp rst must be !pyc.reset")
        if ren0.ty != Bits(1) or ren1.ty != Bits(1):
            raise TypeError("sync_mem_dp ren0/ren1 must be i1")
        if wvalid.ty != Bits(1):
            raise TypeError("sync_mem_dp wvalid must be i1")
        if raddr0.ty != raddr1.ty or raddr0.ty != waddr.ty:
            raise TypeError("sync_mem_dp raddr0/raddr1/waddr must have the same type")
        if depth <= 0:
            raise ValueError("sync_mem_dp depth must be > 0")
        if not isinstance(name, str) or not name.strip() or not _IDENT_RE.match(name):
            raise ValueError("sync_mem_dp name must match [A-Za-z_][A-Za-z0-9_]* (Decision 0025)")

        out0 = self._get_next_temp_var()
        out1 = self._get_next_temp_var()
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
        if not isinstance(in_clk.ty, Clock) or not isinstance(out_clk.ty, Clock):
            raise TypeError("async_fifo clk must be !pyc.clock")
        if not isinstance(in_rst.ty, Reset) or not isinstance(out_rst.ty, Reset):
            raise TypeError("async_fifo rst must be !pyc.reset")
        if in_valid.ty != Bits(1):
            raise TypeError("async_fifo in_valid must be i1")
        if out_ready.ty != Bits(1):
            raise TypeError("async_fifo out_ready must be i1")
        if depth <= 0:
            raise ValueError("async_fifo depth must be > 0")
        in_ready = self._get_next_temp_var()
        out_valid = self._get_next_temp_var()
        out_data = self._get_next_temp_var()
        self._emit(
            f"{in_ready}, {out_valid}, {out_data} = pyc.async_fifo {in_clk.ref}, {in_rst.ref}, {out_clk.ref}, {out_rst.ref}, "
            + f"{in_valid.ref}, {in_data.ref}, {out_ready.ref} {{depth = {int(depth)}}} : {in_data.ty}"
        )
        return Signal(in_ready, Bits(1)), Signal(out_valid, Bits(1)), Signal(out_data, in_data.ty)

    def cdc_sync(self, clk: Signal, rst: Signal, a: Signal, *, stages: int | None = None) -> Signal:
        if not isinstance(clk.ty, Clock):
            raise TypeError("cdc_sync clk must be !pyc.clock")
        if not isinstance(rst.ty, Reset):
            raise TypeError("cdc_sync rst must be !pyc.reset")
        tmp = self._get_next_temp_var()
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
            res_sig = f"-> ({', '.join(str(t) for t in res_types)})"
            ret_ty = ", ".join(str(t) for t in res_types)
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
    def _arg(self, name: str, ty: Data) -> Signal:
        ref = f"%{name}"
        s = Signal(ref=ref, ty=ty)
        self._args.append((name, s))
        return s

    def _get_next_temp_var(self) -> str:
        self._temp_var_index += 1
        return f"%v{self._temp_var_index}"

    def _emit(self, line: str) -> None:
        self._lines.append(("  " * self._indent_level) + line)

    @staticmethod
    def _require_same_ty(a: Data, b: Data, op: str) -> None:
        if a != b:
            raise TypeError(f"{op} requires same types, got {a} and {b}")
