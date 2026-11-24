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

// Edge detector for one clock domain. A plain value type, deliberately not a
// component: a component *has* one (a dual-clock component holds two), it is
// not one. Keeping it out of the hierarchy lets the same detector serve
// components that do not share the ClockedElement protocol (memories,
// channels) without inventing a common ancestor for them.
class ClockEdge {
public:
  explicit ClockEdge(Wire<1> &clk) : clk(clk) {}

  // Returns true on a 0->1 transition and latches the new clock level.
  inline bool rise(bool clkNow) {
    const bool isRise = !clkPrev && clkNow;
    clkPrev = clkNow;
    return isRise;
  }

  Wire<1> &clk;
  bool clkPrev = false;
};

// Clocked single-port element: owns the four edge verbs, the pending flag and
// one edge detector. Derived implements sample() (stage next state, return
// "edge is pending").
template <class Derived>
class ClockedElement : public ComponentBase {
public:
  explicit ClockedElement(Wire<1> &clk) : edge(clk) {}

  inline void tick_compute() { tick_compute(edge.clk.toBool()); }
  inline void tick_compute(bool clkNow) {
    if (__builtin_expect(!edge.rise(clkNow), 1)) {
      pending = false;
      return;
    }
    pending = static_cast<Derived *>(this)->sample();
  }

  // Caller already observed a 0->1 edge (saves the clkPrev read and branch).
  inline void posedge_tick_compute() {
    edge.clkPrev = true;
    pending = static_cast<Derived *>(this)->sample();
  }

  // Falling-edge bookkeeping: just clear the latch so the next rise shows up.
  inline void negedge_update() {
    edge.clkPrev = false;
    pending = false;
  }

  ClockEdge edge;
  bool pending = false;
};

// Multi-port storage/channel base: owns one edge detector plus the bit
// numbering used to report changes on several visible outputs. Storage and
// phase logic stay in the derived class.
class PortBase : public ComponentBase {
public:
  explicit PortBase(Wire<1> &clk) : edge(clk) {}

protected:
  static constexpr change_mask_t portBit(unsigned i) {
    return change_mask_t{1} << i;
  }
  static inline void markPort(change_mask_t &mask, unsigned i) {
    mask |= portBit(i);
  }

public:
  ClockEdge edge;
};

} // namespace pyc::cpp
