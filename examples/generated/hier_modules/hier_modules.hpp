// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct Core__p9d38a692 {
  pyc::cpp::Wire<1> clk{};
  pyc::cpp::Wire<1> rst{};
  pyc::cpp::Wire<1> in_valid{};
  pyc::cpp::Wire<8> in_data{};
  pyc::cpp::Wire<1> out_ready{};
  pyc::cpp::Wire<1> out_valid{};
  pyc::cpp::Wire<8> out_data{};

  pyc::cpp::Wire<8> data_next__hier_modules__L16{};
  pyc::cpp::Wire<8> data_q__hier_modules__L18{};
  pyc::cpp::Wire<1> fire__hier_modules__L15{};
  pyc::cpp::Wire<8> in_data__hier_modules__L12{};
  pyc::cpp::Wire<1> in_valid__hier_modules__L11{};
  pyc::cpp::Wire<1> out_ready__hier_modules__L13{};
  pyc::cpp::Wire<8> pyc_add_6{};
  pyc::cpp::Wire<1> pyc_and_5{};
  pyc::cpp::Wire<1> pyc_comb_10{};
  pyc::cpp::Wire<8> pyc_comb_11{};
  pyc::cpp::Wire<1> pyc_comb_7{};
  pyc::cpp::Wire<1> pyc_comb_8{};
  pyc::cpp::Wire<8> pyc_comb_9{};
  pyc::cpp::Wire<1> pyc_constant_1{};
  pyc::cpp::Wire<1> pyc_constant_2{};
  pyc::cpp::Wire<8> pyc_constant_3{};
  pyc::cpp::Wire<8> pyc_constant_4{};
  pyc::cpp::Wire<8> pyc_reg_12{};
  pyc::cpp::Wire<1> pyc_reg_13{};
  pyc::cpp::Wire<1> valid_q__hier_modules__L19{};

  pyc::cpp::pyc_reg<8> pyc_reg_12_inst;
  pyc::cpp::pyc_reg<1> pyc_reg_13_inst;

  Core__p9d38a692() :
      pyc_reg_12_inst(clk, rst, pyc_comb_10, pyc_comb_11, pyc_comb_9, pyc_reg_12),
      pyc_reg_13_inst(clk, rst, pyc_comb_8, pyc_comb_10, pyc_comb_7, pyc_reg_13) {
    eval();
  }

  inline void eval_comb_0() {
    pyc_constant_1 = pyc::cpp::Wire<1>({0x0ull});
    pyc_constant_2 = pyc::cpp::Wire<1>({0x1ull});
    pyc_constant_3 = pyc::cpp::Wire<8>({0x0ull});
    pyc_constant_4 = pyc::cpp::Wire<8>({0x1ull});
    in_valid__hier_modules__L11 = in_valid;
    in_data__hier_modules__L12 = in_data;
    out_ready__hier_modules__L13 = out_ready;
    pyc_and_5 = (in_valid__hier_modules__L11 & out_ready__hier_modules__L13);
    fire__hier_modules__L15 = pyc_and_5;
    pyc_add_6 = (in_data__hier_modules__L12 + pyc_constant_4);
    data_next__hier_modules__L16 = pyc_add_6;
    pyc_comb_7 = pyc_constant_1;
    pyc_comb_8 = pyc_constant_2;
    pyc_comb_9 = pyc_constant_3;
    pyc_comb_10 = fire__hier_modules__L15;
    pyc_comb_11 = data_next__hier_modules__L16;
  }

  inline void eval_comb_pass() {
    data_q__hier_modules__L18 = pyc_reg_12;
    eval_comb_0();
    valid_q__hier_modules__L19 = pyc_reg_13;
  }

  void eval() {
    data_q__hier_modules__L18 = pyc_reg_12;
    eval_comb_0();
    valid_q__hier_modules__L19 = pyc_reg_13;
    out_valid = valid_q__hier_modules__L19;
    out_data = data_q__hier_modules__L18;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_reg_12_inst.tick_compute();
    pyc_reg_13_inst.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_reg_12_inst.tick_commit();
    pyc_reg_13_inst.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

struct HierModules {
  pyc::cpp::Wire<1> clk{};
  pyc::cpp::Wire<1> rst{};
  pyc::cpp::Wire<1> in_valid{};
  pyc::cpp::Wire<8> in_data{};
  pyc::cpp::Wire<1> out_ready{};
  pyc::cpp::Wire<1> out_valid{};
  pyc::cpp::Wire<8> out_data{};

  pyc::cpp::Wire<1> pyc_comb_13{};
  pyc::cpp::Wire<8> pyc_comb_14{};
  pyc::cpp::Wire<1> pyc_comb_19{};
  pyc::cpp::Wire<8> pyc_comb_20{};
  pyc::cpp::Wire<1> pyc_comb_7{};
  pyc::cpp::Wire<8> pyc_comb_8{};
  pyc::cpp::Wire<1> pyc_instance_1{};
  pyc::cpp::Wire<8> pyc_instance_10{};
  pyc::cpp::Wire<1> pyc_instance_15{};
  pyc::cpp::Wire<8> pyc_instance_16{};
  pyc::cpp::Wire<8> pyc_instance_2{};
  pyc::cpp::Wire<1> pyc_instance_3{};
  pyc::cpp::Wire<8> pyc_instance_4{};
  pyc::cpp::Wire<1> pyc_instance_9{};
  pyc::cpp::Wire<1> pyc_or_11{};
  pyc::cpp::Wire<1> pyc_or_17{};
  pyc::cpp::Wire<1> pyc_or_5{};
  pyc::cpp::Wire<8> pyc_xor_12{};
  pyc::cpp::Wire<8> pyc_xor_18{};
  pyc::cpp::Wire<8> pyc_xor_6{};

  // Sub-modules.
  Core__p9d38a692 core0{};
  Core__p9d38a692 core1{};
  Core__p9d38a692 core2{};
  Core__p9d38a692 core3{};


  HierModules() {
    eval();
  }

  inline void eval_comb_0() {
    pyc_or_11 = (pyc_comb_7 | pyc_instance_9);
    pyc_xor_12 = (pyc_comb_8 ^ pyc_instance_10);
    pyc_comb_13 = pyc_or_11;
    pyc_comb_14 = pyc_xor_12;
  }

  inline void eval_comb_1() {
    pyc_or_17 = (pyc_comb_13 | pyc_instance_15);
    pyc_xor_18 = (pyc_comb_14 ^ pyc_instance_16);
    pyc_comb_19 = pyc_or_17;
    pyc_comb_20 = pyc_xor_18;
  }

  inline void eval_comb_2() {
    pyc_or_5 = (pyc_instance_1 | pyc_instance_3);
    pyc_xor_6 = (pyc_instance_2 ^ pyc_instance_4);
    pyc_comb_7 = pyc_or_5;
    pyc_comb_8 = pyc_xor_6;
  }

  inline void eval_comb_pass() {
    eval_comb_2();
    eval_comb_0();
    eval_comb_1();
  }

  void eval() {
    core0.clk = clk;
    core0.rst = rst;
    core0.in_valid = in_valid;
    core0.in_data = in_data;
    core0.out_ready = out_ready;
    core0.eval();
    pyc_instance_1 = core0.out_valid;
    pyc_instance_2 = core0.out_data;
    core3.clk = clk;
    core3.rst = rst;
    core3.in_valid = in_valid;
    core3.in_data = in_data;
    core3.out_ready = out_ready;
    core3.eval();
    pyc_instance_15 = core3.out_valid;
    pyc_instance_16 = core3.out_data;
    core1.clk = clk;
    core1.rst = rst;
    core1.in_valid = in_valid;
    core1.in_data = in_data;
    core1.out_ready = out_ready;
    core1.eval();
    pyc_instance_3 = core1.out_valid;
    pyc_instance_4 = core1.out_data;
    eval_comb_2();
    core2.clk = clk;
    core2.rst = rst;
    core2.in_valid = in_valid;
    core2.in_data = in_data;
    core2.out_ready = out_ready;
    core2.eval();
    pyc_instance_9 = core2.out_valid;
    pyc_instance_10 = core2.out_data;
    eval_comb_0();
    eval_comb_1();
    out_valid = pyc_comb_19;
    out_data = pyc_comb_20;
  }

  void tick_compute() {
    // Sub-modules.
    core0.clk = clk;
    core0.rst = rst;
    core0.in_valid = in_valid;
    core0.in_data = in_data;
    core0.out_ready = out_ready;
    core0.tick_compute();
    core1.clk = clk;
    core1.rst = rst;
    core1.in_valid = in_valid;
    core1.in_data = in_data;
    core1.out_ready = out_ready;
    core1.tick_compute();
    core2.clk = clk;
    core2.rst = rst;
    core2.in_valid = in_valid;
    core2.in_data = in_data;
    core2.out_ready = out_ready;
    core2.tick_compute();
    core3.clk = clk;
    core3.rst = rst;
    core3.in_valid = in_valid;
    core3.in_data = in_data;
    core3.out_ready = out_ready;
    core3.tick_compute();
    // Local sequential primitives.
  }

  void tick_commit() {
    // Sub-modules.
    core0.tick_commit();
    core1.tick_commit();
    core2.tick_commit();
    core3.tick_commit();
    // Local sequential primitives.
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
