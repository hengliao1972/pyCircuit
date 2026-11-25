#pragma once

#include "pyc_bits.hpp"
#include "pyc_component.hpp"

namespace pyc::cpp {

// Register semantics shared by the register leaves: staging/publish lives here
// once and the leaf stays a 0-line derivation that keeps its generated
// constructor signature. The edge protocol lives in ClockedElement; derived
// classes supply sample().
template <class ValT>
class RegBase : public ClockedElement<RegBase<ValT>> {
  using Base = ClockedElement<RegBase<ValT>>;
  using change_mask_t = typename Base::change_mask_t;

public:
  RegBase(Wire<1> &clk, Wire<1> &rst, Wire<1> &en, ValT &d, ValT &init, ValT &q)
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
  ValT &d;
  ValT &init;
  ValT &q;

  ValT qNext{};
};

template <unsigned Width>
class pycReg : public RegBase<Wire<Width>> {
  using Base = RegBase<Wire<Width>>;

public:
  using Base::Base;
};

} // namespace pyc::cpp
