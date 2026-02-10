from __future__ import annotations

from pycircuit import Circuit, Wire


def ifu_can_fetch(m: Circuit, *, queue_valid: Wire, backend_ready: Wire, flush: Wire, bubble: Wire) -> Wire:
    queue_valid = m.wire(queue_valid)
    backend_ready = m.wire(backend_ready)
    flush = m.wire(flush)
    bubble = m.wire(bubble)
    can_refill = (~queue_valid) | backend_ready
    return can_refill & (~flush) & (~bubble)
