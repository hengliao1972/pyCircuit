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

// RegLoop: lanes of different widths share one loop, so the
// emitter groups by (loop id, fused) instead of (loop id, width, fused).
// Lane values are raw words (Wire<W>::data()); qNext lives in a packed
// arena whose per-lane segment is kWords long, so write-intent probes can
// still reinterpret the segment as Wire<W> via lane_qnext_raw(). Semantics
// mirror the per-object pyc_reg exactly; the per-lane compare/copy loops
// are bounded by the runtime word count.
class RegLoop {
public:
  struct Lane {
    std::uint64_t *q;
    std::uint64_t *d;
    Wire<1> *rst;
    Wire<1> *en;
    const std::uint64_t *init;
    unsigned words;  // ceil(width / 64), from the emitting reg
  };

  void add_lanes(std::initializer_list<Lane> lanes) {
    for (const Lane &lane : lanes) {
      lanes_.push_back(lane);
      laneOff_.push_back(qStore_.size());
      qStore_.resize(qStore_.size() + lane.words, 0);
      pending_.push_back(0);
    }
    dirty_.assign(loop_word_count(lanes_.size()), 0);
  }

  std::size_t size() const { return lanes_.size(); }

  void compute_rise() {
    clkPrev_ = true;
    const std::size_t n = lanes_.size();
    for (std::size_t i = 0; i < n; ++i) {
      const Lane &lane = lanes_[i];
      const bool r = lane.rst->toBool();
      const bool e = lane.en->toBool();
      pending_[i] = static_cast<char>(r | e);
      std::uint64_t *next = qStore_.data() + laneOff_[i];
      const std::uint64_t *src = r ? lane.init : lane.d;
      for (unsigned w = 0; w < lane.words; ++w)
        next[w] = src[w];
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

  const std::vector<std::uint64_t> &commit() {
    const std::size_t n = lanes_.size();
    dirty_.assign(loop_word_count(n), 0);
    for (std::size_t i = 0; i < n; ++i) {
      if (__builtin_expect(pending_[i] != 0, 0)) {
        const Lane &lane = lanes_[i];
        std::uint64_t *q = lane.q;
        const std::uint64_t *next = qStore_.data() + laneOff_[i];
        bool changed = false;
        for (unsigned w = 0; w < lane.words; ++w) {
          changed |= q[w] != next[w];
          q[w] = next[w];
        }
        pending_[i] = 0;
        if (changed)
          dirty_[i >> 6] |= std::uint64_t{1} << (i & 63);
      }
    }
    return dirty_;
  }

  const std::vector<std::uint64_t> &direct_commit() {
    const std::size_t n = lanes_.size();
    clkPrev_ = true;
    dirty_.assign(loop_word_count(n), 0);
    for (std::size_t i = 0; i < n; ++i) {
      const Lane &lane = lanes_[i];
      const bool r = lane.rst->toBool();
      const bool go = r | lane.en->toBool();
      pending_[i] = static_cast<char>(go);
      if (!go)
        continue;
      const std::uint64_t *src = r ? lane.init : lane.d;
      std::uint64_t *next = qStore_.data() + laneOff_[i];
      std::uint64_t *q = lane.q;
      bool changed = false;
      for (unsigned w = 0; w < lane.words; ++w) {
        changed |= q[w] != src[w];
        next[w] = src[w];
        q[w] = src[w];
      }
      if (changed)
        dirty_[i >> 6] |= std::uint64_t{1} << (i & 63);
    }
    return dirty_;
  }

  void retire() { clear_pending(); }

  bool *lane_pending(std::size_t i) {
    return reinterpret_cast<bool *>(&pending_[i]);
  }
  // Segment is kWords long; callers reinterpret it as Wire<W> (Bits is
  // standard-layout with words_ as its only member).
  void *lane_qnext_raw(std::size_t i) {
    return qStore_.data() + laneOff_[i];
  }

private:
  void clear_pending() { pending_.assign(lanes_.size(), 0); }

  std::vector<Lane> lanes_;
  std::vector<std::uint64_t> qStore_;
  std::vector<std::size_t> laneOff_;
  std::vector<char> pending_;
  std::vector<std::uint64_t> dirty_;
  bool clkPrev_ = false;
};

// Width-erased DelayLoop: lanes of different widths share one loop (the
// emitter groups by clock and depth only). Per-lane depth and width vary,
// so stage rings, sampled and qNext are per-lane word vectors of
// lane.words entries; tap reads build a Wire<W> on the fly via the
// explicit template argument the emitter knows.
class DelayLoop {
public:
  struct Lane {
    std::uint64_t *q;
    std::uint64_t *d;
    Wire<1> *rst;
    Wire<1> *en;
    const std::uint64_t *init;
    unsigned depth;  // verifier-guaranteed > 1
    unsigned words;  // ceil(width / 64)
  };

  void add_lanes(std::initializer_list<Lane> lanes) {
    for (const Lane &lane : lanes) {
      lanes_.push_back(lane);
      stages_.emplace_back(std::size_t(lane.depth) * lane.words, 0);
      head_.push_back(0);
      sampled_.emplace_back(lane.words, 0);
      qNext_.emplace_back(lane.words, 0);
      pendingReset_.push_back(0);
      pending_.push_back(0);
    }
    dirty_.assign(loop_word_count(lanes_.size()), 0);
    advanced_.assign(loop_word_count(lanes_.size()), 0);
  }

  std::size_t size() const { return lanes_.size(); }

  void compute_rise() {
    clkPrev_ = true;
    const std::size_t n = lanes_.size();
    for (std::size_t i = 0; i < n; ++i) {
      const Lane &lane = lanes_[i];
      const bool r = lane.rst->toBool();
      const bool e = lane.en->toBool();
      pendingReset_[i] = r;
      pending_[i] = static_cast<char>(r | e);
      if (r) {
        for (unsigned w = 0; w < lane.words; ++w)
          qNext_[i][w] = lane.init[w];
      } else if (e) {
        for (unsigned w = 0; w < lane.words; ++w)
          sampled_[i][w] = lane.d[w];
        const std::uint64_t *stage =
            stages_[i].data() + std::size_t(next_head(i, lane.depth)) * lane.words;
        for (unsigned w = 0; w < lane.words; ++w)
          qNext_[i][w] = stage[w];
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
        std::uint64_t *q = lane.q;
        const std::uint64_t *next = qNext_[i].data();
        bool changed = false;
        for (unsigned w = 0; w < lane.words; ++w) {
          changed |= q[w] != next[w];
          q[w] = next[w];
        }
        if (pendingReset_[i]) {
          for (unsigned s = 0; s < lane.depth; ++s)
            for (unsigned w = 0; w < lane.words; ++w)
              stages_[i][std::size_t(s) * lane.words + w] = lane.init[w];
          head_[i] = 0;
        } else {
          std::uint64_t *stage =
              stages_[i].data() + std::size_t(head_[i]) * lane.words;
          for (unsigned w = 0; w < lane.words; ++w)
            stage[w] = sampled_[i][w];
          head_[i] = next_head(i, lane.depth);
        }
        pending_[i] = 0;
        advanced_[i >> 6] |= std::uint64_t{1} << (i & 63);
        if (changed)
          dirty_[i >> 6] |= std::uint64_t{1} << (i & 63);
      }
    }
    return CommitResult{dirty_, advanced_};
  }

  bool *lane_pending(std::size_t i) {
    return reinterpret_cast<bool *>(&pending_[i]);
  }
  // Per-lane segment of lane.words entries; callers reinterpret it as
  // Wire<W> (Bits is standard-layout with words_ as its only member).
  void *lane_qnext_raw(std::size_t i) { return qNext_[i].data(); }

  // Committed tap read for lane i (mirror pyc_delay_line::tap); the
  // emitter passes the width it knows as an explicit template argument.
  template <unsigned Width>
  Wire<Width> lane_tap(std::size_t i, unsigned depth) const {
    Wire<Width> out;
    tapWords(i, depth, out.data());
    return out;
  }

  // tap_next for per-tap _qNext wires: recompute exactly like
  // pyc_delay_line::tap_next for lane i.
  template <unsigned Width>
  Wire<Width> lane_tap_next(std::size_t i, unsigned depth) const {
    const Lane &lane = lanes_[i];
    if (pendingReset_[i]) {
      Wire<Width> out;
      for (unsigned w = 0; w < lane.words; ++w)
        out.data()[w] = lane.init[w];
      return out;
    }
    if (!pending_[i])
      return lane_tap<Width>(i, depth);
    if (depth == 1) {
      Wire<Width> out;
      for (unsigned w = 0; w < lane.words; ++w)
        out.data()[w] = sampled_[i][w];
      return out;
    }
    return lane_tap<Width>(i, depth - 1);
  }

  // Lightweight per-lane tap view: satisfies the DelayInst interface used by
  // ProbeRegistry::addRegLazyTap* (tap(depth) / has_pending() / pending /
  // qNext members). Held by value in generated probe registration code.
  template <unsigned Width>
  struct TapView {
    const DelayLoop *loop;
    std::size_t lane;
    Wire<Width> tap(unsigned depth) const { return loop->lane_tap<Width>(lane, depth); }
    bool has_pending() const { return loop->pending_[lane] != 0; }
    bool *pending_ptr() const {
      return reinterpret_cast<bool *>(const_cast<char *>(&loop->pending_[lane]));
    }
    const Wire<Width> *qnext() const {
      return reinterpret_cast<const Wire<Width> *>(loop->qNext_[lane].data());
    }
  };

private:
  void tapWords(std::size_t i, unsigned depth, std::uint64_t *out) const {
    const Lane &lane = lanes_[i];
    // Caller passes 1..Depth (verifier-guaranteed); ring layout matches
    // pyc_delay_line::stages/head.
    unsigned idx = head_[i] + lane.depth - depth;
    if (idx >= lane.depth)
      idx -= lane.depth;
    const std::uint64_t *stage =
        stages_[i].data() + std::size_t(idx) * lane.words;
    for (unsigned w = 0; w < lane.words; ++w)
      out[w] = stage[w];
  }
  unsigned next_head(std::size_t i, unsigned depth) const {
    unsigned h = head_[i] + 1u;
    return h == depth ? 0u : h;
  }
  void clear_pending() { pending_.assign(lanes_.size(), 0); }

  std::vector<Lane> lanes_;
  std::vector<std::vector<std::uint64_t>> stages_;
  std::vector<unsigned> head_;
  std::vector<std::vector<std::uint64_t>> sampled_;
  std::vector<std::vector<std::uint64_t>> qNext_;
  std::vector<char> pending_;       // 0/1, stable address via lane_pending
  std::vector<char> pendingReset_;  // 0/1
  std::vector<std::uint64_t> dirty_;
  std::vector<std::uint64_t> advanced_;
  bool clkPrev_ = false;
};

}  // namespace pyc::cpp

