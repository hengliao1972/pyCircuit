// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct WireOps {
  pyc::cpp::Wire<1> sys_clk{};
  pyc::cpp::Wire<1> sys_rst{};
  pyc::cpp::Wire<8> a{};
  pyc::cpp::Wire<8> b{};
  pyc::cpp::Wire<1> sel{};
  pyc::cpp::Wire<8> y{};

  pyc::cpp::Wire<8> COMB__y__wire_ops__L14{};
  pyc::cpp::Wire<8> COMB__y__wire_ops__L15{};
  pyc::cpp::Wire<8> COMB__y__wire_ops__L16{};
  pyc::cpp::Wire<8> a__wire_ops__L9{};
  pyc::cpp::Wire<8> b__wire_ops__L10{};
  pyc::cpp::Wire<8> pyc_and_4{};
  pyc::cpp::Wire<8> pyc_comb_10{};
  pyc::cpp::Wire<8> pyc_comb_6{};
  pyc::cpp::Wire<1> pyc_comb_7{};
  pyc::cpp::Wire<8> pyc_comb_8{};
  pyc::cpp::Wire<8> pyc_constant_1{};
  pyc::cpp::Wire<1> pyc_constant_2{};
  pyc::cpp::Wire<8> pyc_mux_5{};
  pyc::cpp::Wire<8> pyc_reg_9{};
  pyc::cpp::Wire<8> pyc_xor_3{};
  pyc::cpp::Wire<8> r__wire_ops__L18{};
  pyc::cpp::Wire<1> sel__wire_ops__L11{};
  pyc::cpp::Wire<8> y_reg{};
  pyc::cpp::Wire<8> y_reg__next{};

  pyc::cpp::pyc_reg<8> pyc_reg_9_inst;

  WireOps() :
      pyc_reg_9_inst(sys_clk, sys_rst, pyc_comb_7, pyc_comb_8, pyc_comb_6, pyc_reg_9) {
    eval();
  }

  inline void eval_comb_0() {
    y_reg = pyc_reg_9;
    r__wire_ops__L18 = y_reg;
    pyc_comb_10 = r__wire_ops__L18;
  }

  inline void eval_comb_1() {
    pyc_constant_1 = pyc::cpp::Wire<8>({0x0ull});
    pyc_constant_2 = pyc::cpp::Wire<1>({0x1ull});
    a__wire_ops__L9 = a;
    b__wire_ops__L10 = b;
    sel__wire_ops__L11 = sel;
    pyc_xor_3 = (a__wire_ops__L9 ^ b__wire_ops__L10);
    COMB__y__wire_ops__L14 = pyc_xor_3;
    pyc_and_4 = (a__wire_ops__L9 & b__wire_ops__L10);
    COMB__y__wire_ops__L16 = pyc_and_4;
    pyc_mux_5 = (sel__wire_ops__L11.toBool() ? COMB__y__wire_ops__L16 : COMB__y__wire_ops__L14);
    COMB__y__wire_ops__L15 = pyc_mux_5;
    y_reg__next = COMB__y__wire_ops__L15;
    pyc_comb_6 = pyc_constant_1;
    pyc_comb_7 = pyc_constant_2;
    pyc_comb_8 = y_reg__next;
  }

  inline void eval_comb_pass() {
    eval_comb_0();
    eval_comb_1();
  }

  void eval() {
    eval_comb_0();
    eval_comb_1();
    y = pyc_comb_10;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_reg_9_inst.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_reg_9_inst.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
