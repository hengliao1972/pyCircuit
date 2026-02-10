from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.dsl import Signal

from janus.bcc.ooo.helpers import mux_by_uindex


@dataclass(frozen=True)
class BisqRegs:
    head: Reg
    tail: Reg
    count: Reg
    valid: list[Reg]
    brob_idx: list[Reg]
    pe_sel: list[Reg]
    payload: list[Reg]


@dataclass(frozen=True)
class BisqHead:
    valid: Wire
    brob_idx: Wire
    pe_sel: Wire
    payload: Wire


def make_bisq_regs(m: Circuit, clk: Signal, rst: Signal, *, depth: int = 16, name: str = "bisq") -> BisqRegs:
    if depth <= 0 or (depth & (depth - 1)) != 0:
        raise ValueError("BISQ depth must be a positive power of two")

    c = m.const
    idx_w = (depth - 1).bit_length()
    with m.scope(name):
        head = m.out("head", clk=clk, rst=rst, width=idx_w, init=c(0, width=idx_w), en=1)
        tail = m.out("tail", clk=clk, rst=rst, width=idx_w, init=c(0, width=idx_w), en=1)
        count = m.out("count", clk=clk, rst=rst, width=idx_w + 1, init=c(0, width=idx_w + 1), en=1)

        valid: list[Reg] = []
        brob_idx: list[Reg] = []
        pe_sel: list[Reg] = []
        payload: list[Reg] = []
        for i in range(depth):
            valid.append(m.out(f"v{i}", clk=clk, rst=rst, width=1, init=0, en=1))
            brob_idx.append(m.out(f"bid{i}", clk=clk, rst=rst, width=8, init=0, en=1))
            pe_sel.append(m.out(f"pe{i}", clk=clk, rst=rst, width=2, init=0, en=1))
            payload.append(m.out(f"pl{i}", clk=clk, rst=rst, width=64, init=0, en=1))

    return BisqRegs(head=head, tail=tail, count=count, valid=valid, brob_idx=brob_idx, pe_sel=pe_sel, payload=payload)


def bisq_head(m: Circuit, q: BisqRegs) -> BisqHead:
    return BisqHead(
        valid=mux_by_uindex(m, idx=q.head.out(), items=q.valid, default=m.const(0, width=1)),
        brob_idx=mux_by_uindex(m, idx=q.head.out(), items=q.brob_idx, default=m.const(0, width=8)),
        pe_sel=mux_by_uindex(m, idx=q.head.out(), items=q.pe_sel, default=m.const(0, width=2)),
        payload=mux_by_uindex(m, idx=q.head.out(), items=q.payload, default=m.const(0, width=64)),
    )


def bisq_has_space(m: Circuit, q: BisqRegs, *, depth: int) -> Wire:
    return q.count.out().ult(m.const(depth, width=q.count.width))
