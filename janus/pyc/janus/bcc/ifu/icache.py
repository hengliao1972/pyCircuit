from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire
from pycircuit.dsl import Signal


@dataclass(frozen=True)
class ICacheOut:
    rdata: Wire


def build_icache(
    m: Circuit,
    *,
    clk: Signal,
    rst: Signal,
    raddr: Wire,
    mem: Wire | None = None,
    depth_bytes: int = 1 << 20,
) -> ICacheOut:
    raddr = m.wire(raddr)
    # Bring-up model: direct byte-memory front-end (single-cycle read path).
    if mem is not None:
        return ICacheOut(rdata=m.wire(mem))
    rdata = m.byte_mem(
        clk,
        rst,
        raddr=raddr,
        wvalid=m.const(0, width=1),
        waddr=m.const(0, width=64),
        wdata=m.const(0, width=64),
        wstrb=m.const(0, width=8),
        depth=depth_bytes,
        name="icache_mem",
    )
    return ICacheOut(rdata=rdata)
