from __future__ import annotations

from pycircuit import (
    CycleAwareByteMem,
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareSignal,
)


def build_byte_mem(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    *,
    raddr: CycleAwareSignal,
    wvalid: CycleAwareSignal,
    waddr: CycleAwareSignal,
    wdata: CycleAwareSignal,
    wstrb: CycleAwareSignal,
    depth_bytes: int,
    name: str,
) -> CycleAwareSignal:
    mem = m.ca_byte_mem(name, domain=domain, depth=depth_bytes, data_width=64)
    rdata = mem.read(raddr)
    mem.write(waddr, wdata, wstrb, when=wvalid)
    return rdata
