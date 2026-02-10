# -*- coding: utf-8 -*-
"""Dodgeball top — pyCircuit cycle-aware rewrite of lab_final_top.v.

Notes:
- `clk` corresponds to the original `CLK_in`.
- A synchronous `rst` port is introduced for deterministic initialization.
- The internal game logic still uses `RST_BTN` exactly like the reference.
"""
from __future__ import annotations

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    compile_cycle_aware,
    mux,
    ca_cat,
)

try:
    from .lab_final_VGA import vga_timing
except ImportError:
    import sys
    from pathlib import Path
    _ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_ROOT))
    from examples.dodgeball_game.lab_final_VGA import vga_timing


def _dodgeball_impl(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    *,
    MAIN_CLK_BIT: int = 20,
) -> None:
    if MAIN_CLK_BIT < 0 or MAIN_CLK_BIT > 24:
        raise ValueError("MAIN_CLK_BIT must be in [0, 24]")

    c = lambda v, w: domain.const(v, width=w)

    # ================================================================
    # Inputs
    # ================================================================
    rst_btn = domain.input("RST_BTN", width=1)
    start = domain.input("START", width=1)
    left = domain.input("left", width=1)
    right = domain.input("right", width=1)

    # (left/right are unused in the reference logic, but kept as ports.)
    _ = left
    _ = right

    # ================================================================
    # Flops (Q outputs at cycle 0)
    # ================================================================
    cnt = domain.signal("pix_cnt", width=16, reset=0)
    pix_stb = domain.signal("pix_stb", width=1, reset=0)
    main_clk = domain.signal("main_clk", width=25, reset=0)

    player_x = domain.signal("player_x", width=4, reset=8)
    j = domain.signal("j", width=5, reset=0)

    ob1_x = domain.signal("ob1_x", width=4, reset=1)
    ob2_x = domain.signal("ob2_x", width=4, reset=4)
    ob3_x = domain.signal("ob3_x", width=4, reset=7)

    ob1_y = domain.signal("ob1_y", width=4, reset=0)
    ob2_y = domain.signal("ob2_y", width=4, reset=0)
    ob3_y = domain.signal("ob3_y", width=4, reset=0)

    fsm_state = domain.signal("fsm_state", width=3, reset=0)

    # ================================================================
    # Combinational logic (cycle 0)
    # ================================================================

    # --- Pixel strobe divider ---
    cnt_ext = cnt.zext(width=17)
    sum17 = cnt_ext + c(0x4000, 17)
    cnt_next = sum17.trunc(width=16)
    pix_stb_next = sum17[16]

    # --- Main clock divider bit (for game logic tick) ---
    main_clk_next = main_clk + c(1, 25)
    main_bit = main_clk[MAIN_CLK_BIT]
    main_next_bit = main_clk_next[MAIN_CLK_BIT]
    game_tick = (~main_bit) & main_next_bit

    # --- VGA timing ---
    (
        vga_h_count,
        vga_v_count,
        vga_h_next,
        vga_v_next,
        vga_hs,
        vga_vs,
        vga_blanking,
        vga_animate,
        vga_x,
        vga_y,
    ) = vga_timing(domain, pix_stb)
    _ = vga_blanking
    _ = vga_animate

    x = vga_x
    y = vga_y

    # --- Collision detection ---
    collision = (
        (ob1_x.eq(player_x) & ob1_y.eq(c(10, 4))) |
        (ob2_x.eq(player_x) & ob2_y.eq(c(10, 4))) |
        (ob3_x.eq(player_x) & ob3_y.eq(c(10, 4)))
    )

    # --- Object motion increments (boolean -> 4-bit) ---
    inc1 = (j.gt(c(0, 5)) & j.lt(c(13, 5))).zext(width=4)
    inc2 = (j.gt(c(3, 5)) & j.lt(c(16, 5))).zext(width=4)
    inc3 = (j.gt(c(7, 5)) & j.lt(c(20, 5))).zext(width=4)

    # --- FSM state flags ---
    st0 = fsm_state.eq(c(0, 3))
    st1 = fsm_state.eq(c(1, 3))
    st2 = fsm_state.eq(c(2, 3))

    cond_state0 = game_tick & st0
    cond_state1 = game_tick & st1
    cond_state2 = game_tick & st2

    cond_start = cond_state0 & start
    cond_rst_s1 = cond_state1 & rst_btn
    cond_rst_s2 = cond_state2 & rst_btn
    cond_collision = cond_state1 & collision
    cond_j20 = cond_state1 & j.eq(c(20, 5))

    # --- Player movement (left/right) ---
    left_only = left & ~right
    right_only = right & ~left
    can_left = player_x.gt(c(0, 4))
    can_right = player_x.lt(c(15, 4))
    move_left = cond_state1 & left_only & can_left
    move_right = cond_state1 & right_only & can_right

    # --- VGA draw logic ---
    x10 = x
    y10 = y.zext(width=10)

    player_x0 = player_x.zext(width=10) * c(40, 10)
    player_x1 = (player_x + c(1, 4)).zext(width=10) * c(40, 10)

    ob1_x0 = ob1_x.zext(width=10) * c(40, 10)
    ob1_x1 = (ob1_x + c(1, 4)).zext(width=10) * c(40, 10)
    ob1_y0 = ob1_y.zext(width=10) * c(40, 10)
    ob1_y1 = (ob1_y + c(1, 4)).zext(width=10) * c(40, 10)

    ob2_x0 = ob2_x.zext(width=10) * c(40, 10)
    ob2_x1 = (ob2_x + c(1, 4)).zext(width=10) * c(40, 10)
    ob2_y0 = ob2_y.zext(width=10) * c(40, 10)
    ob2_y1 = (ob2_y + c(1, 4)).zext(width=10) * c(40, 10)

    ob3_x0 = ob3_x.zext(width=10) * c(40, 10)
    ob3_x1 = (ob3_x + c(1, 4)).zext(width=10) * c(40, 10)
    ob3_y0 = ob3_y.zext(width=10) * c(40, 10)
    ob3_y1 = (ob3_y + c(1, 4)).zext(width=10) * c(40, 10)

    sq_player = (
        x10.gt(player_x0) & y10.gt(c(400, 10)) &
        x10.lt(player_x1) & y10.lt(c(440, 10))
    )

    sq_object1 = (
        x10.gt(ob1_x0) & y10.gt(ob1_y0) &
        x10.lt(ob1_x1) & y10.lt(ob1_y1)
    )
    sq_object2 = (
        x10.gt(ob2_x0) & y10.gt(ob2_y0) &
        x10.lt(ob2_x1) & y10.lt(ob2_y1)
    )
    sq_object3 = (
        x10.gt(ob3_x0) & y10.gt(ob3_y0) &
        x10.lt(ob3_x1) & y10.lt(ob3_y1)
    )

    over_wire = (
        x10.gt(c(0, 10)) & y10.gt(c(0, 10)) &
        x10.lt(c(640, 10)) & y10.lt(c(480, 10))
    )
    down = (
        x10.gt(c(0, 10)) & y10.gt(c(440, 10)) &
        x10.lt(c(640, 10)) & y10.lt(c(480, 10))
    )
    up = (
        x10.gt(c(0, 10)) & y10.gt(c(0, 10)) &
        x10.lt(c(640, 10)) & y10.lt(c(40, 10))
    )

    fsm_over = fsm_state.eq(c(2, 3))
    not_over = ~fsm_over

    circle = c(0, 1)

    vga_r_bit = sq_player & not_over
    vga_b_bit = (sq_object1 | sq_object2 | sq_object3 | down | up) & not_over
    vga_g_bit = circle | (over_wire & fsm_over)

    vga_r = ca_cat(vga_r_bit, c(0, 3))
    vga_g = ca_cat(vga_g_bit, c(0, 3))
    vga_b = ca_cat(vga_b_bit, c(0, 3))

    # ================================================================
    # DFF boundary
    # ================================================================
    domain.next()

    # ================================================================
    # Flop updates (last-write-wins order mirrors Verilog)
    # ================================================================

    # Clock divider flops
    cnt.set(cnt_next)
    pix_stb.set(pix_stb_next)
    main_clk.set(main_clk_next)

    # FSM state
    fsm_state.set(1, when=cond_start)
    fsm_state.set(0, when=cond_rst_s1)
    fsm_state.set(2, when=cond_collision)
    fsm_state.set(0, when=cond_rst_s2)

    # j counter
    j.set(0, when=cond_rst_s1)
    j.set(0, when=cond_j20)
    j.set(j + c(1, 5), when=cond_state1)
    j.set(0, when=cond_rst_s2)

    # player movement
    player_x.set(player_x - c(1, 4), when=move_left)
    player_x.set(player_x + c(1, 4), when=move_right)

    # object Y updates
    ob1_y.set(0, when=cond_rst_s1)
    ob1_y.set(0, when=cond_j20)
    ob1_y.set(ob1_y + inc1, when=cond_state1)
    ob1_y.set(0, when=cond_rst_s2)

    ob2_y.set(0, when=cond_rst_s1)
    ob2_y.set(0, when=cond_j20)
    ob2_y.set(ob2_y + inc2, when=cond_state1)
    ob2_y.set(0, when=cond_rst_s2)

    ob3_y.set(0, when=cond_rst_s1)
    ob3_y.set(0, when=cond_j20)
    ob3_y.set(ob3_y + inc3, when=cond_state1)
    ob3_y.set(0, when=cond_rst_s2)

    # VGA counters
    vga_h_count.set(vga_h_next)
    vga_v_count.set(vga_v_next)

    # ================================================================
    # Outputs
    # ================================================================
    m.output("VGA_HS_O", vga_hs)
    m.output("VGA_VS_O", vga_vs)
    m.output("VGA_R", vga_r)
    m.output("VGA_G", vga_g)
    m.output("VGA_B", vga_b)

    # Debug / visualization taps
    m.output("dbg_state", fsm_state)
    m.output("dbg_j", j)
    m.output("dbg_player_x", player_x)
    m.output("dbg_ob1_x", ob1_x)
    m.output("dbg_ob1_y", ob1_y)
    m.output("dbg_ob2_x", ob2_x)
    m.output("dbg_ob2_y", ob2_y)
    m.output("dbg_ob3_x", ob3_x)
    m.output("dbg_ob3_y", ob3_y)


def dodgeball_top(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    MAIN_CLK_BIT: int = 20,
) -> None:
    _dodgeball_impl(m, domain, MAIN_CLK_BIT=MAIN_CLK_BIT)


def build():
    return compile_cycle_aware(
        dodgeball_top,
        name="dodgeball_game",
        MAIN_CLK_BIT=20,
    )


if __name__ == "__main__":
    circuit = build()
    print(circuit.emit_mlir())
