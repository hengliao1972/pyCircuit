#pragma once

#include <array>

#include "pyc_bits.hpp"
#include "pyc_clock.hpp"
#include "pyc_component.hpp"

namespace pyc::cpp {

// CDC synchronizer modeled as a destination-clocked flop pipeline.
template <unsigned Width, unsigned Stages>
class pycCdcSync : public ClockedElement<pycCdcSync<Width, Stages>> {
  using Base = ClockedElement<pycCdcSync<Width, Stages>>;
  using change_mask_t = typename Base::change_mask_t;

  static_assert(Stages >= 1, "pycCdcSync requires Stages >= 1");
  static_assert(Width > 0 && Width <= 64,
                "pycCdcSync supports widths 1..64 in the prototype");

public:
  pycCdcSync(Wire<1> &clk, Wire<1> &rst, Wire<Width> &in, Wire<Width> &out)
      : Base(clk), rst(rst), in(in), out(out) {
    for (auto &e : pipe_)
      e = Wire<Width>(0);
    out = Wire<Width>(0);
  }

  // Every destination-clock edge is pending: the pipeline always advances.
  bool sample() {
    if (rst.toBool()) {
      for (auto &e : pipeNext_)
        e = Wire<Width>(0);
      return true;
    }
    pipeNext_[0] = in;
    for (unsigned i = 1; i < Stages; i++)
      pipeNext_[i] = pipe_[i - 1];
    return true;
  }

  // Applies a destination-clock edge and reports whether out changed.
  inline change_mask_t tick_commit() {
    if (!this->pending)
      return 0;
    for (unsigned i = 0; i < Stages; i++)
      pipe_[i] = pipeNext_[i];
    const bool changed = out != pipe_[Stages - 1];
    out = pipe_[Stages - 1];
    this->pending = false;
    return changed ? Base::kChanged : change_mask_t{0};
  }

  Wire<1> &rst;
  Wire<Width> &in;
  Wire<Width> &out;

private:
  std::array<Wire<Width>, Stages> pipe_{};
  std::array<Wire<Width>, Stages> pipeNext_{};
};

} // namespace pyc::cpp
