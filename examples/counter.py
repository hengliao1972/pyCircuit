from __future__ import annotations

from pycircuit import Circuit


def build(m: Circuit) -> None:
    clk = m.clock("clk")
    rst = m.reset("rst")
    do = m.input("en", width=1)

    count = m.out("count", clk=clk, rst=rst, width=8, init=0, en=1)

    # Stage-like style: read current flop outputs, compute, then set next.
    with m.scope("COUNT"):
        c = count.out()
        count.set(c + 1, when=do)

    m.output("count", count)
