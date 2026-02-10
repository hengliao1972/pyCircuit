from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class RenOut:
    alloc_req: Wire
    dst_is_arch: Wire


def ren_decode_dst(m: Circuit, *, dst_areg: Wire, reg_invalid: int = 0x3F) -> RenOut:
    c = m.const
    dst_areg = m.wire(dst_areg)
    dst_is_invalid = dst_areg.eq(c(reg_invalid, width=dst_areg.width))
    dst_is_zero = dst_areg.eq(c(0, width=dst_areg.width))
    dst_is_arch = (~dst_is_invalid) & (~dst_is_zero)
    return RenOut(alloc_req=dst_is_arch, dst_is_arch=dst_is_arch)
