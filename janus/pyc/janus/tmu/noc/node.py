from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire


@dataclass(frozen=True)
class RingNodeOut:
    out_valid: Wire
    out_brob: Wire
    out_payload: Wire
    local_valid: Wire
    local_brob: Wire
    local_payload: Wire


def build_ring_node(
    m: Circuit,
    *,
    in_valid: Wire,
    in_brob: Wire,
    in_payload: Wire,
    inject_valid: Wire,
    inject_brob: Wire,
    inject_payload: Wire,
    node_id: int,
    local_ready: Wire,
) -> RingNodeOut:
    c = m.const
    in_valid = m.wire(in_valid)
    in_brob = m.wire(in_brob)
    in_payload = m.wire(in_payload)
    inject_valid = m.wire(inject_valid)
    inject_brob = m.wire(inject_brob)
    inject_payload = m.wire(inject_payload)
    local_ready = m.wire(local_ready)

    target = in_payload[6:8]
    take_local = in_valid & local_ready & target.eq(c(node_id, width=2))
    forward_valid = in_valid & (~take_local)

    out_valid = forward_valid | (inject_valid & (~forward_valid))
    out_brob = forward_valid.select(in_brob, inject_brob)
    out_payload = forward_valid.select(in_payload, inject_payload)

    return RingNodeOut(
        out_valid=out_valid,
        out_brob=out_brob,
        out_payload=out_payload,
        local_valid=take_local,
        local_brob=in_brob,
        local_payload=in_payload,
    )
