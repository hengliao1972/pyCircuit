#pragma once

#include <cstddef>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "pyc_bits.hpp"
#include "pyc_trace_bin.hpp"
#include "pyc_vcd.hpp"

namespace pyc::cpp {

struct TbClock {
  Wire<1> *clk = nullptr;
  std::uint64_t half_period_steps = 1;
  std::uint64_t phase_steps = 0;

  void set(bool high) const {
    if (clk)
      *clk = Wire<1>(high ? 1u : 0u);
  }

  void toggle() const {
    if (!clk)
      return;
    bool high = clk->toBool();
    *clk = Wire<1>(high ? 0u : 1u);
  }

  bool shouldToggle(std::uint64_t step) const {
    if (!clk || half_period_steps == 0)
      return false;
    return ((step + phase_steps) % half_period_steps) == 0;
  }
};

namespace detail {

template <typename T, typename = void>
struct has_comb : std::false_type {};

template <typename T>
struct has_comb<T, std::void_t<decltype(std::declval<T &>().comb())>> : std::true_type {};

template <typename T, typename = void>
struct has_eval : std::false_type {};

template <typename T>
struct has_eval<T, std::void_t<decltype(std::declval<T &>().eval())>> : std::true_type {};

template <typename T, typename = void>
struct has_transfer : std::false_type {};

template <typename T>
struct has_transfer<T, std::void_t<decltype(std::declval<T &>().transfer())>> : std::true_type {};

// DUT may provide split posedge/negedge tick for faster simulation.
template <typename T, typename = void>
struct has_tick_posedge : std::false_type {};

template <typename T>
struct has_tick_posedge<T, std::void_t<decltype(std::declval<T &>().tick_posedge())>> : std::true_type {};

template <typename T, typename = void>
struct has_tick_negedge : std::false_type {};

template <typename T>
struct has_tick_negedge<T, std::void_t<decltype(std::declval<T &>().tick_negedge())>> : std::true_type {};

template <typename T>
inline void maybe_comb(T &dut) {
  if constexpr (has_comb<T>::value) {
    dut.comb();
  } else if constexpr (has_eval<T>::value) {
    dut.eval();
  }
}

template <typename T>
inline void maybe_transfer(T &dut) {
  if constexpr (has_transfer<T>::value) {
    dut.transfer();
  }
}

template <typename T>
inline void maybe_tick_posedge(T &dut) {
  if constexpr (has_tick_posedge<T>::value) {
    dut.tick_posedge();
  } else {
    dut.tick();
  }
}

template <typename T>
inline void maybe_tick_negedge(T &dut) {
  if constexpr (has_tick_negedge<T>::value) {
    dut.tick_negedge();
  } else {
    dut.tick();
  }
}

} // namespace detail

template <typename Dut>
class Testbench {
public:
  explicit Testbench(Dut &dut) : dut_(dut) {}

  bool enableVcd(const std::string &path, const std::string &top = "tb", const std::string &timescale = "1ns") {
    vcd_.emplace();
    return vcd_->open(path, top, timescale);
  }

  template <unsigned W>
  bool vcdTrace(Wire<W> &sig, const std::string &name) {
    if (!vcd_)
      return false;
    return vcd_->add(sig, name);
  }

  bool enableLog(const std::string &path) {
    log_.emplace(path, std::ios::out | std::ios::trunc);
    return log_->is_open();
  }

  bool logEnabled() const { return log_.has_value() && log_->is_open(); }

  std::ostream &log() { return logEnabled() ? *log_ : std::cerr; }

  void addClock(Wire<1> &clk, std::uint64_t halfPeriodSteps = 1, std::uint64_t phaseSteps = 0, bool startHigh = false) {
    TbClock c;
    c.clk = &clk;
    c.half_period_steps = (halfPeriodSteps == 0) ? 1 : halfPeriodSteps;
    c.phase_steps = phaseSteps;
    c.set(startHigh);
    clocks_.push_back(c);
    fast_clock0_enabled_ = (clocks_.size() == 1u) && (c.clk != nullptr) && (c.half_period_steps == 1u) && (c.phase_steps == 0u);
  }

  std::size_t numClocks() const { return clocks_.size(); }
  std::uint64_t timeSteps() const { return time_; }

  // Restrict VCD dumping to an inclusive time-step window.
  // Useful for bounded trace capture around a trigger (Decision 0145).
  void setVcdWindow(std::uint64_t beginStep, std::uint64_t endStep) {
    if (beginStep > endStep)
      std::swap(beginStep, endStep);
    vcd_window_.emplace(beginStep, endStep);
  }

  void clearVcdWindow() { vcd_window_.reset(); }

  void step() {
    // Drive combinational logic before clock edges.
    detail::maybe_comb(dut_);

    // Toggle all clocks that have an edge on this step.
    for (const auto &c : clocks_) {
      if (c.shouldToggle(time_))
        c.toggle();
    }

    // Sequential update (modules detect posedges internally).
    dut_.tick();
    detail::maybe_transfer(dut_);

    // Re-evaluate combinational logic after state updates.
    detail::maybe_comb(dut_);

    if (shouldDumpVcd(time_))
      vcd_->dump(time_);

    time_++;
  }

  void runSteps(std::uint64_t steps) {
    if (steps == 0)
      return;
    if (!vcd_) {
      for (std::uint64_t i = 0; i < steps; i++)
        stepNoDump();
      return;
    }
    for (std::uint64_t i = 0; i < steps; i++)
      step();
  }

  void runCycles(std::uint64_t cycles) { runCycles(/*clockIdx=*/0, cycles); }

  void runCycles(std::size_t clockIdx, std::uint64_t cycles) {
    if (clockIdx >= clocks_.size())
      return;
    const auto hp = clocks_[clockIdx].half_period_steps;
    runSteps(cycles * 2u * hp);
  }

  // Fast-path cycle stepping for the common single-clock case:
  // - one clock registered in this testbench
  // - selected clock has half_period_steps=1 and phase_steps=0
  //
  // For all other cases we conservatively fall back to runCycles().
  void runPosedgeCycles(std::uint64_t cycles) { runPosedgeCycles(/*clockIdx=*/0, cycles); }

  void runPosedgeCycles(std::size_t clockIdx, std::uint64_t cycles) {
    if (!(clockIdx == 0u && fast_clock0_enabled_)) {
      runCycles(clockIdx, cycles);
      return;
    }
    runPosedgeCyclesFast(cycles);
  }

  void runCyclesAuto(std::uint64_t cycles) { runCyclesAuto(/*clockIdx=*/0, cycles); }

  void runCyclesAuto(std::size_t clockIdx, std::uint64_t cycles) {
    if (clockIdx == 0u && fast_clock0_enabled_) {
      runPosedgeCyclesFast(cycles);
      return;
    }
    runCycles(clockIdx, cycles);
  }

  // Trace-aware single-cycle stepping at canonical observation points
  // (Decision 0113): comb (pre-edge), tick (post-tick pre-transfer), and
  // commit/xfer (post-transfer, post-comb settle).
  //
  // Intended for generated testbenches that want binary trace samples at both
  // TICK-OBS and XFER-OBS without rewriting the clocking logic.
  void runCycleAutoTrace(std::uint64_t cycle, PycTraceBinWriter *trace) { runCycleAutoTrace(/*clockIdx=*/0, cycle, trace); }

  void runCycleAutoTrace(std::size_t clockIdx, std::uint64_t cycle, PycTraceBinWriter *trace) {
    if (!trace) {
      runCyclesAuto(clockIdx, 1);
      return;
    }
    if (clockIdx >= clocks_.size()) {
      runCyclesAuto(clockIdx, 1);
      return;
    }

    if (clockIdx == 0u && fast_clock0_enabled_) {
      auto &c = clocks_[0];

      // Posedge phase.
      detail::maybe_comb(dut_);
      trace->writeCombPhase(cycle);
      c.set(true);
      dut_.tick();
      trace->writeTickPhase(cycle);
      detail::maybe_transfer(dut_);
      detail::maybe_comb(dut_);
      trace->writeCommitPhase(cycle);
      if (shouldDumpVcd(time_))
        vcd_->dump(time_);
      time_++;

      // Negedge bookkeeping (no extra combinational settle needed here).
      c.set(false);
      dut_.tick();
      detail::maybe_transfer(dut_);
      if (shouldDumpVcd(time_))
        vcd_->dump(time_);
      time_++;
      return;
    }

    auto &c0 = clocks_[clockIdx];
    const std::uint64_t hp = (c0.half_period_steps == 0) ? 1u : c0.half_period_steps;
    const std::uint64_t steps_per_cycle = 2u * hp;
    bool traced = false;
    for (std::uint64_t i = 0; i < steps_per_cycle; i++) {
      // Drive combinational logic before clock edges.
      detail::maybe_comb(dut_);

      // Detect whether the selected clock will have a posedge on this step.
      const bool will_posedge = (!traced) && (c0.clk != nullptr) && c0.shouldToggle(time_) && (!c0.clk->toBool());
      if (will_posedge)
        trace->writeCombPhase(cycle);

      // Toggle all clocks that have an edge on this step.
      for (const auto &c : clocks_) {
        if (c.shouldToggle(time_))
          c.toggle();
      }

      // Sequential update (modules detect posedges internally).
      dut_.tick();
      if (will_posedge)
        trace->writeTickPhase(cycle);

      detail::maybe_transfer(dut_);

      // Re-evaluate combinational logic after state updates.
      detail::maybe_comb(dut_);
      if (will_posedge) {
        trace->writeCommitPhase(cycle);
        traced = true;
      }

      if (shouldDumpVcd(time_))
        vcd_->dump(time_);
      time_++;
    }
  }

  void reset(Wire<1> &rst, std::uint64_t cyclesAsserted = 2, std::uint64_t cyclesDeasserted = 1) {
    rst = Wire<1>(1);
    runCycles(cyclesAsserted);
    rst = Wire<1>(0);
    runCycles(cyclesDeasserted);
  }

private:
  void runPosedgeCyclesFast(std::uint64_t cycles) {
    if (cycles == 0)
      return;

    auto &c = clocks_[0];
    if (vcd_) {
      for (std::uint64_t i = 0; i < cycles; i++) {
        // Posedge phase.
        detail::maybe_comb(dut_);
        c.set(true);
        detail::maybe_tick_posedge(dut_);
        detail::maybe_transfer(dut_);
        detail::maybe_comb(dut_);
        if (shouldDumpVcd(time_))
          vcd_->dump(time_);
        time_++;

        // Negedge bookkeeping (no extra combinational settle needed here).
        c.set(false);
        detail::maybe_tick_negedge(dut_);
        detail::maybe_transfer(dut_);
        if (shouldDumpVcd(time_))
          vcd_->dump(time_);
        time_++;
      }
      return;
    }

    for (std::uint64_t i = 0; i < cycles; i++) {
      detail::maybe_comb(dut_);
      c.set(true);
      detail::maybe_tick_posedge(dut_);
      detail::maybe_transfer(dut_);
      detail::maybe_comb(dut_);
      time_++;
      c.set(false);
      detail::maybe_tick_negedge(dut_);
      detail::maybe_transfer(dut_);
      time_++;
    }
  }
  void stepNoDump() {
    detail::maybe_comb(dut_);
    for (const auto &c : clocks_) {
      if (c.shouldToggle(time_))
        c.toggle();
    }
    dut_.tick();
    detail::maybe_transfer(dut_);
    detail::maybe_comb(dut_);
    time_++;
  }

  bool shouldDumpVcd(std::uint64_t step) const {
    if (!vcd_)
      return false;
    if (!vcd_window_)
      return true;
    return step >= vcd_window_->first && step <= vcd_window_->second;
  }

  Dut &dut_;
  std::vector<TbClock> clocks_{};
  std::uint64_t time_ = 0;
  bool fast_clock0_enabled_ = false;
  std::optional<VcdWriter> vcd_{};
  std::optional<std::pair<std::uint64_t, std::uint64_t>> vcd_window_{};
  std::optional<std::ofstream> log_{};
};

} // namespace pyc::cpp
