from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class F4Out:
    valid: Wire
    pc: Wire
    window: Wire


def run_f4(m: Circuit, *, ibuf_valid: Wire, ibuf_pc: Wire, ibuf_window: Wire, backend_ready: Wire) -> F4Out:
    ibuf_valid = m.wire(ibuf_valid)
    ibuf_pc = m.wire(ibuf_pc)
    ibuf_window = m.wire(ibuf_window)
    backend_ready = m.wire(backend_ready)
    valid = ibuf_valid & backend_ready
    return F4Out(valid=valid, pc=ibuf_pc, window=ibuf_window)
