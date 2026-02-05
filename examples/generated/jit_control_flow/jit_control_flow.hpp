// pyCircuit C++ emission (prototype)
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct JitControlFlow {
  pyc::cpp::Wire<8> a{};
  pyc::cpp::Wire<8> b{};
  pyc::cpp::Wire<8> out{};

  pyc::cpp::Wire<8> a__jit_control_flow__L7{};
  pyc::cpp::Wire<8> acc__jit_control_flow__L16{};
  pyc::cpp::Wire<8> acc__jit_control_flow__L17{};
  pyc::cpp::Wire<8> acc__jit_control_flow__L18{};
  pyc::cpp::Wire<8> acc__jit_control_flow__L18_2{};
  pyc::cpp::Wire<8> acc__jit_control_flow__L18_3{};
  pyc::cpp::Wire<8> acc__jit_control_flow__L18_4{};
  pyc::cpp::Wire<8> b__jit_control_flow__L8{};
  pyc::cpp::Wire<8> pyc_add_10{};
  pyc::cpp::Wire<8> pyc_add_11{};
  pyc::cpp::Wire<8> pyc_add_12{};
  pyc::cpp::Wire<8> pyc_add_3{};
  pyc::cpp::Wire<8> pyc_add_6{};
  pyc::cpp::Wire<8> pyc_add_7{};
  pyc::cpp::Wire<8> pyc_add_9{};
  pyc::cpp::Wire<8> pyc_comb_13{};
  pyc::cpp::Wire<8> pyc_constant_1{};
  pyc::cpp::Wire<8> pyc_constant_2{};
  pyc::cpp::Wire<8> pyc_lshri_4{};
  pyc::cpp::Wire<8> pyc_mux_8{};
  pyc::cpp::Wire<1> pyc_ult_5{};
  pyc::cpp::Wire<8> x__jit_control_flow__L10{};
  pyc::cpp::Wire<8> x__jit_control_flow__L11{};
  pyc::cpp::Wire<8> x__jit_control_flow__L12{};
  pyc::cpp::Wire<8> x__jit_control_flow__L14{};


  JitControlFlow() {
    eval();
  }

  inline void eval_comb_0() {
    pyc_constant_1 = pyc::cpp::Wire<8>(2ull);
    pyc_constant_2 = pyc::cpp::Wire<8>(1ull);
    a__jit_control_flow__L7 = a;
    b__jit_control_flow__L8 = b;
    pyc_add_3 = (a__jit_control_flow__L7 + b__jit_control_flow__L8);
    pyc_lshri_4 = pyc::cpp::lshr<8>(pyc_add_3, 1u);
    x__jit_control_flow__L10 = pyc_lshri_4;
    pyc_ult_5 = pyc::cpp::Wire<1>((a__jit_control_flow__L7.value() < b__jit_control_flow__L8.value()) ? 1u : 0u);
    pyc_add_6 = (x__jit_control_flow__L10 + pyc_constant_2);
    x__jit_control_flow__L12 = pyc_add_6;
    pyc_add_7 = (x__jit_control_flow__L10 + pyc_constant_1);
    x__jit_control_flow__L14 = pyc_add_7;
    pyc_mux_8 = (pyc_ult_5.toBool() ? x__jit_control_flow__L12 : x__jit_control_flow__L14);
    x__jit_control_flow__L11 = pyc_mux_8;
    acc__jit_control_flow__L16 = x__jit_control_flow__L11;
    pyc_add_9 = (acc__jit_control_flow__L16 + pyc_constant_2);
    acc__jit_control_flow__L18 = pyc_add_9;
    pyc_add_10 = (acc__jit_control_flow__L18 + pyc_constant_2);
    acc__jit_control_flow__L18_2 = pyc_add_10;
    pyc_add_11 = (acc__jit_control_flow__L18_2 + pyc_constant_2);
    acc__jit_control_flow__L18_3 = pyc_add_11;
    pyc_add_12 = (acc__jit_control_flow__L18_3 + pyc_constant_2);
    acc__jit_control_flow__L18_4 = pyc_add_12;
    acc__jit_control_flow__L17 = acc__jit_control_flow__L18_4;
    pyc_comb_13 = acc__jit_control_flow__L17;
  }

  inline void eval_comb_pass() {
    eval_comb_0();
  }

  void eval() {
    eval_comb_0();
    out = pyc_comb_13;
  }

  void tick() {
    // Two-phase update: compute next state for all sequential elements,
    // then commit together. This avoids ordering artifacts between regs.
    // Phase 1: compute.
    // Phase 2: commit.
  }
};

} // namespace pyc::gen
