from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeGuard, Union, overload

from .connectors import Connector
from .data import Bits, Clock, DT, Data, Reset, Vector
from .vector_lower import LaneInfo, flat_index, iter_indices, lane_count, lane_suffix, port_mlir_type

if TYPE_CHECKING:
    from .hw import Module, Reg, Wire

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REF_TOKEN_RE = re.compile(r"%[A-Za-z0-9_]+")


def _design_error(msg: str) -> Exception:
    # Imported lazily: design.py imports this module at load time.
    from .design import DesignError

    return DesignError(msg)


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
        # Vector lane registry: vector tag ref -> flat row-major lane refs.
        self._lanes: dict[str, LaneInfo] = {}
        # Packed (concatenated) refs for vector results, computed once on emit.
        self._packed_result_refs_cache: list[str] | None = None

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
        a_lanes = self.lanes_of(a)
        b_lanes = self.lanes_of(b)
        a_is_vec = a_lanes is not None
        b_is_vec = b_lanes is not None

        if not (a_is_vec or b_is_vec):
            result_ty = Bits(1) if compare else a_ty
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {a.ref}, {b.ref} : {a_ty}, {b_ty} -> {result_ty}")
            return Signal(ref=tmp, ty=result_ty)

        a_leaf = a_lanes.leaf if a_is_vec else a_ty
        b_leaf = b_lanes.leaf if b_is_vec else b_ty
        # Signedness is a frontend intent bit; MLIR leaf types are width-only (`iN`).
        if str(a_leaf) != str(b_leaf):
            raise TypeError(f"{op} operand leaf types must match: {a_ty} vs {b_ty}")
        if a_is_vec and b_is_vec and a_lanes.shape != b_lanes.shape:
            raise TypeError(f"{op} vector shapes must match: {a_ty} vs {b_ty}")
        lanes = a_lanes if a_is_vec else b_lanes
        assert lanes is not None
        out_leaf = Bits(1) if compare else lanes.leaf
        refs: list[str] = []
        for i in range(lane_count(lanes.shape)):
            a_ref = a_lanes.refs[i] if a_lanes is not None else a.ref
            b_ref = b_lanes.refs[i] if b_lanes is not None else b.ref
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {a_ref}, {b_ref} : {a_leaf}, {b_leaf} -> {out_leaf}")
            refs.append(tmp)
        return self.mk_vec(lanes.shape, out_leaf, refs)

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
        sel_lanes = self.lanes_of(sel)
        a_lanes = self.lanes_of(a)
        b_lanes = self.lanes_of(b)
        sel_is_vec = sel_lanes is not None
        a_is_vec = a_lanes is not None
        b_is_vec = b_lanes is not None

        if sel.width != 1:
            raise TypeError(f"mux sel must be i1, got {sel.ty}")
        if a.width != b.width:
            raise TypeError(f"mux a and b must have same width, got {a.ty} vs {b.ty}")

        if not (sel_is_vec or a_is_vec or b_is_vec):
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.mux {sel.ref}, {a.ref}, {b.ref} : {sel.ty}, {a.ty}, {b.ty} -> {a.ty}")
            return Signal(ref=tmp, ty=a.ty)

        # either Bits or Vector with matching shapes
        if a_is_vec and b_is_vec and a_lanes.shape != b_lanes.shape:
            raise TypeError(f"mux a and b must have same vector shape, got {a.ty} vs {b.ty}")
        if a_is_vec and sel_is_vec and a_lanes.shape != sel_lanes.shape:
            raise TypeError(f"mux sel and a must have same vector shape, got {sel.ty} vs {a.ty}")
        if b_is_vec and sel_is_vec and b_lanes.shape != sel_lanes.shape:
            raise TypeError(f"mux sel and b must have same vector shape, got {sel.ty} vs {b.ty}")
        lanes = a_lanes if a_is_vec else b_lanes if b_is_vec else sel_lanes
        assert lanes is not None
        leaf = a_lanes.leaf if a_lanes is not None else b_lanes.leaf if b_lanes is not None else a.ty
        refs: list[str] = []
        for i in range(lane_count(lanes.shape)):
            s_ref = sel_lanes.refs[i] if sel_lanes is not None else sel.ref
            a_ref = a_lanes.refs[i] if a_lanes is not None else a.ref
            b_ref = b_lanes.refs[i] if b_lanes is not None else b.ref
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.mux {s_ref}, {a_ref}, {b_ref} : {Bits(1)}, {leaf}, {leaf} -> {leaf}")
            refs.append(tmp)
        return self.mk_vec(lanes.shape, leaf, refs)

    def and_(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("and", a, b)

    def or_(self, a: Signal[DT], b: Signal[DT]) -> Signal[DT]:
        return self._emit_elementwise_binary("or", a, b)

    def xor(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("xor", a, b)

    def not_(self, a: Signal) -> Signal:
        lanes = self.lanes_of(a)
        if lanes is None:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.not {a.ref} : {a.ty}")
            return Signal(ref=tmp, ty=a.ty)
        refs: list[str] = []
        for lane in lanes.refs:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.not {lane} : {lanes.leaf}")
            refs.append(tmp)
        return self.mk_vec(lanes.shape, lanes.leaf, refs)

    def eq(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("eq", a, b, compare=True)

    def ult(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("ult", a, b, compare=True)

    def slt(self, a: Signal, b: Signal) -> Signal:
        return self._emit_elementwise_binary("slt", a, b, compare=True)

    def _emit_int_cast(self, op: str, a: Signal, *, width: int) -> Signal:
        lanes = self.lanes_of(a)
        out_leaf = Bits(width)
        if lanes is None:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {a.ref} : {a.ty} -> {out_leaf}")
            return Signal(ref=tmp, ty=out_leaf)
        refs: list[str] = []
        for lane in lanes.refs:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {lane} : {lanes.leaf} -> {out_leaf}")
            refs.append(tmp)
        return self.mk_vec(lanes.shape, out_leaf, refs)

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
        return self._emit_int_cast("trunc", a, width=width)

    def zext(self, a: Signal, *, width: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("zext requires an integer or vector-of-integer input")
        return self._emit_int_cast("zext", a, width=width)

    def sext(self, a: Signal, *, width: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("sext requires an integer or vector-of-integer input")
        return self._emit_int_cast("sext", a, width=width)

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
        lanes = self.lanes_of(a)
        if lanes is not None:
            # Element-wise vector slice: no ``msb`` self-consistency attr (the
            # scalar ASL bitfield/lane gate is the only consumer of ``msb``).
            out_leaf = Bits(width)
            refs: list[str] = []
            for lane in lanes.refs:
                tmp = self._get_next_temp_var()
                self._emit(f"{tmp} = pyc.extract {lane} {{lsb = {int(lsb)}}} : {lanes.leaf} -> {out_leaf}")
                refs.append(tmp)
            return self.mk_vec(lanes.shape, out_leaf, refs)
        # Scalar slice: also emit the optional ``msb`` attribute so the MLIR
        # verifier checks ``msb == lsb + width - 1`` (ASL T1 self-consistency).
        tmp = self._get_next_temp_var()
        out_ty = Bits(width)
        msb = int(lsb) + int(width) - 1
        self._emit(
            f"{tmp} = pyc.extract {a.ref} {{lsb = {int(lsb)}, msb = {int(msb)}}} : {a.ty} -> {out_ty}"
        )
        return Signal(ref=tmp, ty=out_ty)

    def _emit_shift_imm(self, op: str, a: Signal, *, amount: int) -> Signal:
        lanes = self.lanes_of(a)
        if lanes is None:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {a.ref} {{amount = {int(amount)}}} : {a.ty}")
            return Signal(ref=tmp, ty=a.ty)
        refs: list[str] = []
        for lane in lanes.refs:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {lane} {{amount = {int(amount)}}} : {lanes.leaf}")
            refs.append(tmp)
        return self.mk_vec(lanes.shape, lanes.leaf, refs)

    def _emit_shift_val(self, op: str, a: Signal, amount: Signal) -> Signal:
        lanes = self.lanes_of(a)
        if lanes is None:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {a.ref}, {amount.ref} : {a.ty}, {amount.ty}")
            return Signal(ref=tmp, ty=a.ty)
        refs: list[str] = []
        for lane in lanes.refs:
            tmp = self._get_next_temp_var()
            self._emit(f"{tmp} = pyc.{op} {lane}, {amount.ref} : {lanes.leaf}, {amount.ty}")
            refs.append(tmp)
        return self.mk_vec(lanes.shape, lanes.leaf, refs)

    def shli(self, a: Signal, *, amount: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("shli requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("shli amount must be >= 0")
        return self._emit_shift_imm("shli", a, amount=amount)

    def lshri(self, a: Signal, *, amount: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("lshri requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("lshri amount must be >= 0")
        return self._emit_shift_imm("lshri", a, amount=amount)

    def ashri(self, a: Signal, *, amount: int) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)):
            raise TypeError("ashri requires an integer or vector-of-integer input")
        if amount < 0:
            raise ValueError("ashri amount must be >= 0")
        return self._emit_shift_imm("ashri", a, amount=amount)

    def shl(self, a: Signal, amount: Signal) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)) or not isinstance(amount.ty, Bits):
            raise TypeError("shl requires integer or vector-of-integer input and scalar integer amount")
        return self._emit_shift_val("shl", a, amount)

    def lshr(self, a: Signal, amount: Signal) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)) or not isinstance(amount.ty, Bits):
            raise TypeError("lshr requires integer or vector-of-integer input and scalar integer amount")
        return self._emit_shift_val("lshr", a, amount)

    def ashr(self, a: Signal, amount: Signal) -> Signal:
        if not isinstance(a.ty, (Bits, Vector)) or not isinstance(amount.ty, Bits):
            raise TypeError("ashr requires integer or vector-of-integer input and scalar integer amount")
        return self._emit_shift_val("ashr", a, amount)

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
        sub = self.lanes_of(elements[0])
        if sub is None:
            return self.mk_vec((len(elements),), first_ty, [s.ref for s in elements])
        refs = [ref for e in elements for ref in self.lanes_of(e).refs]
        return self.mk_vec((len(elements),) + sub.shape, sub.leaf, refs)

    def v_broadcast(self, scalar: Signal[Bits], *, size: int) -> Signal[Vector[Bits]]:
        lanes = int(size)
        if lanes <= 0:
            raise ValueError("v_broadcast size must be > 0")
        if isinstance(scalar.ty, Vector):
            raise TypeError(f"v_broadcast expects a scalar integer input, got {scalar.ty}")
        return self.mk_vec((lanes,), scalar.ty, [scalar.ref] * lanes)

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
        src = self.lanes_of(vec)
        src_shape = tuple(src.shape)
        refs = [
            src.refs[flat_index(src_shape, tuple(i for k, i in enumerate(idx) if k != d))]
            for idx in iter_indices(tuple(new_shape))
        ]
        return self.mk_vec(tuple(new_shape), elem_ty, refs)

    def v_get(self, vec: Signal[Vector[DT]], *, index: int) -> Signal[DT]:
        if not isinstance(vec.ty, Vector):
            raise TypeError(f"v_get expects a vector, got {vec.ty}")
        idx = int(index)
        if idx < 0 or idx >= vec.ty.length:
            raise ValueError(f"v_get index out of range: {idx} for {vec.ty}")
        lanes = self.lanes_of(vec)
        sub_shape = lanes.shape[1:]
        group = lane_count(sub_shape)
        refs = lanes.refs[idx * group : (idx + 1) * group]
        if not sub_shape:
            return Signal(ref=refs[0], ty=lanes.leaf)
        return self.mk_vec(sub_shape, lanes.leaf, refs)

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
        lanes = self.lanes_of(vec)
        shape = lanes.shape
        elem_ty = lanes.leaf
        if not isinstance(elem_ty, Bits):
            raise TypeError(f"{op} requires a Bits element type, got {elem_ty}")
        scalar_op = {"v_or_reduce": "or", "v_and_reduce": "and", "v_add_reduce": "add"}[op]

        def reduce_refs(refs: list[str]) -> str:
            vals = list(refs)
            if mode == "chain":
                out = vals[0]
                for nxt in vals[1:]:
                    tmp = self._get_next_temp_var()
                    self._emit(f"{tmp} = pyc.{scalar_op} {out}, {nxt} : {elem_ty}, {elem_ty} -> {elem_ty}")
                    out = tmp
                return out
            while len(vals) > 1:
                merged: list[str] = []
                for i in range(0, len(vals), 2):
                    if i + 1 < len(vals):
                        tmp = self._get_next_temp_var()
                        self._emit(
                            f"{tmp} = pyc.{scalar_op} {vals[i]}, {vals[i + 1]} : {elem_ty}, {elem_ty} -> {elem_ty}"
                        )
                        merged.append(tmp)
                    else:
                        merged.append(vals[i])
                vals = merged
            return vals[0]

        if dim is None:
            return Signal(ref=reduce_refs(list(lanes.refs)), ty=elem_ty)
        reduce_dim = int(dim)
        if reduce_dim < 0 or reduce_dim >= len(shape):
            raise ValueError(f"{op} dim out of range: {reduce_dim} for {vec.ty}")
        out_shape = tuple(d for i, d in enumerate(shape) if i != reduce_dim)
        if not out_shape:
            return Signal(ref=reduce_refs(list(lanes.refs)), ty=elem_ty)
        refs: list[str] = []
        for out_idx in iter_indices(out_shape):
            group: list[str] = []
            for k in range(shape[reduce_dim]):
                full = list(out_idx)
                full.insert(reduce_dim, k)
                group.append(lanes.refs[flat_index(shape, tuple(full))])
            refs.append(reduce_refs(group))
        return self.mk_vec(out_shape, elem_ty, refs)

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

        operands: list[Signal] = []
        for s in inputs:
            lanes = self.lanes_of(s)
            operands.append(
                self.pack_lanes(lanes.shape, lanes.leaf, lanes.refs) if lanes is not None else s
            )
        ops = ", ".join(s.ref for s in operands)
        attrs = f"{{callee = @{callee}"
        if name is not None:
            attrs += f', name = {json.dumps(str(name), ensure_ascii=False)}'
        if short_name is not None:
            attrs += f', short_name = {json.dumps(str(short_name), ensure_ascii=False)}'
        if keep:
            attrs += ", pyc.debug_keep = true"
        attrs += "}"

        in_ty_sig = ", ".join(str(port_mlir_type(s.ty)) for s in operands)
        in_sig = f"({in_ty_sig})"
        if len(out) == 0:
            out_sig = "()"
        elif len(out) == 1:
            out_sig = str(port_mlir_type(out[0].ty))
        else:
            out_ty_sig = ", ".join(str(port_mlir_type(s.ty)) for s in out)
            out_sig = f"({out_ty_sig})"

        if ops:
            self._emit(f"{lhs}pyc.instance {ops} {attrs} : {in_sig} -> {out_sig}")
        else:
            self._emit(f"{lhs}pyc.instance {attrs} : {in_sig} -> {out_sig}")
        results: list[Signal] = []
        for s in out:
            if isinstance(s.ty, Vector):
                results.append(self.unpack_lanes(s.ref, s.ty.shape(), s.ty.datatype()))
            else:
                results.append(s)
        return results

    def alias(
        self,
        a: Signal,
        *,
        name: str | None = None,
        generated: str | None = None,
    ) -> Signal:
        """Alias a value (pure) to attach a debug name in codegen."""
        lanes = self.lanes_of(a)
        if lanes is not None:
            refs: list[str] = []
            for lane_idx in iter_indices(lanes.shape):
                lane = lanes.refs[flat_index(lanes.shape, lane_idx)]
                lane_attrs: list[str] = []
                if generated is not None:
                    lane_attrs.append(f"pyc.generated = {json.dumps(str(generated))}")
                if name is not None:
                    lane_name = str(name) + lane_suffix(lanes.shape, lane_idx)
                    lane_attrs.append(f"pyc.name = {json.dumps(lane_name)}")
                tmp = self._get_next_temp_var()
                if not lane_attrs:
                    self._emit(f"{tmp} = pyc.alias {lane} : {lanes.leaf}")
                else:
                    self._emit(
                        f"{tmp} = pyc.alias {lane} {{{', '.join(lane_attrs)}}} : {lanes.leaf}"
                    )
                refs.append(tmp)
            return self.mk_vec(lanes.shape, lanes.leaf, refs)
        tmp = self._get_next_temp_var()
        attrs: list[str] = []
        if generated is not None:
            attrs.append(f"pyc.generated = {json.dumps(str(generated))}")
        if name is not None:
            attrs.append(f"pyc.name = {json.dumps(str(name))}")
        if not attrs:
            self._emit(f"{tmp} = pyc.alias {a.ref} : {a.ty}")
        else:
            self._emit(f"{tmp} = pyc.alias {a.ref} {{{', '.join(attrs)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def new_wire(
        self,
        *,
        width: int,
        shape: list[int] | None = None,
        name: str | None = None,
        generated: str | None = None,
    ) -> Signal:
        return self.new_signal(width=width, shape=shape, name=name, generated=generated)
        
    def new_signal(
        self,
        *,
        width: int,
        shape: list[int] | None = None,
        name: str | None = None,
        generated: str | None = None,
    ) -> Signal:
        if width <= 0:
            raise ValueError("width must be > 0")
        if shape:
            lane_shape = tuple(int(d) for d in shape)
            leaf = Bits(width)
            refs: list[str] = []
            for lane_idx in iter_indices(lane_shape):
                lane_attrs: list[str] = []
                if generated is not None:
                    lane_attrs.append(f"pyc.generated = {json.dumps(str(generated))}")
                if name is not None:
                    lane_name = str(name) + lane_suffix(lane_shape, lane_idx)
                    lane_attrs.append(f"pyc.name = {json.dumps(lane_name)}")
                tmp = self._get_next_temp_var()
                if not lane_attrs:
                    self._emit(f"{tmp} = pyc.wire : {leaf}")
                else:
                    self._emit(f"{tmp} = pyc.wire {{{', '.join(lane_attrs)}}} : {leaf}")
                refs.append(tmp)
            return self.mk_vec(lane_shape, leaf, refs)
        ty = Bits(width)
        tmp = self._get_next_temp_var()
        attrs: list[str] = []
        if generated is not None:
            attrs.append(f"pyc.generated = {json.dumps(str(generated))}")
        if name is not None:
            attrs.append(f"pyc.name = {json.dumps(str(name))}")
        if not attrs:
            self._emit(f"{tmp} = pyc.wire : {ty}")
        else:
            self._emit(f"{tmp} = pyc.wire {{{', '.join(attrs)}}} : {ty}")
        return Signal(ref=tmp, ty=ty)

    def assign(self, dst: Signal, src: Signal) -> None:
        self._require_same_ty(dst.ty, src.ty, "assign")
        dst_lanes = self.lanes_of(dst)
        if dst_lanes is None:
            self._emit(f"pyc.assign {dst.ref}, {src.ref} : {dst.ty}")
            return
        src_lanes = self.lanes_of(src)
        assert src_lanes is not None
        for i in range(lane_count(dst_lanes.shape)):
            self._emit(
                f"pyc.assign {dst_lanes.refs[i]}, {src_lanes.refs[i]} : {dst_lanes.leaf}"
            )

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

    def reg(
        self,
        clk: Signal,
        rst: Signal,
        en: Signal,
        next_: Signal,
        init: Signal,
        *,
        generated: str | None = None,
    ) -> Signal:
        if not isinstance(clk.ty, Clock):
            raise TypeError("reg clk must be !pyc.clock")
        if not isinstance(rst.ty, Reset):
            raise TypeError("reg rst must be !pyc.reset")
        if en.ty != Bits(1):
            raise TypeError("reg en must be i1")
        self._require_same_ty(next_.ty, init.ty, "reg")
        attr_dict = ""
        if generated is not None:
            attr_dict = f" {{pyc.generated = {json.dumps(str(generated))}}}"
        lanes = self.lanes_of(next_)
        if lanes is not None:
            init_lanes = self.lanes_of(init)
            assert init_lanes is not None
            refs: list[str] = []
            for i in range(lane_count(lanes.shape)):
                tmp = self._get_next_temp_var()
                self._emit(
                    f"{tmp} = pyc.reg {clk.ref}, {rst.ref}, {en.ref}, {lanes.refs[i]}, "
                    f"{init_lanes.refs[i]}{attr_dict} : {lanes.leaf}"
                )
                refs.append(tmp)
            return self.mk_vec(lanes.shape, lanes.leaf, refs)
        tmp = self._get_next_temp_var()
        self._emit(
            f"{tmp} = pyc.reg {clk.ref}, {rst.ref}, {en.ref}, {next_.ref}, "
            f"{init.ref}{attr_dict} : {next_.ty}"
        )
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
            f"{tmp} = pyc.byte_mem {clk.ref}, {raddr.ref}, {wvalid.ref}, {waddr.ref}, {wdata.ref}, {wstrb.ref} "
            + f"{attrs} : {raddr.ty}, {wdata.ty}, {wstrb.ty}"
        )
        return Signal(ref=tmp, ty=wdata.ty)

    def sync_mem(
        self,
        clk: Signal,
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

        return self.sync_mem_nm(
            clk,
            [(ren, raddr)],
            [(wvalid, waddr, wdata, wstrb)],
            depth=depth,
            name=name,
            read_mode="old",
        )[0]

    def sync_mem_dp(
        self,
        clk: Signal,
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

        out0, out1 = self.sync_mem_nm(
            clk,
            [(ren0, raddr0), (ren1, raddr1)],
            [(wvalid, waddr, wdata, wstrb)],
            depth=depth,
            name=name,
            read_mode="old",
        )
        return out0, out1

    def sync_mem_nm(
        self,
        clk: Signal,
        read_ports: list[tuple[Signal, Signal]],
        write_ports: list[tuple[Signal, Signal, Signal, Signal]],
        *,
        depth: int,
        name: str,
        read_mode: str = "forward",
    ) -> list[Signal]:
        """Synchronous N-read x M-write memory with registered read data.

        ``read_ports`` is a list of ``(ren, raddr)``; ``write_ports`` a list of
        ``(wvalid, waddr, wdata, wstrb)``. Write ports apply in list order, so a
        later entry wins a same-address same-cycle conflict. ``read_mode`` is
        ``"forward"`` (a same-edge write to the read address is forwarded to the
        read output: register-file semantics) or ``"old"`` (the read output is
        the pre-write entry: plain SRAM semantics). Returns one read-data signal
        per read port.
        """
        if not isinstance(clk.ty, Clock):
            raise TypeError("sync_mem_nm clk must be !pyc.clock")
        if not read_ports:
            raise ValueError("sync_mem_nm needs at least one read port")
        if not write_ports:
            raise ValueError("sync_mem_nm needs at least one write port")
        if read_mode not in ("forward", "old"):
            raise ValueError('sync_mem_nm read_mode must be "forward" or "old"')
        if depth <= 0:
            raise ValueError("sync_mem_nm depth must be > 0")
        if not isinstance(name, str) or not name.strip() or not _IDENT_RE.match(name):
            raise ValueError("sync_mem_nm name must match [A-Za-z_][A-Za-z0-9_]* (Decision 0025)")

        addr_ty = read_ports[0][1].ty
        data_ty = write_ports[0][2].ty
        strb_ty = write_ports[0][3].ty
        for ren, raddr in read_ports:
            if ren.ty != Bits(1):
                raise TypeError("sync_mem_nm ren must be i1")
            if raddr.ty != addr_ty:
                raise TypeError("sync_mem_nm raddrs must have the same type")
        for wvalid, waddr, wdata, wstrb in write_ports:
            if wvalid.ty != Bits(1):
                raise TypeError("sync_mem_nm wvalid must be i1")
            if waddr.ty != addr_ty:
                raise TypeError("sync_mem_nm waddr must match raddr type")
            if wdata.ty != data_ty or wstrb.ty != strb_ty:
                raise TypeError("sync_mem_nm write ports must share data/strb types")

        rp, wp = len(read_ports), len(write_ports)
        outs = [self._get_next_temp_var() for _ in range(rp)]
        operands = [s.ref for s, _ in read_ports] + [a.ref for _, a in read_ports]
        operands += [v.ref for v, _, _, _ in write_ports]
        operands += [a.ref for _, a, _, _ in write_ports]
        operands += [d.ref for _, _, d, _ in write_ports]
        operands += [t.ref for _, _, _, t in write_ports]
        operands.append(clk.ref)
        addr_t, data_t, strb_t = str(addr_ty), str(data_ty), str(strb_ty)
        op_types = ["i1"] * rp + [addr_t] * rp
        op_types += ["i1"] * wp + [addr_t] * wp + [data_t] * wp + [strb_t] * wp + ["!pyc.clock"]
        attrs = (
            f'{{depth = {int(depth)}, name = "{name}", rd_ports = {rp}, wr_ports = {wp}, '
            f'read_mode = "{read_mode}", '
            f'operandSegmentSizes = array<i32: {rp}, {rp}, {wp}, {wp}, {wp}, {wp}, 1>}}'
        )
        res_ty = data_t if rp == 1 else "(" + ", ".join([data_t] * rp) + ")"
        self._emit(
            f"{', '.join(outs)} = \"pyc.sync_mem_nm\"({', '.join(operands)}) "
            + f"{attrs} : ({', '.join(op_types)}) -> {res_ty}"
        )
        return [Signal(ref=o, ty=data_ty) for o in outs]

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

    # --- vector lane lowering ---
    def lanes_of(self, sig: Signal) -> LaneInfo | None:
        """Lane registry for a vector signal (``None`` for scalar signals)."""
        if not isinstance(sig.ty, Vector):
            return None
        info = self._lanes.get(sig.ref)
        if info is None:
            raise _design_error(
                f"vector signal {sig.ref} ({sig.ty}) has no lane registry; "
                "every vector value must come from a lowered vector operation"
            )
        return info

    def mk_vec(self, shape, leaf, refs) -> Signal:
        """Register a vector tag over already-emitted scalar lane refs (no emit)."""
        lane_shape = tuple(int(d) for d in shape)
        lane_refs = tuple(str(r) for r in refs)
        if len(lane_refs) != lane_count(lane_shape):
            raise _design_error(
                f"vector lane count mismatch for {lane_shape}: expected {lane_count(lane_shape)}, "
                f"got {len(lane_refs)}"
            )
        ref = self._get_next_temp_var()
        self._lanes[ref] = LaneInfo(shape=lane_shape, leaf=leaf, refs=lane_refs)
        return Signal(ref=ref, ty=Vector.from_shape(list(lane_shape), leaf))

    def pack_lanes(self, shape, leaf, refs) -> Signal:
        """Pack flat row-major lanes into one integer bus (lane 0 at the LSBs)."""
        lane_shape = tuple(int(d) for d in shape)
        lane_refs = tuple(str(r) for r in refs)
        lane_leaf = Bits(int(leaf.width))
        if len(lane_refs) != lane_count(lane_shape):
            raise _design_error(
                f"pack_lanes lane count mismatch for {lane_shape}: got {len(lane_refs)}"
            )
        out_ty = Bits(int(lane_leaf.width) * lane_count(lane_shape))
        tmp = self._get_next_temp_var()
        ordered = list(reversed(lane_refs))  # pyc.concat is MSB-first
        op_list = ", ".join(ordered)
        ty_list = ", ".join(str(lane_leaf) for _ in ordered)
        self._emit(f"{tmp} = pyc.concat ({op_list}) : ({ty_list}) -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def unpack_lanes(self, ref: str, shape, leaf) -> Signal:
        """Split one packed integer bus into per-lane scalar refs + a vector tag."""
        lane_shape = tuple(int(d) for d in shape)
        lane_leaf = Bits(int(leaf.width))
        total_ty = Bits(int(lane_leaf.width) * lane_count(lane_shape))
        refs: list[str] = []
        for i in range(lane_count(lane_shape)):
            lsb = i * int(lane_leaf.width)
            msb = lsb + int(lane_leaf.width) - 1
            tmp = self._get_next_temp_var()
            self._emit(
                f"{tmp} = pyc.extract {ref} {{lsb = {lsb}, msb = {msb}}} : {total_ty} -> {lane_leaf}"
            )
            refs.append(tmp)
        return self.mk_vec(lane_shape, lane_leaf, refs)

    def _packed_result_refs(self) -> list[str]:
        if self._packed_result_refs_cache is None:
            refs: list[str] = []
            for _, sig in self._results:
                info = self.lanes_of(sig)
                if info is None:
                    refs.append(sig.ref)
                else:
                    refs.append(self.pack_lanes(info.shape, info.leaf, info.refs).ref)
            self._packed_result_refs_cache = refs
        return self._packed_result_refs_cache

    # --- emission ---
    def emit_func_mlir(self) -> str:
        if not self._finalized:
            self._finalized = True
            for fn in list(self._finalizers):
                fn()
            # After user/queue finalizers so late assigns can still be folded.
            self._after_finalizers()

        arg_sig = ", ".join(f"{sig.ref}: {port_mlir_type(sig.ty)}" for _, sig in self._args)
        res_types = [port_mlir_type(v.ty) for _, v in self._results]
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
        # Pack vector results first: the concat ops must land in the body text.
        outs = ", ".join(self._packed_result_refs())
        body = "\n".join(self._lines)
        if outs:
            tail = f"\n  func.return {outs} : {ret_ty}\n}}\n"
        else:
            tail = "\n  func.return\n}\n"
        return header + body + tail

    def emit_mlir(self) -> str:
        return "module {\n" + self.emit_func_mlir() + "}\n"

    # --- finalizers ---
    def _after_finalizers(self) -> None:
        """Hook for Circuit to emit folded assigns after other finalizers."""
        return

    def add_finalizer(self, fn: Callable[[], None]) -> None:
        if self._finalized:
            raise RuntimeError("cannot add finalizers after emit_mlir()")
        self._finalizers.append(fn)

    # --- internals ---
    def _arg(self, name: str, ty: Data) -> Signal:
        ref = f"%{name}"
        s = Signal(ref=ref, ty=ty)
        self._args.append((name, s))
        if isinstance(ty, Vector):
            # The port stays one packed scalar; its lanes are extracted up front.
            lane_shape = tuple(int(d) for d in ty.shape())
            lane_leaf = ty.datatype()
            total_ty = port_mlir_type(ty)
            lane_refs: list[str] = []
            for i in range(lane_count(lane_shape)):
                lsb = i * int(lane_leaf.width)
                msb = lsb + int(lane_leaf.width) - 1
                tmp = self._get_next_temp_var()
                self._emit(
                    f"{tmp} = pyc.extract {ref} {{lsb = {lsb}, msb = {msb}}} : {total_ty} -> {lane_leaf}"
                )
                lane_refs.append(tmp)
            self._lanes[ref] = LaneInfo(shape=lane_shape, leaf=lane_leaf, refs=tuple(lane_refs))
        return s

    def _get_next_temp_var(self) -> str:
        self._temp_var_index += 1
        return f"%v{self._temp_var_index}"

    def _emit(self, line: str) -> None:
        if self._lanes:
            # Watchdog: every vector value must be lowered to scalar lanes before
            # it reaches the IR, otherwise the emitted SSA would be undefined.
            for token in _REF_TOKEN_RE.findall(line):
                if token in self._lanes:
                    raise _design_error(
                        f"vector tag {token} reached the emitted IR without lane lowering: "
                        f"{line.strip()!r}"
                    )
            if "vector<" in line:
                raise _design_error(f"vector type reached the emitted IR: {line.strip()!r}")
        self._lines.append(("  " * self._indent_level) + line)

    @staticmethod
    def _require_same_ty(a: Data, b: Data, op: str) -> None:
        if a != b:
            raise TypeError(f"{op} requires same types, got {a} and {b}")
