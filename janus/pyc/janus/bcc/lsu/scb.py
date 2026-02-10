from __future__ import annotations

from pycircuit import Circuit, Wire


def scb_merge_word(m: Circuit, *, old_data: Wire, new_data: Wire, wstrb: Wire) -> Wire:
    old_data = m.wire(old_data)
    new_data = m.wire(new_data)
    wstrb = m.wire(wstrb)
    merged = old_data
    for i in range(8):
        mask = m.const(0xFF << (i * 8), width=64)
        merged = wstrb[i].select((merged & m.const(~(0xFF << (i * 8)), width=64)) | (new_data & mask), merged)
    return merged
