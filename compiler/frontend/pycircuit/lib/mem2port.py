from __future__ import annotations

from pycircuit.data import Bits
from pycircuit.dsl import Signal
from pycircuit.hw import Circuit, ClockDomain, Wire


class Mem2PortError(ValueError):
    pass


def Mem2Port(
    m: Circuit,
    cd: ClockDomain,
    ren0: Wire,
    raddr0: Wire,
    ren1: Wire,
    raddr1: Wire,
    wvalid: Wire,
    waddr: Wire,
    wdata: Wire,
    wstrb: Wire,
    *,
    depth: int,
):
    clk_v = cd.clk
    rst_v = cd.rst
    if not isinstance(clk_v, Signal) or clk_v.ty != "!pyc.clock":
        raise Mem2PortError("Mem2Port domain clk must be !pyc.clock")
    if not isinstance(rst_v, Signal) or rst_v.ty != "!pyc.reset":
        raise Mem2PortError("Mem2Port domain rst must be !pyc.reset")

    ren0_w = ren0
    ren1_w = ren1
    wvalid_w = wvalid
    raddr0_w = raddr0
    raddr1_w = raddr1
    waddr_w = waddr
    wdata_w = wdata
    wstrb_w = wstrb
    if ren0_w.ty != Bits(1) or ren1_w.ty != Bits(1) or wvalid_w.ty != Bits(1):
        raise Mem2PortError("Mem2Port ren0/ren1/wvalid must be i1")

    rdata0, rdata1 = m.sync_mem_dp(
        clk_v,
        ren0=ren0_w,
        raddr0=raddr0_w,
        ren1=ren1_w,
        raddr1=raddr1_w,
        wvalid=wvalid_w,
        waddr=waddr_w,
        wdata=wdata_w,
        wstrb=wstrb_w,
        depth=int(depth),
        name="mem",
    )

    return m.bundle_connector(
        rdata0=rdata0,
        rdata1=rdata1,
    )
