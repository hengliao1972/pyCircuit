"""Gate for the data-ified cut-point "static-ness contract" (agentic optimizer §2.4.3).

The optimizer loop's innermost mutation is "flip a boolean compile-time constant and
re-JIT". Two properties must hold for that to be a sound, structured search action:

1. **Static-ness contract** — each enabled cut constant folds into exactly one
   pipeline register; the number of ``pyc.reg`` ops emitted equals the number of
   set cut constants (0..n). No dynamic mux, no runtime configurability.
2. **Decision record** — the specialization vector is recorded verbatim in the
   module's ``pyc.params`` attribute (eager path, fixed by TODO A1) so every
   evaluated cut plan is auditable.

This test freezes both as a regression gate over the full ``2^n`` combination space.
"""

from __future__ import annotations

import itertools
import json
import re

import pytest

from pycircuit import cas, compile_cycle_aware, wire_of

# Pre-set candidate cut points (§2.4.3): each name is a compile-time boolean that,
# when True, folds a `domain.next()` into the datapath at that stage boundary.
CUT_NAMES = ("cut_after_decode", "cut_after_rename", "cut_after_issue")


def _datapath(
    m,
    domain,
    *,
    cut_after_decode: bool = False,
    cut_after_rename: bool = False,
    cut_after_issue: bool = False,
):
    """Pure feed-forward datapath with 3 pre-set candidate cut points."""
    a = cas(domain, m.input("a", width=8))

    decoded = a + 1
    if cut_after_decode:
        domain.next()

    renamed = decoded + 2
    if cut_after_rename:
        domain.next()

    issued = renamed + 3
    if cut_after_issue:
        domain.next()

    result = issued + 4
    m.output("y", wire_of(result))


_REG_RE = re.compile(r"=\s*pyc\.reg\b")


def _count_regs(mlir: str) -> int:
    return len(_REG_RE.findall(mlir))


def _extract_pyc_params(mlir: str) -> dict | None:
    """Extract and decode the ``pyc.params`` MLIR string-literal into a dict.

    The attribute is emitted as ``pyc.params = "<escaped-json>"`` where the value
    is itself a JSON object string. Returns ``None`` when the attribute is absent.
    """
    key = "pyc.params = "
    i = mlir.find(key)
    if i < 0:
        return None
    j = i + len(key)
    assert mlir[j] == '"', f"malformed pyc.params attr near: {mlir[j:j + 40]!r}"
    j += 1
    out: list[str] = []
    while j < len(mlir):
        c = mlir[j]
        if c == "\\":  # MLIR/JSON escape: take the next char verbatim
            out.append(mlir[j + 1])
            j += 2
            continue
        if c == '"':
            break
        out.append(c)
        j += 1
    inner = "".join(out)  # the inner JSON object text
    return json.loads(inner)


_COMBOS = list(itertools.product((False, True), repeat=len(CUT_NAMES)))


def _combo_id(combo: tuple[bool, ...]) -> str:
    return "".join("1" if x else "0" for x in combo)


@pytest.mark.parametrize("hierarchical", [False, True], ids=["flat", "hier"])
@pytest.mark.parametrize("combo", _COMBOS, ids=[_combo_id(c) for c in _COMBOS])
def test_cut_point_static_contract(combo: tuple[bool, ...], hierarchical: bool) -> None:
    kwargs = dict(zip(CUT_NAMES, combo))
    n_cuts = sum(combo)

    circ = compile_cycle_aware(
        _datapath,
        name="datapath",
        eager=True,
        hierarchical=hierarchical,
        **kwargs,
    )
    mlir = circ.emit_mlir()

    # (1) static-ness contract: one pipeline reg per enabled cut, nothing more.
    assert _count_regs(mlir) == n_cuts, (
        f"combo={_combo_id(combo)} hierarchical={hierarchical}: "
        f"expected {n_cuts} pyc.reg, got {_count_regs(mlir)}\n{mlir}"
    )

    # (2) decision record: pyc.params reflects the exact specialization vector.
    params = _extract_pyc_params(mlir)
    assert params == kwargs, (
        f"combo={_combo_id(combo)} hierarchical={hierarchical}: "
        f"pyc.params={params!r} != {kwargs!r}"
    )


def test_empty_flat_eager_omits_params() -> None:
    """No kwargs in flat eager mode: emit stays clean (no ``pyc.params`` attr)."""
    circ = compile_cycle_aware(_datapath, name="datapath", eager=True)
    mlir = circ.emit_mlir()
    assert _count_regs(mlir) == 0
    assert "pyc.params" not in mlir
