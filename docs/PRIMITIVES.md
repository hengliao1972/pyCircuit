# PYC Primitive API (prototype)

This file documents the *single-source* “contract” we want to keep stable as
`pyCircuit` grows: every primitive has a **matching C++ template** and a
**matching Verilog module** with the same name and port names.

The MLIR dialect (`pyc.*` ops) should lower to this primitive layer.

## 1) Common data structures

### 1.1 `Wire<W>`

Represents a combinational value of width `W` bits.

- C++: `pyc::cpp::Wire<W>` (see `runtime/cpp/pyc_bits.hpp`)
- Verilog: `wire [W-1:0]` (or just `[W-1:0]` ports)

### 1.2 `Reg<W>` (module-like)

Represents a clocked storage element with synchronous reset and clock enable.

- Verilog module: `pyc_reg` (`runtime/verilog/pyc_reg.v`)
- C++ class: `pyc::cpp::pycReg<W>` (`runtime/cpp/pyc_reg.hpp`)
 
Note: although the testbenches in `designs/examples/` are written in SystemVerilog for convenience,
the *design* backend is plain Verilog.

Ports:

- `clk` (i1 / wire)
- `rst` (i1 / wire)
- `en` (i1 / wire)
- `d` (W-bit)
- `init` (W-bit)
- `q` (W-bit)

Semantics (posedge `clk`):

- if `rst`: `q <= init`
- else if `en`: `q <= d`

### 1.3 `Vec<T, N>`（已删除）

该运行时容器随 IR 层 `vector<>` 的移除一起删除（`runtime/cpp/pyc_vec.hpp` 不再存在）。
向量在前端发射期即展开为逐 lane 标量 op，生成的 C++ 只用 `pyc::cpp::Wire<W>`；
Verilog 端口是单根 packed 总线。

## 2) Primitive operations (combinational)

All combinational primitives have an `eval()` method in C++ and continuous
assign semantics in Verilog.

Note: the current `pycc` emitters typically **inline** these operations
as expressions (Verilog `assign` / C++ `Wire<>` operators) to keep generated
code netlist-like. The `pyc_*` combinational wrappers remain available as a
stable “template library” layer for hand-written designs or future lowering.

### 2.1 `pyc_add` (W-bit)

- Verilog: `module pyc_add #(WIDTH) (a, b, y)`
- C++: `pyc::cpp::pyc_add<W> { a, b, y; eval(); }`

### 2.2 `pyc_mux` (W-bit)

- Verilog: `module pyc_mux #(WIDTH) (sel, a, b, y)`
- C++: `pyc::cpp::pyc_mux<W> { sel, a, b, y; eval(); }`

### 2.3 Bitwise ops (W-bit)

- `pyc_and`: `(a, b) -> y`
- `pyc_or`: `(a, b) -> y`
- `pyc_xor`: `(a, b) -> y`
- `pyc_not`: `(a) -> y`

## 3) Primitive operations (ready/valid)

### 3.1 `pyc_fifo` (single-clock, strict ready/valid)

- Verilog: `module pyc_fifo #(WIDTH, DEPTH) (...)`
- C++: `pyc::cpp::pycFifo<Width, Depth>` (`runtime/cpp/pyc_fifo.hpp`)

Ports (explicit, for simple codegen):

- `clk`, `rst`
- input: `in_valid`, `in_ready`, `in_data`
- output: `out_valid`, `out_ready`, `out_data`

Handshake:

- push when `in_valid && in_ready`
- pop when `out_valid && out_ready`

Notes (prototype):

- `out_data` is defined as 0 when `out_valid == 0` (empty). This keeps
  C++/Verilog equivalence deterministic without requiring the internal storage
  array to be cleared on reset.

Note: this is currently **single-clock**; async FIFO should be a separate
primitive. See `pyc_async_fifo` below.

### 3.2 `pyc_async_fifo` (dual-clock, strict ready/valid)

- Verilog: `module pyc_async_fifo #(WIDTH, DEPTH) (...)` (`runtime/verilog/pyc_async_fifo.v`)
- C++: `pyc::cpp::pyc_async_fifo<Width, Depth>` (`runtime/cpp/pyc_async_fifo.hpp`)

Ports:

- write domain: `in_clk`, `in_rst`, `in_valid`, `in_ready`, `in_data`
- read domain: `out_clk`, `out_rst`, `out_valid`, `out_ready`, `out_data`

Notes (prototype constraints):

- `DEPTH` must be a power of two and `>= 2`.
- No combinational cross-domain paths (strict handshake).

## 4) Memory

### 4.1 `pyc_byte_mem` (byte-addressed, prototype)

- Verilog: `module pyc_byte_mem #(ADDR_WIDTH, DATA_WIDTH, DEPTH) (...)` (`runtime/verilog/pyc_byte_mem.v`)
- C++: `pyc::cpp::pyc_byte_mem<AddrWidth, DataWidth, DepthBytes>` (`runtime/cpp/pyc_byte_mem.hpp`)

Semantics (prototype):
- Read is combinational: `rdata` reflects `mem[raddr]`.
- Write is synchronous on posedge: when `wvalid`, update bytes selected by `wstrb`.
- Simulation initializes memory bytes to 0 by default (deterministic).
- Reset does not clear memory; testbenches can initialize contents via `memh` (SV) or poke helpers (C++).

### 4.2 `pyc_sync_mem_nm` (N-read x M-write, synchronous read, registered outputs)

The single synchronous-memory primitive: every 1R1W / 2R1W / N x M shape is
this op with a different port count (`pyc.sync_mem` / `pyc.sync_mem_dp` were
unified into it).

- Verilog: `module pyc_sync_mem_nm #(ADDR_WIDTH, DATA_WIDTH, DEPTH, R_PORTS, W_PORTS, READ_MODE) (...)` (`runtime/verilog/pyc_sync_mem_nm.v`)
- C++: `pyc::cpp::pyc_sync_mem_nm<AddrWidth, DataWidth, DepthEntries, RPorts, WPorts, bool OldData = false>` (`runtime/cpp/pyc_sync_mem.hpp`)
- DSL: `m.sync_mem_nm(clk, [(ren, raddr), ...], [(wvalid, waddr, wdata, wstrb), ...], depth=..., name=..., read_mode=...)` returns one read-data signal per read port; `sync_mem()` / `sync_mem_dp()` remain as 1R1W / 2R1W shorthands (they emit `read_mode="old"`).

Ports (Verilog packs port *k* into bits `[k*WIDTH +: WIDTH]`):

- `clk`
- read: `rens`, `raddrs`, `rdatas` (registered)
- write: `wvalids`, `waddrs`, `wdatas`, `wstrbs`

`read_mode` (attribute; `READ_MODE` = 0/1 in Verilog, `OldData` in C++):

- `"forward"` (default): a same-cycle write to the read address is forwarded to
  that read output (register-file / write-first semantics).
- `"old"`: the read output is the pre-write entry (plain SRAM semantics).

Write ports apply in index order, so a higher index wins a same-address
same-cycle conflict. Simulation initializes memory entries to 0 by default
(deterministic). Reset does not clear memory contents, and the port carries no
`rst`.

## 5) CDC

### 5.1 `pyc_cdc_sync` (dst-clocked synchronizer)

- Verilog: `module pyc_cdc_sync #(WIDTH, STAGES) (...)` (`runtime/verilog/pyc_cdc_sync.v`)
- C++: `pyc::cpp::pyc_cdc_sync<Width, Stages>` (`runtime/cpp/pyc_cdc_sync.hpp`)

Ports:

- `clk`, `rst`
- `in`, `out`

## 6) Debugging / Testbench (C++)

Prototype-only utilities to help with bring-up and debugging:

- Printing: `runtime/cpp/pyc_print.hpp` defines `operator<<` for `Wire` and primitives.
- Testbench: `runtime/cpp/pyc_tb.hpp` provides `pyc::cpp::Testbench<Dut>` (multi-clock ready).
- Tracing: `runtime/cpp/pyc_vcd.hpp` provides a tiny VCD dumper (usable via `Testbench::enableVcd()`).
- Convenience include: `runtime/cpp/pyc_debug.hpp`.

Example testbenches are authored with `@testbench` in Python and lowered by `pycc`
from the testbench payload embedded in `.pyc`.

## 7) Generated C++ module-eval caching

When C++ is emitted from MLIR (`pycc --emit=cpp`), hierarchical
`pyc.instance` calls include a default-on input-change cache:

- if all instance inputs are unchanged in a parent `eval()` pass, generated
  code skips that submodule's `eval()`;
- instance outputs are still assigned to parent wires every pass.
- across `tick_commit()`, cache validity is preserved for combinational-only
  submodule hierarchies and invalidated only for stateful submodules.

This is a codegen/runtime performance optimization for large software workloads
on cycle models. To disable it for A/B comparisons, compile generated C++ with:

- `-DPYC_DISABLE_INSTANCE_EVAL_CACHE`

Additional scheduler/cache bisect flags:

- `-DPYC_DISABLE_PRIMITIVE_EVAL_CACHE`
- `-DPYC_DISABLE_SCC_WORKLIST_EVAL`
- `-DPYC_DISABLE_VERSIONED_INPUT_CACHE`

Runtime perf stats controls:

- `PYC_SIM_STATS=1`
- `PYC_SIM_STATS_PATH=<path>`
