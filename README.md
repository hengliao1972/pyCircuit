# pyCircuit (pyc4.0 / pyc0.40)

<p align="center">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/Python-3.10%2B-green.svg" alt="Python">
  <img src="https://img.shields.io/badge/MLIR-19-orange.svg" alt="MLIR">
  <a href="https://github.com/LinxISA/pyCircuit/actions"><img src="https://github.com/LinxISA/pyCircuit/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/LinxISA/pyCircuit/actions/workflows/release.yml"><img src="https://github.com/LinxISA/pyCircuit/actions/workflows/release.yml/badge.svg" alt="Release"></a>
  <a href="https://github.com/LinxISA/pyCircuit/releases"><img src="https://img.shields.io/github/v/release/LinxISA/pyCircuit?display_name=tag" alt="Latest Release"></a>
  <img src="https://img.shields.io/badge/PyPI-pycircuit--hisi-blue.svg" alt="PyPI Package">
</p>

pyCircuit is a Python-based hardware construction DSL that compiles Python
modules to an MLIR hardware dialect and emits:

- **C++ functional simulation** (module instances become SimObjects with `tick()` / `transfer()`)
- **Verilog** (RTL integration + Verilator)

pyc4.0 is a hard-break release focused on **ultra-large designs**, scalable DFX,
and strict IR legality gates.

## Key features (pyc4.0)

- **Hierarchy-preserving `@module` boundaries** (1:1 with simulation objects)
- **Two-phase cycle model**: `tick()` then `transfer()`
- **Gate-first compiler**: static-hardware IR legality, comb-cycle checks, logic-depth propagation
- **Structured interfaces** via `spec` (Bundle/Struct/Signature) with deterministic flattening
- **Integrated `@testbench`** flow (device + TB compiled together)

## Quick start

Build the backend tool (`pycc`):

```bash
bash flows/scripts/pyc build
```

The staged toolchain is installed under `.pycircuit_out/toolchain/install/` by default.

Install a release wheel instead of building locally:

```bash
python3 -m pip install /path/to/pycircuit_hisi-<version>-py3-none-<platform>.whl
pycc --version
```

The platform wheel bundles the matching `pycc` toolchain under the `pycircuit`
package, so `pycircuit.cli` and the `pycc` wrapper use the same installed source
tree and do not require a separate repo-local build. The wheel must match both
your OS/architecture and Python 3.10+.

Published package install command:

```bash
python3 -m pip install pycircuit-hisi
```

The distribution name is `pycircuit-hisi` to avoid the existing unrelated
`pycircuit` package on PyPI. The Python import path remains `pycircuit`, and
the installed compiler command remains `pycc`.

Install the frontend from source for development:

```bash
python3 -m pip install -e .
python3 -m pycircuit.cli --help
```

Editable source install is frontend-only. It does not install `pycc`; build the
toolchain with `bash flows/scripts/pyc build` and point `PYC_TOOLCHAIN_ROOT` at
`.pycircuit_out/toolchain/install`, or use a release wheel.

Run the smoke gates:

```bash
bash flows/scripts/run_examples.sh
bash flows/scripts/run_sims.sh
```

### Minimal design snippet (counter)

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

Build a multi-module project (device + TB):

```bash
PYTHONPATH=compiler/frontend \
PYC_TOOLCHAIN_ROOT=.pycircuit_out/toolchain/install \
python3 -m pycircuit.cli build \
  designs/examples/counter/tb_counter.py \
  --out-dir /tmp/pyc_counter \
  --target both \
  --jobs 8
```

Emit device RTL without requiring a testbench:

```bash
PYTHONPATH=compiler/frontend \
  PYC_TOOLCHAIN_ROOT=.pycircuit_out/toolchain/install \
python3 -m pycircuit.cli build \
  designs/examples/counter/counter.py \
  --out-dir /tmp/pyc_counter_rtl \
  --target verilog
```

An optional XingTian post-Verilog flow can run remote logic synthesis after all
normal MLIR gates pass:

```bash
python3 -m pycircuit.cli build design.py \
  --out-dir build/design \
  --target verilog \
  --eda-flow xingtian
```

Machine-local SDC and SSH paths can be stored in the Git-ignored
`eda/xingtian/config.local.json`. Public backend documentation, templates,
scripts, tests, and examples live under `eda/<backend>/`; private credentials
and generated results remain ignored. See `eda/README.md` and
`docs/PIPELINE.md`.

For open 45 nm research synthesis, install the pinned Nangate45 Liberty and
select the local Yosys/ABC adapter:

```bash
./eda/nangate45/scripts/setup.sh
cp eda/nangate45/config.example.json eda/nangate45/config.local.json
python3 -m pycircuit.cli build designs/examples/counter/counter.py \
  --out-dir eda/nangate45/build/counter \
  --target verilog \
  --param width=8 \
  --eda-flow nangate45
```

Nangate45 is non-manufacturable and intended for research/flow bring-up.

For more end-to-end commands, see `docs/QUICKSTART.md`.

## Repo layout

```
pyCircuit
├── compiler/
│   ├── frontend/          # Python frontend (pycircuit package)
│   └── mlir/              # MLIR dialect + passes + tools (pycc, pyc-opt)
├── runtime/
│   ├── cpp/               # C++ simulation runtime
│   └── verilog/           # Verilog primitives
├── designs/
│   └── examples/          # Example designs
└── docs/                  # Documentation
```

## Documentation

- `docs/QUICKSTART.md`
- `docs/FRONTEND_API.md`
- `docs/TESTBENCH.md`
- `docs/IR_SPEC.md`
- `docs/updatePLAN.md` and `docs/rfcs/pyc4.0-decisions.md`

## Examples

| Example | Description |
|---------|-------------|
| [Counter](designs/examples/counter/) | Basic counter with enable |
| [Calculator](designs/examples/calculator/) | Stateful keypad calculator |
| [FIFO Loopback](designs/examples/fifo_loopback/) | FIFO queue with loopback |
| [Digital Clock](designs/examples/digital_clock/) | Time-of-day clock display |
| [FastFWD](designs/examples/fastfwd/) | Network packet forwarding |
| [Linx CPU](contrib/linx/designs/examples/linx_cpu_pyc/) | Full 5-stage pipeline CPU |

## License

pyCircuit is licensed under the MIT License. See `LICENSE`.
