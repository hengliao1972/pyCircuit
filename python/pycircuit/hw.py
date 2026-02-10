from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Union, overload

from .dsl import Module, Signal


def _int_width(ty: str) -> int:
    if not ty.startswith("i"):
        raise TypeError(f"expected integer type iN, got {ty!r}")
    w = int(ty[1:])
    if w <= 0:
        raise ValueError(f"invalid integer width: {ty!r}")
    return w


@dataclass(frozen=True)
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

    def _as_wire(self, v: Union["Wire", "Reg", Signal, int], *, width: int | None) -> "Wire":
        if isinstance(v, Reg):
            v = v.q
        if isinstance(v, Wire):
            if v.m is not self.m:
                raise ValueError("cannot combine wires from different modules")
            return v
        if isinstance(v, Signal):
            return Wire(self.m, v)
        if isinstance(v, int):
            if width is None:
                width = self.width
            # Call the base `Module.const` even if `Circuit.const` is overridden
            # to return a `Wire`.
            const_sig = Module.const(self.m, int(v), width=int(width))
            return Wire(self.m, const_sig, signed=(int(v) < 0))
        raise TypeError(f"unsupported operand type: {type(v).__name__}")

    def _promote2(self, other: Union["Wire", "Reg", Signal, int]) -> tuple["Wire", "Wire"]:
        """Promote operands to a common width (extend smaller operand)."""
        a = self._as_wire(self, width=None)
        if isinstance(other, int):
            b = self._as_wire(int(other), width=a.width)
        else:
            b = self._as_wire(other, width=None)
        out_w = max(a.width, b.width)
        if a.width != out_w:
            a = a.sext(width=out_w) if a.signed else a.zext(width=out_w)
        if b.width != out_w:
            b = b.sext(width=out_w) if b.signed else b.zext(width=out_w)
        return a, b

    def __add__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.add(a.sig, b.sig), signed=(a.signed or b.signed))

    def __radd__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        return self.__add__(other)

    def __sub__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.sub(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rsub__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        b = self._as_wire(self, width=None)
        a = self._as_wire(other, width=b.width)
        aa, bb = a._promote2(b) if isinstance(a, Wire) else (a, b)
        return Wire(self.m, self.m.sub(aa.sig, bb.sig), signed=(aa.signed or bb.signed))

    def __mul__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.mul(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rmul__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        return self.__mul__(other)

    def __rfloordiv__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        num = self._as_wire(other, width=None)
        return num.__floordiv__(self)

    def __floordiv__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.sdiv(a.sig, b.sig), signed=True)
        return Wire(self.m, self.m.udiv(a.sig, b.sig), signed=False)

    def __rtruediv__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        return self.__rfloordiv__(other)

    def __truediv__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        # Treat `/` as integer division for hardware values.
        return self.__floordiv__(other)

    def __rmod__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        num = self._as_wire(other, width=None)
        return num.__mod__(self)

    def __mod__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.srem(a.sig, b.sig), signed=True)
        return Wire(self.m, self.m.urem(a.sig, b.sig), signed=False)

    def __and__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.and_(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rand__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        return self.__and__(other)

    def __or__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.or_(a.sig, b.sig), signed=(a.signed or b.signed))

    def __ror__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        return self.__or__(other)

    def __xor__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.xor(a.sig, b.sig), signed=(a.signed or b.signed))

    def __rxor__(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        return self.__xor__(other)

    def __invert__(self) -> "Wire":
        return Wire(self.m, self.m.not_(self.sig), signed=self.signed)

    def __lshift__(self, other: int) -> "Wire":
        if not isinstance(other, int):
            raise TypeError("<< only supports constant integer shift amounts")
        return self.shl(amount=int(other))

    def lshr(self, *, amount: int) -> "Wire":
        """Logical shift right by a constant amount (zero-fill)."""
        amt = int(amount)
        if amt < 0:
            raise ValueError("lshr amount must be >= 0")
        return Wire(self.m, self.m.lshri(self.sig, amount=amt), signed=False)

    def ashr(self, *, amount: int) -> "Wire":
        """Arithmetic shift right by a constant amount (sign-fill)."""
        amt = int(amount)
        if amt < 0:
            raise ValueError("ashr amount must be >= 0")
        return Wire(self.m, self.m.ashri(self.sig, amount=amt), signed=True)

    def __rshift__(self, other: int) -> "Wire":
        if not isinstance(other, int):
            raise TypeError(">> only supports constant integer shift amounts")
        if self.signed:
            return self.ashr(amount=int(other))
        return self.lshr(amount=int(other))

    def eq(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        a, b = self._promote2(other)
        return Wire(self.m, self.m.eq(a.sig, b.sig))

    def ult(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Unsigned less-than compare (result is i1)."""
        a, b = self._promote2(other)
        return Wire(self.m, self.m.ult(a.sig, b.sig))

    def slt(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Signed less-than compare (result is i1)."""
        a, b = self._promote2(other)
        return Wire(self.m, self.m.slt(a.sig, b.sig))

    def lt(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Less-than compare respecting signed intent (result is i1)."""
        a, b = self._promote2(other)
        if a.signed or b.signed:
            return Wire(self.m, self.m.slt(a.sig, b.sig))
        return Wire(self.m, self.m.ult(a.sig, b.sig))

    def gt(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Greater-than compare respecting signed intent (result is i1)."""
        other_w = self._as_wire(other, width=None)
        return other_w.lt(self)

    def le(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Less-than-or-equal compare respecting signed intent (result is i1)."""
        return ~self.gt(other)

    def ge(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Greater-than-or-equal compare respecting signed intent (result is i1)."""
        return ~self.lt(other)

    def ugt(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Unsigned greater-than compare (result is i1)."""
        other_w = self._as_wire(other, width=None)
        return other_w.ult(self)

    def ule(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Unsigned less-than-or-equal compare (result is i1)."""
        return ~self.ugt(other)

    def uge(self, other: Union["Wire", "Reg", Signal, int]) -> "Wire":
        """Unsigned greater-than-or-equal compare (result is i1)."""
        return ~self.ult(other)

    def select(self, a: Union["Wire", "Reg", Signal, int], b: Union["Wire", "Reg", Signal, int]) -> "Wire":
        if self.ty != "i1":
            raise TypeError("select() requires a 1-bit selector wire (i1)")

        # At least one operand must provide width.
        if isinstance(a, int) and isinstance(b, int):
            raise TypeError("select() requires at least one Wire/Reg/Signal operand (cannot infer width from two ints)")

        aw: Wire | None = None
        bw: Wire | None = None
        if not isinstance(a, int):
            aw = self._as_wire(a, width=None)
        if not isinstance(b, int):
            bw = self._as_wire(b, width=None)

        if aw is None and bw is None:
            raise TypeError("select() requires at least one Wire/Reg/Signal operand (cannot infer width)")

        out_w = max(aw.width if aw is not None else 0, bw.width if bw is not None else 0)
        if aw is None:
            aw = self._as_wire(int(a), width=out_w)
        if bw is None:
            bw = self._as_wire(int(b), width=out_w)

        if aw.width != out_w:
            aw = aw.sext(width=out_w) if aw.signed else aw.zext(width=out_w)
        if bw.width != out_w:
            bw = bw.sext(width=out_w) if bw.signed else bw.zext(width=out_w)
        return Wire(self.m, self.m.mux(self.sig, aw.sig, bw.sig), signed=(aw.signed or bw.signed))

    def trunc(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.trunc(self.sig, width=width), signed=self.signed)

    def zext(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.zext(self.sig, width=width), signed=False)

    def sext(self, *, width: int) -> "Wire":
        return Wire(self.m, self.m.sext(self.sig, width=width), signed=True)

    def slice(self, *, lsb: int, width: int) -> "Wire":
        return Wire(self.m, self.m.extract(self.sig, lsb=lsb, width=width), signed=False)

    def shl(self, *, amount: int) -> "Wire":
        return Wire(self.m, self.m.shli(self.sig, amount=amount), signed=self.signed)

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
                raise ValueError("wire slice out of range")
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


@dataclass(frozen=True)
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

    def eq(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.eq(other)

    def ult(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.ult(other)

    def ugt(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.ugt(other)

    def ule(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.ule(other)

    def uge(self, other: Union[Wire, Signal, int]) -> Wire:
        return self.q.uge(other)

    def select(self, a: Union[Wire, "Reg", Signal, int], b: Union[Wire, "Reg", Signal, int]) -> Wire:
        return self.q.select(a, b)

    def trunc(self, *, width: int) -> Wire:
        return self.q.trunc(width=width)

    def zext(self, *, width: int) -> Wire:
        return self.q.zext(width=width)

    def sext(self, *, width: int) -> Wire:
        return self.q.sext(width=width)

    def slice(self, *, lsb: int, width: int) -> Wire:
        return self.q.slice(lsb=lsb, width=width)

    def shl(self, *, amount: int) -> Wire:
        return self.q.shl(amount=amount)

    def __getitem__(self, idx: int | slice) -> Wire:
        return self.q[idx]

    def set(self, value: Union[Wire, "Reg", Signal, int], *, when: Union[Wire, Signal, int] = 1) -> None:
        """Drive `self.next` (backedge) for a stateful variable.

        - `r.set(v)` is equivalent to `m.assign(r.next, v)`
        - `r.set(v, when=cond)` drives `cond ? v : r` (hold otherwise)
        """
        m = self.q.m
        if not isinstance(m, Circuit):
            raise TypeError("Reg.set requires the Reg to belong to a Circuit")

        def as_wire(v: Union[Wire, Reg, Signal, int], *, width: int) -> Wire:
            if isinstance(v, Reg):
                return v.q
            if isinstance(v, Wire):
                if v.m is not m:
                    raise ValueError("cannot combine wires from different modules")
                return v
            if isinstance(v, Signal):
                return Wire(m, v)
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
        m.assign(self.next, cond.select(next_w, self))

    def __ilshift__(self, other: Union[Wire, "Reg", Signal, int]) -> "Reg":
        self.set(other)
        return self


class Circuit(Module):
    """High-level wrapper over `Module` that returns `Wire`/`Reg` objects."""

    def __init__(self, name: str, design_ctx: Any | None = None) -> None:
        super().__init__(name)
        self._scope_stack: list[str] = []
        # Optional multi-module DesignContext (used by `Circuit.instance`).
        self._design_ctx = design_ctx

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

    def input(self, name: str, *, width: int, signed: bool = False) -> Wire:  # type: ignore[override]
        """Declare a module input port and return it as a `Wire`."""
        return Wire(self, super().input(name, width=width), signed=bool(signed))

    def const(self, value: int, *, width: int) -> Wire:  # type: ignore[override]
        """Create an integer constant `Wire` (two's complement at `width`)."""
        return Wire(self, super().const(int(value), width=width), signed=(int(value) < 0))

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

    def assign(self, dst: Union[Wire, Reg, Signal, "CycleAwareSignal"], src: Union[Wire, Reg, Signal, "CycleAwareSignal", int]) -> None:  # type: ignore[override]
        def as_sig(v: Union[Wire, Reg, Signal, "CycleAwareSignal"]) -> Signal:
            if hasattr(v, '_placeholder') and hasattr(v, 'domain'):
                # CycleAwareSignal — extract raw Signal
                return v.sig
            if isinstance(v, Reg):
                return v.q.sig
            if isinstance(v, Wire):
                return v.sig
            return v

        dst_sig = as_sig(dst)
        if isinstance(src, int):
            src_sig = super().const(int(src), width=_int_width(dst_sig.ty))
            super().assign(dst_sig, src_sig)
            return

        src_sig = as_sig(src)
        if dst_sig.ty == src_sig.ty:
            super().assign(dst_sig, src_sig)
            return

        # Implicit integer resizing for convenience (zext smaller, trunc larger).
        if dst_sig.ty.startswith("i") and src_sig.ty.startswith("i"):
            dst_w = _int_width(dst_sig.ty)
            src_w = _int_width(src_sig.ty)
            if src_w < dst_w:
                src_sig = super().zext(src_sig, width=dst_w)
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
        init: Union[Wire, Reg, Signal, int] = 0,
        en: Union[Wire, Signal, int] = 1,
        stage: str | None = None,
        signed: bool | None = None,  # reserved for future type inference / lowering
    ) -> Reg:
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

        full = str(name)
        if stage:
            full = f"{stage}__{full}"
        full = self.scoped_name(full)

        next_w = Wire(self, super().new_wire(width=width, name=f"{full}__next"), assignable=True)
        if isinstance(en, int):
            en_w: Union[Wire, Signal] = self.const(int(en), width=1)
        else:
            en_w = en

        if isinstance(init, int):
            init_w: Union[Wire, Signal] = self.const(int(init), width=width)
        else:
            init_w = init

        r = self.reg_wire(clk, rst, en_w, next_w, init_w)
        # Name the observable value of the state variable.
        q_named = Wire(self, self.alias(r.q.sig, name=full), signed=r.q.signed)
        return Reg(q=q_named, clk=r.clk, rst=r.rst, en=r.en, next=r.next, init=r.init)

    def reg_wire(
        self, clk: Signal, rst: Signal, en: Union[Wire, Signal], next_: Union[Wire, Signal], init: Union[Wire, Signal, int]
    ) -> Reg:
        en_w = en if isinstance(en, Wire) else Wire(self, en)
        next_w = next_ if isinstance(next_, Wire) else Wire(self, next_)
        if isinstance(init, int):
            init_w = self.const(init, width=next_w.width)
        else:
            init_w = init if isinstance(init, Wire) else Wire(self, init)

        q_sig = self.reg(clk, rst, en_w.sig, next_w.sig, init_w.sig)
        q_w = Wire(self, q_sig, signed=(next_w.signed or init_w.signed))
        return Reg(q=q_w, clk=clk, rst=rst, en=en_w, next=next_w, init=init_w)

    def reg_domain(self, domain: ClockDomain, en: Union[Wire, Signal], next_: Union[Wire, Signal], init: Union[Wire, Signal, int]) -> Reg:
        return self.reg_wire(domain.clk, domain.rst, en, next_, init)

    def backedge_reg(
        self,
        clk: Signal,
        rst: Signal,
        *,
        width: int,
        init: Union[Wire, Signal, int],
        en: Union[Wire, Signal, int] = 1,
    ) -> Reg:
        """Create a register whose `next` is a placeholder `pyc.wire` meant to be driven via `pyc.assign`.

        This pattern enables feedback loops (state machines) in a netlist-like style:

        - `r = m.backedge_reg(...)` creates `r.next` as a `pyc.wire`
        - Later: `m.assign(r.next, some_next_value)`
        """
        next_w = self.new_wire(width=width)
        if isinstance(en, int):
            en_w: Union[Wire, Signal] = self.const(en, width=1)
        else:
            en_w = en
        return self.reg_wire(clk, rst, en_w, next_w, init)

    def vec(self, *elems: Union["Wire", "Reg"]) -> "Vec":
        return Vec(elems)

    def cat(self, *elems: Union["Wire", "Reg"]) -> Wire:
        """Concatenate wires/regs into a packed bus (MSB-first)."""
        return self.vec(*elems).pack()

    def bundle(self, **fields: Union["Wire", "Reg"]) -> "Bundle":
        return Bundle(fields)

    def instance(
        self,
        fn: Any,
        *,
        name: str,
        params: dict[str, Any] | None = None,
        module_name: str | None = None,
        **ports: Union["Wire", "Reg", Signal, int],
    ) -> "Bundle":
        """Instantiate a specialized sub-module and return its outputs as a Bundle.

        The callee is a Python module function `fn(m: Circuit, ...)` compiled into
        the current multi-module Design via `jit.compile_design(...)`.

        - `name` is the instance name (used in codegen).
        - `params` are compile-time specialization parameters for the callee.
        - Remaining kwargs are port connections by callee port name.
        """

        if self._design_ctx is None:
            raise TypeError("Circuit.instance requires a design context (compile via pycircuit.jit.compile_design)")

        from .design import DesignContext, DesignError

        if not isinstance(self._design_ctx, DesignContext):
            raise TypeError("internal error: Circuit design context has an unexpected type")

        cm = self._design_ctx.specialize(fn, params=dict(params or {}), module_name=module_name)

        expected = set(cm.arg_names)
        provided = set(ports.keys())
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        if missing or extra:
            parts: list[str] = []
            if missing:
                parts.append("missing: " + ", ".join(missing))
            if extra:
                parts.append("extra: " + ", ".join(extra))
            raise DesignError(f"instance port mismatch for {cm.sym_name!r} ({'; '.join(parts)})")

        def coerce_to_sig(v: Union[Wire, Reg, Signal, int], *, expected_ty: str, port: str) -> Signal:
            if isinstance(v, Reg):
                v = v.q
            if isinstance(v, Wire):
                if v.m is not self:
                    raise DesignError(f"instance port {port!r}: cannot connect a wire from a different module")
                sig = v.sig
            elif isinstance(v, Signal):
                sig = v
            elif isinstance(v, int):
                if expected_ty == "!pyc.clock" or expected_ty == "!pyc.reset":
                    raise DesignError(f"instance port {port!r}: clock/reset ports cannot be driven by integers")
                if not expected_ty.startswith("i"):
                    raise DesignError(f"instance port {port!r}: expected {expected_ty}, got int")
                width = int(expected_ty[1:])
                sig = self.const(int(v), width=width).sig
            else:
                raise DesignError(f"instance port {port!r}: unsupported value type {type(v).__name__}")

            if sig.ty == expected_ty:
                return sig

            # Convenience: allow implicit integer resizing (zext/trunc) like `Circuit.assign`.
            if sig.ty.startswith("i") and expected_ty.startswith("i"):
                got_w = _int_width(sig.ty)
                exp_w = _int_width(expected_ty)
                w = Wire(self, sig)
                if got_w < exp_w:
                    return (self.sext(w.sig, width=exp_w) if w.signed else self.zext(w.sig, width=exp_w))
                if got_w > exp_w:
                    return self.trunc(w.sig, width=exp_w)
                return sig

            raise DesignError(f"instance port {port!r}: type mismatch, got {sig.ty} expected {expected_ty}")

        # Build operands in callee signature order.
        operands: list[Signal] = []
        for pname, pty in zip(cm.arg_names, cm.arg_types):
            operands.append(coerce_to_sig(ports[pname], expected_ty=pty, port=pname))

        outs = self.instance_op(cm.sym_name, *operands, result_types=list(cm.result_types), name=str(name))
        out_fields: dict[str, Union[Wire, Reg]] = {}
        for oname, sig in zip(cm.result_names, outs):
            out_fields[oname] = Wire(self, sig)
        return Bundle(out_fields)

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
        name: str | None = None,
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
        name: str | None = None,
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
        name: str | None = None,
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
        return Wire(self, in_ready), Wire(self, out_valid), Wire(self, out_data)

    def cdc_sync(self, clk: Signal, rst: Signal, a: Union[Wire, Reg, Signal], *, stages: int | None = None) -> Wire:
        sig = a.q.sig if isinstance(a, Reg) else (a.sig if isinstance(a, Wire) else a)
        out = super().cdc_sync(clk, rst, sig, stages=stages)
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

    def queue(
        self,
        name: str,
        *,
        clk: Signal | None = None,
        rst: Signal | None = None,
        domain: ClockDomain | None = None,
        width: int,
        depth: int,
    ) -> "Queue":
        if domain is not None:
            clk = domain.clk
            rst = domain.rst
        if clk is None or rst is None:
            raise TypeError("queue() requires either domain=... or both clk=... and rst=...")
        return Queue(self, name, clk=clk, rst=rst, width=width, depth=depth)


@dataclass(frozen=True)
class Vec:
    """A small fixed-length container of wires/regs for building pipelines."""

    elems: tuple[Union[Wire, Reg], ...]

    def __post_init__(self) -> None:
        if not self.elems:
            raise ValueError("Vec cannot be empty")

        m0 = self._module_of(self.elems[0])
        for e in self.elems[1:]:
            if self._module_of(e) is not m0:
                raise ValueError("Vec elements must belong to the same Circuit/Module")

    @staticmethod
    def _module_of(e: Union[Wire, Reg]) -> Module:
        if isinstance(e, Wire):
            return e.m
        return e.q.m

    @property
    def m(self) -> Module:
        return self._module_of(self.elems[0])

    def __len__(self) -> int:
        return len(self.elems)

    def __iter__(self) -> Iterator[Union[Wire, Reg]]:
        return iter(self.elems)

    @overload
    def __getitem__(self, idx: int) -> Union[Wire, Reg]: ...

    @overload
    def __getitem__(self, idx: slice) -> "Vec": ...

    def __getitem__(self, idx: int | slice) -> Union[Wire, Reg, "Vec"]:
        if isinstance(idx, slice):
            return Vec(self.elems[idx])
        return self.elems[int(idx)]

    def wires(self) -> tuple[Wire, ...]:
        out: list[Wire] = []
        for e in self.elems:
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

        # Fallback: build packing from basic shifts + ors (legacy Module backends).
        if not isinstance(m, Circuit):
            raise TypeError("Vec.pack requires a Circuit/Module with a concat() builder")
        acc = m.const(0, width=out_w)
        lsb = 0
        for w in reversed(ws):
            part = w.zext(width=out_w)
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
        return Vec(tuple(reversed(parts_rev)))

    def regs_domain(self, domain: ClockDomain, en: Union[Wire, Signal, int], init: Union[Wire, Signal, int] = 0) -> "Vec":
        """Create a register per element and return a Vec of Regs."""
        ws = self.wires()
        m = ws[0].m
        if not isinstance(m, Circuit):
            raise TypeError("regs_domain requires elements to belong to a Circuit")
        regs: list[Reg] = []
        for w in ws:
            regs.append(m.reg_domain(domain, en, w, init))
        return Vec(tuple(regs))


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


class Queue:
    """Queue-like wrapper over `pyc.fifo` (single-clock, strict ready/valid).

    Intended usage (event-ish):
      q = m.queue("q", domain=dom, width=8, depth=2)
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
            raise ValueError("Queue width must be > 0")
        if self.depth <= 0:
            raise ValueError("Queue depth must be > 0")

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
        self._push_valid_expr: Union[Wire, Reg, Signal, int] = 0
        self._push_data_expr: Union[Wire, Reg, Signal, int] = 0
        self._pop_ready_expr: Union[Wire, Reg, Signal, int] = 0

        # Defer assigns so we can keep single-driver semantics while supporting a push/pop API.
        m.add_finalizer(self._finalize)

    def push(self, data: Union[Wire, Reg, Signal, int], *, when: Union[Wire, Signal, int] = 1) -> Wire:
        if self._push_bound:
            raise ValueError("Queue.push() may only be called once per Queue instance (prototype limitation)")
        self._push_bound = True
        self._push_valid_expr = when
        self._push_data_expr = data
        # Fire when valid && ready.
        w_when = self._coerce_i1(when, ctx="queue push when")
        return w_when & self.in_ready

    def pop(self, *, when: Union[Wire, Signal, int] = 1) -> Pop:
        if self._pop_bound:
            raise ValueError("Queue.pop() may only be called once per Queue instance (prototype limitation)")
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

    def _coerce_i1(self, v: Union[Wire, Signal, int], *, ctx: str) -> Wire:
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
        raise TypeError(f"{ctx}: expected Wire/Signal/int, got {type(v).__name__}")


def cat(*elems: Union[Wire, Reg]) -> Wire:
    """Concatenate wires/regs into a packed bus (MSB-first).

    Convenience wrapper so you can write:
      `bus = cat(a, b, c)`

    Equivalent to:
      `bus = m.cat(a, b, c)` (when all values belong to the same Circuit).
    """
    return Vec(tuple(elems)).pack()


# =============================================================================
# Cycle-Aware Signal System (New Architecture)
# =============================================================================


class CycleAwareDomain:
    """时钟域管理器，实现周期状态追踪。
    
    核心功能：
    - 追踪当前时钟周期
    - 提供 next()/prev()/push()/pop() 周期管理API
    - 创建带周期信息的信号
    """

    def __init__(self, name: str, m: "CycleAwareCircuit") -> None:
        self.name = name
        self.m = m
        self.clk: Signal | None = None
        self.rst: Signal | None = None
        self._current_cycle: int = 0
        self._cycle_stack: list[int] = []

    def _ensure_clk_rst(self) -> None:
        """确保clk和rst已初始化。当域名为 'clk' 时使用端口名 'clk'/'rst'，以兼容现有 C++ testbench。"""
        if self.clk is None:
            clk_name = "clk" if self.name == "clk" else f"{self.name}_clk"
            self.clk = self.m.clock(clk_name)
        if self.rst is None:
            rst_name = "rst" if self.name == "clk" else f"{self.name}_rst"
            self.rst = self.m.reset(rst_name)

    @property
    def current_cycle(self) -> int:
        """获取当前周期。"""
        return self._current_cycle

    def next(self) -> None:
        """推进到下一个时钟周期。"""
        self._current_cycle += 1

    def prev(self) -> None:
        """回退到上一个时钟周期。"""
        self._current_cycle -= 1

    def push(self) -> None:
        """保存当前周期状态到栈。"""
        self._cycle_stack.append(self._current_cycle)

    def pop(self) -> None:
        """从栈恢复周期状态。"""
        if not self._cycle_stack:
            raise RuntimeError("pop() without matching push()")
        self._current_cycle = self._cycle_stack.pop()

    # -----------------------------------------------------------------
    # 信号创建 API
    # -----------------------------------------------------------------

    def signal(
        self,
        name: str,
        *,
        width: int,
        reset: int | None = None,
    ) -> "CycleAwareSignal":
        """统一信号定义 — 唯一的信号创建 API。

        Args:
            name: 信号名称
            width: 位宽
            reset: 复位值。不为 None → flop（DFF），None → wire

        Returns:
            CycleAwareSignal，cycle = domain.current_cycle

        用法::

            # wire（组合逻辑 / 反馈占位）
            x = domain.signal("x", width=8)

            # flop（DFF，Q 输出在声明 cycle）
            count = domain.signal("count", width=8, reset=0)
        """
        self._ensure_clk_rst()
        placeholder = self.m.named_wire(name, width=width)
        sig = CycleAwareSignal(
            m=self.m,
            sig=placeholder.sig,
            cycle=self._current_cycle,
            domain=self,
            name=name,
            signed=False,
            _placeholder=placeholder,
            _reset=reset,
        )
        self.m.add_finalizer(sig._finalize)
        return sig

    def input(self, name: str, *, width: int) -> "CycleAwareSignal":
        """创建输入端口信号。

        输入由外部环境驱动，不支持 .set()。
        """
        self._ensure_clk_rst()
        port = self.m.input(name, width=width)
        return CycleAwareSignal(
            m=self.m,
            sig=port,
            cycle=self._current_cycle,
            domain=self,
            name=name,
            signed=False,
        )

    def const(self, value: int, *, width: int) -> "CycleAwareSignal":
        """创建常量信号，cycle = domain.current_cycle。"""
        return self.create_const(value, width=width)

    # -----------------------------------------------------------------
    # 向后兼容（内部使用）
    # -----------------------------------------------------------------

    def create_signal(self, name: str, *, width: int, init_value: int = 0) -> "CycleAwareSignal":
        """创建输入信号（向后兼容，推荐使用 domain.input()）。"""
        return self.input(name, width=width)

    def create_const(self, value: int, *, width: int, name: str = "") -> "CycleAwareSignal":
        """创建常量信号，周期为当前周期。"""
        self._ensure_clk_rst()
        sig = self.m.const(value, width=width)
        return CycleAwareSignal(
            m=self.m,
            sig=sig,
            cycle=self._current_cycle,
            domain=self,
            name=name or f"const_{value}",
            signed=(value < 0),
        )

    def cycle(
        self,
        sig: "CycleAwareSignal",
        *,
        reset_value: int = 0,
        name: str = "",
    ) -> "CycleAwareSignal":
        """创建D触发器（单周期延迟）。
        
        输出周期 = 输入周期 + 1
        """
        self._ensure_clk_rst()
        assert self.clk is not None and self.rst is not None
        
        en = self.m.const(1, width=1)
        init = self.m.const(reset_value, width=_int_width(sig.sig.ty))
        q_sig = self.m.reg(self.clk, self.rst, en, sig.sig, init)
        
        out_name = name or f"{sig.name}__dff"
        return CycleAwareSignal(
            m=self.m,
            sig=q_sig,
            cycle=sig.cycle + 1,
            domain=self,
            name=out_name,
            signed=sig.signed,
        )

    def create_reset(self) -> Signal:
        """创建复位信号。"""
        self._ensure_clk_rst()
        assert self.rst is not None
        return self.rst


@dataclass
class CycleAwareSignal:
    """周期感知信号 — PyCircuit 的统一硬件信号类型。

    每个信号携带：
    - sig: 底层MLIR信号（对于 domain.signal() 创建的信号，指向内部占位导线）
    - cycle: 当前周期
    - domain: 所属时钟域
    - name: 调试名称
    - signed: 符号性

    通过 domain.signal() 创建的信号还具备：
    - _placeholder: 内部占位导线（Wire），用于延迟赋值
    - _reset: 复位值（不为 None 时为 flop，否则为 wire）
    - _updates: .set() 调用累积的 (value, condition) 列表
    - _finalized: 是否已完成最终化

    类型推导：
    - reset is not None → flop（DFF），_finalize 时创建寄存器
    - reset is None 且有 .set() → wire（组合逻辑），_finalize 时直接连线
    - reset is None 且无 .set() 且无 _placeholder → 输入端口（无需 finalize）
    """
    m: "CycleAwareCircuit"
    sig: Signal
    cycle: int
    domain: CycleAwareDomain
    name: str = ""
    signed: bool = False
    _placeholder: Any = field(default=None, repr=False, compare=False)
    _reset: int | None = field(default=None, repr=False, compare=False)
    _updates: list = field(default_factory=list, repr=False, compare=False)
    _finalized: bool = field(default=False, repr=False, compare=False)

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
        return f"{self.name}@cycle{self.cycle}" if self.name else f"{self.sig.ref}@cycle{self.cycle}"

    def __repr__(self) -> str:
        return f"CycleAwareSignal({self.name!r}, cycle={self.cycle}, width={self.width})"

    def __bool__(self) -> bool:
        raise TypeError(
            "CycleAwareSignal cannot be used as a Python boolean. "
            "Use `if` inside a JIT-compiled design function, or compare explicitly."
        )

    def _as_signal(self, v: Union["CycleAwareSignal", int], *, width: int | None = None) -> "CycleAwareSignal":
        """将值转换为CycleAwareSignal。"""
        if isinstance(v, CycleAwareSignal):
            if v.m is not self.m:
                raise ValueError("cannot combine signals from different modules")
            return v
        if isinstance(v, int):
            w = width if width is not None else self.width
            return self.domain.create_const(int(v), width=w)
        raise TypeError(f"unsupported operand type: {type(v).__name__}")

    def _balanced_binop(
        self,
        other: Union["CycleAwareSignal", int],
        op_fn: Any,
        op_name: str,
    ) -> "CycleAwareSignal":
        """执行二元运算，自动周期平衡。"""
        other_sig = self._as_signal(other)
        
        # 周期平衡
        a, b = self.m._balance_cycles(self, other_sig)
        
        # 位宽对齐
        out_w = max(a.width, b.width)
        a_sig = a.sig
        b_sig = b.sig
        if a.width < out_w:
            a_sig = self.m.zext(a.sig, width=out_w) if not a.signed else self.m.sext(a.sig, width=out_w)
        if b.width < out_w:
            b_sig = self.m.zext(b.sig, width=out_w) if not b.signed else self.m.sext(b.sig, width=out_w)
        
        result_sig = op_fn(a_sig, b_sig)
        out_cycle = self.domain.current_cycle
        
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=out_cycle,
            domain=self.domain,
            name=f"({self.name} {op_name} {other_sig.name})" if self.name and other_sig.name else "",
            signed=(a.signed or b.signed),
        )

    def __add__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_binop(other, self.m.add, "+")

    def __radd__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self.__add__(other)

    def __sub__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_binop(other, self.m.sub, "-")

    def __rsub__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        other_sig = self._as_signal(other)
        return other_sig.__sub__(self)

    def __mul__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_binop(other, self.m.mul, "*")

    def __rmul__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self.__mul__(other)

    def _balanced_div(self, other: Union["CycleAwareSignal", int], is_mod: bool = False) -> "CycleAwareSignal":
        """Division / modulo with cycle balancing."""
        other_sig = self._as_signal(other)
        a, b = self.m._balance_cycles(self, other_sig)
        out_w = max(a.width, b.width)
        a_sig, b_sig = a.sig, b.sig
        if a.width < out_w:
            a_sig = self.m.zext(a_sig, out_w)
        if b.width < out_w:
            b_sig = self.m.zext(b_sig, out_w)
        if a.signed or b.signed:
            op = self.m.smod if is_mod else self.m.sdiv
            result_sig = op(a_sig, b_sig)
            signed = True
        else:
            op = self.m.umod if is_mod else self.m.udiv
            result_sig = op(a_sig, b_sig)
            signed = False
        name = "%" if is_mod else "//"
        return CycleAwareSignal(
            m=self.m, sig=result_sig, cycle=self.domain.current_cycle,
            domain=self.domain, name=name, signed=signed,
        )

    def __floordiv__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_div(other, is_mod=False)

    def __truediv__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self.__floordiv__(other)

    def __rfloordiv__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._as_signal(other).__floordiv__(self)

    def __rtruediv__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self.__rfloordiv__(other)

    def __mod__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_div(other, is_mod=True)

    def __rmod__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._as_signal(other).__mod__(self)

    def __and__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_binop(other, self.m.and_, "&")

    def __rand__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self.__and__(other)

    def __or__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_binop(other, self.m.or_, "|")

    def __ror__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self.__or__(other)

    def __xor__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self._balanced_binop(other, self.m.xor, "^")

    def __rxor__(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        return self.__xor__(other)

    def __invert__(self) -> "CycleAwareSignal":
        result_sig = self.m.not_(self.sig)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=f"~{self.name}" if self.name else "",
            signed=self.signed,
        )

    def __lshift__(self, amount: int) -> "CycleAwareSignal":
        if not isinstance(amount, int):
            raise TypeError("<< only supports constant integer shift amounts")
        result_sig = self.m.shli(self.sig, amount=int(amount))
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=f"({self.name} << {amount})" if self.name else "",
            signed=self.signed,
        )

    def __rshift__(self, amount: int) -> "CycleAwareSignal":
        if not isinstance(amount, int):
            raise TypeError(">> only supports constant integer shift amounts")
        if self.signed:
            result_sig = self.m.ashri(self.sig, amount=int(amount))
        else:
            result_sig = self.m.lshri(self.sig, amount=int(amount))
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=f"({self.name} >> {amount})" if self.name else "",
            signed=self.signed,
        )

    def eq(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        """相等比较（返回i1信号）。"""
        other_sig = self._as_signal(other)
        a, b = self.m._balance_cycles(self, other_sig)
        # 位宽对齐
        out_w = max(a.width, b.width)
        a_sig = a.sig if a.width == out_w else self.m.zext(a.sig, width=out_w)
        b_sig = b.sig if b.width == out_w else self.m.zext(b.sig, width=out_w)
        result_sig = self.m.eq(a_sig, b_sig)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.domain.current_cycle,
            domain=self.domain,
            name=f"({self.name} == {other_sig.name})" if self.name else "",
            signed=False,
        )

    def ne(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        """不等比较（返回i1信号）。"""
        eq_result = self.eq(other)
        return ~eq_result

    def lt(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        """小于比较（返回i1信号）。"""
        other_sig = self._as_signal(other)
        a, b = self.m._balance_cycles(self, other_sig)
        out_w = max(a.width, b.width)
        a_sig = a.sig if a.width == out_w else self.m.zext(a.sig, width=out_w)
        b_sig = b.sig if b.width == out_w else self.m.zext(b.sig, width=out_w)
        if a.signed or b.signed:
            result_sig = self.m.slt(a_sig, b_sig)
        else:
            result_sig = self.m.ult(a_sig, b_sig)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.domain.current_cycle,
            domain=self.domain,
            name=f"({self.name} < {other_sig.name})" if self.name else "",
            signed=False,
        )

    def gt(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        """大于比较。"""
        other_sig = self._as_signal(other)
        return other_sig.lt(self)

    def le(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        """小于等于比较。"""
        return ~self.gt(other)

    def ge(self, other: Union["CycleAwareSignal", int]) -> "CycleAwareSignal":
        """大于等于比较。"""
        return ~self.lt(other)

    def select(
        self,
        true_val: Union["CycleAwareSignal", int],
        false_val: Union["CycleAwareSignal", int],
    ) -> "CycleAwareSignal":
        """条件选择（self为条件，必须是i1）。"""
        if self.ty != "i1":
            raise TypeError("select() requires a 1-bit selector signal (i1)")
        
        true_sig = self._as_signal(true_val)
        false_sig = self._as_signal(false_val)
        
        # 周期平衡所有三个信号
        cond, t, f = self.m._balance_cycles(self, true_sig, false_sig)
        
        # 位宽对齐
        out_w = max(t.width, f.width)
        t_sig = t.sig if t.width == out_w else self.m.zext(t.sig, width=out_w)
        f_sig = f.sig if f.width == out_w else self.m.zext(f.sig, width=out_w)
        
        result_sig = self.m.mux(cond.sig, t_sig, f_sig)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.domain.current_cycle,
            domain=self.domain,
            name="",
            signed=(t.signed or f.signed),
        )

    def trunc(self, *, width: int) -> "CycleAwareSignal":
        """截断到指定位宽。"""
        result_sig = self.m.trunc(self.sig, width=width)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=f"{self.name}[{width-1}:0]" if self.name else "",
            signed=self.signed,
        )

    def zext(self, *, width: int) -> "CycleAwareSignal":
        """零扩展到指定位宽。"""
        result_sig = self.m.zext(self.sig, width=width)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=self.name,
            signed=False,
        )

    def sext(self, *, width: int) -> "CycleAwareSignal":
        """符号扩展到指定位宽。"""
        result_sig = self.m.sext(self.sig, width=width)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=self.name,
            signed=True,
        )

    def shl(self, *, amount: int) -> "CycleAwareSignal":
        """左移指定位数。"""
        result_sig = self.m.shli(self.sig, amount=amount)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=self.name,
            signed=self.signed,
        )

    def slice(self, *, lsb: int, width: int) -> "CycleAwareSignal":
        """提取位片段。"""
        result_sig = self.m.extract(self.sig, lsb=lsb, width=width)
        return CycleAwareSignal(
            m=self.m,
            sig=result_sig,
            cycle=self.cycle,
            domain=self.domain,
            name=f"{self.name}[{lsb+width-1}:{lsb}]" if self.name else "",
            signed=False,
        )

    def __getitem__(self, idx: int | slice) -> "CycleAwareSignal":
        if isinstance(idx, slice):
            if idx.step is not None:
                raise TypeError("signal slicing does not support step")
            lsb = 0 if idx.start is None else int(idx.start)
            stop = self.width if idx.stop is None else int(idx.stop)
            if lsb < 0 or stop < 0:
                raise ValueError("signal slice indices must be >= 0")
            if stop < lsb:
                raise ValueError("signal slice stop must be >= start")
            width = stop - lsb
            if width <= 0:
                raise ValueError("signal slice width must be > 0")
            return self.slice(lsb=lsb, width=width)
        
        bit = int(idx)
        if bit < 0:
            raise ValueError("signal bit index must be >= 0")
        if bit >= self.width:
            raise ValueError("signal bit index out of range")
        return self.slice(lsb=bit, width=1)

    def named(self, name: str) -> "CycleAwareSignal":
        """附加调试名称。"""
        scoped = self.m.scoped_name(name) if hasattr(self.m, "scoped_name") else name
        aliased = self.m.alias(self.sig, name=scoped)
        return CycleAwareSignal(
            m=self.m,
            sig=aliased,
            cycle=self.cycle,
            domain=self.domain,
            name=name,
            signed=self.signed,
        )

    def as_signed(self) -> "CycleAwareSignal":
        """标记为有符号。"""
        return CycleAwareSignal(
            m=self.m,
            sig=self.sig,
            cycle=self.cycle,
            domain=self.domain,
            name=self.name,
            signed=True,
        )

    def as_unsigned(self) -> "CycleAwareSignal":
        """标记为无符号。"""
        return CycleAwareSignal(
            m=self.m,
            sig=self.sig,
            cycle=self.cycle,
            domain=self.domain,
            name=self.name,
            signed=False,
        )

    # -----------------------------------------------------------------
    # 统一赋值 API
    # -----------------------------------------------------------------

    def set(
        self,
        value: "CycleAwareSignal | int",
        *,
        when: "CycleAwareSignal | int" = 1,
    ) -> None:
        """条件赋值（等价于 ``if when: signal = value``）。

        对 wire 信号：驱动组合逻辑值。
        对 flop 信号：提供 DFF 的 D 端输入。
        多次调用遵循 last-write-wins 优先级。
        """
        if self._placeholder is None:
            raise TypeError(
                f"Signal '{self.name}' was not created via domain.signal() "
                "and cannot be .set(). Use domain.signal() to create a signal "
                "that supports .set()."
            )
        self._updates.append((value, when))

    def _finalize(self) -> None:
        """最终化：根据 _reset 和 _updates 构建硬件。

        - Flop（_reset is not None）：构建 mux 链 → DFF → 连接 Q 到占位导线
        - Wire（_reset is None）：构建 mux 链 → 直接连接到占位导线
        """
        if self._finalized or self._placeholder is None:
            return
        self._finalized = True

        def to_sig(v: "CycleAwareSignal | int", w: int) -> Signal:
            if isinstance(v, CycleAwareSignal):
                return v.sig
            return self.m.const(int(v), width=w)

        if self._reset is not None:
            # ── Flop 模式 ──
            # 默认保持当前值（占位导线 = Q 输出）
            current = self._placeholder  # Wire, duck-types as Signal
            next_val: Any = current

            for val, cond in self._updates:
                val_sig = to_sig(val, self.width)
                cond_sig = to_sig(cond, 1)
                next_val = self.m.mux(cond_sig, val_sig, next_val)

            # 创建 DFF
            self.domain._ensure_clk_rst()
            assert self.domain.clk is not None and self.domain.rst is not None
            en = self.m.const(1, width=1)
            init_sig = self.m.const(self._reset, width=self.width)
            q = self.m.reg(self.domain.clk, self.domain.rst, en, next_val, init_sig)

            # 将 Q 连接到占位导线
            self.m.assign(self._placeholder, q)
        else:
            # ── Wire 模式 ──
            if not self._updates:
                # 无 .set() 调用 — 可能是仅声明未驱动的 wire，跳过
                return

            if len(self._updates) == 1:
                val, cond = self._updates[0]
                val_sig = to_sig(val, self.width)
                if isinstance(cond, int) and cond == 1:
                    # 无条件赋值
                    self.m.assign(self._placeholder, val_sig)
                    return
                # 单条件赋值，默认 0
                cond_sig = to_sig(cond, 1)
                default = self.m.const(0, width=self.width)
                result = self.m.mux(cond_sig, val_sig, default)
                self.m.assign(self._placeholder, result)
                return

            # 多条件赋值：构建优先级 mux 链
            default = self.m.const(0, width=self.width)
            next_val = default
            for val, cond in self._updates:
                val_sig = to_sig(val, self.width)
                cond_sig = to_sig(cond, 1)
                if isinstance(cond, int) and cond == 1:
                    next_val = val_sig
                else:
                    next_val = self.m.mux(cond_sig, val_sig, next_val)
            self.m.assign(self._placeholder, next_val)


class CycleAwareCircuit(Circuit):
    """支持周期感知的电路模块。
    
    核心功能：
    - 管理多个时钟域
    - 自动周期平衡
    - 自动DFF插入
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._domains: dict[str, CycleAwareDomain] = {}
        self._default_domain: CycleAwareDomain | None = None
        self._dff_counter: int = 0

    def create_domain(self, name: str) -> CycleAwareDomain:
        """创建或获取时钟域。"""
        if name not in self._domains:
            dom = CycleAwareDomain(name, self)
            self._domains[name] = dom
            if self._default_domain is None:
                self._default_domain = dom
        return self._domains[name]

    def get_default_domain(self) -> CycleAwareDomain:
        """获取默认时钟域。"""
        if self._default_domain is None:
            self._default_domain = self.create_domain("default")
        return self._default_domain

    def output(self, name: str, value: Any) -> None:
        """声明模块输出端口，直接接受 CycleAwareSignal。"""
        if isinstance(value, CycleAwareSignal):
            super().output(name, value.sig)
        elif isinstance(value, Wire):
            super().output(name, value.sig)
        else:
            super().output(name, value)

    def _balance_cycles(self, *signals: CycleAwareSignal) -> tuple[CycleAwareSignal, ...]:
        """自动周期平衡：将所有信号对齐到 domain.current_cycle。
        
        - signal.cycle < target → 插入 DFF 链延迟 (forward balancing)
        - signal.cycle >= target → 直接使用 (feedback, no DFF)
        """
        if not signals:
            return ()
        
        target_cycle = signals[0].domain.current_cycle
        result: list[CycleAwareSignal] = []
        
        for s in signals:
            if s.cycle < target_cycle:
                delay = target_cycle - s.cycle
                delayed = self._insert_dff_chain(s, delay)
                result.append(delayed)
            else:
                # cycle >= target: feedback or same cycle — use directly
                result.append(s)
        
        return tuple(result)

    def _insert_dff_chain(self, sig: CycleAwareSignal, delay: int) -> CycleAwareSignal:
        """插入N级DFF实现延迟。"""
        if delay <= 0:
            return sig
        
        sig.domain._ensure_clk_rst()
        assert sig.domain.clk is not None and sig.domain.rst is not None
        
        current = sig
        for i in range(delay):
            self._dff_counter += 1
            en = self.const(1, width=1)
            init = self.const(0, width=current.width)
            q_sig = self.reg(sig.domain.clk, sig.domain.rst, en, current.sig, init)
            
            current = CycleAwareSignal(
                m=self,
                sig=q_sig,
                cycle=current.cycle + 1,
                domain=sig.domain,
                name=f"{sig.name}__dff{self._dff_counter}" if sig.name else f"__dff{self._dff_counter}",
                signed=sig.signed,
            )
        
        return current

    def const_signal(self, value: int, *, width: int, domain: CycleAwareDomain | None = None) -> CycleAwareSignal:
        """创建常量信号。"""
        dom = domain or self.get_default_domain()
        return dom.create_const(value, width=width)

    def input_signal(self, name: str, *, width: int, domain: CycleAwareDomain | None = None) -> CycleAwareSignal:
        """创建输入信号。"""
        dom = domain or self.get_default_domain()
        return dom.create_signal(name, width=width)

    def cat_signals(self, *signals: CycleAwareSignal) -> CycleAwareSignal:
        """拼接多个CycleAwareSignal为一个信号 (MSB-first)。
        
        自动进行周期平衡，输出周期为 domain.current_cycle。
        """
        if not signals:
            raise ValueError("cat_signals requires at least one signal")
        
        if len(signals) == 1:
            return signals[0]
        
        # 周期平衡
        balanced = self._balance_cycles(*signals)
        domain = balanced[0].domain
        
        # 使用底层concat拼接
        underlying_sigs = [s.sig for s in balanced]
        result_sig = self.concat(*underlying_sigs)
        
        total_width = sum(s.width for s in balanced)
        return CycleAwareSignal(
            m=self,
            sig=result_sig,
            cycle=domain.current_cycle,
            domain=domain,
            name="",
            signed=False,
        )

    def ca_queue(
        self,
        name: str,
        *,
        domain: CycleAwareDomain | None = None,
        width: int,
        depth: int,
    ) -> "CycleAwareQueue":
        """创建周期感知队列。
        
        Args:
            name: 队列名称
            domain: 时钟域（默认使用默认时钟域）
            width: 数据位宽
            depth: 队列深度
        """
        dom = domain or self.get_default_domain()
        return CycleAwareQueue(self, name, domain=dom, width=width, depth=depth)

    def ca_byte_mem(
        self,
        name: str,
        *,
        domain: CycleAwareDomain | None = None,
        depth: int,
        data_width: int = 64,
    ) -> "CycleAwareByteMem":
        """创建周期感知字节内存。
        
        Args:
            name: 内存名称
            domain: 时钟域（默认使用默认时钟域）
            depth: 内存深度（字节数）
            data_width: 数据位宽（默认64位）
        """
        dom = domain or self.get_default_domain()
        return CycleAwareByteMem(self, name, domain=dom, depth=depth, data_width=data_width)

    def ca_bundle(self, **fields: CycleAwareSignal) -> "CycleAwareBundle":
        """创建周期感知结构体。
        
        Args:
            **fields: 命名字段
        """
        return CycleAwareBundle(fields)

    def ca_const(self, value: int, *, width: int, domain: CycleAwareDomain | None = None) -> CycleAwareSignal:
        """创建常量信号（向后兼容，推荐使用 domain.const()）。"""
        dom = domain or self.get_default_domain()
        return dom.create_const(value, width=width)


def ca_cat(*signals: CycleAwareSignal) -> CycleAwareSignal:
    """拼接CycleAwareSignal的便捷函数 (MSB-first)。
    
    用法: bus = ca_cat(a, b, c)  # 等同于 {a, b, c} in Verilog
    """
    if not signals:
        raise ValueError("ca_cat requires at least one signal")
    m = signals[0].m
    return m.cat_signals(*signals)


# =============================================================================
# Syntax Sugar: signal factory and mux function
# =============================================================================


class _PendingSignal:
    """待完成的信号，等待 | "description"。"""

    def __init__(
        self,
        domain: CycleAwareDomain,
        width: int,
        value: int | str,
        signed: bool = False,
    ) -> None:
        self.domain = domain
        self.width = width
        self.value = value
        self.signed = signed

    def __or__(self, description: str) -> CycleAwareSignal:
        """signal[7:0](value=0) | "description" 语法。"""
        if isinstance(self.value, int):
            # 常量信号
            sig = self.domain.create_const(self.value, width=self.width, name=description)
        else:
            # 输入信号（value是名称）
            sig = self.domain.create_signal(str(self.value), width=self.width)
        
        return CycleAwareSignal(
            m=sig.m,
            sig=sig.sig,
            cycle=sig.cycle,
            domain=sig.domain,
            name=description,
            signed=self.signed,
        )


class _SignalFactoryInstance:
    """信号工厂实例，支持 signal[7:0](value=0) 语法。"""

    def __init__(self, domain: CycleAwareDomain) -> None:
        self.domain = domain
        self._high: int | None = None
        self._low: int | None = None
        self._width_expr: str | None = None

    def __getitem__(self, key: slice | str) -> "_SignalFactoryInstance":
        """signal[7:0] 或 signal["width-1:0"] 语法。"""
        factory = _SignalFactoryInstance(self.domain)
        if isinstance(key, slice):
            factory._high = key.start
            factory._low = key.stop
        elif isinstance(key, str):
            factory._width_expr = key
        return factory

    def _compute_width(self) -> int:
        """计算位宽。"""
        if self._width_expr is not None:
            # 简单解析 "N-1:0" 格式
            parts = self._width_expr.split(":")
            if len(parts) == 2:
                high_expr = parts[0].strip()
                low_expr = parts[1].strip()
                # 尝试解析简单表达式如 "7" 或 "width-1"
                try:
                    low = int(low_expr)
                    if "-" in high_expr:
                        base, offset = high_expr.rsplit("-", 1)
                        return int(base.strip()) - int(offset.strip()) - low + 1
                    else:
                        return int(high_expr) - low + 1
                except ValueError:
                    raise ValueError(f"Cannot parse width expression: {self._width_expr}")
            raise ValueError(f"Invalid width expression: {self._width_expr}")
        
        if self._high is not None and self._low is not None:
            return self._high - self._low + 1
        
        raise ValueError("Signal width not specified")

    def __call__(self, value: int | str = 0) -> _PendingSignal:
        """signal[7:0](value=0) 语法。"""
        width = self._compute_width()
        return _PendingSignal(self.domain, width, value)


class SignalFactory:
    """全局信号工厂，需要绑定到时钟域使用。
    
    用法:
        signal = SignalFactory(domain)
        counter = signal[7:0](value=0) | "Counter"
    """

    def __init__(self, domain: CycleAwareDomain) -> None:
        self.domain = domain

    def __getitem__(self, key: slice | str) -> _SignalFactoryInstance:
        """signal[7:0] 语法。"""
        factory = _SignalFactoryInstance(self.domain)
        return factory[key]

    def __call__(self, value: int | str = 0) -> _PendingSignal:
        """signal(value=...) 单位信号。"""
        return _PendingSignal(self.domain, 1, value)


def mux(
    cond: CycleAwareSignal,
    true_val: CycleAwareSignal | int,
    false_val: CycleAwareSignal | int,
) -> CycleAwareSignal:
    """多路选择器，自动周期平衡。
    
    cond为True时选择true_val，否则选择false_val。
    """
    if cond.ty != "i1":
        raise TypeError("mux condition must be i1")
    return cond.select(true_val, false_val)


# =============================================================================
# Module Context Manager
# =============================================================================


class _ModuleContext:
    """模块上下文，用于记录输入输出。"""

    def __init__(self, inputs: list[CycleAwareSignal], description: str = "") -> None:
        self.inputs = inputs
        self.description = description
        self.outputs: list[CycleAwareSignal] = []


class CycleAwareModule:
    """电路模块基类，支持 with self.module(...) as mod 语法。
    
    使用方式:
        class MyModule(CycleAwareModule):
            def build(self, input_data):
                with self.module(inputs=[input_data], description="My module") as mod:
                    # 模块逻辑
                    result = ...
                    mod.outputs = [result]
                return result
    """

    def __init__(self, name: str, clock_domain: CycleAwareDomain) -> None:
        self.name = name
        self.clock_domain = clock_domain
        self._signal_factory: SignalFactory | None = None

    @property
    def signal(self) -> SignalFactory:
        """获取信号工厂。"""
        if self._signal_factory is None:
            self._signal_factory = SignalFactory(self.clock_domain)
        return self._signal_factory

    @contextmanager
    def module(
        self,
        inputs: list[CycleAwareSignal] | None = None,
        description: str = "",
    ) -> Iterator[_ModuleContext]:
        """模块上下文管理器。
        
        进入时保存时钟域周期状态，退出时恢复。
        """
        self.clock_domain.push()
        
        ctx = _ModuleContext(inputs=inputs or [], description=description)
        try:
            yield ctx
        finally:
            self.clock_domain.pop()

    def build(self, *args: Any, **kwargs: Any) -> Any:
        """构建模块逻辑（子类需重写）。"""
        raise NotImplementedError("Subclasses must implement build()")


# =============================================================================
# Cycle-Aware Advanced Primitives
# =============================================================================


@dataclass(frozen=True)
class CycleAwarePop:
    """周期感知队列弹出结果。"""
    valid: CycleAwareSignal
    data: CycleAwareSignal
    fire: CycleAwareSignal


class CycleAwareQueue:
    """周期感知队列，封装 pyc.fifo。
    
    用法:
        q = m.queue("q", domain=dom, width=8, depth=2)
        accepted = q.push(data, when=in_valid)
        p = q.pop(when=out_ready)
        # p.valid / p.data / p.fire
    """

    def __init__(
        self,
        m: CycleAwareCircuit,
        name: str,
        *,
        domain: CycleAwareDomain,
        width: int,
        depth: int,
    ) -> None:
        self.m = m
        self.name = str(name)
        self.width = int(width)
        self.depth = int(depth)
        self.domain = domain

        if self.width <= 0:
            raise ValueError("Queue width must be > 0")
        if self.depth <= 0:
            raise ValueError("Queue depth must be > 0")

        domain._ensure_clk_rst()
        assert domain.clk is not None and domain.rst is not None

        # Input placeholders
        self._in_valid = m.named_wire(f"{self.name}__in_valid", width=1)
        self._in_data = m.named_wire(f"{self.name}__in_data", width=self.width)
        self._out_ready = m.named_wire(f"{self.name}__out_ready", width=1)

        # Underlying FIFO instance
        in_ready, out_valid, out_data = m.fifo(
            domain.clk,
            domain.rst,
            in_valid=self._in_valid,
            in_data=self._in_data,
            out_ready=self._out_ready,
            depth=self.depth,
        )
        
        # Wrap as CycleAwareSignal
        self.in_ready = CycleAwareSignal(
            m=m, sig=in_ready, cycle=domain.current_cycle,
            domain=domain, name=f"{name}_in_ready",
        )
        self.out_valid = CycleAwareSignal(
            m=m, sig=out_valid, cycle=domain.current_cycle,
            domain=domain, name=f"{name}_out_valid",
        )
        self.out_data = CycleAwareSignal(
            m=m, sig=out_data, cycle=domain.current_cycle,
            domain=domain, name=f"{name}_out_data",
        )

        self._push_bound = False
        self._pop_bound = False
        self._push_valid_expr: CycleAwareSignal | int = 0
        self._push_data_expr: CycleAwareSignal | int = 0
        self._pop_ready_expr: CycleAwareSignal | int = 0

        m.add_finalizer(self._finalize)

    def push(
        self,
        data: CycleAwareSignal | int,
        *,
        when: CycleAwareSignal | int = 1,
    ) -> CycleAwareSignal:
        """入队操作，返回 fire 信号（valid && ready）。"""
        if self._push_bound:
            raise ValueError("Queue.push() may only be called once per Queue instance")
        self._push_bound = True
        self._push_valid_expr = when
        self._push_data_expr = data
        
        when_sig = self._coerce_i1(when, ctx="queue push when")
        fire = when_sig & self.in_ready
        return fire.named(f"{self.name}_push_fire")

    def pop(self, *, when: CycleAwareSignal | int = 1) -> CycleAwarePop:
        """出队操作，返回 Pop 结构。"""
        if self._pop_bound:
            raise ValueError("Queue.pop() may only be called once per Queue instance")
        self._pop_bound = True
        self._pop_ready_expr = when
        
        when_sig = self._coerce_i1(when, ctx="queue pop when")
        fire = self.out_valid & when_sig
        return CycleAwarePop(
            valid=self.out_valid,
            data=self.out_data,
            fire=fire.named(f"{self.name}_pop_fire"),
        )

    def _finalize(self) -> None:
        """最终化：将绑定的表达式赋值给 FIFO 输入。"""
        def to_sig(v: CycleAwareSignal | int) -> Signal:
            if isinstance(v, CycleAwareSignal):
                return v.sig
            return self.m.const(int(v), width=1 if isinstance(v, int) and v in (0, 1) else _int_width(self._in_data.ty))
        
        if isinstance(self._push_valid_expr, int):
            valid_sig = self.m.const(self._push_valid_expr, width=1)
        else:
            valid_sig = self._push_valid_expr.sig
            
        if isinstance(self._push_data_expr, int):
            data_sig = self.m.const(self._push_data_expr, width=self.width)
        else:
            data_sig = self._push_data_expr.sig
            
        if isinstance(self._pop_ready_expr, int):
            ready_sig = self.m.const(self._pop_ready_expr, width=1)
        else:
            ready_sig = self._pop_ready_expr.sig
        
        self.m.assign(self._in_valid, valid_sig)
        self.m.assign(self._in_data, data_sig)
        self.m.assign(self._out_ready, ready_sig)

    def _coerce_i1(self, v: CycleAwareSignal | int, *, ctx: str) -> CycleAwareSignal:
        """将值转换为 i1 信号。"""
        if isinstance(v, CycleAwareSignal):
            if v.ty != "i1":
                raise TypeError(f"{ctx}: expected i1, got {v.ty}")
            return v
        if isinstance(v, int):
            return self.domain.create_const(int(v) & 1, width=1)
        raise TypeError(f"{ctx}: expected CycleAwareSignal or int")


class CycleAwareByteMem:
    """周期感知字节内存。
    
    用法:
        mem = m.byte_mem("mem", domain=dom, depth=4096)
        rdata = mem.read(raddr)
        mem.write(waddr, wdata, wstrb, when=wvalid)
    """

    def __init__(
        self,
        m: CycleAwareCircuit,
        name: str,
        *,
        domain: CycleAwareDomain,
        depth: int,
        data_width: int = 64,
    ) -> None:
        self.m = m
        self.name = str(name)
        self.domain = domain
        self.depth = int(depth)
        self.data_width = int(data_width)
        self.strb_width = self.data_width // 8

        domain._ensure_clk_rst()
        assert domain.clk is not None and domain.rst is not None
        self.clk = domain.clk
        self.rst = domain.rst
        
        # Address width based on depth
        self.addr_width = max(1, (depth - 1).bit_length())

        # Placeholders for read/write ports
        self._raddr: CycleAwareSignal | None = None
        self._wvalid: CycleAwareSignal | int = 0
        self._waddr: CycleAwareSignal | int = 0
        self._wdata: CycleAwareSignal | int = 0
        self._wstrb: CycleAwareSignal | int = 0
        self._rdata: CycleAwareSignal | None = None
        self._finalized = False

        m.add_finalizer(self._finalize)

    def read(self, raddr: CycleAwareSignal) -> CycleAwareSignal:
        """读取内存，返回读数据信号。"""
        self._raddr = raddr
        
        # Create placeholder for read data
        rdata_placeholder = self.m.named_wire(f"{self.name}__rdata", width=self.data_width)
        self._rdata = CycleAwareSignal(
            m=self.m,
            sig=rdata_placeholder,
            cycle=self.domain.current_cycle,
            domain=self.domain,
            name=f"{self.name}_rdata",
        )
        return self._rdata

    def write(
        self,
        waddr: CycleAwareSignal | int,
        wdata: CycleAwareSignal | int,
        wstrb: CycleAwareSignal | int,
        *,
        when: CycleAwareSignal | int = 1,
    ) -> None:
        """写入内存。"""
        self._wvalid = when
        self._waddr = waddr
        self._wdata = wdata
        self._wstrb = wstrb

    def _finalize(self) -> None:
        """最终化：创建底层内存实例。"""
        if self._finalized:
            return
        self._finalized = True

        def to_sig(v: CycleAwareSignal | int, width: int) -> Signal:
            if isinstance(v, CycleAwareSignal):
                return v.sig
            return self.m.const(int(v), width=width)

        raddr_val = self._raddr if self._raddr is not None else self.domain.create_const(0, width=self.addr_width)
        raddr_sig = to_sig(raddr_val, self.addr_width)
        wvalid_sig = to_sig(self._wvalid, 1)
        waddr_sig = to_sig(self._waddr, self.addr_width)
        wdata_sig = to_sig(self._wdata, self.data_width)
        wstrb_sig = to_sig(self._wstrb, self.strb_width)

        # Create actual byte_mem using base Circuit method (via dsl.Module)
        # Note: We need to use the low-level dsl.Module.byte_mem which takes positional args
        from .dsl import Module
        rdata_sig = Module.byte_mem(
            self.m,
            self.clk,
            self.rst,
            raddr_sig,
            wvalid_sig,
            waddr_sig,
            wdata_sig,
            wstrb_sig,
            depth=self.depth,
            name=self.name,
        )

        # Connect byte_mem output to the placeholder wire
        if self._rdata is not None:
            self.m.assign(self._rdata.sig, rdata_sig)


@dataclass
class CycleAwareBundle:
    """周期感知结构体，支持命名字段打包/解包。
    
    用法:
        bundle = CycleAwareBundle(tag=tag_sig, data=data_sig)
        packed = bundle.pack()
        unpacked = bundle.unpack(packed)
        field = unpacked["tag"]
    """
    fields: dict[str, CycleAwareSignal]

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("CycleAwareBundle cannot be empty")
        # Ensure all signals belong to the same circuit
        sigs = list(self.fields.values())
        m0 = sigs[0].m
        for s in sigs[1:]:
            if s.m is not m0:
                raise ValueError("CycleAwareBundle fields must belong to the same circuit")

    def __getitem__(self, key: str) -> CycleAwareSignal:
        return self.fields[str(key)]

    def items(self) -> Iterable[tuple[str, CycleAwareSignal]]:
        return self.fields.items()

    @property
    def m(self) -> CycleAwareCircuit:
        return list(self.fields.values())[0].m

    @property
    def domain(self) -> CycleAwareDomain:
        return list(self.fields.values())[0].domain

    def pack(self) -> CycleAwareSignal:
        """将所有字段打包为单个信号 (MSB-first)。"""
        elems = list(self.fields.values())
        return ca_cat(*elems)

    def unpack(self, packed: CycleAwareSignal) -> "CycleAwareBundle":
        """从打包的信号中解包字段。"""
        out: dict[str, CycleAwareSignal] = {}
        offset = 0
        # Unpack in reverse order (LSB first)
        for name, template in reversed(list(self.fields.items())):
            width = template.width
            field = packed[offset:offset + width]
            out[name] = field.named(name)
            offset += width
        # Restore original order
        return CycleAwareBundle({k: out[k] for k in self.fields.keys()})


def ca_bundle(**fields: CycleAwareSignal) -> CycleAwareBundle:
    """创建周期感知结构体的便捷函数。
    
    用法: b = ca_bundle(tag=tag_sig, data=data_sig)
    """
    return CycleAwareBundle(fields)


    # CycleAwareReg 已被 CycleAwareSignal.set()/_finalize() 替代，不再需要。
