# FM16 — 16-NPU Full-Mesh System Simulation

Cycle-accurate simulation of a 16-chip Ascend950-like NPU cluster with
full-mesh interconnect topology.

## System Architecture

```
       NPU0 ──4 links── NPU1 ──4 links── NPU2 ...
        │╲                │╲
        │  ╲  full mesh   │  ╲
        │    ╲  (4 links  │    ╲
        │      ╲ per pair)│      ╲
       NPU3 ──────────── NPU4 ...     (16 NPUs total)
```

### NPU Node (Ascend950 simplified)
- **HBM**: 1.6 Tbps bandwidth (packet injection)
- **UB Ports**: 18×4×112 Gbps (simplified to N mesh ports)
- Routing: destination-based (dst → output port mapping)
- Output FIFOs per port with round-robin arbitration

### SW5809s Switch (simplified)
- 16×8×112 Gbps ports
- VOQ (Virtual Output Queue) per (input, output) pair
- Crossbar with round-robin / MDRR scheduling

### Packet Format
- 512 bytes per packet
- 32-bit descriptor: src[4] | dst[4] | seq[8] | tag[16]

## Topology
- **Full mesh**: 4 links per NPU pair (16×15/2 = 120 bidirectional pairs)
- **All-to-all traffic**: each NPU continuously sends to all other NPUs

## Files

| File | Description |
|------|-------------|
| `npu_node.py` | pyCircuit RTL of single NPU (compile-verified) |
| `sw5809s.py` | pyCircuit RTL of switch (compile-verified) |
| `fm16_system.py` | Python behavioral system simulator with real-time visualization |

## Run

```bash
python examples/fm16/fm16_system.py
```

## Statistics
- Per-NPU delivered bandwidth (bar chart)
- Aggregate system bandwidth (Gbps)
- Latency distribution: avg, P50, P95, P99
- Histogram visualization
