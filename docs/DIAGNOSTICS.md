# Diagnostics

pyCircuit uses one structured, source-located diagnostic style across:
- API hygiene scan (`flows/tools/check_api_hygiene.py`)
- CLI pre-JIT contract scan (`pycircuit emit/build`)
- JIT elaboration errors (Python frontend)
- MLIR pass errors in `pycc` (backend)

## Format

Human-readable diagnostics generally look like:

- `path:line:col: [CODE] message`
- `stage=<stage>`
- optional source snippet
- optional `hint: ...`

## Common stages

- `api-hygiene`: repository/static scan
- `api-contract`: CLI pre-JIT scan of entry file + local imports
- `jit`: frontend elaboration errors
- MLIR pass errors from `pycc` (for example `pyc-check-frontend-contract`)

## Frontend contract marker

All frontend-emitted `.pyc` files are stamped with a required module attribute:

- `pyc.frontend.contract = "pycircuit"`

If the backend sees a missing/mismatched contract marker, `pycc` fails early.

## Useful commands

Run hygiene scan (from repository root):

```bash
REPO=/path/to/pyCircuit
python3 "$REPO/flows/tools/check_api_hygiene.py"
```

Emit + compile one module:

```bash
REPO=/path/to/pyCircuit
export PYTHONPATH="$REPO/compiler/frontend"
python3 -m pycircuit.cli emit "$REPO/designs/examples/counter/counter.py" -o /tmp/counter.pyc

export PYC_TOOLCHAIN_ROOT="$REPO/.pycircuit_out/toolchain/install"
"$PYC_TOOLCHAIN_ROOT/bin/pycc" /tmp/counter.pyc --emit=cpp --out-dir /tmp/counter_cpp
```

Dump per-pass IR (debug failing gates, localize IR regressions):

```bash
# Write IR before/after every pass to a directory; diff any pass pair.
"$PYC_TOOLCHAIN_ROOT/bin/pycc" /tmp/counter.pyc --emit=none \
  --dump-pass-ir=/tmp/pir
diff /tmp/pir/*_before_*eliminate-wires*.mlir \
     /tmp/pir/*_after_*eliminate-wires*.mlir
```

See [mlir_pass_ir_dump.md](mlir_pass_ir_dump.md) for the full flag set
(`--dump-pass-ir-phase`, `--dump-pass-ir-filter`, `--dump-pass-ir-max-lines`,
`--dump-pass-ir=auto`).
