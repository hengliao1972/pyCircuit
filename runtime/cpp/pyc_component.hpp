#pragma once

#include <cstdint>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// Shared vocabulary for simulated components (leaf primitives and module
// instances). Deliberately data-free and virtual-free: generated models need a
// uniform protocol, not runtime polymorphism.
//
// Hot path:
//   tick_compute(bool clkNow)   generic path; clocked leaves detect the edge
//   tick_compute()              convenience overload reading the clock wire
//   change_mask_t tick_commit() apply staged sampling, report changed outputs
//
// Optional capabilities are declared with ComponentFlags; callers must query
// them at generation time (never at run time).
struct ComponentBase {
  // Bit i reports a change on the i-th visible output. Single-output
  // components always use bit 0.
  using change_mask_t = std::uint64_t;
  static constexpr change_mask_t kChanged = change_mask_t{1};
};

enum ComponentFlags : std::uint32_t {
  kHasEval = 1u << 0,        // eval() recomputes combinational reads
  kEdgeOptimized = 1u << 1,  // posedge_tick_compute()/negedge_update() exist
  kHasTap = 1u << 2,         // tap()/tap_next()/has_pending() exist
  kIsModule = 1u << 3,       // hierarchical instance (eval + tick + commit)
  kIsCombOnly = 1u << 4,     // stateless module: no tick/commit walk entry
};

// Edge detection for one clock domain. A free function rather than a type: the
// state (previous level) belongs to whichever component owns the clock wire,
// so a wrapper object would only add indirection. Dual-clock components call
// this once per domain with their own previous-level flags.
inline bool edge_rise(bool &clkPrev, bool clkNow) {
  const bool isRise = !clkPrev && clkNow;
  clkPrev = clkNow;
  return isRise;
}

// Clocked single-port element: owns the clock wiring, the four edge verbs and
// the pending flag. Derived implements sample() (stage next state, return
// "edge is pending").
template <class Derived>
class ClockedElement : public ComponentBase {
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

  // Caller already observed a 0->1 edge (saves the clkPrev read and branch).
  inline void posedge_tick_compute() {
    clkPrev = true;
    pending = static_cast<Derived *>(this)->sample();
  }

  // Falling-edge bookkeeping: just clear the latch so the next rise shows up.
  inline void negedge_update() {
    clkPrev = false;
    pending = false;
  }

  Wire<1> &clk;
  bool clkPrev = false;
  bool pending = false;
};

// Multi-port storage/channel base: owns the clock wiring plus the bit
// numbering used to report changes on several visible outputs. Storage and
// phase logic stay in the derived class.
class PortBase : public ComponentBase {
public:
  explicit PortBase(Wire<1> &clk) : clk(clk) {}

protected:
  static constexpr change_mask_t portBit(unsigned i) {
    return change_mask_t{1} << i;
  }
  static inline void markPort(change_mask_t &mask, unsigned i) {
    mask |= portBit(i);
  }

public:
  Wire<1> &clk;
  bool clkPrev = false;
};

} // namespace pyc::cpp
