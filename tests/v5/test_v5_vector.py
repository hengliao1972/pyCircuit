from __future__ import annotations

import pytest

from pycircuit import CycleAwareCircuit, cas
from pycircuit.data import Bits, Vector
from pycircuit.dsl import Module
from pycircuit.hw import Circuit


def test_frontend_shape_defaults_are_isolated() -> None:
    """Omitted shapes remain scalar after independent Vector declarations."""
    dsl = Module("shape_defaults")
    assert dsl.input("scalar", width=1).ty == Bits(1)
    assert dsl.input("vector", width=1, shape=[2]).ty == Vector(2, Bits(1))
    assert dsl.input("scalar_again", width=1).ty == Bits(1)

    circuit = Circuit("shape_defaults")
    supplied_shape = [2]
    vector = circuit.input("vector", width=4, shape=supplied_shape)
    supplied_shape.append(3)
    assert vector.ty == Vector(2, Bits(4))
    assert circuit.input("scalar", width=4).ty == Bits(4)


def test_v5_vector_ports_constants_and_cycle_alignment() -> None:
    """V5 keeps Vector shape while aligning values from different cycles."""
    m = CycleAwareCircuit("v5_vector_alignment")
    domain = m.create_domain("clk")

    early = cas(domain, domain.create_signal("early", width=4, shape=[2, 2]))
    constant = cas(domain, domain.create_const([[1, 2], [3, 4]], width=4))
    domain.next()
    late = cas(domain, domain.create_signal("late", width=4, shape=[2, 2]))

    result = early + late
    assert result.cycle == 1
    assert result.ty == Vector.from_shape([2, 2], Bits(4))
    assert constant.ty == Vector.from_shape([2, 2], Bits(4))

    mlir = m.emit_mlir()
    assert "pyc.reg" in mlir
    # Vector values are expanded to scalar lanes when the IR is emitted:
    # packed ports (2x2 lanes of i4 -> i16) and one add per lane.
    assert "vector<" not in mlir
    assert "%early: i16" in mlir
    assert "%late: i16" in mlir
    assert mlir.count("pyc.add ") == 4


def test_v5_vector_operations_delegate_to_wire_api() -> None:
    """V5 Vector wrappers retain cycles across regular Vector operations."""
    m = CycleAwareCircuit("v5_vector_ops")
    domain = m.create_domain("clk")

    values = cas(domain, domain.create_signal("values", width=4, shape=[3]))
    sels = cas(domain, domain.create_signal("sels", width=1, shape=[3]))
    other = cas(domain, domain.create_signal("other", width=4, shape=[3]))

    assert len(values) == 3
    assert [lane.ty for lane in values] == [Bits(4)] * 3
    assert values[1].ty == Bits(4)
    assert values.broadcast(size=2, dim=0).ty == Vector.from_shape([2, 3], Bits(4))
    assert values.reduce_sum().ty == Bits(4)
    assert values.slice(lsb=1, width=2).ty == Vector.from_shape([3], Bits(2))
    assert (values // other).ty == Vector.from_shape([3], Bits(4))
    assert (values % other).ty == Vector.from_shape([3], Bits(4))
    assert (values << 1).ty == Vector.from_shape([3], Bits(4))
    assert values.ult(other).ty == Vector.from_shape([3], Bits(1))
    assert sels.priority_mux(values).ty == Bits(4)


def test_v5_vector_constructor_aligns_scalar_lanes() -> None:
    """Vector construction aligns lanes instead of rejecting mixed cycles."""
    m = CycleAwareCircuit("v5_vector_constructor")
    domain = m.create_domain("clk")
    first = cas(domain, domain.create_signal("first", width=8))
    domain.next()
    second = cas(domain, domain.create_signal("second", width=8))

    vector = domain.vec(first, second)
    assert vector.cycle == 1
    assert vector.ty == Vector(2, Bits(8))
    assert "pyc.reg" in m.emit_mlir()


def test_v5_vector_state_preserves_shape() -> None:
    """A V5 feedback register can hold a Vector value and expose Vector helpers."""
    m = CycleAwareCircuit("v5_vector_state")
    domain = m.create_domain("clk")
    state = domain.signal(width=4, shape=[2], name="state")
    update = cas(domain, domain.create_signal("update", width=4, shape=[2]))

    state <<= update
    assert len(state) == 2
    assert state[0].ty == Bits(4)
    assert state.reduce_or().ty == Bits(4)
    mlir = m.emit_mlir()
    # Packed port (2 lanes of i4 -> i8) with per-lane registers and reduction.
    assert "vector<" not in mlir
    assert "%update: i8" in mlir
    assert mlir.count("pyc.reg") == 2
    assert "pyc.or " in mlir


def test_v5_signal_type_surface_matches_wire() -> None:
    """ForwardSignal/CAS expose the same .ty/.width/.signed/.wire surface as Wire."""
    m = CycleAwareCircuit("v5_signal_ty")
    domain = m.create_domain("clk")
    ref = Circuit("v5_signal_ty_ref")

    scalar_port = cas(domain, domain.create_signal("s", width=8, signed=True))
    vector_port = cas(domain, domain.create_signal("v", width=4, shape=[2]))
    scalar_state = domain.signal(width=8, name="ss")
    vector_state = domain.signal(width=4, shape=[2], name="vs")

    assert scalar_port.ty == Bits(8)
    assert scalar_port.width == 8
    assert scalar_port.signed is True
    assert scalar_port.wire.ty == Bits(8)

    assert vector_port.ty == Vector(2, Bits(4))
    assert vector_port.width == 4
    assert vector_port.wire.ty == Vector(2, Bits(4))

    assert scalar_state.ty == Bits(8)
    assert scalar_state.width == 8
    assert scalar_state.signed is False
    assert scalar_state.wire.ty == Bits(8)

    assert vector_state.ty == Vector(2, Bits(4))
    assert vector_state.width == 4
    assert vector_state.wire.ty == Vector(2, Bits(4))

    assert ref.input("s", width=8, signed=True).ty == scalar_port.ty
    assert ref.input("v", width=4, shape=[2]).ty == vector_port.ty
    assert (vector_port + vector_state).ty == Vector(2, Bits(4))


def test_v5_vector_rejects_cross_domain_lanes() -> None:
    m = CycleAwareCircuit("v5_vector_domains")
    first = m.create_domain("first")
    second = m.create_domain("second")

    a = cas(first, first.create_signal("a", width=1))
    b = cas(second, second.create_signal("b", width=1))
    with pytest.raises(ValueError, match="share this domain"):
        first.vec(a, b)


def test_v5_reflected_arithmetic_ops_on_vector() -> None:
    """scalar op vector must work and produce the same type as vector op scalar."""
    from pycircuit import compile_cycle_aware
    from pycircuit.data import Bits, Vector

    def build(m, domain):
        a = cas(domain, m.input("a", width=4, shape=[2]), cycle=0)
        results = {
            "rsub": 1 - a,
            "rmul": 2 * a,
            "rand": 5 & a,
            "ror": 5 | a,
            "rxor": 5 ^ a,
            "radd": 1 + a,
            "rfloordiv": 8 // a,
            "rmod": 8 % a,
        }
        expected = Vector(2, Bits(4))
        for name, sig in results.items():
            assert sig.ty == expected, f"{name}: {sig.ty}"

    compile_cycle_aware(build, name="reflected_ops", eager=True)


def test_v5_reduce_defaults_match_wire_full_reduction() -> None:
    """CAS reduce_or/and/sum default to dim=None (full reduction) for rank-2."""
    from pycircuit import compile_cycle_aware
    from pycircuit.data import Bits

    def build(m, domain):
        r2 = cas(domain, m.input("b", width=4, shape=[2, 2]), cycle=0)
        assert r2.reduce_or().ty == Bits(4)
        assert r2.reduce_and().ty == Bits(4)
        assert r2.reduce_sum().ty == Bits(4)
        # Explicit dim still returns a lowered-rank vector.
        from pycircuit.data import Vector
        assert r2.reduce_or(dim=0).ty == Vector(2, Bits(4))

    compile_cycle_aware(build, name="reduce_defaults", eager=True)


def test_v5_module_level_priority_mux_and_cat_cycle_aware() -> None:
    """priority_mux() and cat() align cycles when given CAS operands."""
    from pycircuit import cat, compile_cycle_aware, priority_mux
    from pycircuit.data import Bits, Vector

    def build(m, domain):
        a = cas(domain, m.input("a", width=4, shape=[2]), cycle=0)
        sels = cas(domain, m.input("s", width=1, shape=[2]), cycle=0)
        pm = priority_mux(sels, a, default=a[0])
        assert pm.ty == Bits(4)
        assert pm.cycle == 0
        packed = cat(a[0], a[1])
        assert packed.ty == Bits(8)
        assert packed.cycle == 0

    compile_cycle_aware(build, name="pmux_cat", eager=True)


def test_v5_module_level_priority_mux_and_cat_scalar_path() -> None:
    """priority_mux/cat reject all-scalar inputs: as_cas needs a CAS anchor.

    With the scalar fallback removed, both module-level functions require at
    least one cycle-aware operand to anchor the domain; otherwise they raise
    ``TypeError`` (no silent delegation to Wire.priority_mux / hw.cat).
    """
    from pycircuit import CycleAwareCircuit, cat, priority_mux
    from pycircuit.data import Bits, Vector

    m = CycleAwareCircuit("scalar_path")
    sels = m.input("s", width=1, shape=[2])
    vals = m.input("v", width=4, shape=[2])
    with pytest.raises(TypeError, match="cycle-aware"):
        priority_mux(sels, vals)
    x = m.input("x", width=4)
    y = m.input("y", width=4)
    with pytest.raises(TypeError, match="cycle-aware"):
        cat(x, y)
    _ = Bits  # silence unused import in some linters
    _ = Vector  # silence unused import in some linters


def test_v5_priority_mux_accepts_forward_signal() -> None:
    """CAS.priority_mux accepts ForwardSignal as vals/default without .as_cas().

    Regression for the docs/v5_priority_mux_accepts_forward_signal_plan.md
    change: ``CycleAwareSignal.priority_mux`` used to reject ``ForwardSignal``
    in ``vals``/``default`` via a strict ``isinstance(..., (CAS, Wire))`` and
    force callers to write ``sig.as_cas()``.  After the change, the instance
    method unwraps any cycle-aware signal flavor through ``CAS.as_cas`` and
    produces the same type/cycle as the equivalent CAS path.
    """
    from pycircuit import CycleAwareCircuit
    from pycircuit.data import Bits

    m = CycleAwareCircuit("pmux_forward")
    domain = m.create_domain("clk")

    sels = cas(domain, m.input("s", width=1, shape=[2]), cycle=0)
    vals_forward = domain.signal(width=4, shape=[2], name="vals")
    default_forward = domain.signal(width=4, name="default")

    out = sels.priority_mux(vals_forward, default=default_forward)

    from pycircuit import CycleAwareSignal
    assert isinstance(out, CycleAwareSignal)
    assert out.ty == Bits(4)
    assert out.cycle == 0
    _ = Vector  # silence unused import in some linters
