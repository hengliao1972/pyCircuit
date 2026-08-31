# Per-pass IR dump (`--dump-pass-ir`)

Optional diagnostics that write the MLIR IR **before and/or after every pass**
to a directory, so the effect of any single pass is directly diffable. This is
a diagnostics-only feature: it never modifies the IR, never fails the pipeline
on its own, and is disabled by default (zero overhead when not requested).

Useful when:
- a legality gate (e.g. `pyc-check-comb-cycles`, `pyc-check-logic-depth`,
  `pyc-check-flat-types`) fails and you need to see which pass introduced the
  offending IR;
- comparing two designs/CLI option sets and trying to localize the first pass
  where their IR diverges;
- attaching IR evidence to a bug report or gate log.

## Flags

Available on both `pycc` and `pyc-opt` (with one exception noted below):

| Flag | Default | Purpose |
|------|---------|---------|
| `--dump-pass-ir=<dir>` | (empty = off) | Output directory. On **`pycc` only**, the special value `auto` resolves to `<--out-dir>/pass_ir` so dumps travel with profile/gate artifacts. `pyc-opt` has no `--out-dir` and rejects `auto` with an error — pass an explicit directory. |
| `--dump-pass-ir-phase=before\|after\|both` | `both` | Which phase(s) to record. |
| `--dump-pass-ir-filter=<regex>` | (empty = all) | ECMAScript-style regex on the pass short name (e.g. `eliminate-wires\|fuse-comb`). Match is performed against the short name without the `pyc-` prefix. |
| `--dump-pass-ir-max-lines=<N>` | `0` (unlimited) | Truncate each file after N lines; the file ends with `// truncated at N lines`. |

`pyc-opt` only builds when the host LLVM package exports
`MLIRRegisterAllPasses`; some prebuilt kits do not, in which case `pyc-opt` is
skipped at CMake time and only `pycc` carries the flags.

## Output layout

Files are named so that **lexical order equals execution order**, and the
before/after of one pass are trivial to spot and diff:

```text
NNNN_<before|after>_<NN>_<pass-short>__L<level>[_FAILED].mlir
```

- `NNNN` — global sequence number, zero-padded to at least 4 digits (grows
  past 9999 without wrapping); one per emitted file.
- `<before>` / `<after>` — phase marker.
- `<NN>` — per-pass index; the before and after of the same pass share this
  number.
- `<pass-short>` — pass argument with the `pyc-` prefix stripped (e.g.
  `eliminate-wires`, `symbol-dce`, `canonicalize`).
- `__L<level>` — nesting level. `L0` = module-level pass; `L1` = func-nested
  pass (the `OpToOpPassAdaptor` wrappers that MLIR inserts around nested
  pipelines are also recorded, so you can see the full nesting).
- `_FAILED` — appended when the pass signalled failure; the file's first line
  is then `// PASS FAILED` and the rest is the IR at the point of failure.

Example (counter example, `--dump-pass-ir-phase=both`):

```text
0000_before_0001_check-frontend-contract__L0.mlir
0001_after_0001_check-frontend-contract__L0.mlir
0002_before_0002_inline-functions__L0.mlir
0003_after_0002_inline-functions__L0.mlir
...
0023_before_0013_eliminate-wires__L1.mlir
0024_after_0013_eliminate-wires__L1.mlir
...
```

## CLI usage

```bash
# pycc: full pipeline, no emission (cheapest diagnostic run)
pycc foo.pyc --emit=none --dump-pass-ir=/tmp/pir

# pycc: alongside a real build; dumps land in <out-dir>/pass_ir
pycc foo.pyc --emit=cpp --out-dir=/tmp/out --dump-pass-ir=auto

# Focus on a single pass and only its after-image
pycc foo.pyc --emit=none \
  --dump-pass-ir=/tmp/pir \
  --dump-pass-ir-filter='eliminate-wires' \
  --dump-pass-ir-phase=after

# pyc-opt: experiment with one pass at a time
pyc-opt foo.mlir -pyc-eliminate-wires --dump-pass-ir=/tmp/pir
```

## Diffing a single pass

The before/after pair of any pass is directly diffable:

```bash
diff /tmp/pir/*_before_*eliminate-wires*.mlir \
     /tmp/pir/*_after_*eliminate-wires*.mlir
```

For the counter example this prints the pass's net effect — replacing a
`pyc.wire` + `pyc.assign` pair with a `pyc.alias`:

```diff
-  %6 = pyc.wire {pyc.name = "_v5_bal_1__next"} : i8
+  %6 = pyc.alias %5 {pyc.name = "_v5_bal_1__next"} : i8
   ...
-  pyc.assign %6, %5 : i8
```

## Coexistence with other instrumentation

`--dump-pass-ir` is implemented as a `mlir::PassInstrumentation`, the same
hook used by `--profile-pass-timing` / `--profile-json`. The two are
independent and can be enabled together:

```bash
pycc foo.pyc --emit=none \
  --dump-pass-ir=/tmp/pir \
  --profile-pass-timing --profile-json=/tmp/pir/profile.json
```

`profile.json` records per-pass timing/memory; the `.mlir` files record
per-pass IR. They share a directory so the whole bundle can be attached to a
gate log or bug report.

For MLIR's built-in stderr dumper (`-mlir-print-ir-before` /
`-mlir-print-ir-after`, available on `pyc-opt`) use that directly when you
only need a quick stderr trace; this feature is for stable, diffable,
filterable file output.

## Notes on `pyc-opt`

`pyc-opt` registers the dump flags via `MlirOptMainConfig::setPassPipelineSetupFn`,
so they work whether you pass a single pass (`-pyc-eliminate-wires`) or a full
`-pass-pipeline='func.func(...)'`. The flags are parsed alongside MLIR's
standard options, so `--help` lists both. Pass an explicit directory;
`--dump-pass-ir=auto` is rejected (no `--out-dir` on `pyc-opt`).

## Gate

```bash
bash compiler/mlir/test/pass_ir_dumper_smoke.sh
```

The gate exercises: default-off behavior, file naming and lexical ordering,
filter regex, `phase=after`, `max-lines` truncation, before/after
diffability, and coexistence with `--profile-pass-timing` / `--profile-json`.
