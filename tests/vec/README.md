# Vec Operator Tests

`tests/vec` contains generated pytest coverage for pyCircuit `Vec` operations.
`Vec` is a frontend-only type: while emitting `.pyc` the frontend expands every
vector operation into scalar per-lane ops and packs vector ports into one
`i{leaf*lanes}` bus, so the generated IR never contains `vector<` or `pyc.v_*`.
Each case in `cases.py` describes the operator, sample input values, expected
scalar IR tokens, and a Python oracle. The generator writes a temporary pyCircuit module
and testbench into `.pycircuit_out/vec-tests/<case>/src/`; the runner then uses
`pycircuit.cli build` to emit `.pyc`, generate C++/Verilog, build the C++ test
binary, and, when available, run Verilator.

## Run

```bash
PYTHONPATH=compiler/frontend pytest tests/vec -m vec
PYTHONPATH=compiler/frontend pytest tests/vec -m "vec and not slow"
PYTHONPATH=compiler/frontend pytest tests/vec -m "vec and verilator"
bash tests/vec/run_vec_ops.sh
PYTHON=python3.12 bash tests/vec/run_vec_ops.sh   # if the default python3 is < 3.10
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

All cases gate the scalar-lane contract: `check_ir` requires the expected lane
op token (`pyc.add`, `pyc.mux`, `pyc.or`, ...) and rejects any leftover
`vector<` / `pyc.v_*`. The generated C++ is also checked for the removed
vector runtime.

`select_vv` (`Vec` condition with two `Vec` arms) is a full backend case.

Vector cast cases (`zext`, `sext`, `trunc`) use the contract-approved
function-style API (`zext(a, width=...)`, etc.) and run as full backend cases.
Method-style calls such as `a.zext(width=...)` remain rejected by `PYC415`.

Signed div/rem are full backend cases. The Verilog emitter keeps the divide/rem
ternary signed by casting the zero branch, avoiding Verilator treating the
protected expression as unsigned.

Scalar-arm Vec select cases (`select_vs` and `select_sv`) are full backend
cases. They guard the broadcast-to-vector fast path used by issue-queue style
masked muxes.

Vector-shaped IO, ordinary JIT `Circuit.instance` Vec port binding, and 2D
dim-reduce have standalone emit+pycc checks.

## Add A Case

Add a `VecCase` entry in `cases.py` and a matching expression branch in
`generate.py`. Keep the generated module small and deterministic. The preferred
shape is scalar lane ports at the testbench boundary and `Vec(...)` inside the
DUT, because the canonical CLI testbench can already drive and expect scalar
ports reliably while the emitted IR stays entirely scalar.

Use `expected` to call the bit-accurate helpers in `oracle.py`. Add
`ir_tokens` for the scalar PYC op that the frontend emits for that operation.

For reduce fastpaths, the runner emits an additional shape-based port source
without a testbench. The scalar wrapper is used for C++/Verilator behavior, and
the packed-port source is used for `.pyc` lane IR and pycc C++/Verilog compile
checks.

## Generated Output

All generated files live under:

```text
.pycircuit_out/vec-tests/<case-name>/
```

This keeps source-tree changes limited to the test framework itself.
