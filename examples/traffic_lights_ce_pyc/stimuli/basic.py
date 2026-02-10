"""Basic stimulus: run continuously with no interruptions."""


def total_seconds() -> int:
    return 120


def sleep_s() -> float:
    return 0.08


def init(rtl) -> None:
    rtl.go = 1
    rtl.emergency = 0


def step(sec: int, rtl) -> None:
    _ = sec
    _ = rtl
    # No changes during run.
