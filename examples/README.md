# Examples

Emit `.pyc` (MLIR) from Python:

```bash
PYTHONPATH=../python python3 -m pycircuit.cli emit counter.py -o /tmp/counter.pyc
PYTHONPATH=../python python3 -m pycircuit.cli emit fifo_loopback.py -o /tmp/fifo_loopback.pyc
PYTHONPATH=../python python3 -m pycircuit.cli emit multiclock_regs.py -o /tmp/multiclock_regs.pyc
PYTHONPATH=../python python3 -m pycircuit.cli emit wire_ops.py -o /tmp/wire_ops.pyc
PYTHONPATH=../python python3 -m pycircuit.cli emit jit_control_flow.py -o /tmp/jit_control_flow.pyc
PYTHONPATH=../python python3 -m pycircuit.cli emit jit_pipeline_vec.py -o /tmp/jit_pipeline_vec.pyc
PYTHONPATH=../python python3 -m pycircuit.cli emit jit_cache.py -o /tmp/jit_cache.pyc
PYTHONPATH=../python python3 -m pycircuit.cli emit fastfwd_pyc/fastfwd_pyc.py -o /tmp/fastfwd_pyc.pyc
```

Then compile to Verilog:

```bash
../build/bin/pyc-compile /tmp/counter.pyc --emit=verilog -o /tmp/counter.v
```

## Verilog simulation (open-source)

See `docs/VERILOG_FLOW.md`, or run directly:

```bash
python3 ../tools/pyc_flow.py verilog-sim fastfwd_pyc +max_cycles=500 +max_pkts=1000 +seed=1
python3 ../tools/pyc_flow.py verilog-sim issue_queue_2picker
python3 ../tools/pyc_flow.py verilog-sim linx_cpu_pyc --tool verilator \
  +memh=examples/linx_cpu/programs/test_or.memh +expected=0000ff00
```

## Checked-in generated outputs

This repo checks in generated outputs under `examples/generated/`:

```bash
bash update_generated.sh          # examples only
(cd .. && scripts/pyc regen)      # all goldens (examples + janus)
```

### FastFwd (exam-style top)

`examples/generated/fastfwd_pyc/` includes:

- `fastfwd_pyc.v`: `FastFwd` core netlist (has FEIN/FEOUT ports)
- `exam2021_top.v`: `EXAM2021_TOP` wrapper that instantiates `FE` internally and exposes only PKTIN/PKTOUT/BKPR
- `fe.v`: a small FE stub model (replace with the official exam `fe.v` / `fe.v.e` RTL when integrating)

Important:
- The number of instantiated `FE` blocks **must match** the `FastFwd` core’s FE port count (`fwded0..N-1` / `fwd0..N-1`).
- Don’t hand-edit `N_FE` unless you also regenerate the core with the same engine count (otherwise the design will stall waiting for missing completions and you’ll see “no output”).
- `bash examples/update_generated.sh` auto-detects the FE count in `fastfwd_pyc.v` and regenerates a matching `exam2021_top.v`.

To change the FE count:

```bash
# Example: build FastFwd with 8 FEs (4 lanes × 2 engines/lane)
FASTFWD_N_FE=8 bash examples/update_generated.sh
```

## Debug traces

- C++ CPU TB (`examples/linx_cpu_pyc/tb_linx_cpu_pyc.cpp`):
  - `PYC_TRACE=1` writes a commit log under `examples/generated/linx_cpu_pyc/`.
  - `PYC_VCD=1` writes a VCD waveform under `examples/generated/linx_cpu_pyc/`.
  - `PYC_KONATA=1` writes a Konata trace (`*.konata`) under `examples/generated/linx_cpu_pyc/` for viewing in Konata.
  - Optional: set `PYC_TRACE_DIR=/path/to/dir` to override the output directory.
- C++ FastFwd TB (`examples/fastfwd_pyc/tb_fastfwd_pyc.cpp`):
  - `PYC_TRACE=1` writes a text log under `examples/generated/fastfwd_pyc/`.
  - `PYC_VCD=1` writes a VCD waveform under `examples/generated/fastfwd_pyc/`.
  - `PYC_KONATA=1` writes a Konata trace (`*.konata`) under `examples/generated/fastfwd_pyc/` for viewing in Konata.
  - Optional: set `PYC_TRACE_DIR=/path/to/dir` to override the output directory.
- C++ FIFO TB (`examples/cpp/tb_fifo.cpp`) and issue-queue TB (`examples/cpp/tb_issue_queue_2picker.cpp`):
  - Write `*.log` and `*.vcd` under `examples/generated/tb_fifo/` and `examples/generated/tb_issue_queue_2picker/`.
  - Optional: set `PYC_TRACE_DIR=/path/to/dir` to override the output directory.
- SystemVerilog CPU TB (`examples/linx_cpu/tb_linx_cpu_pyc.sv`):
  - Dumps to `examples/generated/linx_cpu_pyc/` by default.
  - Disable with `+notrace` (VCD) and/or `+nolog` (log). Add `+logcycles` to log per-cycle CSV rows.
- SystemVerilog issue-queue TB (`examples/issue_queue_2picker/tb_issue_queue_2picker.sv`):
  - Dumps to `examples/generated/tb_issue_queue_2picker/` by default (override with `+trace_dir=<path>`).
  - Disable with `+notrace` (VCD) and/or `+nolog` (log). Add `+logcycles` to log per-cycle CSV rows.
