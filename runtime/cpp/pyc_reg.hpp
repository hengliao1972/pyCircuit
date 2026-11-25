#pragma once

#include "pyc_bits.hpp"
#include "pyc_component.hpp"

namespace pyc::cpp {

// Generated register. Staging and publish live here; the clock-edge protocol
// comes from ClockedElement, which calls sample() and tick_commit().
template <unsigned Width>
class pycReg : public ClockedElement<pycReg<Width>> {
  using Base = ClockedElement<pycReg<Width>>;
  using change_mask_t = typename Base::change_mask_t;

public:
  pycReg(Wire<1> &clk, Wire<1> &rst, Wire<1> &en, Wire<Width> &d,
         Wire<Width> &init, Wire<Width> &q)
      : Base(clk), rst(rst), en(en), d(d), init(init), q(q) {}

  // Applies a pending edge and reports whether the visible output changed.
  inline change_mask_t tick_commit() {
    if (__builtin_expect(!this->pending, 1))
      return 0;
    this->pending = false;
    const bool changed = q != qNext;
    if (changed)
      q = qNext;
    return changed ? Base::kChanged : change_mask_t{0};
  }

  bool sample() {
    const bool r = rst.toBool();
    const bool e = en.toBool();
    if (r)
      qNext = init;
    else
      qNext = d;
    return r | e;
  }

  Wire<1> &rst;
  Wire<1> &en;
  Wire<Width> &d;
  Wire<Width> &init;
  Wire<Width> &q;

  Wire<Width> qNext{};
};

} // namespace pyc::cpp
