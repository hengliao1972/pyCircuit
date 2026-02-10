from __future__ import annotations

from pycircuit import Circuit, Wire


def rob_head_can_commit(m: Circuit, *, head_valid: Wire, head_done: Wire, flush: Wire) -> Wire:
    head_valid = m.wire(head_valid)
    head_done = m.wire(head_done)
    flush = m.wire(flush)
    return head_valid & head_done & (~flush)
