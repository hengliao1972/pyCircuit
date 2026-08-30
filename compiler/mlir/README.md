# `compiler/mlir`: MLIR dialect + tools (prototype)

This folder contains the MLIR-based implementation of the `pyc` dialect, along with:

- `pyc-opt`: `mlir-opt`-style tool with `pyc` dialect + passes
- `pycc`: compile `.pyc` (MLIR) to Verilog or C++ via template libraries

## Build

Recommended: build from the repo root via top-level `CMakeLists.txt` (see `README.md`).

You can also build this subproject standalone if you already have an LLVM+MLIR build/install.

This example assumes an existing LLVM/MLIR 19 install or build tree.

```bash
cmake -G Ninja -S compiler/mlir -B /tmp/pyc-mlir-build \
  -DMLIR_DIR=/path/to/llvm-19/lib/cmake/mlir \
  -DLLVM_DIR=/path/to/llvm-19/lib/cmake/llvm

ninja -C /tmp/pyc-mlir-build pyc-opt pycc
```

## Passes (prototype)

### `pyc-eliminate-wires`

Eliminates trivial `pyc.wire` + `pyc.assign` pairs when safe (single driver that
dominates all reads), and removes dead wires. This reduces netlist noise and
helps subsequent CSE/constprop.

`pycc` runs this pass by default before emission.

### `pyc-comb-canonicalize`

Combinational simplifications, currently focused on mux canonicalization:

- collapses nested muxes with the same select
- rewrites some `i1` mux patterns into simpler boolean logic

`pycc` runs this pass by default before emission.

### `pyc-fuse-comb`

Fuses consecutive pure combinational ops (`pyc.add/mux/and/or/xor/not/constant`) into
`pyc.comb` regions. This is a codegen-oriented transform intended to enable:

- flattened Verilog emission (`assign` instead of many tiny module instantiations)
- inlined C++ combinational evaluation (fewer tiny objects / calls)

`pycc` runs this pass by default before emission.

### `pyc-check-flat-types`

Verifies that the IR is fully lowered to flat hardware-carrying types
(integers + `!pyc.clock`/`!pyc.reset`) before emission. This is a safety net
similar in spirit to FIRRTL's type-lowering: pyCircuit's Python frontend packs
bundles/vectors into integers, so aggregate types should never reach the PYC IR.

`pycc` runs this check by default.

### `pyc-prune-ports`

Module-level cleanup pass that prunes unused `func.func` arguments and updates
`func.call` sites. This changes the externally visible interface, so it is
**not** run by default in `pycc`, but can be useful for internal
refactors or design-space exploration flows.

## Per-pass IR dump (diagnostics)

`pycc` and `pyc-opt` can write the IR before and/or after every pass to a
directory so the effect of any single pass is directly diffable. This is a
diagnostics-only feature: it never modifies the IR and is disabled by default
(zero overhead when not requested).

```bash
pycc foo.pyc --emit=none --dump-pass-ir=/tmp/pir
ls /tmp/pir
# 0000_before_0001_check-frontend-contract__L0.mlir
# 0001_after_0001_check-frontend-contract__L0.mlir
# 0002_before_0002_inline-functions__L0.mlir
# ...

# Diff the IR across a specific pass:
diff /tmp/pir/0046_before_*eliminate-wires* /tmp/pir/0047_after_*eliminate-wires*
```

File names are `NNNN_<before|after>_<NN>_<pass>__L<level>[_FAILED].mlir`, so
lexical order matches execution order, before/after of one pass share the same
`<NN>`, `__L0` is a module-level pass and `__L1` is func-nested, and a failed
pass gets an `__FAILED` suffix (the file begins with `// PASS FAILED`).

Flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `--dump-pass-ir=<dir>` | (empty = off) | Output directory. `auto` means `<--out-dir>/pass_ir` so dumps travel with profile/gate artifacts. |
| `--dump-pass-ir-phase=before\|after\|both` | `both` | Which phase(s) to record. |
| `--dump-pass-ir-filter=<regex>` | (empty = all) | ECMAScript-style regex on the pass short name (e.g. `eliminate-wires|fuse-comb`). |
| `--dump-pass-ir-max-lines=<N>` | `0` (unlimited) | Truncate each file after N lines (appends `// truncated at N lines`). |

The instrumentation coexists with `--profile-pass-timing` / `--profile-json`; the
two instrumentations are independent and can be enabled together.
