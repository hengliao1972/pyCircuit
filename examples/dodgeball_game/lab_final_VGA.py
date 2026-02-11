# -*- coding: utf-8 -*-
"""VGA timing generator — pyCircuit cycle-aware rewrite of lab_final_VGA.v.

Implements the same 640x480@60Hz timing logic with 800x524 total counts.
"""
from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    compile_cycle_aware,
    mux,
)

# VGA timing constants (same as reference Verilog)
HS_STA = 16
HS_END = 16 + 96
HA_STA = 16 + 96 + 48
VS_STA = 480 + 11
VS_END = 480 + 11 + 2
VA_END = 480
LINE = 800
SCREEN = 524


def vga_timing(domain: CycleAwareDomain, i_pix_stb):
    """Build VGA timing logic.

    Returns a tuple containing internal regs, next-state signals, and outputs
    so callers can update all flops after a shared domain.next().
    """
    c = lambda v, w: domain.const(v, width=w)

    h_count = domain.signal("vga_h_count", width=10, reset=0)
    v_count = domain.signal("vga_v_count", width=10, reset=0)

    h_end = h_count.eq(c(LINE, 10))
    v_end = v_count.eq(c(SCREEN, 10))

    h_inc = h_count + c(1, 10)
    v_inc = v_count + c(1, 10)

    h_after = mux(h_end, c(0, 10), h_inc)
    v_after = mux(h_end, v_inc, v_count)
    v_after = mux(v_end, c(0, 10), v_after)

    h_next = mux(i_pix_stb, h_after, h_count)
    v_next = mux(i_pix_stb, v_after, v_count)

    o_hs = ~(h_count.ge(c(HS_STA, 10)) & h_count.lt(c(HS_END, 10)))
    o_vs = ~(v_count.ge(c(VS_STA, 10)) & v_count.lt(c(VS_END, 10)))

    o_x = mux(h_count.lt(c(HA_STA, 10)), c(0, 10), h_count - c(HA_STA, 10))
    y_full = mux(v_count.ge(c(VA_END, 10)), c(VA_END - 1, 10), v_count)
    o_y = y_full.trunc(width=9)

    o_blanking = h_count.lt(c(HA_STA, 10)) | v_count.gt(c(VA_END - 1, 10))
    o_animate = v_count.eq(c(VA_END - 1, 10)) & h_count.eq(c(LINE, 10))

    return (
        h_count,
        v_count,
        h_next,
        v_next,
        o_hs,
        o_vs,
        o_blanking,
        o_animate,
        o_x,
        o_y,
    )


def _lab_final_vga_impl(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    """Standalone VGA module (ports mirror the reference Verilog)."""
    i_pix_stb = domain.input("i_pix_stb", width=1)

    (
        h_count,
        v_count,
        h_next,
        v_next,
        o_hs,
        o_vs,
        o_blanking,
        o_animate,
        o_x,
        o_y,
    ) = vga_timing(domain, i_pix_stb)

    # DFF boundary
    domain.next()

    # Flop updates
    h_count.set(h_next)
    v_count.set(v_next)

    # Outputs
    m.output("o_hs", o_hs)
    m.output("o_vs", o_vs)
    m.output("o_blanking", o_blanking)
    m.output("o_animate", o_animate)
    m.output("o_x", o_x)
    m.output("o_y", o_y)


def lab_final_vga(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    _lab_final_vga_impl(m, domain)


def build():
    return compile_cycle_aware(lab_final_vga, name="lab_final_vga")


if __name__ == "__main__":
    circuit = build()
    print(circuit.emit_mlir())
