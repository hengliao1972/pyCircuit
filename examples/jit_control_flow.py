from __future__ import annotations

from pycircuit import Circuit


def build(m: Circuit, N: int = 4) -> object:
    a = m.input("a", width=8)
    b = m.input("b", width=8)

    x = (a + b) >> 1
    if a < b:
        x = x + 1
    else:
        x = x + 2

    acc = x
    for _ in range(N):
        acc = acc + 1

    return acc
