# Janus (pyCircuit)

This folder contains **Janus bring-up cores** written in **pyCircuit**:

`Python` → `*.pyc` (MLIR) → `pyc-compile` → `Verilog` / `C++`

Layout:

- Sources: `janus/pyc/janus/bcc/`
- Generated outputs (checked in): `janus/generated/`
- C++ testbenches: `janus/tb/`
- Program fixtures: `janus/programs/*.memh`

## Janus Structure (pyCircuit source map)

Requested Janus hierarchy is implemented under `janus/pyc/janus/`:

- `top.py` (compiles to `top.pyc`)
- `bcc/ifu/{f0,f1,f2,f3,f4,icache,ctrl}.py`
- `bcc/ooo/{dec1,dec2,ren,s1,s2,rob,pc_buffer,flush_ctrl,renu}.py`
- `bcc/iex/{iex,iex_alu,iex_bru,iex_fsu,iex_agu,iex_std}.py`
- `bcc/bctrl/{bctrl,bisq,brenu,brob}.py`
- `bcc/lsu/{liq,lhq,stq,scb,l1d}.py`
- `tmu/noc/{node,pipe}.py`
- `tmu/sram/tilereg.py`
- `tma/tma.py`
- `cube/cube.py`
- `tau/tau.py`

`*.py` files are pyCircuit source modules; `pycircuit.cli emit` generates `*.pyc` MLIR.

## Benchmark Bring-up

Bring-up benchmark scripts:

- `bash janus/tools/run_janus_benchmarks.sh`
  - Builds `coremark_lite.S` and `dhrystone_lite.S`
  - Runs BCC in:
    - C++ model (`run_janus_bcc_ooo_pyc_cpp.sh`)
    - Verilog model via Verilator (`run_janus_bcc_ooo_pyc_verilator.sh`)
  - Writes report to `janus/generated/benchmarks/janus_bcc_report.md`

## Quickstart (C++ regressions)

From the repo root:

```bash
scripts/pyc build
scripts/pyc regen
scripts/pyc test
```

Or run individually:

```bash
bash janus/tools/run_janus_bcc_pyc_cpp.sh
bash janus/tools/run_janus_bcc_ooo_pyc_cpp.sh
```

## Emit `.pyc` / Verilog

```bash
PYTHONPATH=python:janus/pyc python3 -m pycircuit.cli emit janus/pyc/janus/bcc/janus_bcc_pyc.py -o /tmp/janus_bcc_pyc.pyc
./build/bin/pyc-compile /tmp/janus_bcc_pyc.pyc --emit=verilog -o /tmp/janus_bcc_pyc.v
```

## Tracing

Both C++ testbenches support:

- `PYC_TRACE=1` (log file)
- `PYC_VCD=1` (VCD waveform)
- `PYC_KONATA=1` (Konata pipeview trace, `*.konata`)
- `PYC_TRACE_DIR=/path/to/out` (override output dir; default is under `janus/generated/`)

`bash janus/tools/run_janus_bcc_pyc_cpp.sh` and `bash janus/tools/run_janus_bcc_ooo_pyc_cpp.sh` enable `PYC_KONATA=1` by default (disable with `PYC_KONATA=0`).
