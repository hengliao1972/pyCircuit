from __future__ import annotations

from pycircuit import Circuit, module


@module
def child(m: Circuit, a, mask):
    return a & mask


@module
def build(m: Circuit) -> None:
    a = m.input("a", width=8)
    mask = m.input("mask", width=8)
    bias = m.input("bias", width=8)
    child_out = m.instance(child, name="u_child", a=a, mask=mask).read()
    m.output("child_out", child_out)
    m.output("result", child_out ^ bias)


build.__pycircuit_name__ = "comb_dirty_instance"
