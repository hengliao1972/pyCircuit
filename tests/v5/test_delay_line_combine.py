"""Frontend ownership-marker regression for cycle-balance delay chains."""

from __future__ import annotations

from pycircuit import CycleAwareCircuit, cas, wire_of


def test_cycle_balance_stages_carry_explicit_generated_metadata() -> None:
    """The combine pass must not infer compiler ownership from `_v5_bal` names."""
    circuit = CycleAwareCircuit("generated_balance_metadata")
    domain = circuit.create_domain("clk")
    early = cas(domain, circuit.input("a", width=8), cycle=0)
    late = cas(domain, circuit.input("b", width=8), cycle=3)
    circuit.output("c", wire_of(early + late))

    mlir = circuit.emit_mlir()
    reg_lines = [line for line in mlir.splitlines() if "pyc.reg" in line]
    balance_q_aliases = [
        line
        for line in mlir.splitlines()
        if "pyc.alias" in line and "_v5_bal_" in line and "__next" not in line
    ]

    assert len(reg_lines) == 3
    assert len(balance_q_aliases) == 3
    assert all('pyc.generated = "cycle_balance"' in line for line in reg_lines)
    assert all(
        'pyc.generated = "cycle_balance"' in line for line in balance_q_aliases
    )
