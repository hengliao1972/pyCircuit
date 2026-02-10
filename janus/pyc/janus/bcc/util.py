from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire, jit_inline


@dataclass(frozen=True)
class Consts:
    one1: Wire
    zero1: Wire
    zero3: Wire
    zero4: Wire
    zero6: Wire
    zero8: Wire
    zero32: Wire
    zero64: Wire
    one64: Wire


def make_consts(m: Circuit) -> Consts:
    c = m.const
    return Consts(
        one1=c(1, width=1),
        zero1=c(0, width=1),
        zero3=c(0, width=3),
        zero4=c(0, width=4),
        zero6=c(0, width=6),
        zero8=c(0, width=8),
        zero32=c(0, width=32),
        zero64=c(0, width=64),
        one64=c(1, width=64),
    )

def masked_eq(x: Wire, *, mask: int, match: int) -> Wire:
    return (x & int(mask)).eq(int(match))


@jit_inline
def shl_var(m: Circuit, value: Wire, shamt: Wire) -> Wire:
    """Variable shift-left by `shamt` (uses low 6 bits)."""
    _ = m
    s = shamt.trunc(width=6)
    out = value
    out = s[0].select(out.shl(amount=1), out)
    out = s[1].select(out.shl(amount=2), out)
    out = s[2].select(out.shl(amount=4), out)
    out = s[3].select(out.shl(amount=8), out)
    out = s[4].select(out.shl(amount=16), out)
    out = s[5].select(out.shl(amount=32), out)
    return out


@jit_inline
def lshr_var(m: Circuit, value: Wire, shamt: Wire) -> Wire:
    """Variable logical shift-right by `shamt` (uses low 6 bits)."""
    _ = m
    s = shamt.trunc(width=6)
    out = value
    out = s[0].select(out.lshr(amount=1), out)
    out = s[1].select(out.lshr(amount=2), out)
    out = s[2].select(out.lshr(amount=4), out)
    out = s[3].select(out.lshr(amount=8), out)
    out = s[4].select(out.lshr(amount=16), out)
    out = s[5].select(out.lshr(amount=32), out)
    return out


@jit_inline
def ashr_var(m: Circuit, value: Wire, shamt: Wire) -> Wire:
    """Variable arithmetic shift-right by `shamt` (uses low 6 bits)."""
    _ = m
    s = shamt.trunc(width=6)
    out = value.as_signed()
    out = s[0].select(out.ashr(amount=1), out)
    out = s[1].select(out.ashr(amount=2), out)
    out = s[2].select(out.ashr(amount=4), out)
    out = s[3].select(out.ashr(amount=8), out)
    out = s[4].select(out.ashr(amount=16), out)
    out = s[5].select(out.ashr(amount=32), out)
    return out
