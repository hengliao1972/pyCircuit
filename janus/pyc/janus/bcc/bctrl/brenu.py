from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.dsl import Signal

from janus.bcc.ooo.helpers import mux_by_uindex


@dataclass(frozen=True)
class BrenuRegs:
    next_phys: Reg
    tile_map: list[Reg]


def make_brenu_regs(m: Circuit, clk: Signal, rst: Signal, *, logical_tiles: int = 64, name: str = "brenu") -> BrenuRegs:
    if logical_tiles <= 0 or (logical_tiles & (logical_tiles - 1)) != 0:
        raise ValueError("logical_tiles must be a positive power of two")

    c = m.const
    tile_w = (logical_tiles - 1).bit_length()
    with m.scope(name):
        next_phys = m.out("next_phys", clk=clk, rst=rst, width=tile_w, init=c(0, width=tile_w), en=1)
        tile_map: list[Reg] = []
        for i in range(logical_tiles):
            tile_map.append(m.out(f"map{i}", clk=clk, rst=rst, width=tile_w, init=c(i, width=tile_w), en=1))

    return BrenuRegs(next_phys=next_phys, tile_map=tile_map)


def lookup_tile(m: Circuit, r: BrenuRegs, logical_tile: Wire) -> Wire:
    return mux_by_uindex(m, idx=logical_tile, items=r.tile_map, default=m.const(0, width=r.next_phys.width))


def rewrite_payload_tile(m: Circuit, *, payload: Wire, physical_tile: Wire, tile_bits: int = 6) -> Wire:
    if tile_bits == 6:
        mask = 0x3F
    elif tile_bits == 5:
        mask = 0x1F
    elif tile_bits == 4:
        mask = 0x0F
    else:
        raise ValueError("rewrite_payload_tile supports tile_bits in {4,5,6} for bring-up")
    clear = payload & m.const(~mask, width=payload.width)
    return clear | physical_tile.zext(width=payload.width)
