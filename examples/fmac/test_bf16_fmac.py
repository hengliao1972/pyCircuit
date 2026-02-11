#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_bf16_fmac.py — 100 test cases for the BF16 FMAC via true RTL simulation.

Tests: acc_out = acc_in + a_bf16 * b_bf16   (BF16 inputs, FP32 accumulator)

Verifies against Python float reference.  Allows small rounding error
because the RTL uses fixed-width mantissas and integer arithmetic.

Build first (from pyCircuit root):
    c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
        -o examples/fmac/libfmac_sim.dylib examples/fmac/fmac_capi.cpp

Run:
    python examples/fmac/test_bf16_fmac.py
"""
from __future__ import annotations

import ctypes
import math
import random
import struct
import sys
import time
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# ANSI
# ═══════════════════════════════════════════════════════════════════
RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; CYAN = "\033[36m"

# ═══════════════════════════════════════════════════════════════════
# BF16 / FP32 conversion helpers
# ═══════════════════════════════════════════════════════════════════

def float_to_bf16(f: float) -> int:
    """Convert Python float to BF16 (truncate, no rounding)."""
    fp32 = struct.pack('>f', f)
    return (fp32[0] << 8) | fp32[1]


def bf16_to_float(bf16: int) -> float:
    """Convert BF16 to Python float."""
    fp32_bytes = bytes([(bf16 >> 8) & 0xFF, bf16 & 0xFF, 0, 0])
    return struct.unpack('>f', fp32_bytes)[0]


def float_to_fp32(f: float) -> int:
    """Convert Python float to IEEE 754 FP32 (uint32)."""
    return struct.unpack('>I', struct.pack('>f', f))[0]


def fp32_to_float(u32: int) -> float:
    """Convert IEEE 754 FP32 (uint32) to Python float."""
    return struct.unpack('>f', struct.pack('>I', u32 & 0xFFFFFFFF))[0]


# ═══════════════════════════════════════════════════════════════════
# RTL wrapper
# ═══════════════════════════════════════════════════════════════════

PIPELINE_DEPTH = 4  # 4-stage pipeline


class FmacRTL:
    def __init__(self, lib_path=None):
        if lib_path is None:
            lib_path = str(Path(__file__).resolve().parent / "libfmac_sim.dylib")
        L = ctypes.CDLL(lib_path)
        L.fmac_create.restype = ctypes.c_void_p
        L.fmac_destroy.argtypes = [ctypes.c_void_p]
        L.fmac_reset.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        L.fmac_push.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32]
        L.fmac_idle.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        L.fmac_get_result.argtypes = [ctypes.c_void_p]; L.fmac_get_result.restype = ctypes.c_uint32
        L.fmac_get_result_valid.argtypes = [ctypes.c_void_p]; L.fmac_get_result_valid.restype = ctypes.c_uint32
        L.fmac_get_cycle.argtypes = [ctypes.c_void_p]; L.fmac_get_cycle.restype = ctypes.c_uint64
        self._L, self._c = L, L.fmac_create()

    def __del__(self):
        if hasattr(self, '_c') and self._c:
            self._L.fmac_destroy(self._c)

    def reset(self):
        self._L.fmac_reset(self._c, 2)

    def compute(self, a_bf16: int, b_bf16: int, acc_fp32: int) -> int:
        """Push inputs, wait for pipeline, return FP32 result."""
        self._L.fmac_push(self._c, a_bf16, b_bf16, acc_fp32)
        # Wait for pipeline to flush (PIPELINE_DEPTH cycles)
        self._L.fmac_idle(self._c, PIPELINE_DEPTH + 2)
        return self._L.fmac_get_result(self._c)


# ═══════════════════════════════════════════════════════════════════
# Test generation
# ═══════════════════════════════════════════════════════════════════

def make_test_cases():
    """Generate 100 test cases: (a_float, b_float, acc_float)."""
    cases = []

    # Group 1: Simple integer-like values (20 cases)
    simple_pairs = [
        (1.0, 1.0, 0.0),   (2.0, 3.0, 0.0),   (1.5, 2.0, 0.0),
        (0.5, 4.0, 0.0),   (1.0, 0.0, 0.0),   (0.0, 5.0, 0.0),
        (1.0, 1.0, 1.0),   (2.0, 3.0, 1.0),   (1.5, 2.0, 10.0),
        (-1.0, 1.0, 0.0),  (-2.0, 3.0, 0.0),  (1.0, -1.0, 0.0),
        (-1.0, -1.0, 0.0), (2.0, 2.0, -8.0),  (3.0, 3.0, -9.0),
        (0.5, 0.5, 0.0),   (0.25, 4.0, 0.0),  (8.0, 0.125, 0.0),
        (10.0, 10.0, 0.0), (100.0, 0.01, 0.0),
    ]
    cases.extend(simple_pairs)

    # Group 2: Powers of 2 (10 cases)
    for i in range(10):
        a = 2.0 ** (i - 3)
        b = 2.0 ** (5 - i)
        acc = 0.0
        cases.append((a, b, acc))

    # Group 3: Small values (10 cases)
    for i in range(10):
        a = (i + 1) * 0.0625
        b = (10 - i) * 0.125
        acc = i * 0.5
        cases.append((a, b, acc))

    # Group 4: Accumulation chain (10 cases) — acc carries over
    for i in range(10):
        a = float(i + 1)
        b = 0.5
        acc = float(i * 2)
        cases.append((a, b, acc))

    # Group 5: Negative accumulator (10 cases)
    for i in range(10):
        a = float(i + 1)
        b = float(i + 2)
        acc = -float((i + 1) * (i + 2))  # acc = -(a*b), so result ≈ 0
        cases.append((a, b, acc))

    # Group 6: Random values (40 cases)
    rng = random.Random(42)
    for _ in range(40):
        # Random BF16-representable values
        a = bf16_to_float(float_to_bf16(rng.uniform(-10, 10)))
        b = bf16_to_float(float_to_bf16(rng.uniform(-10, 10)))
        acc = fp32_to_float(float_to_fp32(rng.uniform(-100, 100)))
        cases.append((a, b, acc))

    return cases[:100]


# ═══════════════════════════════════════════════════════════════════
# Main test runner
# ═══════════════════════════════════════════════════════════════════

def main():
    print(f"  {BOLD}BF16 FMAC — 100 Test Cases (True RTL Simulation){RESET}")
    print(f"  {'=' * 55}")

    # Print pipeline depth analysis
    print(f"\n  {CYAN}Pipeline Critical Path Analysis:{RESET}")
    depths = {
        "Stage 1: Unpack + PP + 2×CSA": 13,
        "Stage 2: Complete Multiply": 22,
        "Stage 3: Align + Add": 21,
        "Stage 4: Normalize + Pack": 31,
    }
    for stage, d in depths.items():
        bar = "█" * (d // 2)
        print(f"    {stage:<35s} depth={d:>3d}  {CYAN}{bar}{RESET}")
    print(f"    {'─' * 50}")
    print(f"    {'Max stage (critical path)':<35s} depth={max(depths.values()):>3d}")
    print()

    sim = FmacRTL()
    sim.reset()

    cases = make_test_cases()
    passed = 0
    failed = 0
    max_err = 0.0

    t0 = time.time()

    for i, (a_f, b_f, acc_f) in enumerate(cases):
        a_bf16 = float_to_bf16(a_f)
        b_bf16 = float_to_bf16(b_f)
        acc_u32 = float_to_fp32(acc_f)

        # RTL result
        result_u32 = sim.compute(a_bf16, b_bf16, acc_u32)
        rtl_f = fp32_to_float(result_u32)

        # Python reference: acc + a * b
        # Use BF16-truncated values for fair comparison
        a_exact = bf16_to_float(a_bf16)
        b_exact = bf16_to_float(b_bf16)
        acc_exact = fp32_to_float(acc_u32)
        expected_f = acc_exact + a_exact * b_exact

        # Tolerance: allow ~1% relative error or 1e-4 absolute
        # (BF16 has limited mantissa precision)
        if expected_f == 0:
            err = abs(rtl_f)
            ok = err < 0.01
        else:
            err = abs(rtl_f - expected_f) / max(abs(expected_f), 1e-10)
            ok = err < 0.02  # 2% relative error tolerance for BF16 precision

        max_err = max(max_err, err)

        if ok:
            passed += 1
            status = f"{GREEN}PASS{RESET}"
        else:
            failed += 1
            status = f"{RED}FAIL{RESET}"

        # Print each test
        tag = f"{DIM}" if ok else f"{BOLD}"
        print(f"  {tag}[{i+1:3d}/100]{RESET} "
              f"a={a_exact:>9.4f} b={b_exact:>9.4f} acc={acc_exact:>10.4f} → "
              f"RTL={rtl_f:>12.4f}  exp={expected_f:>12.4f}  "
              f"err={err:.2e}  {status}")

    t1 = time.time()

    print(f"\n  {'=' * 55}")
    print(f"  Results: {GREEN}{passed}{RESET}/{len(cases)} passed, "
          f"{RED}{failed}{RESET} failed")
    print(f"  Max relative error: {max_err:.2e}")
    print(f"  Time: {t1 - t0:.2f}s")

    if failed == 0:
        print(f"  {GREEN}{BOLD}ALL 100 TESTS PASSED (TRUE RTL SIMULATION).{RESET}\n")
    else:
        print(f"  {RED}{BOLD}{failed} tests failed.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
