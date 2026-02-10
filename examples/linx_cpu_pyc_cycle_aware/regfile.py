from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareSignal,
    mux,
)

from examples.linx_cpu_pyc.isa import REG_INVALID


def make_gpr(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    *,
    boot_sp: CycleAwareSignal,
) -> list[CycleAwareSignal]:
    """24-entry GPR file (r0 forced to 0, r1 initialized to boot_sp)."""
    regs: list[CycleAwareSignal] = []
    for i in range(24):
        regs.append(domain.signal(f"r{i}", width=64, reset=0))
    return regs


def make_regs(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    *,
    count: int,
    width: int,
    init: int = 0,
) -> list[CycleAwareSignal]:
    regs: list[CycleAwareSignal] = []
    for i in range(count):
        regs.append(domain.signal(f"r{i}", width=width, reset=init))
    return regs


def read_reg(
    m: CycleAwareCircuit,
    code: CycleAwareSignal,
    *,
    gpr: list[CycleAwareSignal],
    t: list[CycleAwareSignal],
    u: list[CycleAwareSignal],
    default: CycleAwareSignal,
) -> CycleAwareSignal:
    """Mux-based regfile read with strict defaulting (out-of-range -> default)."""
    v: CycleAwareSignal = default

    for i in range(24):
        vv = default if i == 0 else gpr[i]
        cond = code.eq(i)
        v = mux(cond, vv, v)
    for i in range(4):
        cond = code.eq(24 + i)
        v = mux(cond, t[i], v)
    for i in range(4):
        cond = code.eq(28 + i)
        v = mux(cond, u[i], v)
    return v


def stack_next(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    arr: list[CycleAwareSignal],
    *,
    do_push: CycleAwareSignal,
    do_clear: CycleAwareSignal,
    value: CycleAwareSignal,
) -> list[CycleAwareSignal]:
    """Compute next values for a 4-entry stack."""
    n0 = arr[0]
    n1 = arr[1]
    n2 = arr[2]
    n3 = arr[3]

    zero64 = domain.const(0, width=64)

    # Push: shift down and insert at top
    n0_push = value
    n1_push = n0
    n2_push = n1
    n3_push = n2

    n0 = mux(do_push, n0_push, n0)
    n1 = mux(do_push, n1_push, n1)
    n2 = mux(do_push, n2_push, n2)
    n3 = mux(do_push, n3_push, n3)

    # Clear overrides push
    n0 = mux(do_clear, zero64, n0)
    n1 = mux(do_clear, zero64, n1)
    n2 = mux(do_clear, zero64, n2)
    n3 = mux(do_clear, zero64, n3)

    return [n0, n1, n2, n3]


def commit_gpr(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    gpr: list[CycleAwareSignal],
    *,
    do_reg_write: CycleAwareSignal,
    regdst: CycleAwareSignal,
    value: CycleAwareSignal,
) -> None:
    zero64 = domain.const(0, width=64)
    for i in range(24):
        if i == 0:
            gpr[i].set(zero64)
            continue
        we = do_reg_write & regdst.eq(i)
        gpr[i].set(value, when=we)


def commit_stack(
    m: CycleAwareCircuit,
    arr: list[CycleAwareSignal],
    next_vals: list[CycleAwareSignal],
) -> None:
    for i in range(4):
        arr[i].set(next_vals[i])
