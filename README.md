# pyCircuit

`pyCircuit` is a Python-first hardware construction + compilation toolkit built around a small MLIR dialect (**PYC**).
You write *sequential-looking* Python; the frontend emits `.pyc` (MLIR), MLIR passes canonicalize + fuse, and `pyc-compile`
emits either:

- **Verilog** (static RTL; strict ready/valid streaming)
- **Header-only C++** (cycle/tick model; convenient for bring-up + debug)

Everything between Python and codegen stays **MLIR-only**.

Docs:
- `docs/USAGE.md` (how to write designs; JIT rules; debug/tracing)
- `docs/IR_SPEC.md` (PYC dialect contract)
- `docs/PRIMITIVES.md` (backend template “ABI”: matching C++/Verilog primitives)
- `docs/VERILOG_FLOW.md` (open-source Verilog sim/lint with Icarus/Verilator/GTKWave)

## Design goals (why this repo exists)

- **Readable Python**: build pipelines/modules with `with m.scope("STAGE"):` + normal Python operators.
- **Static hardware only**: Python control flow lowers to MLIR `scf.*`, then into *static* mux/unrolled logic.
- **Traceability**: stable name mangling (`scope + file:line`) so generated Verilog/C++ stays debuggable.
- **Multi-clock from day 1**: explicit `!pyc.clock` / `!pyc.reset`.
- **Strict ready/valid**: streaming primitives use a single interpretation everywhere.

## Tiny example (JIT-by-default)

```python
from pycircuit import Circuit, cat

def build(m: Circuit, STAGES: int = 3) -> None:
    dom = m.domain("sys")

    a = m.input("a", width=16)
    b = m.input("b", width=16)
    sel = m.input("sel", width=1)

    with m.scope("EX"):
        x = a ^ b
        y = a + b
        data = x
        if sel:
            data = y

    pkt = m.bundle(data=data, tag=(a == b))
    bus = pkt.pack()           # lowers to `pyc.concat`

    with m.scope("PIPE0"):
        r = m.out("bus", domain=dom, width=bus.width, init=0)
        r.set(bus)

    out = pkt.unpack(r.out())
    m.output("out_data", out["data"])
    m.output("out_tag", out["tag"])
```

## Cycle-Aware API (New)

pyCircuit includes a new **cycle-aware** programming paradigm that tracks signal timing automatically:

```python
from pycircuit import compile_cycle_aware, mux

def counter(m, domain, width=8):
    # Cycle 0: inputs
    enable = domain.create_signal("enable", width=1)
    count = domain.create_const(0, width=width, name="count")
    
    # Combinational logic
    count_next = mux(enable, count + 1, count)
    
    # Cycle 1: register
    domain.next()
    count_reg = domain.cycle(count_next, reset_value=0, name="count")
    
    m.output("count", count_reg.sig)

circuit = compile_cycle_aware(counter, name="counter", width=8)
print(circuit.emit_mlir())
```

Key features:
- **Automatic cycle balancing**: When combining signals of different cycles, DFFs are inserted automatically
- **Cycle management**: `domain.next()`, `prev()`, `push()`, `pop()` for precise cycle control
- **JIT compilation**: Python functions compile directly to MLIR

See `docs/CYCLE_AWARE_API.md` for the full API reference and `examples/counter_cycle_aware.py` for more examples.

## Build (pyc-compile / pyc-opt)

Prereqs:
- CMake ≥ 3.20 + Ninja
- A C++17 compiler
- An LLVM+MLIR build/install that provides `LLVMConfig.cmake` + `MLIRConfig.cmake`

### Quickstart (recommended)

If `llvm-config` is on your PATH:

```bash
scripts/pyc build
scripts/pyc regen
scripts/pyc test
```

### Configure + build (recommended: top-level CMake)

```bash
LLVM_DIR="$(llvm-config --cmakedir)"
MLIR_DIR="$(dirname "$LLVM_DIR")/mlir"

cmake -G Ninja -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLVM_DIR="$LLVM_DIR" \
  -DMLIR_DIR="$MLIR_DIR"

ninja -C build pyc-compile pyc-opt
```

### Alternative: build helper (llvm-project source tree)

If you keep `llvm-project` in `~/llvm-project`, you can use:

```bash
bash pyc/mlir/scripts/build_all.sh
```

## Emit + compile a design

### (Optional) install the Python frontend

For convenience you can install the Python package (so `pycircuit` is on your PATH):

```bash
python3 -m pip install -e .
```

Emit `.pyc` (MLIR) from Python:

```bash
PYTHONPATH=python python3 -m pycircuit.cli emit examples/jit_pipeline_vec.py -o /tmp/jit_pipeline_vec.pyc
```

If installed via `pip`, you can also run:

```bash
pycircuit emit examples/jit_pipeline_vec.py -o /tmp/jit_pipeline_vec.pyc
```

Compile MLIR to Verilog:

```bash
./build/bin/pyc-compile /tmp/jit_pipeline_vec.pyc --emit=verilog -o /tmp/jit_pipeline_vec.v
```

Regenerate the checked-in golden outputs under `examples/generated/`:

```bash
scripts/pyc regen
```

## Open-source Verilog simulation (Icarus / Verilator)

See `docs/VERILOG_FLOW.md`.

## LinxISA CPU bring-up (example)

- pyCircuit source: `examples/linx_cpu_pyc/`
- Cycle-aware variant: `examples/linx_cpu_pyc_cycle_aware/`
- SV testbench + program images: `examples/linx_cpu/`
- Generated outputs (checked in): `examples/generated/linx_cpu_pyc/`

Run the self-checking C++ regression:

```bash
bash tools/run_linx_cpu_pyc_cpp.sh
```

Run the cycle-aware variant (same memh fixtures, simpler core model):

```bash
bash tools/run_linx_cpu_pyc_cycle_aware_cpp.sh
```

Optional debug artifacts:
- `PYC_TRACE=1` enables a WB/commit log
- `PYC_VCD=1` enables VCD dumping
- `PYC_TRACE_DIR=/path/to/out` overrides the output directory
- `PYC_KONATA=1` writes a Konata pipeview trace (`*.konata`)
- `PYC_COMMIT_TRACE=/path/to/trace.jsonl` writes a JSONL commit trace (for diffing)

QEMU vs pyCircuit commit-trace diff (requires Linx QEMU + an LLVM `llvm-mc` build):

```bash
# Optional env overrides:
#   QEMU_BIN=/path/to/qemu-system-linx64
#   LLVM_BUILD=/path/to/llvm-build (must contain bin/llvm-mc)
bash tools/run_linx_qemu_vs_pyc.sh /path/to/test.s
```

## Packaging (release tarball)

After building, you can install + package the toolchain:

```bash
cmake --install build --prefix dist/pycircuit
(cd build && cpack -G TGZ)
```

The tarball includes:
- `bin/pyc-compile`, `bin/pyc-opt`
- `include/pyc/*` (C++ + Verilog template libraries)
- `share/pycircuit/python/pycircuit` (Python frontend sources; usable via `PYTHONPATH=...`)

## Repo layout

- `python/pycircuit/`: Python DSL + AST/JIT frontend + CLI
- `pyc/mlir/`: MLIR dialect, passes, tools (`pyc-opt`, `pyc-compile`)
- `include/pyc/`: backend template libraries (C++ + Verilog primitives)
- `examples/`: example designs, testbenches, and checked-in generated outputs
