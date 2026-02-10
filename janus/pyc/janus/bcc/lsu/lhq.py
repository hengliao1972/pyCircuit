from __future__ import annotations

from pycircuit import Circuit, Wire


def lq_conflict(m: Circuit, *, ld_addr: Wire, st_addr: Wire, ld_valid: Wire, st_valid: Wire) -> Wire:
    ld_addr = m.wire(ld_addr)
    st_addr = m.wire(st_addr)
    ld_valid = m.wire(ld_valid)
    st_valid = m.wire(st_valid)
    return ld_valid & st_valid & ld_addr.eq(st_addr)
