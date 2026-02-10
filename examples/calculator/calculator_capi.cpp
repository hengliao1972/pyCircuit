/**
 * calculator_capi.cpp — C API wrapper for the generated calculator RTL model.
 *
 * Build:
 *   c++ -std=c++17 -O2 -shared -fPIC -I../../include \
 *       -o libcalculator_sim.dylib calculator_capi.cpp
 */
#include <cstdint>
#include <pyc/cpp/pyc_sim.hpp>
#include <pyc/cpp/pyc_tb.hpp>

// NOTE: compile from the pyCircuit root directory:
//   c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
//       -o examples/calculator/libcalculator_sim.dylib \
//       examples/calculator/calculator_capi.cpp
#include "../generated/calculator/calculator_gen.hpp"

using pyc::cpp::Wire;

struct SimContext {
    pyc::gen::calculator dut{};
    pyc::cpp::Testbench<pyc::gen::calculator> tb;
    uint64_t cycle = 0;
    SimContext() : tb(dut) { tb.addClock(dut.clk, 1); }
};

extern "C" {

SimContext* calc_create()                         { return new SimContext(); }
void        calc_destroy(SimContext* c)           { delete c; }

void calc_reset(SimContext* c, uint64_t n) {
    c->tb.reset(c->dut.rst, n, 1);
    c->dut.eval();
    c->cycle = 0;
}

void calc_press_key(SimContext* c, int key_code, uint64_t hold) {
    // Assert key + press for exactly 1 cycle (strobe)
    c->dut.key       = Wire<5>(static_cast<uint64_t>(key_code & 0x1F));
    c->dut.key_press = Wire<1>(1u);
    c->tb.runCycles(1);
    c->cycle += 1;
    // Release and idle
    c->dut.key_press = Wire<1>(0u);
    if (hold > 1) {
        c->tb.runCycles(hold - 1);
        c->cycle += hold - 1;
    }
}

void calc_run_cycles(SimContext* c, uint64_t n) {
    c->dut.key_press = Wire<1>(0u);
    c->tb.runCycles(n);
    c->cycle += n;
}

uint64_t calc_get_display(SimContext* c)     { return c->dut.display.value(); }
uint32_t calc_get_display_neg(SimContext* c) { return c->dut.display_neg.value(); }
uint32_t calc_get_display_err(SimContext* c) { return c->dut.display_err.value(); }
uint32_t calc_get_display_dp(SimContext* c)  { return c->dut.display_dp.value(); }
uint32_t calc_get_op_pending(SimContext* c)  { return c->dut.op_pending.value(); }
uint64_t calc_get_cycle(SimContext* c)       { return c->cycle; }

} // extern "C"
