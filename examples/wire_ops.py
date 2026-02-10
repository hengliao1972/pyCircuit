from __future__ import annotations

from pycircuit import Circuit


def build(m: Circuit) -> None:
    dom = m.domain("sys")

    a = m.input("a", width=8)
    b = m.input("b", width=8)
    sel = m.input("sel", width=1)

    with m.scope("COMB"):
        y = a ^ b
        if sel:
            y = a & b

    r = m.out("y_reg", domain=dom, width=8)
    with m.scope("REG"):
        r.set(y)

    m.output("y", r)
