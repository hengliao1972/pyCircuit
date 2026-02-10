from __future__ import annotations

from pycircuit import Circuit, Wire


def freelist_after_alloc(m: Circuit, *, free_mask: Wire, alloc_mask: Wire, flush: Wire, flush_free_mask: Wire) -> Wire:
    free_mask = m.wire(free_mask)
    alloc_mask = m.wire(alloc_mask)
    flush = m.wire(flush)
    flush_free_mask = m.wire(flush_free_mask)
    next_mask = free_mask & (~alloc_mask)
    return flush.select(flush_free_mask, next_mask)
