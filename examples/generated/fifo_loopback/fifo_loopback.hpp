// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct FifoLoopback {
  pyc::cpp::Wire<1> clk{};
  pyc::cpp::Wire<1> rst{};
  pyc::cpp::Wire<1> in_valid{};
  pyc::cpp::Wire<8> in_data{};
  pyc::cpp::Wire<1> out_ready{};
  pyc::cpp::Wire<1> in_ready{};
  pyc::cpp::Wire<1> out_valid{};
  pyc::cpp::Wire<8> out_data{};

  pyc::cpp::Wire<8> in_data__fifo_loopback__L11{};
  pyc::cpp::Wire<1> in_valid__fifo_loopback__L10{};
  pyc::cpp::Wire<1> out_ready__fifo_loopback__L12{};
  pyc::cpp::Wire<1> pyc_comb_1{};
  pyc::cpp::Wire<8> pyc_comb_2{};
  pyc::cpp::Wire<1> pyc_comb_3{};
  pyc::cpp::Wire<1> pyc_fifo_4{};
  pyc::cpp::Wire<1> pyc_fifo_5{};
  pyc::cpp::Wire<8> pyc_fifo_6{};
  pyc::cpp::Wire<8> q__in_data{};
  pyc::cpp::Wire<1> q__in_valid{};
  pyc::cpp::Wire<1> q__out_ready{};

  pyc::cpp::pyc_fifo<8, 2> pyc_fifo_4_inst;

  FifoLoopback() :
      pyc_fifo_4_inst(clk, rst, pyc_comb_1, pyc_fifo_4, pyc_comb_2, pyc_fifo_5, pyc_comb_3, pyc_fifo_6) {
    eval();
  }

  inline void eval_comb_0() {
    in_valid__fifo_loopback__L10 = in_valid;
    q__in_valid = in_valid__fifo_loopback__L10;
    in_data__fifo_loopback__L11 = in_data;
    q__in_data = in_data__fifo_loopback__L11;
    out_ready__fifo_loopback__L12 = out_ready;
    q__out_ready = out_ready__fifo_loopback__L12;
    pyc_comb_1 = q__in_valid;
    pyc_comb_2 = q__in_data;
    pyc_comb_3 = q__out_ready;
  }

  inline void eval_comb_pass() {
    eval_comb_0();
  }

  void eval() {
    eval_comb_0();
    pyc_fifo_4_inst.eval();
    in_ready = pyc_fifo_4;
    out_valid = pyc_fifo_5;
    out_data = pyc_fifo_6;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_fifo_4_inst.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_fifo_4_inst.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
