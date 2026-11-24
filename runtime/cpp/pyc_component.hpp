#pragma once

#include <cstdint>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// Root of the simulated component family: leaf primitives and (later) module
// instances. Deliberately data-free and virtual-free — it carries the shared
// vocabulary, not behaviour, so generated code can treat every component
// uniformly without a vtable.
//
//   change_mask_t  bit i = i-th visible output changed (single output -> bit 0)
//   eval()         combinational read; default no-op, overridden only by
//                  components that actually publish combinational outputs
class BaseElement {
public:
  // One bit per visible output (single-output components use bit 0). 64 bits
  // matches the runtime's dirty/wake words, and no component reports more than
  // 64 outputs -- pycSyncMemNM asserts RPorts <= 64.
  using change_mask_t = std::uint64_t;
  static constexpr change_mask_t kChanged = change_mask_t{1};

  void eval() {}

  // Bit for the i-th visible output.
  static constexpr change_mask_t change_bit(unsigned i) {
    return change_mask_t{1} << i;
  }
};

enum ComponentFlags : std::uint32_t {
  kHasEval = 1u << 0,        // eval() is not the empty default
  kEdgeOptimized = 1u << 1,  // component is driven from a classified clock edge
  kHasTap = 1u << 2,         // tap()/tap_next()/has_pending() exist
  kIsModule = 1u << 3,       // hierarchical instance (eval + tick + commit)
  kIsCombOnly = 1u << 4,     // stateless module: no tick/commit walk entry
};

// Clocked single-port element: owns the clock wiring and exposes exactly three
// entries.
//
//   eval()         single phase: detect the edge, sample, publish, return the
//                  change mask. The parent schedule calls it where no reader can
//                  observe the new value in the same pass.
//   tick_sample()  fallback, first half: detects the edge and stages the next
//                  value. Called every step, so it also keeps the stored clock
//                  level in sync.
//   tick_commit()  fallback, second half: publish what was staged (derived).
template <class Derived>
class ClockedElement : public BaseElement {
public:
  using change_mask_t = BaseElement::change_mask_t;

  explicit ClockedElement(Wire<1> &clk) : clk(clk) {}

  inline change_mask_t eval() {
    if (__builtin_expect(!rise(clk.toBool()), 1))
      return 0;
    pending = static_cast<Derived *>(this)->sample();
    return static_cast<Derived *>(this)->tick_commit();
  }

  inline void tick_sample() {
    if (__builtin_expect(!rise(clk.toBool()), 1))
      return;
    pending = static_cast<Derived *>(this)->sample();
  }

  // Edge detection for this component's clock domain: the only state is the
  // previous level, which belongs to the component that owns the wire.
  inline bool rise(bool clkNow) {
    const bool isRise = !clkPrev && clkNow;
    clkPrev = clkNow;
    return isRise;
  }

  Wire<1> &clk;
  bool clkPrev = false;
  bool pending = false;
};

} // namespace pyc::cpp
