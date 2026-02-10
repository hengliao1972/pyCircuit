# -*- coding: utf-8 -*-
"""Traffic Lights Controller — pyCircuit cycle-aware design.

Reimplements the Traffic-lights-ce project in the pyCircuit unified signal model.
Outputs are BCD countdowns per direction plus discrete red/yellow/green lights.

JIT parameters:
  CLK_FREQ     — system clock frequency in Hz (default 50 MHz)
  EW_GREEN_S   — east/west green time in seconds
  EW_YELLOW_S  — east/west yellow time in seconds
  NS_GREEN_S   — north/south green time in seconds
  NS_YELLOW_S  — north/south yellow time in seconds

Derived:
  EW_RED_S = NS_GREEN_S + NS_YELLOW_S
  NS_RED_S = EW_GREEN_S + EW_YELLOW_S
"""
from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    compile_cycle_aware,
    mux,
)

try:
    from examples.digital_clock.bcd import bin_to_bcd_60
except ImportError:
    import sys
    from pathlib import Path
    _ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_ROOT))
    from examples.digital_clock.bcd import bin_to_bcd_60


# Phase encoding
PH_EW_GREEN  = 0
PH_EW_YELLOW = 1
PH_NS_GREEN  = 2
PH_NS_YELLOW = 3


def _traffic_lights_impl(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    CLK_FREQ: int,
    EW_GREEN_S: int,
    EW_YELLOW_S: int,
    NS_GREEN_S: int,
    NS_YELLOW_S: int,
) -> None:
    if min(EW_GREEN_S, EW_YELLOW_S, NS_GREEN_S, NS_YELLOW_S) <= 0:
        raise ValueError("all durations must be > 0")

    EW_RED_S = NS_GREEN_S + NS_YELLOW_S
    NS_RED_S = EW_GREEN_S + EW_YELLOW_S

    max_dur = max(EW_GREEN_S, EW_YELLOW_S, NS_GREEN_S, NS_YELLOW_S, EW_RED_S, NS_RED_S)
    if max_dur > 59:
        raise ValueError("all durations must be <= 59 to fit bin_to_bcd_60")

    c = lambda v, w: domain.const(v, width=w)

    # ================================================================
    # Inputs
    # ================================================================
    go = domain.input("go", width=1)
    emergency = domain.input("emergency", width=1)

    # ================================================================
    # Flops (Q outputs at cycle 0)
    # ================================================================
    PRESCALER_W = max((CLK_FREQ - 1).bit_length(), 1)
    CNT_W = max(max_dur.bit_length(), 1)

    prescaler_r = domain.signal("prescaler", width=PRESCALER_W, reset=0)
    phase_r = domain.signal("phase", width=2, reset=PH_EW_GREEN)
    ew_cnt_r = domain.signal("ew_cnt", width=CNT_W, reset=EW_GREEN_S)
    ns_cnt_r = domain.signal("ns_cnt", width=CNT_W, reset=NS_RED_S)
    blink_r = domain.signal("blink", width=1, reset=0)

    # ================================================================
    # Combinational logic (cycle 0)
    # ================================================================
    en = go & (~emergency)

    # 1 Hz tick via prescaler (gated by en)
    tick_raw = prescaler_r.eq(c(CLK_FREQ - 1, PRESCALER_W))
    tick_1hz = tick_raw & en
    prescaler_next = mux(en, mux(tick_raw, c(0, PRESCALER_W), prescaler_r + 1), prescaler_r)

    # Phase flags
    is_ew_green = phase_r.eq(c(PH_EW_GREEN, 2))
    is_ew_yellow = phase_r.eq(c(PH_EW_YELLOW, 2))
    is_ns_green = phase_r.eq(c(PH_NS_GREEN, 2))
    is_ns_yellow = phase_r.eq(c(PH_NS_YELLOW, 2))
    yellow_active = is_ew_yellow | is_ns_yellow

    # Countdown end flags (1 -> reload at next tick)
    ew_end = ew_cnt_r.eq(c(1, CNT_W))
    ns_end = ns_cnt_r.eq(c(1, CNT_W))

    ew_cnt_dec = ew_cnt_r - 1
    ns_cnt_dec = ns_cnt_r - 1

    # Phase transitions
    cond_ew_to_yellow = tick_1hz & is_ew_green & ew_end
    cond_ew_to_ns_green = tick_1hz & is_ew_yellow & ew_end
    cond_ns_to_yellow = tick_1hz & is_ns_green & ns_end
    cond_ns_to_ew_green = tick_1hz & is_ns_yellow & ns_end

    phase_next = phase_r
    phase_next = mux(cond_ew_to_yellow, c(PH_EW_YELLOW, 2), phase_next)
    phase_next = mux(cond_ew_to_ns_green, c(PH_NS_GREEN, 2), phase_next)
    phase_next = mux(cond_ns_to_yellow, c(PH_NS_YELLOW, 2), phase_next)
    phase_next = mux(cond_ns_to_ew_green, c(PH_EW_GREEN, 2), phase_next)

    # EW countdown
    ew_cnt_next = ew_cnt_r
    ew_cnt_next = mux(tick_1hz, ew_cnt_dec, ew_cnt_next)
    ew_cnt_next = mux(cond_ew_to_yellow, c(EW_YELLOW_S, CNT_W), ew_cnt_next)
    ew_cnt_next = mux(cond_ew_to_ns_green, c(EW_RED_S, CNT_W), ew_cnt_next)
    ew_cnt_next = mux(cond_ns_to_ew_green, c(EW_GREEN_S, CNT_W), ew_cnt_next)

    # NS countdown
    ns_cnt_next = ns_cnt_r
    ns_cnt_next = mux(tick_1hz, ns_cnt_dec, ns_cnt_next)
    ns_cnt_next = mux(cond_ew_to_ns_green, c(NS_GREEN_S, CNT_W), ns_cnt_next)
    ns_cnt_next = mux(cond_ns_to_yellow, c(NS_YELLOW_S, CNT_W), ns_cnt_next)
    ns_cnt_next = mux(cond_ns_to_ew_green, c(NS_RED_S, CNT_W), ns_cnt_next)

    # BCD conversion (combinational)
    ew_bcd_raw = bin_to_bcd_60(domain, ew_cnt_r, "ew")
    ns_bcd_raw = bin_to_bcd_60(domain, ns_cnt_r, "ns")

    # Lights (base, before emergency override)
    ew_red_base = is_ns_green | is_ns_yellow
    ew_green_base = is_ew_green
    ew_yellow_base = is_ew_yellow & blink_r

    ns_red_base = is_ew_green | is_ew_yellow
    ns_green_base = is_ns_green
    ns_yellow_base = is_ns_yellow & blink_r

    # Emergency overrides
    ew_bcd = mux(emergency, c(0x88, 8), ew_bcd_raw)
    ns_bcd = mux(emergency, c(0x88, 8), ns_bcd_raw)

    ew_red = mux(emergency, c(1, 1), ew_red_base)
    ew_yellow = mux(emergency, c(0, 1), ew_yellow_base)
    ew_green = mux(emergency, c(0, 1), ew_green_base)

    ns_red = mux(emergency, c(1, 1), ns_red_base)
    ns_yellow = mux(emergency, c(0, 1), ns_yellow_base)
    ns_green = mux(emergency, c(0, 1), ns_green_base)

    # ================================================================
    # DFF boundary
    # ================================================================
    domain.next()

    # ================================================================
    # Flop updates
    # ================================================================
    prescaler_r.set(prescaler_next)
    phase_r.set(phase_next)
    ew_cnt_r.set(ew_cnt_next)
    ns_cnt_r.set(ns_cnt_next)

    # Blink: toggle on tick_1hz while in yellow; reset to 0 when not yellow.
    blink_r.set(blink_r)
    blink_r.set(0, when=~yellow_active)
    blink_r.set(~blink_r, when=tick_1hz & yellow_active)

    # ================================================================
    # Outputs
    # ================================================================
    m.output("ew_bcd", ew_bcd)
    m.output("ns_bcd", ns_bcd)
    m.output("ew_red", ew_red)
    m.output("ew_yellow", ew_yellow)
    m.output("ew_green", ew_green)
    m.output("ns_red", ns_red)
    m.output("ns_yellow", ns_yellow)
    m.output("ns_green", ns_green)


# ------------------------------------------------------------------
# Public entry point (with JIT parameters)
# ------------------------------------------------------------------

def traffic_lights_ce_pyc(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    CLK_FREQ: int = 50_000_000,
    EW_GREEN_S: int = 45,
    EW_YELLOW_S: int = 5,
    NS_GREEN_S: int = 30,
    NS_YELLOW_S: int = 5,
) -> None:
    _traffic_lights_impl(
        m, domain,
        CLK_FREQ=CLK_FREQ,
        EW_GREEN_S=EW_GREEN_S,
        EW_YELLOW_S=EW_YELLOW_S,
        NS_GREEN_S=NS_GREEN_S,
        NS_YELLOW_S=NS_YELLOW_S,
    )


# ------------------------------------------------------------------
# CLI entry point: pycircuit.cli expects `build` -> Module.
# ------------------------------------------------------------------

def build():
    return compile_cycle_aware(
        traffic_lights_ce_pyc,
        name="traffic_lights_ce_pyc",
        CLK_FREQ=50_000_000,
        EW_GREEN_S=45,
        EW_YELLOW_S=5,
        NS_GREEN_S=30,
        NS_YELLOW_S=5,
    )


# ------------------------------------------------------------------
# Standalone compile
# ------------------------------------------------------------------

if __name__ == "__main__":
    circuit = build()
    print(circuit.emit_mlir())
