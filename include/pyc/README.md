# `include/pyc/`

Backend “template libraries” used by code generators:

- `runtime/cpp/`: cycle-accurate C++ models (header-only, template-heavy)
- `runtime/verilog/`: Verilog primitives used by emitted RTL

Generated code instantiates these templates for **stateful** primitives (regs,
FIFOs, memories). Most **combinational** logic is emitted inline as expressions
(Verilog operators / C++ `Wire<>` operator overloads) to keep output netlist-like
and easy to read.

## Primitive API (prototype)

The intent is for each primitive to exist in both backends with the same name and
port names (e.g. `pyc_add` / `pyc_reg` / `pyc_fifo`), so MLIR lowering and codegen
can stay backend-agnostic.

Examples:

- Verilog: `runtime/verilog/pyc_reg.v` defines `module pyc_reg #(WIDTH) (...)`
- C++: `runtime/cpp/pyc_reg.hpp` defines `template<unsigned Width> class pyc::cpp::pycReg { ... }`

Runtime header ownership map: `runtime/cpp/README.md`.

Current checked-in primitives (prototype):

- Combinational: (usually emitted inline; wrappers exist but are not required)
  - `pyc_add`, `pyc_and`, `pyc_or`, `pyc_xor`, `pyc_not`, `pyc_mux`
- Sequential: `pyc_reg`
- Ready/valid:
  - `pyc_fifo` (strict handshake, single-clock)
  - `pyc_async_fifo` (dual-clock CDC stream FIFO)
- Memory:
  - `pyc_byte_mem` (byte-addressed, async read + sync write, prototype)
  - `pyc_sync_mem` (1R1W, synchronous read, registered output)
  - `pyc_sync_mem_dp` (2R1W, synchronous read, registered outputs)
- CDC:
  - `pyc_cdc_sync` (multi-flop synchronizer)

Debug/testbench helpers (C++ only):

- `runtime/cpp/pyc_print.hpp`: `operator<<` for wires and primitives
- `runtime/cpp/pyc_tb.hpp`: small multi-clock-capable testbench harness
- `runtime/cpp/pyc_vcd.hpp`: tiny VCD writer (waveform dumping)
- Convenience include: `runtime/cpp/pyc_debug.hpp`

For the full primitive “ABI” (ports/semantics) see `docs/PRIMITIVES.md`.
