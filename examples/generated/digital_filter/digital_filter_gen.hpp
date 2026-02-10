// pyCircuit C++ emission (prototype)
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct digital_filter {
  pyc::cpp::Wire<1> clk{};
  pyc::cpp::Wire<1> rst{};
  pyc::cpp::Wire<16> x_in{};
  pyc::cpp::Wire<1> x_valid{};
  pyc::cpp::Wire<34> y_out{};
  pyc::cpp::Wire<1> y_valid{};

  pyc::cpp::Wire<16> delay_1{};
  pyc::cpp::Wire<16> delay_2{};
  pyc::cpp::Wire<16> delay_3{};
  pyc::cpp::Wire<34> pyc_add_18{};
  pyc::cpp::Wire<34> pyc_add_21{};
  pyc::cpp::Wire<34> pyc_add_24{};
  pyc::cpp::Wire<34> pyc_comb_10{};
  pyc::cpp::Wire<1> pyc_comb_11{};
  pyc::cpp::Wire<16> pyc_comb_12{};
  pyc::cpp::Wire<1> pyc_comb_13{};
  pyc::cpp::Wire<34> pyc_comb_14{};
  pyc::cpp::Wire<34> pyc_comb_25{};
  pyc::cpp::Wire<34> pyc_comb_8{};
  pyc::cpp::Wire<34> pyc_comb_9{};
  pyc::cpp::Wire<34> pyc_constant_1{};
  pyc::cpp::Wire<34> pyc_constant_2{};
  pyc::cpp::Wire<34> pyc_constant_3{};
  pyc::cpp::Wire<1> pyc_constant_4{};
  pyc::cpp::Wire<16> pyc_constant_5{};
  pyc::cpp::Wire<1> pyc_constant_6{};
  pyc::cpp::Wire<34> pyc_constant_7{};
  pyc::cpp::Wire<34> pyc_mul_17{};
  pyc::cpp::Wire<34> pyc_mul_20{};
  pyc::cpp::Wire<34> pyc_mul_23{};
  pyc::cpp::Wire<16> pyc_mux_26{};
  pyc::cpp::Wire<16> pyc_mux_28{};
  pyc::cpp::Wire<16> pyc_mux_30{};
  pyc::cpp::Wire<34> pyc_mux_32{};
  pyc::cpp::Wire<16> pyc_reg_27{};
  pyc::cpp::Wire<16> pyc_reg_29{};
  pyc::cpp::Wire<16> pyc_reg_31{};
  pyc::cpp::Wire<34> pyc_reg_33{};
  pyc::cpp::Wire<1> pyc_reg_34{};
  pyc::cpp::Wire<34> pyc_sext_15{};
  pyc::cpp::Wire<34> pyc_sext_16{};
  pyc::cpp::Wire<34> pyc_sext_19{};
  pyc::cpp::Wire<34> pyc_sext_22{};
  pyc::cpp::Wire<34> y_out_reg{};
  pyc::cpp::Wire<1> y_valid_reg{};

  pyc::cpp::pyc_reg<16> pyc_reg_27_inst;
  pyc::cpp::pyc_reg<16> pyc_reg_29_inst;
  pyc::cpp::pyc_reg<16> pyc_reg_31_inst;
  pyc::cpp::pyc_reg<34> pyc_reg_33_inst;
  pyc::cpp::pyc_reg<1> pyc_reg_34_inst;

  digital_filter() :
      pyc_reg_27_inst(clk, rst, pyc_comb_13, pyc_mux_26, pyc_comb_12, pyc_reg_27),
      pyc_reg_29_inst(clk, rst, pyc_comb_13, pyc_mux_28, pyc_comb_12, pyc_reg_29),
      pyc_reg_31_inst(clk, rst, pyc_comb_13, pyc_mux_30, pyc_comb_12, pyc_reg_31),
      pyc_reg_33_inst(clk, rst, pyc_comb_13, pyc_mux_32, pyc_comb_14, pyc_reg_33),
      pyc_reg_34_inst(clk, rst, pyc_comb_13, x_valid, pyc_comb_11, pyc_reg_34) {
    eval();
  }

  inline void eval_comb_0() {
    pyc_sext_15 = pyc::cpp::sext<34, 16>(x_in);
    pyc_sext_16 = pyc::cpp::sext<34, 16>(delay_1);
    pyc_mul_17 = (pyc_sext_16 * pyc_comb_10);
    pyc_add_18 = (pyc_sext_15 + pyc_mul_17);
    pyc_sext_19 = pyc::cpp::sext<34, 16>(delay_2);
    pyc_mul_20 = (pyc_sext_19 * pyc_comb_9);
    pyc_add_21 = (pyc_add_18 + pyc_mul_20);
    pyc_sext_22 = pyc::cpp::sext<34, 16>(delay_3);
    pyc_mul_23 = (pyc_sext_22 * pyc_comb_8);
    pyc_add_24 = (pyc_add_21 + pyc_mul_23);
    pyc_comb_25 = pyc_add_24;
  }

  inline void eval_comb_1() {
    pyc_constant_1 = pyc::cpp::Wire<34>({0x4ull});
    pyc_constant_2 = pyc::cpp::Wire<34>({0x3ull});
    pyc_constant_3 = pyc::cpp::Wire<34>({0x2ull});
    pyc_constant_4 = pyc::cpp::Wire<1>({0x0ull});
    pyc_constant_5 = pyc::cpp::Wire<16>({0x0ull});
    pyc_constant_6 = pyc::cpp::Wire<1>({0x1ull});
    pyc_constant_7 = pyc::cpp::Wire<34>({0x0ull});
    pyc_comb_8 = pyc_constant_1;
    pyc_comb_9 = pyc_constant_2;
    pyc_comb_10 = pyc_constant_3;
    pyc_comb_11 = pyc_constant_4;
    pyc_comb_12 = pyc_constant_5;
    pyc_comb_13 = pyc_constant_6;
    pyc_comb_14 = pyc_constant_7;
  }

  inline void eval_comb_pass() {
    delay_1 = pyc_reg_27;
    delay_2 = pyc_reg_29;
    delay_3 = pyc_reg_31;
    eval_comb_1();
    eval_comb_0();
    pyc_mux_26 = (x_valid.toBool() ? x_in : delay_1);
    pyc_mux_28 = (x_valid.toBool() ? delay_1 : delay_2);
    pyc_mux_30 = (x_valid.toBool() ? delay_2 : delay_3);
    y_out_reg = pyc_reg_33;
    pyc_mux_32 = (x_valid.toBool() ? pyc_comb_25 : y_out_reg);
    y_valid_reg = pyc_reg_34;
  }

  void eval() {
    delay_1 = pyc_reg_27;
    delay_2 = pyc_reg_29;
    delay_3 = pyc_reg_31;
    eval_comb_1();
    eval_comb_0();
    pyc_mux_26 = (x_valid.toBool() ? x_in : delay_1);
    pyc_mux_28 = (x_valid.toBool() ? delay_1 : delay_2);
    pyc_mux_30 = (x_valid.toBool() ? delay_2 : delay_3);
    y_out_reg = pyc_reg_33;
    pyc_mux_32 = (x_valid.toBool() ? pyc_comb_25 : y_out_reg);
    y_valid_reg = pyc_reg_34;
    y_out = y_out_reg;
    y_valid = y_valid_reg;
  }

  void tick() {
    // Two-phase update: compute next state for all sequential elements,
    // then commit together. This avoids ordering artifacts between regs.
    // Phase 1: compute.
    pyc_reg_27_inst.tick_compute();
    pyc_reg_29_inst.tick_compute();
    pyc_reg_31_inst.tick_compute();
    pyc_reg_33_inst.tick_compute();
    pyc_reg_34_inst.tick_compute();
    // Phase 2: commit.
    pyc_reg_27_inst.tick_commit();
    pyc_reg_29_inst.tick_commit();
    pyc_reg_31_inst.tick_commit();
    pyc_reg_33_inst.tick_commit();
    pyc_reg_34_inst.tick_commit();
  }
};

} // namespace pyc::gen
