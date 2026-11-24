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
  using change_mask_t = std::uint64_t;
  static constexpr change_mask_t kChanged = change_mask_t{1};

  void eval() {}
};

enum ComponentFlags : std::uint32_t {
  kHasEval = 1u << 0,        // eval() is not the empty default
  kEdgeOptimized = 1u << 1,  // component is driven from a classified clock edge
  kHasTap = 1u << 2,         // tap()/tap_next()/has_pending() exist
  kIsModule = 1u << 3,       // hierarchical instance (eval + tick + commit)
  kIsCombOnly = 1u << 4,     // stateless module: no tick/commit walk entry
};

// Bit for the i-th visible output.
inline constexpr BaseElement::change_mask_t change_bit(unsigned i) {
  return BaseElement::change_mask_t{1} << i;
}

// Edge detection for one clock domain. A free function rather than a type: the
// only state is the previous clock level, which belongs to the component that
// owns the wire. Dual-clock components call this once per domain with their own
// previous-level flags.
inline bool edge_rise(bool &clkPrev, bool clkNow) {
  const bool isRise = !clkPrev && clkNow;
  clkPrev = clkNow;
  return isRise;
}

// Clocked single-port element: owns the clock wiring, the previous clock level
// and the pending flag. Only the level is kept — rising/falling edges are
// derived from it, never tracked as separate entry points. Derived implements
// sample() (stage next state, return "edge is pending").
template <class Derived>
class ClockedElement : public BaseElement {
public:
  explicit ClockedElement(Wire<1> &clk) : clk(clk) {}

  inline void tick_compute() { tick_compute(clk.toBool()); }
  inline void tick_compute(bool clkNow) {
    if (__builtin_expect(!edge_rise(clkPrev, clkNow), 1)) {
      pending = false;
      return;
    }
    pending = static_cast<Derived *>(this)->sample();
  }

  // Single-phase update: sample and publish in one call, returning the change
  // mask. Legal only when the emitter has proven that no reader of this
  // component can observe the new value inside the same pass -- i.e. the state
  // dependency graph is acyclic and the pass runs readers before producers.
  // Components in a dependency cycle keep the two-call fallback
  // (tick_compute() then tick_commit()).
  inline change_mask_t evo(bool clkNow) {
    tick_compute(clkNow);
    return static_cast<Derived *>(this)->tick_commit();
  }
  inline change_mask_t evo() { return evo(clk.toBool()); }

  Wire<1> &clk;
  bool clkPrev = false;
  bool pending = false;
};

} // namespace pyc::cpp
