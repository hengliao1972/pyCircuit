// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct Counter {
  pyc::cpp::Wire<1> clk{};
  pyc::cpp::Wire<1> rst{};
  pyc::cpp::Wire<1> en{};
  pyc::cpp::Wire<8> count{};

  pyc::cpp::Wire<8> COUNT__c__counter__L15{};
  pyc::cpp::Wire<8> count_2{};
  pyc::cpp::Wire<8> count__counter__L11{};
  pyc::cpp::Wire<8> count__next{};
  pyc::cpp::Wire<1> do__counter__L9{};
  pyc::cpp::Wire<8> pyc_add_9{};
  pyc::cpp::Wire<8> pyc_comb_11{};
  pyc::cpp::Wire<8> pyc_comb_12{};
  pyc::cpp::Wire<8> pyc_comb_4{};
  pyc::cpp::Wire<8> pyc_comb_5{};
  pyc::cpp::Wire<1> pyc_comb_6{};
  pyc::cpp::Wire<1> pyc_comb_7{};
  pyc::cpp::Wire<8> pyc_constant_1{};
  pyc::cpp::Wire<8> pyc_constant_2{};
  pyc::cpp::Wire<1> pyc_constant_3{};
  pyc::cpp::Wire<8> pyc_mux_10{};
  pyc::cpp::Wire<8> pyc_reg_8{};

  pyc::cpp::pyc_reg<8> pyc_reg_8_inst;

  Counter() :
      pyc_reg_8_inst(clk, rst, pyc_comb_6, count__next, pyc_comb_5, pyc_reg_8) {
    eval();
  }

  inline void eval_comb_0() {
    count_2 = pyc_reg_8;
    count__counter__L11 = count_2;
    COUNT__c__counter__L15 = count__counter__L11;
    pyc_add_9 = (COUNT__c__counter__L15 + pyc_comb_4);
    pyc_mux_10 = (pyc_comb_7.toBool() ? pyc_add_9 : count__counter__L11);
    pyc_comb_11 = count__counter__L11;
    pyc_comb_12 = pyc_mux_10;
  }

  inline void eval_comb_1() {
    pyc_constant_1 = pyc::cpp::Wire<8>({0x1ull});
    pyc_constant_2 = pyc::cpp::Wire<8>({0x0ull});
    pyc_constant_3 = pyc::cpp::Wire<1>({0x1ull});
    do__counter__L9 = en;
    pyc_comb_4 = pyc_constant_1;
    pyc_comb_5 = pyc_constant_2;
    pyc_comb_6 = pyc_constant_3;
    pyc_comb_7 = do__counter__L9;
  }

  inline void eval_comb_pass() {
    eval_comb_1();
    eval_comb_0();
    count__next = pyc_comb_12;
  }

  void eval() {
    eval_comb_1();
    eval_comb_0();
    count__next = pyc_comb_12;
    count = pyc_comb_11;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_reg_8_inst.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_reg_8_inst.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
