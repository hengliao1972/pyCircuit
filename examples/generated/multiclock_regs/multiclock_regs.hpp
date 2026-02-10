// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct multiclock_regs {
  pyc::cpp::Wire<1> clk_a_clk{};
  pyc::cpp::Wire<1> clk_a_rst{};
  pyc::cpp::Wire<1> clk_b_clk{};
  pyc::cpp::Wire<1> clk_b_rst{};
  pyc::cpp::Wire<8> a_count{};
  pyc::cpp::Wire<8> b_count{};

  pyc::cpp::Wire<8> a_next__multiclock_regs__L28{};
  pyc::cpp::Wire<8> a_reg__multiclock_regs__L32{};
  pyc::cpp::Wire<8> a_val__multiclock_regs__L27{};
  pyc::cpp::Wire<8> b_next__multiclock_regs__L39{};
  pyc::cpp::Wire<8> b_reg__multiclock_regs__L43{};
  pyc::cpp::Wire<8> b_val__multiclock_regs__L38{};
  pyc::cpp::Wire<8> pyc_add_10{};
  pyc::cpp::Wire<8> pyc_add_4{};
  pyc::cpp::Wire<8> pyc_comb_11{};
  pyc::cpp::Wire<8> pyc_comb_12{};
  pyc::cpp::Wire<1> pyc_comb_5{};
  pyc::cpp::Wire<8> pyc_comb_6{};
  pyc::cpp::Wire<8> pyc_comb_7{};
  pyc::cpp::Wire<8> pyc_comb_8{};
  pyc::cpp::Wire<1> pyc_constant_1{};
  pyc::cpp::Wire<8> pyc_constant_2{};
  pyc::cpp::Wire<8> pyc_constant_3{};
  pyc::cpp::Wire<8> pyc_reg_13{};
  pyc::cpp::Wire<8> pyc_reg_9{};

  pyc::cpp::pyc_reg<8> pyc_reg_13_inst;
  pyc::cpp::pyc_reg<8> pyc_reg_9_inst;

  multiclock_regs() :
      pyc_reg_13_inst(clk_b_clk, clk_b_rst, pyc_comb_5, pyc_comb_12, pyc_comb_7, pyc_reg_13),
      pyc_reg_9_inst(clk_a_clk, clk_a_rst, pyc_comb_5, pyc_comb_8, pyc_comb_7, pyc_reg_9) {
    eval();
  }

  inline void eval_comb_0() {
    a_reg__multiclock_regs__L32 = pyc_reg_9;
    b_val__multiclock_regs__L38 = pyc_comb_7;
    pyc_add_10 = (b_val__multiclock_regs__L38 + pyc_comb_6);
    b_next__multiclock_regs__L39 = pyc_add_10;
    pyc_comb_11 = a_reg__multiclock_regs__L32;
    pyc_comb_12 = b_next__multiclock_regs__L39;
  }

  inline void eval_comb_1() {
    pyc_constant_1 = pyc::cpp::Wire<1>({0x1ull});
    pyc_constant_2 = pyc::cpp::Wire<8>({0x1ull});
    pyc_constant_3 = pyc::cpp::Wire<8>({0x0ull});
    a_val__multiclock_regs__L27 = pyc_constant_3;
    pyc_add_4 = (a_val__multiclock_regs__L27 + pyc_constant_2);
    a_next__multiclock_regs__L28 = pyc_add_4;
    pyc_comb_5 = pyc_constant_1;
    pyc_comb_6 = pyc_constant_2;
    pyc_comb_7 = pyc_constant_3;
    pyc_comb_8 = a_next__multiclock_regs__L28;
  }

  inline void eval_comb_pass() {
    b_reg__multiclock_regs__L43 = pyc_reg_13;
    eval_comb_1();
    eval_comb_0();
  }

  void eval() {
    b_reg__multiclock_regs__L43 = pyc_reg_13;
    eval_comb_1();
    eval_comb_0();
    a_count = pyc_comb_11;
    b_count = b_reg__multiclock_regs__L43;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_reg_13_inst.tick_compute();
    pyc_reg_9_inst.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_reg_13_inst.tick_commit();
    pyc_reg_9_inst.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
