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
SW_LINKS_PER_NPU = 32      # SW16: 32 links from each NPU to the switch (8×4)
SW_XBAR_LINKS    = 512     # SW5809s: 512×512 physical links (112Gbps each)
SW_LINKS_PER_PORT = 4      # 4 links bundled as 1 logical port
SW_XBAR_PORTS    = SW_XBAR_LINKS // SW_LINKS_PER_PORT  # 128 logical ports
SW_PORTS_PER_NPU = SW_LINKS_PER_NPU // SW_LINKS_PER_PORT  # 8 logical ports per NPU
PKT_SIZE         = 512      # bytes
LINK_BW_GBPS     = 112      # Gbps per link
HBM_BW_TBPS      = 4.0      # Tbps HBM per NPU
PKT_TIME_NS      = PKT_SIZE * 8 / LINK_BW_GBPS  # ~36.6 ns
HBM_INJECT_PROB  = min(1.0, HBM_BW_TBPS * 1000 / LINK_BW_GBPS / N_NPUS)
INJECT_BATCH     = 8      # ~8 pkt/cycle/NPU ≈ SW capacity (128 ports / 16 NPUs)
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
    """SW5809s: 512×512 link crossbar, 128×128 logical port crossbar.

    Physical: 512 input links × 512 output links (each 112 Gbps).
    Logical:  every 4 links are bundled into 1 port → 128×128 port crossbar.
    Each logical port is independently arbitrated: up to
    SW_LINKS_PER_PORT (4) packets per cycle.

    NPU mapping: NPU i → ports [i*8 .. i*8+7] (8 ports, 32 links).

    Ingress path for a packet from src_npu to dst_npu:
      1. Pick one of dst_npu's 8 egress ports via ECMP hash/policy
      2. Enqueue into VOQ[input_port][chosen_egress_port]
      3. Egress arbiter grants crossbar connection and delivers

    ECMP modes:
      'independent' : each input port has its own independent RR per dest NPU.
                      This is the REAL hardware behavior — causes VOQ collision
                      because uncoordinated RR pointers naturally converge.
      'coordinated' : a single global RR per dest NPU shared across all input
                      ports — ideal distribution, no collision (reference).

    VOQ collision: when multiple input ports independently pick the *same*
    egress port for the same destination NPU, those packets pile up in
    VOQs targeting that one port while the other 7 ports sit idle.
    This increases tail latency significantly under high load.
    """

    def __init__(self, ecmp_mode: str = "independent"):
        self.n_ports = SW_XBAR_PORTS       # 128
        self.ports_per_npu = SW_PORTS_PER_NPU  # 8
        self.pkts_per_port = SW_LINKS_PER_PORT  # 4
        self.ecmp_mode = ecmp_mode

        self.voqs = [[collections.deque(maxlen=VOQ_DEPTH)
                      for _ in range(self.n_ports)]
                     for _ in range(self.n_ports)]
        self.rr = [0] * self.n_ports

        # Independent mode: each input port has its own RR pointer per dest NPU
        # Shape: [n_ports][N_NPUS] — 128 × 16 = 2048 independent counters
        self.ingress_rr = [[0] * N_NPUS for _ in range(self.n_ports)]

        # Coordinated mode: single global RR per dest NPU (ideal reference)
        self.global_rr = [0] * N_NPUS

        self.rng = random.Random(123)

        # Statistics
        self.pkts_switched = 0
        self.pkts_enqueued = 0
        self.pkts_dropped  = 0     # VOQ full drops
        self.port_enq_count = [0] * self.n_ports  # per-egress-port enqueue count

    def npu_to_ports(self, npu_id):
        base = npu_id * self.ports_per_npu
        return range(base, base + self.ports_per_npu)

    def enqueue(self, src_npu, in_port_hint, pkt):
        """Enqueue packet arriving at a specific input port.

        in_port_hint: the physical input port index (within src NPU's 8 ports).
        The input port uses its OWN independent RR to pick the egress port.
        """
        dst_npu = pkt.dst
        if dst_npu == src_npu or dst_npu >= N_NPUS:
            return False

        # Determine actual input port
        in_port = src_npu * self.ports_per_npu + (in_port_hint % self.ports_per_npu)
        dst_base = dst_npu * self.ports_per_npu

        # ECMP: pick one of dst_npu's 8 egress ports
        if self.ecmp_mode == "independent":
            # Each input port has its own RR counter per dest NPU
            idx = self.ingress_rr[in_port][dst_npu]
            self.ingress_rr[in_port][dst_npu] = (idx + 1) % self.ports_per_npu
        else:  # coordinated
            # Global RR shared by ALL input ports → perfect distribution
            idx = self.global_rr[dst_npu]
            self.global_rr[dst_npu] = (idx + 1) % self.ports_per_npu

        out_port = dst_base + idx

        if len(self.voqs[in_port][out_port]) < VOQ_DEPTH:
            self.voqs[in_port][out_port].append(pkt)
            self.pkts_enqueued += 1
            self.port_enq_count[out_port] += 1
            return True
        self.pkts_dropped += 1
        return False

    def schedule(self):
        """Crossbar scheduling: each egress port independently arbitrates
        to select exactly 1 packet per cycle from all input-port VOQs.

        128 egress ports × 1 pkt/cycle = 128 pkt/cycle max throughput.
        Round-robin arbiter per egress port scans across 128 input ports.
        """
        delivered = []
        for out_port in range(self.n_ports):
            dest_npu = out_port // self.ports_per_npu
            # Round-robin: pick 1 packet from any input port's VOQ
            for offset in range(self.n_ports):
                in_port = (self.rr[out_port] + offset) % self.n_ports
                if in_port // self.ports_per_npu == dest_npu:
                    continue  # skip loopback
                if self.voqs[in_port][out_port]:
                    pkt = self.voqs[in_port][out_port].popleft()
                    self.rr[out_port] = (in_port + 1) % self.n_ports
                    self.pkts_switched += 1
                    delivered.append((dest_npu, pkt))
                    break  # exactly 1 per egress port per cycle
        return delivered

    def occupancy(self):
        return sum(len(self.voqs[i][j])
                   for i in range(self.n_ports) for j in range(self.n_ports))

    def port_load_imbalance(self):
        """Return (min, avg, max) enqueue count across egress ports per NPU."""
        imbalances = []
        for npu in range(N_NPUS):
            ports = self.npu_to_ports(npu)
            counts = [self.port_enq_count[p] for p in ports]
            if max(counts) > 0:
                imbalances.append((min(counts), sum(counts)/len(counts), max(counts)))
        if not imbalances:
            return 0, 0, 0
        mins = [x[0] for x in imbalances]
        avgs = [x[1] for x in imbalances]
        maxs = [x[2] for x in imbalances]
        return sum(mins)/len(mins), sum(avgs)/len(avgs), sum(maxs)/len(maxs)


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
    def __init__(self, ecmp_mode="ideal_rr"):
        self.ecmp_mode = ecmp_mode
        self.npus = [NPUNode(i, N_NPUS) for i in range(N_NPUS)]
        self.switch = SW5809s(ecmp_mode=ecmp_mode)
        self.cycle = 0
        self.rng = random.Random(42)
        self._to_switch: list[tuple[int, int, Packet]] = []  # (arrive, src_npu, pkt)
        self._to_npu:    list[tuple[int, Packet]] = []        # (arrive, pkt)

    def step(self):
        for npu in self.npus:
            npu.inject(self.cycle, self.rng)

        # NPU → switch: each NPU can push up to SW_LINKS_PER_NPU pkts/cycle
        # Packets are distributed across the NPU's 8 input ports via RR
        for npu in self.npus:
            sent = 0
            for port in range(N_NPUS):
                while sent < SW_LINKS_PER_NPU:
                    pkt = npu.tx(port)
                    if pkt is None: break
                    if pkt.dst == npu.id: continue
                    # Assign to one of src NPU's 8 input ports (RR)
                    in_port_idx = sent % SW_PORTS_PER_NPU
                    self._to_switch.append((self.cycle + SW_LINK_LATENCY,
                                           npu.id, in_port_idx, pkt))
                    sent += 1

        # Deliver to switch — each packet arrives at a specific input port
        keep = []
        for (t, src, port_idx, pkt) in self._to_switch:
            if t <= self.cycle:
                self.switch.enqueue(src, port_idx, pkt)
            else:
                keep.append((t, src, port_idx, pkt))
        self._to_switch = keep

        # Switch crossbar: 128 ports × 4 pkt/port = up to 512 pkt/cycle
        delivered = self.switch.schedule()
        for (dst_npu, pkt) in delivered:
            self._to_npu.append((self.cycle + SW_XBAR_LATENCY + SW_LINK_LATENCY, pkt))

        # Deliver to destination NPU
        keep2 = []
        for (t, pkt) in self._to_npu:
            if t <= self.cycle:
                self.npus[pkt.dst].rx(pkt, self.cycle)
            else:
                keep2.append((t, pkt))
        self._to_npu = keep2

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
    n_npus = len(npus)
    agg_bw = total_del * PKT_SIZE * 8 / t_ns if t_ns > 0 else 0
    return {
        "avg": sum(all_lats)/n,
        "p50": all_lats[n//2],
        "p95": all_lats[int(n*0.95)],
        "p99": all_lats[int(n*0.99)],
        "max_lat": all_lats[-1],
        "agg_bw_gbps": agg_bw,
        "per_npu_bw_gbps": agg_bw / n_npus if n_npus > 0 else 0,
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
    print(_side(f"{DIM}4 links/pair, 1 hop{RESET}",
                f"{DIM}{SW_XBAR_LINKS}×{SW_XBAR_LINKS} xbar, {SW_LINKS_PER_PORT}link/port, 2 hop{RESET}"))
    print(_bl(f"  {'─' * COL_W} │ {'─' * COL_W}"))

    # Bandwidth (per NPU)
    fm_max = (N_NPUS - 1) * FM_LINKS_PER_PAIR * LINK_BW_GBPS  # 15×4×112 = 6720
    sw_max = SW_LINKS_PER_NPU * LINK_BW_GBPS                   # 32×112 = 3584
    # But switch crossbar limits to 1 pkt/output/cycle → effective max:
    sw_eff = LINK_BW_GBPS  # 1 pkt per output per cycle = 112 Gbps per dest
    print(_side(f"Per-NPU BW: {BOLD}{sf['per_npu_bw_gbps']:>6.0f}{RESET} Gbps",
                f"Per-NPU BW: {BOLD}{ss['per_npu_bw_gbps']:>6.0f}{RESET} Gbps"))
    print(_side(f"  (max: {fm_max} Gbps mesh)",
                f"  (max: {sw_max} Gbps link)"))
    print(_side(f"Aggregate: {sf['agg_bw_gbps']:>8.0f} Gbps",
                f"Aggregate: {ss['agg_bw_gbps']:>8.0f} Gbps"))
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
    print(f"  {BOLD}FM16 vs SW16 — Topology + ECMP Collision Comparison{RESET}")
    print(f"  Initializing 3 systems (FM16 + SW16-independent + SW16-coordinated)...")

    fm  = FM16System()
    sw_ind  = SW16System(ecmp_mode="independent")   # real hardware: VOQ collision
    sw_crd  = SW16System(ecmp_mode="coordinated")   # ideal: no collision

    print(f"  {GREEN}Systems ready. Running {SIM_CYCLES} cycles...{RESET}")
    time.sleep(0.3)

    t0 = time.time()
    for cyc in range(SIM_CYCLES):
        fm.step()
        sw_ind.step()
        sw_crd.step()
        if (cyc + 1) % DISPLAY_INTERVAL == 0 or cyc == SIM_CYCLES - 1:
            draw(fm, sw_ind, cyc + 1)
            elapsed = time.time() - t0
            if elapsed < 0.3:
                time.sleep(0.03)
    t1 = time.time()

    sf   = fm.stats()
    si   = sw_ind.stats()
    sc   = sw_crd.stats()
    li_min, li_avg, li_max = sw_ind.switch.port_load_imbalance()
    lc_min, lc_avg, lc_max = sw_crd.switch.port_load_imbalance()

    print(f"  {GREEN}{BOLD}Simulation complete!{RESET}  ({t1-t0:.2f}s)")
    print(f"  {'─'*72}")
    print(f"  {'':24s} {'FM16':>14s} {'SW16-indep':>14s} {'SW16-coord':>14s}")
    print(f"  {'Per-NPU BW (Gbps)':24s} {sf['per_npu_bw_gbps']:>14.0f} {si['per_npu_bw_gbps']:>14.0f} {sc['per_npu_bw_gbps']:>14.0f}")
    print(f"  {'Aggregate BW (Gbps)':24s} {sf['agg_bw_gbps']:>14.0f} {si['agg_bw_gbps']:>14.0f} {sc['agg_bw_gbps']:>14.0f}")
    print(f"  {'Avg Latency (cycles)':24s} {sf['avg']:>14.1f} {si['avg']:>14.1f} {sc['avg']:>14.1f}")
    print(f"  {'P50 Latency':24s} {sf['p50']:>14d} {si['p50']:>14d} {sc['p50']:>14d}")
    print(f"  {'P95 Latency':24s} {sf['p95']:>14d} {si['p95']:>14d} {sc['p95']:>14d}")
    print(f"  {'P99 Latency':24s} {sf['p99']:>14d} {si['p99']:>14d} {sc['p99']:>14d}")
    print(f"  {'Max Latency':24s} {sf['max_lat']:>14d} {si['max_lat']:>14d} {sc['max_lat']:>14d}")
    print(f"  {'Delivered pkts':24s} {sf['del']:>14d} {si['del']:>14d} {sc['del']:>14d}")
    print(f"  {'Dropped pkts':24s} {'N/A':>14s} {si.get('sw_dropped',sw_ind.switch.pkts_dropped):>14d} {sc.get('sw_dropped',sw_crd.switch.pkts_dropped):>14d}")
    print(f"  {'─'*72}")

    print(f"\n  {YELLOW}ECMP VOQ Collision Analysis:{RESET}")
    print(f"    Each input port independently round-robins across 8 egress ports.")
    print(f"    'independent': 128 uncoordinated RR pointers → collisions")
    print(f"    'coordinated': 1 global RR per dest NPU → no collision (ideal)")
    print(f"")
    print(f"    {'Egress port load (per dest NPU)':40s} {'Independent':>14s} {'Coordinated':>14s}")
    print(f"    {'  Min enqueued':40s} {li_min:>14.0f} {lc_min:>14.0f}")
    print(f"    {'  Avg enqueued':40s} {li_avg:>14.0f} {lc_avg:>14.0f}")
    print(f"    {'  Max enqueued':40s} {li_max:>14.0f} {lc_max:>14.0f}")
    if li_avg > 0:
        print(f"    {'  Max/Avg ratio (imbalance)':40s} {li_max/li_avg:>14.2f}x {lc_max/lc_avg:>14.2f}x")
    print(f"")
    print(f"    VOQ collision causes the {'independent':s} mode to have")
    if si['p99'] > sc['p99']:
        print(f"    {RED}higher P99 latency: {si['p99']} vs {sc['p99']} cycles{RESET}")
    else:
        print(f"    similar latency (collision effect minimal at this load level)")
    print()


if __name__ == "__main__":
    main()
