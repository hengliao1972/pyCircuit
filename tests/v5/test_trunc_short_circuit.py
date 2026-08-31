"""Frontend trunc short-circuit tests.

Verifies that `Module.trunc` aligns with the MLIR trunc verifier
(`verifyIntCast` in PYCOps.cpp): width == input is a legal identity cast
and must not emit a `pyc.trunc` node; width > input still raises; and
width < input behaves as before.
"""
from __future__ import annotations

import pytest

from pycircuit.data import Bits
from pycircuit.dsl import Module
from pycircuit.hw import Circuit


def test_trunc_equal_width_returns_same_signal_and_emits_no_op() -> None:
    """trunc(width == input.width) is an identity short-circuit.

    The returned Signal must be the exact same object (no new SSA temp), and
    the emitted MLIR must contain no `pyc.trunc` line.
    """
    m = Module("trunc_identity")
    a = m.input("a", width=8)

    out = m.trunc(a, width=8)

    assert out is a
    mlir = m.emit_mlir()
    assert "pyc.trunc" not in mlir


def test_trunc_wire_level_equal_width_short_circuits() -> None:
    """Wire.trunc transparently inherits the short-circuit from Module.trunc."""
    c = Circuit("trunc_wire_identity")
    w = c.input("w", width=8)

    out = w.trunc(width=8)

    assert out.sig is w.sig
    assert out.signed == w.signed
    assert "pyc.trunc" not in c.emit_mlir()


def test_trunc_equal_width_preserves_signedness() -> None:
    """A signed Wire stays signed through the equal-width short-circuit."""
    c = Circuit("trunc_signed_identity")
    w = c.input("w", width=8)
    w_signed = c.input("w_signed", width=8, signed=True)

    assert w.trunc(width=8).signed is False
    assert w_signed.trunc(width=8).signed is True


def test_trunc_wider_width_still_raises() -> None:
    """trunc(width > input.width) must keep raising (matches MLIR out > in)."""
    m = Module("trunc_wider")
    a = m.input("a", width=4)

    with pytest.raises(ValueError, match="<= input width"):
        m.trunc(a, width=8)


def test_trunc_narrower_width_behaves_as_before() -> None:
    """trunc(width < input.width) still emits a real pyc.trunc op."""
    m = Module("trunc_narrower")
    a = m.input("a", width=8)

    out = m.trunc(a, width=4)

    assert out is not a
    assert out.ty == Bits(4)
    mlir = m.emit_mlir()
    assert "pyc.trunc" in mlir


def test_trunc_equal_width_on_parameterized_code() -> None:
    """Simulates the parameterized-code scenario the fix targets.

    When `width` is derived from a runtime value (e.g. bit_length of a size),
    it may coincidentally equal the input width. The short-circuit makes that
    path legal instead of raising mid-compile.
    """
    c = Circuit("trunc_param")
    w = c.input("w", width=8)

    for size in (64, 128, 255):
        width = max(1, size.bit_length())  # 7, 8, 8
        # For size=128, width == 8 == w.width: previously raised, now no-op.
        out = w.trunc(width=min(width, w.sig.ty.width))
        assert out.sig is w.sig or out.sig.ty == Bits(min(width, 8))
