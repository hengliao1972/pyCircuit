/**
 * filter_capi.cpp — C API wrapper for the 4-tap FIR filter RTL.
 *
 * Build (from pyCircuit root):
 *   c++ -std=c++17 -O2 -shared -fPIC -I include -I . \
 *       -o examples/digital_filter/libfilter_sim.dylib \
 *       examples/digital_filter/filter_capi.cpp
 */
#include <cstdint>
#include <pyc/cpp/pyc_sim.hpp>
#include <pyc/cpp/pyc_tb.hpp>

#include "examples/generated/digital_filter/digital_filter_gen.hpp"

using pyc::cpp::Wire;

struct SimContext {
    pyc::gen::digital_filter dut{};
    pyc::cpp::Testbench<pyc::gen::digital_filter> tb;
    uint64_t cycle = 0;
    SimContext() : tb(dut) { tb.addClock(dut.clk, 1); }
};

extern "C" {

SimContext* fir_create()                       { return new SimContext(); }
void        fir_destroy(SimContext* c)         { delete c; }

void fir_reset(SimContext* c, uint64_t n) {
    c->tb.reset(c->dut.rst, n, 1);
    c->dut.eval();
    c->cycle = 0;
}

void fir_push_sample(SimContext* c, int16_t sample) {
    // Assert x_in + x_valid for 1 cycle.
    // The registered output captures the result on this clock edge.
    c->dut.x_in    = Wire<16>(static_cast<uint64_t>(static_cast<uint16_t>(sample)));
    c->dut.x_valid = Wire<1>(1u);
    c->tb.runCycles(1);
    c->cycle++;
    // Deassert and idle 1 cycle so output is stable for reading.
    c->dut.x_valid = Wire<1>(0u);
    c->dut.x_in    = Wire<16>(0u);
    c->tb.runCycles(1);
    c->cycle++;
}

void fir_idle(SimContext* c, uint64_t n) {
    c->dut.x_valid = Wire<1>(0u);
    c->tb.runCycles(n);
    c->cycle += n;
}

int64_t  fir_get_y_out(SimContext* c)   { return static_cast<int64_t>(c->dut.y_out.value()); }
uint32_t fir_get_y_valid(SimContext* c) { return c->dut.y_valid.value(); }
uint64_t fir_get_cycle(SimContext* c)   { return c->cycle; }

} // extern "C"
