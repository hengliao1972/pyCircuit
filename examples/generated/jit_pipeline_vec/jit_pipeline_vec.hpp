// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct JitPipelineVec {
  pyc::cpp::Wire<1> sys_clk{};
  pyc::cpp::Wire<1> sys_rst{};
  pyc::cpp::Wire<16> a{};
  pyc::cpp::Wire<16> b{};
  pyc::cpp::Wire<1> sel{};
  pyc::cpp::Wire<1> tag{};
  pyc::cpp::Wire<16> data{};
  pyc::cpp::Wire<8> lo8{};

  pyc::cpp::Wire<25> PIPE0__bus_s0{};
  pyc::cpp::Wire<25> PIPE0__bus_s0__next{};
  pyc::cpp::Wire<25> PIPE1__bus_s1{};
  pyc::cpp::Wire<25> PIPE1__bus_s1__next{};
  pyc::cpp::Wire<25> PIPE2__bus_s2{};
  pyc::cpp::Wire<25> PIPE2__bus_s2__next{};
  pyc::cpp::Wire<16> a__jit_pipeline_vec__L19{};
  pyc::cpp::Wire<16> b__jit_pipeline_vec__L20{};
  pyc::cpp::Wire<25> bus__jit_pipeline_vec__L33{};
  pyc::cpp::Wire<25> bus__jit_pipeline_vec__L35{};
  pyc::cpp::Wire<16> data__jit_pipeline_vec__L26{};
  pyc::cpp::Wire<16> data__jit_pipeline_vec__L27{};
  pyc::cpp::Wire<16> data__jit_pipeline_vec__L28{};
  pyc::cpp::Wire<8> lo8__jit_pipeline_vec__L30{};
  pyc::cpp::Wire<16> pyc_add_3{};
  pyc::cpp::Wire<1> pyc_comb_10{};
  pyc::cpp::Wire<25> pyc_comb_11{};
  pyc::cpp::Wire<25> pyc_comb_13{};
  pyc::cpp::Wire<25> pyc_comb_15{};
  pyc::cpp::Wire<8> pyc_comb_20{};
  pyc::cpp::Wire<16> pyc_comb_21{};
  pyc::cpp::Wire<1> pyc_comb_22{};
  pyc::cpp::Wire<25> pyc_comb_9{};
  pyc::cpp::Wire<25> pyc_concat_8{};
  pyc::cpp::Wire<25> pyc_constant_1{};
  pyc::cpp::Wire<1> pyc_constant_2{};
  pyc::cpp::Wire<1> pyc_eq_6{};
  pyc::cpp::Wire<8> pyc_extract_17{};
  pyc::cpp::Wire<16> pyc_extract_18{};
  pyc::cpp::Wire<1> pyc_extract_19{};
  pyc::cpp::Wire<8> pyc_extract_7{};
  pyc::cpp::Wire<16> pyc_mux_5{};
  pyc::cpp::Wire<25> pyc_reg_12{};
  pyc::cpp::Wire<25> pyc_reg_14{};
  pyc::cpp::Wire<25> pyc_reg_16{};
  pyc::cpp::Wire<16> pyc_xor_4{};
  pyc::cpp::Wire<1> sel__jit_pipeline_vec__L21{};
  pyc::cpp::Wire<16> sum___jit_pipeline_vec__L24{};
  pyc::cpp::Wire<1> tag__jit_pipeline_vec__L29{};
  pyc::cpp::Wire<16> x__jit_pipeline_vec__L25{};

  pyc::cpp::pyc_reg<25> pyc_reg_12_inst;
  pyc::cpp::pyc_reg<25> pyc_reg_14_inst;
  pyc::cpp::pyc_reg<25> pyc_reg_16_inst;

  JitPipelineVec() :
      pyc_reg_12_inst(sys_clk, sys_rst, pyc_comb_10, pyc_comb_11, pyc_comb_9, pyc_reg_12),
      pyc_reg_14_inst(sys_clk, sys_rst, pyc_comb_10, pyc_comb_13, pyc_comb_9, pyc_reg_14),
      pyc_reg_16_inst(sys_clk, sys_rst, pyc_comb_10, pyc_comb_15, pyc_comb_9, pyc_reg_16) {
    eval();
  }

  inline void eval_comb_0() {
    PIPE0__bus_s0 = pyc_reg_12;
    PIPE1__bus_s1__next = PIPE0__bus_s0;
    pyc_comb_13 = PIPE1__bus_s1__next;
  }

  inline void eval_comb_1() {
    PIPE1__bus_s1 = pyc_reg_14;
    PIPE2__bus_s2__next = PIPE1__bus_s1;
    pyc_comb_15 = PIPE2__bus_s2__next;
  }

  inline void eval_comb_2() {
    PIPE2__bus_s2 = pyc_reg_16;
    bus__jit_pipeline_vec__L35 = PIPE2__bus_s2;
    pyc_extract_17 = pyc::cpp::extract<8, 25>(bus__jit_pipeline_vec__L35, 0u);
    pyc_extract_18 = pyc::cpp::extract<16, 25>(bus__jit_pipeline_vec__L35, 8u);
    pyc_extract_19 = pyc::cpp::extract<1, 25>(bus__jit_pipeline_vec__L35, 24u);
    pyc_comb_20 = pyc_extract_17;
    pyc_comb_21 = pyc_extract_18;
    pyc_comb_22 = pyc_extract_19;
  }

  inline void eval_comb_3() {
    pyc_constant_1 = pyc::cpp::Wire<25>({0x0ull});
    pyc_constant_2 = pyc::cpp::Wire<1>({0x1ull});
    a__jit_pipeline_vec__L19 = a;
    b__jit_pipeline_vec__L20 = b;
    sel__jit_pipeline_vec__L21 = sel;
    pyc_add_3 = (a__jit_pipeline_vec__L19 + b__jit_pipeline_vec__L20);
    sum___jit_pipeline_vec__L24 = pyc_add_3;
    pyc_xor_4 = (a__jit_pipeline_vec__L19 ^ b__jit_pipeline_vec__L20);
    x__jit_pipeline_vec__L25 = pyc_xor_4;
    data__jit_pipeline_vec__L26 = x__jit_pipeline_vec__L25;
    data__jit_pipeline_vec__L28 = sum___jit_pipeline_vec__L24;
    pyc_mux_5 = (sel__jit_pipeline_vec__L21.toBool() ? data__jit_pipeline_vec__L28 : data__jit_pipeline_vec__L26);
    data__jit_pipeline_vec__L27 = pyc_mux_5;
    pyc_eq_6 = pyc::cpp::Wire<1>((a__jit_pipeline_vec__L19 == b__jit_pipeline_vec__L20) ? 1u : 0u);
    tag__jit_pipeline_vec__L29 = pyc_eq_6;
    pyc_extract_7 = pyc::cpp::extract<8, 16>(data__jit_pipeline_vec__L27, 0u);
    lo8__jit_pipeline_vec__L30 = pyc_extract_7;
    pyc_concat_8 = pyc::cpp::concat(tag__jit_pipeline_vec__L29, data__jit_pipeline_vec__L27, lo8__jit_pipeline_vec__L30);
    bus__jit_pipeline_vec__L33 = pyc_concat_8;
    PIPE0__bus_s0__next = bus__jit_pipeline_vec__L33;
    pyc_comb_9 = pyc_constant_1;
    pyc_comb_10 = pyc_constant_2;
    pyc_comb_11 = PIPE0__bus_s0__next;
  }

  inline void eval_comb_pass() {
    eval_comb_0();
    eval_comb_1();
    eval_comb_2();
    eval_comb_3();
  }

  void eval() {
    eval_comb_0();
    eval_comb_1();
    eval_comb_2();
    eval_comb_3();
    tag = pyc_comb_22;
    data = pyc_comb_21;
    lo8 = pyc_comb_20;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_reg_12_inst.tick_compute();
    pyc_reg_14_inst.tick_compute();
    pyc_reg_16_inst.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_reg_12_inst.tick_commit();
    pyc_reg_14_inst.tick_commit();
    pyc_reg_16_inst.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
