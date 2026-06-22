from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pycircuit import Circuit, CycleAwareCircuit, CycleAwareDomain, Vec, compile_cycle_aware, function, u

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from issq_config import (  # noqa: E402
    _derive_cfg,
    _entry_spec,
    _lane_lt,
    _not1,
    _slot_select,
    _uop_spec,
)


@function
def _snapshot_entries(m: Circuit, entry_state: list, entries: int) -> list[dict[str, Any]]:
    _ = m
    cur: list[dict[str, Any]] = []
    for i in range(int(entries)):
        s = entry_state[i]
        cur.append(
            {
                "valid": s["valid"].read(),
                "src0_valid": s["uop.src0.valid"].read(),
                "src0_ptag": s["uop.src0.ptag"].read(),
                "src0_ready": s["uop.src0.ready"].read(),
                "src1_valid": s["uop.src1.valid"].read(),
                "src1_ptag": s["uop.src1.ptag"].read(),
                "src1_ready": s["uop.src1.ready"].read(),
                "dst_valid": s["uop.dst.valid"].read(),
                "dst_ptag": s["uop.dst.ptag"].read(),
                "dst_ready": s["uop.dst.ready"].read(),
                "payload": s["uop.payload"].read(),
            }
        )
    return cur


@function
def _ready_lookup_vec(m: Circuit, ready_v: Vec, ptag_wire, ptag_w: int, ptag_count: int):
    tags = Vec([m.const(t, width=int(ptag_w)) for t in range(int(ptag_count))])
    return ((tags == ptag_wire) & ready_v).or_reduce()


@function
def _wake_hit_vec(m: Circuit, wake_valid_v: Vec, wake_ptag_v: Vec, ptag_wire):
    _ = m
    return (wake_valid_v & (wake_ptag_v == ptag_wire)).or_reduce()


@function
def _alloc_field_vec(m: Circuit, enq_uops: list, alloc_lane: list[Vec], slot: int, path: str, width: int, enq_ports: int):
    sels = Vec([alloc_lane[k][int(slot)] for k in range(int(enq_ports))])
    vals = Vec([enq_uops[k][path].read() for k in range(int(enq_ports))])
    return sels.onehot_mux(vals, zero=u(int(width), 0))


@function
def _select_oldest_ready_vec(
    m: Circuit,
    *,
    fields: dict[str, Vec],
    age_v: Vec,
    entries: int,
    issue_ports: int,
) -> tuple[Vec, list[Vec], list, Vec, Vec]:
    _ = m
    entry_ready = fields["valid"] & fields["src0.ready"] & fields["src1.ready"]

    issue_sel: list[Vec] = []
    issue_valid = [u(1, 0) for _ in range(int(issue_ports))]
    remaining = entry_ready
    for k in range(int(issue_ports)):
        oldest = []
        for i in range(int(entries)):
            older_exists = (remaining & age_v[:, i]).or_reduce()
            oldest.append(remaining[i] & _not1(m, older_exists))
        oldest_v = Vec(oldest)
        issue_sel.append(oldest_v)
        issue_valid[k] = oldest_v.or_reduce()
        remaining = remaining & ~oldest_v

    issue_win = Vec(issue_sel).or_reduce(dim=0)
    keep_valid = fields["valid"] & ~issue_win
    return entry_ready, issue_sel, issue_valid, issue_win, keep_valid


@function
def _allocate_enqueue_lanes_vec(
    m: Circuit,
    *,
    enq_valid_v: Vec,
    keep_valid: Vec,
    entries: int,
    enq_ports: int,
) -> tuple[list[Vec], list, Vec, Vec]:
    free_avail = ~keep_valid
    alloc_lane: list[Vec] = []
    enq_ready = []

    for k in range(int(enq_ports)):
        any_free = free_avail.or_reduce()
        enq_ready.append(any_free)

        first = []
        lower_seen = u(1, 0)
        for i in range(int(entries)):
            first_i = free_avail[i] & _not1(m, lower_seen)
            first.append(first_i)
            lower_seen = lower_seen | free_avail[i]

        accept_k = enq_valid_v[k] & any_free
        lane_v = Vec([first[i] & accept_k for i in range(int(entries))])
        alloc_lane.append(lane_v)
        free_avail = free_avail & ~lane_v

    new_alloc = Vec(alloc_lane).or_reduce(dim=0)
    next_valid = keep_valid | new_alloc
    return alloc_lane, enq_ready, new_alloc, next_valid


@function
def _emit_issue_ports_vec(
    m: Circuit,
    *,
    uop_spec,
    issue_sel: list[Vec],
    issue_valid: list,
    fields: dict[str, Vec],
    ptag_width: int,
    payload_width: int,
    issue_ports: int,
) -> list[dict[str, Any]]:
    issue_uops: list[dict[str, Any]] = []
    for k in range(int(issue_ports)):
        sel = issue_sel[k]
        vals = {
            "src0.valid": sel.onehot_mux(fields["src0.valid"], zero=u(1, 0)),
            "src0.ptag": sel.onehot_mux(fields["src0.ptag"], zero=u(int(ptag_width), 0)),
            "src0.ready": sel.onehot_mux(fields["src0.ready"], zero=u(1, 0)),
            "src1.valid": sel.onehot_mux(fields["src1.valid"], zero=u(1, 0)),
            "src1.ptag": sel.onehot_mux(fields["src1.ptag"], zero=u(int(ptag_width), 0)),
            "src1.ready": sel.onehot_mux(fields["src1.ready"], zero=u(1, 0)),
            "dst.valid": sel.onehot_mux(fields["dst.valid"], zero=u(1, 0)),
            "dst.ptag": sel.onehot_mux(fields["dst.ptag"], zero=u(int(ptag_width), 0)),
            "dst.ready": sel.onehot_mux(fields["dst.ready"], zero=u(1, 0)),
            "payload": sel.onehot_mux(fields["payload"], zero=u(int(payload_width), 0)),
        }
        issue_uops.append(vals)
        m.output(f"iss{k}_valid", issue_valid[k])
        m.outputs(uop_spec, vals, prefix=f"iss{k}_")
    return issue_uops


@function
def _issue_wake_vectors_vec(m: Circuit, issue_valid: list, issue_uops: list[dict[str, Any]], issue_ports: int) -> tuple[Vec, Vec]:
    _ = m
    wake_valid = Vec([issue_valid[k] & issue_uops[k]["dst.valid"] for k in range(int(issue_ports))])
    wake_ptag = Vec([issue_uops[k]["dst.ptag"] for k in range(int(issue_ports))])
    return wake_valid, wake_ptag


@function
def _write_entry_next_state_vec(
    m: Circuit,
    *,
    entry_state: list,
    fields: dict[str, Vec],
    enq_uops: list,
    alloc_lane: list[Vec],
    keep_valid: Vec,
    new_alloc: Vec,
    next_valid: Vec,
    wake_valid_v: Vec,
    wake_ptag_v: Vec,
    ready_v: Vec,
    entries: int,
    enq_ports: int,
    ptag_width: int,
    payload_width: int,
    ptag_count: int,
) -> None:
    for i in range(int(entries)):
        new_src0_valid = _alloc_field_vec(m, enq_uops, alloc_lane, i, "src0.valid", 1, int(enq_ports))
        new_src0_ptag = _alloc_field_vec(m, enq_uops, alloc_lane, i, "src0.ptag", int(ptag_width), int(enq_ports))
        new_src0_ready_in = _alloc_field_vec(m, enq_uops, alloc_lane, i, "src0.ready", 1, int(enq_ports))

        new_src1_valid = _alloc_field_vec(m, enq_uops, alloc_lane, i, "src1.valid", 1, int(enq_ports))
        new_src1_ptag = _alloc_field_vec(m, enq_uops, alloc_lane, i, "src1.ptag", int(ptag_width), int(enq_ports))
        new_src1_ready_in = _alloc_field_vec(m, enq_uops, alloc_lane, i, "src1.ready", 1, int(enq_ports))

        new_dst_valid = _alloc_field_vec(m, enq_uops, alloc_lane, i, "dst.valid", 1, int(enq_ports))
        new_dst_ptag = _alloc_field_vec(m, enq_uops, alloc_lane, i, "dst.ptag", int(ptag_width), int(enq_ports))
        new_dst_ready = _alloc_field_vec(m, enq_uops, alloc_lane, i, "dst.ready", 1, int(enq_ports))
        new_payload = _alloc_field_vec(m, enq_uops, alloc_lane, i, "payload", int(payload_width), int(enq_ports))

        cur_src0_valid = fields["src0.valid"][i]
        cur_src0_ptag = fields["src0.ptag"][i]
        cur_src0_ready = fields["src0.ready"][i]
        cur_src1_valid = fields["src1.valid"][i]
        cur_src1_ptag = fields["src1.ptag"][i]
        cur_src1_ready = fields["src1.ready"][i]

        keep_src0_ready = (
            cur_src0_ready
            | _not1(m, cur_src0_valid)
            | _ready_lookup_vec(m, ready_v, cur_src0_ptag, int(ptag_width), int(ptag_count))
            | (cur_src0_valid & _wake_hit_vec(m, wake_valid_v, wake_ptag_v, cur_src0_ptag))
        )
        keep_src1_ready = (
            cur_src1_ready
            | _not1(m, cur_src1_valid)
            | _ready_lookup_vec(m, ready_v, cur_src1_ptag, int(ptag_width), int(ptag_count))
            | (cur_src1_valid & _wake_hit_vec(m, wake_valid_v, wake_ptag_v, cur_src1_ptag))
        )

        new_src0_ready = (
            _not1(m, new_src0_valid)
            | new_src0_ready_in
            | _ready_lookup_vec(m, ready_v, new_src0_ptag, int(ptag_width), int(ptag_count))
            | (new_src0_valid & _wake_hit_vec(m, wake_valid_v, wake_ptag_v, new_src0_ptag))
        )
        new_src1_ready = (
            _not1(m, new_src1_valid)
            | new_src1_ready_in
            | _ready_lookup_vec(m, ready_v, new_src1_ptag, int(ptag_width), int(ptag_count))
            | (new_src1_valid & _wake_hit_vec(m, wake_valid_v, wake_ptag_v, new_src1_ptag))
        )

        st = entry_state[i]
        st["valid"].set(next_valid[i])
        st["uop.src0.valid"].set(_slot_select(m, keep_valid[i], new_alloc[i], fields["src0.valid"][i], new_src0_valid, 1))
        st["uop.src0.ptag"].set(
            _slot_select(m, keep_valid[i], new_alloc[i], fields["src0.ptag"][i], new_src0_ptag, int(ptag_width))
        )
        st["uop.src0.ready"].set(_slot_select(m, keep_valid[i], new_alloc[i], keep_src0_ready, new_src0_ready, 1))

        st["uop.src1.valid"].set(_slot_select(m, keep_valid[i], new_alloc[i], fields["src1.valid"][i], new_src1_valid, 1))
        st["uop.src1.ptag"].set(
            _slot_select(m, keep_valid[i], new_alloc[i], fields["src1.ptag"][i], new_src1_ptag, int(ptag_width))
        )
        st["uop.src1.ready"].set(_slot_select(m, keep_valid[i], new_alloc[i], keep_src1_ready, new_src1_ready, 1))

        st["uop.dst.valid"].set(_slot_select(m, keep_valid[i], new_alloc[i], fields["dst.valid"][i], new_dst_valid, 1))
        st["uop.dst.ptag"].set(
            _slot_select(m, keep_valid[i], new_alloc[i], fields["dst.ptag"][i], new_dst_ptag, int(ptag_width))
        )
        st["uop.dst.ready"].set(_slot_select(m, keep_valid[i], new_alloc[i], fields["dst.ready"][i], new_dst_ready, 1))
        st["uop.payload"].set(
            _slot_select(m, keep_valid[i], new_alloc[i], fields["payload"][i], new_payload, int(payload_width))
        )


@function
def _update_age_state_vec(
    m: Circuit,
    *,
    age_state: Vec,
    age_v: Vec,
    keep_valid: Vec,
    new_alloc: Vec,
    next_valid: Vec,
    alloc_lane: list[Vec],
    entries: int,
    enq_ports: int,
) -> None:
    for i in range(int(entries)):
        for j in range(int(entries)):
            if i == j:
                age_state[i][j].set(u(1, 0))
            else:
                keep_keep = keep_valid[i] & keep_valid[j] & age_v[i, j]
                keep_new = keep_valid[i] & new_alloc[j]
                new_new = new_alloc[i] & new_alloc[j] & _lane_lt(m, alloc_lane, i, j, int(enq_ports))
                rel = keep_keep | keep_new | new_new
                age_state[i][j].set(next_valid[i] & next_valid[j] & rel)


@function
def _update_ready_table_vec(
    m: Circuit,
    *,
    ready_state: list,
    wake_valid_v: Vec,
    wake_ptag_v: Vec,
    ptag_count: int,
    ptag_width: int,
) -> None:
    for t in range(int(ptag_count)):
        wake_t = _wake_hit_vec(m, wake_valid_v, wake_ptag_v, u(int(ptag_width), t))
        ready_state[t].set(ready_state[t].out() | wake_t)


@function
def _emit_debug_and_ready_vec(
    m: Circuit,
    *,
    fields: dict[str, Vec],
    enq_ready: list,
    issue_valid: list,
    issued_total_q,
    enq_ports: int,
    occupancy_width: int,
    issue_count_width: int,
    issued_total_width: int,
) -> None:
    occupancy = fields["valid"].reduce_sum(width=int(occupancy_width))
    issued_this = Vec(issue_valid).reduce_sum(width=int(issue_count_width))
    issued_total_q.set((issued_total_q.out() + issued_this)[0 : int(issued_total_width)])

    for k in range(int(enq_ports)):
        m.output(f"enq{k}_ready", enq_ready[k])
    m.output("occupancy", occupancy)
    m.output("issued_total", issued_total_q.out())


def build(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    *,
    entries: int = 16,
    ptag_count: int = 64,
    payload_width: int = 32,
    enq_ports: int = 2,
    issue_ports: int = 2,
    init_ready_mask: int = 0,
):
    cfg = _derive_cfg(
        m,
        entries=entries,
        ptag_count=ptag_count,
        payload_width=payload_width,
        enq_ports=enq_ports,
        issue_ports=issue_ports,
        init_ready_mask=init_ready_mask,
    )

    e = int(cfg.entries)
    p = int(cfg.ptag_count)
    ptag_w = int(cfg.ptag_width)
    payload_w = int(cfg.payload_width)
    n_enq = int(cfg.enq_ports)
    n_issue = int(cfg.issue_ports)
    occ_w = int(cfg.occupancy_width)
    issue_cnt_w = int(cfg.issue_count_width)
    issued_total_w = int(cfg.issued_total_width)
    cd = domain.clock_domain

    uop_spec = _uop_spec(m, cfg)
    entry_spec = _entry_spec(m, cfg)

    enq_valid = [m.input(f"enq{k}_valid", width=1) for k in range(n_enq)]
    enq_valid_v = Vec(enq_valid)
    enq_uops = [m.inputs(uop_spec, prefix=f"enq{k}_") for k in range(n_enq)]

    entry_state = [
        m.state(entry_spec, clk=cd.clk, rst=cd.rst, prefix=f"ent{i}_", init=0)
        for i in range(e)
    ]

    age_state = m.out("age", domain=cd, width=1, init=u(1, 0), shape=(e, e))

    ready_state = [
        m.out(
            f"ready_ptag_{t}",
            domain=cd,
            width=1,
            init=u(1, (int(cfg.init_ready_mask) >> t) & 1),
        )
        for t in range(p)
    ]
    ready_v = Vec([ready_state[t].out() for t in range(p)])

    issued_total_q = m.out("issued_total_q", domain=cd, width=issued_total_w, init=u(issued_total_w, 0))

    cur = _snapshot_entries(m, entry_state, e)
    fields = {
        "valid": Vec([cur[i]["valid"] for i in range(e)]),
        "src0.valid": Vec([cur[i]["src0_valid"] for i in range(e)]),
        "src0.ptag": Vec([cur[i]["src0_ptag"] for i in range(e)]),
        "src0.ready": Vec([cur[i]["src0_ready"] for i in range(e)]),
        "src1.valid": Vec([cur[i]["src1_valid"] for i in range(e)]),
        "src1.ptag": Vec([cur[i]["src1_ptag"] for i in range(e)]),
        "src1.ready": Vec([cur[i]["src1_ready"] for i in range(e)]),
        "dst.valid": Vec([cur[i]["dst_valid"] for i in range(e)]),
        "dst.ptag": Vec([cur[i]["dst_ptag"] for i in range(e)]),
        "dst.ready": Vec([cur[i]["dst_ready"] for i in range(e)]),
        "payload": Vec([cur[i]["payload"] for i in range(e)]),
    }
    age_v = Vec([Vec([age_state[i][j].out() for j in range(e)]) for i in range(e)])

    _entry_ready, issue_sel, issue_valid, _issue_win, keep_valid = _select_oldest_ready_vec(
        m,
        fields=fields,
        age_v=age_v,
        entries=e,
        issue_ports=n_issue,
    )

    alloc_lane, enq_ready, new_alloc, next_valid = _allocate_enqueue_lanes_vec(
        m,
        enq_valid_v=enq_valid_v,
        keep_valid=keep_valid,
        entries=e,
        enq_ports=n_enq,
    )

    issue_uops = _emit_issue_ports_vec(
        m,
        uop_spec=uop_spec,
        issue_sel=issue_sel,
        issue_valid=issue_valid,
        fields=fields,
        ptag_width=ptag_w,
        payload_width=payload_w,
        issue_ports=n_issue,
    )
    wake_valid_v, wake_ptag_v = _issue_wake_vectors_vec(m, issue_valid, issue_uops, n_issue)

    _write_entry_next_state_vec(
        m,
        entry_state=entry_state,
        fields=fields,
        enq_uops=enq_uops,
        alloc_lane=alloc_lane,
        keep_valid=keep_valid,
        new_alloc=new_alloc,
        next_valid=next_valid,
        wake_valid_v=wake_valid_v,
        wake_ptag_v=wake_ptag_v,
        ready_v=ready_v,
        entries=e,
        enq_ports=n_enq,
        ptag_width=ptag_w,
        payload_width=payload_w,
        ptag_count=p,
    )

    _update_age_state_vec(
        m,
        age_state=age_state,
        age_v=age_v,
        keep_valid=keep_valid,
        new_alloc=new_alloc,
        next_valid=next_valid,
        alloc_lane=alloc_lane,
        entries=e,
        enq_ports=n_enq,
    )
    _update_ready_table_vec(
        m,
        ready_state=ready_state,
        wake_valid_v=wake_valid_v,
        wake_ptag_v=wake_ptag_v,
        ptag_count=p,
        ptag_width=ptag_w,
    )

    _emit_debug_and_ready_vec(
        m,
        fields=fields,
        enq_ready=enq_ready,
        issue_valid=issue_valid,
        issued_total_q=issued_total_q,
        enq_ports=n_enq,
        occupancy_width=occ_w,
        issue_count_width=issue_cnt_w,
        issued_total_width=issued_total_w,
    )


if __name__ == "__main__":
    print(
        compile_cycle_aware(
            build,
            name="issq",
            entries=16,
            ptag_count=64,
            payload_width=32,
            enq_ports=2,
            issue_ports=2,
            init_ready_mask=0,
        ).emit_mlir()
    )
