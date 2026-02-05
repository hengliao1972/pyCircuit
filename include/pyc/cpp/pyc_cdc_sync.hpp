#pragma once

#include <array>

#include "pyc_bits.hpp"
#include "pyc_clock.hpp"

namespace pyc::cpp {

// CDC synchronizer modeled as a destination-clocked flop pipeline.
template <unsigned Width, unsigned Stages>
class pyc_cdc_sync {
public:
  static_assert(Width > 0 && Width <= 64, "pyc_cdc_sync supports widths 1..64 in the prototype");
  static_assert(Stages >= 1, "pyc_cdc_sync requires Stages >= 1");

  pyc_cdc_sync(Wire<1> &clk, Wire<1> &rst, Wire<Width> &in, Wire<Width> &out) : clk(clk), rst(rst), in(in), out(out) {
    for (auto &e : pipe_)
      e = Wire<Width>(0);
    out = Wire<Width>(0);
  }

  void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) && clkNow;
    clkPrev = clkNow;
    pending = false;
    if (!posedge)
      return;

    pending = true;
    if (rst.toBool()) {
      for (auto &e : pipeNext_)
        e = Wire<Width>(0);
      return;
    }

    pipeNext_[0] = in;
    for (unsigned i = 1; i < Stages; i++)
      pipeNext_[i] = pipe_[i - 1];
  }

  void tick_commit() {
    if (!pending)
      return;
    for (unsigned i = 0; i < Stages; i++)
      pipe_[i] = pipeNext_[i];
    out = pipe_[Stages - 1];
    pending = false;
  }

public:
  Wire<1> &clk;
  Wire<1> &rst;
  Wire<Width> &in;
  Wire<Width> &out;

  bool clkPrev = false;
  bool pending = false;

private:
  std::array<Wire<Width>, Stages> pipe_{};
  std::array<Wire<Width>, Stages> pipeNext_{};
};

} // namespace pyc::cpp

