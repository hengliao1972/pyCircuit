#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emulate_filter.py — True RTL simulation of the 4-tap FIR filter
with animated terminal visualization.

Shows the filter structure, delay line contents, coefficients,
input/output waveforms, and step-by-step operation.

Build (from pyCircuit root):
    PYTHONPATH=python:. python -m pycircuit.cli emit \
        examples/digital_filter/digital_filter.py \
        -o examples/generated/digital_filter/digital_filter.pyc
    build/bin/pyc-compile examples/generated/digital_filter/digital_filter.pyc \
        --emit=cpp -o examples/generated/digital_filter/digital_filter_gen.hpp
    c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
        -o examples/digital_filter/libfilter_sim.dylib \
        examples/digital_filter/filter_capi.cpp

Run:
    python examples/digital_filter/emulate_filter.py
"""
from __future__ import annotations

import ctypes
import re as _re
import struct
import sys
import time
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# ANSI
# ═══════════════════════════════════════════════════════════════════
RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
CYAN = "\033[36m"; WHITE = "\033[37m"; MAGENTA = "\033[35m"
BG_GREEN = "\033[42m"; BLACK = "\033[30m"; BLUE = "\033[34m"

_ANSI = _re.compile(r'\x1b\[[0-9;]*m')
def _vl(s): return len(_ANSI.sub('', s))
def _pad(s, w): return s + ' ' * max(0, w - _vl(s))
def clear(): sys.stdout.write("\033[2J\033[H"); sys.stdout.flush()

# ═══════════════════════════════════════════════════════════════════
# Filter coefficients (must match RTL)
# ═══════════════════════════════════════════════════════════════════
COEFFS = (1, 2, 3, 4)
TAPS = len(COEFFS)
DATA_W = 16

# ═══════════════════════════════════════════════════════════════════
# RTL wrapper
# ═══════════════════════════════════════════════════════════════════
class FilterRTL:
    def __init__(self, lib_path=None):
        if lib_path is None:
            lib_path = str(Path(__file__).resolve().parent / "libfilter_sim.dylib")
        L = ctypes.CDLL(lib_path)
        L.fir_create.restype = ctypes.c_void_p
        L.fir_destroy.argtypes = [ctypes.c_void_p]
        L.fir_reset.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        L.fir_push_sample.argtypes = [ctypes.c_void_p, ctypes.c_int16]
        L.fir_idle.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        L.fir_get_y_out.argtypes = [ctypes.c_void_p]; L.fir_get_y_out.restype = ctypes.c_int64
        L.fir_get_y_valid.argtypes = [ctypes.c_void_p]; L.fir_get_y_valid.restype = ctypes.c_uint32
        L.fir_get_cycle.argtypes = [ctypes.c_void_p]; L.fir_get_cycle.restype = ctypes.c_uint64
        self._L, self._c = L, L.fir_create()
        self._delay = [0] * TAPS  # Python-side tracking for display

    def __del__(self):
        if hasattr(self,'_c') and self._c: self._L.fir_destroy(self._c)

    def reset(self):
        self._L.fir_reset(self._c, 2)
        self._delay = [0] * TAPS

    def push(self, sample: int):
        self._L.fir_push_sample(self._c, sample & 0xFFFF)
        # Track delay line for display
        for i in range(TAPS - 1, 0, -1):
            self._delay[i] = self._delay[i - 1]
        self._delay[0] = sample

    def idle(self, n=4):
        self._L.fir_idle(self._c, n)

    @property
    def y_out(self):
        raw = self._L.fir_get_y_out(self._c)
        # Sign-extend from ACC_W bits
        ACC_W = DATA_W + 16 + (TAPS - 1).bit_length()
        if raw >= (1 << (ACC_W - 1)):
            raw -= (1 << ACC_W)
        return raw

    @property
    def y_valid(self): return bool(self._L.fir_get_y_valid(self._c))
    @property
    def cycle(self): return self._L.fir_get_cycle(self._c)

    def expected_output(self):
        """Compute expected y using Python for verification."""
        return sum(self._delay[i] * COEFFS[i] for i in range(TAPS))

# ═══════════════════════════════════════════════════════════════════
# Terminal UI
# ═══════════════════════════════════════════════════════════════════
BOX_W = 64

def _bl(content):
    return f"  {CYAN}║{RESET}{_pad(content, BOX_W)}{CYAN}║{RESET}"

def _bar_char(val, max_abs, width=20):
    """Render a horizontal bar for a signed value."""
    if max_abs == 0: max_abs = 1
    half = width // 2
    pos = int(abs(val) / max_abs * half)
    pos = min(pos, half)
    if val >= 0:
        bar = " " * half + "│" + f"{GREEN}{'█' * pos}{RESET}" + " " * (half - pos)
    else:
        bar = " " * (half - pos) + f"{RED}{'█' * pos}{RESET}" + "│" + " " * half
    return bar

def draw(sim, x_history, y_history, message="", test_info="", step=-1):
    clear()
    bar = "═" * BOX_W

    print(f"\n  {CYAN}╔{bar}╗{RESET}")
    print(_bl(f"  {BOLD}{WHITE}4-TAP FIR FILTER — TRUE RTL SIMULATION{RESET}"))
    print(f"  {CYAN}╠{bar}╣{RESET}")

    if test_info:
        print(_bl(f"  {YELLOW}{test_info}{RESET}"))
        print(f"  {CYAN}╠{bar}╣{RESET}")

    # Filter structure diagram
    print(_bl(""))
    print(_bl(f"  {BOLD}y[n] = c0·x[n] + c1·x[n-1] + c2·x[n-2] + c3·x[n-3]{RESET}"))
    print(_bl(f"  {DIM}Coefficients: c0={COEFFS[0]}, c1={COEFFS[1]}, c2={COEFFS[2]}, c3={COEFFS[3]}{RESET}"))
    print(_bl(""))

    # Delay line contents
    print(_bl(f"  {BOLD}{CYAN}Delay Line:{RESET}"))
    for i in range(TAPS):
        tag = "x[n]  " if i == 0 else f"x[n-{i}]"
        val = sim._delay[i]
        coef = COEFFS[i]
        prod = val * coef
        vc = f"{GREEN}" if val >= 0 else f"{RED}"
        pc = f"{GREEN}" if prod >= 0 else f"{RED}"
        print(_bl(f"    {tag} = {vc}{val:>7}{RESET}  × c{i}={coef:>3}  = {pc}{prod:>10}{RESET}"))

    expected = sim.expected_output()
    actual = sim.y_out
    match = actual == expected
    mc = GREEN if match else RED

    print(_bl(f"    {'─' * 48}"))
    print(_bl(f"    {BOLD}y_out = {mc}{actual:>10}{RESET}   "
              f"(expected: {expected:>10}  {'✓' if match else '✗'})"))
    print(_bl(""))

    # Waveform display (last 16 samples)
    WAVE_LEN = 16
    max_x = max((abs(v) for v in x_history[-WAVE_LEN:]), default=1) or 1
    max_y = max((abs(v) for v in y_history[-WAVE_LEN:]), default=1) or 1
    max_all = max(max_x, max_y)

    print(_bl(f"  {BOLD}{CYAN}Input Waveform (last {min(len(x_history), WAVE_LEN)} samples):{RESET}"))
    for v in x_history[-WAVE_LEN:]:
        print(_bl(f"    {v:>7} {_bar_char(v, max_all)}"))

    print(_bl(""))
    print(_bl(f"  {BOLD}{CYAN}Output Waveform:{RESET}"))
    for v in y_history[-WAVE_LEN:]:
        print(_bl(f"    {v:>7} {_bar_char(v, max_all)}"))

    print(_bl(""))
    print(_bl(f"  Cycle: {DIM}{sim.cycle}{RESET}"))

    if message:
        print(f"  {CYAN}╠{bar}╣{RESET}")
        print(_bl(f"  {BOLD}{WHITE}{message}{RESET}"))
    print(f"  {CYAN}╚{bar}╝{RESET}")
    print()


# ═══════════════════════════════════════════════════════════════════
# Test scenarios
# ═══════════════════════════════════════════════════════════════════

def main():
    print("  Loading FIR filter RTL simulation...")
    sim = FilterRTL()
    sim.reset()
    sim.idle(4)
    print(f"  {GREEN}RTL model loaded. Coefficients: {COEFFS}{RESET}")
    time.sleep(0.5)

    x_hist = []
    y_hist = []
    all_ok = True

    def run_scenario(name, num, inputs, sim, x_hist, y_hist):
        """Run a filter test scenario. Returns True if all outputs match.

        The RTL output is registered (1-cycle latency): after pushing x[n],
        the y_out we read corresponds to the computation from x[n]'s state
        (delay line updated, then combinational result captured).
        We compare against the Python model which tracks the delay line
        identically.
        """
        nonlocal all_ok
        sim.reset(); x_hist.clear(); y_hist.clear()
        info = f"Test {num}: {name}"

        draw(sim, x_hist, y_hist, name, test_info=info)
        time.sleep(0.8)

        ok_all = True
        for i, x in enumerate(inputs):
            sim.push(x)
            x_hist.append(x)
            y = sim.y_out
            y_hist.append(y)
            exp = sim.expected_output()
            ok = (y == exp)
            if not ok:
                ok_all = False
                all_ok = False
            st = f"{GREEN}✓{RESET}" if ok else f"{RED}✗ exp {exp}{RESET}"
            draw(sim, x_hist, y_hist,
                 f"Push x={x:>6}, y={y:>8} {st}",
                 test_info=info)
            time.sleep(0.5)

        result = f"{GREEN}PASS{RESET}" if ok_all else f"{RED}FAIL{RESET}"
        draw(sim, x_hist, y_hist,
             f"{name} — {result}", test_info=info)
        time.sleep(0.8)
        return ok_all

    # ── Test 1: Impulse ──────────────────────────────────────
    run_scenario("Impulse [1, 0, 0, 0, 0, 0, 0, 0]", 1,
                 [1, 0, 0, 0, 0, 0, 0, 0], sim, x_hist, y_hist)

    # ── Test 2: Step ─────────────────────────────────────────
    run_scenario("Step [1, 1, 1, 1, 1, 1, 1, 1]", 2,
                 [1]*8, sim, x_hist, y_hist)

    # ── Test 3: Ramp ─────────────────────────────────────────
    run_scenario("Ramp [0, 1, 2, 3, 4, 5, 6, 7]", 3,
                 list(range(8)), sim, x_hist, y_hist)

    # ── Test 4: Alternating ±100 ─────────────────────────────
    run_scenario("Alternating ±100", 4,
                 [100, -100, 100, -100, 100, -100, 100, -100],
                 sim, x_hist, y_hist)

    # ── Test 5: Large values ─────────────────────────────────
    run_scenario("Large values (10000)", 5,
                 [10000, 10000, 10000, 10000, 0, 0, 0, 0],
                 sim, x_hist, y_hist)

    # ── Summary ──────────────────────────────────────────────
    if all_ok:
        draw(sim, x_hist, y_hist,
             f"All 5 tests PASSED! Filter verified against RTL.",
             test_info="Complete")
        time.sleep(2.0)
        print(f"  {GREEN}{BOLD}All tests passed (TRUE RTL SIMULATION).{RESET}\n")
    else:
        draw(sim, x_hist, y_hist,
             f"{RED}Some tests FAILED!{RESET}",
             test_info="Complete")
        time.sleep(2.0)
        print(f"  {RED}{BOLD}Some tests failed.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
