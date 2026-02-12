# -*- coding: utf-8 -*-
"""Simplified NPU node — pyCircuit RTL.

Models a single NPU chip with:
  - HBM injection port (1 packet/cycle max, rate-limited)
  - N_PORTS bidirectional UB ports (for mesh + switch connections)
  - Output FIFOs per port (depth FIFO_DEPTH)
  - Destination-based routing (dst → port map via modulo)
  - Round-robin output arbiter

Packet format (32 bits):
  [31:28] src    — source NPU ID (0-15)
  [27:24] dst    — destination NPU ID (0-15)
  [23:16] seq    — sequence number
  [15:0]  tag    — payload tag / timestamp

Ports:
  Inputs:
    hbm_pkt[31:0], hbm_valid           — HBM injection
    rx_pkt_0..N-1[31:0], rx_valid_0..N-1 — receive from network
  Outputs:
    tx_pkt_0..N-1[31:0], tx_valid_0..N-1 — transmit to network
    hbm_ready                            — backpressure to HBM
"""
from __future__ import annotations

import sys
from pathlib import Path

from pycircuit import (
    CycleAwareCircuit, CycleAwareDomain, CycleAwareSignal,
    compile_cycle_aware, mux,
)

PKT_W = 32  # packet descriptor width


def _npu_impl(m, domain, N_PORTS, FIFO_DEPTH, NODE_ID):
    c = lambda v, w: domain.const(v, width=w)

    # ═══════════ Inputs ═══════════
    hbm_pkt   = domain.input("hbm_pkt",   width=PKT_W)
    hbm_valid = domain.input("hbm_valid",  width=1)

    rx_pkts  = [domain.input(f"rx_pkt_{i}",   width=PKT_W) for i in range(N_PORTS)]
    rx_vals  = [domain.input(f"rx_valid_{i}",  width=1)      for i in range(N_PORTS)]

    # ═══════════ Output FIFOs (one per port) ═══════════
    fifos = []
    for i in range(N_PORTS):
        q = m.ca_queue(f"oq_{i}", domain=domain, width=PKT_W, depth=FIFO_DEPTH)
        fifos.append(q)

    # ═══════════ Routing: dst → output port ═══════════
    # Simple modulo routing: port = dst % N_PORTS
    PORT_BITS = max((N_PORTS - 1).bit_length(), 1)
    hbm_dst = hbm_pkt[24:28]  # dst field [27:24]
    hbm_port = hbm_dst.trunc(width=PORT_BITS)  # dst % N_PORTS (works when N_PORTS is power of 2)

    # ═══════════ HBM injection → output FIFO ═══════════
    # Push HBM packet into the target port's FIFO
    for i in range(N_PORTS):
        port_match = hbm_port.eq(c(i, PORT_BITS))
        push_cond = hbm_valid & port_match
        fifos[i].push(hbm_pkt, when=push_cond)

    # ═══════════ Receive ports → forward (store-and-forward) ═══════════
    # Received packets are also routed to output FIFOs
    for i in range(N_PORTS):
        rx_dst = rx_pkts[i][24:28]
        rx_port = rx_dst.trunc(width=PORT_BITS)
        for j in range(N_PORTS):
            fwd_match = rx_port.eq(c(j, PORT_BITS)) & rx_vals[i]
            fifos[j].push(rx_pkts[i], when=fwd_match)

    # ═══════════ Output: pop from FIFOs ═══════════
    # Always pop if data available (no backpressure for simplicity)
    tx_pkts = []
    tx_vals = []
    for i in range(N_PORTS):
        pop_result = fifos[i].pop(when=c(1, 1))  # always ready to pop
        tx_pkts.append(pop_result.data)
        tx_vals.append(pop_result.valid)

    # ═══════════ HBM backpressure ═══════════
    # Ready if the target FIFO is not full (simplified: always ready)
    hbm_ready_sig = c(1, 1)

    # ═══════════ Outputs ═══════════
    for i in range(N_PORTS):
        m.output(f"tx_pkt_{i}",   tx_pkts[i])
        m.output(f"tx_valid_{i}", tx_vals[i])
    m.output("hbm_ready", hbm_ready_sig)


def npu_node(m: CycleAwareCircuit, domain: CycleAwareDomain,
             N_PORTS: int = 4, FIFO_DEPTH: int = 8, NODE_ID: int = 0) -> None:
    _npu_impl(m, domain, N_PORTS, FIFO_DEPTH, NODE_ID)


def build():
    return compile_cycle_aware(npu_node, name="npu_node",
                               N_PORTS=4, FIFO_DEPTH=8, NODE_ID=0)


if __name__ == "__main__":
    circuit = build()
    print(circuit.emit_mlir()[:500])
    print(f"... ({len(circuit.emit_mlir())} chars)")
