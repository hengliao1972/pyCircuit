# C++ comb wire member placement (`pyc-cpp-placement`)

Always-on C++ emit preparation. After `pyc-fuse-comb`, `pycc` runs
`pyc-cpp-placement` and stamps each SSA value as a struct member or a
method-local `Wire<>`. Single-method comb temporaries leave the generated
SimObject header, which shrinks `.hpp` size and downstream `g++` compile cost.

There is no `--cpp-localize-members` flag. Placement runs whenever
`--emit=cpp`. Verilog / other emitters do not run the pass.

## Pipeline

- `pyc-cpp-placement` runs after `pyc-check-logic-depth` and before
  `pyc-collect-compile-stats`.
- The pass writes `pyc.cpp.comb_chunk_nodes` on the module (from
  `--cpp-shard-max-ast-nodes`, or 256) and annotates:
  - `pyc.cpp.storage` = `local` | `struct`
  - `pyc.cpp.owner` = owning method for locals
  - `pyc.cpp.method` = `eval_comb_N` or `eval_comb_N_part_K`
  - `pyc.cpp.placement_summary` on each `func.func`
- `CppEmitter` only reads those attributes. Missing summary or method attrs
  fail C++ emit.

## Reorder safety

`pyc.comb` is a pure, single-block SSA region. Its verifier rejects nested
regions and any body operation that is not memory-effect-free. Placement may
pick another valid topological order; stateful ops, assigns, instances,
memories, FIFOs, and assertions never enter this scheduler.

## Locality-aware scheduling

Large comb regions are partitioned to a per-part node cap. Placement builds
the SSA DAG, applies deterministic shortest-completion-first Kahn scheduling,
then uses DP to choose part boundaries that minimize the weighted cut of
values that would otherwise stay local. A value that crosses part methods is
promoted back to a struct member.

## Usage

```bash
pycc design.pyc --emit=cpp --out-dir <dir> --cpp-split=module
python3 -m pycircuit.cli build <design.py> --out-dir <dir> --target cpp
```

## Manifest

`cpp_compile_manifest.json` `profile_summary.cpp_placement` fields:

- `struct_members`
- `local_in_method`
- `probe_pinned_struct`
- `cross_part_promoted`
- `scheduled_cross_method`
- `scheduled_cut_weight`

## Gate

```bash
bash compiler/mlir/test/cpp_member_placement_smoke.sh
```
