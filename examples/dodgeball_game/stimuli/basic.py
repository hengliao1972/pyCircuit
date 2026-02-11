# -*- coding: utf-8 -*-
"""Basic stimulus for the dodgeball demo."""
from __future__ import annotations


def init(rtl) -> None:
    rtl.rst_btn = 0
    rtl.start = 0
    rtl.left = 0
    rtl.right = 0


def total_ticks() -> int:
    return 24


def sleep_s() -> float:
    return 0.08


def step(tick: int, rtl) -> None:
    # Start the game at tick 0
    rtl.start = 1 if tick == 0 else 0

    # Move left for a few ticks, then right
    rtl.left = 1 if 4 <= tick < 7 else 0
    rtl.right = 1 if 9 <= tick < 12 else 0

    # Demonstrate reset and restart
    rtl.rst_btn = 1 if tick == 16 else 0
    if tick == 18:
        rtl.start = 1
