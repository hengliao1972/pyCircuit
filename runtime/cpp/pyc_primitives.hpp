#pragma once

#include <cstdint>

#include "pyc_bits.hpp"
#include "pyc_clock.hpp"

namespace pyc::cpp {

// "Module-like" primitives that mirror the Verilog templates in
// `runtime/verilog/` (same names, same port names).
//
// These are intended as stable building blocks for generated C++ cycle-accurate
// models.

template <unsigned Width>
struct pyc_add {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a + b; }
};

template <unsigned Width>
struct pyc_mux {
  Wire<1> &sel;
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = sel.toBool() ? a : b; }
};

template <unsigned Width>
struct pyc_and {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a & b; }
};

template <unsigned Width>
struct pyc_or {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a | b; }
};

template <unsigned Width>
struct pyc_xor {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a ^ b; }
};

template <unsigned Width>
struct pyc_not {
  Wire<Width> &a;
  Wire<Width> &y;

  void eval() { y = ~a; }
};

template <unsigned Width>
class pyc_reg {
public:
  pyc_reg(Wire<1> &clk, Wire<1> &rst, Wire<1> &en, Wire<Width> &d, Wire<Width> &init, Wire<Width> &q)
      : clk(clk), rst(rst), en(en), d(d), init(init), q(q) {}

  // Branch-optimized two-phase update.
  // tick_compute: sample inputs; tick_commit: apply.
  inline void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) & clkNow;
    clkPrev = clkNow;
    if (__builtin_expect(!posedge, 1)) {
      pending = false;
      return;
    }
    posedge_compute_inner();
  }

  // Direct posedge path — caller guarantees a 0→1 edge just occurred.
  // Saves the clkPrev read + posedge check (~2 branches per register).
  inline void posedge_tick_compute() {
    clkPrev = true;
    posedge_compute_inner();
  }

  // Negedge bookkeeping — just reset clkPrev so next posedge is detected.
  // Avoids running the full tick_compute logic on the falling edge.
  inline void negedge_update() {
    clkPrev = false;
    pending = false;
  }

  // Applies a pending edge and reports whether the visible register output changed.
  inline bool tick_commit() {
    if (__builtin_expect(pending, 0)) {
      const bool changed = q != qNext;
      q = qNext;
      pending = false;
      return changed;
    }
    return false;
  }

private:
  inline void posedge_compute_inner() {
    bool r = rst.toBool();
    bool e = en.toBool();
    pending = r | e;
    if (r)
      qNext = init;
    else
      qNext = d;
  }

public:

  Wire<1> &clk;
  Wire<1> &rst;
  Wire<1> &en;
  Wire<Width> &d;
  Wire<Width> &init;
  Wire<Width> &q;

  bool clkPrev = false;
  bool pending = false;
  Wire<Width> qNext{};
};

// Simulator representation of DEPTH cascaded registers. A circular buffer
// makes the normal enabled edge O(1); reset still initializes every stage so
// reset/enable and compute-before-commit semantics match pyc_reg chains.
template <unsigned Width, unsigned Depth>
class pyc_delay_line {
  static_assert(Depth > 1, "pyc_delay_line requires Depth > 1");

public:
  pyc_delay_line(Wire<1> &clk, Wire<1> &rst, Wire<1> &en, Wire<Width> &d,
                 Wire<Width> &init, Wire<Width> &q)
      : clk(clk), rst(rst), en(en), d(d), init(init), q(q) {}

  inline void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) & clkNow;
    clkPrev = clkNow;
    if (__builtin_expect(!posedge, 1)) {
      pending = false;
      return;
    }
    posedge_compute_inner();
  }

  inline void posedge_tick_compute() {
    clkPrev = true;
    posedge_compute_inner();
  }

  inline void negedge_update() {
    clkPrev = false;
    pending = false;
  }

  // Return the value at a fixed enabled-edge distance from the input.  The
  // caller must pass 1..Depth; the verifier guarantees this for generated IR.
  inline Wire<Width> tap(unsigned depth) const {
    return stages[(head + Depth - depth) % Depth];
  }

  inline Wire<Width> tap_next(unsigned depth) const {
    if (pendingReset)
      return init;
    if (!pending)
      return tap(depth);
    return depth == 1 ? sampledInput : tap(depth - 1);
  }

  // True while an enabled edge is waiting for tick_commit. Tap consumers
  // must wake on this (every enabled edge shifts every tap), while tail-`q`
  // consumers can use tick_commit's tail-change result directly.
  inline bool has_pending() const { return pending; }

  // Applies a pending edge and reports whether the tail `q` changed. Tap
  // consumers wake via has_pending(); a tail-only result would leave them
  // stale on cycles where the tail happens to repeat.
  inline bool tick_commit() {
    if (__builtin_expect(!pending, 1))
      return false;
    const bool changed = q != qNext;
    if (pendingReset) {
      stages.fill(init);
      head = 0;
    } else {
      stages[head] = sampledInput;
      head = nextIndex(head);
    }
    q = qNext;
    pending = false;
    return changed;
  }

private:
  static constexpr unsigned nextIndex(unsigned index) {
    return index + 1u == Depth ? 0u : index + 1u;
  }

  inline void posedge_compute_inner() {
    pendingReset = rst.toBool();
    bool enabled = en.toBool();
    pending = pendingReset | enabled;
    if (pendingReset) {
      qNext = init;
      return;
    }
    if (enabled) {
      sampledInput = d;
      qNext = stages[nextIndex(head)];
    }
  }

public:
  Wire<1> &clk;
  Wire<1> &rst;
  Wire<1> &en;
  Wire<Width> &d;
  Wire<Width> &init;
  Wire<Width> &q;

  bool clkPrev = false;
  bool pending = false;
  bool pendingReset = false;
  Wire<Width> qNext{};
  Wire<Width> sampledInput{};
  std::array<Wire<Width>, Depth> stages{};
  unsigned head = 0;
};

template <typename T>
class pyc_vec_reg {
public:
  pyc_vec_reg(Wire<1> &clk, Wire<1> &rst, Wire<1> &en, T &d, T &init, T &q)
      : clk(clk), rst(rst), en(en), d(d), init(init), q(q) {}

  inline void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) & clkNow;
    clkPrev = clkNow;
    if (__builtin_expect(!posedge, 1)) {
      pending = false;
      return;
    }
    posedge_compute_inner();
  }

  inline void posedge_tick_compute() {
    clkPrev = true;
    posedge_compute_inner();
  }

  inline void negedge_update() {
    clkPrev = false;
    pending = false;
  }

  // Applies a pending edge and reports whether the visible vector output changed.
  inline bool tick_commit() {
    if (__builtin_expect(pending, 0)) {
      const bool changed = q != qNext;
      q = qNext;
      pending = false;
      return changed;
    }
    return false;
  }

private:
  inline void posedge_compute_inner() {
    bool r = rst.toBool();
    bool e = en.toBool();
    pending = r | e;
    if (r)
      qNext = init;
    else
      qNext = d;
  }

public:
  Wire<1> &clk;
  Wire<1> &rst;
  Wire<1> &en;
  T &d;
  T &init;
  T &q;
  bool clkPrev = false;
  bool pending = false;
  T qNext{};
};

// Vector counterpart of pyc_delay_line; T is one complete vector value, so a
// single circular-buffer slot advances every lane atomically.
template <typename T, unsigned Depth>
class pyc_vec_delay_line {
  static_assert(Depth > 1, "pyc_vec_delay_line requires Depth > 1");

public:
  pyc_vec_delay_line(Wire<1> &clk, Wire<1> &rst, Wire<1> &en, T &d, T &init,
                     T &q)
      : clk(clk), rst(rst), en(en), d(d), init(init), q(q) {}

  inline void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) & clkNow;
    clkPrev = clkNow;
    if (__builtin_expect(!posedge, 1)) {
      pending = false;
      return;
    }
    posedge_compute_inner();
  }

  inline void posedge_tick_compute() {
    clkPrev = true;
    posedge_compute_inner();
  }

  inline void negedge_update() {
    clkPrev = false;
    pending = false;
  }

  // True while an enabled edge is waiting for tick_commit. Tap consumers
  // must wake on this (every enabled edge shifts every tap).
  inline bool has_pending() const { return pending; }

  // Applies a pending edge and reports whether the tail `q` changed; tap
  // consumers wake via has_pending() (see the scalar note above).
  inline bool tick_commit() {
    if (__builtin_expect(!pending, 1))
      return false;
    const bool changed = q != qNext;
    if (pendingReset) {
      stages.fill(init);
      head = 0;
    } else {
      stages[head] = sampledInput;
      head = nextIndex(head);
    }
    q = qNext;
    pending = false;
    return changed;
  }

  inline T tap(unsigned depth) const {
    return stages[(head + Depth - depth) % Depth];
  }

  inline T tap_next(unsigned depth) const {
    if (pendingReset)
      return init;
    if (!pending)
      return tap(depth);
    return depth == 1 ? sampledInput : tap(depth - 1);
  }

private:
  static constexpr unsigned nextIndex(unsigned index) {
    return index + 1u == Depth ? 0u : index + 1u;
  }

  inline void posedge_compute_inner() {
    pendingReset = rst.toBool();
    bool enabled = en.toBool();
    pending = pendingReset | enabled;
    if (pendingReset) {
      qNext = init;
      return;
    }
    if (enabled) {
      sampledInput = d;
      qNext = stages[nextIndex(head)];
    }
  }

public:
  Wire<1> &clk;
  Wire<1> &rst;
  Wire<1> &en;
  T &d;
  T &init;
  T &q;
  bool clkPrev = false;
  bool pending = false;
  bool pendingReset = false;
  T qNext{};
  T sampledInput{};
  std::array<T, Depth> stages{};
  unsigned head = 0;
};

template <unsigned Width, unsigned Depth>
class pyc_fifo {
public:
  using change_mask_t = std::uint8_t;
  static constexpr change_mask_t kInReadyChanged = change_mask_t{1} << 0;
  static constexpr change_mask_t kOutValidChanged = change_mask_t{1} << 1;
  static constexpr change_mask_t kOutDataChanged = change_mask_t{1} << 2;

  pyc_fifo(Wire<1> &clk,
           Wire<1> &rst,
           Wire<1> &in_valid,
           Wire<1> &in_ready,
           Wire<Width> &in_data,
           Wire<1> &out_valid,
           Wire<1> &out_ready,
           Wire<Width> &out_data)
      : clk(clk), rst(rst), in_valid(in_valid), in_ready(in_ready), in_data(in_data), out_valid(out_valid),
        out_ready(out_ready), out_data(out_data) {
    static_assert(Depth > 0, "pyc_fifo Depth must be > 0");
    resetState();
    eval();
  }

  static constexpr unsigned depth() { return Depth; }
  unsigned debug_count() const { return count_; }
  unsigned debug_rd_ptr() const { return rd_; }
  unsigned debug_wr_ptr() const { return wr_; }

  // Combinational ready/valid generation.
  void eval() {
    const bool outReadyNow = out_ready.toBool();
    Wire<Width> out_data_int = (count_ != 0) ? storage_[rd_] : Wire<Width>(0);
    if (evalValid_ && lastEvalCount_ == count_ && lastEvalRd_ == rd_ && lastEvalOutReady_ == outReadyNow &&
        lastEvalOutData_ == out_data_int)
      return;

    bool out_valid_int = (count_ != 0);
    bool in_ready_int = (count_ < Depth) || (out_valid_int && outReadyNow);

    in_ready = Wire<1>(in_ready_int ? 1u : 0u);
    out_valid = Wire<1>(out_valid_int ? 1u : 0u);
    out_data = out_data_int;

    evalValid_ = true;
    lastEvalCount_ = count_;
    lastEvalRd_ = rd_;
    lastEvalOutReady_ = outReadyNow;
    lastEvalOutData_ = out_data_int;
  }

  void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) && clkNow;
    clkPrev = clkNow;
    pending = false;
    if (!posedge)
      return;

    pending = true;
    if (rst.toBool()) {
      rdNext_ = 0;
      wrNext_ = 0;
      countNext_ = 0;
      for (unsigned i = 0; i < Depth; ++i)
        storageNext_[i] = Wire<Width>(0);
      return;
    }

    // Start from the current state.
    rdNext_ = rd_;
    wrNext_ = wr_;
    countNext_ = count_;
    for (unsigned i = 0; i < Depth; ++i)
      storageNext_[i] = storage_[i];

    bool out_valid_int = (count_ != 0);
    bool in_ready_int = (count_ < Depth) || (out_valid_int && out_ready.toBool());
    bool do_pop = out_valid_int && out_ready.toBool();
    bool do_push = in_valid.toBool() && in_ready_int;

    if (do_pop) {
      rdNext_ = bump(rdNext_);
      countNext_--;
    }
    if (do_push) {
      storageNext_[wrNext_] = in_data;
      wrNext_ = bump(wrNext_);
      countNext_++;
    }
  }

  // The mask describes state-driven changes to the three visible FIFO outputs.
  // Output wires remain updated by eval(), preserving the existing phase split.
  change_mask_t tick_commit() {
    if (!pending)
      return 0;

    const bool oldOutValid = count_ != 0;
    const bool oldInReady = (count_ < Depth) || (oldOutValid && out_ready.toBool());
    const Wire<Width> oldOutData = oldOutValid ? storage_[rd_] : Wire<Width>(0);

    rd_ = rdNext_;
    wr_ = wrNext_;
    count_ = countNext_;
    for (unsigned i = 0; i < Depth; ++i)
      storage_[i] = storageNext_[i];
    pending = false;

    const bool newOutValid = count_ != 0;
    const bool newInReady = (count_ < Depth) || (newOutValid && out_ready.toBool());
    const Wire<Width> newOutData = newOutValid ? storage_[rd_] : Wire<Width>(0);
    change_mask_t changed = 0;
    if (oldInReady != newInReady)
      changed |= kInReadyChanged;
    if (oldOutValid != newOutValid)
      changed |= kOutValidChanged;
    if (oldOutData != newOutData)
      changed |= kOutDataChanged;
    return changed;
  }

private:
  static constexpr unsigned bump(unsigned p) { return (p + 1 >= Depth) ? 0 : (p + 1); }

  void resetState() {
    rd_ = 0;
    wr_ = 0;
    count_ = 0;
    for (auto &e : storage_)
      e = Wire<Width>(0);
  }

public:
  Wire<1> &clk;
  Wire<1> &rst;

  Wire<1> &in_valid;
  Wire<1> &in_ready;
  Wire<Width> &in_data;

  Wire<1> &out_valid;
  Wire<1> &out_ready;
  Wire<Width> &out_data;

  bool clkPrev = false;
  bool pending = false;
  bool evalValid_ = false;
  bool lastEvalOutReady_ = false;
  unsigned lastEvalCount_ = 0;
  unsigned lastEvalRd_ = 0;
  Wire<Width> lastEvalOutData_{};

private:
  Wire<Width> storage_[Depth]{};
  unsigned rd_ = 0;
  unsigned wr_ = 0;
  unsigned count_ = 0;

  Wire<Width> storageNext_[Depth]{};
  unsigned rdNext_ = 0;
  unsigned wrNext_ = 0;
  unsigned countNext_ = 0;
};

} // namespace pyc::cpp
