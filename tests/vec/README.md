# Vec Operator Tests

`tests/vec` contains generated pytest coverage for pyCircuit `Vec` operations.
Each case in `cases.py` describes the operator, sample input values, expected IR
tokens, and a Python oracle. The generator writes a temporary pyCircuit module
and testbench into `.pycircuit_out/vec-tests/<case>/src/`; the runner then uses
`pycircuit.cli build` to emit `.pyc`, generate C++/Verilog, build the C++ test
binary, and, when available, run Verilator.

## Run

```bash
PYTHONPATH=compiler/frontend pytest tests/vec -m vec
PYTHONPATH=compiler/frontend pytest tests/vec -m "vec and not slow"
PYTHONPATH=compiler/frontend pytest tests/vec -m "vec and verilator"
bash tests/vec/run_vec_ops.sh
make vec-smoke
```

The runner looks for `pycc` in this order:

1. `$PYCC`
2. `.pycircuit_out/toolchain/install/bin/pycc`
3. `build/bin/pycc`
4. `compiler/mlir/build2/bin/pycc`
5. `compiler/mlir/build/bin/pycc`

If `verilator` is not installed, the framework still runs frontend, pycc, and
C++ simulator checks.

The default matrix explicitly distinguishes operand direction:

- `*_vv`: `Vec op Vec`
- `*_vs`: `Vec op scalar`
- `*_sv`: `scalar op Vec`

Arithmetic, bitwise, comparison, division, and remainder cases include both
scalar-vector directions where the operator supports them. This catches Python
dunder dispatch regressions such as `Vec.__add__` versus `Wire.__radd__` paths.

Some cases are intentionally frontend-only in the default matrix:

- `select_vv`: current JIT supports scalar conditional expressions, but does
  not yet have a contract-approved Vec condition select syntax.
- `zext`, `sext`, `trunc`: the methods still exist on `Vec`, but current API
  contract blocks method-style cast calls in canonical build inputs.
- `slice`: currently scalarizes to per-lane `pyc.extract`; it remains tracked
  as an optimization target rather than a vector-IR gate.

Signed div/rem run as C++ backend cases only by default because the generated
Verilator behavior currently disagrees with the bit-accurate signed oracle. The
case remains in the matrix so the gap is visible.

Vector-shaped IO and 2D dim-reduce have standalone emit+pycc checks. Ordinary
JIT `Circuit.instance` still does not accept `Vec` as an instance port binding;
hierarchical Vec IO should be covered through the cycle-aware `domain.call`
path or after `Circuit.instance` grows equivalent vector-port support.

## Add A Case

Add a `VecCase` entry in `cases.py` and a matching expression branch in
`generate.py`. Keep the generated module small and deterministic. The preferred
shape is scalar lane ports at the testbench boundary and `Vec(...)` inside the
DUT, because the canonical CLI testbench can already drive and expect scalar
ports reliably while the IR still contains vector operations.

Use `expected` to call the bit-accurate helpers in `oracle.py`. Add
`ir_tokens` for the vector type plus the PYC op token that should appear in the
emitted `.pyc`. If an operation is known to scalarize today, set
`allow_scalarized=True` so it is visible as a future optimization target.

For reduce fastpaths, the runner emits an additional vector-shaped-port source
without a testbench. The scalar wrapper is used for C++/Verilator behavior, and
the vector-shaped source is used for `.pyc` vector IR and pycc C++/Verilog
compile checks.

## Generated Output

All generated files live under:

```text
.pycircuit_out/vec-tests/<case-name>/
```

This keeps source-tree changes limited to the test framework itself.
