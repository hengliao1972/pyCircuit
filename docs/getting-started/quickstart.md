# Quickstart Guide (pyc4.0)

This guide walks through building the compiler and running a first design using
the pyCircuit v4.0 (`pyc0.40`) module/testbench flow.

## 1) Build `pycc`

```bash
bash flows/scripts/pyc build
```

This stages the toolchain under `.pycircuit_out/toolchain/install/` by default.

## 2) Run the example smoke gate

Compiler smoke (`emit + pycc`):

```bash
bash flows/scripts/run_examples.sh
```

Simulation smoke (`@testbench`, C++ + Verilator):

```bash
bash flows/scripts/run_sims.sh
```

## 3) Your first design: counter

The repo includes a minimal counter example:

- Design: `designs/examples/counter/counter.py`
- Testbench: `designs/examples/counter/tb_counter.py`

Design excerpt:

```python
from pycircuit import Circuit, module, u

@module
def build(m: Circuit, width: int = 8) -> None:
    clk = m.clock("clk")
    rst = m.reset("rst")
    en = m.input("enable", width=1)

    count = m.out("count_q", clk=clk, rst=rst, width=width, init=u(width, 0))
    count.set(count.out() + 1, when=en)
    m.output("count", count)
```

## 4) Minimal manual flow (`pycircuit.cli`)

Build a multi-module project (device + TB) into an output directory:

```bash
PYTHONPATH=compiler/frontend \
PYC_TOOLCHAIN_ROOT=.pycircuit_out/toolchain/install \
python3 -m pycircuit.cli build \
  designs/examples/counter/tb_counter.py \
  --out-dir /tmp/pyc_counter \
  --target both \
  --jobs 8
```

For more end-to-end commands (including direct `emit` and `pycc --emit=cpp`),
see `docs/QUICKSTART.md`.

## 5) Debugging compiler passes

When a backend gate fails (e.g. `pyc-check-comb-cycles`,
`pyc-check-logic-depth`, `pyc-check-flat-types`) or you need to see which pass
changed the IR, dump per-pass IR to a directory and diff any pass pair:

```bash
pycc /tmp/counter.pyc --emit=none --dump-pass-ir=/tmp/pir
diff /tmp/pir/*_before_*eliminate-wires*.mlir /tmp/pir/*_after_*eliminate-wires*.mlir
```

See `docs/mlir_pass_ir_dump.md` for the full flag set and
`docs/DIAGNOSTICS.md` for the general diagnostic model.
