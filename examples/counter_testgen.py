from __future__ import annotations

from pycircuit import Circuit
from pycircuit.tb import Tb, sva


def build(m: Circuit) -> None:
    clk = m.clock("clk")
    rst = m.reset("rst")
    en = m.input("en", width=1)

    count = m.out("count_q", clk=clk, rst=rst, width=8, init=0, en=1)
    count.set(count.out() + 1, when=en)
    m.output("count", count)


def tb(t: Tb) -> None:
    t.clock("clk")
    t.reset("rst", cycles_asserted=2, cycles_deasserted=1)
    t.timeout(32)

    # Hold enable high starting at cycle 0 (after reset deassert).
    t.drive("en", True, at=0)

    for cyc in range(5):
        t.expect("count", cyc + 1, at=cyc, msg=f"count mismatch at cycle {cyc}")

    # SVA: once reset is low, count increments by 1 each cycle while enabled.
    t.sva_assert(
        sva.id("en") & (sva.id("count") == (sva.past("count") + 1)),
        clock="clk",
        reset="rst",
        name="count_incr",
        msg="count did not increment by 1",
    )

    t.finish(at=4)

