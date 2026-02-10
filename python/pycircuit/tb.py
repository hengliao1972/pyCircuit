from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class TbError(RuntimeError):
    pass


def _sanitize_id(s: str) -> str:
    out: list[str] = []
    for c in str(s):
        if ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or (c == "_"):
            out.append(c)
        else:
            out.append("_")
    if not out or ("0" <= out[0] <= "9"):
        out.insert(0, "_")
    return "".join(out)


def _unique_names(raw: Iterable[str]) -> list[str]:
    used: dict[str, int] = {}
    out: list[str] = []
    for r in raw:
        base = _sanitize_id(r)
        n = used.get(base, 0) + 1
        used[base] = n
        out.append(base if n == 1 else f"{base}_{n}")
    return out


class SvaExpr:
    def __init__(self, text: str) -> None:
        t = str(text).strip()
        if not t:
            raise TbError("SVA expression must be non-empty")
        self.text = t

    def __str__(self) -> str:
        return self.text

    def _bin(self, op: str, other: Any) -> "SvaExpr":
        o = _as_sva_expr(other)
        return SvaExpr(f"({self}) {op} ({o})")

    def __and__(self, other: Any) -> "SvaExpr":
        return self._bin("&&", other)

    def __or__(self, other: Any) -> "SvaExpr":
        return self._bin("||", other)

    def __add__(self, other: Any) -> "SvaExpr":
        return self._bin("+", other)

    def __sub__(self, other: Any) -> "SvaExpr":
        return self._bin("-", other)

    def __invert__(self) -> "SvaExpr":
        return SvaExpr(f"!({self})")

    def __eq__(self, other: Any) -> "SvaExpr":  # type: ignore[override]
        return self._bin("==", other)

    def __ne__(self, other: Any) -> "SvaExpr":  # type: ignore[override]
        return self._bin("!=", other)

    def __lt__(self, other: Any) -> "SvaExpr":
        return self._bin("<", other)

    def __le__(self, other: Any) -> "SvaExpr":
        return self._bin("<=", other)

    def __gt__(self, other: Any) -> "SvaExpr":
        return self._bin(">", other)

    def __ge__(self, other: Any) -> "SvaExpr":
        return self._bin(">=", other)


def _as_sva_expr(v: Any) -> SvaExpr:
    if isinstance(v, SvaExpr):
        return v
    if isinstance(v, str):
        return SvaExpr(_sanitize_id(v))
    if isinstance(v, bool):
        return SvaExpr("1" if v else "0")
    if isinstance(v, int):
        return SvaExpr(str(int(v)))
    raise TbError(f"unsupported SVA value: {type(v).__name__}")


class sva:
    @staticmethod
    def id(name: str) -> SvaExpr:
        return _as_sva_expr(name)

    @staticmethod
    def past(sig: Any, n: int = 1) -> SvaExpr:
        if int(n) < 1:
            raise TbError("sva.past(n) requires n >= 1")
        s = _as_sva_expr(sig)
        return SvaExpr(f"$past({s}, {int(n)})")

    @staticmethod
    def rose(sig: Any) -> SvaExpr:
        s = _as_sva_expr(sig)
        return SvaExpr(f"$rose({s})")

    @staticmethod
    def fell(sig: Any) -> SvaExpr:
        s = _as_sva_expr(sig)
        return SvaExpr(f"$fell({s})")

    @staticmethod
    def stable(sig: Any) -> SvaExpr:
        s = _as_sva_expr(sig)
        return SvaExpr(f"$stable({s})")


@dataclass(frozen=True)
class ClockSpec:
    port: str
    half_period_steps: int = 1
    phase_steps: int = 0
    start_high: bool = False


@dataclass(frozen=True)
class ResetSpec:
    port: str
    cycles_asserted: int = 2
    cycles_deasserted: int = 1


@dataclass(frozen=True)
class Drive:
    port: str
    value: int | bool
    at: int


@dataclass(frozen=True)
class Expect:
    port: str
    value: int | bool
    at: int
    msg: str | None = None


@dataclass(frozen=True)
class SvaAssert:
    expr: SvaExpr
    clock: str
    reset: str | None = None
    name: str | None = None
    msg: str | None = None


@dataclass(frozen=True)
class RandomStream:
    port: str
    seed: int = 1
    start: int = 0
    every: int = 1


@dataclass
class Tb:
    """A tiny, cycle-based testbench description (prototype).

    This builder is intentionally backend-neutral: it can be rendered into a
    C++ testbench (fast) or a SystemVerilog testbench with SVA.
    """

    clocks: list[ClockSpec] = field(default_factory=list)
    reset_spec: ResetSpec | None = None
    drives: list[Drive] = field(default_factory=list)
    expects: list[Expect] = field(default_factory=list)
    sva_asserts: list[SvaAssert] = field(default_factory=list)
    random_streams: list[RandomStream] = field(default_factory=list)

    timeout_cycles: int = 1000
    finish_cycle: int | None = None

    def clock(self, port: str, *, half_period_steps: int = 1, phase_steps: int = 0, start_high: bool = False) -> None:
        p = str(port).strip()
        if not p:
            raise TbError("clock port must be non-empty")
        hp = int(half_period_steps)
        if hp <= 0:
            raise TbError("half_period_steps must be > 0")
        self.clocks.append(ClockSpec(port=p, half_period_steps=hp, phase_steps=int(phase_steps), start_high=bool(start_high)))

    def reset(self, port: str, *, cycles_asserted: int = 2, cycles_deasserted: int = 1) -> None:
        p = str(port).strip()
        if not p:
            raise TbError("reset port must be non-empty")
        ca = int(cycles_asserted)
        cd = int(cycles_deasserted)
        if ca < 0 or cd < 0:
            raise TbError("reset cycles must be >= 0")
        self.reset_spec = ResetSpec(port=p, cycles_asserted=ca, cycles_deasserted=cd)

    def drive(self, port: str, value: int | bool, *, at: int) -> None:
        p = str(port).strip()
        if not p:
            raise TbError("drive port must be non-empty")
        cyc = int(at)
        if cyc < 0:
            raise TbError("drive cycle must be >= 0")
        if not isinstance(value, (bool, int)):
            raise TbError("drive value must be bool or int")
        self.drives.append(Drive(port=p, value=value, at=cyc))

    def expect(self, port: str, value: int | bool, *, at: int, msg: str | None = None) -> None:
        p = str(port).strip()
        if not p:
            raise TbError("expect port must be non-empty")
        cyc = int(at)
        if cyc < 0:
            raise TbError("expect cycle must be >= 0")
        if not isinstance(value, (bool, int)):
            raise TbError("expect value must be bool or int")
        self.expects.append(Expect(port=p, value=value, at=cyc, msg=(None if msg is None else str(msg))))

    def timeout(self, cycles: int) -> None:
        t = int(cycles)
        if t <= 0:
            raise TbError("timeout cycles must be > 0")
        self.timeout_cycles = t

    def finish(self, *, at: int) -> None:
        cyc = int(at)
        if cyc < 0:
            raise TbError("finish cycle must be >= 0")
        self.finish_cycle = cyc

    def sva_assert(
        self,
        expr: Any,
        *,
        clock: str,
        reset: str | None = None,
        name: str | None = None,
        msg: str | None = None,
    ) -> None:
        e = _as_sva_expr(expr)
        clk = str(clock).strip()
        if not clk:
            raise TbError("sva_assert clock must be non-empty")
        rst = None if reset is None else str(reset).strip()
        if rst == "":
            rst = None
        nm = None if name is None else _sanitize_id(str(name))
        if nm == "":
            nm = None
        self.sva_asserts.append(SvaAssert(expr=e, clock=clk, reset=rst, name=nm, msg=(None if msg is None else str(msg))))

    def random(self, port: str, *, seed: int = 1, start: int = 0, every: int = 1) -> None:
        """Drive an input port with a deterministic pseudo-random stream.

        Notes:
        - The stream is rendered in both the generated C++ and SV testbenches.
        - Random drives are applied before any explicit `drive(...)` calls in the
          same cycle (explicit drives override random).
        """

        p = str(port).strip()
        if not p:
            raise TbError("random port must be non-empty")
        st = int(start)
        if st < 0:
            raise TbError("random start cycle must be >= 0")
        ev = int(every)
        if ev <= 0:
            raise TbError("random every must be > 0")
        self.random_streams.append(RandomStream(port=p, seed=int(seed), start=st, every=ev))
