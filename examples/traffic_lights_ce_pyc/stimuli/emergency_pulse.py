"""Emergency pulse stimulus: inject emergency for a short window."""


def total_seconds() -> int:
    return 140


def sleep_s() -> float:
    return 0.08


def init(rtl) -> None:
    rtl.go = 1
    rtl.emergency = 0


def step(sec: int, rtl) -> None:
    if sec == 60:
        rtl.emergency = 1
    if sec == 72:
        rtl.emergency = 0
