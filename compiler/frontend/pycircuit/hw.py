from __future__ import annotations

import builtins
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import inspect
import json
from typing import Any, Generator, Generic, Iterable, Iterator, Literal, Mapping, Sequence, TypeVar, Union, cast, overload

from .connectors import (
    Connector,
    ConnectorBundle,
    ConnectorError,
    ConnectorStruct,
    ModuleCollectionHandle,
    ModuleInstanceHandle,
    RegConnector,
    WireConnector,
    is_connector_bundle,
    is_connector_struct,
)
from .data import Bits, DT, Clock, Data, Reset, Vector
from .design import DesignError
from .dsl import Module, Signal, is_bits_signal, is_vector_signal
from .literals import LiteralValue, infer_literal_width
VT = TypeVar("VT", bound=Data)


def _coerce_literal_width(
    lit: LiteralValue,
    *,
    ctx_width: int | None,
    ctx_signed: bool | None,
) -> tuple[int, bool]:
    signed = bool(lit.signed) if lit.signed is not None else bool(ctx_signed)
    if lit.width is not None:
        return int(lit.width), signed
    if ctx_width is not None:
        return int(ctx_width), signed
    return infer_literal_width(int(lit.value), signed=signed), signed


def _normalize_as_values(args: tuple, values: object) -> list | None:
    """Normalize ``Wire.as_`` value-set inputs (positional varargs or ``values=``).

    Returns a list of raw values (``as_(2)`` / ``as_(2, 3)`` / ``as_([2, 3])`` /
    ``as_(values=[2, 3])``) or ``None`` when no value-set was given. Raises if
    both positional and ``values=`` are supplied.
    """
    if args and values is not None:
        raise TypeError("as_: pass values positionally or via values=, not both")
    if args:
        raw = args
    elif values is not None:
        raw = (values,)
    else:
        return None
    if len(raw) == 1 and not isinstance(raw[0], int):
        return list(raw[0])  # a single iterable, e.g. as_([2, 3]) / values=[2, 3]
    return list(raw)


def _normalize_shape_arg(shape: int | tuple[int, ...] | list[int]) -> tuple[int, ...]:
    # Normalize public shape arguments for vector ports/state. Accept a bare int
    # for 1-D convenience and tuple/list for callers that already carry a shape.
    if isinstance(shape, int):
        dims = (int(shape),)
    elif isinstance(shape, (tuple, list)):
        dims = tuple(int(d) for d in shape)
    else:
        raise TypeError(f"shape must be int, tuple[int, ...], or list[int], got {type(shape).__name__}")
    if not dims:
        raise ValueError("shape cannot be empty")
    for d in dims:
        if d <= 0:
            raise ValueError(f"shape dimensions must be > 0, got {dims}")
    return dims


@dataclass(frozen=True, eq=False)
class Wire(Generic[DT]):
    m: Module
    sig: Signal[DT]
    signed: bool = False
    # True if this Wire originates from `pyc.wire` and is intended to be driven
    # by `pyc.assign` (SSA backedge placeholder). JIT debug aliasing must not
    # wrap such wires in `pyc.alias`, because `pyc.assign` destinations must be
    # defined by `pyc.wire`.
    assignable: bool = False

    @property
    def ref(self) -> str:
        return self.sig.ref

    @property
    def ty(self) -> DT:
        return self.sig.ty

    @property
    def width(self) -> int:
        return self.sig.width

    def __str__(self) -> str:
        return self.sig.ref

    def __bool__(self) -> bool:
        raise TypeError(
            "Wire cannot be used as a Python boolean. "
            "Use `if` inside a JIT-compiled design function, or compare explicitly and return an i1 Wire."
        )

    def out(self) -> "Wire[DT]":
        """Stage-friendly sugar: a Wire's value is itself."""
        return self

    # -- vector operations ----------------------------------------------------

    def __len__(self) -> int:
        if not isinstance(self.sig.ty, Vector):
            raise TypeError(f"len(Wire) requires Vector type, got {self.sig.ty!r}")
        return self.sig.ty.length

    def __iter__(self) -> Iterator[Wire]:
        if not is_vector_signal(self.sig):
            raise TypeError(f"iter(Wire) requires Vector type, got {self.sig.ty!r}")
        for i in range(self.sig.ty.length):
            yield Wire(self.m, self.m.v_get(self.sig, index=i), signed=self.signed)

    @overload
    def reduce_or(self: "Wire[Vector[Data]]", *, dim: None = None, mode: str = "chain") -> "Wire[Bits]": ...

    @overload
    def reduce_or(self: "Wire[Vector[Data]]", *, dim: int, mode: str = "chain") -> "Wire[Data]": ...

    def reduce_or(self, *, dim: int | None = None, mode: str = "chain") -> "Wire[Data]":
        if not is_vector_signal(self.sig):
            raise TypeError(f"reduce_or requires Vector type, got {self.sig.ty!r}")
        return Wire(self.m, self.m.v_or_reduce(self.sig, dim=dim, mode=mode))

    @overload
    def reduce_and(self: "Wire[Vector[Data]]", *, dim: None = None, mode: str = "chain") -> "Wire[Bits]": ...

    @overload
    def reduce_and(self: "Wire[Vector[Data]]", *, dim: int, mode: str = "chain") -> "Wire[Data]": ...

    def reduce_and(self, *, dim: int | None = None, mode: str = "chain") -> "Wire[Data]":
        if not is_vector_signal(self.sig):
            raise TypeError(f"reduce_and requires Vector type, got {self.sig.ty!r}")
        return Wire(self.m, self.m.v_and_reduce(self.sig, dim=dim, mode=mode))

    @overload
    def reduce_sum(
        self: "Wire[Vector[Data]]",
        *,
        dim: None = None,
        mode: str = "chain",
    ) -> "Wire[Bits]": ...

    @overload
    def reduce_sum(
        self: "Wire[Vector[Data]]",
        *,
        dim: int,
        mode: str = "chain",
    ) -> "Wire[Data]": ...

    def reduce_sum(
        self,
        *,
        dim: int | None = None,
        mode: str = "chain",
    ) -> "Wire[Data]":
        """Sum reduction via ``pyc.v_add_reduce``.

        The result preserves the vector element width; overflow wraps at that
        width.
        - ``dim=None`` reduces across every vector dimension and returns one scalar Wire.
        - ``dim=int`` reduces along that axis, returning a lowered-rank Vector Wire.
        """
        if not is_vector_signal(self.sig):
            raise TypeError(f"reduce_sum requires Vector type, got {self.sig.ty!r}")
        shape = self.sig.ty.shape()
        if dim is not None and (dim < 0 or dim >= len(shape)):
            raise ValueError(f"reduce_sum dim out of range: {dim} for Vector rank {len(shape)}")
        if not isinstance(self.sig.ty.datatype(), Bits):
            raise TypeError(f"reduce_sum requires Bits element type, got {self.sig.ty.datatype()!r}")

        red_sig = self.m.v_add_reduce(self.sig, dim=dim, mode=mode)
        return Wire(self.m, red_sig)

    def broadcast(
        self: "Wire[Vector[VT]]", *, size: int, dim: int
    ) -> "Wire[Vector[Vector[VT]]]":
        if not is_vector_signal(self.sig):
            raise TypeError(f"broadcast requires Vector type, got {self.sig.ty!r}")
        return Wire(self.m, self.m.v_broadcast_dim(self.sig, size=size, dim=dim))

    @classmethod
    def as_wire(cls, v: Union[Connector, Wire, Reg, Signal, int, list, LiteralValue], *, width: int | None = None, signed: bool | None = None, m: Module | None = None) -> Wire:
        if isinstance(v, Connector):
            v = v.read()
        if isinstance(v, Reg):
            v = v.q
        if isinstance(v, Wire):
            if v.m is not m:
                raise ValueError("cannot combine wires from different modules")
            return v
        # below need a non-None module
        if m is None:
            raise ValueError("as_wire requires a non-None module for non-Wire values")
        if isinstance(v, Signal):
            return Wire(m, v)
        if isinstance(v, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(v, ctx_width=width, ctx_signed=v.signed)
            const_sig = Module.const(m, int(v.value), width=int(lit_w))
            return Wire(m, const_sig, signed=lit_signed)
        if isinstance(v, int) or isinstance(v, list):
            if width is None:
                raise ValueError("as_wire requires a width for int or list values")
            const_sig = Module.const(m, v, width=int(width))
            has_neg = lambda x: x < 0 if isinstance(x, int) else any(has_neg(e) for e in x)
            return Wire(m, const_sig, signed=has_neg(v))
        raise TypeError(f"unsupported operand type: {type(v).__name__}")
    
    def _as_wire(self, v: Union[Connector, Wire, Reg, Signal, int, list, LiteralValue], *, width: int | None) -> "Wire":
        if width is None:
            width = self.width
        return self.as_wire(v, width=width, signed=None, m=self.m)

    def _promote2(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> tuple["Wire", "Wire"]:
        """Promote operands to a common width (extend smaller operand).

        For Vector types no width promotion is performed: vector-vector ops
        require matching shape and element datatype (validated here), and
        scalar operands are broadcast to the vector's leaf element width.
        """
        a = self._as_wire(self, width=None)
        if isinstance(other, int):
            b = self._as_wire(int(other), width=a.width if isinstance(a.sig.ty, Bits) else None)
        else:
            b = self._as_wire(other, width=None)
        
        out_w = max(a.width, b.width)
        if a.width != out_w:
            a = a.sext(width=out_w) if a.signed else a.zext(width=out_w)
        if b.width != out_w:
            b = b.sext(width=out_w) if b.signed else b.zext(width=out_w)
        return a, b

    def __add__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.add(a.sig, b.sig), signed=(a.signed or b.signed))

    def __radd__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self.__add__(other)

    def __sub__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.sub(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rsub__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        b = self._as_wire(self, width=None)
        a = self._as_wire(other, width=b.width if isinstance(b.sig.ty, Bits) else None)
        aa, bb = a._promote2(b) if isinstance(a, Wire) else (a, b)
        return Wire(self.m, self.m.sub(aa.sig, bb.sig), signed=(aa.signed or bb.signed))

    def __mul__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.mul(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rmul__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self.__mul__(other)

    def __rfloordiv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        num = self._as_wire(other, width=None)
        return num.__floordiv__(self)

    def __floordiv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.sdiv(a.sig, b.sig), signed=True)
        return Wire(self.m, self.m.udiv(a.sig, b.sig), signed=False)

    def __rtruediv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        raise TypeError("hardware `/` division is not supported; use `//` for integer division")

    def __truediv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        raise TypeError("hardware `/` division is not supported; use `//` for integer division")

    def __rmod__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        num = self._as_wire(other, width=None)
        return num.__mod__(self)

    def __mod__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.srem(a.sig, b.sig), signed=True)
        return Wire(self.m, self.m.urem(a.sig, b.sig), signed=False)

    def __and__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.and_(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rand__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self.__and__(other)

    def __or__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.or_(a.sig, b.sig), signed=(a.signed or b.signed))

    def __ror__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self.__or__(other)

    def __xor__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.xor(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rxor__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self.__xor__(other)

    def __invert__(self) -> "Wire":
        return Wire(self.m, self.m.not_(self.sig), signed=self.signed)

    def __lshift__(self, other: Union[int, "Wire"]) -> "Wire":
        return self.shl(amount=other)

    def lshr(self, *, amount: Union[int, "Wire", "Reg", Signal, LiteralValue]) -> "Wire":
        """Logical shift right by an immediate or dynamic amount (zero-fill)."""
        if isinstance(amount, (int, LiteralValue)):
            amt = int(amount) if isinstance(amount , int) else int(amount.value)
            if amt < 0:
                raise ValueError("lshr amount must be >= 0")
            return Wire(self.m, self.m.lshri(self.sig, amount=amt), signed=False)
        amt = self._as_wire(amount, width=None)
        return Wire(self.m, self.m.lshr(self.sig, amt.sig), signed=False)

    def ashr(self, *, amount: Union[int, "Wire", "Reg", Signal, LiteralValue]) -> "Wire":
        """Arithmetic shift right by an immediate or dynamic amount (sign-fill)."""
        if isinstance(amount, (int, LiteralValue)):
            amt = int(amount) if isinstance(amount, int) else int(amount.value)
            if amt < 0:
                raise ValueError("ashr amount must be >= 0")
            return Wire(self.m, self.m.ashri(self.sig, amount=amt), signed=True)
        amt = self._as_wire(amount, width=None)
        return Wire(self.m, self.m.ashr(self.sig, amt.sig), signed=True)

    def __rshift__(self, other: Union[int, "Wire"]) -> "Wire":
        if self.signed:
            return self.ashr(amount=other)
        return self.lshr(amount=other)

    def __eq__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":  # type: ignore[override]
        if not isinstance(other, (Wire, Reg, Signal, Connector, int, LiteralValue)):
            return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.eq(a.sig, b.sig))

    def __ne__(self, other: object) -> "Wire":  # type: ignore[override]
        if not isinstance(other, (Wire, Reg, Signal, Connector, int, LiteralValue)):
            return NotImplemented
        return ~(self == other)

    def eq(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self == other

    def ne(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self != other

    def ult(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Unsigned less-than compare (result is i1)."""
        a, b = self._promote2(other)
        return Wire(self.m, self.m.ult(a.sig, b.sig))

    def slt(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Signed less-than compare (result is i1)."""
        a, b = self._promote2(other)
        return Wire(self.m, self.m.slt(a.sig, b.sig))

    def __lt__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Less-than compare respecting signed intent (result is i1)."""
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.slt(a.sig, b.sig))
        return Wire(self.m, self.m.ult(a.sig, b.sig))

    def __gt__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Greater-than compare respecting signed intent (result is i1)."""
        other_w = self._as_wire(other, width=None)
        return other_w < self

    def __le__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Less-than-or-equal compare respecting signed intent (result is i1)."""
        return ~(self > other)

    def __ge__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Greater-than-or-equal compare respecting signed intent (result is i1)."""
        return ~(self < other)

    def ugt(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Unsigned greater-than compare (result is i1)."""
        other_w = self._as_wire(other, width=None)
        return other_w.ult(self)

    def ule(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Unsigned less-than-or-equal compare (result is i1)."""
        return ~self.ugt(other)

    def uge(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Unsigned greater-than-or-equal compare (result is i1)."""
        return ~self.ult(other)

    def sgt(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Signed greater-than compare (result is i1)."""
        other_w = self._as_wire(other, width=None)
        return other_w.slt(self)

    def sle(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Signed less-than-or-equal compare (result is i1)."""
        return ~self.sgt(other)

    def sge(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        """Signed greater-than-or-equal compare (result is i1)."""
        return ~self.slt(other)

    def _select_internal(self, a: Union["Wire", "Reg", Signal, int, LiteralValue, Connector], b: Union["Wire", "Reg", Signal, int, LiteralValue, Connector]) -> "Wire":
        scalar_selector = self.ty == Bits(1)
        vector_selector = isinstance(self.ty, Vector) and self.ty.datatype() == Bits(1)
        if not scalar_selector and not vector_selector:
            raise TypeError("conditional selection requires an i1 or vector<...xi1> selector wire")

        # At least one operand must provide width.
        if isinstance(a, int) and isinstance(b, int):
            raise TypeError("conditional selection requires at least one Wire/Reg/Signal operand (cannot infer width from two ints)")

        aw_temp = self._as_wire(a, width=None)
        bw_temp = self._as_wire(b, width=None)
        
        out_w = max(aw_temp.width, bw_temp.width)
        
        aw = self._as_wire(a, width=out_w)
        bw = self._as_wire(b, width=out_w)

        if aw.width != out_w:
            aw = aw.sext(width=out_w) if aw.signed else aw.zext(width=out_w)
        if bw.width != out_w:
            bw = bw.sext(width=out_w) if bw.signed else bw.zext(width=out_w)
        if vector_selector:
            def broadcast_scalar(value: Wire) -> Wire:
                if isinstance(value.ty, Vector):
                    return value
                return Wire(
                    self.m,
                    self.m.v_broadcast(value.sig, size=self.ty.length),
                    signed=value.signed,
                )

            aw = broadcast_scalar(aw)
            bw = broadcast_scalar(bw)
            if not isinstance(aw.ty, Vector) or aw.ty.shape() != self.ty.shape():
                raise TypeError("vector conditional selection requires selector and values with matching vector shapes")
        return Wire(self.m, self.m.mux(self.sig, aw.sig, bw.sig), signed=(aw.signed or bw.signed))

    def select(self, a: Union["Wire", "Reg", Signal, int, LiteralValue], b: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self._select_internal(a, b)

    def trunc(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.trunc(self.sig, width=width), signed=self.signed)

    def zext(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.zext(self.sig, width=width), signed=False)

    def sext(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.sext(self.sig, width=width), signed=True)

    def slice(self, *, lsb: int, width: int) -> "Wire":
        return Wire(self.m, self.m.extract(self.sig, lsb=lsb, width=width), signed=False)

    def lane(self, idx: int, *, width: int) -> "Wire":
        """ASL scaled slice ``x[idx *: width]`` — element-granular access.

        Equivalent to ``x[idx*width : (idx+1)*width]`` (``x.slice(lsb=idx*width,
        width=width)``). ``idx`` is an elaboration-time Python ``int``; lane
        ``idx`` occupies bits ``[idx*width, idx*width+width-1]``.
        """
        i = int(idx)
        w = int(width)
        if w <= 0:
            raise ValueError("lane(width=) must be > 0")
        if i < 0:
            raise ValueError("lane index must be >= 0")
        lsb = i * w
        if lsb + w > self.width:
            raise ValueError(
                f"lane {i} (width {w}) out of range for {self.width}-bit signal"
            )
        return self.slice(lsb=lsb, width=w)

    def shl(self, *, amount: Union[int, "Wire", "Reg", Signal, LiteralValue]) -> "Wire":
        """Shift left by an immediate or dynamic amount."""
        if isinstance(amount, int):
            return Wire(self.m, self.m.shli(self.sig, amount=int(amount)), signed=self.signed)
        amt = self._as_wire(amount, width=None)
        return Wire(self.m, self.m.shl(self.sig, amt.sig), signed=self.signed)

    @overload
    def __getitem__(self: "Wire[Vector[VT]]", idx: int) -> "Wire[VT]": ...

    @overload
    def __getitem__(self: "Wire[DT]", idx: int | builtins.slice) -> "Wire[DT]": ...

    def __getitem__(self, idx: int | slice | tuple) -> "Wire[Data]":
        if isinstance(idx, tuple):
            # ASL scaled slice sugar ``x[i, w]`` == ``x.lane(i, width=w)``
            # (element i of a packed vector whose elements are w bits wide).
            if len(idx) != 2:
                raise TypeError("wire lane subscript must be (index, width)")
            return self.lane(idx[0], width=idx[1])
        if is_vector_signal(self.sig):
            if isinstance(idx, slice):
                raise TypeError("Vector Wire indexing does not support slice (use v_get)")
            if not isinstance(idx, int):
                raise TypeError(f"Vector Wire index must be int, got {type(idx).__name__}")
            if idx < 0 or idx >= self.sig.ty.length:
                raise IndexError(f"Vector Wire index {idx} out of range for {self.sig.ty}")
            return Wire(self.m, self.m.v_get(self.sig, index=idx), signed=self.signed)
        if isinstance(idx, slice):
            if idx.step is not None:
                raise TypeError("wire slicing does not support step")
            lsb = 0 if idx.start is None else int(idx.start)
            stop = self.width if idx.stop is None else int(idx.stop)
            if lsb < 0 or stop < 0:
                raise ValueError("wire slice indices must be >= 0")
            if stop < lsb:
                raise ValueError("wire slice stop must be >= start")
            width = stop - lsb
            if width <= 0:
                raise ValueError("wire slice width must be > 0")
            if lsb + width > self.width:
                raise ValueError(f"wire slice out of range: [{lsb}:{stop}] on width {self.width}")
            return self.slice(lsb=lsb, width=width)

        bit = int(idx)
        if bit < 0:
            raise ValueError("wire bit index must be >= 0")
        if bit >= self.width:
            raise ValueError("wire bit index out of range")
        return self.slice(lsb=bit, width=1)

    def named(self, name: str) -> "Wire":
        """Attach a debug name via `pyc.alias` (pure)."""
        scoped = str(name)
        scoped_name = getattr(self.m, "scoped_name", None)
        if callable(scoped_name):
            scoped = scoped_name(scoped)
        return Wire(self.m, self.m.alias(self.sig, name=str(scoped)), signed=self.signed)

    def as_signed(self) -> "Wire":
        """Mark this value as signed for shift/div/compare lowering."""
        return Wire(self.m, self.sig, signed=True)

    def as_unsigned(self) -> "Wire":
        """Mark this value as unsigned for shift/div/compare lowering."""
        return Wire(self.m, self.sig, signed=False)

    def matches(self, pattern: str) -> "Wire":
        """ASL-style bit-mask match: ``(self & mask) == value`` (returns i1).

        ``pattern`` is MSB-first with ``0``/``1`` care bits and ``x``/``-`` (or
        parenthesized bits, e.g. ``'1(0)x0'``) don't-care bits; its width must
        equal this wire's width.
        """
        from .bitmask import parse_bitmask_checked

        mask, value = parse_bitmask_checked(pattern, width=self.width)
        return (self & mask) == value

    def in_(self, *patterns: "str | Iterable[str]") -> "Wire":
        """True iff any pattern matches (OR-reduction of :meth:`matches`).

        Accepts varargs (``in_("1010", "1100")``) or a single iterable
        (``in_(["1010", "1100"])``).
        """
        from .bitmask import normalize_patterns

        pats = normalize_patterns(patterns)
        result = self.matches(pats[0])
        for p in pats[1:]:
            result = result | self.matches(p)
        return result

    def not_in_(self, *patterns: "str | Iterable[str]") -> "Wire":
        """True iff no pattern matches (ASL ``IN !{...}``)."""
        return ~self.in_(*patterns)

    def as_(
        self,
        *args: "int | Iterable[int]",
        width: int | None = None,
        range: tuple[int, int] | None = None,
        values: "Iterable[int] | None" = None,
        msg: str | None = None,
    ) -> "Wire":
        """ASL ``expression as ty`` — a *checked* cast. Exactly one of:

        - **positional value(s)** — ``x.as_(2)`` / ``x.as_(2, 3)`` / ``x.as_([2, 3])``
          asserts ``x`` equals one of the given values, returns ``x`` unchanged.
          Mirrors ASL ``x as integer{2, 3}`` (also spellable ``values=[...]``).
        - ``width=w``  — narrowing to ``w`` bits (asserts truncated-away high bits
          are zero, then ``trunc(w)``; equal width is a no-op, widening rejected).
          Mirrors ASL ``x as bits(w)``.
        - ``range=(lo, hi)`` — asserts ``lo <= x <= hi`` (unsigned), returns ``x``
          unchanged. Mirrors ASL ``x as integer{lo..hi}``.

        The assertion is simulation-time (``pyc.assert``) and droppable in
        synthesis (zero-cost contract); unlike a silent :meth:`trunc` it records
        the intent as a verifiable check.
        """
        val_set = _normalize_as_values(args, values)
        given = [n for n, v in (("width", width), ("range", range), ("values", val_set)) if v is not None]
        if len(given) != 1:
            raise TypeError(
                "as_ requires exactly one of: positional value(s)/values=, width=, or range="
            )
        if val_set is not None:
            return self.assert_in(val_set, msg=msg)
        if width is not None:
            w = int(width)
            if w <= 0:
                raise ValueError("as_(width=) must be > 0")
            if w > self.width:
                raise ValueError(
                    f"as_(width={w}) cannot widen a {self.width}-bit value; use zext/sext"
                )
            if w == self.width:
                return self
            high = self[w:self.width]               # bits that must be zero
            cond = high == 0
            self.m.assert_(cond, msg=msg or f"as_: value does not fit in {w} bits")
            return self.trunc(width=w)
        lo, hi = range  # type: ignore[misc]
        return self.assert_range(lo, hi, msg=msg)

    def assert_fits(self, *, width: int, msg: str | None = None) -> "Wire":
        """Alias for ``as_(width=..)`` (spelled out for the assertion intent)."""
        return self.as_(width=width, msg=msg)

    def assert_range(self, lo: int, hi: int, *, msg: str | None = None) -> "Wire":
        """Assert (unsigned) ``lo <= self <= hi``; returns ``self`` unchanged.

        Bounds trivially satisfied by the signal's width emit no comparison; a
        fully-covering range emits no assertion at all.
        """
        lo_i, hi_i = int(lo), int(hi)
        if lo_i > hi_i:
            raise ValueError(f"assert_range: empty range [{lo_i}, {hi_i}]")
        if lo_i < 0:
            raise ValueError("assert_range: lower bound must be >= 0 (unsigned)")
        maxv = (1 << self.width) - 1
        if hi_i > maxv:
            raise ValueError(
                f"assert_range: upper bound {hi_i} exceeds {self.width}-bit max {maxv}"
            )
        conds: list[Wire] = []
        if lo_i > 0:
            conds.append(self.uge(lo_i))
        if hi_i < maxv:
            conds.append(self.ule(hi_i))
        if conds:
            cond = conds[0]
            for c in conds[1:]:
                cond = cond & c
            self.m.assert_(cond, msg=msg or f"assert_range: value not in [{lo_i}, {hi_i}]")
        return self

    def assert_in(self, values: "Iterable[int]", *, msg: str | None = None) -> "Wire":
        """Assert ``self`` equals one of ``values``; returns ``self`` unchanged."""
        vals = [int(v) for v in values]
        if not vals:
            raise ValueError("assert_in requires at least one value")
        maxv = (1 << self.width) - 1
        for v in vals:
            if v < 0 or v > maxv:
                raise ValueError(
                    f"assert_in: value {v} out of range for {self.width}-bit signal"
                )
        cond = self == vals[0]
        for v in vals[1:]:
            cond = cond | (self == v)
        self.m.assert_(cond, msg=msg or f"assert_in: value not in {sorted(set(vals))}")
        return self


@dataclass(frozen=True)
class ClockDomain:
    clk: Signal[Clock]
    rst: Signal[Reset]


@dataclass(frozen=True, eq=False)
class Reg(Generic[DT]):
    q: Wire[DT]
    clk: Signal
    rst: Signal
    en: Wire
    next: Wire
    init: Wire

    @property
    def ref(self) -> str:
        return self.q.ref

    @property
    def ty(self) -> DT:
        return self.q.ty

    @property
    def width(self) -> int:
        return self.q.width

    def __str__(self) -> str:
        return self.q.ref

    def __bool__(self) -> bool:
        raise TypeError(
            "Reg cannot be used as a Python boolean. "
            "Use `if` inside a JIT-compiled design function, or compare explicitly and return an i1 Wire."
        )

    def out(self) -> Wire[DT]:
        """Read the current value of the register (q) as a Wire."""
        return self.q

    def __add__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q + other

    def __and__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q & other

    def __or__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q | other

    def __xor__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q ^ other

    def __invert__(self) -> Wire:
        return ~self.q

    def __lshift__(self, other: int) -> Wire:
        return self.q << other

    def __rshift__(self, other: int) -> Wire:
        return self.q >> other

    def lshr(self, *, amount: int) -> Wire:
        return self.q.lshr(amount=amount)

    def ashr(self, *, amount: int) -> Wire:
        return self.q.ashr(amount=amount)

    def __eq__(self, other: Union[Wire, "Reg", Signal, int]) -> Wire:
        return self.q == other

    def __ne__(self, other: Union[Wire, "Reg", Signal, int]) -> Wire:
        return self.q != other

    def eq(self, other: Union[Wire, "Reg", Signal, int]) -> Wire:
        return self == other

    def ne(self, other: Union[Wire, "Reg", Signal, int]) -> Wire:
        return self.q.ne(other)

    def __lt__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q < other

    def __gt__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q > other

    def __le__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q <= other

    def __ge__(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q >= other

    def ult(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.ult(other)

    def ugt(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.ugt(other)

    def ule(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.ule(other)

    def uge(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.uge(other)

    def slice(self, *, lsb: int, width: int) -> Wire:
        return self.q.slice(lsb=lsb, width=width)

    def select(self, a: Union[Wire, Reg, Signal, int, LiteralValue], b: Union[Wire, Reg, Signal, int, LiteralValue]) -> Wire:
        return self.q.select(a, b)

    def trunc(self, *, width: int) -> Wire:
        return self.q.trunc(width=width)

    def zext(self, *, width: int) -> Wire:
        return self.q.zext(width=width)

    def sext(self, *, width: int) -> Wire:
        return self.q.sext(width=width)

    def shl(self, *, amount: int) -> Wire:
        return self.q.shl(amount=amount)

    def __getitem__(self, idx: int | builtins.slice) -> Wire:
        return self.q[idx]

    def set(
        self,
        value: Union[Wire, Reg, Signal, Connector, int, LiteralValue],
        *,
        when: Union[Wire, Signal, Connector, int, LiteralValue] = 1,
    ) -> None:
        """Drive `self.next` (backedge) for a stateful variable.

        - `r.set(v)` is equivalent to `m.assign(r.next, v)`
        - `r.set(v, when=cond)` drives `cond ? v : r` (hold otherwise)
        """
        m = self.q.m
        if not isinstance(m, Circuit):
            raise TypeError("Reg.set requires the Reg to belong to a Circuit")
        
        next_w = Wire.as_wire(value, m=m, width=self.width)

        if isinstance(when, int) and int(when) == 1:
            m.assign(self.next, next_w)
            return
        
        cond = Wire.as_wire(when, m=m, width=1)
        if cond.width != 1:
            raise TypeError("when width must be 1")
        when_w = Wire.as_wire(when, m=m)

        m.assign(self.next, when_w._select_internal(value, self.q))

class Circuit(Module):
    """High-level wrapper over `Module` that returns `Wire`/`Reg` objects."""

    def __init__(self, name: str, design_ctx: Any | None = None) -> None:
        super().__init__(name)
        self._scope_stack: list[str] = []
        # Optional multi-module DesignContext (used by `Circuit.instance`).
        self._design_ctx = design_ctx
        # Stable debug exports materialized as module outputs.
        self._debug_exports: dict[str, Signal] = {}
        # Hardened layout metadata (Decision 0125/0143).
        self._hardened_layout_groups: list[dict[str, Any]] = []
        # Hardened probe metadata (Decision 0132/0140).
        # Keyed by exported port name (e.g. "dbg__...").
        self._hardened_probe_table: dict[str, dict[str, Any]] = {}
        # Structural metadata for hierarchy-discipline checks.
        self._struct_instance_count = 0
        self._struct_state_alloc_count = 0
        self._struct_collections: list[dict[str, Any]] = []

    @staticmethod
    def _struct_identity(payload: Any) -> str:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _record_struct_instance(self) -> None:
        self._struct_instance_count += 1

    def _record_struct_state_alloc(self) -> None:
        self._struct_state_alloc_count += 1

    def _record_struct_collection(self, meta: Mapping[str, Any]) -> None:
        self._struct_collections.append(dict(meta))

    def structural_runtime_metadata(self) -> dict[str, Any]:
        collection_instance_count = 0
        module_family_collection_count = 0
        for entry in self._struct_collections:
            collection_instance_count += int(entry.get("key_count", 0))
            if bool(entry.get("from_module_family", False)):
                module_family_collection_count += 1
        return {
            "instance_count": int(self._struct_instance_count),
            "state_alloc_count": int(self._struct_state_alloc_count),
            "collection_count": int(len(self._struct_collections)),
            "collection_instance_count": int(collection_instance_count),
            "module_family_collection_count": int(module_family_collection_count),
            "collections": list(self._struct_collections),
        }

    def _record_hardened_layout_group(self, group: Mapping[str, Any]) -> None:
        """Record a hardened metadata group to be emitted into MLIR attrs."""
        self._hardened_layout_groups.append(dict(group))
        self._materialize_hardened_metadata_attr()

    def _record_hardened_probe(self, *, port: str, meta: Mapping[str, Any]) -> None:
        """Record a hardened probe entry to be emitted into MLIR attrs."""
        p = str(port).strip()
        if not p:
            raise ValueError("probe port must be non-empty")
        self._hardened_probe_table[p] = dict(meta)
        self._materialize_hardened_metadata_attr()

    @staticmethod
    def _normalize_probe_at(at: str | None) -> str:
        raw = "xfer" if at is None else str(at).strip().lower()
        if raw in {"pre"}:
            return "tick"
        if raw in {"post"}:
            return "xfer"
        if raw not in {"tick", "xfer"}:
            raise ValueError("probe `at` must be 'tick' or 'xfer'")
        return raw

    @staticmethod
    def _normalize_probe_tags(tags: Mapping[str, Any] | None) -> dict[str, Any]:
        if not tags:
            return {}
        out: dict[str, Any] = {}
        for k in sorted(tags.keys(), key=lambda x: str(x)):
            kk = str(k).strip()
            if not kk:
                raise ValueError("probe tag keys must be non-empty")
            v = tags[k]
            if v is None:
                continue
            if isinstance(v, (bool, int, str)):
                out[kk] = v
                continue
            out[kk] = str(v)
        return out

    def _materialize_hardened_metadata_attr(self) -> None:
        if not self._hardened_layout_groups and not self._hardened_probe_table:
            return

        layout_table: dict[str, Any] = {}
        layout_names: dict[str, set[str]] = {}
        groups: list[dict[str, Any]] = []
        for g in self._hardened_layout_groups:
            spec = g.get("spec", {})
            if not isinstance(spec, Mapping):
                continue
            layout_id = str(spec.get("layout_id", "")).strip()
            if not layout_id:
                continue

            kind = str(spec.get("kind", "")).strip()
            name = str(spec.get("name", "")).strip()
            layout_names.setdefault(layout_id, set()).add(name or "<unnamed>")

            if layout_id not in layout_table:
                layout_table[layout_id] = {
                    "kind": kind,
                    "total_width": int(spec.get("total_width", 0)),
                    "field_map": spec.get("field_map", {}),
                    "fields": spec.get("fields", []),
                }

            groups.append(
                {
                    "usage": str(g.get("usage", "")),
                    "prefix": str(g.get("prefix", "")),
                    "spec": {"kind": kind, "name": name, "layout_id": layout_id},
                    "ports": dict(g.get("ports", {})),
                }
            )

        # Deterministic ordering independent of frontend call order (Decision 0147).
        for lid, names in layout_names.items():
            entry = layout_table.get(lid)
            if isinstance(entry, dict):
                entry["schema_names"] = sorted(n for n in names if n)

        def group_sort_key(g: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
            usage = str(g.get("usage", ""))
            prefix = str(g.get("prefix", ""))
            spec = g.get("spec", {})
            if isinstance(spec, Mapping):
                skind = str(spec.get("kind", ""))
                sname = str(spec.get("name", ""))
                lid = str(spec.get("layout_id", ""))
            else:
                skind, sname, lid = "", "", ""
            return (usage, prefix, skind, sname, lid)

        payload = {
            "version": 1,
            "layout_table": layout_table,
            "layout_groups": sorted(groups, key=group_sort_key),
            "probe_table": dict(self._hardened_probe_table),
        }
        # Attach as a JSON string attribute for tool-visible, backend-consumable
        # hardened metadata (Decision 0125/0132).
        import json  # local import to keep hw.py import surface small

        hardened_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.set_func_attr("pyc.hardened", hardened_json)

    def scoped_name(self, name: str) -> str:
        if not self._scope_stack:
            return name
        return "__".join([*self._scope_stack, name])

    def scope(self, name: str) -> Generator[None]:
        self._scope_stack.append(str(name))
        try:
            yield
        finally:
            self._scope_stack.pop()

    def domain(self, name: str) -> ClockDomain:
        return ClockDomain(clk=self.clock(f"{name}_clk"), rst=self.reset(f"{name}_rst"))

    def create_domain(self, name: str, *, frequency_desc: str = "", reset_active_high: bool = False) -> Any:
        """V5 cycle-aware domain (next/prev/push/pop); see `pycircuit.v5.CycleAwareDomain`."""
        
        _ = (frequency_desc, reset_active_high)
        from .v5 import CycleAwareDomain
        return CycleAwareDomain(self, str(name))

    @overload
    def input(self, name:str, *, width: int,signed: bool = False, shape: None = None) -> Wire[Bits]: ...

    @overload
    def input(
        self, name: str, *, width: int, signed: bool, shape: list[int]
    ) -> Wire[Vector[Data]]: ...

    def input(  # type: ignore[override]
        self,
        name: str,
        *,
        width: int | None = None,
        signed: bool = False,
        shape: int | tuple[int, ...] | list[int] | None = None,
        fields: Any | None = None,
        enum: Any | None = None,
    ) -> Wire:
        """Declare a module input port.

        Scalar inputs return ``Wire[Bits]``; shaped inputs return
        ``Wire[Vector]`` whose ``Wire[i]`` extracts lane ``i``. Passing
        ``fields=`` (a ``BitfieldSpec`` or a plain ``{name: (msb, lsb)}`` mapping)
        binds the layout and returns a ``BitfieldSignal`` supporting
        ``x["field"]`` / ``x.field`` access; when ``width`` is omitted it is taken
        from the spec (required for a plain mapping). Passing ``enum=`` (a
        ``PycEnum`` subclass) sizes the port to the enum width and returns an
        ``EnumSignal`` supporting ``x.is_(E.MEMBER)`` (type-safe).
        """
        if enum is not None:
            if fields is not None or shape is not None:
                raise TypeError("input(enum=...) cannot be combined with fields=/shape=")
            from .enums import EnumSignal, coerce_enum_cls, enum_width

            enum = coerce_enum_cls(enum)
            ew = enum_width(enum)
            if width is None:
                width = ew
            elif int(width) != ew:
                raise ValueError(
                    f"input width {width} does not match enum {enum.__name__} width {ew}"
                )
            wire = Wire(self, super().input(name, width=width), signed=bool(signed))
            return EnumSignal(enum, wire)
        if fields is not None:
            if shape is not None:
                raise TypeError("input(fields=...) cannot be combined with shape=")
            from .bitfield import coerce_bitfield_spec

            fields = coerce_bitfield_spec(fields, width=width)
            if width is None:
                width = int(fields.width)
            elif int(width) != int(fields.width):
                raise ValueError(
                    f"input width {width} does not match BitfieldSpec width {fields.width}"
                )
        if width is None:
            raise TypeError("input() requires width= (or fields=)")
        # Treat ``None`` and empty shape (``[]``/``()``) alike as a scalar port.
        norm_shape = list(_normalize_shape_arg(shape)) if shape else None
        wire = Wire(self, super().input(name, width=width, shape=norm_shape), signed=bool(signed))
        return fields.bind(wire) if fields is not None else wire

    @overload
    def const(self, value: int, *, width: int, signed: bool = ...) -> Wire[Bits]: ... 

    @overload
    def const(self, value: list[int], *, width: int, signed: bool = ...) -> Wire[Vector[Bits]]: ...

    @overload
    def const(self, value: list[list[int]], *, width: int, signed: bool = ...) -> Wire[Vector[Vector[Bits]]]: ...

    @overload
    def const(self, value: list[Any], *, width: int, signed: bool = ...) -> Wire[Vector[Vector[Vector[Data]]]]: ...

    def const(
        self,
        value: int | list[Any],
        *,
        width: int,
        signed: bool = False,
    ) -> Wire[Bits | Vector]:
        """Create a constant `Wire` (two's complement at `width`).

        - ``int`` → ``Wire[Bits]``
        - ``list`` (possibly nested) → ``Wire[Vector]``
        """
        return Wire(self, super().const(value, width=width), signed=signed)

    def output(self, name: str, value: Union[Wire, Reg, Signal, Connector, int, LiteralValue]) -> None:  # type: ignore[override]
        # Unwrap ASL-alignment wrappers (EnumSignal / BitfieldSignal) and
        # connectors first, then defer to the vector-aware ``as_wire`` path.
        unwrap = getattr(value, "__pyc_unwrap__", None)
        if callable(unwrap):
            value = unwrap()
        if isinstance(value, Connector):
            value = value.read()
        if isinstance(value, LiteralValue):
            lit_w, _ = _coerce_literal_width(value, ctx_width=value.width, ctx_signed=value.signed)
            super().output(name, super().const(int(value.value), width=lit_w))
            return
        if isinstance(value, int):
            w = infer_literal_width(int(value), signed=(int(value) < 0))
            super().output(name, super().const(int(value), width=w))
            return
        value = Wire.as_wire(value, m=self)
        super().output(name, value.sig)

    def new_wire(self, *, width: int) -> Wire[Bits]:
        return Wire(self, super().new_signal(width=width), assignable=True)

    def named_wire(self, name: str, *, width: int) -> Wire:
        return Wire(self, super().new_signal(width=width, name=self.scoped_name(name)), assignable=True)

    def wire(self, sig: Signal) -> Wire:
        return Wire(self, sig)

    def named(self, v: Union[Wire, Reg, Signal], name: str) -> Wire:
        """Attach a scoped debug name via `pyc.alias` (pure)."""
        return Wire(self, self.alias(Signal.as_sig(v), name=self.scoped_name(name)))

    def debug(
        self,
        name: str,
        value: Union[Wire, Reg, Signal, Connector],
        *,
        at: str | None = None,
        tags: Mapping[str, Any] | None = None,
    ) -> Wire:
        _ = (name, value, at, tags)
        raise DesignError("Legacy debug helper was removed; use standalone `@probe(target=...)` definitions instead")

    def debug_bundle(self, prefix: str, fields: Mapping[str, Union[Wire, Reg, Signal, Connector]]) -> dict[str, Wire]:
        _ = (prefix, fields)
        raise DesignError("Legacy debug-bundle helper was removed; use standalone `@probe(target=...)` definitions instead")

    def debug_probe(
        self,
        stage: str,
        lane: int,
        fields: Mapping[str, Union[Wire, Reg, Signal, Connector]],
        *,
        family: str = "pv",
        at: str | None = None,
        tags: Mapping[str, Any] | None = None,
    ) -> dict[str, Wire]:
        _ = (stage, lane, fields, family, at, tags)
        raise DesignError("Legacy debug-probe helper was removed; use standalone `@probe(target=...)` definitions instead")

    def debug_occ(self, stage: str, lane: int, fields: Mapping[str, Union[Wire, Reg, Signal, Connector]]) -> dict[str, Wire]:
        _ = (stage, lane, fields)
        raise DesignError("Legacy occupancy-debug helper was removed; use standalone `@probe(target=...)` definitions instead")

    def probe(
        self,
        value: Any,
        *,
        stage: str,
        lane: int,
        family: str = "pv",
        prefix: str | None = None,
        at: str | None = None,
        tags: Mapping[str, Any] | None = None,
    ) -> dict[str, Wire]:
        _ = (value, stage, lane, family, prefix, at, tags)
        raise DesignError("Legacy probe helper was removed; use standalone `@probe(target=...)` definitions instead")

    def assign(
        self,
        dst: Union[Wire, Reg, Signal, Connector],
        src: Union[Wire, Reg, Signal, Connector, int, LiteralValue],
    ) -> None:
        if isinstance(dst, Connector):
            if isinstance(dst, RegConnector):
                dst.set(src)
                return
            dst = dst.read()
        if isinstance(src, Connector):
            src = src.read()
        
        def is_signed_src(v: Union[Wire, Reg, Signal, int, LiteralValue, Connector]) -> bool:
            if isinstance(v, Wire):
                return bool(v.signed)
            if isinstance(v, Reg):
                return bool(v.q.signed)
            if isinstance(v, Connector):
                return bool(v.read().signed)
            if isinstance(v, LiteralValue):
                if v.signed is not None:
                    return bool(v.signed)
                return int(v.value) < 0
            return False

        dst_sig = Signal.as_sig(dst)
        if isinstance(src, LiteralValue):
            lit_w, _ = _coerce_literal_width(src, ctx_width=dst_sig.ty.width, ctx_signed=is_signed_src(src))
            src_sig = super().const(int(src.value), width=lit_w)
            super().assign(dst_sig, src_sig)
            return
        if isinstance(src, int):
            src_sig = super().const(int(src), width=dst_sig.ty.width)
            super().assign(dst_sig, src_sig)
            return

        src_signed = is_signed_src(src)
        src_sig = Signal.as_sig(src)
        
        if dst_sig.ty == src_sig.ty:
            super().assign(dst_sig, src_sig)
            return

        # Implicit integer resizing for convenience (zext smaller, trunc larger).
        if isinstance(dst_sig.ty, Bits) and isinstance(src_sig.ty, Bits):
            dst_w = dst_sig.ty.width
            src_w = src_sig.ty.width
            if src_w < dst_w:
                src_sig = super().sext(src_sig, width=dst_w) if src_signed else super().zext(src_sig, width=dst_w)
            elif src_w > dst_w:
                src_sig = super().trunc(src_sig, width=dst_w)
            super().assign(dst_sig, src_sig)
            return

        raise TypeError(f"assign requires same types, got {dst_sig.ty} and {src_sig.ty}")

    def assert_(self, cond: Union[Wire, Reg, Signal], *, msg: str | None = None) -> None:
        c = cond.q.sig if isinstance(cond, Reg) else cond
        sig = c.sig if isinstance(c, Wire) else c
        super().assert_(sig, msg=msg)


    @overload
    def out(
        self, 
        name: str, 
        *, 
        clk: Signal[Clock] | None = None,
        rst: Signal[Reset] | None = None,
        domain: ClockDomain | None = None,
        width: int,
        init: Union[Wire, Reg, Signal, int, list, LiteralValue] | None = None,
        en: Union[Wire, Signal, int, LiteralValue] = 1,
        shape: list[int],
        stage: str | None = None,
        signed: bool | None = None,
        generated: str | None = None,
    ) -> Reg[Vector]: ...
    
    @overload
    def out(
        self, 
        name: str, 
        *, 
        clk: Signal[Clock] | None = None,
        rst: Signal[Reset] | None = None,
        domain: ClockDomain | None = None,
        width: int,
        init: Union[Wire, Reg, Signal, int, list, LiteralValue] | None = None,
        en: Union[Wire, Signal, int, LiteralValue] = 1,
        stage: str | None = None,
        signed: bool | None = None,
        generated: str | None = None,
    ) -> Reg: ...
    
    def out(
        self,
        name: str,
        *,
        clk: Signal[Clock] | None = None,
        rst: Signal[Reset] | None = None,
        domain: ClockDomain | None = None,
        width: int,
        init: Union[Wire, Reg, Signal, int, list, LiteralValue] | None = None,
        en: Union[Wire, Signal, int, LiteralValue] = 1,
        shape: list[int] | None = None,
        stage: str | None = None,
        signed: bool | None = None,  # reserved for future type inference / lowering
        generated: str | None = None,
    ) -> Reg:
        """Declare a named stateful variable (backedge register).

        This is a higher-level replacement for `backedge_reg(...)` that:
        - takes a stable logical name (for debug/name mangling),
        - optionally tags the name with a pipeline stage prefix,
        - declares a named backedge wire for `next`.

        With ``shape=...``, ``next``/``q``/``init`` are vectors of that shape.
        Scalar ``init`` is broadcast to ``shape``; ``en`` stays scalar ``i1``
        (shared enable for the whole vector register).
        """
        _ = signed  # unused for now (kept for API stability)
        shape = [] if shape is None else list(shape)

        if domain is not None:
            clk = domain.clk
            rst = domain.rst
        if clk is None or rst is None:
            raise TypeError("out() requires either domain=... or both clk=... and rst=...")

        if shape and not all(isinstance(d, int) and d > 0 for d in shape):
            raise ValueError("shape entries must be all int and all > 0")

        fullname = str(name)
        if stage:
            fullname = f"{stage}__{fullname}"
        fullname = self.scoped_name(fullname)

        next_w = Wire(
            self,
            super().new_signal(
                width=width,
                name=f"{fullname}__next",
                shape=shape,
                generated=generated,
            ),
        )

        # ``pyc.reg`` enable is scalar i1 (shared across vector lanes).
        en_w = Wire.as_wire(en, width=1, m=self)
        
        if en_w.ty != Bits(1):
            raise TypeError(f"out() en must be i1, got {en_w.ty}")

        if init is None:
            init = 0
        init_w = Wire.as_wire(init, m=self, width=width)
        if shape and is_bits_signal(init_w.sig):
            init_sig = cast(Signal[Bits], init_w.sig) 
            init_sig = super().v_broadcast(init_sig, size=shape[0])
            for dim in shape[1:]:
                init_sig = super().v_broadcast_dim(init_sig, size=dim, dim=len(init_sig.ty.shape()))
            init_w = Wire(self, init_sig)
            
        if shape and isinstance(init_w.ty, Vector) and init_w.ty.shape() != shape:
            raise TypeError(f"out() init shape must be {shape}, got {init_w.ty}")

        r = self.reg(clk, rst, en_w, next_w, init_w, generated=generated)

        # Name the observable value of the state variable.
        q_named = Wire(
            self,
            self.alias(r.q.sig, name=fullname, generated=generated),
            signed=r.q.signed,
        )
        return Reg(q=q_named, clk=r.clk, rst=r.rst, en=r.en, next=r.next, init=r.init)

    def reg_wire(
        self,
        clk: Signal,
        rst: Signal,
        en: Union[Wire, Signal],
        next_: Union[Wire, Signal],
        init: Union[Wire, Signal, int, LiteralValue],
    ) -> Reg:
         return self.reg(clk, rst, en, next_, init)

    def reg(
        self,
        clk: Signal,
        rst: Signal,
        en: Union[Wire, Signal],
        next_: Union[Wire, Signal],
        init: Union[Wire, Signal, int, LiteralValue],
        *,
        generated: str | None = None,
    ) -> Reg:
        en = Wire.as_wire(en, m=self)
        next_ = Wire.as_wire(next_, m=self)
        init = Wire.as_wire(init, m=self, width=next_.width)
        self._record_struct_state_alloc()
        q_sig = super().reg(
            clk,
            rst,
            en.sig,
            next_.sig,
            init.sig,
            generated=generated,
        )
        q_w = Wire(self, q_sig)
        return Reg(q=q_w, clk=clk, rst=rst, en=en, next=next_, init=init)

    def backedge_reg(
        self,
        clk: Signal,
        rst: Signal,
        *,
        width: int,
        init: Union[Wire, Signal, int, LiteralValue],
        en: Union[Wire, Signal, int, LiteralValue] = 1,
    ) -> Reg:
        """Create a register whose `next` is a placeholder `pyc.wire` meant to be driven via `pyc.assign`.

        This pattern enables feedback loops (state machines) in a netlist-like style:

        - `r = m.backedge_reg(...)` creates `r.next` as a `pyc.wire`
        - Later: `m.assign(r.next, some_next_value)`
        """
        next_w = self.new_wire(width=width)
        if isinstance(en, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(en, ctx_width=1, ctx_signed=False)
            en_w: Union[Wire, Signal] = Wire(self, super().const(int(en.value), width=lit_w), signed=lit_signed)
        elif isinstance(en, int):
            en_w: Union[Wire, Signal] = self.const(en, width=1)
        else:
            en_w = en
        
        init_w = self.const(init, width=width) if isinstance(init, int) else init
        
        return self.reg(clk, rst, en_w, next_w, init_w)

    def vec(self, *elems: Union[Wire[DT], Reg[DT], Sequence[Union[Wire[DT], Reg[DT]]], int, LiteralValue]) -> Wire[Vector[DT]]:
        """Build a vector Wire from scalar lanes (via ``pyc.v_create``).

        Accepts ``m.vec(w1, w2, w3)`` and ``m.vec([w1, w2, w3])``. Each lane
        may be a ``Wire``/``Reg`` or a hardware literal (``LiteralValue`` via
        ``u()``/``S()``/``s()``) or a plain Python ``int``.

        Width resolution mirrors ``cat``/``+``:
          - If any lane is a Wire/Reg, all Wires must already share an identical
            leaf type (existing strict behavior), and literals are aligned to
            that width/signedness.
          - If every lane is a literal/int, the widest explicit width wins
            (``u(8,1)`` dominates ``u(4,2)``); bare ints are inferred per
            ``infer_literal_width`` and the max across lanes is used so
            ``m.vec(1,2,3)`` resolves to ``vector<3xi2>``.
        Lanes must share uniform signedness.
        """
        if len(elems) == 1 and isinstance(elems[0], list):
            elems = tuple(elems[0])
        elif len(elems) == 1 and isinstance(elems[0], tuple):
            raise TypeError("vec() expects list[Wire], got tuple")
        elif any(isinstance(e, (list, tuple)) for e in elems):
            raise TypeError("vec() expects list[Wire] as its sole sequence argument")
        if not elems:
            raise ValueError("vec() requires at least one element")

        from .literals import infer_literal_width

        # Pass 1: resolve the target lane width/signedness.
        wire_width: int | None = None
        wire_signed: bool | None = None
        lit_width: int | None = None
        lit_signed: bool | None = None
        for e in elems:
            if isinstance(e, (Wire, Reg)):
                w = e.q if isinstance(e, Reg) else e
                if wire_width is None:
                    wire_width = w.width
                    wire_signed = w.signed
                elif w.width != wire_width or w.signed != wire_signed:
                    raise TypeError(
                        f"vec() wire lanes must share identical width/signedness, "
                        f"got {w.width}/{w.signed} vs {wire_width}/{wire_signed}"
                    )
            elif isinstance(e, LiteralValue):
                lw = e.width if e.width is not None else infer_literal_width(int(e.value), signed=bool(e.signed))
                ls = bool(e.signed)
                if lit_width is None or lw > lit_width:
                    lit_width = lw
                if lit_signed is None:
                    lit_signed = ls
            elif isinstance(e, int):
                lw = infer_literal_width(int(e), signed=int(e) < 0)
                ls = int(e) < 0
                if lit_width is None or lw > lit_width:
                    lit_width = lw
                if lit_signed is None:
                    lit_signed = ls
            else:
                raise TypeError(f"vec() lane must be Wire/Reg/int/LiteralValue, got {type(e).__name__}")

        if wire_width is not None:
            resolved_width, resolved_signed = wire_width, wire_signed
        else:
            resolved_width, resolved_signed = lit_width, lit_signed

        # Pass 2: build lane wires, aligning literals/ints to the resolved width.
        wires: list[Wire] = []
        for e in elems:
            if isinstance(e, (Wire, Reg)):
                wires.append(e.q if isinstance(e, Reg) else e)
            elif isinstance(e, LiteralValue):
                wires.append(Wire.as_wire(e, width=resolved_width, signed=resolved_signed, m=self))
            else:  # int
                wires.append(Wire.as_wire(int(e), width=resolved_width, signed=resolved_signed, m=self))

        sigs: list[Signal] = [w.sig for w in wires]
        for sig in sigs:
            if not sig.ty == sigs[0].ty:
                raise TypeError(f"assert all types are same, but got {sig.ty} and {sigs[0].ty}")

        signed_lanes = [w.signed for w in wires]
        if any(signed != signed_lanes[0] for signed in signed_lanes[1:]):
            raise TypeError("vec() requires uniform lane signedness")
        return Wire(self, self.v_create(sigs), signed=signed_lanes[0])

    @overload
    def priority_mux(
        self,
        sels: Wire[Vector[Bits]],
        vals: Wire[Vector[VT]],
        *,
        mode: str = "chain",
        default: Wire[VT] | None = None,
    ) -> Wire[VT]: ...

    @overload
    def priority_mux(
        self,
        sels: Wire[Vector[Data]],
        vals: Wire[Vector[Data]],
        *,
        mode: str = "chain",
        default: Wire[Data] | None = None,
    ) -> Wire[Data]: ...

    def priority_mux(
        self,
        sels: Wire[Vector[Data]],
        vals: Wire[Vector[Data]],
        *,
        mode: str = "chain",
        default: Wire[Data] | None = None,
    ) -> Wire[Data]:  # type: ignore[override]
        """Wire-level wrapper for :meth:`Module.priority_mux`."""
        if not isinstance(sels, Wire) or not isinstance(vals, Wire):
            raise TypeError("priority_mux sels and vals must be Wire values")
        if sels.m is not self or vals.m is not self:
            raise ValueError("priority_mux sels and vals must belong to this Circuit")
        if default is not None and not isinstance(default, Wire):
            raise TypeError("priority_mux default must be a Wire or None")
        if default is not None and default.m is not self:
            raise ValueError("priority_mux default must belong to this Circuit")
        return Wire(
            self,
            super().priority_mux(
                sels.sig,
                vals.sig,
                mode=mode,
                default=None if default is None else default.sig,
            ),
        )

    def cat(self, *elems: Union["Wire", "Reg", int, LiteralValue]) -> Wire:
        """Concatenate values into a packed bus (MSB-first)."""
        if not elems:
            raise ValueError("cat() requires at least one element")
        sigs: list[Signal] = [Wire.as_wire(v=e, m=self).sig for e in elems]
        return Wire(self, super().concat(*sigs))

    def bundle(self, **fields: Union["Wire", "Reg"]) -> "Bundle":
        return Bundle(fields)

    def as_connector(
        self,
        value: Union[Connector, Wire, Reg, Signal, LiteralValue, int],
        *,
        name: str | None = None,
    ) -> Connector:
        if isinstance(value, Connector):
            if value.owner is not self:
                raise ConnectorError("connector belongs to a different Circuit")
            return value
        if isinstance(value, Reg):
            if value.q.m is not self:
                raise ConnectorError("reg belongs to a different Circuit")
            return RegConnector(owner=self, name=str(name or value.ref), reg=value)
        if isinstance(value, Wire):
            if value.m is not self:
                raise ConnectorError("wire belongs to a different Circuit")
            return WireConnector(owner=self, name=str(name or value.ref), wire=value)
        if isinstance(value, Signal):
            return WireConnector(owner=self, name=str(name or value.ref), wire=value)
        if isinstance(value, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(value, ctx_width=value.width, ctx_signed=value.signed)
            w = Wire(self, Module.const(self, int(value.value), width=int(lit_w)), signed=lit_signed)
            return WireConnector(owner=self, name=str(name or f"lit_{int(value.value)}"), wire=w)
        if isinstance(value, int):
            ww = infer_literal_width(int(value), signed=(int(value) < 0))
            w = self.const(int(value), width=ww)
            return WireConnector(owner=self, name=str(name or f"lit_{int(value)}"), wire=w)
        raise ConnectorError(f"expected Connector/Wire/Reg/Signal/int/literal, got {type(value).__name__}")

    def input_connector(self, name: str, *, width: int, signed: bool = False) -> WireConnector:
        w = self.input(str(name), width=width, signed=signed)
        return WireConnector(owner=self, name=str(name), wire=w)

    def output_connector(
        self,
        name: str,
        value: Union[Connector, Wire, Reg, Signal, None] = None,
        *,
        width: int | None = None,
    ) -> Connector:
        if value is None:
            if width is None:
                raise TypeError("output_connector() requires `value` or `width`")
            w = self.named_wire(str(name), width=int(width))
            self.output(str(name), w)
            return WireConnector(owner=self, name=str(name), wire=w)
        c = self.as_connector(value, name=str(name))
        self.output(str(name), c)
        return c

    def reg_connector(
        self,
        name: str,
        *,
        clk: Signal | None = None,
        rst: Signal | None = None,
        domain: ClockDomain | None = None,
        width: int,
        init: Union[Wire, Reg, Signal, int, LiteralValue] = 0,
        en: Union[Wire, Signal, int, LiteralValue] = 1,
        stage: str | None = None,
    ) -> RegConnector:
        r = self.out(
            str(name),
            clk=clk,
            rst=rst,
            domain=domain,
            width=width,
            init=init,
            en=en,
            stage=stage,
        )
        return RegConnector(owner=self, name=str(name), reg=r)

    def bundle_connector(self, **fields: Union[Connector, Wire, Reg, Signal]) -> ConnectorBundle:
        out: dict[str, Connector] = {}
        for k, v in fields.items():
            out[str(k)] = self.as_connector(v, name=str(k))
        return ConnectorBundle(out)

    def connect(
        self,
        dst: Connector | ConnectorBundle | ConnectorStruct,
        src: Connector | ConnectorBundle | ConnectorStruct | Wire | Reg | Signal,
        *,
        when: Union[Wire, Signal, int, LiteralValue] = 1,
    ) -> None:
        if isinstance(dst, ConnectorStruct):
            if not isinstance(src, ConnectorStruct):
                raise ConnectorError("struct connect requires ConnectorStruct source")
            dkeys = set(dst.keys())
            skeys = set(src.keys())
            if dkeys != skeys:
                missing = sorted(dkeys - skeys)
                extra = sorted(skeys - dkeys)
                parts: list[str] = []
                if missing:
                    parts.append("missing: " + ", ".join(missing))
                if extra:
                    parts.append("extra: " + ", ".join(extra))
                raise ConnectorError(f"struct connect key mismatch ({'; '.join(parts)})")
            dflat = dst.flatten()
            sflat = src.flatten()
            for k in sorted(dkeys):
                self.connect(dflat[k], sflat[k], when=when)
            return

        if isinstance(dst, ConnectorBundle):
            if not isinstance(src, ConnectorBundle):
                raise ConnectorError("bundle connect requires ConnectorBundle source")
            dkeys = set(dst.keys())
            skeys = set(src.keys())
            if dkeys != skeys:
                missing = sorted(dkeys - skeys)
                extra = sorted(skeys - dkeys)
                parts: list[str] = []
                if missing:
                    parts.append("missing: " + ", ".join(missing))
                if extra:
                    parts.append("extra: " + ", ".join(extra))
                raise ConnectorError(f"bundle connect key mismatch ({'; '.join(parts)})")
            for k in sorted(dkeys):
                self.connect(dst[k], src[k], when=when)
            return

        d = self.as_connector(dst)
        s = self.as_connector(src) if not isinstance(src, Connector) else self.as_connector(src)

        if isinstance(d, RegConnector):
            d.set(s.read(), when=when)
            return
        if not (isinstance(when, int) and int(when) == 1):
            raise ConnectorError("conditional connect (`when=...`) is only supported for RegConnector destinations")
        self.assign(d.read(), s.read())

    def inputs(self, spec: Any, *, prefix: str | None = None) -> ConnectorBundle | ConnectorStruct:
        """Declare connector-backed input ports from a spec."""
        from .wiring.connect import inputs

        return inputs(self, spec, prefix=prefix)

    def io(self, sig: Any, *, prefix: str | None = None) -> ConnectorStruct:
        """Declare a mixed-direction IO interface from a signature spec.

        Returns a `ConnectorStruct` keyed by signature leaf path (dotted).
        """

        from .spec.types import SignatureSpec

        if not isinstance(sig, SignatureSpec):
            raise TypeError(f"io() expects SignatureSpec, got {type(sig).__name__}")
        pfx = "" if prefix is None else str(prefix)
        shape = sig.as_struct()

        flat: dict[str, Connector] = {}
        for leaf in sig.leaves:
            pname = str(leaf.path).replace(".", "_")
            port = f"{pfx}{pname}"
            if leaf.direction == "in":
                flat[leaf.path] = self.input_connector(port, width=int(leaf.width), signed=bool(leaf.signed))
                continue

            # Output port placeholder with signedness tracking on the type.
            w_sig = self.new_signal(width=int(leaf.width), name=self.scoped_name(port))
            if leaf.signed:
                w_sig = Signal(ref=w_sig.ref, ty=w_sig.ty.as_signed())
            w = Wire(self, w_sig)
            self.output(port, w)
            flat[leaf.path] = WireConnector(owner=self, name=port, wire=w)

        return ConnectorStruct.from_flat(flat, spec=shape)

    def outputs(
        self,
        spec: Any,
        values: ConnectorBundle | ConnectorStruct | Mapping[str, Any],
        *,
        prefix: str | None = None,
    ) -> ConnectorBundle | ConnectorStruct:
        """Declare connector-backed output ports from a spec."""
        from .wiring.connect import outputs

        return outputs(self, spec, values, prefix=prefix)

    def state(
        self,
        spec: Any,
        *,
        clk: Connector | Signal,
        rst: Connector | Signal,
        prefix: str | None = None,
        init: Mapping[str, Any] | Any = 0,
        en: Connector | Signal | int | LiteralValue = 1,
    ) -> ConnectorBundle | ConnectorStruct:
        """Declare state register connectors from a spec."""
        from .wiring.connect import state

        return state(
            self,
            spec,
            clk=clk,
            rst=rst,
            prefix=prefix,
            init=init,
            en=en,
        )

    def pipe(
        self,
        spec: Any,
        src_values: ConnectorBundle | ConnectorStruct | Mapping[str, Any],
        *,
        clk: Connector | Signal,
        rst: Connector | Signal,
        en: Connector | Signal | int | LiteralValue = 1,
        flush: Connector | Signal | int | LiteralValue | None = None,
        prefix: str | None = None,
        init: Mapping[str, Any] | Any = 0,
    ) -> ConnectorBundle | ConnectorStruct:
        """Register a stage payload and connect inputs with optional flush."""
        regs = self.state(spec, clk=clk, rst=rst, prefix=prefix, init=init, en=en)

        if isinstance(regs, ConnectorStruct):
            if not isinstance(src_values, ConnectorStruct):
                if isinstance(src_values, Mapping):
                    src = ConnectorStruct(src_values)
                else:
                    raise ConnectorError("pipe(struct): source must be ConnectorStruct or mapping")
            else:
                src = src_values
            self.connect(regs, src, when=en)
            if flush is not None:
                for _, r in regs.items():
                    if isinstance(r, RegConnector):
                        r.set(0, when=flush)
            return regs

        src_map: Mapping[str, Any]
        if isinstance(src_values, ConnectorBundle):
            src_map = {k: v for k, v in src_values.items()}
        elif isinstance(src_values, Mapping):
            src_map = dict(src_values)
        else:
            raise ConnectorError("pipe(bundle): source must be ConnectorBundle or mapping")

        dkeys = set(regs.keys())
        skeys = set(str(k) for k in src_map.keys())
        missing = sorted(dkeys - skeys)
        extra = sorted(skeys - dkeys)
        if missing or extra:
            parts: list[str] = []
            if missing:
                parts.append("missing: " + ", ".join(missing))
            if extra:
                parts.append("extra: " + ", ".join(extra))
            raise ConnectorError(f"pipe key mismatch ({'; '.join(parts)})")

        for key in sorted(dkeys):
            self.connect(regs[key], self.as_connector(src_map[key], name=key), when=en)
        if flush is not None:
            for key in sorted(dkeys):
                r = regs[key]
                if isinstance(r, RegConnector):
                    r.set(0, when=flush)
        return regs

    def new(
        self,
        fn: Any,
        *,
        name: str,
        bind: Mapping[str, Connector | ConnectorBundle | ConnectorStruct | Mapping[str, Any] | Any],
        params: dict[str, Any] | None = None,
        module_name: str | None = None,
        short_name: str | None = None,
    ) -> ModuleInstanceHandle:
        """Instantiate a module from connector/spec bindings."""
        from .wiring.connect import ports

        bound_ports = ports(self, bind)
        return self.instance_handle(
            fn,
            name=str(name),
            params=params,
            module_name=module_name,
            short_name=short_name,
            **bound_ports,
        )

    def instance_auto(
        self,
        fn: Any,
        *,
        name: str,
        params: dict[str, Any] | None = None,
        module_name: str | None = None,
        short_name: str | None = None,
        keep: bool = False,
        **ports: Any,
    ) -> Connector | ConnectorBundle:
        """Instantiate a module while auto-wrapping port values as connectors."""
        wrapped = {str(k): self.as_connector(v, name=str(k)) for k, v in ports.items()}
        return self.instance(
            fn,
            name=str(name),
            params=params,
            module_name=module_name,
            short_name=short_name,
            keep=keep,
            **wrapped,
        )

    @staticmethod
    def _sanitize_instance_key(key: Any) -> str:
        raw = str(key)
        if not raw:
            return "k"
        out = []
        for ch in raw:
            if ch.isalnum() or ch == "_":
                out.append(ch)
            else:
                out.append("_")
        s = "".join(out).strip("_")
        return s or "k"

    def _resolve_keyed_binding(self, v: Any, key: str) -> Any:
        if callable(v):
            return v(key)
        return v

    def array(
        self,
        fn_or_collection: Any,
        *,
        name: str,
        bind: Mapping[str, Any],
        keys: Iterable[Any] | None = None,
        per: Mapping[str, Mapping[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        module_name: str | None = None,
    ) -> ModuleCollectionHandle:
        """Instantiate a deterministic collection of module instances.

        `fn_or_collection` may be:
        - a `@module` function (requires `keys`)
        - a `spec.Module*Spec` collection (fn/keys inferred)
        """
        from .spec.types import (
            ModuleDictSpec,
            ModuleFamilySpec,
            ModuleListSpec,
            ModuleMapSpec,
            ModuleVectorSpec,
            iter_module_collection,
        )

        fn = fn_or_collection
        key_list: list[tuple[str, dict[str, Any] | None]] = []
        base_params = dict(params or {})

        if isinstance(fn_or_collection, ModuleFamilySpec):
            fn = fn_or_collection.module
            if keys is None:
                raise TypeError("array(ModuleFamilySpec, ...) requires `keys=`")
            if fn_or_collection.params is not None:
                base_params.update(fn_or_collection.params.as_dict())
            key_list = [(str(k), None) for k in sorted((str(x) for x in keys), key=lambda x: x)]
        elif isinstance(fn_or_collection, (ModuleListSpec, ModuleVectorSpec, ModuleMapSpec, ModuleDictSpec)):
            family = fn_or_collection.family
            fn = family.module
            if family.params is not None:
                base_params.update(family.params.as_dict())
            for k, ps in iter_module_collection(fn_or_collection):
                key_list.append((str(k), None if ps is None else ps.as_dict()))
        else:
            if keys is None:
                raise TypeError("array(fn, ...) requires `keys=`")
            key_list = [(str(k), None) for k in sorted((str(x) for x in keys), key=lambda x: x)]

        if not key_list:
            raise ValueError("array requires at least one key")

        collection_kind = "plain"
        family_payload: dict[str, Any] | None = None
        template_payload: dict[str, Any] | None = None
        from_module_family = False
        if isinstance(fn_or_collection, ModuleFamilySpec):
            collection_kind = "family"
            family_payload = fn_or_collection.__pyc_template_value__()
            template_payload = family_payload
            from_module_family = True
        elif isinstance(fn_or_collection, ModuleListSpec):
            collection_kind = "list"
            family_payload = fn_or_collection.family.__pyc_template_value__()
            template_payload = fn_or_collection.__pyc_template_value__()
            from_module_family = True
        elif isinstance(fn_or_collection, ModuleVectorSpec):
            collection_kind = "vector"
            family_payload = fn_or_collection.family.__pyc_template_value__()
            template_payload = fn_or_collection.__pyc_template_value__()
            from_module_family = True
        elif isinstance(fn_or_collection, ModuleMapSpec):
            collection_kind = "map"
            family_payload = fn_or_collection.family.__pyc_template_value__()
            template_payload = fn_or_collection.__pyc_template_value__()
            from_module_family = True
        elif isinstance(fn_or_collection, ModuleDictSpec):
            collection_kind = "dict"
            family_payload = fn_or_collection.family.__pyc_template_value__()
            template_payload = fn_or_collection.__pyc_template_value__()
            from_module_family = True

        meta: dict[str, Any] = {
            "name": str(name),
            "collection_kind": str(collection_kind),
            "key_count": int(len(key_list)),
            "from_module_family": bool(from_module_family),
        }
        if family_payload is not None:
            meta["family_identity"] = self._struct_identity(family_payload)
            meta["family_payload"] = family_payload
        if template_payload is not None:
            meta["template_payload"] = template_payload
        self._record_struct_collection(meta)

        keyed_bindings = dict(per or {})
        instances: dict[str, ModuleInstanceHandle] = {}
        outputs: dict[str, Connector | ConnectorBundle | ConnectorStruct] = {}

        for key, param_override in key_list:
            merged_bindings: dict[str, Any] = {}
            for pname, vv in bind.items():
                merged_bindings[str(pname)] = self._resolve_keyed_binding(vv, key)
            if key in keyed_bindings:
                for pname, vv in keyed_bindings[key].items():
                    merged_bindings[str(pname)] = self._resolve_keyed_binding(vv, key)

            inst_params = dict(base_params)
            if param_override:
                inst_params.update(param_override)

            inst_name = f"{str(name)}_{self._sanitize_instance_key(key)}"
            inst = self.new(
                fn,
                name=inst_name,
                bind=merged_bindings,
                params=inst_params,
                module_name=module_name,
            )
            instances[key] = inst
            outputs[key] = inst.outputs

        return ModuleCollectionHandle(
            name=str(name),
            instances=instances,
            outputs=outputs,
        )

    def _coerce_instance_connector(self, v: Any, *, port: str) -> Connector:
        from .design import DesignError

        if is_connector_bundle(v):
            raise DesignError(f"instance port {port!r}: ConnectorBundle is not valid for a single callee port")
        if is_connector_struct(v):
            raise DesignError(f"instance port {port!r}: ConnectorStruct is not valid for a single callee port")
        try:
            return self.as_connector(v, name=port)
        except Exception as e:  # noqa: BLE001
            raise DesignError(
                f"instance port {port!r}: unsupported value {type(v).__name__}; "
                "expected Connector/Wire/Reg/Signal/int/literal"
            ) from e

    def instance_handle(
        self,
        fn: Any,
        *,
        name: str,
        params: dict[str, Any] | None = None,
        module_name: str | None = None,
        short_name: str | None = None,
        keep: bool = False,
        **ports: Any,
    ) -> ModuleInstanceHandle:
        """Instantiate a specialized sub-module and return a rich instance handle."""

        if self._design_ctx is None:
            raise TypeError("Circuit.instance requires a design context (compile via pycircuit.jit.compile)")

        from .design import DesignContext, DesignError, value_params_of

        if not isinstance(self._design_ctx, DesignContext):
            raise TypeError("internal error: Circuit design context has an unexpected type")

        params_dict = dict(params or {})
        overlap = sorted(set(params_dict.keys()) & set(ports.keys()))
        if overlap:
            raise DesignError(f"instance params/ports overlap: {', '.join(overlap)}")
        callee_value_params = value_params_of(fn)
        value_param_overlap = sorted(set(params_dict.keys()) & set(callee_value_params.keys()))
        if value_param_overlap:
            raise DesignError(
                "value-param(s) must be connected as instance ports, not specialization params: "
                + ", ".join(value_param_overlap)
            )

        normalized_ports: dict[str, Connector] = {}
        for pname, v in ports.items():
            normalized_ports[str(pname)] = self._coerce_instance_connector(v, port=str(pname))

        # Signature-bound hardware args: if a function parameter name is provided
        # as a port connection, treat it as a formal input type for specialization.
        sig_port_specs: dict[str, Any] = {}
        try:
            sig = inspect.signature(fn)
            ps = list(sig.parameters.values())
            sig_param_names = {
                p.name
                for p in ps[1:]
                if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            }
        except (TypeError, ValueError):
            sig_param_names = set()

        for pname in sorted(sig_param_names & set(normalized_ports.keys())):
            if pname in callee_value_params:
                # Value-param port types are declared at the @module boundary;
                # they are not part of specialization key inference.
                continue

            c = normalized_ports[pname]
            rv = c.read()
            if isinstance(rv, Wire):
                if rv.m is not self:
                    raise DesignError(f"instance port {pname!r}: cannot connect a wire from a different module")
                if isinstance(rv.ty, Vector):
                    sig_port_specs[pname] = {
                        "kind": "vec",
                        "ty": str(rv.ty),
                        "signed": bool(rv.signed),
                    }
                else:
                    sig_port_specs[pname] = {
                        "kind": "wire",
                        "ty": str(rv.ty),
                        "signed": bool(rv.signed),
                    }
                continue
            if isinstance(rv, Signal):
                if isinstance(rv.ty, Clock):
                    sig_port_specs[pname] = {"kind": "clock"}
                elif isinstance(rv.ty, Reset):
                    sig_port_specs[pname] = {"kind": "reset"}
                elif isinstance(rv.ty, Vector):
                    sig_port_specs[pname] = {
                        "kind": "vec",
                        "ty": str(rv.ty),
                        "signed": bool(getattr(c, "signed", False)),
                    }
                elif isinstance(rv.ty, Bits):
                    sig_port_specs[pname] = {
                        "kind": "wire",
                        "ty": str(rv.ty),
                        "signed": bool(getattr(c, "signed", False)),
                    }
                else:
                    raise DesignError(f"instance port {pname!r}: unsupported signal type {rv.ty!r}")
                continue
            raise DesignError(f"instance port {pname!r}: unsupported connector payload {type(rv).__name__}")

        cm = self._design_ctx.specialize(
            fn,
            params=params_dict,
            module_name=module_name,
            port_specs=sig_port_specs,
        )

        expected = set(cm.arg_names)
        provided = set(normalized_ports.keys())
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        if missing or extra:
            parts: list[str] = []
            if missing:
                parts.append("missing: " + ", ".join(missing))
            if extra:
                parts.append("extra: " + ", ".join(extra))
            raise DesignError(f"instance port mismatch for {cm.sym_name!r} ({'; '.join(parts)})")

        def coerce_to_sig(c: Connector, *, expected_ty: Data, port: str) -> Signal:
            rv = c.read()
            if isinstance(rv, Wire):
                if rv.m is not self:
                    raise DesignError(f"instance port {port!r}: cannot connect a wire from a different module")
                sig = rv.sig
                src_signed = bool(rv.signed)
            elif isinstance(rv, Signal):
                sig = rv
                src_signed = bool(getattr(c, "signed", False))
            else:
                raise DesignError(f"instance port {port!r}: unsupported connector payload {type(rv).__name__}")

            if sig.ty == expected_ty:
                return sig

            # Convenience: allow implicit integer resizing (zext/trunc) like `Circuit.assign`.
            if isinstance(sig.ty, Bits) and isinstance(expected_ty, Bits):
                got_w = sig.ty.width
                exp_w = expected_ty.width
                if got_w < exp_w:
                    return self.sext(sig, width=exp_w) if src_signed else self.zext(sig, width=exp_w)
                if got_w > exp_w:
                    return self.trunc(sig, width=exp_w)
                return sig

            raise DesignError(f"instance port {port!r}: type mismatch, got {sig.ty} expected {expected_ty}")

        # Build operands in callee signature order.
        operands: list[Signal] = []
        for pname, pty in zip(cm.arg_names, cm.arg_types):
            operands.append(coerce_to_sig(normalized_ports[pname], expected_ty=pty, port=pname))

        outs = self.instance_op(
            cm.sym_name,
            *operands,
            result_types=list(cm.result_types),
            name=str(name),
            short_name=None if short_name is None else str(short_name),
            keep=bool(keep),
        )
        self._record_struct_instance()
        out_fields: dict[str, Connector] = {}
        for oname, sig in zip(cm.result_names, outs):
            if isinstance(sig.ty, Vector):
                out_fields[oname] = WireConnector(owner=self, name=oname, wire=Wire(self, sig))
            else:
                out_fields[oname] = WireConnector(owner=self, name=oname, wire=Wire(self, sig))
        force_bundle = False
        try:
            ann = inspect.signature(cm.fn).return_annotation
            if ann is ConnectorBundle:
                force_bundle = True
            elif isinstance(ann, str) and ann.replace(" ", "").lower() == "connectorbundle":
                force_bundle = True
        except (TypeError, ValueError):
            pass

        if len(out_fields) == 1 and not force_bundle:
            outputs: Connector | ConnectorBundle = next(iter(out_fields.values()))
        else:
            outputs = ConnectorBundle(out_fields)

        return ModuleInstanceHandle(
            name=str(name),
            symbol=str(cm.sym_name),
            inputs=dict(normalized_ports),
            outputs=outputs,
        )

    def instance(
        self,
        fn: Any,
        *,
        name: str,
        params: dict[str, Any] | None = None,
        module_name: str | None = None,
        short_name: str | None = None,
        keep: bool = False,
        **ports: Any,
    ) -> Connector | ConnectorBundle:
        """Instantiate a specialized sub-module.

        Port bindings accept `Connector` or raw values that can be coerced by
        `Circuit.as_connector` (Wire/Reg/Signal/int/literal).

        Returns:
        - single output: return `Connector`
        - multiple outputs: return `ConnectorBundle`
        """

        return self.instance_handle(
            fn,
            name=name,
            params=params,
            module_name=module_name,
            short_name=short_name,
            keep=keep,
            **ports,
        ).outputs

    def byte_mem(
        self,
        clk: Signal,
        rst: Signal,
        *,
        raddr: Union[Wire, Reg, Signal],
        wvalid: Union[Wire, Reg, Signal],
        waddr: Union[Wire, Reg, Signal],
        wdata: Union[Wire, Reg, Signal],
        wstrb: Union[Wire, Reg, Signal],
        depth: int,
        name: str,
    ) -> Wire:
        rdata = super().byte_mem(
            clk,
            rst,
            Signal.as_sig(raddr),
            Signal.as_sig(wvalid),
            Signal.as_sig(waddr),
            Signal.as_sig(wdata),
            Signal.as_sig(wstrb),
            depth=depth,
            name=name,
        )
        self._record_struct_state_alloc()
        return Wire(self, rdata)

    def sync_mem(
        self,
        clk: Signal,
        rst: Signal,
        *,
        ren: Union[Wire, Reg, Signal],
        raddr: Union[Wire, Reg, Signal],
        wvalid: Union[Wire, Reg, Signal],
        waddr: Union[Wire, Reg, Signal],
        wdata: Union[Wire, Reg, Signal],
        wstrb: Union[Wire, Reg, Signal],
        depth: int,
        name: str,
    ) -> Wire:
        rdata = super().sync_mem(
            clk,
            rst,
            Signal.as_sig(ren),
            Signal.as_sig(raddr),
            Signal.as_sig(wvalid),
            Signal.as_sig(waddr),
            Signal.as_sig(wdata),
            Signal.as_sig(wstrb),
            depth=depth,
            name=name,
        )
        self._record_struct_state_alloc()
        return Wire(self, rdata)

    def sync_mem_dp(
        self,
        clk: Signal,
        rst: Signal,
        *,
        ren0: Union[Wire, Reg, Signal],
        raddr0: Union[Wire, Reg, Signal],
        ren1: Union[Wire, Reg, Signal],
        raddr1: Union[Wire, Reg, Signal],
        wvalid: Union[Wire, Reg, Signal],
        waddr: Union[Wire, Reg, Signal],
        wdata: Union[Wire, Reg, Signal],
        wstrb: Union[Wire, Reg, Signal],
        depth: int,
        name: str,
    ) -> tuple[Wire, Wire]:
        rdata0, rdata1 = super().sync_mem_dp(
            clk,
            rst,
            Signal.as_sig(ren0),
            Signal.as_sig(raddr0),
            Signal.as_sig(ren1),
            Signal.as_sig(raddr1),
            Signal.as_sig(wvalid),
            Signal.as_sig(waddr),
            Signal.as_sig(wdata),
            Signal.as_sig(wstrb),
            depth=depth,
            name=name,
        )
        self._record_struct_state_alloc()
        return Wire(self, rdata0), Wire(self, rdata1)

    def async_fifo(
        self,
        in_clk: Signal,
        in_rst: Signal,
        out_clk: Signal,
        out_rst: Signal,
        *,
        in_valid: Union[Wire, Reg, Signal],
        in_data: Union[Wire, Reg, Signal],
        out_ready: Union[Wire, Reg, Signal],
        depth: int,
    ) -> tuple[Wire, Wire, Wire]:
        in_ready, out_valid, out_data = super().async_fifo(
            in_clk,
            in_rst,
            out_clk,
            out_rst,
            Signal.as_sig(in_valid),
            Signal.as_sig(in_data),
            Signal.as_sig(out_ready),
            depth=depth,
        )
        self._record_struct_state_alloc()
        return Wire(self, in_ready), Wire(self, out_valid), Wire(self, out_data)

    def cdc_sync(self, clk: Signal, rst: Signal, a: Union[Wire, Reg, Signal], *, stages: int | None = None) -> Wire:
        sig = Signal.as_sig(a)
        out = super().cdc_sync(clk, rst, sig, stages=stages)
        self._record_struct_state_alloc()
        return Wire(self, out)

    def fifo(
        self,
        clk: Signal,
        rst: Signal,
        *,
        in_valid: Union[Wire, Reg, Signal],
        in_data: Union[Wire, Reg, Signal],
        out_ready: Union[Wire, Reg, Signal],
        depth: int,
    ) -> tuple[Wire, Wire, Wire]:
        """Strict ready/valid FIFO (single-clock, prototype)."""

        in_ready, out_valid, out_data = super().fifo(
            clk,
            rst,
            Signal.as_sig(in_valid),
            Signal.as_sig(in_data),
            Signal.as_sig(out_ready),
            depth=depth,
        )
        self._record_struct_state_alloc()
        return Wire(self, in_ready), Wire(self, out_valid), Wire(self, out_data)

    def fifo_domain(
        self,
        domain: ClockDomain,
        *,
        in_valid: Union[Wire, Reg, Signal],
        in_data: Union[Wire, Reg, Signal],
        out_ready: Union[Wire, Reg, Signal],
        depth: int,
    ) -> tuple[Wire, Wire, Wire]:
        return self.fifo(domain.clk, domain.rst, in_valid=in_valid, in_data=in_data, out_ready=out_ready, depth=depth)

    def rv_queue(
        self,
        name: str,
        *,
        clk: Signal | None = None,
        rst: Signal | None = None,
        domain: ClockDomain | None = None,
        width: int,
        depth: int,
    ) -> "RvQueue":
        if domain is not None:
            clk = domain.clk
            rst = domain.rst
        if clk is None or rst is None:
            raise TypeError("rv_queue() requires either domain=... or both clk=... and rst=...")
        return RvQueue(self, name, clk=clk, rst=rst, width=width, depth=depth)


@dataclass(frozen=True)
class Bundle:
    """A small named container (like a Verilog struct/bundle).

    Intended syntax:
      b = m.bundle(a=a, b=b)
      x = b["a"]
      packed = b.pack()
    """

    fields: dict[str, Union[Wire, Reg]]

    def __post_init__(self) -> None:
        if not self.fields:
            return
        # Ensure all elements come from the same Module.
        vals = list(self.fields.values())
        m0 = self._wire_module_of(vals[0])
        for v in vals[1:]:
            mv = self._wire_module_of(v)
            if mv is not m0:
                raise ValueError("Bundle fields must belong to the same Circuit/Module")

    def _wire_module_of(self, v: Union[Wire, Reg]) -> Module:
        return v.q.m if isinstance(v, Reg) else v.m

    def __getitem__(self, key: str) -> Union[Wire, Reg]:
        return self.fields[str(key)]

    def items(self) -> Iterable[tuple[str, Union[Wire, Reg]]]:
        return self.fields.items()

    def pack(self) -> Wire:
        if not self.fields:
            raise ValueError("cannot pack an empty Bundle")
        first_value = list(self.fields.values())[0]
        m = first_value.m if isinstance(first_value, Wire) else first_value.q.m 
        sigs = [Signal.as_sig(v) for v in self.fields.values()]
        return Wire(m,  m.concat(*sigs))

    def unpack(self, packed: Wire) -> "Bundle":
        """Extract fields from a packed bus (inverse of pack())."""
        if not self.fields:
            raise ValueError("cannot unpack into an empty Bundle")
        elems = list(self.fields.values())
        widths = [(e.q.width if isinstance(e, Reg) else e.width) for e in elems]
        total = sum(widths)
        if packed.width != total:
            raise ValueError(f"unpack width mismatch: got i{packed.width}, expected i{total}")
        out: dict[str, Union[Wire, Reg]] = {}
        lsb = 0
        # pack is MSB-first: first field is at the top bits
        for (k, _), w in zip(reversed(list(self.fields.items())), reversed(widths)):
            out[k] = packed.slice(lsb=lsb, width=w)
            lsb += w
        # rebuild in original key order
        ordered = {k: out[k] for k in self.fields}
        return Bundle(ordered)


@dataclass(frozen=True)
class Pop:
    valid: Wire
    data: Wire
    fire: Wire


class RvQueue:
    """Queue-like wrapper over `pyc.fifo` (single-clock, strict ready/valid).

    Intended usage (event-ish):
      q = m.rv_queue("q", domain=dom, width=8, depth=2)
      accepted = q.push(x, when=in_valid)
      p = q.pop(when=out_ready)
      # p.valid / p.data / p.fire
    """

    def __init__(self, m: Circuit, name: str, *, clk: Signal, rst: Signal, width: int, depth: int) -> None:
        self.m = m
        self.name = str(name)
        self.width = int(width)
        self.depth = int(depth)

        if self.width <= 0:
            raise ValueError("RvQueue width must be > 0")
        if self.depth <= 0:
            raise ValueError("RvQueue depth must be > 0")

        # Input placeholders driven by the high-level API (finalized before emit_mlir()).
        self._in_valid = m.named_wire(f"{self.name}__in_valid", width=1)
        self._in_data = m.named_wire(f"{self.name}__in_data", width=self.width)
        self._out_ready = m.named_wire(f"{self.name}__out_ready", width=1)

        # Underlying FIFO instance.
        in_ready, out_valid, out_data = m.fifo(clk, rst, in_valid=self._in_valid, in_data=self._in_data, out_ready=self._out_ready, depth=self.depth)
        self.in_ready = in_ready
        self.out_valid = out_valid
        self.out_data = out_data

        self._push_bound = False
        self._pop_bound = False
        self._push_valid_expr: Union[Wire, Reg, Signal, int, LiteralValue] = 0
        self._push_data_expr: Union[Wire, Reg, Signal, int, LiteralValue] = 0
        self._pop_ready_expr: Union[Wire, Reg, Signal, int, LiteralValue] = 0

        # Defer assigns so we can keep single-driver semantics while supporting a push/pop API.
        m.add_finalizer(self._finalize)

    def push(self, data: Union[Wire, Reg, Signal, int, LiteralValue], *, when: Union[Wire, Signal, int, LiteralValue] = 1) -> Wire:
        if self._push_bound:
            raise ValueError("RvQueue.push() may only be called once per RvQueue instance (prototype limitation)")
        self._push_bound = True
        self._push_valid_expr = when
        self._push_data_expr = data
        # Fire when valid && ready.
        w_when = self._coerce_i1(when, ctx="queue push when")
        return w_when & self.in_ready

    def pop(self, *, when: Union[Wire, Signal, int, LiteralValue] = 1) -> Pop:
        if self._pop_bound:
            raise ValueError("RvQueue.pop() may only be called once per RvQueue instance (prototype limitation)")
        self._pop_bound = True
        self._pop_ready_expr = when
        w_when = self._coerce_i1(when, ctx="queue pop when")
        fire = self.out_valid & w_when
        return Pop(valid=self.out_valid, data=self.out_data, fire=fire)

    def _finalize(self) -> None:
        # Defaults: drive inactive.
        m = self.m
        m.assign(self._in_valid, self._push_valid_expr)
        m.assign(self._in_data, self._push_data_expr)
        m.assign(self._out_ready, self._pop_ready_expr)

    def _coerce_i1(self, v: Union[Wire, Signal, int, LiteralValue], *, ctx: str) -> Wire:
        if isinstance(v, Wire):
            if v.m is not self.m:
                raise ValueError("cannot combine wires from different modules")
            if v.ty != Bits(1):
                raise TypeError(f"{ctx}: expected i1, got {v.ty}")
            return v
        if isinstance(v, Signal):
            if v.ty != Bits(1):
                raise TypeError(f"{ctx}: expected i1, got {v.ty}")
            return Wire(self.m, v)
        if isinstance(v, int):
            return self.m.const(int(v), width=1)
        if isinstance(v, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(v, ctx_width=1, ctx_signed=False)
            w = Wire(self.m, Module.const(self.m, int(v.value), width=lit_w), signed=lit_signed)
            if w.ty != "i1":
                raise TypeError(f"{ctx}: expected i1 literal, got {w.ty}")
            return w
        raise TypeError(f"{ctx}: expected Wire/Signal/int, got {type(v).__name__}")


def cat(*elems: Union[Wire, Reg, int, LiteralValue]) -> Wire:
    """Concatenate wires/regs into a packed bus (MSB-first).

    Convenience wrapper so you can write:
      `bus = cat(a, b, c)`

    Equivalent to:
      `bus = m.cat(a, b, c)` (when all values belong to the same Circuit).
    """
    if not elems:
        raise ValueError("cat() requires at least one element")

    owner: Module | None = None
    for e in elems:
        if isinstance(e, Wire):
            owner = e.m
            break
        if isinstance(e, Reg):
            owner = e.q.m
            break
    if owner is None:
        raise TypeError("cat() requires at least one Wire/Reg element to establish module ownership")

    sigs = [Wire.as_wire(e, m=owner).sig for e in elems]
    return Wire(owner, owner.concat(*sigs))


def _cast_value(value: Any, *, width: int, op: str) -> Wire:
    if isinstance(value, Connector):
        value = value.read()
    if isinstance(value, Reg):
        value = value.q
    if isinstance(value, Wire):
        if op == "zext":
            return value.zext(width=int(width))
        if op == "sext":
            return value.sext(width=int(width))
        if op == "trunc":
            return value.trunc(width=int(width))
    if isinstance(value, (Wire, Reg)):
        if op == "zext":
            return value.zext(width=int(width))
        if op == "sext":
            return value.sext(width=int(width))
        if op == "trunc":
            return value.trunc(width=int(width))
    raise TypeError(f"{op}() expects Wire/Reg/Connector, got {type(value).__name__}")


def zext(value: Any, *, width: int) -> Wire:
    """Zero-extend a Wire/Reg using the canonical function-style API."""
    return _cast_value(value, width=int(width), op="zext")


def sext(value: Any, *, width: int) -> Wire:
    """Sign-extend a Wire/Reg using the canonical function-style API."""
    return _cast_value(value, width=int(width), op="sext")


def trunc(value: Any, *, width: int) -> Wire:
    """Truncate a Wire/Reg using the canonical function-style API."""
    return _cast_value(value, width=int(width), op="trunc")

@overload
def unsigned(v: Wire) -> Wire:
    ...


@overload
def unsigned(v: Reg) -> Wire:
    ...


def unsigned(v: Wire | Reg) -> Wire:
    """Return the unsigned view of a hardware value."""
    if isinstance(v, Reg):
        return v.q.as_unsigned()
    if isinstance(v, Wire):
        return v.as_unsigned()
    raise TypeError(f"unsigned() expects Wire/Reg, got {type(v).__name__}")


@overload
def signed(v: Wire) -> Wire:
    ...


@overload
def signed(v: Reg) -> Wire:
    ...


def signed(v: Wire | Reg) -> Wire:
    """Return the signed view of a hardware value."""
    if isinstance(v, Reg):
        return v.q.as_signed()
    if isinstance(v, Wire):
        return v.as_signed()
    raise TypeError(f"signed() expects Wire/Reg, got {type(v).__name__}")
