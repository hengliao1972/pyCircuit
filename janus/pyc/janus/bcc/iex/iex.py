from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class IexRoute:
    to_alu: Wire
    to_bru: Wire
    to_lsu: Wire


def route_fu(m: Circuit, *, is_mem: Wire, is_branch: Wire) -> IexRoute:
    is_mem = m.wire(is_mem)
    is_branch = m.wire(is_branch)
    to_lsu = is_mem
    to_bru = (~to_lsu) & is_branch
    to_alu = (~to_lsu) & (~to_bru)
    return IexRoute(to_alu=to_alu, to_bru=to_bru, to_lsu=to_lsu)
