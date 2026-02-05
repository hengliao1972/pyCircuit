# PYC IR Spec (prototype)

PYC is an MLIR dialect (`pyc`) intended to be a common, backend-agnostic IR for
hardware components, with multi-clock modeling and strict ready/valid streaming
semantics.

## 1) Types

- `!pyc.clock`: a clock signal
- `!pyc.reset`: a reset signal
- Data types use MLIR integers (`i1`, `i8`, `i32`, ...).

## 2) Operations

All examples below live inside a standard MLIR `module { ... }` and use
`func.func` as the top-level “hardware module” container (prototype convention).

### 2.1 `pyc.constant`

```mlir
%c1 = pyc.constant 1 : i8
```

### 2.2 Combinational ops

```mlir
%y = pyc.add %a, %b : i8
%y = pyc.mux %sel, %a, %b : i8
%y = pyc.and %a, %b : i8
%y = pyc.or  %a, %b : i8
%y = pyc.xor %a, %b : i8
%y = pyc.not %a : i8

%p = pyc.eq %a, %b : i8

%lo = pyc.trunc %x : i64 -> i32
%zx = pyc.zext  %x : i8  -> i64
%sx = pyc.sext  %x : i8  -> i64

%s = pyc.extract %x {lsb = 4} : i16 -> i8
%sh = pyc.shli %x {amount = 2} : i16

%bus = pyc.concat(%a, %b, %c) : (i8, i16, i1) -> i25
```

### 2.2.1 `pyc.alias` (debug naming)

`pyc.alias` is a pure identity op used to attach stable debug names for codegen:

```mlir
%y = pyc.alias %x {pyc.name = "foo__my_file__L42"} : i8
```

Backends use the `pyc.name` attribute (not `name`) to avoid conflicts with other
ops that legitimately use a `name` attribute (e.g. memory instances).

### 2.3 `pyc.wire` / `pyc.assign` (netlist backedges)

The prototype includes a netlist-style “wire placeholder” + explicit driver:

```mlir
%d = pyc.wire : i64
pyc.assign %d, %next : i64
```

`pyc.assign` destinations must be defined by `pyc.wire`. This is used to model
feedback loops (state machines) in SSA-based MLIR.

### 2.4 `pyc.reg` (clocked register)

```mlir
%q = pyc.reg %clk, %rst, %en, %next, %init : i8
```

Semantics (single register):

- On `posedge %clk`:
  - if `%rst` then `q := init`
  - else if `%en` then `q := next`

Common pattern (backedge):

```mlir
%d = pyc.wire : i8
%q = pyc.reg %clk, %rst, %en, %d, %init : i8
pyc.assign %d, %next : i8
```

### 2.5 `pyc.fifo` (strict ready/valid)

```mlir
%in_ready, %out_valid, %out_data =
  pyc.fifo %clk, %rst, %in_valid, %in_data, %out_ready {depth = 2} : i8
```

Handshake semantics:

- **Push** occurs when `in_valid && in_ready`
- **Pop** occurs when `out_valid && out_ready`

Notes:

- This prototype FIFO is **single-clock** (one `%clk`, one `%rst`).
- Cross-clock FIFOs should use `pyc.async_fifo` (dual-clock, strict ready/valid).

### 2.6 `pyc.comb` / `pyc.yield` (fused combinational regions)

`pyc.comb` is a codegen-oriented wrapper for fusing many small pure comb ops
into a single region:

```mlir
%y = pyc.comb(%a, %b) : (i8, i8) -> i8 {
  ^bb0(%a0: i8, %b0: i8):
    %t = pyc.add %a0, %b0 : i8
    pyc.yield %t : i8
}
```

## 3) Verilog backend (prototype)

`pyc-compile --emit=verilog` emits Verilog:

- Combinational ops become instances of primitives in `include/pyc/verilog/` (e.g. `pyc_add`, `pyc_mux`, `pyc_and`)
- `pyc.reg` becomes an instance of `include/pyc/verilog/pyc_reg.v`
- `pyc.fifo` becomes an instance of `include/pyc/verilog/pyc_fifo.v`
- `pyc.async_fifo` becomes an instance of `include/pyc/verilog/pyc_async_fifo.v`
- `pyc.byte_mem` becomes an instance of `include/pyc/verilog/pyc_byte_mem.v`
- `pyc.sync_mem` becomes an instance of `include/pyc/verilog/pyc_sync_mem.v`
- `pyc.sync_mem_dp` becomes an instance of `include/pyc/verilog/pyc_sync_mem_dp.v`
- `pyc.cdc_sync` becomes an instance of `include/pyc/verilog/pyc_cdc_sync.v`

`pyc-compile` also runs `pyc-fuse-comb`, which enables emission of flattened
Verilog `assign` statements for large purely-combinational regions.

## 4) Structured control flow (frontend temporary IR)

The Python AST/JIT frontend may emit a small subset of standard MLIR dialects:

- `scf.if` / `scf.for` (Structured Control Flow)
- `arith.constant` of type `index` (loop bounds)

These are **not** part of the stable PYC dialect contract: `pyc-compile` runs
`pyc-lower-scf-static` to lower them into static PYC hardware ops:

- `scf.if` → `pyc.mux` networks (both branches are speculated; must be side-effect-free)
- `scf.for` → fully unrolled logic (bounds must be compile-time constants)

Note: MLIR canonicalization may also introduce `arith.select` during cleanup.
`pyc-compile` supports `arith.select` in both C++ and Verilog emission, but it
is not considered part of the stable PYC dialect surface area.
