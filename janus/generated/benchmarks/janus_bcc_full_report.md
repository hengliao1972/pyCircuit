# Janus BCC Full Benchmark Report

Workloads:
- CoreMark iterations: 1
- Dhrystone runs: 1000
- DMIPS assumes 1 MHz cycle clock for normalization.

| Mode | CoreMark Cycles | CoreMark/MHz | Dhrystone Cycles | DMIPS |
| --- | ---: | ---: | ---: | ---: |
| C++ model | 1585 | 0.000631 | 122 | 4665.180029 |
| Verilog model (Verilator) | 1585 | 0.000631 | 122 | 4665.180029 |

## Notes
- Full ELF workloads reach the configured __linx_exit loop termination.
- Dhrystone currently exits very early compared with reference QEMU instruction count; DMIPS is therefore not yet a validated architectural performance number.
