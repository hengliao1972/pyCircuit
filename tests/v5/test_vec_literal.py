"""vec() literal/acceptance tests.

Covers Circuit.vec (Wire layer). CycleAwareDomain.vec is exercised in
test_v5_vector_constructor_with_literals after the v5.py change.
"""
from __future__ import annotations

import pytest

from pycircuit import u, s, U, S
from pycircuit.data import Bits, Vector
from pycircuit.hw import Circuit


def test_vec_accepts_typed_literals() -> None:
    """m.vec(u(8,1), u(8,2), u(8,3)) builds vector<3xi8>."""
    c = Circuit("vec_typed_lit")
    v = c.vec(u(8, 1), u(8, 2), u(8, 3))

    assert v.ty == Vector(3, Bits(8))
    assert v.signed is False
    mlir = c.emit_mlir()
    assert "pyc.v_create" in mlir
    assert "vector<3xi8>" in mlir


def test_vec_accepts_signed_literals() -> None:
    """Signed literals produce a signed vector."""
    c = Circuit("vec_signed_lit")
    v = c.vec(s(8, -1), s(8, 2), s(8, -3))

    assert v.ty == Vector(3, Bits(8))
    assert v.signed is True


def test_vec_accepts_bare_ints_and_infers_width() -> None:
    """m.vec(1, 2, 3) infers width from the widest int lane (3 -> 2 bits)."""
    c = Circuit("vec_bare_int")
    v = c.vec(1, 2, 3)

    assert v.ty == Vector(3, Bits(2))
    assert v.signed is False


def test_vec_accepts_U_S_helpers() -> None:
    """U()/S() (width-less) literals are inferred like bare ints."""
    c = Circuit("vec_US_lit")
    v = c.vec(U(1), U(2), U(3))

    assert v.ty == Vector(3, Bits(2))


def test_vec_literal_list_form() -> None:
    """m.vec([u(8,1), u(8,2)]) works like spread args."""
    c = Circuit("vec_list_lit")
    v = c.vec([u(8, 1), u(8, 2)])

    assert v.ty == Vector(2, Bits(8))


def test_vec_mixed_widthless_literal_and_wire_aligns_to_wire() -> None:
    """A width-less literal (U/S) mixed with a Wire adopts the Wire's width.

    Explicit-width literals (u(8,1)) are authoritative and must match the Wire
    exactly; width-less literals (U(1)) defer to the surrounding context, like
    they do in ``+`` and ``cat``.
    """
    c = Circuit("vec_mixed")
    w = c.input("w", width=16)

    v = c.vec(U(1), w)

    assert v.ty == Vector(2, Bits(16))


def test_vec_explicit_literal_width_mismatch_with_wire_raises() -> None:
    """An explicit-width literal must match the Wire width (no silent resize)."""
    c = Circuit("vec_lit_mismatch")
    w = c.input("w", width=16)

    with pytest.raises(TypeError, match="same"):
        c.vec(u(8, 1), w)


def test_vec_rejects_signed_mismatch() -> None:
    """Mixing signed and unsigned lanes still raises."""
    c = Circuit("vec_signed_mismatch")
    w = c.input("w", width=8)

    with pytest.raises(TypeError, match="signedness"):
        c.vec(s(8, -1), w)


def test_vec_rejects_width_mismatch_between_wires() -> None:
    """Wires of different widths keep raising (literals widen, wires do not)."""
    c = Circuit("vec_width_mismatch")
    w8 = c.input("w8", width=8)
    w4 = c.input("w4", width=4)

    with pytest.raises(TypeError, match="width/signedness|same"):
        c.vec(w8, w4)


def test_vec_rejects_tuple() -> None:
    """A bare tuple argument remains a TypeError (use list)."""
    c = Circuit("vec_tuple")
    with pytest.raises(TypeError, match="tuple"):
        c.vec((u(8, 1), u(8, 2)))


def test_vec_empty_raises() -> None:
    c = Circuit("vec_empty")
    with pytest.raises(ValueError, match="at least one"):
        c.vec()


def test_vec_backward_compat_wires_only() -> None:
    """All-Wire vec (pre-existing usage) is unaffected."""
    c = Circuit("vec_wires_only")
    a = c.input("a", width=8)
    b = c.input("b", width=8)

    v = c.vec(a, b)

    assert v.ty == Vector(2, Bits(8))
    assert "pyc.v_create" in c.emit_mlir()


# ── CycleAwareDomain.vec (v5 layer) ──────────────────────────────────────


def _cycle_domain(name: str):
    from pycircuit import CycleAwareCircuit

    m = CycleAwareCircuit(name)
    return m, m.create_domain("clk")


def test_cas_vec_accepts_literals() -> None:
    """domain.vec(u(8,1), u(8,2)) builds vector<2xi8> at cycle 0."""
    from pycircuit import u

    _, domain = _cycle_domain("cas_vec_lit")

    v = domain.vec(u(8, 1), u(8, 2))

    assert v.ty == Vector(2, Bits(8))
    assert v.cycle == 0


def test_cas_vec_accepts_bare_ints() -> None:
    _, domain = _cycle_domain("cas_vec_int")

    v = domain.vec(1, 2, 3)

    assert v.ty == Vector(3, Bits(2))


def test_cas_vec_aligns_cycles() -> None:
    """A literal at cycle 0 and a signal at cycle 1 align to cycle 1."""
    from pycircuit import cas, u

    _, domain = _cycle_domain("cas_vec_align")
    early_lit = u(8, 1)
    domain.next()
    late_sig = cas(domain, domain.create_signal("late", width=8))

    v = domain.vec(early_lit, late_sig)

    assert v.cycle == 1
    assert v.ty == Vector(2, Bits(8))


def test_cas_vec_mixed_widthless_literal_and_signal() -> None:
    """Width-less literal adopts the signal's width (parity with Wire layer)."""
    from pycircuit import U, cas

    _, domain = _cycle_domain("cas_vec_mixed")
    sig = cas(domain, domain.create_signal("s", width=16))

    v = domain.vec(U(1), sig)

    assert v.ty == Vector(2, Bits(16))
