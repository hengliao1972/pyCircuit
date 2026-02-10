/**
 * dodgeball_capi.cpp — C API wrapper around the generated RTL model.
 *
 * Build:
 *   cd <pyCircuit root>
 *   c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
 *       -o examples/dodgeball_game/libdodgeball_sim.dylib \
 *       examples/dodgeball_game/dodgeball_capi.cpp
 */

#include <cstdint>
#include <pyc/cpp/pyc_sim.hpp>
#include <pyc/cpp/pyc_tb.hpp>

#include "../generated/dodgeball_game/dodgeball_game.hpp"

using pyc::cpp::Wire;

struct SimContext {
    pyc::gen::dodgeball_game dut{};
    pyc::cpp::Testbench<pyc::gen::dodgeball_game> tb;
    uint64_t cycle = 0;

    SimContext() : tb(dut) {
        tb.addClock(dut.clk, /*halfPeriodSteps=*/1);
    }
};

extern "C" {

SimContext* db_create() {
    return new SimContext();
}

void db_destroy(SimContext* ctx) {
    delete ctx;
}

void db_reset(SimContext* ctx, uint64_t cycles) {
    ctx->tb.reset(ctx->dut.rst, /*cyclesAsserted=*/cycles, /*cyclesDeasserted=*/1);
    ctx->dut.eval();
    ctx->cycle = 0;
}

void db_set_inputs(SimContext* ctx, int rst_btn, int start, int left, int right) {
    ctx->dut.RST_BTN = Wire<1>(rst_btn ? 1u : 0u);
    ctx->dut.START   = Wire<1>(start ? 1u : 0u);
    ctx->dut.left    = Wire<1>(left ? 1u : 0u);
    ctx->dut.right   = Wire<1>(right ? 1u : 0u);
}

void db_tick(SimContext* ctx) {
    ctx->tb.runCycles(1);
    ctx->cycle++;
}

void db_run_cycles(SimContext* ctx, uint64_t n) {
    ctx->tb.runCycles(n);
    ctx->cycle += n;
}

// VGA outputs
uint32_t db_get_vga_hs(SimContext* ctx) { return ctx->dut.VGA_HS_O.value(); }
uint32_t db_get_vga_vs(SimContext* ctx) { return ctx->dut.VGA_VS_O.value(); }
uint32_t db_get_vga_r(SimContext* ctx)  { return ctx->dut.VGA_R.value(); }
uint32_t db_get_vga_g(SimContext* ctx)  { return ctx->dut.VGA_G.value(); }
uint32_t db_get_vga_b(SimContext* ctx)  { return ctx->dut.VGA_B.value(); }

// Debug outputs
uint32_t db_get_state(SimContext* ctx)     { return ctx->dut.dbg_state.value(); }
uint32_t db_get_j(SimContext* ctx)         { return ctx->dut.dbg_j.value(); }
uint32_t db_get_player_x(SimContext* ctx)  { return ctx->dut.dbg_player_x.value(); }
uint32_t db_get_ob1_x(SimContext* ctx)     { return ctx->dut.dbg_ob1_x.value(); }
uint32_t db_get_ob1_y(SimContext* ctx)     { return ctx->dut.dbg_ob1_y.value(); }
uint32_t db_get_ob2_x(SimContext* ctx)     { return ctx->dut.dbg_ob2_x.value(); }
uint32_t db_get_ob2_y(SimContext* ctx)     { return ctx->dut.dbg_ob2_y.value(); }
uint32_t db_get_ob3_x(SimContext* ctx)     { return ctx->dut.dbg_ob3_x.value(); }
uint32_t db_get_ob3_y(SimContext* ctx)     { return ctx->dut.dbg_ob3_y.value(); }

uint64_t db_get_cycle(SimContext* ctx) { return ctx->cycle; }

} // extern "C"
