from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.dsl import Signal

from janus.bcc.ooo.helpers import mux_by_uindex


@dataclass(frozen=True)
class TileRegFile:
    cells: list[Reg]
    read_data: Wire


def build_tilereg(
    m: Circuit,
    *,
    clk: Signal,
    rst: Signal,
    read_idx: Wire,
    write_valid: Wire,
    write_idx: Wire,
    write_data: Wire,
    num_tiles: int = 64,
    name: str = "tilereg",
) -> TileRegFile:
    if num_tiles <= 0 or (num_tiles & (num_tiles - 1)) != 0:
        raise ValueError("num_tiles must be a positive power of two")

    c = m.const
    idx_w = (num_tiles - 1).bit_length()
    read_idx = m.wire(read_idx)
    write_valid = m.wire(write_valid)
    write_idx = m.wire(write_idx)
    write_data = m.wire(write_data)

    with m.scope(name):
        cells: list[Reg] = []
        for i in range(num_tiles):
            cells.append(m.out(f"t{i}", clk=clk, rst=rst, width=64, init=0, en=1))

    for i in range(num_tiles):
        hit = write_valid & write_idx.eq(c(i, width=write_idx.width))
        cells[i].set(write_data, when=hit)

    ridx = read_idx
    if ridx.width < idx_w:
        ridx = ridx.zext(width=idx_w)
    elif ridx.width > idx_w:
        ridx = ridx.trunc(width=idx_w)
    read_data = mux_by_uindex(m, idx=ridx, items=cells, default=c(0, width=64))

    return TileRegFile(cells=cells, read_data=read_data)
