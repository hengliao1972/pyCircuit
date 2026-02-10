# Open-source Verilog dev/verification flow

pyCircuit emits **plain Verilog** for designs, plus a few small **SystemVerilog** testbenches under `examples/`.
This repo supports an open-source workflow using:

- **Icarus Verilog** (`iverilog` + `vvp`) for quick RTL simulation
- **Verilator** for lint and (optionally) simulation of larger designs
- **GTKWave** for waveform viewing

All debug artifacts (VCD + logs + Verilator build dirs) are written under `examples/generated/`.

---

## 1) Install tools

### macOS (Homebrew)

```bash
brew install icarus-verilog verilator gtkwave
```

### Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install -y iverilog verilator gtkwave
```

---

## 2) Quickstart: run with the universal Python runner

First, check tool availability:

```bash
python3 tools/pyc_flow.py doctor
```

Regenerate the checked-in outputs (requires `pyc-compile`):

```bash
python3 tools/pyc_flow.py regen
```

To regenerate FastFwd with a different number of Forwarding Engines (FEs), override the JIT param through the runner:

```bash
# Example: 8 FEs total (must be a multiple of 4, <= 32)
python3 tools/pyc_flow.py regen --examples --fastfwd-nfe 8
```

Run Verilog simulations:

```bash
# FastFwd (exam-style wrapper + internal FE stub)
python3 tools/pyc_flow.py verilog-sim fastfwd_pyc +max_cycles=500 +max_pkts=1000 +seed=1

# Issue queue (2 pickers)
python3 tools/pyc_flow.py verilog-sim issue_queue_2picker
```

Note (FastFwd FE count):
- `FastFwd` exposes one FEIN/FEOUT port-set per engine (`fwded0..N-1`, `fwd0..N-1`).
- The exam-style wrapper `examples/generated/fastfwd_pyc/exam2021_top.v` instantiates exactly `N_FE` engines to match the core.
- If you change the engine count (e.g. via JIT param `ENG_PER_LANE`), regenerate both the core and wrapper; leaving any engine unconnected will stall (“no output”).

Cross-check the **C++ tick model vs Verilog** using identical stimulus (writes traces under `examples/generated/fastfwd_pyc/crosscheck/`):

```bash
python3 tools/pyc_flow.py fastfwd-crosscheck --tool iverilog --seed 1 --cycles 200 --packets 400
```

Run a CPU Verilog simulation (recommended: Verilator, Icarus is slow due to the 1MB byte memory array):

```bash
python3 tools/pyc_flow.py verilog-sim linx_cpu_pyc --tool verilator \
  +memh=examples/linx_cpu/programs/test_or.memh +expected=0000ff00
```

Lint generated Verilog:

```bash
python3 tools/pyc_flow.py verilog-lint fastfwd_pyc
python3 tools/pyc_flow.py verilog-lint issue_queue_2picker
python3 tools/pyc_flow.py verilog-lint linx_cpu_pyc
```

Open waveforms:

```bash
python3 tools/pyc_flow.py wave examples/generated/fastfwd_pyc/tb_fastfwd_pyc_sv.vcd
```

---

## 3) Where wave/log outputs go

Default output locations (override via plusargs shown below):

- FastFwd: `examples/generated/fastfwd_pyc/`
  - `tb_fastfwd_pyc_sv.vcd`
  - `tb_fastfwd_pyc_sv.log`
- Issue queue TB: `examples/generated/tb_issue_queue_2picker/`
  - `tb_issue_queue_2picker_sv.vcd`
  - `tb_issue_queue_2picker_sv.log`
- Linx CPU TB: `examples/generated/linx_cpu_pyc/`
  - `tb_linx_cpu_pyc_sv.vcd`
  - `tb_linx_cpu_pyc_sv.log`

Common knobs:

- Disable VCD dump: `+notrace`
- Disable log file: `+nolog`
- Override paths: `+vcd=/path/to/out.vcd +log=/path/to/out.log`

---

## 4) Notes on compiling generated Verilog manually

Generated Verilog includes primitives via backtick includes like:

```verilog
`include "pyc_reg.v"
```

So simulators must be invoked with an include path:

- Icarus: `-I include/pyc/verilog`
- Verilator: `-Iinclude/pyc/verilog`

The `tools/pyc_flow.py` runner adds these automatically.
