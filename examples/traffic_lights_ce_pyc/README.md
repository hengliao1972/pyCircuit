# Traffic Lights (pyCircuit)

A cycle-aware traffic lights controller based on the [Traffic-lights-ce](https://github.com/Starrynightzyq/Traffic-lights-ce) design.
It exposes BCD countdowns for East/West and North/South, plus discrete red/yellow/green lights.
The terminal emulator renders a simple 7-seg view and can load multiple stimulus patterns.

**Key files**
- `traffic_lights_ce.py`: pyCircuit implementation of the FSM, countdowns, blink, and outputs.
- `traffic_lights_capi.cpp`: C API wrapper around the generated C++ model for ctypes.
- `emulate_traffic_lights.py`: terminal visualization; drives the DUT via the C API.
- `stimuli/*.py`: independent stimulus modules (driver logic separated from the DUT).
- `PLAN.md`: design notes and implementation plan.

## Ports

| Port | Dir | Width | Description |
|------|-----|-------|-------------|
| `clk` | in | 1 | System clock |
| `rst` | in | 1 | Synchronous reset |
| `go` | in | 1 | Run/pause (1=run, 0=freeze) |
| `emergency` | in | 1 | Emergency override (1=all red, BCD=88) |
| `ew_bcd` | out | 8 | East/West countdown BCD `{tens,ones}` |
| `ns_bcd` | out | 8 | North/South countdown BCD `{tens,ones}` |
| `ew_red` | out | 1 | East/West red |
| `ew_yellow` | out | 1 | East/West yellow (blink) |
| `ew_green` | out | 1 | East/West green |
| `ns_red` | out | 1 | North/South red |
| `ns_yellow` | out | 1 | North/South yellow (blink) |
| `ns_green` | out | 1 | North/South green |

## JIT parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CLK_FREQ` | 50_000_000 | System clock frequency (Hz) |
| `EW_GREEN_S` | 45 | East/West green time (seconds) |
| `EW_YELLOW_S` | 5 | East/West yellow time (seconds) |
| `NS_GREEN_S` | 30 | North/South green time (seconds) |
| `NS_YELLOW_S` | 5 | North/South yellow time (seconds) |

Derived durations:
- `EW_RED_S = NS_GREEN_S + NS_YELLOW_S`
- `NS_RED_S = EW_GREEN_S + EW_YELLOW_S`

## Build and Run

The emulator assumes `CLK_FREQ=1000` for fast visualization. Set it via
`PYC_TL_CLK_FREQ=1000` when emitting the design. The following sequence is
verified end-to-end (including all stimuli):

```bash
PYC_TL_CLK_FREQ=1000 PYTHONPATH=python python3 -m pycircuit.cli emit \
  examples/traffic_lights_ce_pyc/traffic_lights_ce.py \
  -o /tmp/traffic_lights_ce_pyc.pyc

./build/bin/pyc-compile /tmp/traffic_lights_ce_pyc.pyc \
  --emit=verilog --out-dir=examples/generated/traffic_lights_ce_pyc

./build/bin/pyc-compile /tmp/traffic_lights_ce_pyc.pyc \
  --emit=cpp --out-dir=examples/generated/traffic_lights_ce_pyc

c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
  -o examples/traffic_lights_ce_pyc/libtraffic_lights_sim.dylib \
  examples/traffic_lights_ce_pyc/traffic_lights_capi.cpp

python3 examples/traffic_lights_ce_pyc/emulate_traffic_lights.py --stim basic
python3 examples/traffic_lights_ce_pyc/emulate_traffic_lights.py --stim emergency_pulse
python3 examples/traffic_lights_ce_pyc/emulate_traffic_lights.py --stim pause_resume
```

## Stimuli

Stimulus is loaded as an independent module, separate from the DUT.
Available modules live under `examples/traffic_lights_ce_pyc/stimuli/`.

- `basic`: continuous run, no interruptions
- `emergency_pulse`: assert emergency for a window
- `pause_resume`: toggle `go` to pause/resume
