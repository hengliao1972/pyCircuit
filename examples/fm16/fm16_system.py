#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FM16 vs SW16 System Comparison Simulator.

Compares two 16-NPU topologies side-by-side:

  FM16: Full Mesh — 4 direct links between every NPU pair
        (16×15/2 = 120 bidirectional link-pairs, 480 total links)
        Each pair: 4 × 112 Gbps = 448 Gbps

  SW16: Star via SW5809s — each NPU connects to a central switch
        with 8×4 = 32 links (simplified to SW_LINKS_PER_NPU).
        Switch: VOQ + crossbar + round-robin (MDRR).
        Path: NPU → switch → NPU  (2 hops)

Both run all-to-all continuous 512B packet traffic from 4Tbps HBM.

Usage:
    python examples/fm16/fm16_system.py
"""
from __future__ import annotations

import collections
import random
import re as _re
import sys
import time
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════════
# ANSI
# ═══════════════════════════════════════════════════════════════════
RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
CYAN = "\033[36m"; WHITE = "\033[37m"; MAGENTA = "\033[35m"; BLUE = "\033[34m"
_ANSI = _re.compile(r'\x1b\[[0-9;]*m')
def _vl(s): return len(_ANSI.sub('', s))
def _pad(s, w): return s + ' ' * max(0, w - _vl(s))
def clear(): sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()

# ═══════════════════════════════════════════════════════════════════
# Parameters
# ═══════════════════════════════════════════════════════════════════
N_NPUS           = 16
FM_LINKS_PER_PAIR = 4       # FM16: 4 links per NPU pair
SW_LINKS_PER_NPU = 32      # SW16: 32 links from each NPU to the switch
PKT_SIZE         = 512      # bytes
LINK_BW_GBPS     = 112      # Gbps per link
HBM_BW_TBPS      = 4.0      # Tbps HBM per NPU
PKT_TIME_NS      = PKT_SIZE * 8 / LINK_BW_GBPS  # ~36.6 ns
HBM_INJECT_PROB  = min(1.0, HBM_BW_TBPS * 1000 / LINK_BW_GBPS / N_NPUS)
INJECT_BATCH     = 8
FIFO_DEPTH       = 64
VOQ_DEPTH        = 32
SIM_CYCLES       = 3000
DISPLAY_INTERVAL = 150

FM_LINK_LATENCY  = 3        # direct mesh: 3 cycle pipeline
SW_LINK_LATENCY  = 2        # NPU→switch or switch→NPU: 2 cycles each
SW_XBAR_LATENCY  = 1        # switch internal crossbar: 1 cycle


# ═══════════════════════════════════════════════════════════════════
# Packet
# ═══════════════════════════════════════════════════════════════════
@dataclass
class Packet:
    src: int
    dst: int
    seq: int
    inject_cycle: int
    def latency(self, now): return now - self.inject_cycle


# ═══════════════════════════════════════════════════════════════════
# NPU Node (shared by both topologies)
# ═══════════════════════════════════════════════════════════════════
class NPUNode:
    def __init__(self, nid, n_ports):
        self.id = nid
        self.n_ports = n_ports
        self.out_fifos = [collections.deque(maxlen=FIFO_DEPTH) for _ in range(n_ports)]
        self.seq = 0
        self.pkts_injected = 0
        self.pkts_delivered = 0
        self.latencies: list[int] = []

    def inject(self, cycle, rng):
        for _ in range(INJECT_BATCH):
            if rng.random() > HBM_INJECT_PROB:
                continue
            dst = self.id
            while dst == self.id:
                dst = rng.randint(0, N_NPUS - 1)
            pkt = Packet(self.id, dst, self.seq, cycle)
            self.seq += 1
            port = dst % self.n_ports
            if len(self.out_fifos[port]) < FIFO_DEPTH:
                self.out_fifos[port].append(pkt)
                self.pkts_injected += 1

    def tx(self, port):
        if self.out_fifos[port]:
            return self.out_fifos[port].popleft()
        return None

    def rx(self, pkt, cycle):
        self.pkts_delivered += 1
        self.latencies.append(pkt.latency(cycle))


# ═══════════════════════════════════════════════════════════════════
# SW5809s Switch (behavioral — VOQ + crossbar + round-robin)
# ═══════════════════════════════════════════════════════════════════
class SW5809s:
    def __init__(self, n_ports):
        self.n_ports = n_ports
        self.voqs = [[collections.deque(maxlen=VOQ_DEPTH) for _ in range(n_ports)]
                     for _ in range(n_ports)]
        self.rr = [0] * n_ports
        self.pkts_switched = 0

    def enqueue(self, in_port, pkt):
        out_port = pkt.dst  # direct dst → output port mapping
        if out_port < self.n_ports and len(self.voqs[in_port][out_port]) < VOQ_DEPTH:
            self.voqs[in_port][out_port].append(pkt)
            return True
        return False

    def schedule(self):
        """Round-robin crossbar: one pkt per output per cycle."""
        results = [None] * self.n_ports
        for j in range(self.n_ports):
            for offset in range(self.n_ports):
                i = (self.rr[j] + offset) % self.n_ports
                if self.voqs[i][j]:
                    results[j] = self.voqs[i][j].popleft()
                    self.rr[j] = (i + 1) % self.n_ports
                    self.pkts_switched += 1
                    break
        return results

    def occupancy(self):
        """Total packets buffered in all VOQs."""
        return sum(len(self.voqs[i][j])
                   for i in range(self.n_ports) for j in range(self.n_ports))


# ═══════════════════════════════════════════════════════════════════
# FM16 Topology: full mesh, 4 links per pair
# ═══════════════════════════════════════════════════════════════════
class FM16System:
    def __init__(self):
        self.npus = [NPUNode(i, N_NPUS) for i in range(N_NPUS)]
        self.cycle = 0
        self.rng = random.Random(42)
        self._inflight: list[tuple[int, Packet]] = []

    def step(self):
        for npu in self.npus:
            npu.inject(self.cycle, self.rng)

        for npu in self.npus:
            for port in range(N_NPUS):
                for _ in range(FM_LINKS_PER_PAIR):
                    pkt = npu.tx(port)
                    if pkt is None: break
                    if pkt.dst == npu.id: continue
                    qlat = len(npu.out_fifos[port])
                    self._inflight.append((self.cycle + FM_LINK_LATENCY + qlat, pkt))

        keep = []
        for (t, pkt) in self._inflight:
            if t <= self.cycle:
                self.npus[pkt.dst].rx(pkt, self.cycle)
            else:
                keep.append((t, pkt))
        self._inflight = keep
        self.cycle += 1

    def stats(self):
        return _compute_stats(self.npus, self.cycle)


# ═══════════════════════════════════════════════════════════════════
# SW16 Topology: star through SW5809s
# ═══════════════════════════════════════════════════════════════════
class SW16System:
    def __init__(self):
        self.npus = [NPUNode(i, N_NPUS) for i in range(N_NPUS)]
        self.switch = SW5809s(N_NPUS)
        self.cycle = 0
        self.rng = random.Random(42)
        # Packets in flight: NPU→switch and switch→NPU
        self._to_switch: list[tuple[int, int, Packet]] = []  # (arrive, in_port, pkt)
        self._to_npu:    list[tuple[int, Packet]] = []        # (arrive, pkt)

    def step(self):
        for npu in self.npus:
            npu.inject(self.cycle, self.rng)

        # NPU → switch (up to SW_LINKS_PER_NPU / (N_NPUS-1) pkts per port per cycle)
        links_per_dst = max(1, SW_LINKS_PER_NPU // (N_NPUS - 1))
        for npu in self.npus:
            for port in range(N_NPUS):
                for _ in range(links_per_dst):
                    pkt = npu.tx(port)
                    if pkt is None: break
                    if pkt.dst == npu.id: continue
                    self._to_switch.append((self.cycle + SW_LINK_LATENCY, npu.id, pkt))

        # Deliver to switch input ports
        keep_sw = []
        for (t, inp, pkt) in self._to_switch:
            if t <= self.cycle:
                self.switch.enqueue(inp, pkt)
            else:
                keep_sw.append((t, inp, pkt))
        self._to_switch = keep_sw

        # Switch crossbar scheduling
        winners = self.switch.schedule()
        for pkt in winners:
            if pkt is not None:
                self._to_npu.append((self.cycle + SW_XBAR_LATENCY + SW_LINK_LATENCY, pkt))

        # Deliver from switch to destination NPU
        keep_npu = []
        for (t, pkt) in self._to_npu:
            if t <= self.cycle:
                self.npus[pkt.dst].rx(pkt, self.cycle)
            else:
                keep_npu.append((t, pkt))
        self._to_npu = keep_npu

        self.cycle += 1

    def stats(self):
        s = _compute_stats(self.npus, self.cycle)
        s["sw_occupancy"] = self.switch.occupancy()
        s["sw_switched"] = self.switch.pkts_switched
        return s


# ═══════════════════════════════════════════════════════════════════
# Statistics helper
# ═══════════════════════════════════════════════════════════════════
def _compute_stats(npus, cycle):
    all_lats = []
    total_inj = total_del = 0
    for n in npus:
        all_lats.extend(n.latencies)
        total_inj += n.pkts_injected
        total_del += n.pkts_delivered
    if not all_lats:
        return {"avg":0,"p50":0,"p95":0,"p99":0,"max_lat":0,
                "bw_gbps":0,"inj":total_inj,"del":total_del,"npu_del":[0]*len(npus)}
    all_lats.sort()
    n = len(all_lats)
    t_ns = cycle * PKT_TIME_NS
    return {
        "avg": sum(all_lats)/n,
        "p50": all_lats[n//2],
        "p95": all_lats[int(n*0.95)],
        "p99": all_lats[int(n*0.99)],
        "max_lat": all_lats[-1],
        "bw_gbps": total_del * PKT_SIZE * 8 / t_ns if t_ns > 0 else 0,
        "inj": total_inj,
        "del": total_del,
        "npu_del": [npu.pkts_delivered for npu in npus],
    }

def _hist(npus, bins=12):
    lats = []
    for n in npus: lats.extend(n.latencies)
    if not lats: return [], 0, 0
    lo, hi = min(lats), max(lats)
    if lo == hi: return [len(lats)], lo, hi
    bw = max(1, (hi - lo + bins - 1) // bins)
    h = [0] * bins
    for l in lats:
        h[min((l - lo) // bw, bins - 1)] += 1
    return h, lo, hi


# ═══════════════════════════════════════════════════════════════════
# Side-by-side visualization
# ═══════════════════════════════════════════════════════════════════
COL_W = 35   # width of each column
BOX_W = COL_W * 2 + 5  # total inner width

def _bl(content):
    return f"  {CYAN}║{RESET}{_pad(content, BOX_W)}{CYAN}║{RESET}"

def _bar(v, mx, w=14, ch="█", co=GREEN):
    if mx <= 0: return ""
    n = min(int(v / mx * w), w)
    return f"{co}{ch*n}{RESET}"

def _side(left, right):
    """Render two strings side-by-side in the box."""
    return _bl(f"  {_pad(left, COL_W)} │ {_pad(right, COL_W)}")

def draw(fm, sw, cycle):
    clear()
    bar = "═" * BOX_W
    sf = fm.stats()
    ss = sw.stats()
    pct = cycle * 100 // SIM_CYCLES

    print(f"\n  {CYAN}╔{bar}╗{RESET}")
    print(_bl(f"  {BOLD}{WHITE}FM16 vs SW16 — Side-by-Side Comparison{RESET}"))
    print(f"  {CYAN}╠{bar}╣{RESET}")
    print(_bl(f"  {DIM}16 NPU | HBM {HBM_BW_TBPS}Tbps | 512B pkts | All-to-all{RESET}"))
    prog = _bar(cycle, SIM_CYCLES, 30, "█", CYAN)
    print(_bl(f"  Cycle {cycle}/{SIM_CYCLES} [{prog}] {pct}%"))
    print(f"  {CYAN}╠{bar}╣{RESET}")

    # Headers
    print(_side(f"{BOLD}{YELLOW}FM16 (Full Mesh){RESET}",
                f"{BOLD}{MAGENTA}SW16 (Switch){RESET}"))
    print(_side(f"{DIM}4 links/pair, direct{RESET}",
                f"{DIM}{SW_LINKS_PER_NPU} links/NPU→SW, VOQ+xbar{RESET}"))
    print(_bl(f"  {'─' * COL_W} │ {'─' * COL_W}"))

    # Bandwidth
    print(_side(f"BW: {BOLD}{sf['bw_gbps']:>8.0f}{RESET} Gbps",
                f"BW: {BOLD}{ss['bw_gbps']:>8.0f}{RESET} Gbps"))
    print(_side(f"Injected:  {sf['inj']:>8d}",
                f"Injected:  {ss['inj']:>8d}"))
    print(_side(f"Delivered: {sf['del']:>8d}",
                f"Delivered: {ss['del']:>8d}"))
    sw_extra = f"  SW queued: {ss.get('sw_occupancy',0):>5d}"
    print(_side("", sw_extra))

    print(_bl(f"  {'─' * COL_W} │ {'─' * COL_W}"))

    # Latency
    print(_side(f"Avg: {YELLOW}{sf['avg']:>5.1f}{RESET}  P50:{sf['p50']:>3d}  P99:{sf['p99']:>3d}",
                f"Avg: {YELLOW}{ss['avg']:>5.1f}{RESET}  P50:{ss['p50']:>3d}  P99:{ss['p99']:>3d}"))
    print(_side(f"Max: {sf['max_lat']:>3d} cycles",
                f"Max: {ss['max_lat']:>3d} cycles"))

    print(_bl(f"  {'─' * COL_W} │ {'─' * COL_W}"))

    # Per-NPU bars
    print(_side(f"{BOLD}Per-NPU delivered:{RESET}", f"{BOLD}Per-NPU delivered:{RESET}"))
    max_f = max(sf["npu_del"]) if sf["npu_del"] else 1
    max_s = max(ss["npu_del"]) if ss["npu_del"] else 1
    mx = max(max_f, max_s, 1)
    for i in range(N_NPUS):
        fd = sf["npu_del"][i] if i < len(sf["npu_del"]) else 0
        sd = ss["npu_del"][i] if i < len(ss["npu_del"]) else 0
        fb = _bar(fd, mx, 12, "█", GREEN)
        sb = _bar(sd, mx, 12, "█", MAGENTA)
        print(_side(f" {i:>2d}:{fb}{fd:>6d}", f" {i:>2d}:{sb}{sd:>6d}"))

    print(_bl(f"  {'─' * COL_W} │ {'─' * COL_W}"))

    # Latency histograms
    hf, lof, hif = _hist(fm.npus, bins=8)
    hs, los, his = _hist(sw.npus, bins=8)
    print(_side(f"{BOLD}Latency Histogram:{RESET}", f"{BOLD}Latency Histogram:{RESET}"))
    maxh = max(max(hf, default=1), max(hs, default=1), 1)
    nbins = max(len(hf), len(hs))
    for bi in range(nbins):
        bwf = max(1, (hif - lof + len(hf) - 1) // len(hf)) if hf else 1
        bws = max(1, (his - los + len(hs) - 1) // len(hs)) if hs else 1
        fv = hf[bi] if bi < len(hf) else 0
        sv = hs[bi] if bi < len(hs) else 0
        flo = lof + bi * bwf if hf else 0
        slo = los + bi * bws if hs else 0
        fb = _bar(fv, maxh, 10, "▓", GREEN)
        sb = _bar(sv, maxh, 10, "▓", MAGENTA)
        print(_side(f" {flo:>3d}+: {fb}{fv:>6d}", f" {slo:>3d}+: {sb}{sv:>6d}"))

    print(_bl(""))
    print(f"  {CYAN}╚{bar}╝{RESET}")
    print()


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
def main():
    print(f"  {BOLD}FM16 vs SW16 — Topology Comparison Simulator{RESET}")
    print(f"  Initializing 2 × 16 NPU systems...")

    fm = FM16System()
    sw = SW16System()

    print(f"  {GREEN}Systems ready. Running {SIM_CYCLES} cycles...{RESET}")
    time.sleep(0.3)

    t0 = time.time()
    for cyc in range(SIM_CYCLES):
        fm.step()
        sw.step()
        if (cyc + 1) % DISPLAY_INTERVAL == 0 or cyc == SIM_CYCLES - 1:
            draw(fm, sw, cyc + 1)
            elapsed = time.time() - t0
            if elapsed < 0.3:
                time.sleep(0.03)
    t1 = time.time()

    sf = fm.stats()
    ss = sw.stats()
    print(f"  {GREEN}{BOLD}Simulation complete!{RESET}  ({t1-t0:.2f}s)")
    print(f"  {'─'*60}")
    print(f"  {'':20s} {'FM16':>15s} {'SW16':>15s}")
    print(f"  {'Bandwidth (Gbps)':20s} {sf['bw_gbps']:>15.0f} {ss['bw_gbps']:>15.0f}")
    print(f"  {'Avg Latency':20s} {sf['avg']:>15.1f} {ss['avg']:>15.1f}")
    print(f"  {'P50 Latency':20s} {sf['p50']:>15d} {ss['p50']:>15d}")
    print(f"  {'P95 Latency':20s} {sf['p95']:>15d} {ss['p95']:>15d}")
    print(f"  {'P99 Latency':20s} {sf['p99']:>15d} {ss['p99']:>15d}")
    print(f"  {'Max Latency':20s} {sf['max_lat']:>15d} {ss['max_lat']:>15d}")
    print(f"  {'Delivered pkts':20s} {sf['del']:>15d} {ss['del']:>15d}")
    print()


if __name__ == "__main__":
    main()
