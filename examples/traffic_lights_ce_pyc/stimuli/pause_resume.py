"""Pause/resume stimulus: toggles go while running."""


def total_seconds() -> int:
    return 140


def sleep_s() -> float:
    return 0.08


def init(rtl) -> None:
    rtl.go = 1
    rtl.emergency = 0


def step(sec: int, rtl) -> None:
    if sec == 50:
        rtl.go = 0
    if sec == 65:
        rtl.go = 1
