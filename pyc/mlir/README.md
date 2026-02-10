# `pyc/mlir`: MLIR dialect + tools (prototype)

This folder contains the MLIR-based implementation of the `pyc` dialect, along with:

- `pyc-opt`: `mlir-opt`-style tool with `pyc` dialect + passes
- `pyc-compile`: compile `.pyc` (MLIR) to Verilog or C++ via template libraries

## Build

Recommended: build from the repo root via top-level `CMakeLists.txt` (see `README.md`).

You can also build this subproject standalone if you already have an LLVM+MLIR build/install.

This example assumes an existing `~/llvm-project/build-mlir` containing MLIR.

```bash
cmake -G Ninja -S pyc/mlir -B pyc/mlir/build \
  -DMLIR_DIR=$HOME/llvm-project/build-mlir/lib/cmake/mlir \
  -DLLVM_DIR=$HOME/llvm-project/build-mlir/lib/cmake/llvm

ninja -C pyc/mlir/build pyc-opt pyc-compile
```

## Passes (prototype)

### `pyc-eliminate-wires`

Eliminates trivial `pyc.wire` + `pyc.assign` pairs when safe (single driver that
dominates all reads), and removes dead wires. This reduces netlist noise and
helps subsequent CSE/constprop.

`pyc-compile` runs this pass by default before emission.

### `pyc-comb-canonicalize`

Combinational simplifications, currently focused on mux canonicalization:

- collapses nested muxes with the same select
- rewrites some `i1` mux patterns into simpler boolean logic

`pyc-compile` runs this pass by default before emission.

### `pyc-fuse-comb`

Fuses consecutive pure combinational ops (`pyc.add/mux/and/or/xor/not/constant`) into
`pyc.comb` regions. This is a codegen-oriented transform intended to enable:

- flattened Verilog emission (`assign` instead of many tiny module instantiations)
- inlined C++ combinational evaluation (fewer tiny objects / calls)

`pyc-compile` runs this pass by default before emission.

### `pyc-check-flat-types`

Verifies that the IR is fully lowered to flat hardware-carrying types
(integers + `!pyc.clock`/`!pyc.reset`) before emission. This is a safety net
similar in spirit to FIRRTL's type-lowering: pyCircuit's Python frontend packs
bundles/vectors into integers, so aggregate types should never reach the PYC IR.

`pyc-compile` runs this check by default.

### `pyc-prune-ports`

Module-level cleanup pass that prunes unused `func.func` arguments and updates
`func.call` sites. This changes the externally visible interface, so it is
**not** run by default in `pyc-compile`, but can be useful for internal
refactors or design-space exploration flows.
