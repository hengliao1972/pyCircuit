# 4-Tap FIR Feed-Forward Filter (pyCircuit)

A 4-tap direct-form FIR (Finite Impulse Response) filter implemented in
pyCircuit's unified signal model, with true RTL simulation and waveform
visualization.

## Transfer Function

```
y[n] = c0·x[n] + c1·x[n-1] + c2·x[n-2] + c3·x[n-3]
```

Default coefficients: `c0=1, c1=2, c2=3, c3=4`

## Architecture

```
x_in ──┬──[×c0]──┐
       │          │
      z⁻¹─[×c1]─(+)──┐
       │               │
      z⁻¹─[×c2]─────(+)──┐
       │                    │
      z⁻¹─[×c3]──────────(+)──→ y_out
```

Single-cycle design: 3-stage delay line (shift register) + 4 parallel
multipliers + accumulator tree.

| Register | Width | Description |
|----------|-------|-------------|
| delay_1  | 16    | x[n-1] |
| delay_2  | 16    | x[n-2] |
| delay_3  | 16    | x[n-3] |
| y_valid  | 1     | Output valid (1-cycle delayed x_valid) |

Accumulator width: DATA_W + COEFF_W + 2 guard bits = 34 bits (signed).

## Ports

| Port | Dir | Width | Description |
|------|-----|-------|-------------|
| x_in | in | 16 | Input sample (signed) |
| x_valid | in | 1 | Input strobe |
| y_out | out | 34 | Filter output (signed) |
| y_valid | out | 1 | Output valid |

## Build & Run

```bash
# 1. Compile RTL
PYTHONPATH=python:. python -m pycircuit.cli emit \
    examples/digital_filter/digital_filter.py \
    -o examples/generated/digital_filter/digital_filter.pyc
build/bin/pyc-compile examples/generated/digital_filter/digital_filter.pyc \
    --emit=cpp -o examples/generated/digital_filter/digital_filter_gen.hpp

# 2. Build shared library
c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
    -o examples/digital_filter/libfilter_sim.dylib \
    examples/digital_filter/filter_capi.cpp

# 3. Run emulator
python examples/digital_filter/emulate_filter.py
```

## Test Scenarios

| # | Input | Description |
|---|-------|-------------|
| 1 | Impulse [1,0,0,...] | Verifies impulse response = coefficients |
| 2 | Step [1,1,1,...] | Verifies step response converges to sum(coeffs)=10 |
| 3 | Ramp [0,1,2,...] | Verifies linear input response |
| 4 | Alternating ±100 | Tests signed arithmetic with cancellation |
| 5 | Large values (10000) | Tests near-overflow behavior |
