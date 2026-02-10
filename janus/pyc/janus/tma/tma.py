from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.dsl import Signal


@dataclass(frozen=True)
class TmaOut:
    ready: Wire
    done_valid: Wire
    done_brob: Wire
    tile_write_valid: Wire
    tile_write_idx: Wire
    tile_write_data: Wire


def build_tma(
    m: Circuit,
    *,
    clk: Signal,
    rst: Signal,
    launch_valid: Wire,
    launch_brob: Wire,
    launch_payload: Wire,
    latency: int = 3,
    name: str = "tma",
) -> TmaOut:
    if latency <= 0:
        raise ValueError("latency must be > 0")

    c = m.const
    launch_valid = m.wire(launch_valid)
    launch_brob = m.wire(launch_brob)
    launch_payload = m.wire(launch_payload)

    with m.scope(name):
        busy = m.out("busy", clk=clk, rst=rst, width=1, init=0, en=1)
        cnt = m.out("cnt", clk=clk, rst=rst, width=4, init=0, en=1)
        brob = m.out("brob", clk=clk, rst=rst, width=8, init=0, en=1)
        payload = m.out("payload", clk=clk, rst=rst, width=64, init=0, en=1)

    ready = ~busy.out()
    launch_fire = launch_valid & ready
    running = busy.out()
    finishing = running & cnt.out().eq(c(1, width=4))

    busy_next = busy.out()
    busy_next = launch_fire.select(c(1, width=1), busy_next)
    busy_next = finishing.select(c(0, width=1), busy_next)
    busy.set(busy_next)

    cnt_next = cnt.out()
    cnt_next = launch_fire.select(c(latency, width=4), cnt_next)
    cnt_next = (running & (~finishing)).select(cnt.out() + c(0xF, width=4), cnt_next)
    cnt.set(cnt_next)

    brob.set(launch_brob, when=launch_fire)
    payload.set(launch_payload, when=launch_fire)

    tile_idx = payload.out().trunc(width=6)
    tile_data = payload.out() + c(0x40, width=64)
    return TmaOut(
        ready=ready,
        done_valid=finishing,
        done_brob=brob.out(),
        tile_write_valid=finishing,
        tile_write_idx=tile_idx,
        tile_write_data=tile_data,
    )
