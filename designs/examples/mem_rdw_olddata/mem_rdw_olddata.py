from __future__ import annotations

from pycircuit import Circuit, module, compile_cycle_aware, CycleAwareCircuit, CycleAwareDomain


def build(m: CycleAwareCircuit, domain: CycleAwareDomain, depth: int = 4, data_width: int = 32, addr_width: int = 2) -> None:
    cd = domain.clock_domain
    clk = cd.clk
    rst = cd.rst

    ren = m.input("ren", width=1)
    raddr = m.input("raddr", width=addr_width)

    wvalid = m.input("wvalid", width=1)
    waddr = m.input("waddr", width=addr_width)
    wdata = m.input("wdata", width=data_width)
    wstrb = m.input("wstrb", width=(data_width + 7) // 8)

    rdata = m.sync_mem(
        clk,
        rst,
        ren=ren,
        raddr=raddr,
        wvalid=wvalid,
        waddr=waddr,
        wdata=wdata,
        wstrb=wstrb,
        depth=depth,
        name="mem0",
    )
    m.output("rdata", rdata)


build.__pycircuit_name__ = "mem_rdw_olddata"


if __name__ == "__main__":
    print(compile_cycle_aware(build, name="mem_rdw_olddata", eager=True, depth=4, data_width=32, addr_width=2).emit_mlir())

