/**
 * traffic_lights_capi.cpp — C API wrapper around the generated RTL model.
 *
 * Build:
 *   cd <pyCircuit root>
 *   c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
 *       -o examples/traffic_lights_ce_pyc/libtraffic_lights_sim.dylib \
 *       examples/traffic_lights_ce_pyc/traffic_lights_capi.cpp
 */

#include <cstdint>
#include <pyc/cpp/pyc_sim.hpp>
#include <pyc/cpp/pyc_tb.hpp>

#include "../generated/traffic_lights_ce_pyc/traffic_lights_ce_pyc.hpp"

using pyc::cpp::Wire;

struct SimContext {
    pyc::gen::traffic_lights_ce_pyc dut{};
    pyc::cpp::Testbench<pyc::gen::traffic_lights_ce_pyc> tb;
    uint64_t cycle = 0;

    SimContext() : tb(dut) {
        tb.addClock(dut.clk, /*halfPeriodSteps=*/1);
    }
};

extern "C" {

SimContext* tl_create() {
    return new SimContext();
}

void tl_destroy(SimContext* ctx) {
    delete ctx;
}

void tl_reset(SimContext* ctx, uint64_t cycles) {
    ctx->tb.reset(ctx->dut.rst, /*cyclesAsserted=*/cycles, /*cyclesDeasserted=*/1);
    ctx->dut.eval();
    ctx->cycle = 0;
}

void tl_set_inputs(SimContext* ctx, int go, int emergency) {
    ctx->dut.go = Wire<1>(go ? 1u : 0u);
    ctx->dut.emergency = Wire<1>(emergency ? 1u : 0u);
}

void tl_tick(SimContext* ctx) {
    ctx->tb.runCycles(1);
    ctx->cycle++;
}

void tl_run_cycles(SimContext* ctx, uint64_t n) {
    ctx->tb.runCycles(n);
    ctx->cycle += n;
}

uint32_t tl_get_ew_bcd(SimContext* ctx) { return ctx->dut.ew_bcd.value(); }
uint32_t tl_get_ns_bcd(SimContext* ctx) { return ctx->dut.ns_bcd.value(); }

uint32_t tl_get_ew_red(SimContext* ctx) { return ctx->dut.ew_red.value(); }
uint32_t tl_get_ew_yellow(SimContext* ctx) { return ctx->dut.ew_yellow.value(); }
uint32_t tl_get_ew_green(SimContext* ctx) { return ctx->dut.ew_green.value(); }

uint32_t tl_get_ns_red(SimContext* ctx) { return ctx->dut.ns_red.value(); }
uint32_t tl_get_ns_yellow(SimContext* ctx) { return ctx->dut.ns_yellow.value(); }
uint32_t tl_get_ns_green(SimContext* ctx) { return ctx->dut.ns_green.value(); }

uint64_t tl_get_cycle(SimContext* ctx) { return ctx->cycle; }

} // extern "C"
