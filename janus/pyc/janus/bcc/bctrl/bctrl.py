from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire
from pycircuit.dsl import Signal

from .bisq import bisq_has_space, bisq_head, make_bisq_regs
from .brenu import lookup_tile, make_brenu_regs, rewrite_payload_tile
from .brob import brob_has_space, brob_head, make_brob_regs


@dataclass(frozen=True)
class BCtrlOutputs:
    cmd_accept: Wire
    dispatch_tma_valid: Wire
    dispatch_tma_brob: Wire
    dispatch_tma_payload: Wire
    dispatch_cube_valid: Wire
    dispatch_cube_brob: Wire
    dispatch_cube_payload: Wire
    dispatch_tau_valid: Wire
    dispatch_tau_brob: Wire
    dispatch_tau_payload: Wire
    retire_valid: Wire
    retire_tag: Wire
    retire_tile: Wire
    retire_pe: Wire
    bisq_count: Wire
    brob_count: Wire


def _fit_u8(m: Circuit, v: Wire) -> Wire:
    if v.width < 8:
        return v.zext(width=8)
    if v.width > 8:
        return v.trunc(width=8)
    return v


def _fit_idx(v: Wire, *, width: int) -> Wire:
    if v.width < width:
        return v.zext(width=width)
    if v.width > width:
        return v.trunc(width=width)
    return v


def build_bctrl(
    m: Circuit,
    *,
    clk: Signal,
    rst: Signal,
    cmd_valid: Wire,
    cmd_kind: Wire,
    cmd_payload: Wire,
    cmd_tile: Wire,
    cmd_tag: Wire,
    tma_ready: Wire,
    cube_ready: Wire,
    tau_ready: Wire,
    tma_done_valid: Wire,
    tma_done_brob: Wire,
    cube_done_valid: Wire,
    cube_done_brob: Wire,
    tau_done_valid: Wire,
    tau_done_brob: Wire,
    bisq_depth: int = 16,
    brob_depth: int = 32,
) -> BCtrlOutputs:
    c = m.const
    cmd_valid = m.wire(cmd_valid)
    cmd_kind = m.wire(cmd_kind)
    cmd_payload = m.wire(cmd_payload)
    cmd_tile = m.wire(cmd_tile)
    cmd_tag = m.wire(cmd_tag)
    tma_ready = m.wire(tma_ready)
    cube_ready = m.wire(cube_ready)
    tau_ready = m.wire(tau_ready)
    tma_done_valid = m.wire(tma_done_valid)
    tma_done_brob = m.wire(tma_done_brob)
    cube_done_valid = m.wire(cube_done_valid)
    cube_done_brob = m.wire(cube_done_brob)
    tau_done_valid = m.wire(tau_done_valid)
    tau_done_brob = m.wire(tau_done_brob)

    bisq = make_bisq_regs(m, clk, rst, depth=bisq_depth)
    brob = make_brob_regs(m, clk, rst, depth=brob_depth)
    brenu = make_brenu_regs(m, clk, rst, logical_tiles=64)

    bisq_space = bisq_has_space(m, bisq, depth=bisq_depth)
    brob_space = brob_has_space(m, brob, depth=brob_depth)
    cmd_accept = cmd_valid & bisq_space & brob_space

    pe_sel = cmd_kind.eq(c(0, width=2)).select(c(0, width=2), c(2, width=2))
    pe_sel = cmd_kind.eq(c(1, width=2)).select(c(1, width=2), pe_sel)

    physical_tile = lookup_tile(m, brenu, cmd_tile)
    renamed_payload = rewrite_payload_tile(m, payload=cmd_payload, physical_tile=physical_tile, tile_bits=6)

    qh = bisq_head(m, bisq)
    head_ready = qh.pe_sel.eq(c(0, width=2)).select(tma_ready, qh.pe_sel.eq(c(1, width=2)).select(cube_ready, tau_ready))
    dispatch_fire = qh.valid & head_ready

    dispatch_tma_valid = dispatch_fire & qh.pe_sel.eq(c(0, width=2))
    dispatch_cube_valid = dispatch_fire & qh.pe_sel.eq(c(1, width=2))
    dispatch_tau_valid = dispatch_fire & qh.pe_sel.eq(c(2, width=2))

    done_any = tma_done_valid | cube_done_valid | tau_done_valid
    done_brob_u8 = tma_done_valid.select(tma_done_brob, cube_done_valid.select(cube_done_brob, tau_done_brob))
    done_brob_idx = _fit_idx(done_brob_u8, width=brob.head.width)

    bh = brob_head(m, brob)
    retire_valid = bh.valid & bh.done

    # --- BISQ updates ---
    for i in range(bisq_depth):
        idx = c(i, width=bisq.head.width)
        push_hit = cmd_accept & bisq.tail.out().eq(idx)
        pop_hit = dispatch_fire & bisq.head.out().eq(idx)

        valid_next = bisq.valid[i].out()
        valid_next = pop_hit.select(c(0, width=1), valid_next)
        valid_next = push_hit.select(c(1, width=1), valid_next)
        bisq.valid[i].set(valid_next)

        bid_next = bisq.brob_idx[i].out()
        bid_next = push_hit.select(_fit_u8(m, brob.tail.out()), bid_next)
        bisq.brob_idx[i].set(bid_next)

        pe_next = bisq.pe_sel[i].out()
        pe_next = push_hit.select(pe_sel, pe_next)
        bisq.pe_sel[i].set(pe_next)

        payload_next = bisq.payload[i].out()
        payload_next = push_hit.select(renamed_payload, payload_next)
        bisq.payload[i].set(payload_next)

    bisq_head_next = dispatch_fire.select(bisq.head.out() + c(1, width=bisq.head.width), bisq.head.out())
    bisq_tail_next = cmd_accept.select(bisq.tail.out() + c(1, width=bisq.tail.width), bisq.tail.out())
    bisq_cnt_next = bisq.count.out()
    only_push = cmd_accept & (~dispatch_fire)
    only_pop = dispatch_fire & (~cmd_accept)
    plus1_bisq = c(1, width=bisq.count.width)
    minus1_bisq = (~plus1_bisq) + c(1, width=bisq.count.width)
    bisq_cnt_next = only_push.select(bisq_cnt_next + plus1_bisq, bisq_cnt_next)
    bisq_cnt_next = only_pop.select(bisq_cnt_next + minus1_bisq, bisq_cnt_next)
    bisq.head.set(bisq_head_next)
    bisq.tail.set(bisq_tail_next)
    bisq.count.set(bisq_cnt_next)

    # --- BROB updates ---
    for i in range(brob_depth):
        idx = c(i, width=brob.head.width)
        alloc_hit = cmd_accept & brob.tail.out().eq(idx)
        done_hit = done_any & done_brob_idx.eq(idx)
        retire_hit = retire_valid & brob.head.out().eq(idx)

        valid_next = brob.valid[i].out()
        valid_next = retire_hit.select(c(0, width=1), valid_next)
        valid_next = alloc_hit.select(c(1, width=1), valid_next)
        brob.valid[i].set(valid_next)

        done_next = brob.done[i].out()
        done_next = retire_hit.select(c(0, width=1), done_next)
        done_next = alloc_hit.select(c(0, width=1), done_next)
        done_next = done_hit.select(c(1, width=1), done_next)
        brob.done[i].set(done_next)

        pe_next = brob.pe_sel[i].out()
        pe_next = alloc_hit.select(pe_sel, pe_next)
        brob.pe_sel[i].set(pe_next)

        tag_next = brob.tag[i].out()
        tag_next = alloc_hit.select(_fit_u8(m, cmd_tag), tag_next)
        brob.tag[i].set(tag_next)

        tile_next = brob.tile[i].out()
        tile_next = alloc_hit.select(_fit_idx(physical_tile, width=6), tile_next)
        brob.tile[i].set(tile_next)

        payload_next = brob.payload[i].out()
        payload_next = alloc_hit.select(renamed_payload, payload_next)
        brob.payload[i].set(payload_next)

    brob_head_next = retire_valid.select(brob.head.out() + c(1, width=brob.head.width), brob.head.out())
    brob_tail_next = cmd_accept.select(brob.tail.out() + c(1, width=brob.tail.width), brob.tail.out())
    brob_cnt_next = brob.count.out()
    only_alloc = cmd_accept & (~retire_valid)
    only_retire = retire_valid & (~cmd_accept)
    plus1_brob = c(1, width=brob.count.width)
    minus1_brob = (~plus1_brob) + c(1, width=brob.count.width)
    brob_cnt_next = only_alloc.select(brob_cnt_next + plus1_brob, brob_cnt_next)
    brob_cnt_next = only_retire.select(brob_cnt_next + minus1_brob, brob_cnt_next)
    brob.head.set(brob_head_next)
    brob.tail.set(brob_tail_next)
    brob.count.set(brob_cnt_next)

    # --- BRENU updates ---
    next_phys = brenu.next_phys.out()
    next_phys = cmd_accept.select(next_phys + c(1, width=next_phys.width), next_phys)
    brenu.next_phys.set(next_phys)

    for i in range(len(brenu.tile_map)):
        idx = c(i, width=cmd_tile.width)
        hit = cmd_accept & cmd_tile.eq(idx)
        map_next = brenu.tile_map[i].out()
        map_next = hit.select(physical_tile, map_next)
        brenu.tile_map[i].set(map_next)

    m.output("bctrl_cmd_accept", cmd_accept)
    m.output("bctrl_dispatch_tma", dispatch_tma_valid)
    m.output("bctrl_dispatch_cube", dispatch_cube_valid)
    m.output("bctrl_dispatch_tau", dispatch_tau_valid)
    m.output("bctrl_retire_valid", retire_valid)
    m.output("bctrl_retire_tag", bh.tag)
    m.output("bctrl_retire_tile", bh.tile)
    m.output("bctrl_retire_pe", bh.pe_sel)
    m.output("bctrl_bisq_count", bisq.count)
    m.output("bctrl_brob_count", brob.count)

    return BCtrlOutputs(
        cmd_accept=cmd_accept,
        dispatch_tma_valid=dispatch_tma_valid,
        dispatch_tma_brob=qh.brob_idx,
        dispatch_tma_payload=qh.payload,
        dispatch_cube_valid=dispatch_cube_valid,
        dispatch_cube_brob=qh.brob_idx,
        dispatch_cube_payload=qh.payload,
        dispatch_tau_valid=dispatch_tau_valid,
        dispatch_tau_brob=qh.brob_idx,
        dispatch_tau_payload=qh.payload,
        retire_valid=retire_valid,
        retire_tag=bh.tag,
        retire_tile=bh.tile,
        retire_pe=bh.pe_sel,
        bisq_count=bisq.count.out(),
        brob_count=brob.count.out(),
    )
