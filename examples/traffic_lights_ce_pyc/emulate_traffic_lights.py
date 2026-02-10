#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emulate_traffic_lights.py — True RTL simulation of the traffic lights
with a terminal visualization.

Build the shared library first:
  cd <pyCircuit root>
  c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
      -o examples/traffic_lights_ce_pyc/libtraffic_lights_sim.dylib \
      examples/traffic_lights_ce_pyc/traffic_lights_capi.cpp

Then run:
  python examples/traffic_lights_ce_pyc/emulate_traffic_lights.py
"""
from __future__ import annotations

import argparse
import ctypes
import importlib
import sys
import time
from pathlib import Path

# =============================================================================
# ANSI helpers
# =============================================================================

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
WHITE = "\033[37m"
CYAN = "\033[36m"


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


# =============================================================================
# 7-segment ASCII art
# =============================================================================

_SEG = {
    0: (" _ ", "| |", "|_|"),
    1: ("   ", "  |", "  |"),
    2: (" _ ", " _|", "|_ "),
    3: (" _ ", " _|", " _|"),
    4: ("   ", "|_|", "  |"),
    5: (" _ ", "|_ ", " _|"),
    6: (" _ ", "|_ ", "|_|"),
    7: (" _ ", "  |", "  |"),
    8: (" _ ", "|_|", "|_|"),
    9: (" _ ", "|_|", " _|"),
}


def _digit_rows(d: int, color: str = WHITE) -> list[str]:
    rows = _SEG.get(d, _SEG[0])
    return [f"{color}{r}{RESET}" for r in rows]


def _light(on: int, color: str, label: str) -> str:
    return f"{color}{label}{RESET}" if on else f"{DIM}{label}{RESET}"


# =============================================================================
# RTL simulation wrapper (ctypes -> compiled C++ netlist)
# =============================================================================

# Must match the CLK_FREQ used when generating the RTL for this demo.
RTL_CLK_FREQ = 1000


class TrafficLightsRTL:
    def __init__(self, lib_path: str | None = None):
        if lib_path is None:
            lib_path = str(Path(__file__).resolve().parent / "libtraffic_lights_sim.dylib")
        self._lib = ctypes.CDLL(lib_path)

        self._lib.tl_create.restype = ctypes.c_void_p
        self._lib.tl_destroy.argtypes = [ctypes.c_void_p]
        self._lib.tl_reset.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self._lib.tl_set_inputs.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._lib.tl_tick.argtypes = [ctypes.c_void_p]
        self._lib.tl_run_cycles.argtypes = [ctypes.c_void_p, ctypes.c_uint64]

        for name in (
            "tl_get_ew_bcd", "tl_get_ns_bcd",
            "tl_get_ew_red", "tl_get_ew_yellow", "tl_get_ew_green",
            "tl_get_ns_red", "tl_get_ns_yellow", "tl_get_ns_green",
        ):
            getattr(self._lib, name).argtypes = [ctypes.c_void_p]
            getattr(self._lib, name).restype = ctypes.c_uint32

        self._lib.tl_get_cycle.argtypes = [ctypes.c_void_p]
        self._lib.tl_get_cycle.restype = ctypes.c_uint64

        self._ctx = self._lib.tl_create()
        self.go = 0
        self.emergency = 0

    def __del__(self):
        if hasattr(self, "_ctx") and self._ctx:
            self._lib.tl_destroy(self._ctx)

    def reset(self, cycles: int = 2):
        self._lib.tl_reset(self._ctx, cycles)

    def _apply_inputs(self):
        self._lib.tl_set_inputs(self._ctx, self.go, self.emergency)

    def tick(self):
        self._apply_inputs()
        self._lib.tl_tick(self._ctx)

    def run_cycles(self, n: int):
        self._apply_inputs()
        self._lib.tl_run_cycles(self._ctx, n)

    @property
    def ew_bcd(self) -> tuple[int, int]:
        v = self._lib.tl_get_ew_bcd(self._ctx)
        return ((v >> 4) & 0xF, v & 0xF)

    @property
    def ns_bcd(self) -> tuple[int, int]:
        v = self._lib.tl_get_ns_bcd(self._ctx)
        return ((v >> 4) & 0xF, v & 0xF)

    @property
    def ew_lights(self) -> tuple[int, int, int]:
        return (
            int(self._lib.tl_get_ew_red(self._ctx)),
            int(self._lib.tl_get_ew_yellow(self._ctx)),
            int(self._lib.tl_get_ew_green(self._ctx)),
        )

    @property
    def ns_lights(self) -> tuple[int, int, int]:
        return (
            int(self._lib.tl_get_ns_red(self._ctx)),
            int(self._lib.tl_get_ns_yellow(self._ctx)),
            int(self._lib.tl_get_ns_green(self._ctx)),
        )

    @property
    def cycle(self) -> int:
        return int(self._lib.tl_get_cycle(self._ctx))


# =============================================================================
# Rendering
# =============================================================================


def render_direction(label: str, tens: int, ones: int, lights: tuple[int, int, int]) -> list[str]:
    r, y, g = lights
    lights_str = " ".join([
        _light(r, RED, "R"),
        _light(y, YELLOW, "Y"),
        _light(g, GREEN, "G"),
    ])
    header = f"{BOLD}{label}{RESET}  {lights_str}"

    d0 = _digit_rows(tens, WHITE)
    d1 = _digit_rows(ones, WHITE)

    lines = [header]
    for i in range(3):
        lines.append(f"  {d0[i]} {d1[i]}")
    return lines


def _load_stimulus(name: str):
    if "." in name:
        return importlib.import_module(name)
    try:
        return importlib.import_module(f"examples.traffic_lights_ce_pyc.stimuli.{name}")
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root))
        return importlib.import_module(f"examples.traffic_lights_ce_pyc.stimuli.{name}")


def main():
    ap = argparse.ArgumentParser(description="Traffic lights terminal emulator")
    ap.add_argument(
        "--stim",
        default="emergency_pulse",
        help="Stimulus module name (e.g. basic, emergency_pulse, pause_resume)",
    )
    args = ap.parse_args()

    stim = _load_stimulus(args.stim)

    rtl = TrafficLightsRTL()
    rtl.reset()
    if hasattr(stim, "init"):
        stim.init(rtl)
    else:
        rtl.go = 1
        rtl.emergency = 0

    total_seconds = int(getattr(stim, "total_seconds", lambda: 120)())
    sleep_s = float(getattr(stim, "sleep_s", lambda: 0.08)())

    for sec in range(total_seconds):
        if hasattr(stim, "step"):
            stim.step(sec, rtl)

        clear_screen()
        ew_t, ew_o = rtl.ew_bcd
        ns_t, ns_o = rtl.ns_bcd

        ew_lines = render_direction("EW", ew_t, ew_o, rtl.ew_lights)
        ns_lines = render_direction("NS", ns_t, ns_o, rtl.ns_lights)

        print(f"{CYAN}traffic_lights_ce_pyc{RESET}  cycle={rtl.cycle}  sec={sec}")
        print(f"go={rtl.go}  emergency={rtl.emergency}  CLK_FREQ={RTL_CLK_FREQ}")
        print("")
        for line in ew_lines:
            print(line)
        print("")
        for line in ns_lines:
            print(line)

        rtl.run_cycles(RTL_CLK_FREQ)
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
