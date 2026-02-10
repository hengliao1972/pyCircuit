from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire
from pycircuit.dsl import Signal


@dataclass(frozen=True)
class L1DOut:
    rdata: Wire


def build_l1d(
    m: Circuit,
    *,
    clk: Signal,
    rst: Signal,
    raddr: Wire,
    wvalid: Wire,
    waddr: Wire,
    wdata: Wire,
    wstrb: Wire,
    depth_bytes: int = 1 << 20,
    name: str = "l1d_mem",
) -> L1DOut:
    raddr = m.wire(raddr)
    wvalid = m.wire(wvalid)
    waddr = m.wire(waddr)
    wdata = m.wire(wdata)
    wstrb = m.wire(wstrb)
    rdata = m.byte_mem(clk, rst, raddr=raddr, wvalid=wvalid, waddr=waddr, wdata=wdata, wstrb=wstrb, depth=depth_bytes, name=name)
    return L1DOut(rdata=rdata)
