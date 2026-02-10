# -*- coding: utf-8 -*-
"""Multi-clock domain registers example using cycle-aware API.

Demonstrates independent counters in two separate clock domains.
"""
from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    compile_cycle_aware,
)


def multiclock_regs(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    """Build two independent counters in separate clock domains.
    
    Note: This example creates a second clock domain for demonstration.
    In practice, multi-clock designs require CDC (clock domain crossing).
    
    Args:
        m: Cycle-aware circuit builder
        domain: Primary clock domain (clk_a)
    """
    # Domain A (primary): Counter A
    domain_a = domain
    
    # Cycle 0 in domain A
    a_val = domain_a.create_const(0, width=8, name="a_init")
    a_next = a_val + 1
    
    # Cycle 1 in domain A: Register
    domain_a.next()
    a_reg = domain_a.cycle(a_next, reset_value=0, name="a")
    
    # Create domain B for second counter
    domain_b = m.create_domain("clk_b")
    
    # Cycle 0 in domain B
    b_val = domain_b.create_const(0, width=8, name="b_init")
    b_next = b_val + 1
    
    # Cycle 1 in domain B: Register
    domain_b.next()
    b_reg = domain_b.cycle(b_next, reset_value=0, name="b")
    
    # Outputs
    m.output("a_count", a_reg.sig)
    m.output("b_count", b_reg.sig)


def build():
    return compile_cycle_aware(multiclock_regs, name="multiclock_regs", domain_name="clk_a")


# Entry point for JIT compilation
if __name__ == "__main__":
    print(build().emit_mlir())
