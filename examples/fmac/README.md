# BF16 Fused Multiply-Accumulate (FMAC)

A BF16 floating-point fused multiply-accumulate unit with 4-stage pipeline,
built from primitive standard cells (half adders, full adders, MUXes).

## Operation

```
acc_out (FP32) = acc_in (FP32) + a (BF16) × b (BF16)
```

## Formats

| Format | Bits | Layout | Bias |
|--------|------|--------|------|
| BF16 | 16 | sign(1) \| exp(8) \| mantissa(7) | 127 |
| FP32 | 32 | sign(1) \| exp(8) \| mantissa(23) | 127 |

## 4-Stage Pipeline — Critical Path Summary

```
  Stage 1: Unpack + Exp Add        depth =  8  ████
  Stage 2: 8x8 Multiply (Wallace)  depth = 28  ██████████████
  Stage 3: Align + Add             depth = 21  ██████████
  Stage 4: Normalize + Pack        depth = 31  ███████████████
  ──────────────────────────────────────────────
  Total combinational depth        depth =  88
  Max stage (critical path)        depth =  31
```

| Stage | Function | Depth | Key Components |
|-------|----------|------:|----------------|
| 1 | Unpack BF16 operands, exponent addition | 8 | Bit extract, MUX (implicit 1), 10-bit RCA |
| 2 | 8×8 mantissa multiply | 28 | AND partial products, 3:2 CSA Wallace tree, **carry-select final adder** |
| 3 | Align exponents, add/sub mantissas | 21 | Exponent compare, 5-level barrel shift, 26-bit RCA, magnitude compare |
| 4 | Normalize, pack FP32 | 31 | 26-bit LZC (priority MUX), 5-level barrel shift left/right, exponent adjust |

**Pipeline balance**: The carry-select adder (splitting the 16-bit final
addition into two 8-bit halves computed in parallel) reduced Stage 2 from
depth 46 to 28.  Combined with accurate per-round depth tracking in the
Wallace tree (parallel CSAs share the same depth level), the pipeline is
now well-balanced with the critical path in Stage 4 (depth 31).

## Design Hierarchy

```
bf16_fmac.py (top level)
└── primitive_standard_cells.py
    ├── half_adder, full_adder        (1-bit)
    ├── ripple_carry_adder            (N-bit)
    ├── partial_product_array         (AND gate array)
    ├── compress_3to2 (CSA)           (carry-save adder)
    ├── reduce_partial_products       (Wallace tree)
    ├── unsigned_multiplier           (N×M multiply)
    ├── barrel_shift_right/left       (MUX layers)
    └── leading_zero_count            (priority encoder)
```

## Files

| File | Description |
|------|-------------|
| `primitive_standard_cells.py` | HA, FA, RCA, CSA, multiplier, shifters, LZC |
| `bf16_fmac.py` | 4-stage pipelined FMAC |
| `fmac_capi.cpp` | C API wrapper |
| `test_bf16_fmac.py` | 100 test cases (true RTL simulation) |

## Build & Run

```bash
# 1. Compile RTL
PYTHONPATH=python:. python -m pycircuit.cli emit \
    examples/fmac/bf16_fmac.py \
    -o examples/generated/fmac/bf16_fmac.pyc
build/bin/pyc-compile examples/generated/fmac/bf16_fmac.pyc \
    --emit=cpp -o examples/generated/fmac/bf16_fmac_gen.hpp

# 2. Build shared library
c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
    -o examples/fmac/libfmac_sim.dylib examples/fmac/fmac_capi.cpp

# 3. Run 100 test cases
python examples/fmac/test_bf16_fmac.py
```

## Test Results

100 test cases verified against Python float reference via true RTL simulation:

- **100/100 passed**
- **Max relative error**: 5.36e-04 (limited by BF16's 7-bit mantissa)
- **Test groups**: simple values, powers of 2, small fractions, accumulation
  chains, sign cancellation (acc ≈ -a×b), and 40 random cases
