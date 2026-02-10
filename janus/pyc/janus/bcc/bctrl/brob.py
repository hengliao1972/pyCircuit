from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.dsl import Signal

from janus.bcc.ooo.helpers import mux_by_uindex


@dataclass(frozen=True)
class BrobRegs:
    head: Reg
    tail: Reg
    count: Reg
    valid: list[Reg]
    done: list[Reg]
    pe_sel: list[Reg]
    tag: list[Reg]
    tile: list[Reg]
    payload: list[Reg]


@dataclass(frozen=True)
class BrobHead:
    valid: Wire
    done: Wire
    pe_sel: Wire
    tag: Wire
    tile: Wire
    payload: Wire
    idx: Wire


def make_brob_regs(m: Circuit, clk: Signal, rst: Signal, *, depth: int = 32, name: str = "brob") -> BrobRegs:
    if depth <= 0 or (depth & (depth - 1)) != 0:
        raise ValueError("BROB depth must be a positive power of two")

    c = m.const
    idx_w = (depth - 1).bit_length()
    with m.scope(name):
        head = m.out("head", clk=clk, rst=rst, width=idx_w, init=c(0, width=idx_w), en=1)
        tail = m.out("tail", clk=clk, rst=rst, width=idx_w, init=c(0, width=idx_w), en=1)
        count = m.out("count", clk=clk, rst=rst, width=idx_w + 1, init=c(0, width=idx_w + 1), en=1)

        valid: list[Reg] = []
        done: list[Reg] = []
        pe_sel: list[Reg] = []
        tag: list[Reg] = []
        tile: list[Reg] = []
        payload: list[Reg] = []
        for i in range(depth):
            valid.append(m.out(f"v{i}", clk=clk, rst=rst, width=1, init=0, en=1))
            done.append(m.out(f"d{i}", clk=clk, rst=rst, width=1, init=0, en=1))
            pe_sel.append(m.out(f"pe{i}", clk=clk, rst=rst, width=2, init=0, en=1))
            tag.append(m.out(f"tag{i}", clk=clk, rst=rst, width=8, init=0, en=1))
            tile.append(m.out(f"tile{i}", clk=clk, rst=rst, width=6, init=0, en=1))
            payload.append(m.out(f"pl{i}", clk=clk, rst=rst, width=64, init=0, en=1))

    return BrobRegs(head=head, tail=tail, count=count, valid=valid, done=done, pe_sel=pe_sel, tag=tag, tile=tile, payload=payload)


def brob_head(m: Circuit, b: BrobRegs) -> BrobHead:
    idx = b.head.out()
    return BrobHead(
        valid=mux_by_uindex(m, idx=idx, items=b.valid, default=m.const(0, width=1)),
        done=mux_by_uindex(m, idx=idx, items=b.done, default=m.const(0, width=1)),
        pe_sel=mux_by_uindex(m, idx=idx, items=b.pe_sel, default=m.const(0, width=2)),
        tag=mux_by_uindex(m, idx=idx, items=b.tag, default=m.const(0, width=8)),
        tile=mux_by_uindex(m, idx=idx, items=b.tile, default=m.const(0, width=6)),
        payload=mux_by_uindex(m, idx=idx, items=b.payload, default=m.const(0, width=64)),
        idx=idx,
    )


def brob_has_space(m: Circuit, b: BrobRegs, *, depth: int) -> Wire:
    return b.count.out().ult(m.const(depth, width=b.count.width))
