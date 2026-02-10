/**
 * digital_clock_capi.cpp — C API wrapper around the generated RTL model.
 *
 * Compiled as a shared library (.dylib / .so), loaded by the Python
 * emulator via ctypes to perform true cycle-accurate simulation.
 *
 * Build:
 *   cd <pyCircuit root>
 *   c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
 *       -o examples/digital_clock/libdigital_clock_sim.dylib \
 *       examples/digital_clock/digital_clock_capi.cpp
 */

#include <cstdint>
#include <pyc/cpp/pyc_sim.hpp>
#include <pyc/cpp/pyc_tb.hpp>

#include "../generated/digital_clock/digital_clock_gen.hpp"

using pyc::cpp::Wire;

// ── Simulation context ─────────────────────────────────────────
struct SimContext {
    pyc::gen::digital_clock dut{};
    pyc::cpp::Testbench<pyc::gen::digital_clock> tb;
    uint64_t cycle = 0;

    SimContext() : tb(dut) {
        tb.addClock(dut.clk, /*halfPeriodSteps=*/1);
    }
};

// ── C API ──────────────────────────────────────────────────────
extern "C" {

SimContext* dc_create() {
    return new SimContext();
}

void dc_destroy(SimContext* ctx) {
    delete ctx;
}

void dc_reset(SimContext* ctx, uint64_t cycles) {
    ctx->tb.reset(ctx->dut.rst, /*cyclesAsserted=*/cycles, /*cyclesDeasserted=*/1);
    ctx->dut.eval();
    ctx->cycle = 0;
}

void dc_set_inputs(SimContext* ctx, int btn_set, int btn_plus, int btn_minus) {
    ctx->dut.btn_set   = Wire<1>(btn_set ? 1u : 0u);
    ctx->dut.btn_plus  = Wire<1>(btn_plus ? 1u : 0u);
    ctx->dut.btn_minus = Wire<1>(btn_minus ? 1u : 0u);
}

void dc_tick(SimContext* ctx) {
    // One full clock cycle = 2 half-period steps
    ctx->tb.runCycles(1);
    ctx->cycle++;
}

void dc_run_cycles(SimContext* ctx, uint64_t n) {
    // Bulk-run n clock cycles in C++ (avoids per-cycle ctypes overhead)
    ctx->tb.runCycles(n);
    ctx->cycle += n;
}

void dc_press_button(SimContext* ctx,
                     int btn_set, int btn_plus, int btn_minus,
                     uint64_t hold_cycles) {
    // Assert buttons, run hold_cycles, release, run hold_cycles
    ctx->dut.btn_set   = Wire<1>(btn_set   ? 1u : 0u);
    ctx->dut.btn_plus  = Wire<1>(btn_plus  ? 1u : 0u);
    ctx->dut.btn_minus = Wire<1>(btn_minus ? 1u : 0u);
    ctx->tb.runCycles(hold_cycles);
    ctx->cycle += hold_cycles;

    ctx->dut.btn_set   = Wire<1>(0u);
    ctx->dut.btn_plus  = Wire<1>(0u);
    ctx->dut.btn_minus = Wire<1>(0u);
    ctx->tb.runCycles(hold_cycles);
    ctx->cycle += hold_cycles;
}

// Output getters — return simple integers for ctypes
uint32_t dc_get_hours_bcd(SimContext* ctx)    { return ctx->dut.hours_bcd.value(); }
uint32_t dc_get_minutes_bcd(SimContext* ctx)  { return ctx->dut.minutes_bcd.value(); }
uint32_t dc_get_seconds_bcd(SimContext* ctx)  { return ctx->dut.seconds_bcd.value(); }
uint32_t dc_get_setting_mode(SimContext* ctx) { return ctx->dut.setting_mode.value(); }
uint32_t dc_get_colon_blink(SimContext* ctx)  { return ctx->dut.colon_blink.value(); }
uint64_t dc_get_cycle(SimContext* ctx)        { return ctx->cycle; }

} // extern "C"
