from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire, jit_inline


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


def mux_read(m: Circuit, idx: Wire, entries: list[Wire | Reg], *, default: int = 0) -> Wire:
    """Read `entries[idx]` using a mux-chain (small-table helper)."""
    if not entries:
        raise ValueError("entries must be non-empty")
    width = entries[0].width
    v = m.const(int(default), width=width)
    for i, e in enumerate(entries):
        ev = e.out() if isinstance(e, Reg) else e
        v = idx.eq(m.const(i, width=idx.width)).select(ev, v)
    return v


def make_bp_table(
    m: Circuit,
    clk,
    rst,
    *,
    entries: int,
    en: Wire,
) -> tuple[list[Reg], list[Reg], list[Reg], list[Reg]]:
    """Allocate a tiny BTB/BHT table as named regs (JIT-time elaboration)."""
    bp_valid: list[Reg] = []
    bp_tag: list[Reg] = []
    bp_target: list[Reg] = []
    bp_ctr: list[Reg] = []
    for i in range(int(entries)):
        bp_valid.append(m.out(f"valid{i}", clk=clk, rst=rst, width=1, init=0, en=en))
        bp_tag.append(m.out(f"tag{i}", clk=clk, rst=rst, width=64, init=0, en=en))
        bp_target.append(m.out(f"target{i}", clk=clk, rst=rst, width=64, init=0, en=en))
        bp_ctr.append(m.out(f"ctr{i}", clk=clk, rst=rst, width=2, init=0, en=en))
    return bp_valid, bp_tag, bp_target, bp_ctr


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
