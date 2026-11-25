#pragma once

#include <array>

#include "pyc_bits.hpp"
#include "pyc_clock.hpp"
#include "pyc_component.hpp"

namespace pyc::cpp {

// CDC synchronizer modeled as a destination-clocked flop pipeline.
template <class ValT, unsigned Stages>
class CdcBase : public ClockedElement<CdcBase<ValT, Stages>> {
  using Base = ClockedElement<CdcBase<ValT, Stages>>;
  using change_mask_t = typename Base::change_mask_t;

  static_assert(Stages >= 1, "CdcBase requires Stages >= 1");

public:
  CdcBase(Wire<1> &clk, Wire<1> &rst, ValT &in, ValT &out)
      : Base(clk), rst(rst), in(in), out(out) {
    for (auto &e : pipe_)
      e = ValT(0);
    out = ValT(0);
  }

  // Every destination-clock edge is pending: the pipeline always advances.
  bool sample() {
    if (rst.toBool()) {
      for (auto &e : pipeNext_)
        e = ValT(0);
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
  ValT &in;
  ValT &out;

private:
  std::array<ValT, Stages> pipe_{};
  std::array<ValT, Stages> pipeNext_{};
};

template <unsigned Width, unsigned Stages>
class pycCdcSync : public CdcBase<Wire<Width>, Stages> {
  using Base = CdcBase<Wire<Width>, Stages>;

public:
  static_assert(Width > 0 && Width <= 64,
                "pycCdcSync supports widths 1..64 in the prototype");
  using Base::Base;
};

} // namespace pyc::cpp
