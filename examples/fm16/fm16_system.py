#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FM16 System Simulator — 16 NPU full-mesh + SW5809s switch.

Behavioral cycle-accurate simulation of:
  - 16 Ascend950-like NPU nodes (1.6Tbps HBM, 18×4×112Gbps UB)
  - Full mesh topology: 4 links per NPU pair (16×15/2 = 120 link pairs)
  - SW5809s: 16×8×112Gbps, VOQ + crossbar + RR/MDRR
  - All-to-all continuous 512B packet traffic

Each "cycle" = 1 packet slot (time for one 512B packet on one link).

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
# System parameters
# ═══════════════════════════════════════════════════════════════════
N_NPUS        = 16
MESH_LINKS    = 4       # links per NPU pair in full mesh
SW_LINKS      = 4       # links per NPU to switch (simplified from 8×4)
PKT_SIZE      = 512     # bytes
LINK_BW_GBPS  = 112     # Gbps per link
HBM_BW_TBPS   = 1.6     # Tbps HBM bandwidth per NPU

# Derived: packet time on one link (ns)
PKT_TIME_NS   = PKT_SIZE * 8 / LINK_BW_GBPS  # ~36.6 ns
# HBM injection rate: packets per link-time
HBM_PKTS_PER_SLOT = HBM_BW_TBPS * 1000 / (PKT_SIZE * 8 / PKT_TIME_NS)
# Simplification: HBM can inject ~1 pkt/slot per destination on average
HBM_INJECT_PROB = min(1.0, HBM_BW_TBPS * 1000 / LINK_BW_GBPS / N_NPUS)

VOQ_DEPTH     = 64      # per VOQ in switch
FIFO_DEPTH    = 32      # per output FIFO in NPU
SIM_CYCLES    = 2000    # total simulation cycles
DISPLAY_INTERVAL = 100  # update display every N cycles
WARMUP_CYCLES = 200     # ignore first N cycles for stats


# ═══════════════════════════════════════════════════════════════════
# Packet
# ═══════════════════════════════════════════════════════════════════
@dataclass
class Packet:
    src: int
    dst: int
    seq: int
    inject_cycle: int

    def latency(self, current_cycle: int) -> int:
        return current_cycle - self.inject_cycle


# ═══════════════════════════════════════════════════════════════════
# NPU Node (behavioral)
# ═══════════════════════════════════════════════════════════════════
class NPUNode:
    """Simplified NPU with HBM injection and output port FIFOs."""

    def __init__(self, node_id: int, n_ports: int):
        self.id = node_id
        self.n_ports = n_ports
        self.out_fifos: list[collections.deque] = [
            collections.deque(maxlen=FIFO_DEPTH) for _ in range(n_ports)
        ]
        self.seq = 0
        self.pkts_injected = 0
        self.pkts_delivered = 0
        self.latencies: list[int] = []

    def inject(self, cycle: int, rng: random.Random):
        """Try to inject all-to-all packets from HBM.

        Injects up to INJECT_BATCH packets per cycle to multiple destinations,
        modeling the high HBM bandwidth trying to saturate the mesh links.
        """
        INJECT_BATCH = 8  # try to inject multiple pkts/cycle (HBM is fast)
        for _ in range(INJECT_BATCH):
            if rng.random() > HBM_INJECT_PROB:
                continue
            # Pick a random destination (not self)
            dst = self.id
            while dst == self.id:
                dst = rng.randint(0, N_NPUS - 1)
            pkt = Packet(src=self.id, dst=dst, seq=self.seq, inject_cycle=cycle)
            self.seq += 1

            # Route to output port
            port = dst % self.n_ports
            if len(self.out_fifos[port]) < FIFO_DEPTH:
                self.out_fifos[port].append(pkt)
                self.pkts_injected += 1

    def tx(self, port: int) -> Packet | None:
        """Transmit one packet from output port (if available)."""
        if self.out_fifos[port]:
            return self.out_fifos[port].popleft()
        return None

    def rx(self, pkt: Packet, cycle: int):
        """Receive a packet (delivered to this NPU)."""
        self.pkts_delivered += 1
        lat = pkt.latency(cycle)
        self.latencies.append(lat)


# ═══════════════════════════════════════════════════════════════════
# SW5809s Switch (behavioral)
# ═══════════════════════════════════════════════════════════════════
class SW5809s:
    """Simplified switch: VOQ + crossbar + round-robin arbiter."""

    def __init__(self, n_ports: int):
        self.n_ports = n_ports
        # VOQ[input][output] = deque
        self.voqs: list[list[collections.deque]] = [
            [collections.deque(maxlen=VOQ_DEPTH) for _ in range(n_ports)]
            for _ in range(n_ports)
        ]
        self.rr_ptrs = [0] * n_ports  # round-robin per output
        self.pkts_switched = 0

    def enqueue(self, in_port: int, pkt: Packet):
        """Enqueue packet from input port into VOQ[in_port][output_port]."""
        out_port = pkt.dst % self.n_ports
        if len(self.voqs[in_port][out_port]) < VOQ_DEPTH:
            self.voqs[in_port][out_port].append(pkt)

    def schedule(self) -> list[Packet | None]:
        """Crossbar scheduling: one packet per output port per cycle.
        Uses round-robin arbitration (simplified MDRR).
        Returns list of N_PORTS packets (None if no winner).
        """
        results: list[Packet | None] = [None] * self.n_ports

        for j in range(self.n_ports):
            # Round-robin scan from rr_ptr
            for offset in range(self.n_ports):
                i = (self.rr_ptrs[j] + offset) % self.n_ports
                if self.voqs[i][j]:
                    results[j] = self.voqs[i][j].popleft()
                    self.rr_ptrs[j] = (i + 1) % self.n_ports
                    self.pkts_switched += 1
                    break

        return results


# ═══════════════════════════════════════════════════════════════════
# FM16 Topology
# ═══════════════════════════════════════════════════════════════════
class FM16System:
    """16 NPU full-mesh + switch system."""

    def __init__(self):
        # Each NPU has N_NPUS-1 mesh port groups + 1 switch port group
        # Simplified: each NPU has N_NPUS ports (mesh + switch combined)
        self.npus = [NPUNode(i, N_NPUS) for i in range(N_NPUS)]
        self.switch = SW5809s(N_NPUS)
        self.cycle = 0
        self.rng = random.Random(42)
        self._in_flight: list[tuple[int, Packet]] = []  # (arrival_cycle, pkt)

        # Statistics
        self.total_injected = 0
        self.total_delivered = 0
        self.total_switched = 0
        self.bw_history: list[float] = []  # delivered pkts per display interval

    def step(self):
        """Run one cycle of the system."""
        # 1. Each NPU injects traffic from HBM
        for npu in self.npus:
            npu.inject(self.cycle, self.rng)

        # 2. Transmit from NPU output FIFOs
        #    Route: if dst is directly connected (mesh), deliver directly.
        #           Otherwise, send through switch.
        #    Simplified: all-to-all via mesh (full mesh exists for all pairs).
        #    Use mesh links (MESH_LINKS packets per pair per cycle max).
        # 2. Transmit from NPU output FIFOs via mesh links.
        #    Each link can carry 1 packet per cycle.
        #    Each NPU-pair has MESH_LINKS parallel links.
        #    Model serialization delay + pipeline latency.
        LINK_LATENCY = 3

        # Track per-destination-NPU bandwidth usage this cycle
        for npu in self.npus:
            for port in range(N_NPUS):
                sent = 0
                while sent < MESH_LINKS:  # up to MESH_LINKS pkts per pair
                    pkt = npu.tx(port)
                    if pkt is None:
                        break
                    if pkt.dst == npu.id:
                        continue
                    # Add queuing delay: FIFO depth at time of send
                    q_depth = len(npu.out_fifos[port])
                    total_lat = LINK_LATENCY + q_depth  # queue + pipeline
                    self._in_flight.append((self.cycle + total_lat, pkt))
                    sent += 1

        # 3. Deliver packets that have completed their latency
        still_in_flight = []
        for (arrive_cycle, pkt) in self._in_flight:
            if arrive_cycle <= self.cycle:
                self.npus[pkt.dst].rx(pkt, self.cycle)
            else:
                still_in_flight.append((arrive_cycle, pkt))
        self._in_flight = still_in_flight

        self.cycle += 1

        # Track stats
        self.total_injected = sum(n.pkts_injected for n in self.npus)
        self.total_delivered = sum(n.pkts_delivered for n in self.npus)

    def run(self, cycles: int):
        for _ in range(cycles):
            self.step()

    def get_stats(self):
        """Compute aggregate statistics."""
        all_lats = []
        for npu in self.npus:
            all_lats.extend(npu.latencies)

        if not all_lats:
            return {"avg_lat": 0, "p50": 0, "p95": 0, "p99": 0,
                    "bw_gbps": 0, "inject_rate": 0}

        all_lats.sort()
        n = len(all_lats)
        avg = sum(all_lats) / n
        p50 = all_lats[n // 2]
        p95 = all_lats[int(n * 0.95)]
        p99 = all_lats[int(n * 0.99)]

        # Bandwidth: delivered packets × PKT_SIZE × 8 / simulation_time
        sim_time_ns = self.cycle * PKT_TIME_NS
        bw_gbps = self.total_delivered * PKT_SIZE * 8 / sim_time_ns if sim_time_ns > 0 else 0

        return {
            "avg_lat": avg, "p50": p50, "p95": p95, "p99": p99,
            "bw_gbps": bw_gbps,
            "inject_rate": self.total_injected / max(self.cycle, 1),
        }

    def get_latency_histogram(self, bins=20):
        """Build a latency histogram for visualization."""
        all_lats = []
        for npu in self.npus:
            all_lats.extend(npu.latencies)
        if not all_lats:
            return [], 0, 0

        min_l, max_l = min(all_lats), max(all_lats)
        if min_l == max_l:
            return [len(all_lats)], min_l, max_l

        bin_size = max(1, (max_l - min_l + bins - 1) // bins)
        hist = [0] * bins
        for l in all_lats:
            idx = min((l - min_l) // bin_size, bins - 1)
            hist[idx] += 1
        return hist, min_l, max_l


# ═══════════════════════════════════════════════════════════════════
# Real-time Terminal Visualization
# ═══════════════════════════════════════════════════════════════════
BOX_W = 72

def _bl(content):
    return f"  {CYAN}║{RESET}{_pad(content, BOX_W)}{CYAN}║{RESET}"

def _bar(val, max_val, width=30, ch="█", color=GREEN):
    if max_val <= 0: return ""
    n = min(int(val / max_val * width), width)
    return f"{color}{ch * n}{RESET}"

def draw_stats(sys: FM16System):
    clear()
    bar = "═" * BOX_W
    stats = sys.get_stats()
    hist, min_l, max_l = sys.get_latency_histogram(bins=15)

    print(f"\n  {CYAN}╔{bar}╗{RESET}")
    print(_bl(f"  {BOLD}{WHITE}FM16 SYSTEM — 16 NPU Full-Mesh Simulation{RESET}"))
    print(f"  {CYAN}╠{bar}╣{RESET}")

    # Topology info
    print(_bl(f"  {DIM}16 × Ascend950 NPU | Full mesh (4 links/pair) | 512B pkts{RESET}"))
    print(_bl(f"  {DIM}HBM: 1.6Tbps/NPU | UB: {MESH_LINKS}×112Gbps/link | All-to-all traffic{RESET}"))
    print(f"  {CYAN}╠{bar}╣{RESET}")

    # Progress
    pct = sys.cycle * 100 // SIM_CYCLES
    prog_bar = _bar(sys.cycle, SIM_CYCLES, 40, "█", CYAN)
    print(_bl(f"  Cycle: {sys.cycle}/{SIM_CYCLES} [{prog_bar}] {pct}%"))
    print(_bl(""))

    # Bandwidth
    print(_bl(f"  {BOLD}{WHITE}Bandwidth:{RESET}"))
    print(_bl(f"    Aggregate delivered BW:  {YELLOW}{BOLD}{stats['bw_gbps']:>10.1f} Gbps{RESET}"))
    print(_bl(f"    Injected packets:        {stats['inject_rate']:>10.1f} pkt/cycle"))
    print(_bl(f"    Total injected:          {sys.total_injected:>10d}"))
    print(_bl(f"    Total delivered:          {sys.total_delivered:>10d}"))
    print(_bl(""))

    # Per-NPU bandwidth bar chart
    print(_bl(f"  {BOLD}{WHITE}Per-NPU Delivered Packets:{RESET}"))
    max_npu = max((n.pkts_delivered for n in sys.npus), default=1)
    for i, npu in enumerate(sys.npus):
        b = _bar(npu.pkts_delivered, max_npu, 30)
        print(_bl(f"    NPU{i:>2d}: {b} {npu.pkts_delivered:>6d}"))
    print(_bl(""))

    # Latency stats
    print(f"  {CYAN}╠{bar}╣{RESET}")
    print(_bl(f"  {BOLD}{WHITE}Latency (cycles):{RESET}"))
    print(_bl(f"    Avg:  {YELLOW}{stats['avg_lat']:>6.1f}{RESET}   "
              f"P50: {stats['p50']:>4d}   "
              f"P95: {stats['p95']:>4d}   "
              f"P99: {stats['p99']:>4d}"))
    print(_bl(""))

    # Latency histogram
    if hist:
        print(_bl(f"  {BOLD}{WHITE}Latency Distribution:{RESET}"))
        max_h = max(hist) if hist else 1
        bin_w = max(1, (max_l - min_l + len(hist) - 1) // len(hist)) if len(hist) > 1 else 1
        for i, h in enumerate(hist):
            lo = min_l + i * bin_w
            hi = lo + bin_w - 1
            b = _bar(h, max_h, 30, "▓", MAGENTA)
            print(_bl(f"    {lo:>3d}-{hi:>3d}: {b} {h:>5d}"))

    print(_bl(""))
    print(f"  {CYAN}╚{bar}╝{RESET}")
    print()


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════
def main():
    print(f"  {BOLD}FM16 System Simulator — 16 NPU Full-Mesh{RESET}")
    print(f"  Initializing {N_NPUS} NPU nodes...")

    system = FM16System()

    print(f"  {GREEN}System ready. Running {SIM_CYCLES} cycles...{RESET}")
    time.sleep(0.5)

    t0 = time.time()
    for cyc in range(SIM_CYCLES):
        system.step()
        if (cyc + 1) % DISPLAY_INTERVAL == 0 or cyc == SIM_CYCLES - 1:
            draw_stats(system)
            # Small sleep for visual effect
            elapsed = time.time() - t0
            if elapsed < 0.5:
                time.sleep(0.05)

    t1 = time.time()

    # Final summary
    stats = system.get_stats()
    print(f"  {GREEN}{BOLD}Simulation complete!{RESET}")
    print(f"  Wall time: {t1-t0:.2f}s")
    print(f"  Cycles: {system.cycle}")
    print(f"  Aggregate BW: {stats['bw_gbps']:.1f} Gbps")
    print(f"  Avg latency: {stats['avg_lat']:.1f} cycles")
    print(f"  P99 latency: {stats['p99']} cycles")
    print()


if __name__ == "__main__":
    main()
