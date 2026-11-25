#pragma once

#include <cassert>
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
// Semantics contract (matches the per-object pycReg / pycDelayLine):
//   compute_rise()    == tick_compute(true)  for every lane (known rising edge)
//   compute_generic() == tick_compute(clkNow) for every lane
//   negedge()         == tick_compute(false) for every lane
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
// mirror the per-object pycReg exactly; the per-lane compare/copy loops
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

  // Lane data pointers are stored structure-of-arrays: the hot sampling
  // loops stream q/d/init/words as contiguous arrays instead of chasing the
  // interleaved lane struct. rst/en are a single value per loop: the emitter
  // groups lanes by (clk, rst, en), so every lane of one loop shares the
  // same control wires by construction.
  void add_lanes(std::initializer_list<Lane> lanes) {
    for (const Lane &lane : lanes) {
      if (q_.empty()) {
        rst_ = lane.rst;
        en_ = lane.en;
      } else {
        assert(rst_ == lane.rst && en_ == lane.en &&
               "RegLoop lanes share one rst/en (emitter grouping invariant)");
      }
      q_.push_back(lane.q);
      d_.push_back(lane.d);
      init_.push_back(lane.init);
      words_.push_back(lane.words);
      laneOff_.push_back(qStore_.size());
      qStore_.resize(qStore_.size() + lane.words, 0);
      pending_.push_back(0);
    }
    dirty_.assign(loop_word_count(q_.size()), 0);
    pendingSeen_.assign(q_.size(), 0);
    shadowSeen_.assign(q_.size(), 0);
  }

  std::size_t size() const { return q_.size(); }

  void compute_rise() {
    clkPrev_ = true;
    const std::size_t n = q_.size();
    // One control read per loop (not per lane): qNext stays written for
    // every lane, matching the per-object pycReg sampling order.
    const bool r = rst_->toBool();
    const bool e = en_->toBool();
    const bool go = r | e;
    for (std::size_t i = 0; i < n; ++i) {
      pending_[i] = static_cast<char>(go);
      const std::uint64_t *src = r ? init_[i] : d_[i];
      std::uint64_t *next = qStore_.data() + laneOff_[i];
      for (unsigned w = 0; w < words_[i]; ++w)
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

  // Applies pending updates, writes back through the lane's q pointer, and
  // returns the per-lane dirty bitmap (bit i set == lane i's q changed).
  // The returned bitmap is consumed (and cleared) by the wake walk
  // (FusedLaneWake/FlatLaneWake::apply), so no upfront memset: the only
  // reader in generated code clears as it walks, and add_lanes
  // zero-initializes once.
  std::vector<std::uint64_t> &commit() {
    const std::size_t n = q_.size();
    for (std::size_t i = 0; i < n; ++i) {
      if (__builtin_expect(pending_[i] != 0, 0)) {
        std::uint64_t *q = q_[i];
        const std::uint64_t *next = qStore_.data() + laneOff_[i];
        bool changed = false;
        for (unsigned w = 0; w < words_[i]; ++w) {
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

  // Tick-fuse single-phase update: consumed (and cleared) by the wake walk
  // (FusedLaneWake/FlatLaneWake::apply) — see commit().
  std::vector<std::uint64_t> &direct_commit() {
    const std::size_t n = q_.size();
    clkPrev_ = true;
    // One control read per loop; a disabled edge skips the whole lane walk
    // (qNext keeps its last value, exactly as a per-lane `continue` would).
    const bool r = rst_->toBool();
    const bool go = r | en_->toBool();
    if (go) {
      std::uint64_t *const *const d = d_.data();
      const std::uint64_t *const *const init = init_.data();
      std::uint64_t *const *const qp = q_.data();
      const unsigned *const words = words_.data();
      for (std::size_t base = 0; base < n; base += 64) {
        const std::size_t end = (base + 64 < n) ? base + 64 : n;
        std::uint64_t mask = 0;
        for (std::size_t i = base; i < end; ++i) {
          const std::uint64_t *src = r ? init[i] : d[i];
          std::uint64_t *q = qp[i];
          if (words[i] == 1) {
            const std::uint64_t v = src[0];
            mask |= static_cast<std::uint64_t>(q[0] != v) << (i - base);
            q[0] = v;
          } else {
            std::uint64_t changed = 0;
            for (unsigned w = 0; w < words[i]; ++w) {
              const std::uint64_t v = src[w];
              changed |= q[w] != v;
              q[w] = v;
            }
            mask |= changed << (i - base);
          }
        }
        dirty_[base >> 6] |= mask;
      }
      // Only lanes a consumer bound (lane_pending/lane_qnext_raw) are mirrored:
      // an untouched probe costs nothing, no retroactive scan is needed.
      for (std::size_t k = 0; k < shadowLanes_.size(); ++k) {
        const std::size_t i = shadowLanes_[k];
        const std::uint64_t *src = r ? init_[i] : d_[i];
        std::uint64_t *next = qStore_.data() + laneOff_[i];
        for (unsigned w = 0; w < words_[i]; ++w)
          next[w] = src[w];
      }
      for (std::size_t k = 0; k < pendingLanes_.size(); ++k)
        pending_[pendingLanes_[k]] = 1;
    } else {
      for (std::size_t k = 0; k < pendingLanes_.size(); ++k)
        pending_[pendingLanes_[k]] = 0;
    }
    return dirty_;
  }

  // q is published in tick_compute; retire() only clears probe pending flags.
  void retire() {
    for (std::size_t k = 0; k < pendingLanes_.size(); ++k)
      pending_[pendingLanes_[k]] = 0;
  }

  // Diagnostic/helper reset of the consumed-in-place dirty bitmap; generated
  // code never calls this (the wake walks clear as they consume).
  void clearDirty() { dirty_.assign(loop_word_count(q_.size()), 0); }

  // Probe/trace write-intent bindings: stable addresses for lane i's pending
  // flag and sampled next value (mirror pycReg::pending/qNext). pending_
  // stores char with values exactly 0/1; bool has the same object
  // representation on the Itanium ABI and consumers only test truthiness.
  bool *lane_pending(std::size_t i) {
    if (!pendingSeen_[i]) {
      pendingSeen_[i] = 1;
      pendingLanes_.push_back(i);
    }
    return reinterpret_cast<bool *>(&pending_[i]);
  }
  // Segment is kWords long; callers reinterpret it as Wire<W> (Bits is
  // standard-layout with words_ as its only member).
  void *lane_qnext_raw(std::size_t i) {
    if (!shadowSeen_[i]) {
      shadowSeen_[i] = 1;
      shadowLanes_.push_back(i);
    }
    return qStore_.data() + laneOff_[i];
  }

private:
  void clear_pending() { pending_.assign(q_.size(), 0); }

  std::vector<std::uint64_t *> q_;
  std::vector<std::uint64_t *> d_;
  Wire<1> *rst_ = nullptr;  // one per loop: lanes are grouped by (clk, rst, en)
  Wire<1> *en_ = nullptr;
  std::vector<const std::uint64_t *> init_;
  std::vector<unsigned> words_;
  std::vector<std::uint64_t> qStore_;
  std::vector<std::size_t> laneOff_;
  std::vector<char> pending_;
  std::vector<std::size_t> pendingLanes_;  // lanes bound via lane_pending()
  std::vector<std::size_t> shadowLanes_;   // lanes bound via lane_qnext_raw()
  std::vector<char> pendingSeen_;
  std::vector<char> shadowSeen_;
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
      if (!lanes_.empty() && (lane.rst != lanes_.front().rst ||
                              lane.en != lanes_.front().en))
        sharedCtrl_ = false;
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
    if (sharedCtrl_) {
      // Uniform rst/en: one read replaces two dependent loads per lane.
      const bool r = lanes_[0].rst->toBool();
      const bool e = lanes_[0].en->toBool();
      const bool go = r | e;
      for (std::size_t i = 0; i < n; ++i) {
        const Lane &lane = lanes_[i];
        pendingReset_[i] = r;
        pending_[i] = static_cast<char>(go);
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
      return;
    }
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

  // Committed tap read for lane i (mirror pycDelayLine::tap); the
  // emitter passes the width it knows as an explicit template argument.
  template <unsigned Width>
  Wire<Width> lane_tap(std::size_t i, unsigned depth) const {
    Wire<Width> out;
    tapWords(i, depth, out.data());
    return out;
  }

  // tap_next for per-tap _qNext wires: recompute exactly like
  // pycDelayLine::tap_next for lane i.
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

private:
  void tapWords(std::size_t i, unsigned depth, std::uint64_t *out) const {
    const Lane &lane = lanes_[i];
    // Caller passes 1..Depth (verifier-guaranteed); ring layout matches
    // pycDelayLine::stages/head.
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
  bool sharedCtrl_ = true;  // all lanes share one rst and one en pointer
};

}  // namespace pyc::cpp

