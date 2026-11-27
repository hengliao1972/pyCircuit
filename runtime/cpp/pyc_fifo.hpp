#pragma once

#include "pyc_bits.hpp"
#include "pyc_component.hpp"

namespace pyc::cpp {

template <unsigned Width, unsigned Depth>
class pycFifo : public ClockedElement<pycFifo<Width, Depth>> {
public:
  using change_mask_t = BaseElement::change_mask_t;

  // Bit numbers of the three visible outputs (rule: i-th output -> bit i).
  static constexpr change_mask_t kInReadyChanged = BaseElement::change_bit(0);
  static constexpr change_mask_t kOutValidChanged = BaseElement::change_bit(1);
  static constexpr change_mask_t kOutDataChanged = BaseElement::change_bit(2);

  pycFifo(Wire<1> &clk,
           Wire<1> &rst,
           Wire<1> &in_valid,
           Wire<1> &in_ready,
           Wire<Width> &in_data,
           Wire<1> &out_valid,
           Wire<1> &out_ready,
           Wire<Width> &out_data)
      : ClockedElement<pycFifo<Width, Depth>>(clk), rst(rst), in_valid(in_valid), in_ready(in_ready), in_data(in_data), out_valid(out_valid),
        out_ready(out_ready), out_data(out_data) {
    static_assert(Depth > 0, "pycFifo Depth must be > 0");
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
