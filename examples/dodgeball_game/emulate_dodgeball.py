#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emulate_dodgeball.py — True RTL simulation of the dodgeball game
with a terminal visualization.

By default the script will build the C++ simulation library if missing.
Use --rebuild to force regeneration.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib
import os
import shutil
import subprocess
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
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
WHITE = "\033[37m"


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


# =============================================================================
# RTL simulation wrapper (ctypes -> compiled C++ netlist)
# =============================================================================

MAIN_CLK_BIT = 20
CYCLES_PER_TICK = 1 << (MAIN_CLK_BIT + 1)


class DodgeballRTL:
    def __init__(self, lib_path: str | None = None):
        if lib_path is None:
            lib_path = str(Path(__file__).resolve().parent / "libdodgeball_sim.dylib")
        self._lib = ctypes.CDLL(lib_path)

        self._lib.db_create.restype = ctypes.c_void_p
        self._lib.db_destroy.argtypes = [ctypes.c_void_p]
        self._lib.db_reset.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        self._lib.db_set_inputs.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self._lib.db_tick.argtypes = [ctypes.c_void_p]
        self._lib.db_run_cycles.argtypes = [ctypes.c_void_p, ctypes.c_uint64]

        for name in (
            "db_get_state", "db_get_j", "db_get_player_x",
            "db_get_ob1_x", "db_get_ob1_y",
            "db_get_ob2_x", "db_get_ob2_y",
            "db_get_ob3_x", "db_get_ob3_y",
            "db_get_vga_hs", "db_get_vga_vs",
            "db_get_vga_r", "db_get_vga_g", "db_get_vga_b",
        ):
            getattr(self._lib, name).argtypes = [ctypes.c_void_p]
            getattr(self._lib, name).restype = ctypes.c_uint32

        self._lib.db_get_cycle.argtypes = [ctypes.c_void_p]
        self._lib.db_get_cycle.restype = ctypes.c_uint64

        self._ctx = self._lib.db_create()
        self.rst_btn = 0
        self.start = 0
        self.left = 0
        self.right = 0

    def __del__(self):
        if hasattr(self, "_ctx") and self._ctx:
            self._lib.db_destroy(self._ctx)

    def reset(self, cycles: int = 2):
        self._lib.db_reset(self._ctx, cycles)

    def _apply_inputs(self):
        self._lib.db_set_inputs(self._ctx, self.rst_btn, self.start, self.left, self.right)

    def tick(self):
        self._apply_inputs()
        self._lib.db_tick(self._ctx)

    def run_cycles(self, n: int):
        self._apply_inputs()
        self._lib.db_run_cycles(self._ctx, n)

    @property
    def state(self) -> int:
        return int(self._lib.db_get_state(self._ctx))

    @property
    def j(self) -> int:
        return int(self._lib.db_get_j(self._ctx))

    @property
    def player_x(self) -> int:
        return int(self._lib.db_get_player_x(self._ctx))

    @property
    def ob1(self) -> tuple[int, int]:
        return (int(self._lib.db_get_ob1_x(self._ctx)), int(self._lib.db_get_ob1_y(self._ctx)))

    @property
    def ob2(self) -> tuple[int, int]:
        return (int(self._lib.db_get_ob2_x(self._ctx)), int(self._lib.db_get_ob2_y(self._ctx)))

    @property
    def ob3(self) -> tuple[int, int]:
        return (int(self._lib.db_get_ob3_x(self._ctx)), int(self._lib.db_get_ob3_y(self._ctx)))

    @property
    def cycle(self) -> int:
        return int(self._lib.db_get_cycle(self._ctx))


# =============================================================================
# Build helpers
# =============================================================================


def _find_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _find_pyc_compile(root: Path) -> Path:
    candidates = [
        root / "build-top" / "bin" / "pyc-compile",
        root / "build" / "bin" / "pyc-compile",
        root / "pyc" / "mlir" / "build" / "bin" / "pyc-compile",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    found = shutil.which("pyc-compile")
    if found:
        return Path(found)
    raise RuntimeError("missing pyc-compile (build it with: scripts/pyc build)")


def _ensure_built(force: bool = False) -> None:
    root = _find_root()
    lib_path = Path(__file__).resolve().parent / "libdodgeball_sim.dylib"
    srcs = [
        root / "examples" / "dodgeball_game" / "lab_final_top.py",
        root / "examples" / "dodgeball_game" / "lab_final_VGA.py",
        root / "examples" / "dodgeball_game" / "dodgeball_capi.cpp",
    ]
    if lib_path.exists() and not force:
        lib_mtime = lib_path.stat().st_mtime
        if all(s.exists() and s.stat().st_mtime <= lib_mtime for s in srcs):
            return

    gen_dir = root / "examples" / "generated" / "dodgeball_game"
    gen_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    py_path = f"{root}/python:{root}"
    if env.get("PYTHONPATH"):
        py_path = f"{py_path}:{env['PYTHONPATH']}"
    env["PYTHONPATH"] = py_path

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pycircuit.cli",
            "emit",
            "examples/dodgeball_game/lab_final_top.py",
            "-o",
            str(gen_dir / "dodgeball_game.pyc"),
        ],
        cwd=root,
        env=env,
        check=True,
    )

    pyc_compile = _find_pyc_compile(root)
    subprocess.run(
        [
            str(pyc_compile),
            str(gen_dir / "dodgeball_game.pyc"),
            "--emit=cpp",
            f"--out-dir={gen_dir}",
        ],
        cwd=root,
        check=True,
    )

    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-O2",
            "-shared",
            "-fPIC",
            "-I",
            "include",
            "-I",
            ".",
            "-o",
            str(lib_path),
            "examples/dodgeball_game/dodgeball_capi.cpp",
        ],
        cwd=root,
        check=True,
    )


# =============================================================================
# Rendering (downsampled VGA)
# =============================================================================

ACTIVE_W = 640
ACTIVE_H = 480
SCALE_X = 40
SCALE_Y = 40
GRID_W = ACTIVE_W // SCALE_X
GRID_H = ACTIVE_H // SCALE_Y

_COLOR = {
    (0, 0, 0): f"{DIM}.{RESET}",
    (1, 0, 0): f"{RED}#{RESET}",
    (0, 1, 0): f"{GREEN}#{RESET}",
    (0, 0, 1): f"{BLUE}#{RESET}",
    (1, 1, 0): f"{YELLOW}#{RESET}",
    (1, 0, 1): f"{RED}#{RESET}",
    (0, 1, 1): f"{CYAN}#{RESET}",
    (1, 1, 1): f"{WHITE}#{RESET}",
}

STATE_NAMES = {
    0: "INIT",
    1: "PLAY",
    2: "OVER",
}


def _vga_color_at(
    x: int,
    y: int,
    *,
    state: int,
    player_x: int,
    objects: list[tuple[int, int]],
) -> tuple[int, int, int]:
    def in_range(v: int, lo: int, hi: int) -> bool:
        return (v > lo) and (v < hi)

    sq_player = (
        in_range(x, 40 * player_x, 40 * (player_x + 1)) and
        in_range(y, 400, 440)
    )

    def sq_object(ox: int, oy: int) -> bool:
        return (
            in_range(x, 40 * ox, 40 * (ox + 1)) and
            in_range(y, 40 * oy, 40 * (oy + 1))
        )

    sq_obj1 = sq_object(*objects[0])
    sq_obj2 = sq_object(*objects[1])
    sq_obj3 = sq_object(*objects[2])

    over_wire = in_range(x, 0, 640) and in_range(y, 0, 480)
    down = in_range(x, 0, 640) and in_range(y, 440, 480)
    up = in_range(x, 0, 640) and in_range(y, 0, 40)

    over = (state == 2)
    not_over = not over

    r = 1 if (sq_player and not_over) else 0
    b = 1 if ((sq_obj1 or sq_obj2 or sq_obj3 or down or up) and not_over) else 0
    g = 1 if (over_wire and over) else 0
    return (r, g, b)


def render_vga_sampled(state: int, player_x: int, objects: list[tuple[int, int]]) -> list[str]:
    lines: list[str] = []
    for row in range(GRID_H):
        y = row * SCALE_Y + (SCALE_Y // 2)
        line = []
        for col in range(GRID_W):
            x = col * SCALE_X + (SCALE_X // 2)
            rgb = _vga_color_at(x, y, state=state, player_x=player_x, objects=objects)
            line.append(_COLOR.get(rgb, _COLOR[(0, 0, 0)]))
        lines.append("".join(line))
    return lines


# =============================================================================
# Stimulus loading
# =============================================================================


def _load_stimulus(name: str):
    if "." in name:
        return importlib.import_module(name)
    try:
        return importlib.import_module(f"examples.dodgeball_game.stimuli.{name}")
    except ModuleNotFoundError:
        root = _find_root()
        sys.path.insert(0, str(root))
        return importlib.import_module(f"examples.dodgeball_game.stimuli.{name}")


def main():
    ap = argparse.ArgumentParser(description="Dodgeball terminal emulator")
    ap.add_argument(
        "--stim",
        default="basic",
        help="Stimulus module name (e.g. basic)",
    )
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild of the C++ simulation library",
    )
    args = ap.parse_args()

    _ensure_built(force=args.rebuild)

    stim = _load_stimulus(args.stim)

    rtl = DodgeballRTL()
    rtl.reset()
    if hasattr(stim, "init"):
        stim.init(rtl)

    total_ticks = int(getattr(stim, "total_ticks", lambda: 20)())
    frame_sleep = float(getattr(stim, "sleep_s", lambda: 0.08)())

    for tick in range(total_ticks):
        if hasattr(stim, "step"):
            stim.step(tick, rtl)
        rtl.run_cycles(CYCLES_PER_TICK)

        clear_screen()

        state_name = STATE_NAMES.get(rtl.state, f"S{rtl.state}")
        objs = [rtl.ob1, rtl.ob2, rtl.ob3]
        grid_lines = render_vga_sampled(rtl.state, rtl.player_x, objs)

        print(f"{BOLD}{CYAN}dodgeball_game{RESET}  tick={tick}")
        print(f"cycle={rtl.cycle}  state={state_name}  j={rtl.j}  main_clk_bit={MAIN_CLK_BIT}")
        print(f"RST_BTN={rtl.rst_btn}  START={rtl.start}  left={rtl.left}  right={rtl.right}")
        print(f"note: VGA shown with {GRID_W}x{GRID_H} downsample")
        print("")
        for line in grid_lines:
            print(line)

        time.sleep(frame_sleep)


if __name__ == "__main__":
    main()
