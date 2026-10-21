#pragma once

#include <cstdint>
#include <initializer_list>
#include <vector>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// State loops: all same-clock registers (RegLoop) or delay lines (DelayLoop)
// of one clock domain are updated by one generated loop instead of one call
// per object. Values are NOT merged in the IR: `q` stays the caller-owned
// Wire<W> (the model struct member), so comb reads, probe registry, trace and
// VCD paths need no adaptation.
//
// Semantics contract (matches the per-object pyc_reg / pyc_delay_line):
//   compute_rise()    == posedge_tick_compute() for every lane
//   compute_generic() == tick_compute() for every lane
//   negedge()         == negedge_update() for every lane
//   commit()          == tick_commit() for every lane; returns per-lane dirty
//     bitmaps (one bit per lane, 64 lanes per word) so the change-driven
//     fanout wake stays exactly as precise as the per-object path.

constexpr std::size_t loop_word_count(std::size_t lanes) {
  return (lanes + 63) / 64;
}

template <unsigned Width>
struct RegLoopLane {
  Wire<Width> *q;
  Wire<Width> *d;
  Wire<1> *rst;
  Wire<1> *en;
  const Wire<Width> *init;
};

template <unsigned Width>
class RegLoop {
  static_assert(Width > 0, "RegLoop requires Width > 0");

public:
  using Lane = RegLoopLane<Width>;

  // Bulk form used by generated code: add_lanes({a,b,c,d,e}, {f,g,h,i,j}).
  void add_lanes(std::initializer_list<Lane> lanes) {
    for (const Lane &lane : lanes) {
      lanes_.push_back(lane);
      qNext_.emplace_back();
      pending_.push_back(0);
    }
    dirty_.assign(loop_word_count(lanes_.size()), 0);
  }

  std::size_t size() const { return lanes_.size(); }

  // Caller guarantees a 0->1 edge just occurred on this loop's clock.
  void compute_rise() {
    clkPrev_ = true;
    const std::size_t n = lanes_.size();
    for (std::size_t i = 0; i < n; ++i) {
      const Lane &lane = lanes_[i];
      const bool r = lane.rst->toBool();
      const bool e = lane.en->toBool();
      pending_[i] = static_cast<char>(r | e);
      if (r)
        qNext_[i] = *lane.init;
      else
        qNext_[i] = *lane.d;
    }
  }

  // Generic path: caller only knows the current clock level.
  void compute_generic(bool clkNow) {
    const bool posedge = (!clkPrev_) & clkNow;
    clkPrev_ = clkNow;
    if (__builtin_expect(!posedge, 1)) {
      clear_pending();
      return;
    }
    compute_rise();
  }

  // Falling edge bookkeeping: next compute must re-detect the edge.
  void negedge() { clkPrev_ = false; clear_pending(); }

  // Applies pending updates, writes back through the lane's q pointer, and
  // returns the per-lane dirty bitmap (bit i set == lane i's q changed).
  const std::vector<std::uint64_t> &commit() {
    const std::size_t n = lanes_.size();
    dirty_.assign(loop_word_count(n), 0);
    for (std::size_t i = 0; i < n; ++i) {
      if (__builtin_expect(pending_[i] != 0, 0)) {
        Wire<Width> &q = *lanes_[i].q;
        const bool changed = q != qNext_[i];
        q = qNext_[i];
        pending_[i] = 0;
        if (changed)
          dirty_[i >> 6] |= std::uint64_t{1} << (i & 63);
      }
    }
    return dirty_;
  }

  // Tick-fuse single-phase update: sample rst/en/d and publish q in one pass,
  // returning the per-lane dirty bitmap. Only valid for lanes whose rst/en/d
  // are pre-edge snapshots (no dependency on any state output) and only right
  // after a rising edge; such a loop never uses compute_rise()/commit().
  const std::vector<std::uint64_t> &direct_commit() {
    const std::size_t n = lanes_.size();
    clkPrev_ = true;
    dirty_.assign(loop_word_count(n), 0);
    for (std::size_t i = 0; i < n; ++i) {
      const Lane &lane = lanes_[i];
      const bool r = lane.rst->toBool();
      if (!(r | lane.en->toBool()))
        continue;
      const Wire<Width> &nxt = r ? *lane.init : *lane.d;
      Wire<Width> &q = *lane.q;
      if (q != nxt) {
        q = nxt;
        dirty_[i >> 6] |= std::uint64_t{1} << (i & 63);
      }
    }
    return dirty_;
  }

  // Probe/trace write-intent bindings: stable addresses for lane i's pending
  // flag and sampled next value (mirror pyc_reg::pending/qNext). pending_
  // stores char with values exactly 0/1; bool has the same object
  // representation on the Itanium ABI and consumers only test truthiness.
  bool *lane_pending(std::size_t i) {
    return reinterpret_cast<bool *>(&pending_[i]);
  }
  Wire<Width> *lane_qnext(std::size_t i) { return &qNext_[i]; }

private:
  void clear_pending() { pending_.assign(lanes_.size(), 0); }

  std::vector<Lane> lanes_;
  std::vector<Wire<Width>> qNext_;
  std::vector<char> pending_;  // char (not vector<bool>): stable lane addresses
  std::vector<std::uint64_t> dirty_;
  bool clkPrev_ = false;
};

template <unsigned Width>
struct DelayLoopLane {
  Wire<Width> *q;
  Wire<Width> *d;
  Wire<1> *rst;
  Wire<1> *en;
  const Wire<Width> *init;
  unsigned depth;  // verifier-guaranteed > 1
};

// Per-lane depth varies, so stage storage is per-lane vectors and the ring
// index is per-lane state; everything else is loop-continuous.
template <unsigned Width>
class DelayLoop {
public:
  using Lane = DelayLoopLane<Width>;

  void add_lanes(std::initializer_list<Lane> lanes) {
    for (const Lane &lane : lanes) {
      lanes_.push_back(lane);
      stages_.emplace_back(lane.depth);
      head_.push_back(0);
      sampled_.emplace_back();
      qNext_.emplace_back();
      pendingReset_.push_back(0);
      pending_.push_back(0);
    }
    dirty_.assign(loop_word_count(lanes_.size()), 0);
    advanced_.assign(loop_word_count(lanes_.size()), 0);
  }

  std::size_t size() const { return lanes_.size(); }

  // Caller guarantees a 0->1 edge just occurred on this loop's clock.
  void compute_rise() {
    clkPrev_ = true;
    const std::size_t n = lanes_.size();
    for (std::size_t i = 0; i < n; ++i) {
      const Lane &lane = lanes_[i];
      const bool r = lane.rst->toBool();
      const bool e = lane.en->toBool();
      pendingReset_[i] = r;
      pending_[i] = static_cast<char>(r | e);
      if (r)
        qNext_[i] = *lane.init;
      else if (e) {
        sampled_[i] = *lane.d;
        qNext_[i] = stages_[i][next_head(i, lane.depth)];
      }
    }
  }

  void compute_generic(bool clkNow) {
    const bool posedge = (!clkPrev_) & clkNow;
    clkPrev_ = clkNow;
    if (__builtin_expect(!posedge, 1)) {
      clear_pending();
      return;
    }
    compute_rise();
  }

  void negedge() { clkPrev_ = false; clear_pending(); }

  // Applies pending edges; returns {tail_dirty, advanced} bitmaps. `advanced`
  // has a bit set for every lane whose history advanced this commit (tap
  // consumers wake on it), matching has_pending() per lane.
  struct CommitResult {
    const std::vector<std::uint64_t> &tailDirty;
    const std::vector<std::uint64_t> &advanced;
  };

  CommitResult commit() {
    const std::size_t n = lanes_.size();
    dirty_.assign(loop_word_count(n), 0);
    advanced_.assign(loop_word_count(n), 0);
    for (std::size_t i = 0; i < n; ++i) {
      if (__builtin_expect(pending_[i] != 0, 0)) {
        const Lane &lane = lanes_[i];
        Wire<Width> &q = *lane.q;
        const bool changed = q != qNext_[i];
        if (pendingReset_[i]) {
          for (unsigned s = 0; s < lane.depth; ++s)
            stages_[i][s] = *lane.init;
          head_[i] = 0;
        } else {
          stages_[i][head_[i]] = sampled_[i];
          head_[i] = next_head(i, lane.depth);
        }
        q = qNext_[i];
        pending_[i] = 0;
        advanced_[i >> 6] |= std::uint64_t{1} << (i & 63);
        if (changed)
          dirty_[i >> 6] |= std::uint64_t{1} << (i & 63);
      }
    }
    return CommitResult{dirty_, advanced_};
  }

  // Probe write-intent bindings (mirror pyc_delay_line::pending/qNext).
  bool *lane_pending(std::size_t i) {
    return reinterpret_cast<bool *>(&pending_[i]);
  }
  Wire<Width> *lane_qnext(std::size_t i) { return &qNext_[i]; }

  // Lightweight per-lane tap view: satisfies the DelayInst interface used by
  // ProbeRegistry::addRegLazyTap* (tap(depth) / has_pending() / pending /
  // qNext members). Held by value in generated probe registration code.
  struct TapView {
    const DelayLoop *loop;
    std::size_t lane;
    Wire<Width> tap(unsigned depth) const { return loop->tap(lane, depth); }
    bool has_pending() const { return loop->pending_[lane] != 0; }
    bool *pending_ptr() const {
      return reinterpret_cast<bool *>(const_cast<char *>(&loop->pending_[lane]));
    }
    const Wire<Width> *qnext() const { return &loop->qNext_[lane]; }
  };

  // Committed tap read for lane i (mirror pyc_delay_line::tap).
  Wire<Width> lane_tap(std::size_t i, unsigned depth) const {
    return tap(i, depth);
  }

  // tap_next for per-tap _qNext wires: recompute exactly like
  // pyc_delay_line::tap_next for lane i.
  Wire<Width> lane_tap_next(std::size_t i, unsigned depth) const {
    const Lane &lane = lanes_[i];
    if (pendingReset_[i])
      return *lane.init;
    if (!pending_[i])
      return tap(i, depth);
    return depth == 1 ? sampled_[i] : tap(i, depth - 1);
  }

private:
  Wire<Width> tap(std::size_t i, unsigned depth) const {
    // Caller passes 1..Depth (verifier-guaranteed); ring layout matches
    // pyc_delay_line::stages/head.
    unsigned idx = head_[i] + lanes_[i].depth - depth;
    if (idx >= lanes_[i].depth)
      idx -= lanes_[i].depth;
    return stages_[i][idx];
  }
  unsigned next_head(std::size_t i, unsigned depth) const {
    unsigned h = head_[i] + 1u;
    return h == depth ? 0u : h;
  }
  void clear_pending() { pending_.assign(lanes_.size(), 0); }

  std::vector<Lane> lanes_;
  std::vector<std::vector<Wire<Width>>> stages_;
  std::vector<unsigned> head_;
  std::vector<Wire<Width>> sampled_;
  std::vector<Wire<Width>> qNext_;
  std::vector<char> pending_;       // 0/1, stable address via lane_pending
  std::vector<char> pendingReset_;  // 0/1
  std::vector<std::uint64_t> dirty_;
  std::vector<std::uint64_t> advanced_;
  bool clkPrev_ = false;
};

}  // namespace pyc::cpp
