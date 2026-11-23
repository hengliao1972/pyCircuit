from __future__ import annotations

from pycircuit import compile_cycle_aware, CycleAwareCircuit, CycleAwareDomain


def build(m: CycleAwareCircuit, domain: CycleAwareDomain, depth: int = 8, data_width: int = 32, addr_width: int = 3) -> None:
    """3-read x 2-write synchronous memory through the generic NxM DSL entry."""
    clk = domain.clock_domain.clk
    sw = (data_width + 7) // 8

    reads = []
    for i in range(3):
        reads.append((m.input(f"ren{i}", width=1), m.input(f"raddr{i}", width=addr_width)))
    writes = []
    for i in range(2):
        writes.append(
            (
                m.input(f"wvalid{i}", width=1),
                m.input(f"waddr{i}", width=addr_width),
                m.input(f"wdata{i}", width=data_width),
                m.input(f"wstrb{i}", width=sw),
            )
        )

    rdatas = m.sync_mem_nm(clk, reads, writes, depth=depth, name="mem_nm")
    for i, rd in enumerate(rdatas):
        m.output(f"rdata{i}", rd)


build.__pycircuit_name__ = "sync_mem_nm_ports"


if __name__ == "__main__":
    print(
        compile_cycle_aware(
            build, name="sync_mem_nm_ports", eager=True, depth=8, data_width=32, addr_width=3
        ).emit_mlir()
    )
