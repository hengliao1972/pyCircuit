"""Gate for optional pipeline-stage naming (agentic optimizer §5.3/§5.4, TODO A3).

The attribution chain (NDF clause ↔ source ↔ **pipeline stage** ↔ netlist ↔ PPA)
needs a stable stage key. Signals already carry an integer ``.cycle``; this adds an
optional human-readable stage *name* per cycle:

- ``domain.name_stage("fetch")`` names the current cycle;
- ``domain.next(stage="decode")`` advances and names the entered cycle;
- ``sig.stage_name`` / ``fwd.stage_name`` resolve the name (or ``None``);
- the cycle→name map is emitted as the module attr ``pyc.stage_names``.

Naming is metadata only: it must not change emitted hardware (no extra ``pyc.reg``).
"""

from __future__ import annotations

import json
import re

import pytest

from pycircuit import (
    CycleAwareCircuit,
    cas,
    compile_cycle_aware,
    wire_of,
)


def _extract_stage_names(mlir: str) -> dict | None:
    key = "pyc.stage_names = "
    i = mlir.find(key)
    if i < 0:
        return None
    j = i + len(key)
    assert mlir[j] == '"'
    j += 1
    out: list[str] = []
    while j < len(mlir):
        c = mlir[j]
        if c == "\\":
            out.append(mlir[j + 1])
            j += 2
            continue
        if c == '"':
            break
        out.append(c)
        j += 1
    return json.loads("".join(out))


_REG_RE = re.compile(r"=\s*pyc\.reg\b")


def _pipeline(m, domain):
    domain.name_stage("fetch")
    a = cas(domain, m.input("a", width=8))
    pc = domain.signal(width=8, name="pc")  # forward signal declared in fetch
    decoded = a + 1

    domain.next(stage="decode")
    renamed = decoded + 2

    domain.next(stage="execute")
    result = renamed + 3
    m.output("y", wire_of(result))
    # expose handles for assertions via the returned dict
    return {"a": a, "renamed": renamed, "pc": pc, "result": result}


def test_stage_name_resolution_on_signals() -> None:
    captured: dict = {}

    def top(m, domain):
        captured.update(_pipeline(m, domain))

    compile_cycle_aware(top, name="pipe", eager=True)

    assert captured["a"].stage_name == "fetch"
    assert captured["a"].cycle == 0
    assert captured["pc"].stage_name == "fetch"  # ForwardSignal delegation
    assert captured["renamed"].stage_name == "decode"
    assert captured["renamed"].cycle == 1


@pytest.mark.parametrize("hierarchical", [False, True], ids=["flat", "hier"])
def test_stage_names_emitted_as_module_attr(hierarchical: bool) -> None:
    circ = compile_cycle_aware(_pipeline, name="pipe", eager=True, hierarchical=hierarchical)
    mlir = circ.emit_mlir()
    assert _extract_stage_names(mlir) == {"0": "fetch", "1": "decode", "2": "execute"}


def test_stage_naming_is_metadata_only() -> None:
    """Naming stages must not change the emitted hardware (same reg count)."""

    def named(m, domain):
        domain.name_stage("fetch")
        a = cas(domain, m.input("a", width=8))
        d = a + 1
        domain.next(stage="decode")
        m.output("y", wire_of(d + 2))

    def unnamed(m, domain):
        a = cas(domain, m.input("a", width=8))
        d = a + 1
        domain.next()
        m.output("y", wire_of(d + 2))

    n = len(_REG_RE.findall(compile_cycle_aware(named, name="p", eager=True).emit_mlir()))
    u = len(_REG_RE.findall(compile_cycle_aware(unnamed, name="p", eager=True).emit_mlir()))
    assert n == u


def test_unnamed_pipeline_omits_stage_attr() -> None:
    def top(m, domain):
        a = cas(domain, m.input("a", width=8))
        domain.next()
        m.output("y", wire_of(a + 1))

    mlir = compile_cycle_aware(top, name="p", eager=True).emit_mlir()
    assert "pyc.stage_names" not in mlir


def test_conflicting_stage_name_raises() -> None:
    m = CycleAwareCircuit("x")
    d = m.create_domain("clk")
    d.name_stage("fetch")
    d.name_stage("fetch")  # idempotent: same name at same cycle is fine
    with pytest.raises(ValueError, match="conflicting stage names"):
        d.name_stage("decode")


def test_empty_stage_name_rejected() -> None:
    m = CycleAwareCircuit("x")
    d = m.create_domain("clk")
    with pytest.raises(ValueError, match="non-empty"):
        d.name_stage("   ")


_REG_LINE_RE = re.compile(r"pyc\.reg\b.*")
_STAGE_ATTR_RE = re.compile(r'pyc\.stage = "([^"]*)"')


def _reg_stage_attrs(mlir: str) -> list[str | None]:
    """Return the pyc.stage value (or None) for each pyc.reg line, in order."""
    out: list[str | None] = []
    for line in mlir.splitlines():
        if "pyc.reg" not in line:
            continue
        mm = _STAGE_ATTR_RE.search(line)
        out.append(mm.group(1) if mm else None)
    return out


def test_reg_ops_carry_stage_attr() -> None:
    """Named stages propagate onto the emitted pyc.reg ops as pyc.stage attrs."""

    def dp(m, domain):
        domain.name_stage("fetch")
        a = cas(domain, m.input("a", width=8))
        pc = domain.signal(width=8, name="pc")  # feedback reg declared in fetch
        d = a + 1
        domain.next(stage="decode")
        r = d + 2  # d(cyc0) auto-balanced into decode
        domain.next(stage="execute")
        e = r + 3  # r(cyc1) auto-balanced into execute
        pc <<= e[0:8]
        m.output("y", wire_of(e))

    mlir = compile_cycle_aware(dp, name="dp", eager=True).emit_mlir()
    stages = _reg_stage_attrs(mlir)
    # pc feedback reg -> fetch; two auto-balance regs -> decode, execute.
    assert set(stages) == {"fetch", "decode", "execute"}
    assert None not in stages


def test_unnamed_regs_have_no_stage_attr() -> None:
    def dp(m, domain):
        a = cas(domain, m.input("a", width=8))
        d = a + 1
        domain.next()
        m.output("y", wire_of(d + 2))

    mlir = compile_cycle_aware(dp, name="dp", eager=True).emit_mlir()
    assert _reg_stage_attrs(mlir) == [None]
    assert "pyc.stage" not in mlir


def test_stage_name_of_and_snapshot() -> None:
    m = CycleAwareCircuit("x")
    d = m.create_domain("clk")
    d.name_stage("fetch")
    d.next(stage="decode")
    assert d.stage_name_of(0) == "fetch"
    assert d.stage_name_of(1) == "decode"
    assert d.stage_name_of(2) is None
    assert d.stage_names == {0: "fetch", 1: "decode"}
