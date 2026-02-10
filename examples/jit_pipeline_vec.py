from __future__ import annotations

from pycircuit import Circuit


def _pipe_bus(m: Circuit, *, bus, dom, stages: int):
    """Pipeline a packed bus through `stages` flops (python-unrolled)."""
    for i in range(int(stages)):
        with m.scope(f"PIPE{i}"):
            bus_r = m.out(f"bus_s{i}", domain=dom, width=bus.width)
            bus_r.set(bus)
            bus = bus_r.out()
    return bus


def build(m: Circuit, STAGES: int = 3) -> None:
    dom = m.domain("sys")

    a = m.input("a", width=16)
    b = m.input("b", width=16)
    sel = m.input("sel", width=1)

    # Some combinational logic feeding a multi-field pipeline bus.
    sum_ = a + b
    x = a ^ b
    data = x
    if sel:
        data = sum_
    tag = a == b
    lo8 = data[0:8]

    pkt = m.bundle(tag=tag, data=data, lo8=lo8)
    bus = pkt.pack()

    bus = _pipe_bus(m, bus=bus, dom=dom, stages=STAGES)

    out = pkt.unpack(bus)
    m.output("tag", out["tag"])
    m.output("data", out["data"])
    m.output("lo8", out["lo8"])
