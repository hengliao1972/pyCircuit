# Janus BCC Benchmark Report

Workloads:
- CoreMark-lite proxy ops: 4096
- Dhrystone-lite proxy iterations: 4096
- DMIPS assumes 1 MHz cycle clock for normalization.

| Mode | Core Cycles | CoreMark/MHz (proxy) | Dhrystone Cycles | DMIPS (proxy) |
| --- | ---: | ---: | ---: | ---: |
| C++ model | 4102 | 0.998537 | 4103 | 568.180951 |
| Verilog model (Verilator) | 4102 | 0.998537 | 4103 | 568.180951 |
