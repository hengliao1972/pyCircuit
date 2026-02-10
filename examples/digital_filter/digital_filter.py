# -*- coding: utf-8 -*-
"""4-tap Feed-Forward (FIR) Filter — pyCircuit unified signal model.

Implements:
    y[n] = c0·x[n] + c1·x[n-1] + c2·x[n-2] + c3·x[n-3]

Architecture (single-cycle, direct-form):

    x_in ──┬──[×c0]──┐
           │          │
           z⁻¹──[×c1]──(+)──┐
           │                  │
           z⁻¹──[×c2]──────(+)──┐
           │                      │
           z⁻¹──[×c3]──────────(+)──→ y_out

  cycle 0:  read delay-line Q → multiply → accumulate
            domain.next()
  cycle 1:  .set() shift register D-inputs

Ports:
  Inputs:
    x_in [DATA_W-1:0]   — input sample (signed)
    x_valid              — input strobe (advance filter)

  Outputs:
    y_out [ACC_W-1:0]    — filter output (signed)
    y_valid              — output valid strobe

JIT parameters:
    TAPS      — number of taps (default 4)
    DATA_W    — input data width in bits (default 16, signed)
    COEFF_W   — coefficient width in bits (default 16, signed)
    COEFFS    — tuple of coefficient values (default (1,2,3,4))
"""
from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareSignal,
    compile_cycle_aware,
    mux,
)


def _filter_impl(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    TAPS: int,
    DATA_W: int,
    COEFF_W: int,
    COEFFS: tuple[int, ...],
) -> None:
    c = lambda v, w: domain.const(v, width=w)

    assert len(COEFFS) == TAPS, f"need {TAPS} coefficients, got {len(COEFFS)}"

    # Accumulator width: DATA_W + COEFF_W + ceil(log2(TAPS)) guard bits
    GUARD = (TAPS - 1).bit_length()
    ACC_W = DATA_W + COEFF_W + GUARD

    # ════════════════════════════════════════════════════════
    # Inputs
    # ════════════════════════════════════════════════════════
    x_in    = domain.input("x_in",    width=DATA_W)
    x_valid = domain.input("x_valid", width=1)

    # ════════════════════════════════════════════════════════
    # Delay line (shift register): x[n], x[n-1], ..., x[n-(TAPS-1)]
    # Each tap is a DATA_W-bit signed register.
    # tap[0] = x[n] (current input, combinational)
    # tap[1..TAPS-1] = z⁻¹ ... z⁻(TAPS-1) (registered)
    # ════════════════════════════════════════════════════════
    delay_regs = []
    for i in range(1, TAPS):
        r = domain.signal(f"delay_{i}", width=DATA_W, reset=0)
        delay_regs.append(r)

    # Build the tap array: tap[0] = x_in, tap[1..] = delay registers
    taps = [x_in] + delay_regs

    # ════════════════════════════════════════════════════════
    # Coefficients (compile-time constants)
    # ════════════════════════════════════════════════════════
    coeff_sigs = []
    for i, cv in enumerate(COEFFS):
        coeff_sigs.append(c(cv & ((1 << COEFF_W) - 1), COEFF_W))

    # ════════════════════════════════════════════════════════
    # Multiply-accumulate (combinational, cycle 0)
    #   y = sum( taps[i] * coeffs[i] )  for i in 0..TAPS-1
    # All operands sign-extended to ACC_W before multiply.
    # ════════════════════════════════════════════════════════
    acc = c(0, ACC_W).as_signed()

    for i in range(TAPS):
        tap_ext  = taps[i].as_signed().sext(width=ACC_W)
        coef_ext = coeff_sigs[i].as_signed().sext(width=ACC_W)
        product  = tap_ext * coef_ext
        acc = acc + product

    y_comb = acc.as_unsigned()

    # Registered output (1-cycle latency — standard for synchronous filters)
    y_out_r   = domain.signal("y_out_reg",   width=ACC_W, reset=0)
    y_valid_r = domain.signal("y_valid_reg", width=1,     reset=0)

    # ════════════════════════════════════════════════════════
    # DFF boundary
    # ════════════════════════════════════════════════════════
    domain.next()

    # ════════════════════════════════════════════════════════
    # Shift register update: on valid input, shift delay line
    # ════════════════════════════════════════════════════════
    for r in delay_regs:
        r.set(r)   # default: hold

    # delay[0] ← x_in  (newest sample)
    delay_regs[0].set(x_in, when=x_valid)

    # delay[i] ← delay[i-1]  (shift)
    for i in range(1, len(delay_regs)):
        delay_regs[i].set(delay_regs[i - 1], when=x_valid)

    # Capture combinational result only when valid input arrives
    y_out_r.set(y_out_r)                      # hold
    y_out_r.set(y_comb, when=x_valid)         # capture on valid input
    y_valid_r.set(x_valid)

    # ════════════════════════════════════════════════════════
    # Outputs (registered — stable after clock edge)
    # ════════════════════════════════════════════════════════
    m.output("y_out",   y_out_r)
    m.output("y_valid", y_valid_r)


# ── Public entry points ──────────────────────────────────────

def digital_filter(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    TAPS: int = 4,
    DATA_W: int = 16,
    COEFF_W: int = 16,
    COEFFS: tuple = (1, 2, 3, 4),
) -> None:
    _filter_impl(m, domain, TAPS, DATA_W, COEFF_W, COEFFS)


def build():
    return compile_cycle_aware(
        digital_filter, name="digital_filter",
        TAPS=4, DATA_W=16, COEFF_W=16, COEFFS=(1, 2, 3, 4),
    )


if __name__ == "__main__":
    print(build().emit_mlir())
