# pyCircuit Usage Guide (prototype, evolving)

This document is the practical “how to write real designs” guide for pyCircuit.
It focuses on the Python frontend, the JIT/SCF rules, and how to debug what you
generate in Verilog and C++.

> Key idea: write **sequential-looking** Python that is compiled into a **static**
> hardware circuit (parallel combinational logic + clocked state).

---

## 1) Mental model

- A `build(m: Circuit, ...)` function describes a **module**.
- `Wire` is a combinational value (`iN`).
- `Reg` is a clocked state element with an explicit “next” (via `set()`).
- Python `if` / `for range(...)` inside JIT mode compile into MLIR `scf.*`, then
  `pyc-compile` lowers `scf` into static mux/unrolled logic.

### Static control flow only

- **Allowed**: `if cond:` where `cond` is:
  - a Python `bool/int` (compile-time) or
  - an `i1` `Wire` (hardware conditional)
- **Allowed**: `for _ in range(CONST)` (fully unrolled).
- **Not allowed**: dynamic loops, variable loop bounds, while-loops, recursion.

---

## 2) Writing a design

### 2.1 Top-level skeleton (JIT mode is default)

`pycircuit.cli emit` treats `build(m: Circuit, ...)` as a JIT design function
as long as all non-`m` parameters have defaults.

```python
from pycircuit import Circuit

def build(m: Circuit, STAGES: int = 3) -> None:
    dom = m.domain("sys")

    a = m.input("a", width=16)
    b = m.input("b", width=16)

    r = m.out("acc", domain=dom, width=16, init=0)

    x = a + b
    r.set(x)  # default when=1

    m.output("acc", r)
```

You can override JIT parameters from the CLI (repeat `--param` as needed):

```bash
PYTHONPATH=python python3 -m pycircuit.cli emit examples/fastfwd_pyc/fastfwd_pyc.py \
  --param N_FE=8 \
  --param LANE_Q_DEPTH=64 --param ROB_DEPTH=32 \
  -o /tmp/fastfwd.pyc
```

### 2.2 Stage-friendly coding style

Write each pipeline stage in the same pattern:

```python
with m.scope("EX"):
    a = prev_a.out()
    b = prev_b.out()
    y = a + b
    next_a.set(y)
```

- `.out()` reads the current flop output (`q`).
- `.set(v, when=cond)` drives the backedge “next” wire (`d`).
- `with m.scope("NAME"):` prefixes debug names for easier traceability.

### 2.3 Hierarchy: `m.instance(...)` (multi-module designs)

For large projects (SoCs, many cores), pyCircuit supports explicit module
instantiation while preserving module boundaries through codegen.

`pycircuit.cli emit` compiles a design into a single MLIR `module { ... }` that
may contain **multiple** `func.func` hardware modules. Use `Circuit.instance`
to instantiate submodules:

```python
from pycircuit import Circuit, module

@module(name="Core")
def core(m: Circuit, *, WIDTH: int = 32) -> None:
    clk = m.clock("clk")
    rst = m.reset("rst")
    x = m.input("x", width=WIDTH)
    m.output("y", x + 1)

def build(m: Circuit) -> None:
    clk = m.clock("clk")
    rst = m.reset("rst")
    x = m.input("x", width=32)

    u0 = m.instance(core, name="core0", params={"WIDTH": 32}, clk=clk, rst=rst, x=x)
    m.output("y", u0["y"])
```

Key rules:

- `name=` is the **instance name** used in generated Verilog/C++.
- `params=` are **compile-time** specialization parameters (must be deterministic:
  `bool/int/str` and tuples/lists/dicts thereof).
- Port connections are **by port name**; missing/extra ports are hard errors.
- The output of `m.instance(...)` is a `Bundle` keyed by the callee’s output port names.

To scale builds, `pyc-compile` also supports split emission:

```bash
pyc-compile design.pyc --emit=verilog --out-dir out/
pyc-compile design.pyc --emit=cpp --out-dir out/
```

### 2.4 Cycle-aware designs (optional API)

pyCircuit also provides a **cycle-aware** API (`CycleAwareCircuit` / `CycleAwareDomain`) that tracks the
cycle of each signal and can automatically balance cycles when values are combined.

For CLI compatibility, the recommended pattern is:

- Keep your “design function” as `fn(m: CycleAwareCircuit, domain: CycleAwareDomain, ...) -> None`
- Provide a zero-arg `build()` wrapper that calls `compile_cycle_aware(fn, ...)` and returns the circuit.

Examples:

- `examples/digital_clock/digital_clock.py`
- `examples/calculator/calculator.py`
- `examples/multiclock_regs.py`

Reference:

- `docs/CYCLE_AWARE_API.md`

This writes one file per module plus an `out/manifest.json`.

FPGA-first Verilog emission enables inference-friendly attributes in primitives:

```bash
pyc-compile design.pyc --emit=verilog --target=fpga -o out.v
```

### 2.4 Assertions (simulation-only)

In JIT-compiled design code, Python `assert` is supported and lowers to a
simulation-only `pyc.assert` op:

```python
assert in_ready, "in_ready must be high"
```

Notes:

- The assertion condition must be an `i1` `Wire`.
- Assertions are emitted under `ifndef SYNTHESIS` in Verilog, and as runtime
  checks in the C++ model.
- In non-JIT (elaboration) designs, use `m.assert_(cond, msg="...")` instead of
  Python `assert`, since `Wire` cannot be used as a Python boolean.

### 2.5 Testbench generation: `pycircuit testgen` (C++ + SV/SVA)

A single Python file can define both the design and a small cycle-based
testbench:

```python
from pycircuit import Circuit, Tb, sva

def build(m: Circuit) -> None:
    ...

def tb(t: Tb) -> None:
    t.clock("clk")
    t.reset("rst")
    t.random("in_data", seed=1)
    t.drive("in_valid", True, at=0)
    t.expect("out_valid", True, at=3)
    t.sva_assert(sva.rose("in_valid"), clock="clk", reset="rst", name="in_valid_rose")
    t.finish(at=10)
```

Generate split RTL plus C++/SystemVerilog testbenches:

```bash
PYTHONPATH=python python3 -m pycircuit.cli testgen path/to/design.py --out-dir out/
```

This emits:

- `out/*.v` (+ `out/pyc_primitives.v`) and `out/manifest.json`
- `out/*.hpp`
- `out/tb_<Top>.cpp`
- `out/tb_<Top>.sv` (includes SVA assertions)

---

## 3) Types, widths, and “inference”

### 3.1 Where widths are required today

You must still provide widths for:

- module ports: `m.input("x", width=...)`
- state: `m.out("r", width=..., ...)`
- any manually created wires: `m.new_wire(width=...)`

### 3.2 What is inferred/automatic

The frontend tries to remove common “cast noise”:

- **Binary ops auto-promote width**: `a + b` zero-extends the smaller operand
  to the larger width (unsigned semantics).
- **Assignment auto-resizes**:
  - assigning a narrower integer into a wider destination inserts `zext`
  - assigning a wider integer into a narrower destination inserts `trunc`
- **`if` merges auto-default**:
  - if a variable is only assigned in one branch, the other branch keeps the
    previous value (if pre-defined) or defaults to `0`.

If you need explicit signed behavior, use `.sext(...)` / `.ashr(...)` manually
today. You can also mark signed intent at the edges (e.g. `m.input(..., signed=True)`
or negative literals); signed intent is propagated through most arithmetic and
selects signed lowering (`pyc.slt`, `pyc.sdiv`, `pyc.ashri`, etc).

---

## 4) Operators and common patterns

### 4.1 Bit slicing / indexing

`Wire` supports Verilog-like slicing:

- `x[0]` → 1-bit slice
- `x[4:8]` → bits `[7:4]` as a packed wire (lsb=4, width=4)

### 4.2 Shift operators (`<<`, `>>`)

- `x << 3` is a constant left shift.
- `x >> 2` is a constant right shift:
  - logical (zero-fill) by default
  - arithmetic (sign-fill) if `x` is marked signed
- Use `x.ashr(amount=...)` for arithmetic right shift (sign-fill).

### 4.3 Comparisons in JIT code

Inside JIT-compiled code, these compile to `i1` wires:

- `==`, `!=`
- `<`, `<=`, `>`, `>=` (respects signed intent: if either operand is signed, lowers to signed compare)

In helper functions executed at JIT time (plain Python), prefer explicit methods:

- `x.eq(y)` for equality
- `x.ult(y)`, `x.ule(y)`, `x.ugt(y)`, `x.uge(y)` for unsigned compares

### 4.4 Concatenation (packed buses)

Python cannot overload `{a, b, c}` syntax, but pyCircuit supports these forms:

```python
from pycircuit import cat

bus0 = cat(a, b, c)   # MSB-first (Verilog-style)
bus1 = m.cat(a, b, c) # same, using the Circuit builder
```

Under the hood this lowers to `pyc.concat`, so the Verilog backend emits a readable
`{a, b, c}` packed concatenation instead of shift/or glue logic.

You can unpack with slices or via `Bundle`/`Vec` helpers (next sections).

---

## 5) Containers: Vec and Bundle

### 5.1 `Vec` (fixed-length list of lanes)

Use `Vec` when you want arrays of wires/regs (pipelines, regfiles):

```python
v = m.vec(a, b, c)     # Vec
bus = v.pack()         # packed concat
v2 = v.unpack(bus)     # reverse
```

### 5.2 `Bundle` (named fields)

`Bundle` is a tiny named container (like a Verilog struct):

```python
pkt = m.bundle(tag=tag, data=data, lo8=data[0:8])
bus = pkt.pack()
pkt2 = pkt.unpack(bus)

m.output("tag", pkt2["tag"])
```

---

## 6) Ready/valid streaming: FIFO + Queue wrapper

### 6.1 FIFO primitive (`pyc.fifo`)

Handshake is strict:

- push when `in_valid && in_ready`
- pop when `out_valid && out_ready`

### 6.2 Queue API (Python wrapper)

The frontend provides a small “event-ish” wrapper:

```python
q = m.queue("q", domain=dom, width=32, depth=2)
q.push(in_data, when=in_valid)
p = q.pop(when=out_ready)

m.output("in_ready", q.in_ready)
m.output("out_valid", p.valid)
m.output("out_data", p.data)
```

Limitations (prototype): one `push()` and one `pop()` call per `Queue` instance.

---

## 7) Memory

`pyc.byte_mem` models a byte-addressed memory (prototype):

- async read (combinational)
- sync write on clock edge (byte strobes)

See `examples/linx_cpu_pyc/memory.py` and `examples/linx_cpu_pyc/linx_cpu_pyc.py`.

---

## 8) Multi-file designs: `@jit_inline`

If you split a design into multiple Python files (pipeline stages/modules),
mark helpers with `@jit_inline` so they compile into the current circuit rather
than executing at JIT time.

This preserves consistent name mangling:

- scope prefixes (`with m.scope("WB")`)
- file stem + line number (`__decode__L55`, etc)

---

## 9) Debugging and traceability

### 9.1 Name mangling and generated readability

- Use `with m.scope("NAME"):` to group signals by stage/module.
- Assigning to a Python variable in JIT code usually creates a `pyc.alias`
  with a stable name that includes `file:line`.

Generated Verilog/C++ tries to keep readable identifiers:

- If a value has a `pyc.name`, it becomes the identifier.
- Otherwise, the emitter falls back to op-based names (e.g. `pyc_add_123`).

### 9.2 C++ tracing (linx CPU)

`examples/linx_cpu_pyc/tb_linx_cpu_pyc.cpp` supports:

- `PYC_TRACE=1` → instruction-level log (writes into `examples/generated/linx_cpu_pyc/`)
- `PYC_VCD=1` → VCD waveform dump (same folder)

### 9.3 SV tracing

`examples/linx_cpu/tb_linx_cpu_pyc.sv` supports:

- VCD dump by default (disable with `+notrace`)
- log file by default (disable with `+nolog`)
- output paths override: `+vcd=<path>` / `+log=<path>`

For running the generated Verilog through open-source tools (Icarus/Verilator/GTKWave),
see `docs/VERILOG_FLOW.md`.

---

## 10) What’s still missing / recommended next ops

The prototype already includes the “baseline bring-up set” (cmp/shift/mul-div/rem,
mem ports, async FIFO + CDC sync). The most useful missing pieces for scaling up
to serious designs are now less about single ops and more about structure:

1. **Hierarchical modules / instantiation**
   - preserve boundaries in IR and optionally in emission (readability + reuse)
2. **Interfaces**
   - first-class ready/valid “stream” bundles; better type-checking for handshakes
3. **More memory variants**
   - true dual-port (2R2W), write-first/read-first policies, per-port clocks
4. **Variable shifts**
   - `shl`, `lshr`, `ashr` by a *value* (not only immediates)
5. **Better diagnostics**
   - error messages that always include `file:line` and the current `scope()`
