from __future__ import annotations

from pycircuit import Circuit, Wire


def stq_ready_to_commit(m: Circuit, *, st_valid: Wire, st_addr_ready: Wire, st_data_ready: Wire) -> Wire:
    st_valid = m.wire(st_valid)
    st_addr_ready = m.wire(st_addr_ready)
    st_data_ready = m.wire(st_data_ready)
    return st_valid & st_addr_ready & st_data_ready
