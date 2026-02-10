from __future__ import annotations

from pycircuit import Circuit, Wire


def next_pc(m: Circuit, *, pc: Wire, len_bytes: Wire, redirect_valid: Wire, redirect_pc: Wire) -> Wire:
    pc = m.wire(pc)
    len_bytes = m.wire(len_bytes)
    redirect_valid = m.wire(redirect_valid)
    redirect_pc = m.wire(redirect_pc)
    fallthrough = pc + len_bytes.zext(width=64)
    return redirect_valid.select(redirect_pc, fallthrough)
