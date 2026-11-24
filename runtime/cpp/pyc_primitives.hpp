#pragma once

#include <cstdint>

#include "pyc_bits.hpp"
#include "pyc_clock.hpp"
#include "pyc_component.hpp"

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

// Register semantics shared by pyc_reg (scalar) and pyc_vec_reg (vector): the
// two differ only in the value type, so staging/publish lives here once and the
// leaves are 0-line derivations that keep their generated constructor signature.
// The edge protocol lives in ClockedElement; derived classes supply sample().
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
class pyc_reg : public RegBase<Wire<Width>> {
  using Base = RegBase<Wire<Width>>;

public:
  using Base::Base;
};

template <typename T>
class pyc_vec_reg : public RegBase<T> {
  using Base = RegBase<T>;

public:
  using Base::Base;
};

// Simulator representation of DEPTH cascaded registers. A circular buffer
// makes the normal enabled edge O(1); reset still initializes every stage so
// reset/enable and compute-before-commit semantics match pyc_reg chains.
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
class pyc_delay_line : public DelayBase<Wire<Width>, Depth> {
  using Base = DelayBase<Wire<Width>, Depth>;

public:
  using Base::Base;
};

template <typename T, unsigned Depth>
class pyc_vec_delay_line : public DelayBase<T, Depth> {
  using Base = DelayBase<T, Depth>;

public:
  using Base::Base;
};

template <unsigned Width, unsigned Depth>
class pyc_fifo : public ClockedElement<pyc_fifo<Width, Depth>> {
public:
  using change_mask_t = BaseElement::change_mask_t;

  // Bit numbers of the three visible outputs (rule: i-th output -> bit i).
  static constexpr change_mask_t kInReadyChanged = change_bit(0);
  static constexpr change_mask_t kOutValidChanged = change_bit(1);
  static constexpr change_mask_t kOutDataChanged = change_bit(2);

  pyc_fifo(Wire<1> &clk,
           Wire<1> &rst,
           Wire<1> &in_valid,
           Wire<1> &in_ready,
           Wire<Width> &in_data,
           Wire<1> &out_valid,
           Wire<1> &out_ready,
           Wire<Width> &out_data)
      : ClockedElement<pyc_fifo<Width, Depth>>(clk), rst(rst), in_valid(in_valid), in_ready(in_ready), in_data(in_data), out_valid(out_valid),
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

  void tick_sample() {
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
  Wire<1> &rst;

  Wire<1> &in_valid;
  Wire<1> &in_ready;
  Wire<Width> &in_data;

  Wire<1> &out_valid;
  Wire<1> &out_ready;
  Wire<Width> &out_data;

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
