"""ICache — Instruction Cache for XiangShan-pyc.

VIPT (Virtual Index Physical Tag) set-associative instruction cache.
4-stage pipeline: s0 (request), s1 (tag compare), s2 (hit-miss), s3 (response).

Reference: XiangShan/src/main/scala/xiangshan/frontend/icache/

Pipeline:
  s0  Accept fetch request, compute set index, read Tag/Data SRAMs
  s1  Tag compare: match physical tag, generate per-way hit vector
  s2  Data mux on hit; MSHR allocation on miss; refill bypass
  s3  Drive response (valid, data, hit) to IFU

Key parameters (from XiangShan KunMingHu defaults):
  nSets=256, nWays=4, blockBytes=64  →  64 KB, VIPT

Simplified vs full XiangShan:
  - Single MSHR entry (XiangShan has 4 fetch + 10 prefetch MSHRs)
  - No ECC / parity
  - No prefetch pipe or WayLookup queue
  - No TileLink coherence (Probe/Release)
  - Pipeline flush kills in-flight requests; no content invalidation here
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_XS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_XS_ROOT) not in sys.path:
    sys.path.insert(0, str(_XS_ROOT))

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareSignal,
    cas,
    compile_cycle_aware,
    mux,
    u,
    wire_of,
)
from top.parameters import ICACHE_SETS, ICACHE_WAYS, ICACHE_BLOCK_BYTES, PC_WIDTH, CACHE_LINE_SIZE


def icache(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    *,
    prefix: str = "ic",
    n_sets: int = ICACHE_SETS,
    n_ways: int = ICACHE_WAYS,
    block_bytes: int = ICACHE_BLOCK_BYTES,
    pc_width: int = PC_WIDTH,
    inputs: dict[str, CycleAwareSignal] | None = None,
) -> dict[str, CycleAwareSignal]:
    """ICache: VIPT set-associative instruction cache with 4-stage pipeline."""
    _in = inputs or {}
    _out: dict[str, CycleAwareSignal] = {}


    block_bits = block_bytes * 8
    offset_bits = int(math.log2(block_bytes))
    index_bits = int(math.log2(n_sets))
    tag_bits = pc_width - index_bits - offset_bits
    way_bits = max(1, int(math.log2(n_ways)))
    tag_strobe_w = (tag_bits + 7) // 8
    data_strobe_w = block_bytes

    cd = domain.clock_domain

    # ================================================================
    # s0 — Request: accept fetch, decompose address, read SRAMs
    # ================================================================

    flush = (_in["flush"] if "flush" in _in else

        cas(domain, m.input(f"{prefix}_flush", width=1), cycle=0))
    fetch_valid = (_in["fetch_valid"] if "fetch_valid" in _in else
        cas(domain, m.input(f"{prefix}_fetch_valid", width=1), cycle=0))
    fetch_vaddr = (_in["fetch_vaddr"] if "fetch_vaddr" in _in else
        cas(domain, m.input(f"{prefix}_fetch_vaddr", width=pc_width), cycle=0))
    fetch_ptag = (_in["fetch_ptag"] if "fetch_ptag" in _in else
        cas(domain, m.input(f"{prefix}_fetch_ptag", width=tag_bits), cycle=0))

    refill_valid = (_in["refill_valid"] if "refill_valid" in _in else

        cas(domain, m.input(f"{prefix}_refill_valid", width=1), cycle=0))
    refill_set = (_in["refill_set"] if "refill_set" in _in else
        cas(domain, m.input(f"{prefix}_refill_set", width=index_bits), cycle=0))
    refill_tag = (_in["refill_tag"] if "refill_tag" in _in else
        cas(domain, m.input(f"{prefix}_refill_tag", width=tag_bits), cycle=0))
    refill_way = (_in["refill_way"] if "refill_way" in _in else
        cas(domain, m.input(f"{prefix}_refill_way", width=way_bits), cycle=0))
    refill_data = (_in["refill_data"] if "refill_data" in _in else
        cas(domain, m.input(f"{prefix}_refill_data", width=block_bits), cycle=0))

    s0_set_idx = fetch_vaddr[offset_bits:offset_bits + index_bits]
    s0_fire = fetch_valid & (~flush)

    # ── Feedback state (registers read at cycle 0, updated at end) ──

    valid_regs = [
        domain.signal(width=n_sets, reset_value=0, name=f"{prefix}_vld{w}")
        for w in range(n_ways)
    ]

    mshr_valid = domain.signal(width=1, reset_value=0, name=f"{prefix}_mshr_v")
    mshr_set = domain.signal(width=index_bits, reset_value=0, name=f"{prefix}_mshr_set")
    mshr_tag = domain.signal(width=tag_bits, reset_value=0, name=f"{prefix}_mshr_tag")

    # ── Per-way Tag & Data SRAMs (synchronous read) ─────────────────
    # Address presented at s0; read data available one cycle later (s1).

    tag_rd = []
    data_rd = []
    for w in range(n_ways):
        w_const = cas(domain, m.const(w, width=way_bits), cycle=0)
        wr_en_w = wire_of(refill_valid & (refill_way == w_const))

        tag_rd.append(m.sync_mem(
            cd.clk, cd.rst,
            ren=wire_of(s0_fire), raddr=wire_of(s0_set_idx),
            wvalid=wr_en_w, waddr=wire_of(refill_set),
            wdata=wire_of(refill_tag),
            wstrb=m.const((1 << tag_strobe_w) - 1, width=tag_strobe_w),
            depth=n_sets, name=f"{prefix}_tag_w{w}",
        ))

        data_rd.append(m.sync_mem(
            cd.clk, cd.rst,
            ren=wire_of(s0_fire), raddr=wire_of(s0_set_idx),
            wvalid=wr_en_w, waddr=wire_of(refill_set),
            wdata=wire_of(refill_data),
            wstrb=m.const((1 << data_strobe_w) - 1, width=data_strobe_w),
            depth=n_sets, name=f"{prefix}_data_w{w}",
        ))

    # ── Pipeline registers s0 → s1 ─────────────────────────────────

    s1_valid_w = domain.cycle(wire_of(s0_fire), name=f"{prefix}_s1_v")
    s1_set_idx_w = domain.cycle(wire_of(s0_set_idx), name=f"{prefix}_s1_set")
    s1_ptag_w = domain.cycle(wire_of(fetch_ptag), name=f"{prefix}_s1_ptag")
    s1_vaddr_w = domain.cycle(wire_of(fetch_vaddr), name=f"{prefix}_s1_va")

    domain.next()  # ─────────────── s0 → s1 boundary ───────────────

    # ================================================================
    # s1 — Tag Compare: match physical tag against each way
    # ================================================================

    s1_way_hit = []
    for w in range(n_ways):
        vld_bit = wire_of(valid_regs[w]).lshr(amount=s1_set_idx_w)[0:1]
        tag_eq = (tag_rd[w][0:tag_bits] == s1_ptag_w)
        s1_way_hit.append(vld_bit & tag_eq)

    s1_any_hit = s1_way_hit[0]
    for w in range(1, n_ways):
        s1_any_hit = s1_any_hit | s1_way_hit[w]

    s1_miss = s1_valid_w & (~s1_any_hit)

    s1_hit_data = data_rd[0]
    for w in range(1, n_ways):
        s1_hit_data = s1_way_hit[w].select(data_rd[w], s1_hit_data)

    # ── Pipeline registers s1 → s2 ─────────────────────────────────

    s2_valid_w = domain.cycle(s1_valid_w, name=f"{prefix}_s2_v")
    s2_hit_w = domain.cycle(s1_any_hit, name=f"{prefix}_s2_hit")
    s2_miss_w = domain.cycle(s1_miss, name=f"{prefix}_s2_miss")
    s2_data_w = domain.cycle(s1_hit_data, name=f"{prefix}_s2_data")
    s2_set_idx_w = domain.cycle(s1_set_idx_w, name=f"{prefix}_s2_set")
    s2_ptag_w = domain.cycle(s1_ptag_w, name=f"{prefix}_s2_ptag")

    domain.next()  # ─────────────── s1 → s2 boundary ───────────────

    # ================================================================
    # s2 — Data Mux / Hit-Miss: finalize, MSHR allocation, refill bypass
    # ================================================================

    mshr_free = ~wire_of(mshr_valid)
    mshr_alloc = s2_miss_w & s2_valid_w & mshr_free

    s2_refill_match = (
        wire_of(refill_valid)
        & (wire_of(refill_set) == s2_set_idx_w)
        & (wire_of(refill_tag) == s2_ptag_w)
    )

    s2_resp_hit = s2_hit_w | s2_refill_match
    s2_resp_data = s2_refill_match.select(wire_of(refill_data), s2_data_w)
    s2_resp_valid = s2_valid_w & s2_resp_hit

    # ── Pipeline registers s2 → s3 ─────────────────────────────────

    s3_valid_w = domain.cycle(s2_resp_valid, name=f"{prefix}_s3_v")
    s3_hit_w = domain.cycle(s2_resp_hit, name=f"{prefix}_s3_hit")
    s3_data_w = domain.cycle(s2_resp_data, name=f"{prefix}_s3_data")

    domain.next()  # ─────────────── s2 → s3 boundary ───────────────

    # ================================================================
    # s3 — Response to IFU
    # ================================================================

    # (outputs collected at end of function)

    domain.next()  # ─────────────── s3 → state-update boundary ─────

    # ================================================================
    # State Updates
    # ================================================================

    # Valid bits: set way-valid on refill (no full-flush in simplified model)
    for w in range(n_ways):
        wr_way = wire_of(refill_valid) & (wire_of(refill_way) == m.const(w, width=way_bits))
        one_hot = m.const(1, width=n_sets).shl(amount=wire_of(refill_set))
        new_vld = wire_of(valid_regs[w]) | one_hot
        valid_regs[w] <<= wr_way.select(new_vld, wire_of(valid_regs[w]))

    # MSHR: allocate on miss, clear on refill completion, clear on flush
    mshr_clear = wire_of(refill_valid) & wire_of(mshr_valid)
    nv = mshr_clear.select(
        m.const(0, width=1),
        mshr_alloc.select(m.const(1, width=1), wire_of(mshr_valid)),
    )
    nv = wire_of(flush).select(m.const(0, width=1), nv)
    mshr_valid <<= nv

    mshr_set <<= mshr_alloc.select(s2_set_idx_w, wire_of(mshr_set))
    mshr_tag <<= mshr_alloc.select(s2_ptag_w, wire_of(mshr_tag))

    # ================================================================
    # Output ports
    # ================================================================

    m.output(f"{prefix}_resp_valid", s3_valid_w)
    _out["resp_valid"] = cas(domain, s3_valid_w, cycle=domain.cycle_index)
    m.output(f"{prefix}_resp_data", s3_data_w)
    _out["resp_data"] = cas(domain, s3_data_w, cycle=domain.cycle_index)
    m.output(f"{prefix}_resp_hit", s3_hit_w)
    _out["resp_hit"] = cas(domain, s3_hit_w, cycle=domain.cycle_index)

    m.output(f"{prefix}_miss_valid", mshr_alloc)
    _out["miss_valid"] = cas(domain, mshr_alloc, cycle=domain.cycle_index)
    m.output(f"{prefix}_miss_addr", m.cat(
        s2_ptag_w,
        s2_set_idx_w,
        m.const(0, width=offset_bits),
    ))
    return _out


icache.__pycircuit_name__ = "icache"


if __name__ == "__main__":
    print(compile_cycle_aware(
        icache, name="icache", eager=True,
        n_sets=ICACHE_SETS, n_ways=ICACHE_WAYS,
        block_bytes=ICACHE_BLOCK_BYTES, pc_width=PC_WIDTH,
    ).emit_mlir())
