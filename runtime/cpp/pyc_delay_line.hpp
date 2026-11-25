#pragma once

#include <array>

#include "pyc_bits.hpp"
#include "pyc_component.hpp"

namespace pyc::cpp {

// Simulator representation of DEPTH cascaded registers. A circular buffer
// makes the normal enabled edge O(1); reset still initializes every stage so
// reset/enable and compute-before-commit semantics match pycReg chains.
template <class ValT, unsigned Depth>
class DelayBase : public ClockedElement<DelayBase<ValT, Depth>> {
  using Base = ClockedElement<DelayBase<ValT, Depth>>;
  using change_mask_t = typename Base::change_mask_t;

  static_assert(Depth > 1, "DelayBase requires Depth > 1");

public:
  DelayBase(Wire<1> &clk, Wire<1> &rst, Wire<1> &en, ValT &d, ValT &init,
            ValT &q)
      : Base(clk), rst(rst), en(en), d(d), init(init), q(q) {}

  // True while an enabled edge is waiting for tick_commit. Tap consumers must
  // wake on this (every enabled edge shifts every tap).
  inline bool has_pending() const { return this->pending; }

  // Applies a pending edge and reports whether the tail q changed; tap
  // consumers wake via has_pending() instead (see the scalar note above).
  inline change_mask_t tick_commit() {
    if (__builtin_expect(!this->pending, 1))
      return 0;
    const bool changed = q != qNext;
    if (pendingReset) {
      stages.fill(init);
      head = 0;
    } else {
      stages[head] = sampledInput;
      head = nextIndex(head);
    }
    q = qNext;
    this->pending = false;
    return changed ? Base::kChanged : change_mask_t{0};
  }

  inline ValT tap(unsigned depth) const {
    return stages[(head + Depth - depth) % Depth];
  }

  inline ValT tap_next(unsigned depth) const {
    if (pendingReset)
      return init;
    if (!this->pending)
      return tap(depth);
    return depth == 1 ? sampledInput : tap(depth - 1);
  }

  bool sample() {
    pendingReset = rst.toBool();
    const bool enabled = en.toBool();
    if (pendingReset) {
      qNext = init;
      return true;
    }
    if (enabled) {
      sampledInput = d;
      qNext = stages[nextIndex(head)];
    }
    return enabled;
  }

  Wire<1> &rst;
  Wire<1> &en;
  ValT &d;
  ValT &init;
  ValT &q;
  bool pendingReset = false;
  ValT qNext{};
  ValT sampledInput{};
  std::array<ValT, Depth> stages{};
  unsigned head = 0;

private:
  static constexpr unsigned nextIndex(unsigned index) {
    return index + 1u == Depth ? 0u : index + 1u;
  }
};

template <unsigned Width, unsigned Depth>
class pycDelayLine : public DelayBase<Wire<Width>, Depth> {
  using Base = DelayBase<Wire<Width>, Depth>;

public:
  using Base::Base;
};

} // namespace pyc::cpp
