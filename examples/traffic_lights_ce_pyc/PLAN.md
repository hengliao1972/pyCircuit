# PLAN: traffic_lights_ce_pyc

## Core observations from Traffic-lights-ce

- Two-direction intersection with East/West (main) and North/South (secondary).
- Default timing: EW green 45s, EW yellow 5s, NS green 30s, NS yellow 5s.
- Red durations are derived from the opposite direction's green+yellow (EW red = 30+5, NS red = 45+5).
- Yellow blinks at 1 Hz during yellow phases.
- Emergency mode forces all-red and displays "88" on both countdowns.
- Original design uses separate countdown modules per direction and an edge-trigger to make single-cycle change pulses.

## Implementation plan for pyCircuit

- Build a new example under `examples/traffic_lights_ce_pyc/` with a cycle-aware design.
- Top-level outputs are 8-bit BCD countdowns (`ew_bcd`, `ns_bcd`) plus discrete red/yellow/green lights.
- Reuse `examples/digital_clock/bcd.py` for BCD conversion (`bin_to_bcd_60`).
- Use a combined 4-phase FSM: EW_GREEN -> EW_YELLOW -> NS_GREEN -> NS_YELLOW -> EW_GREEN
- Maintain two countdown registers (EW/NS). Decrement on each 1 Hz tick.
  - Reload only the direction whose light changes.
  - Red durations are derived from opposite green+yellow.
- Emergency behavior:
  - Outputs forced to all-red and BCD=0x88.
  - Internal counters and phase freeze while `emergency=1` or `go=0`.
- Provide a C API wrapper and a terminal emulator similar to `digital_clock`.

## Deliverables

- `traffic_lights_ce.py` (pyCircuit design)
- `traffic_lights_capi.cpp` (C API wrapper)
- `emulate_traffic_lights.py` (terminal visualization)
- `README.md` (build and run instructions)
- `PLAN.md` (this document)
- `__init__.py` (package marker)

## Interfaces (planned)

- Inputs: `clk`, `rst`, `go`, `emergency`
- Outputs:
  - `ew_bcd`, `ns_bcd` (8-bit BCD, `{tens, ones}`)
  - `ew_red/ew_yellow/ew_green`, `ns_red/ns_yellow/ns_green`

## JIT parameters (planned)

- `CLK_FREQ` (Hz)
- `EW_GREEN_S`, `EW_YELLOW_S`
- `NS_GREEN_S`, `NS_YELLOW_S`
- Derived: `EW_RED_S = NS_GREEN_S + NS_YELLOW_S`, `NS_RED_S = EW_GREEN_S + EW_YELLOW_S`

## Test/usage (planned)

- Generate MLIR via `pycircuit.cli emit` with optional `--param CLK_FREQ=1000` for faster emulation.
- Compile to Verilog/C++ using `pyc-compile --emit=verilog/cpp`.
- Build shared lib and run `emulate_traffic_lights.py`.
