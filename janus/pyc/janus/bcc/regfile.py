from __future__ import annotations

from pycircuit import Circuit, Reg, Wire
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


def stack_next(m: Circuit, arr: list[Reg], *, do_push: Wire, do_clear: Wire, value: Wire) -> list[Wire]:
    zero64 = m.const(0, width=64)
    n0 = do_clear.select(zero64, do_push.select(value, arr[0]))
    n1 = do_clear.select(zero64, do_push.select(arr[0], arr[1]))
    n2 = do_clear.select(zero64, do_push.select(arr[1], arr[2]))
    n3 = do_clear.select(zero64, do_push.select(arr[2], arr[3]))
    return [n0, n1, n2, n3]


def commit_gpr(m: Circuit, gpr: list[Reg], *, do_reg_write: Wire, regdst: Reg, value: Reg) -> None:
    c = m.const
    zero64 = m.const(0, width=64)
    for i in range(24):
        if i == 0:
            gpr[i].set(zero64)
            continue
        we = do_reg_write & regdst.eq(c(i, width=6))
        gpr[i].set(value, when=we)


def commit_stack(m: Circuit, arr: list[Reg], next_vals: list[Wire]) -> None:
    for i in range(4):
        arr[i].set(next_vals[i])
