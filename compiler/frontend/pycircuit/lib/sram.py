from __future__ import annotations

from pycircuit.data import Bits
from pycircuit.dsl import Signal
from pycircuit.hw import Circuit, ClockDomain, Wire


class SRAMError(ValueError):
    pass


def SRAM(
    m: Circuit,
    cd: ClockDomain,
    ren: Wire,
    raddr: Wire,
    wvalid: Wire,
    waddr: Wire,
    wdata: Wire,
    wstrb: Wire,
    *,
    depth: int,
):
    clk_v = cd.clk
    if not isinstance(clk_v, Signal) or clk_v.ty != "!pyc.clock":
        raise SRAMError("SRAM domain clk must be !pyc.clock")

    ren_w = ren
    wvalid_w = wvalid
    raddr_w = raddr
    waddr_w = waddr
    wdata_w = wdata
    wstrb_w = wstrb
    if ren_w.ty != Bits(1) or wvalid_w.ty != Bits(1):
        raise SRAMError("SRAM ren/wvalid must be i1")

    rdata = m.sync_mem(
        clk_v,
        ren=ren_w,
        raddr=raddr_w,
        wvalid=wvalid_w,
        waddr=waddr_w,
        wdata=wdata_w,
        wstrb=wstrb_w,
        depth=int(depth),
        name="mem",
    )

    return m.bundle_connector(
        rdata=rdata,
    )
