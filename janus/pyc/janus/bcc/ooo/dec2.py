from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class Dec2Out:
    op: Wire
    imm: Wire
    srcl: Wire
    srcr: Wire
    srcp: Wire
    has_split: Wire


def dec2_expand(m: Circuit, *, op: Wire, imm: Wire, srcl: Wire, srcr: Wire, srcp: Wire) -> Dec2Out:
    op = m.wire(op)
    imm = m.wire(imm)
    srcl = m.wire(srcl)
    srcr = m.wire(srcr)
    srcp = m.wire(srcp)
    return Dec2Out(op=op, imm=imm, srcl=srcl, srcr=srcr, srcp=srcp, has_split=m.const(0, width=1))
