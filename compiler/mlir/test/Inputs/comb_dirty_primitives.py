from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareDomain, wire_of


def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    clk, rst = domain.clock_domain.clk, domain.clock_domain.rst
    valid = m.input("valid", width=1)
    ready = m.input("ready", width=1)
    data = m.input("data", width=8)
    data64 = m.input("data64", width=64)
    addr = m.input("addr", width=2)
    strb = m.input("strb", width=1)
    strb8 = m.input("strb8", width=8)

    in_ready, out_valid, out_data = m.fifo(
        clk, rst, in_valid=valid, in_data=data, out_ready=ready, depth=2
    )
    byte_data = m.byte_mem(
        clk,
        rst,
        raddr=addr,
        wvalid=valid,
        waddr=addr,
        wdata=data64,
        wstrb=strb8,
        depth=16,
        name="byte",
    )
    sync_data = m.sync_mem(
        clk,
        rst,
        ren=valid,
        raddr=addr,
        wvalid=valid,
        waddr=addr,
        wdata=data,
        wstrb=strb,
        depth=4,
        name="sync",
    )
    sync0, sync1 = m.sync_mem_dp(
        clk,
        rst,
        ren0=valid,
        raddr0=addr,
        ren1=valid,
        raddr1=addr,
        wvalid=valid,
        waddr=addr,
        wdata=data,
        wstrb=strb,
        depth=4,
        name="sync_dp",
    )
    async_ready, async_valid, async_data = m.async_fifo(
        clk,
        rst,
        clk,
        rst,
        in_valid=valid,
        in_data=data,
        out_ready=ready,
        depth=2,
    )
    cdc_data = m.cdc_sync(clk, rst, data, stages=1)

    state = domain.signal(width=8, reset_value=0, name="state")
    current = wire_of(state)
    domain.next()
    state <<= data

    for name, value in (
        ("fifo_ready", in_ready),
        ("fifo_valid", out_valid),
        ("fifo_data", out_data),
        ("byte_data", byte_data),
        ("sync_data", sync_data),
        ("sync0", sync0),
        ("sync1", sync1),
        ("async_ready", async_ready),
        ("async_valid", async_valid),
        ("async_data", async_data),
        ("cdc_data", cdc_data),
        ("reg_data", current),
    ):
        m.output(name, value + 1)


build.__pycircuit_name__ = "comb_dirty_primitives"
