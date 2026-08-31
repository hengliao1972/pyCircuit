# Pipeline

pyCircuit uses a two-stage compile pipeline:

1. Frontend (Python): source scan + JIT elaboration + `.pyc` emission
2. Backend (`pycc`): MLIR passes + emit C++ and/or Verilog

## Frontend

Frontend responsibilities:
- strict API contract scan (entry file + local imports)
- JIT elaboration of `@module` / `@function` / `@const`
- materialize `@module(value_params=...)` as runtime boundary input ports
- emit one `.pyc` per specialized module
- emit a deterministic `project_manifest.json`
- emit a testbench `.pyc` payload from `@testbench`

All emitted modules are stamped with:
- `pyc.frontend.contract = "pycircuit"`

## Backend (`pycc`)

Backend responsibilities:
- verify required frontend contract attrs (`pyc-check-frontend-contract`)
- verify value-param metadata arity/alignment (`pyc.value_params` + `pyc.value_param_types`)
- inline helper functions and run cleanup/verification passes
- preserve `@module` hierarchy boundaries in strict mode (default: `--hierarchy-policy=strict`)
- emit:
  - C++ model (`--emit=cpp`)
  - Verilog netlist (`--emit=verilog`)
  - testbench text (for `.pyc` files containing `pyc.tb.payload`)

Default backend hierarchy policy:
- `--hierarchy-policy=strict`
- `--inline-policy=off` for hierarchy-preserving module builds
- strict mode fails compilation if frontend module symbol set changes after lowering passes

### Per-pass IR dump (diagnostics)

Write the MLIR IR before and/or after every pass to a directory so the effect
of any single pass is directly diffable. Diagnostics only; disabled by
default (zero overhead when not requested). Works on both `pycc` and
`pyc-opt` (pass an explicit dump directory on `pyc-opt`).

```bash
pycc foo.pyc --emit=none --dump-pass-ir=/tmp/pir
diff /tmp/pir/*_before_*eliminate-wires*.mlir /tmp/pir/*_after_*eliminate-wires*.mlir
```

Related flags: `--dump-pass-ir-phase=before|after|both`,
`--dump-pass-ir-filter=<regex>`, `--dump-pass-ir-max-lines=<N>`, and
`--dump-pass-ir=auto` on **`pycc` only** (resolves to `<--out-dir>/pass_ir`).
Coexists with `--profile-pass-timing` / `--profile-json`.

See [mlir_pass_ir_dump.md](mlir_pass_ir_dump.md) for full details.

## CLI entrypoints

Emit a single `.pyc`:

```bash
python3 -m pycircuit.cli emit <design.py> -o out.pyc
```

Build a project (multi-module + testbench):

```bash
python3 -m pycircuit.cli build <tb_or_top.py> --out-dir <dir> --target cpp|verilator|both --jobs <N>
```

Simulation (Verilator):

```bash
python3 -m pycircuit.cli build <tb.py> --out-dir <dir> --target verilator --run-verilator
```
