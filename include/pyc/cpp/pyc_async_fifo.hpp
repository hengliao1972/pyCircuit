#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "pyc_bits.hpp"
#include "pyc_clock.hpp"

namespace pyc::cpp {

namespace detail {

constexpr bool isPowerOfTwo(std::size_t v) { return v && ((v & (v - 1)) == 0); }

constexpr unsigned clog2(std::size_t value) {
  unsigned r = 0;
  std::size_t x = value - 1;
  while (x > 0) {
    x >>= 1;
    r++;
  }
  return r;
}

} // namespace detail

// Async ready/valid FIFO with gray-code pointers (prototype).
//
// - Strict ready/valid handshake (no combinational cross-domain paths).
// - Depth must be a power of two and >= 2.
template <unsigned Width, std::size_t Depth>
class pyc_async_fifo {
public:
  static_assert(Width > 0 && Width <= 64, "pyc_async_fifo supports widths 1..64 in the prototype");
  static_assert(Depth >= 2, "pyc_async_fifo requires Depth >= 2");
  static_assert(detail::isPowerOfTwo(Depth), "pyc_async_fifo requires power-of-two Depth");

  static constexpr unsigned AW = detail::clog2(Depth);
  static_assert(AW >= 1, "internal: pyc_async_fifo requires AW >= 1");

  pyc_async_fifo(Wire<1> &in_clk,
                 Wire<1> &in_rst,
                 Wire<1> &in_valid,
                 Wire<1> &in_ready,
                 Wire<Width> &in_data,
                 Wire<1> &out_clk,
                 Wire<1> &out_rst,
                 Wire<1> &out_valid,
                 Wire<1> &out_ready,
                 Wire<Width> &out_data)
      : in_clk(in_clk), in_rst(in_rst), in_valid(in_valid), in_ready(in_ready), in_data(in_data), out_clk(out_clk),
        out_rst(out_rst), out_valid(out_valid), out_ready(out_ready), out_data(out_data) {
    resetState();
    eval();
  }

  void eval() {
    in_ready = Wire<1>(wfull_ ? 0u : 1u);
    out_valid = Wire<1>(out_valid_r_ ? 1u : 0u);
    out_data = out_data_r_;
  }

  void tick_compute() {
    pendingIn_ = false;
    pendingOut_ = false;

    bool inClkNow = in_clk.toBool();
    bool inPosedge = (!inClkPrev_) && inClkNow;
    inClkPrev_ = inClkNow;

    bool outClkNow = out_clk.toBool();
    bool outPosedge = (!outClkPrev_) && outClkNow;
    outClkPrev_ = outClkNow;

    if (inPosedge)
      tick_compute_in();
    if (outPosedge)
      tick_compute_out();
  }

  void tick_commit() {
    if (pendingIn_)
      tick_commit_in();
    if (pendingOut_)
      tick_commit_out();
    eval();
  }

private:
  using ptr_t = std::uint64_t;

  static constexpr ptr_t indexMask() { return static_cast<ptr_t>(Depth - 1); }
  static constexpr ptr_t ptrMask() {
    if constexpr (AW + 1 >= 64)
      return ~ptr_t{0};
    return (ptr_t{1} << (AW + 1)) - 1;
  }

  static constexpr ptr_t bin2gray(ptr_t b) { return (b >> 1) ^ b; }

  static constexpr ptr_t invert2msb(ptr_t g) {
    // Flip the top two bits of the (AW+1)-bit gray pointer.
    return (g ^ ((ptr_t{1} << AW) | (ptr_t{1} << (AW - 1)))) & ptrMask();
  }

  void resetState() {
    for (auto &e : mem_)
      e = Wire<Width>(0);

    wptr_bin_ = 0;
    wptr_gray_ = 0;
    wfull_ = false;
    rptr_gray_w1_ = 0;
    rptr_gray_w2_ = 0;

    rptr_bin_ = 0;
    rptr_gray_ = 0;
    wptr_gray_r1_ = 0;
    wptr_gray_r2_ = 0;
    out_valid_r_ = false;
    out_data_r_ = Wire<Width>(0);

    inClkPrev_ = false;
    outClkPrev_ = false;
  }

  void tick_compute_in() {
    pendingIn_ = true;
    // Defaults: hold state.
    wptr_bin_next_ = wptr_bin_;
    wptr_gray_next_ = wptr_gray_;
    wfull_next_ = wfull_;
    rptr_gray_w1_next_ = rptr_gray_w1_;
    rptr_gray_w2_next_ = rptr_gray_w2_;
    doWrite_ = false;
    writeAddr_ = 0;
    writeData_ = Wire<Width>(0);

    if (in_rst.toBool()) {
      wptr_bin_next_ = 0;
      wptr_gray_next_ = 0;
      wfull_next_ = false;
      rptr_gray_w1_next_ = 0;
      rptr_gray_w2_next_ = 0;
      return;
    }

    // Sync read pointer gray into the write domain.
    rptr_gray_w1_next_ = rptr_gray_;
    rptr_gray_w2_next_ = rptr_gray_w1_;

    bool canPush = !wfull_;
    bool do_push = in_valid.toBool() && canPush;

    if (do_push) {
      doWrite_ = true;
      writeAddr_ = wptr_bin_ & indexMask();
      writeData_ = in_data;
    }

    ptr_t wptr_bin_inc = (wptr_bin_ + (do_push ? 1u : 0u)) & ptrMask();
    ptr_t wptr_gray_inc = bin2gray(wptr_bin_inc) & ptrMask();

    wptr_bin_next_ = wptr_bin_inc;
    wptr_gray_next_ = wptr_gray_inc;
    wfull_next_ = (wptr_gray_next_ == invert2msb(rptr_gray_w2_));
  }

  void tick_commit_in() {
    if (doWrite_)
      mem_[static_cast<std::size_t>(writeAddr_)] = writeData_;

    wptr_bin_ = wptr_bin_next_;
    wptr_gray_ = wptr_gray_next_;
    wfull_ = wfull_next_;
    rptr_gray_w1_ = rptr_gray_w1_next_;
    rptr_gray_w2_ = rptr_gray_w2_next_;
    pendingIn_ = false;
  }

  void tick_compute_out() {
    pendingOut_ = true;
    // Defaults: hold state.
    rptr_bin_next_ = rptr_bin_;
    rptr_gray_next_ = rptr_gray_;
    wptr_gray_r1_next_ = wptr_gray_r1_;
    wptr_gray_r2_next_ = wptr_gray_r2_;
    out_valid_r_next_ = out_valid_r_;
    out_data_r_next_ = out_data_r_;

    if (out_rst.toBool()) {
      rptr_bin_next_ = 0;
      rptr_gray_next_ = 0;
      wptr_gray_r1_next_ = 0;
      wptr_gray_r2_next_ = 0;
      out_valid_r_next_ = false;
      out_data_r_next_ = Wire<Width>(0);
      return;
    }

    // Sync write pointer gray into the read domain.
    wptr_gray_r1_next_ = wptr_gray_;
    wptr_gray_r2_next_ = wptr_gray_r1_;

    bool empty_now = (rptr_gray_ == wptr_gray_r2_);

    if (!out_valid_r_) {
      if (!empty_now) {
        out_valid_r_next_ = true;
        out_data_r_next_ = mem_[static_cast<std::size_t>(rptr_bin_ & indexMask())];
      }
      return;
    }

    bool do_pop = out_valid_r_ && out_ready.toBool();
    if (!do_pop)
      return;

    ptr_t rptr_bin_inc = (rptr_bin_ + 1u) & ptrMask();
    ptr_t rptr_gray_inc = bin2gray(rptr_bin_inc) & ptrMask();
    bool empty_next = (rptr_gray_inc == wptr_gray_r2_);

    rptr_bin_next_ = rptr_bin_inc;
    rptr_gray_next_ = rptr_gray_inc;

    if (empty_next) {
      out_valid_r_next_ = false;
      out_data_r_next_ = Wire<Width>(0);
    } else {
      out_valid_r_next_ = true;
      out_data_r_next_ = mem_[static_cast<std::size_t>(rptr_bin_inc & indexMask())];
    }
  }

  void tick_commit_out() {
    rptr_bin_ = rptr_bin_next_;
    rptr_gray_ = rptr_gray_next_;
    wptr_gray_r1_ = wptr_gray_r1_next_;
    wptr_gray_r2_ = wptr_gray_r2_next_;
    out_valid_r_ = out_valid_r_next_;
    out_data_r_ = out_data_r_next_;
    pendingOut_ = false;
  }

public:
  Wire<1> &in_clk;
  Wire<1> &in_rst;
  Wire<1> &in_valid;
  Wire<1> &in_ready;
  Wire<Width> &in_data;

  Wire<1> &out_clk;
  Wire<1> &out_rst;
  Wire<1> &out_valid;
  Wire<1> &out_ready;
  Wire<Width> &out_data;

private:
  std::array<Wire<Width>, Depth> mem_{};

  // Write domain state.
  ptr_t wptr_bin_ = 0;
  ptr_t wptr_gray_ = 0;
  bool wfull_ = false;
  ptr_t rptr_gray_w1_ = 0;
  ptr_t rptr_gray_w2_ = 0;
  bool inClkPrev_ = false;

  // Read domain state.
  ptr_t rptr_bin_ = 0;
  ptr_t rptr_gray_ = 0;
  ptr_t wptr_gray_r1_ = 0;
  ptr_t wptr_gray_r2_ = 0;
  bool out_valid_r_ = false;
  Wire<Width> out_data_r_{};
  bool outClkPrev_ = false;

  // Next-state storage (computed in tick_compute).
  bool pendingIn_ = false;
  ptr_t wptr_bin_next_ = 0;
  ptr_t wptr_gray_next_ = 0;
  bool wfull_next_ = false;
  ptr_t rptr_gray_w1_next_ = 0;
  ptr_t rptr_gray_w2_next_ = 0;
  bool doWrite_ = false;
  ptr_t writeAddr_ = 0;
  Wire<Width> writeData_{};

  bool pendingOut_ = false;
  ptr_t rptr_bin_next_ = 0;
  ptr_t rptr_gray_next_ = 0;
  ptr_t wptr_gray_r1_next_ = 0;
  ptr_t wptr_gray_r2_next_ = 0;
  bool out_valid_r_next_ = false;
  Wire<Width> out_data_r_next_{};
};

} // namespace pyc::cpp

