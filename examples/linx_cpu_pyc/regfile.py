from __future__ import annotations

from pycircuit import Circuit, Reg, Vec, Wire, jit_inline
from pycircuit.dsl import Signal

from .isa import REG_INVALID


def make_gpr(m: Circuit, clk: Signal, rst: Signal, *, boot_sp: Wire, en: Wire) -> list[Reg]:
    """24-entry GPR file (r0 forced to 0, r1 initialized to boot_sp)."""
    zero64 = m.const(0, width=64)
    regs: list[Reg] = []
    for i in range(24):
        init = boot_sp if i == 1 else zero64
        regs.append(m.out(f"r{i}", clk=clk, rst=rst, width=64, init=init, en=en))
    return regs


def make_regs(m: Circuit, clk: Signal, rst: Signal, *, count: int, width: int, init: Wire, en: Wire) -> list[Reg]:
    regs: list[Reg] = []
    for i in range(count):
        regs.append(m.out(f"r{i}", clk=clk, rst=rst, width=width, init=init, en=en))
    return regs


def read_reg(m: Circuit, code: Wire, *, gpr: list[Reg], t: list[Reg], u: list[Reg], default: Wire) -> Wire:
    """Mux-based regfile read with strict defaulting (out-of-range -> default)."""
    c = m.const
    v: Wire = default

    for i in range(24):
        vv = default if i == 0 else gpr[i]
        v = code.eq(c(i, width=6)).select(vv, v)
    for i in range(4):
        v = code.eq(c(24 + i, width=6)).select(t[i], v)
    for i in range(4):
        v = code.eq(c(28 + i, width=6)).select(u[i], v)
    return v


@jit_inline
def stack_next(m: Circuit, arr: list[Reg], *, do_push: Wire, do_clear: Wire, value: Wire) -> Vec:
    n0 = arr[0].out()
    n1 = arr[1].out()
    n2 = arr[2].out()
    n3 = arr[3].out()

    if do_push:
        n3 = n2
        n2 = n1
        n1 = n0
        n0 = value

    # Clear overrides push (matches previous priority).
    if do_clear:
        n0 = 0
        n1 = 0
        n2 = 0
        n3 = 0

    return m.vec(n0, n1, n2, n3)


def commit_gpr(
    m: Circuit,
    gpr: list[Reg],
    *,
    do_reg_write0: Wire,
    regdst0: Wire | Reg,
    value0: Wire | Reg,
    do_reg_write1: Wire | None = None,
    regdst1: Wire | Reg | None = None,
    value1: Wire | Reg | None = None,
    macro_do_reg_write: Wire | None = None,
    macro_regdst: Wire | Reg | None = None,
    macro_value: Wire | Reg | None = None,
) -> None:
    c = m.const
    zero64 = m.const(0, width=64)
    if do_reg_write1 is None:
        do_reg_write1 = m.const(0, width=1)
    if regdst1 is None:
        regdst1 = m.const(REG_INVALID, width=6)
    if value1 is None:
        value1 = zero64
    if macro_do_reg_write is None:
        macro_do_reg_write = m.const(0, width=1)
    if macro_regdst is None:
        macro_regdst = m.const(REG_INVALID, width=6)
    if macro_value is None:
        macro_value = zero64
    for i in range(24):
        if i == 0:
            gpr[i].set(zero64)
            continue
        we_pipe0 = do_reg_write0 & regdst0.eq(c(i, width=6))
        we_pipe1 = do_reg_write1 & regdst1.eq(c(i, width=6))
        we_macro = macro_do_reg_write & macro_regdst.eq(c(i, width=6))
        we = we_pipe0 | we_pipe1 | we_macro
        # Priority: macro (highest), then younger pipe lane (1), then older (0).
        wdata_pipe = we_pipe1.select(value1, value0)
        wdata = we_macro.select(macro_value, wdata_pipe)
        gpr[i].set(wdata, when=we)


def commit_stack(m: Circuit, arr: list[Reg], next_vals: list[Wire]) -> None:
    for i in range(4):
        arr[i].set(next_vals[i])
