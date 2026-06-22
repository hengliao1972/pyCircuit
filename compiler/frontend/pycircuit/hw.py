from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import inspect
import json
from typing import Any, Iterable, Iterator, Mapping, Union, overload

from .connectors import (
    Connector,
    ConnectorBundle,
    ConnectorError,
    ConnectorStruct,
    ModuleCollectionHandle,
    ModuleInstanceHandle,
    RegConnector,
    WireConnector,
    is_connector,
    is_connector_bundle,
    is_connector_struct,
)
from .design import DesignError
from .dsl import Module, Signal
from .literals import LiteralValue, infer_literal_width


_VEC_BINARY_METHODS: dict[str, str] = {
    "add": "add",
    "sub": "sub",
    "mul": "mul",
    "udiv": "udiv",
    "urem": "urem",
    "sdiv": "sdiv",
    "srem": "srem",
    "and": "and_",
    "or": "or_",
    "xor": "xor",
    "eq": "eq",
    "ult": "ult",
    "slt": "slt",
}
_VEC_COMPARE_OPS = {"eq", "ult", "slt"}


def _int_width(ty: str) -> int:
    if not ty.startswith("i"):
        raise TypeError(f"expected integer type iN, got {ty!r}")
    w = int(ty[1:])
    if w <= 0:
        raise ValueError(f"invalid integer width: {ty!r}")
    return w


def _removed_design_api(name: str, replacement: str) -> TypeError:
    return TypeError(f"{name} was removed from pyCircuit; use {replacement}")


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
class Wire:
    m: Module
    sig: Signal
    signed: bool = False
    # True if this Wire originates from `pyc.wire` and is intended to be driven
    # by `pyc.assign` (SSA backedge placeholder). JIT debug aliasing must not
    # wrap such wires in `pyc.alias`, because `pyc.assign` destinations must be
    # defined by `pyc.wire`.
    assignable: bool = False

    def __post_init__(self) -> None:
        _int_width(self.sig.ty)

    @property
    def ref(self) -> str:
        return self.sig.ref

    @property
    def ty(self) -> str:
        return self.sig.ty

    @property
    def width(self) -> int:
        return _int_width(self.sig.ty)

    def __str__(self) -> str:
        return self.sig.ref

    def __bool__(self) -> bool:
        raise TypeError(
            "Wire cannot be used as a Python boolean. "
            "Use `if` inside a JIT-compiled design function, or compare explicitly and return an i1 Wire."
        )

    def out(self) -> "Wire":
        """Stage-friendly sugar: a Wire's value is itself."""
        return self

    def _as_wire(self, v: Union["Wire", "Reg", Signal, int, LiteralValue], *, width: int | None) -> "Wire":
        if isinstance(v, Connector):
            v = v.read()
        if isinstance(v, Reg):
            v = v.q
        if isinstance(v, Wire):
            if v.m is not self.m:
                raise ValueError("cannot combine wires from different modules")
            return v
        if isinstance(v, Signal):
            return Wire(self.m, v)
        if isinstance(v, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(v, ctx_width=width, ctx_signed=v.signed)
            const_sig = Module.const(self.m, int(v.value), width=int(lit_w))
            return Wire(self.m, const_sig, signed=lit_signed)
        if isinstance(v, int):
            if width is None:
                width = self.width
            # Call the base `Module.const` even if `Circuit.const` is overridden
            # to return a `Wire`.
            const_sig = Module.const(self.m, int(v), width=int(width))
            return Wire(self.m, const_sig, signed=(int(v) < 0))
        raise TypeError(f"unsupported operand type: {type(v).__name__}")

    def _promote2(self, other: Union["Wire", "Reg", Signal, int, LiteralValue]) -> tuple["Wire", "Wire"]:
        """Promote operands to a common width (extend smaller operand)."""
        a = self._as_wire(self, width=None)
        if isinstance(other, int):
            b = self._as_wire(int(other), width=a.width)
        else:
            b = self._as_wire(other, width=None)
        out_w = max(a.width, b.width)
        if a.width != out_w:
            a = a._sext(width=out_w) if a.signed else a._zext(width=out_w)
        if b.width != out_w:
            b = b._sext(width=out_w) if b.signed else b._zext(width=out_w)
        return a, b

    def __add__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.add(a.sig, b.sig), signed=(a.signed or b.signed))

    def __radd__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        return self.__add__(other)

    def __sub__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.sub(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rsub__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        b = self._as_wire(self, width=None)
        a = self._as_wire(other, width=b.width)
        aa, bb = a._promote2(b) if isinstance(a, Wire) else (a, b)
        return Wire(self.m, self.m.sub(aa.sig, bb.sig), signed=(aa.signed or bb.signed))

    def __mul__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.mul(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rmul__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        return self.__mul__(other)

    def __rfloordiv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        num = self._as_wire(other, width=None)
        return num.__floordiv__(self)

    def __floordiv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.sdiv(a.sig, b.sig), signed=True)
        return Wire(self.m, self.m.udiv(a.sig, b.sig), signed=False)

    def __rtruediv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        raise TypeError("hardware `/` division is not supported; use `//` for integer division")

    def __truediv__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        raise TypeError("hardware `/` division is not supported; use `//` for integer division")

    def __rmod__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        num = self._as_wire(other, width=None)
        return num.__mod__(self)

    def __mod__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.srem(a.sig, b.sig), signed=True)
        return Wire(self.m, self.m.urem(a.sig, b.sig), signed=False)

    def __and__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.and_(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rand__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        return self.__and__(other)

    def __or__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.or_(a.sig, b.sig), signed=(a.signed or b.signed))

    def __ror__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        return self.__or__(other)

    def __xor__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.xor(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rxor__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        return self.__xor__(other)

    def __invert__(self) -> "Wire":
        return Wire(self.m, self.m.not_(self.sig), signed=self.signed)

    def __lshift__(self, other: int) -> "Wire":
        if not isinstance(other, int):
            raise TypeError("<< only supports constant integer shift amounts")
        return self.shl(amount=other)

    def lshr(self, *, amount: Union[int, "Wire", "Reg", Signal, LiteralValue]) -> "Wire":
        """Logical shift right by an immediate or dynamic amount (zero-fill)."""
        if isinstance(amount, Vec): return NotImplemented
        if isinstance(amount, int):
            amt = int(amount)
            if amt < 0:
                raise ValueError("lshr amount must be >= 0")
            return Wire(self.m, self.m.lshri(self.sig, amount=amt), signed=False)
        amt = self._as_wire(amount, width=None)
        return Wire(self.m, self.m.lshr(self.sig, amt.sig), signed=False)

    def ashr(self, *, amount: Union[int, "Wire", "Reg", Signal, LiteralValue]) -> "Wire":
        """Arithmetic shift right by an immediate or dynamic amount (sign-fill)."""
        if isinstance(amount, Vec): return NotImplemented
        if isinstance(amount, int):
            amt = int(amount)
            if amt < 0:
                raise ValueError("ashr amount must be >= 0")
            return Wire(self.m, self.m.ashri(self.sig, amount=amt), signed=True)
        amt = self._as_wire(amount, width=None)
        return Wire(self.m, self.m.ashr(self.sig, amt.sig), signed=True)

    def __rshift__(self, other: int) -> "Wire":
        if not isinstance(other, int):
            raise TypeError(">> only supports constant integer shift amounts")
        if self.signed:
            return self.ashr(amount=other)
        return self.lshr(amount=other)

    def __eq__(self, other: object) -> "Wire":  # type: ignore[override]
        if isinstance(other, Vec): return NotImplemented
        if not isinstance(other, (Wire, Reg, Signal, Connector, int, LiteralValue)):
            return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.eq(a.sig, b.sig))

    def __ne__(self, other: object) -> "Wire":  # type: ignore[override]
        if isinstance(other, Vec): return NotImplemented
        if not isinstance(other, (Wire, Reg, Signal, Connector, int, LiteralValue)):
            return NotImplemented
        return ~(self == other)

    def eq(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        return self == other

    def ne(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        if isinstance(other, Vec): return NotImplemented
        return self != other

    def ult(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Unsigned less-than compare (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.ult(a.sig, b.sig))

    def slt(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Signed less-than compare (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        return Wire(self.m, self.m.slt(a.sig, b.sig))

    def __lt__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Less-than compare respecting signed intent (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.slt(a.sig, b.sig))
        return Wire(self.m, self.m.ult(a.sig, b.sig))

    def __gt__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Greater-than compare respecting signed intent (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        other_w = self._as_wire(other, width=None)
        return other_w < self

    def __le__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Less-than-or-equal compare respecting signed intent (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        return ~(self > other)

    def __ge__(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Greater-than-or-equal compare respecting signed intent (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        return ~(self < other)

    def ugt(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Unsigned greater-than compare (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        other_w = self._as_wire(other, width=None)
        return other_w.ult(self)

    def ule(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Unsigned less-than-or-equal compare (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        return ~self.ugt(other)

    def uge(self, other: Union["Wire", "Reg", Signal, int, LiteralValue, "Vec"]) -> "Wire":
        """Unsigned greater-than-or-equal compare (result is i1)."""
        if isinstance(other, Vec): return NotImplemented
        return ~self.ult(other)

    def _select_internal(self, a: Union["Wire", "Reg", Signal, int, LiteralValue], b: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        if self.ty != "i1":
            raise TypeError("conditional selection requires a 1-bit selector wire (i1)")

        # At least one operand must provide width.
        if isinstance(a, int) and isinstance(b, int):
            raise TypeError("conditional selection requires at least one Wire/Reg/Signal operand (cannot infer width from two ints)")

        aw: Wire | None = None
        bw: Wire | None = None
        if not isinstance(a, int):
            aw = self._as_wire(a, width=None)
        if not isinstance(b, int):
            bw = self._as_wire(b, width=None)

        if aw is None and bw is None:
            raise TypeError("conditional selection requires at least one Wire/Reg/Signal operand (cannot infer width)")

        out_w = max(aw.width if aw is not None else 0, bw.width if bw is not None else 0)
        if aw is None:
            aw = self._as_wire(int(a), width=out_w)
        if bw is None:
            bw = self._as_wire(int(b), width=out_w)

        if aw.width != out_w:
            aw = aw._sext(width=out_w) if aw.signed else aw._zext(width=out_w)
        if bw.width != out_w:
            bw = bw._sext(width=out_w) if bw.signed else bw._zext(width=out_w)
        return Wire(self.m, self.m.mux(self.sig, aw.sig, bw.sig), signed=(aw.signed or bw.signed))

    def select(self, a: Union["Wire", "Reg", Signal, int, LiteralValue], b: Union["Wire", "Reg", Signal, int, LiteralValue]) -> "Wire":
        return self._select_internal(a, b)

    def _trunc(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.trunc(self.sig, width=width), signed=self.signed)

    def _zext(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.zext(self.sig, width=width), signed=False)

    def _sext(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.sext(self.sig, width=width), signed=True)

    def trunc(self, *, width: int) -> "Wire":
        return self._trunc(width=width)

    def zext(self, *, width: int) -> "Wire":
        return self._zext(width=width)

    def sext(self, *, width: int) -> "Wire":
        return self._sext(width=width)

    def slice(self, *, lsb: int, width: int) -> "Wire":
        return Wire(self.m, self.m.extract(self.sig, lsb=lsb, width=width), signed=False)

    def shl(self, *, amount: Union[int, "Wire", "Reg", Signal, LiteralValue, "Vec"]) -> "Wire":
        """Shift left by an immediate or dynamic amount."""
        if isinstance(amount, Vec): return NotImplemented
        if isinstance(amount, int):
            return Wire(self.m, self.m.shli(self.sig, amount=int(amount)), signed=self.signed)
        amt = self._as_wire(amount, width=None)
        return Wire(self.m, self.m.shl(self.sig, amt.sig), signed=self.signed)

    def __getitem__(self, idx: int | slice) -> "Wire":
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
        return Wire(self.m, self.m.alias(self.sig, name=scoped), signed=self.signed)

    def as_signed(self) -> "Wire":
        """Mark this value as signed for shift/div/compare lowering."""
        return Wire(self.m, self.sig, signed=True)

    def as_unsigned(self) -> "Wire":
        """Mark this value as unsigned for shift/div/compare lowering."""
        return Wire(self.m, self.sig, signed=False)


@dataclass(frozen=True)
class ClockDomain:
    clk: Signal
    rst: Signal


@dataclass(frozen=True, eq=False)
class Reg:
    q: Wire
    clk: Signal
    rst: Signal
    en: Wire
    next: Wire
    init: Wire

    @property
    def ref(self) -> str:
        return self.q.ref

    @property
    def ty(self) -> str:
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

    def out(self) -> Wire:
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

    def __eq__(self, other: object) -> Wire:  # type: ignore[override]
        return self.q == other

    def __ne__(self, other: object) -> Wire:  # type: ignore[override]
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

    def select(self, a: Union[Wire, "Reg", Signal, int], b: Union[Wire, "Reg", Signal, int]) -> Wire:
        return self.q.select(a, b)

    def trunc(self, *, width: int) -> Wire:
        return self.q.trunc(width=width)

    def zext(self, *, width: int) -> Wire:
        return self.q.zext(width=width)

    def sext(self, *, width: int) -> Wire:
        return self.q.sext(width=width)

    def shl(self, *, amount: int) -> Wire:
        return self.q.shl(amount=amount)

    def __getitem__(self, idx: int | slice) -> Wire:
        return self.q[idx]

    def set(
        self,
        value: Union[Wire, "Reg", Signal, Connector, int, LiteralValue],
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

        def as_wire(v: Union[Wire, Reg, Signal, Connector, int, LiteralValue], *, width: int) -> Wire:
            if isinstance(v, Connector):
                v = v.read()
            if isinstance(v, Reg):
                return v.q
            if isinstance(v, Wire):
                if v.m is not m:
                    raise ValueError("cannot combine wires from different modules")
                return v
            if isinstance(v, Signal):
                return Wire(m, v)
            if isinstance(v, LiteralValue):
                lit_w, lit_signed = _coerce_literal_width(v, ctx_width=width, ctx_signed=v.signed)
                return Wire(m, Module.const(m, int(v.value), width=lit_w), signed=lit_signed)
            if isinstance(v, int):
                return m.const(int(v), width=width)
            raise TypeError(f"unsupported value type: {type(v).__name__}")

        next_w = as_wire(value, width=self.width)

        if isinstance(when, int) and int(when) == 1:
            m.assign(self.next, next_w)
            return

        cond = as_wire(when, width=1)
        if cond.ty != "i1":
            raise TypeError("when must be i1")
        m.assign(self.next, cond._select_internal(next_w, self))

    def __ilshift__(self, other: Union[Wire, "Reg", Signal, int, LiteralValue]) -> "Reg":
        self.set(other)
        return self


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

    @contextmanager
    def scope(self, name: str) -> Iterator[None]:
        self._scope_stack.append(str(name))
        try:
            yield
        finally:
            self._scope_stack.pop()

    def domain(self, name: str) -> ClockDomain:
        return ClockDomain(clk=self.clock(f"{name}_clk"), rst=self.reset(f"{name}_rst"))

    def create_domain(self, name: str, *, frequency_desc: str = "", reset_active_high: bool = False) -> Any:
        """V5 cycle-aware domain (next/prev/push/pop); see `pycircuit.v5.CycleAwareDomain`."""
        from .v5 import CycleAwareDomain

        _ = (frequency_desc, reset_active_high)
        return CycleAwareDomain(self, str(name))

    def input(  # type: ignore[override]
        self,
        name: str,
        *,
        width: int,
        signed: bool = False,
        shape: int | tuple[int, ...] | list[int] | None = None,
    ) -> Union[Wire, "Vec"]:
        """Declare a module input port.

        Scalar inputs return ``Wire``. Shaped inputs return a vector-backed
        ``Vec`` whose lanes are extracted lazily with ``pyc.v_get``.
        """
        if shape is None:
            return Wire(self, super().input(name, width=width), signed=bool(signed))
        dims = _normalize_shape_arg(shape)
        sig = super().input(name, shape=list(dims), width=width)
        return Vec._from_vector_signal(self, sig, signs=[bool(signed) for _ in range(dims[0])])

    def const(self, value: int, *, width: int) -> Wire:  # type: ignore[override]
        """Create an integer constant `Wire` (two's complement at `width`)."""
        return Wire(self, super().const(int(value), width=width), signed=(int(value) < 0))

    def output(self, name: str, value: Union[Wire, Reg, Signal, Connector, int, LiteralValue, "Vec"]) -> None:  # type: ignore[override]
        if isinstance(value, Connector):
            value = value.read()
        if isinstance(value, Vec):
            info = value._as_vector_signal()
            if info is None:
                raise TypeError("output(Vec) requires a rectangular Vec of wire-like elements")
            _m, sig, _signs = info
            if _m is not self:
                raise ValueError("output(Vec) elements must belong to this module")
            super().output(name, sig)
            return
        if isinstance(value, Reg):
            super().output(name, value.q.sig)
            return
        if isinstance(value, Wire):
            super().output(name, value.sig)
            return
        if isinstance(value, Signal):
            super().output(name, value)
            return
        if isinstance(value, LiteralValue):
            lit_w, _ = _coerce_literal_width(value, ctx_width=value.width, ctx_signed=value.signed)
            super().output(name, super().const(int(value.value), width=lit_w))
            return
        if isinstance(value, int):
            w = infer_literal_width(int(value), signed=(int(value) < 0))
            super().output(name, super().const(int(value), width=w))
            return
        raise TypeError(f"output() expects Wire/Reg/Signal/Connector/int/literal, got {type(value).__name__}")

    def new_wire(self, *, width: int) -> Wire:
        return Wire(self, super().new_wire(width=width), assignable=True)

    def named_wire(self, name: str, *, width: int) -> Wire:
        return Wire(self, super().new_wire(width=width, name=self.scoped_name(name)), assignable=True)

    def wire(self, sig: Signal) -> Wire:
        return Wire(self, sig)

    def named(self, v: Union[Wire, Reg, Signal], name: str) -> Wire:
        """Attach a scoped debug name via `pyc.alias` (pure)."""
        if isinstance(v, Reg):
            v = v.q
        if isinstance(v, Wire):
            return Wire(self, self.alias(v.sig, name=self.scoped_name(name)), signed=v.signed)
        return Wire(self, self.alias(v, name=self.scoped_name(name)))

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
    ) -> None:  # type: ignore[override]
        if isinstance(dst, Connector):
            if isinstance(dst, RegConnector):
                dst.set(src)
                return
            dst = dst.read()
        if isinstance(src, Connector):
            src = src.read()

        def as_sig(v: Union[Wire, Reg, Signal]) -> Signal:
            if isinstance(v, Reg):
                return v.q.sig
            if isinstance(v, Wire):
                return v.sig
            return v

        def is_signed_src(v: Union[Wire, Reg, Signal, int, LiteralValue]) -> bool:
            if isinstance(v, Wire):
                return bool(v.signed)
            if isinstance(v, Reg):
                return bool(v.q.signed)
            if isinstance(v, LiteralValue):
                if v.signed is not None:
                    return bool(v.signed)
                return int(v.value) < 0
            return False

        dst_sig = as_sig(dst)
        if isinstance(src, LiteralValue):
            lit_w, _ = _coerce_literal_width(src, ctx_width=_int_width(dst_sig.ty), ctx_signed=is_signed_src(src))
            src_sig = super().const(int(src.value), width=lit_w)
            super().assign(dst_sig, src_sig)
            return
        if isinstance(src, int):
            src_sig = super().const(int(src), width=_int_width(dst_sig.ty))
            super().assign(dst_sig, src_sig)
            return

        src_signed = is_signed_src(src)
        src_sig = as_sig(src)
        if dst_sig.ty == src_sig.ty:
            super().assign(dst_sig, src_sig)
            return

        # Implicit integer resizing for convenience (zext smaller, trunc larger).
        if dst_sig.ty.startswith("i") and src_sig.ty.startswith("i"):
            dst_w = _int_width(dst_sig.ty)
            src_w = _int_width(src_sig.ty)
            if src_w < dst_w:
                src_sig = super().sext(src_sig, width=dst_w) if src_signed else super().zext(src_sig, width=dst_w)
            elif src_w > dst_w:
                src_sig = super().trunc(src_sig, width=dst_w)
            super().assign(dst_sig, src_sig)
            return

        raise TypeError(f"assign requires same types, got {dst_sig.ty} and {src_sig.ty}")

    def assert_(self, cond: Union[Wire, Reg, Signal], *, msg: str | None = None) -> None:
        c = cond.q if isinstance(cond, Reg) else cond
        sig = c.sig if isinstance(c, Wire) else c
        super().assert_(sig, msg=msg)

    def out(
        self,
        name: str,
        *,
        clk: Signal | None = None,
        rst: Signal | None = None,
        domain: ClockDomain | None = None,
        width: int,
        init: Union[Wire, Reg, Signal, int, LiteralValue] = 0,
        en: Union[Wire, Signal, int, LiteralValue] = 1,
        shape: int | tuple[int, ...] | None = None,
        stage: str | None = None,
        signed: bool | None = None,  # reserved for future type inference / lowering
    ) -> Union[Reg, "Vec"]:
        """Declare a named stateful variable (backedge register).

        This is a higher-level replacement for `backedge_reg(...)` that:
        - takes a stable logical name (for debug/name mangling),
        - optionally tags the name with a pipeline stage prefix,
        - declares a named backedge wire for `next`.
        """
        _ = signed  # unused for now (kept for API stability)

        if domain is not None:
            clk = domain.clk
            rst = domain.rst
        if clk is None or rst is None:
            raise TypeError("out() requires either domain=... or both clk=... and rst=...")

        if shape is not None:
            dims = _normalize_shape_arg(shape)

            def pick_axis(v: Any, idx: int) -> Any:
                if isinstance(v, Vec):
                    return v[idx]
                if isinstance(v, (list, tuple)):
                    return v[idx]
                return v

            def build_shaped(base_name: str, axis: int, axis_init: Any, axis_en: Any) -> "Vec | Reg":
                if axis == len(dims):
                    return self.out(
                        base_name,
                        clk=clk,
                        rst=rst,
                        width=width,
                        init=axis_init,
                        en=axis_en,
                        stage=stage,
                        signed=signed,
                    )
                n = dims[axis]
                elems: list[Union[Wire, Reg, Vec]] = []
                for i in range(n):
                    child_name = f"{base_name}_{i}"
                    child = build_shaped(
                        child_name,
                        axis + 1,
                        pick_axis(axis_init, i),
                        pick_axis(axis_en, i),
                    )
                    elems.append(child)
                return Vec(elems)

            return build_shaped(str(name), 0, init, en)

        full = str(name)
        if stage:
            full = f"{stage}__{full}"
        full = self.scoped_name(full)

        next_w = Wire(self, super().new_wire(width=width, name=f"{full}__next"), assignable=True)
        if isinstance(en, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(en, ctx_width=1, ctx_signed=False)
            en_w = Wire(self, super().const(int(en.value), width=lit_w), signed=lit_signed)
        elif isinstance(en, int):
            en_w: Union[Wire, Signal] = self.const(int(en), width=1)
        else:
            en_w = en

        if isinstance(init, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(init, ctx_width=width, ctx_signed=init.signed)
            init_w = Wire(self, super().const(int(init.value), width=lit_w), signed=lit_signed)
        elif isinstance(init, int):
            init_w: Union[Wire, Signal] = self.const(int(init), width=width)
        else:
            init_w = init

        r = self.reg_wire(clk, rst, en_w, next_w, init_w)
        # Name the observable value of the state variable.
        q_named = Wire(self, self.alias(r.q.sig, name=full), signed=r.q.signed)
        return Reg(q=q_named, clk=r.clk, rst=r.rst, en=r.en, next=r.next, init=r.init)

    def reg_wire(
        self,
        clk: Signal,
        rst: Signal,
        en: Union[Wire, Signal],
        next_: Union[Wire, Signal],
        init: Union[Wire, Signal, int, LiteralValue],
    ) -> Reg:
        en_w = en if isinstance(en, Wire) else Wire(self, en)
        next_w = next_ if isinstance(next_, Wire) else Wire(self, next_)
        if isinstance(init, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(init, ctx_width=next_w.width, ctx_signed=next_w.signed)
            init_w = Wire(self, super().const(int(init.value), width=lit_w), signed=lit_signed)
        elif isinstance(init, int):
            init_w = self.const(init, width=next_w.width)
        else:
            init_w = init if isinstance(init, Wire) else Wire(self, init)

        self._record_struct_state_alloc()
        q_sig = self.reg(clk, rst, en_w.sig, next_w.sig, init_w.sig)
        q_w = Wire(self, q_sig, signed=(next_w.signed or init_w.signed))
        return Reg(q=q_w, clk=clk, rst=rst, en=en_w, next=next_w, init=init_w)

    def reg_domain(
        self,
        domain: ClockDomain,
        en: Union[Wire, Signal],
        next_: Union[Wire, Signal],
        init: Union[Wire, Signal, int, LiteralValue],
    ) -> Reg:
        return self.reg_wire(domain.clk, domain.rst, en, next_, init)

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
        return self.reg_wire(clk, rst, en_w, next_w, init)

    def vec(self, *elems: Union["Wire", "Reg"]) -> "Vec":
        return Vec(elems)

    def cat(self, *elems: Union["Wire", "Reg", int, LiteralValue]) -> Wire:
        """Concatenate values into a packed bus (MSB-first)."""
        if not elems:
            raise ValueError("cat() requires at least one element")
        ws: list[Union[Wire, Reg]] = []
        for e in elems:
            if isinstance(e, (Wire, Reg)):
                ws.append(e)
                continue
            if isinstance(e, LiteralValue):
                lit_w, lit_signed = _coerce_literal_width(e, ctx_width=e.width, ctx_signed=e.signed)
                ws.append(Wire(self, super().const(int(e.value), width=lit_w), signed=lit_signed))
                continue
            if isinstance(e, int):
                w = infer_literal_width(int(e), signed=(int(e) < 0))
                ws.append(self.const(int(e), width=w))
                continue
            raise TypeError(f"cat() element must be Wire/Reg/int/literal, got {type(e).__name__}")
        return self.vec(*ws).pack()

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

            # Output port placeholder with signedness tracking on the connector.
            w_sig = super().new_wire(width=int(leaf.width), name=self.scoped_name(port))
            w = Wire(self, w_sig, signed=bool(leaf.signed), assignable=True)
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
                sig_port_specs[pname] = {"kind": "wire", "ty": rv.ty, "signed": bool(getattr(rv, "signed", False))}
                continue
            if isinstance(rv, Signal):
                if rv.ty == "!pyc.clock":
                    sig_port_specs[pname] = {"kind": "clock"}
                elif rv.ty == "!pyc.reset":
                    sig_port_specs[pname] = {"kind": "reset"}
                elif rv.ty.startswith("i"):
                    sig_port_specs[pname] = {"kind": "wire", "ty": rv.ty, "signed": bool(getattr(c, "signed", False))}
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

        def coerce_to_sig(c: Connector, *, expected_ty: str, port: str) -> Signal:
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
            if sig.ty.startswith("i") and expected_ty.startswith("i"):
                got_w = _int_width(sig.ty)
                exp_w = _int_width(expected_ty)
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
        def as_sig(v: Union[Wire, Reg, Signal]) -> Signal:
            if isinstance(v, Reg):
                return v.q.sig
            if isinstance(v, Wire):
                return v.sig
            return v

        rdata = super().byte_mem(
            clk,
            rst,
            as_sig(raddr),
            as_sig(wvalid),
            as_sig(waddr),
            as_sig(wdata),
            as_sig(wstrb),
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
        def as_sig(v: Union[Wire, Reg, Signal]) -> Signal:
            if isinstance(v, Reg):
                return v.q.sig
            if isinstance(v, Wire):
                return v.sig
            return v

        rdata = super().sync_mem(
            clk,
            rst,
            as_sig(ren),
            as_sig(raddr),
            as_sig(wvalid),
            as_sig(waddr),
            as_sig(wdata),
            as_sig(wstrb),
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
        def as_sig(v: Union[Wire, Reg, Signal]) -> Signal:
            if isinstance(v, Reg):
                return v.q.sig
            if isinstance(v, Wire):
                return v.sig
            return v

        rdata0, rdata1 = super().sync_mem_dp(
            clk,
            rst,
            as_sig(ren0),
            as_sig(raddr0),
            as_sig(ren1),
            as_sig(raddr1),
            as_sig(wvalid),
            as_sig(waddr),
            as_sig(wdata),
            as_sig(wstrb),
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
        def as_sig(v: Union[Wire, Reg, Signal]) -> Signal:
            if isinstance(v, Reg):
                return v.q.sig
            if isinstance(v, Wire):
                return v.sig
            return v

        in_ready, out_valid, out_data = super().async_fifo(
            in_clk,
            in_rst,
            out_clk,
            out_rst,
            as_sig(in_valid),
            as_sig(in_data),
            as_sig(out_ready),
            depth=depth,
        )
        self._record_struct_state_alloc()
        return Wire(self, in_ready), Wire(self, out_valid), Wire(self, out_data)

    def cdc_sync(self, clk: Signal, rst: Signal, a: Union[Wire, Reg, Signal], *, stages: int | None = None) -> Wire:
        sig = a.q.sig if isinstance(a, Reg) else (a.sig if isinstance(a, Wire) else a)
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

        def as_sig(v: Union[Wire, Reg, Signal]) -> Signal:
            if isinstance(v, Reg):
                return v.q.sig
            if isinstance(v, Wire):
                return v.sig
            return v

        in_ready, out_valid, out_data = super().fifo(
            clk,
            rst,
            as_sig(in_valid),
            as_sig(in_data),
            as_sig(out_ready),
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


def _scalar_to_wire(m: Module, value: Any, *, width: int) -> Wire:
    """将多种标量类型（Connector/Reg/Wire/Signal/LiteralValue/int）统一转为 Wire。"""
    if isinstance(value, Connector):
        value = value.read()
    if isinstance(value, Reg):
        value = value.q
    if isinstance(value, Wire):
        if value.m is not m:
            raise ValueError("cannot combine wires from different modules")
        return value
    if isinstance(value, Signal):
        return Wire(m, value)
    if isinstance(value, LiteralValue):
        lit_w, lit_signed = _coerce_literal_width(value, ctx_width=width, ctx_signed=value.signed)
        return Wire(m, Module.const(m, int(value.value), width=int(lit_w)), signed=lit_signed)
    if isinstance(value, int):
        return Wire(m, Module.const(m, int(value), width=int(width)), signed=(int(value) < 0))
    raise TypeError(f"unsupported operand type: {type(value).__name__}")


def _scalar_signed(value: Any) -> bool | None:
    """判断标量值是否有符号；无法识别则返回 None。"""
    if isinstance(value, Connector):
        value = value.read()
    if isinstance(value, Reg):
        return bool(value.signed)
    if isinstance(value, Wire):
        return bool(value.signed)
    if isinstance(value, LiteralValue):
        return bool(value.signed)
    if isinstance(value, int):
        return int(value) < 0
    if isinstance(value, Signal):
        return False
    return None


def _shift_amount_wire(m: Module, amount: Any) -> Wire | None:
    """将移位量转为 Wire；失败返回 None（由调用方回退到逐元素路径）。"""
    try:
        return _scalar_to_wire(m, amount, width=32)
    except TypeError:
        return None



@dataclass(frozen=True)
class Vec:
    """A small fixed-length container of wires/regs for building pipelines."""

    elems: list[Union[Wire, Reg, "Vec"]]
    _vector_module: Module | None = None
    _vector_sig: Signal | None = None
    _vector_signs: list[bool] | None = None
    _lane_cache: dict[int, Union[Wire, "Vec"]] = field(default_factory=dict, compare=False)

    @staticmethod
    def _normalize_init_elem(e: Any) -> Union[Wire, Reg, "Vec"]:
        if isinstance(e, (Wire, Reg, Vec)):
            return e

        # Accept cycle-aware wrappers only when they denote the current-cycle
        # value.  Reg is intentionally left untouched; Vec([reg]) should still
        # expose Reg elements rather than silently rewriting them to reg.q.
        cas = getattr(e, "_cas", e)
        w = getattr(cas, "_w", None)
        cycle = getattr(cas, "_cycle", None)
        if isinstance(w, Wire) and cycle is not None:
            if int(cycle) != 0:
                raise ValueError(f"Vec only accepts cycle-aware signals at cycle=0, got cycle={int(cycle)}")
            return w

        return e

    def __post_init__(self) -> None:
        if not isinstance(self.elems, list):
            try:
                object.__setattr__(self, "elems", list(self.elems))
            except TypeError as e:
                raise TypeError("Vec expects an iterable of elements") from e
        object.__setattr__(self, "elems", [self._normalize_init_elem(e) for e in self.elems])

        if self._vector_sig is not None:
            if self._vector_module is None:
                raise ValueError("vector-backed Vec requires a module")
            if self._vector_signs is None:
                raise ValueError("vector-backed Vec requires lane signedness")
            shape, _ = self._vector_shape_elem_type(self._vector_sig.ty)
            if shape[0] != len(self._vector_signs):
                raise ValueError("vector-backed Vec lane count must match vector type")
            if self.elems:
                raise ValueError("vector-backed Vec must not also store eager elements")
            return

        if not self.elems:
            raise ValueError("Vec cannot be empty")

        first = self.elems[0]
        if isinstance(first, Vec):
            return  # nested Vec — defer module check to innermost Vec
        m0 = self._module_of(first)
        for e in self.elems[1:]:
            if isinstance(e, Vec):
                continue
            if self._module_of(e) is not m0:
                raise ValueError("Vec elements must belong to the same Circuit/Module")

    @staticmethod
    def _module_of(e: Union[Wire, Reg, "Vec"]) -> Module:
        if isinstance(e, Wire):
            return e.m
        if isinstance(e, Reg):
            return e.q.m
        return e.m  # fallback (Vec or other with .m)

    @property
    def m(self) -> Module:
        if self._vector_module is not None:
            return self._vector_module
        return self._module_of(self.elems[0])

    def __len__(self) -> int:
        if self._vector_sig is not None:
            return len(self._vector_signs or [])
        return len(self.elems)

    def __iter__(self) -> Iterator[Union[Wire, Reg]]:
        if self._vector_sig is not None:
            return iter([self[i] for i in range(len(self))])
        return iter(self.elems)

    @overload
    def __getitem__(self, idx: int) -> Union[Wire, Reg]: ...

    @overload
    def __getitem__(self, idx: slice) -> "Vec": ...

    @overload
    def __getitem__(self, idx: tuple[int | slice, ...]) -> Union[Wire, Reg, "Vec"]: ...

    def __getitem__(self, idx: int | slice | tuple[int | slice, ...]) -> Union[Wire, Reg, "Vec"]:
        if self._vector_sig is not None:
            if isinstance(idx, tuple):
                if len(idx) == 0:
                    return self
                head, *tail = idx
                elem = self[head]
                if not tail:
                    return elem
                if not isinstance(elem, Vec):
                    raise TypeError("tuple indexing into nested dimensions requires Vec elements")
                rest = tuple(tail) if len(tail) > 1 else tail[0]
                return elem[rest]
            if isinstance(idx, slice):
                return Vec([self[i] for i in range(len(self))[idx]])
            lane = int(idx)
            if lane < 0:
                lane += len(self)
            if lane < 0 or lane >= len(self):
                raise IndexError("Vec index out of range")
            cached = self._lane_cache.get(lane)
            if cached is not None:
                return cached
            assert self._vector_sig is not None
            assert self._vector_signs is not None
            lane_sig = self.m.v_get(self._vector_sig, index=lane)
            if lane_sig.ty.startswith("vector<"):
                shape, _elem_ty = self._vector_shape_elem_type(lane_sig.ty)
                sub_signs = [bool(self._vector_signs[lane]) for _ in range(shape[0])]
                sub_vec = self._from_vector_signal(self.m, lane_sig, signs=sub_signs)
                self._lane_cache[lane] = sub_vec
                return sub_vec
            wire = Wire(self.m, lane_sig, signed=bool(self._vector_signs[lane]))
            self._lane_cache[lane] = wire
            return wire

        if isinstance(idx, tuple):
            if len(idx) == 0:
                return self
            head, *tail = idx
            if isinstance(head, slice):
                selected = self.elems[head]
                if not tail:
                    return Vec(selected)
                rest: int | slice | tuple[int | slice, ...]
                rest = tuple(tail) if len(tail) > 1 else tail[0]
                out: list[Union[Wire, Reg, Vec]] = []
                for e in selected:
                    if not isinstance(e, Vec):
                        raise TypeError("tuple indexing into nested dimensions requires Vec elements")
                    out.append(e[rest])
                return Vec(out)
            elem = self.elems[int(head)]
            if not tail:
                return elem
            if not isinstance(elem, Vec):
                raise TypeError("tuple indexing into nested dimensions requires Vec elements")
            rest = tuple(tail) if len(tail) > 1 else tail[0]
            return elem[rest]
        if isinstance(idx, slice):
            return Vec(self.elems[idx])
        return self.elems[int(idx)]

    # -- internal helpers -------------------------------------------------------

    @classmethod
    def _vector_shape_elem_type(cls, ty: str) -> tuple[list[int], str]:
        """从 MLIR 向量类型字符串解析 shape 和元素类型。
        如 ``vector<4xi8>`` → ``([4], i8)``；``vector<4xvector<3xi8>>`` → ``([4, 3], i8)``。
        维度是纯数字，从左边连续取；遇非数字段时剩余部分即元素类型。
        """
        raw = str(ty).strip()
        if not (raw.startswith("vector<") and raw.endswith(">")):
            raise ValueError(f"expected vector type, got {ty!r}")
        body = raw[len("vector<") : -1]
        parts = body.split("x")
        dims: list[int] = []
        i = 0
        while i < len(parts) and parts[i].strip().isdigit():
            d = int(parts[i])
            if d <= 0:
                raise ValueError(f"vector lanes must be > 0, got {d}")
            dims.append(d)
            i += 1
        if i == 0 or i == len(parts):
            raise ValueError(f"invalid vector type: {ty!r}")
        return dims, "x".join(parts[i:])

    @classmethod
    def _from_vector_signal(cls, m: Module, sig: Signal, *, signs: list[bool]) -> "Vec":
        """从底层向量 Signal 构造惰性 Vec（elems 为空，按需提取 lane）。"""
        return cls([], _vector_module=m, _vector_sig=sig, _vector_signs=list(signs))

    @staticmethod
    def _wire_of(e: Union[Wire, Reg, "Vec"]) -> Wire:
        """Reg → Reg.q, Wire → itself, Vec → pass through."""
        if isinstance(e, Wire):
            return e
        if isinstance(e, Reg):
            return e.q
        return e  # Vec or other — pass through for recursive op dispatch

    def _map1(self, fn: Callable[[Wire], Wire]) -> "Vec":
        """逐元素一元运算。元素可以是 Wire/Reg/Vec（嵌套递归）。"""
        return Vec([fn(e) for e in self])

    def _map2(self, other: Any, fn: Callable[[Any, Any], Any]) -> "Vec":
        """逐元素二元运算。fn 直接作用于元素，Python 运算符分发自动处理嵌套 Vec。"""
        if isinstance(other, Vec):
            if len(other) != len(self):
                raise ValueError(f"Vec size mismatch: {len(self)} vs {len(other)}")
            return Vec([fn(e1, e2) for e1, e2 in zip(self, other)])
        if isinstance(other, (Wire, Reg, int)):
            scalar = other if isinstance(other, Wire) else (other.q if isinstance(other, Reg) else other)
            return Vec([fn(e, scalar) for e in self])
        return NotImplemented

    def _leaf_wires(self) -> list[Wire] | None:
        """若为 1-D 叶子 Vec，返回所有 Wire 列表；含嵌套 Vec 或空则返回 None。"""
        if self._vector_sig is not None:
            return [self[i] for i in range(len(self))]
        if not self.elems or any(isinstance(e, Vec) for e in self.elems):
            return None
        out = [self._wire_of(e) for e in self.elems]
        if not out:
            return None
        m0, ty0 = out[0].m, out[0].ty
        for w in out[1:]:
            if w.m is not m0 or w.ty != ty0:
                return None
        return out
    def _any_lane_signed(self) -> bool:
        """判断是否存在任何 lane 为有符号类型。"""
        if self._vector_signs is not None:
            return any(self._vector_signs)
        ws = self._leaf_wires()
        if ws is not None:
            return any(w.signed for w in ws)
        return any(e._any_lane_signed() for e in self.elems if isinstance(e, Vec))

    def _as_vector_signal(self) -> tuple[Module, Signal, list[bool]] | None:
        """尝试将 Vec 提升为单一向量 Signal。
        1) 已为 vector-backed → 直接返回；
        2) 所有元素为同构子 Vec → v_create 合并成高维向量；
        3) 叶子 Wire → v_create 构造 1-D 向量，或识别 v_get 链路反推原始向量。
        提升失败返回 None，调用方回退到逐元素映射路径。
        """
        if self._vector_sig is not None:
            assert self._vector_signs is not None
            return self.m, self._vector_sig, list(self._vector_signs)
        if self.elems and all(isinstance(e, Vec) for e in self.elems):
            row_infos = [e._as_vector_signal() for e in self.elems]
            if all(info is not None for info in row_infos):
                infos = [info for info in row_infos if info is not None]
                m, first_sig, _first_signs = infos[0]
                if all(row_m is m and row_sig.ty == first_sig.ty for row_m, row_sig, _row_signs in infos):
                    return (
                        m,
                        m.v_create([row_sig for _row_m, row_sig, _row_signs in infos]),
                        [any(row_signs) for _row_m, _row_sig, row_signs in infos],
                    )
        ws = self._leaf_wires()
        if ws is None:
            return None
        m = ws[0].m
        vec_get_map = getattr(m, "_vec_get_map", None)
        if isinstance(vec_get_map, dict):
            lane_info: list[tuple[str, int]] = []
            for w in ws:
                info = vec_get_map.get(w.sig.ref)
                if info is None:
                    lane_info = []
                    break
                lane_info.append(info)
            if lane_info:
                src_ref = lane_info[0][0]
                if all(ref == src_ref for ref, _idx in lane_info):
                    idxs = [idx for _ref, idx in lane_info]
                    if idxs == list(range(len(ws))):
                        return m, Signal(ref=src_ref, ty=f"vector<{len(ws)}x{ws[0].ty}>"), [bool(w.signed) for w in ws]
        return m, m.v_create([w.sig for w in ws]), [bool(w.signed) for w in ws]

    def _vector_operand_info(
        self,
        value: Any,
        *,
        module: Module,
        width: int,
        shape: list[int],
        lanes: int,
        vector_ty: str,
    ) -> tuple[Signal, list[bool]] | None:
        """获取二元向量运算的右操作数信息。
        若 value 为同模同型 Vec → 取其向量 Signal + signs；
        若为标量 → 转 Wire 后广播为 lanes 份相同 signed 标记。
        不匹配则返回 None，调用方回退到逐元素路径。
        """
        if isinstance(value, Vec):
            info = value._as_vector_signal()
            if info is None:
                return None
            value_m, value_sig, value_signs = info
            value_shape, _value_elem_ty = self._vector_shape_elem_type(value_sig.ty)
            if (
                value_m is not module
                or value_sig.ty != vector_ty
                or value_shape != shape
                or len(value_signs) != lanes
            ):
                return None
            return value_sig, value_signs

        try:
            scalar = _scalar_to_wire(module, value, width=width)
        except TypeError:
            return None
        if scalar.m is not module:
            return None
        return scalar.sig, [bool(scalar.signed) for _ in range(lanes)]

    def _try_vector_unary(self, op: str) -> "Vec | None":
        """尝试用原生向量指令做一元运算（目前仅支持 ``not``）。失败返回 None。"""
        info = self._as_vector_signal()
        if info is None:
            return None
        m, in_sig, signs = info
        if op == "not":
            out_sig = m.not_(in_sig)
            return self._from_vector_signal(m, out_sig, signs=signs)
        return None

    def _try_vector_binary(self, other: Any, op: str, *, reverse: bool = False) -> "Vec | None":
        """尝试用原生向量指令做二元运算（add/sub/mul/and/or/xor 等）。
        成功返回新的惰性 Vec；失败返回 None，调用方回退到逐元素 ``_map2`` 路径。
        """
        lhs_info = self._as_vector_signal()
        method_name = _VEC_BINARY_METHODS.get(op)
        if lhs_info is None or method_name is None:
            return None
        m, lhs_sig, lhs_signs = lhs_info
        shape, elem_ty = self._vector_shape_elem_type(lhs_sig.ty)
        width = _int_width(elem_ty)

        rhs_info = self._vector_operand_info(
            other,
            module=m,
            width=width,
            shape=shape,
            lanes=len(lhs_signs),
            vector_ty=lhs_sig.ty,
        )
        if rhs_info is None:
            return None
        rhs_sig, rhs_signs = rhs_info

        a_sig, b_sig = (rhs_sig, lhs_sig) if reverse else (lhs_sig, rhs_sig)
        builder = getattr(m, method_name, None)
        if not callable(builder):
            return None
        out_sig = builder(a_sig, b_sig)
        out_signs = [False for _ in lhs_signs] if op in _VEC_COMPARE_OPS else [ls or rs for ls, rs in zip(lhs_signs, rhs_signs)]
        return self._from_vector_signal(m, out_sig, signs=out_signs)

    def _try_vector_signed_binary(
        self,
        other: Any,
        *,
        unsigned_op: str,
        signed_op: str,
        reverse: bool = False,
    ) -> "Vec | None":
        """尝试用向量指令做需区分有无符号的二元运算（div/mod）。
        根据两侧 signed 标记选择 signed_op 或 unsigned_op，然后委托给 _try_vector_binary。
        """
        lhs_info = self._as_vector_signal()
        if lhs_info is None:
            return None
        _m, _lhs_sig, lhs_signs = lhs_info
        if isinstance(other, Vec):
            rhs_info = other._as_vector_signal()
            if rhs_info is None:
                return None
            _rhs_m, _rhs_sig, rhs_signs = rhs_info
            rhs_signed = any(rhs_signs)
        else:
            rhs_signed_opt = _scalar_signed(other)
            if rhs_signed_opt is None:
                return None
            rhs_signed = rhs_signed_opt
        op = signed_op if any(lhs_signs) or rhs_signed else unsigned_op
        return self._try_vector_binary(other, op, reverse=reverse)

    def _try_vector_lt(self, other: Any, *, reverse: bool = False) -> "Vec | None":
        """尝试用向量指令做比较运算（根据 signed 选 slt 或 ult）。"""
        return self._try_vector_signed_binary(other, unsigned_op="ult", signed_op="slt", reverse=reverse)

    def _try_vector_select(self, a: Any, b: Any) -> "Vec | None":
        """尝试用向量指令做 mux。

        ``sel`` 必须为 i1 向量。a/b 可以是同模同型 Vec，也可以是
        与另一侧 Vec leaf width 匹配的标量；标量 arm 会广播到 Vec shape。
        """
        sel_info = self._as_vector_signal()
        if sel_info is None:
            return None
        m, sel_sig, _sel_signs = sel_info
        sel_shape, sel_elem_ty = self._vector_shape_elem_type(sel_sig.ty)
        if sel_elem_ty != "i1":
            return None

        a_vec_info = a._as_vector_signal() if isinstance(a, Vec) else None
        b_vec_info = b._as_vector_signal() if isinstance(b, Vec) else None
        if a_vec_info is None and b_vec_info is None:
            return None

        vec_info = a_vec_info if a_vec_info is not None else b_vec_info
        assert vec_info is not None
        vec_m, vec_sig, vec_signs = vec_info
        if vec_m is not m:
            return None
        value_shape, value_elem_ty = self._vector_shape_elem_type(vec_sig.ty)
        if value_shape != sel_shape:
            return None

        def arm_signal(value: Any, info: tuple[Module, Signal, list[bool]] | None) -> tuple[Signal, list[bool]] | None:
            if info is not None:
                arm_m, arm_sig, arm_signs = info
                if arm_m is not m or arm_sig.ty != vec_sig.ty or len(arm_signs) != len(vec_signs):
                    return None
                return arm_sig, arm_signs
            scalar = _scalar_to_wire(m, value, width=_int_width(value_elem_ty))
            if scalar.m is not m:
                return None
            return self._broadcast_scalar_signal(m, scalar.sig, shape=value_shape), [
                bool(scalar.signed) for _ in vec_signs
            ]

        a_arm = arm_signal(a, a_vec_info)
        b_arm = arm_signal(b, b_vec_info)
        if a_arm is None or b_arm is None:
            return None
        a_sig, a_signs = a_arm
        b_sig, b_signs = b_arm
        out_sig = m.mux(sel_sig, a_sig, b_sig)
        return self._from_vector_signal(m, out_sig, signs=[as_ or bs for as_, bs in zip(a_signs, b_signs)])

    @staticmethod
    def _broadcast_scalar_signal(m: Module, scalar: Signal, *, shape: list[int]) -> Signal:
        if not shape:
            return scalar
        if len(shape) == 1:
            return m.v_broadcast(scalar, size=shape[0])
        row = Vec._broadcast_scalar_signal(m, scalar, shape=shape[1:])
        return m.v_create([row for _ in range(shape[0])])

    def _try_vector_cast(self, op: str, *, width: int) -> "Vec | None":
        """尝试用向量指令做宽度变换（trunc/zext/sext）。zext → 无符号，sext → 有符号。"""
        info = self._as_vector_signal()
        if info is None:
            return None
        m, in_sig, signs = info
        builder = getattr(m, op, None)
        if not callable(builder):
            return None
        out_sig = builder(in_sig, width=int(width))
        if op == "zext":
            out_signs = [False for _ in signs]
        elif op == "sext":
            out_signs = [True for _ in signs]
        else:
            out_signs = signs
        return self._from_vector_signal(m, out_sig, signs=out_signs)

    def _try_vector_shift(self, op: str, amount: Any) -> "Vec | None":
        """尝试用向量指令做移位。立即数用 shli/lshri/ashri，动态移位量用 shl/lshr/ashr。
        lshr 结果强制无符号。
        """
        info = self._as_vector_signal()
        if info is None:
            return None
        m, in_sig, signs = info
        if isinstance(amount, LiteralValue):
            amount = int(amount.value)
        if isinstance(amount, int):
            if int(amount) < 0:
                raise ValueError(f"{op} amount must be >= 0")
            immediate_ops = {"shl": "shli", "lshr": "lshri", "ashr": "ashri"}
            builder = getattr(m, immediate_ops[op], None)
            if not callable(builder):
                return None
            out_sig = builder(in_sig, amount=int(amount))
        else:
            amount_wire = _shift_amount_wire(m, amount)
            if amount_wire is None:
                return None
            builder = getattr(m, op, None)
            if not callable(builder):
                return None
            out_sig = builder(in_sig, amount_wire.sig)
        out_signs = [False for _ in signs] if op == "lshr" else signs
        return self._from_vector_signal(m, out_sig, signs=out_signs)

    @staticmethod
    def _tree_reduce_wires_1d(ws: list[Wire], *, is_or: bool) -> Wire:
        """1-D 树状归约：逐对 | (OR) 或 & (AND) 直到剩一个 Wire。"""
        if not ws:
            raise ValueError("reduce requires at least one element")
        cur = list(ws)
        while len(cur) > 1:
            nxt: list[Wire] = []
            i = 0
            while i < len(cur):
                if i + 1 < len(cur):
                    nxt.append((cur[i] | cur[i + 1]) if is_or else (cur[i] & cur[i + 1]))
                    i += 2
                else:
                    nxt.append(cur[i])
                    i += 1
            cur = nxt
        return cur[0]

    @staticmethod
    def _sum_output_width(ws: list[Wire], reduce_len: int, width: int | None) -> int:
        """从 Wire 列表和归约长度计算 sum 输出宽度。"""
        if not ws:
            raise ValueError("reduce_sum requires at least one element")
        return Vec._sum_output_width_from_input_width(max(int(w.width) for w in ws), reduce_len, width)

    @staticmethod
    def _sum_output_width_from_input_width(input_width: int, reduce_len: int, width: int | None) -> int:
        """从输入位宽和归约长度计算 sum 输出位宽：``width=None`` 时自动加进位位宽。"""
        max_width = int(input_width)
        if width is None:
            carry_width = max(0, int(reduce_len) - 1).bit_length()
            return max_width + carry_width
        out_width = int(width)
        if out_width < max_width:
            raise ValueError(f"reduce_sum width {out_width} is smaller than input width {max_width}")
        return out_width

    @staticmethod
    def _extend_sum_lane(w: Wire, *, width: int, signed: bool) -> Wire:
        """将单个 sum lane 扩展到输出宽度（有符号用 sext，无符号用 zext）。"""
        if int(w.width) < int(width):
            return w.sext(width=width) if signed else w.zext(width=width)
        return Wire(w.m, w.sig, signed=bool(signed))

    @classmethod
    def _tree_sum_wires_1d(cls, ws: list[Wire], *, width: int | None = None, signed: bool = False) -> Wire:
        """1-D 树状求和：先扩展各 lane 防溢出，再逐对相加直到剩一个 Wire。"""
        out_width = cls._sum_output_width(ws, len(ws), width)
        cur = [cls._extend_sum_lane(w, width=out_width, signed=bool(signed)) for w in ws]
        while len(cur) > 1:
            nxt: list[Wire] = []
            i = 0
            while i < len(cur):
                if i + 1 < len(cur):
                    nxt.append(cur[i] + cur[i + 1])
                    i += 2
                else:
                    nxt.append(cur[i])
                    i += 1
            cur = nxt
        return cur[0]

    def _is_leaf_1d(self) -> bool:
        """判断是否为 1-D 叶子 Vec（所有元素为 Wire/Reg，无嵌套 Vec）。"""
        if self._vector_sig is not None:
            shape, _ = self._vector_shape_elem_type(self._vector_sig.ty)
            return len(shape) == 1
        return all(not isinstance(e, Vec) for e in self.elems)

    @staticmethod
    def _reduced_vector_signs(in_shape: list[int], in_signs: list[bool], *, dim: int) -> list[bool]:
        """向量沿某轴归约后的符号标记。dim==0 → 保守取 any(in_signs)；否则尽量保留原标记。"""
        out_shape = [d for i, d in enumerate(in_shape) if i != dim]
        if not out_shape:
            return []
        # `_vector_signs` tracks the outer visible lane signs. After reducing
        # away that axis, use a conservative sign for each new outer lane.
        if dim == 0:
            return [any(in_signs) for _ in range(out_shape[0])]
        if len(in_signs) == out_shape[0]:
            return list(in_signs)
        return [any(in_signs) for _ in range(out_shape[0])]

    def _reduce_vector_backed(self, dim: int | None, *, is_or: bool) -> Union[Wire, "Vec"] | None:
        """用原生向量归约指令（v_or_reduce / v_and_reduce）。dim=None 归约到 Wire。"""
        if self._vector_sig is None:
            return None
        assert self._vector_signs is not None
        shape, _elem_ty = self._vector_shape_elem_type(self._vector_sig.ty)
        if dim is None:
            if len(shape) != 1:
                raise ValueError("reduce(dim=None) requires a 1-D Vec (leaf elements are Wire/Reg)")
            red_sig = self.m.v_or_reduce(self._vector_sig) if is_or else self.m.v_and_reduce(self._vector_sig)
            return Wire(self.m, red_sig, signed=any(self._vector_signs))

        reduce_dim = int(dim)
        if reduce_dim < 0 or reduce_dim >= len(shape):
            raise ValueError(f"reduce dim out of range: {reduce_dim} for Vec rank {len(shape)}")
        red_sig = (
            self.m.v_or_reduce(self._vector_sig, dim=reduce_dim)
            if is_or
            else self.m.v_and_reduce(self._vector_sig, dim=reduce_dim)
        )
        out_signs = self._reduced_vector_signs(shape, list(self._vector_signs), dim=reduce_dim)
        if not out_signs:
            return Wire(self.m, red_sig, signed=any(self._vector_signs))
        return self._from_vector_signal(self.m, red_sig, signs=out_signs)

    def _reduce_1d(self, *, is_or: bool) -> Wire:
        """1-D 归约到单个 Wire。优先用向量指令，否则树状归约。"""
        vector_reduced = self._reduce_vector_backed(None, is_or=is_or)
        if vector_reduced is not None:
            assert isinstance(vector_reduced, Wire)
            return vector_reduced
        if not self._is_leaf_1d():
            raise ValueError("reduce(dim=None) requires a 1-D Vec (leaf elements are Wire/Reg)")
        ws = [self._wire_of(e) for e in self]
        return self._tree_reduce_wires_1d(ws, is_or=is_or)

    def _sum_1d(self, *, width: int | None = None, signed: bool = False) -> Wire:
        """1-D 求和归约到单个 Wire。优先用向量指令，否则树状求和。"""
        vector_reduced = self._sum_vector_backed(None, width=width, signed=bool(signed))
        if vector_reduced is not None:
            assert isinstance(vector_reduced, Wire)
            return vector_reduced
        if not self._is_leaf_1d():
            raise ValueError("reduce_sum(dim=None) requires a 1-D Vec (leaf elements are Wire/Reg)")
        ws = [self._wire_of(e) for e in self]
        return self._tree_sum_wires_1d(ws, width=width, signed=bool(signed))

    def _sum_vector_backed(
        self,
        dim: int | None,
        *,
        width: int | None = None,
        signed: bool = False,
    ) -> Union[Wire, "Vec"] | None:
        """用原生向量求和指令（v_add_reduce）。必要时先扩展位宽防溢出。"""
        info = self._as_vector_signal()
        if info is None:
            return None
        m, sig, signs = info
        shape, elem_ty = self._vector_shape_elem_type(sig.ty)
        reduce_dim = 0 if dim is None else int(dim)
        if reduce_dim < 0 or reduce_dim >= len(shape):
            raise ValueError(f"reduce_sum dim out of range: {reduce_dim} for Vec rank {len(shape)}")
        if dim is None and len(shape) != 1:
            raise ValueError("reduce_sum(dim=None) requires a 1-D Vec (leaf elements are Wire/Reg)")
        elem_width = _int_width(elem_ty)
        out_width = self._sum_output_width_from_input_width(elem_width, shape[reduce_dim], width)
        if elem_width < out_width:
            sig = m.sext(sig, width=out_width) if signed else m.zext(sig, width=out_width)
        red_sig = m.v_add_reduce(sig, dim=None if dim is None else reduce_dim)
        out_shape = [d for i, d in enumerate(shape) if i != reduce_dim]
        if not out_shape:
            return Wire(m, red_sig, signed=bool(signed))
        out_signs = self._reduced_vector_signs(shape, [bool(signed) for _ in signs], dim=reduce_dim)
        return self._from_vector_signal(m, red_sig, signs=out_signs)

    def _as_children_vecs(self) -> list["Vec"]:
        """将嵌套 Vec 的子 Vec 展开为列表，同时校验矩形维度（所有子 Vec 等长）。"""
        if self._is_leaf_1d():
            raise ValueError("reduce dim out of range for Vec shape")
        out: list[Vec] = []
        for e in self.elems:
            if not isinstance(e, Vec):
                raise ValueError("nested reduce requires a regular Vec (all elements at this level must be Vec)")
            out.append(e)
        if not out:
            raise ValueError("reduce requires at least one element")
        n = len(out[0])
        for c in out[1:]:
            if len(c) != n:
                raise ValueError("nested reduce requires rectangular Vec dimensions")
        return out

    def _reduce_dim(self, dim: int, *, is_or: bool) -> Union[Wire, "Vec"]:
        """沿指定轴做 AND/OR 归约。优先用向量指令，否则递归降维。
        dim==0 且为 1-D 叶子时返回单元素 Vec（保持 rank 语义一致）。
        """
        if dim < 0:
            raise ValueError("reduce dim must be >= 0")

        vector_reduced = self._reduce_vector_backed(dim, is_or=is_or)
        if vector_reduced is not None:
            return vector_reduced

        if dim == 0:
            if self._is_leaf_1d():
                # dim-specified reductions return Vec; reducing a leaf row gives a
                # singleton Vec containing the reduced wire.
                return Vec([self._reduce_1d(is_or=is_or)])

            children = self._as_children_vecs()
            infos = [child._as_vector_signal() for child in children]
            if all(info is not None for info in infos):
                row_infos = [info for info in infos if info is not None]
                m, first_sig, first_signs = row_infos[0]
                if all(
                    row_m is m and row_sig.ty == first_sig.ty and len(row_signs) == len(first_signs)
                    for row_m, row_sig, row_signs in row_infos
                ):
                    rank2_sig = m.v_create([row_sig for _row_m, row_sig, _row_signs in row_infos])
                    red_sig = m.v_or_reduce(rank2_sig, dim=0) if is_or else m.v_and_reduce(rank2_sig, dim=0)
                    out_signs = [
                        any(row_signs[i] for _row_m, _row_sig, row_signs in row_infos)
                        for i in range(len(first_signs))
                    ]
                    return self._from_vector_signal(m, red_sig, signs=out_signs)

            width = len(children[0])
            out: list[Union[Wire, Reg, Vec]] = []
            for j in range(width):
                col = Vec([child[j] for child in children])
                # Reduce this column to a single wire so dim=0 lowers rank by one.
                out.append(col._reduce_1d(is_or=is_or))
            return Vec(out)

        children = self._as_children_vecs()
        if dim == 1:
            infos = [child._as_vector_signal() for child in children]
            if all(info is not None for info in infos):
                row_infos = [info for info in infos if info is not None]
                m, first_sig, first_signs = row_infos[0]
                if all(
                    row_m is m and row_sig.ty == first_sig.ty and len(row_signs) == len(first_signs)
                    for row_m, row_sig, row_signs in row_infos
                ):
                    rank2_sig = m.v_create([row_sig for _row_m, row_sig, _row_signs in row_infos])
                    red_sig = m.v_or_reduce(rank2_sig, dim=1) if is_or else m.v_and_reduce(rank2_sig, dim=1)
                    return self._from_vector_signal(
                        m,
                        red_sig,
                        signs=[any(row_signs) for _row_m, _row_sig, row_signs in row_infos],
                    )
        if dim == 1 and all(child._is_leaf_1d() for child in children):
            return Vec([child._reduce_1d(is_or=is_or) for child in children])
        return Vec([child._reduce_dim(dim - 1, is_or=is_or) for child in children])

    def _sum_dim(self, dim: int, *, width: int | None = None, signed: bool = False) -> Union[Wire, "Vec"]:
        """沿指定轴做求和归约。优先用向量指令，否则递归降维。
        dim==0 且为 1-D 叶子时返回单元素 Vec。
        """
        if dim < 0:
            raise ValueError("reduce_sum dim must be >= 0")

        vector_reduced = self._sum_vector_backed(dim, width=width, signed=bool(signed))
        if vector_reduced is not None:
            return vector_reduced

        if dim == 0:
            if self._is_leaf_1d():
                return Vec([self._sum_1d(width=width, signed=bool(signed))])

            children = self._as_children_vecs()
            # Try vector-backed path: promote children to rank-2 signal → v_add_reduce
            infos = [child._as_vector_signal() for child in children]
            if all(info is not None for info in infos):
                row_infos = [info for info in infos if info is not None]
                m, first_sig, first_signs = row_infos[0]
                if all(
                    row_m is m and row_sig.ty == first_sig.ty and len(row_signs) == len(first_signs)
                    for row_m, row_sig, row_signs in row_infos
                ):
                    rank2_sig = m.v_create([row_sig for _row_m, row_sig, _row_signs in row_infos])
                    outer_signs = [
                        any(row_signs[i] for _row_m, _row_sig, row_signs in row_infos)
                        for i in range(len(first_signs))
                    ]
                    tmp = Vec._from_vector_signal(m, rank2_sig, signs=outer_signs)
                    reduced = tmp._sum_vector_backed(0, width=width, signed=bool(signed))
                    if reduced is not None:
                        return reduced

            col_count = len(children[0])
            return Vec([
                Vec([child[j] for child in children])._sum_1d(width=width, signed=bool(signed))
                for j in range(col_count)
            ])

        children = self._as_children_vecs()
        if dim == 1 and all(child._is_leaf_1d() for child in children):
            return Vec([child._sum_1d(width=width, signed=bool(signed)) for child in children])
        return Vec([child._sum_dim(dim - 1, width=width, signed=bool(signed)) for child in children])

    def or_reduce(self, dim: int | None = None) -> Union[Wire, "Vec"]:
        """Bitwise OR reduction.

        - dim=None: require 1-D Vec, return a single Wire (tree reduction).
        - dim=int: reduce along that axis, return Vec.
        """
        if dim is None:
            return self._reduce_1d(is_or=True)
        return self._reduce_dim(int(dim), is_or=True)

    def and_reduce(self, dim: int | None = None) -> Union[Wire, "Vec"]:
        """Bitwise AND reduction.

        - dim=None: require 1-D Vec, return a single Wire (tree reduction).
        - dim=int: reduce along that axis, return Vec.
        """
        if dim is None:
            return self._reduce_1d(is_or=False)
        return self._reduce_dim(int(dim), is_or=False)

    def reduce_sum(
        self,
        *,
        width: int | None = None,
        dim: int | None = None,
        signed: bool = False,
    ) -> Union[Wire, "Vec"]:
        """Sum reduction.

        - Unsigned mode (default) zero-extends each lane before summing.
        - Signed mode sign-extends each lane before summing and returns signed wires.
        - ``width=None`` chooses ``max_input_width + ceil_log2(reduce_len)``.
        - ``dim=None`` requires a 1-D Vec and returns one Wire.
        - ``dim=int`` reduces along that axis and returns the lowered-rank Vec.
        """
        if dim is None:
            return self._sum_1d(width=width, signed=bool(signed))
        return self._sum_dim(int(dim), width=width, signed=bool(signed))

    # -- arithmetic -------------------------------------------------------------

    def __add__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "add")
        return v if v is not None else self._map2(other, lambda a, b: a + b)

    def __radd__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "add", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b + a)

    def __sub__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "sub")
        return v if v is not None else self._map2(other, lambda a, b: a - b)

    def __rsub__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "sub", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b - a)

    def __mul__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "mul")
        return v if v is not None else self._map2(other, lambda a, b: a * b)

    def __rmul__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "mul", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b * a)
    def __truediv__(self, other: Any) -> "Vec":
        raise TypeError("Vec `/` division is not supported; use `//` for integer division")

    def __rtruediv__(self, other: Any) -> "Vec":
        raise TypeError("Vec `/` division is not supported; use `//` for integer division")

    def __floordiv__(self, other: Any) -> "Vec":
        v = self._try_vector_signed_binary(other, unsigned_op="udiv", signed_op="sdiv")
        return v if v is not None else self._map2(other, lambda a, b: a // b)

    def __rfloordiv__(self, other: Any) -> "Vec":
        v = self._try_vector_signed_binary(other, unsigned_op="udiv", signed_op="sdiv", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b // a)

    def __mod__(self, other: Any) -> "Vec":
        v = self._try_vector_signed_binary(other, unsigned_op="urem", signed_op="srem")
        return v if v is not None else self._map2(other, lambda a, b: a % b)

    def __rmod__(self, other: Any) -> "Vec":
        v = self._try_vector_signed_binary(other, unsigned_op="urem", signed_op="srem", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b % a)

    # -- bitwise ----------------------------------------------------------------

    def __and__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "and")
        return v if v is not None else self._map2(other, lambda a, b: a & b)

    def __rand__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "and", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b & a)

    def __or__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "or")
        return v if v is not None else self._map2(other, lambda a, b: a | b)

    def __ror__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "or", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b | a)

    def __xor__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "xor")
        return v if v is not None else self._map2(other, lambda a, b: a ^ b)

    def __rxor__(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "xor", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: b ^ a)

    def __invert__(self) -> "Vec":
        v = self._try_vector_unary("not")
        return v if v is not None else self._map1(lambda a: ~a)

    # -- shifts -----------------------------------------------------------------

    def __lshift__(self, other: int) -> "Vec":
        if not isinstance(other, int):
            raise TypeError("<< only supports constant integer shift amounts")
        v = self._try_vector_shift("shl", other)
        return v if v is not None else self._map1(lambda a: a << other)

    def __rshift__(self, other: int) -> "Vec":
        if not isinstance(other, int):
            raise TypeError(">> only supports constant integer shift amounts")
        v = self._try_vector_shift("ashr" if self._any_lane_signed() else "lshr", other)
        return v if v is not None else self._map1(lambda a: a >> other)

    def lshr(self, *, amount: Union[int, Wire, Reg, Signal, LiteralValue]) -> "Vec":
        v = self._try_vector_shift("lshr", amount)
        return v if v is not None else self._map1(lambda a: a.lshr(amount=amount))

    def ashr(self, *, amount: Union[int, Wire, Reg, Signal, LiteralValue]) -> "Vec":
        v = self._try_vector_shift("ashr", amount)
        return v if v is not None else self._map1(lambda a: a.ashr(amount=amount))

    def shl(self, *, amount: Union[int, Wire, Reg, Signal, LiteralValue]) -> "Vec":
        v = self._try_vector_shift("shl", amount)
        return v if v is not None else self._map1(lambda a: a.shl(amount=amount))

    # -- comparison -------------------------------------------------------------

    def __eq__(self, other: object) -> "Vec":  # type: ignore[override]
        if not isinstance(other, (Vec, Wire, Reg, Signal, LiteralValue, int)):
            return NotImplemented
        v = self._try_vector_binary(other, "eq")
        return v if v is not None else self._map2(other, lambda a, b: a == b)

    def __ne__(self, other: object) -> "Vec":  # type: ignore[override]
        if not isinstance(other, (Vec, Wire, Reg, Signal, LiteralValue, int)):
            return NotImplemented
        return ~(self == other)

    def __lt__(self, other: Any) -> "Vec":
        v = self._try_vector_lt(other)
        return v if v is not None else self._map2(other, lambda a, b: a < b)

    def __gt__(self, other: Any) -> "Vec":
        v = self._try_vector_lt(other, reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: a > b)

    def __le__(self, other: Any) -> "Vec":
        return ~(self > other)

    def __ge__(self, other: Any) -> "Vec":
        return ~(self < other)

    def eq(self, other: Any) -> "Vec":            return self == other
    def ne(self, other: Any) -> "Vec":            return self != other
    def ult(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "ult")
        return v if v is not None else self._map2(other, lambda a, b: a.ult(b))

    def slt(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "slt")
        return v if v is not None else self._map2(other, lambda a, b: a.slt(b))

    def ugt(self, other: Any) -> "Vec":
        v = self._try_vector_binary(other, "ult", reverse=True)
        return v if v is not None else self._map2(other, lambda a, b: a.ugt(b))

    def ule(self, other: Any) -> "Vec":
        return ~self.ugt(other)

    def uge(self, other: Any) -> "Vec":
        return ~self.ult(other)

    # -- mux --------------------------------------------------------------------

    def select(self, a: Any, b: Any) -> "Vec":
        """Element-wise mux. self is Vec[i1], a/b are Vec[T] or scalar."""
        v = self._try_vector_select(a, b)
        if v is not None:
            return v
        if isinstance(a, Vec) and isinstance(b, Vec):
            if len(a) != len(self) or len(b) != len(self):
                raise ValueError("Vec size mismatch in select")
            return Vec([
                self._wire_of(s).select(va, vb)
                for s, va, vb in zip(self, a, b)
            ])
        return Vec([self._wire_of(e).select(a, b) for e in self])

    def masked_or(self, vals: Any, *, zero: Any = 0) -> Wire:
        """Select ``vals`` where this mask is true, then OR-reduce the lanes."""
        values = vals if isinstance(vals, Vec) else Vec(list(vals))
        return self.select(values, zero).or_reduce()

    def onehot_mux(self, vals: Any, *, zero: Any = 0) -> Wire:
        """One-hot mux implemented as masked OR over a Vec of candidate values."""
        return self.masked_or(vals, zero=zero)

    # -- width transforms -------------------------------------------------------

    def trunc(self, *, width: int) -> "Vec":
        v = self._try_vector_cast("trunc", width=width)
        return v if v is not None else self._map1(lambda a: a.trunc(width=width))

    def zext(self, *, width: int) -> "Vec":
        v = self._try_vector_cast("zext", width=width)
        return v if v is not None else self._map1(lambda a: a.zext(width=width))

    def sext(self, *, width: int) -> "Vec":
        v = self._try_vector_cast("sext", width=width)
        return v if v is not None else self._map1(lambda a: a.sext(width=width))

    def slice(self, *, lsb: int, width: int) -> "Vec":
        return self._map1(lambda a: a.slice(lsb=lsb, width=width))

    # -- existing methods -------------------------------------------------------

    def out(self) -> "Vec":
        """Return a Vec of wires by reading each leaf element.

        - Reg leaf: use `.out()`
        - Wire leaf: keep as-is
        """
        return self._map1(lambda a: a.out() if isinstance(a, Reg) else a)

    def wires(self) -> tuple[Wire, ...]:
        out: list[Wire] = []
        for e in self:
            out.append(e if isinstance(e, Wire) else e.q)
        return tuple(out)

    @property
    def total_width(self) -> int:
        return sum(w.width for w in self.wires())

    def pack(self) -> Wire:
        """Concatenate elements into a single bus wire (MSB-first).

        `Vec([a, b, c]).pack()` yields `{a, b, c}` in Verilog terms.
        """
        ws = self.wires()
        out_w = self.total_width
        if out_w <= 0:
            raise ValueError("cannot pack a zero-width Vec")

        m = ws[0].m
        concat = getattr(m, "concat", None)
        if callable(concat):
            return Wire(m, concat(*(w.sig for w in ws)))

        # Fallback: build packing from basic shifts + ors for minimal backends.
        if not isinstance(m, Circuit):
            raise TypeError("Vec.pack requires a Circuit/Module with a concat() builder")
        acc = m.const(0, width=out_w)
        lsb = 0
        for w in reversed(ws):
            part = w._zext(width=out_w)
            if lsb:
                part = part.shl(amount=lsb)
            acc = acc | part
            lsb += w.width
        return acc

    def unpack(self, packed: Wire) -> "Vec":
        """Extract elements from a packed bus (inverse of pack())."""
        ws = self.wires()
        if packed.width != self.total_width:
            raise ValueError(f"unpack width mismatch: got i{packed.width}, expected i{self.total_width}")

        parts_rev: list[Wire] = []
        lsb = 0
        for w in reversed(ws):
            parts_rev.append(packed.slice(lsb=lsb, width=w.width))
            lsb += w.width
        return Vec(list(reversed(parts_rev)))

    def regs_domain(
        self,
        domain: ClockDomain,
        en: Union[Wire, Signal, int],
        init: Union[Wire, Signal, int, LiteralValue] = 0,
    ) -> "Vec":
        """Create a register per element and return a Vec of Regs."""
        ws = self.wires()
        m = ws[0].m
        if not isinstance(m, Circuit):
            raise TypeError("regs_domain requires elements to belong to a Circuit")
        regs: list[Reg] = []
        for w in ws:
            regs.append(m.reg_domain(domain, en, w, init))
        return Vec(regs)


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
        m0 = Vec._module_of(vals[0])
        for v in vals[1:]:
            mv = Vec._module_of(v)
            if mv is not m0:
                raise ValueError("Bundle fields must belong to the same Circuit/Module")

    def __getitem__(self, key: str) -> Union[Wire, Reg]:
        return self.fields[str(key)]

    def items(self) -> Iterable[tuple[str, Union[Wire, Reg]]]:
        return self.fields.items()

    def pack(self) -> Wire:
        if not self.fields:
            raise ValueError("cannot pack an empty Bundle")
        elems = tuple(self.fields.values())
        return Vec(elems).pack()

    def unpack(self, packed: Wire) -> "Bundle":
        """Extract fields from a packed bus (inverse of pack())."""
        if not self.fields:
            raise ValueError("cannot unpack into an empty Bundle")
        elems = tuple(self.fields.values())
        vec = Vec(elems)
        parts = vec.unpack(packed)
        out: dict[str, Union[Wire, Reg]] = {}
        for (k, _), v in zip(self.fields.items(), parts.elems):
            out[k] = v
        return Bundle(out)


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
            if v.ty != "i1":
                raise TypeError(f"{ctx}: expected i1, got {v.ty}")
            return v
        if isinstance(v, Signal):
            if v.ty != "i1":
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

    ws: list[Union[Wire, Reg]] = []
    for e in elems:
        if isinstance(e, (Wire, Reg)):
            ws.append(e)
            continue
        if isinstance(e, LiteralValue):
            lit_w, lit_signed = _coerce_literal_width(e, ctx_width=e.width, ctx_signed=e.signed)
            ws.append(Wire(owner, Module.const(owner, int(e.value), width=lit_w), signed=lit_signed))
            continue
        if isinstance(e, int):
            w = infer_literal_width(int(e), signed=(int(e) < 0))
            if isinstance(owner, Circuit):
                ws.append(owner.const(int(e), width=w))
            else:
                ws.append(Wire(owner, Module.const(owner, int(e), width=w), signed=(int(e) < 0)))
            continue
        raise TypeError(f"cat() element must be Wire/Reg/int/literal, got {type(e).__name__}")
    return Vec(ws).pack()




def mux(*_args: Any, **_kwargs: Any) -> Wire:
    raise TypeError("mux() was removed from pyCircuit; use `true_v if cond else false_v` in JIT-compiled design code")


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
