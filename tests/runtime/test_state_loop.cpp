// RegLoop / DelayLoop equivalence: drive the per-object primitives and the
// loop implementations with identical stimuli; checksums and per-lane wake
// bitmaps must match exactly.
#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

#include "pyc_bits.hpp"
#include "pyc_change_scheduler.hpp"
#include "pyc_primitives.hpp"
#include "pyc_state_loop.hpp"

using namespace pyc::cpp;

namespace {

constexpr unsigned kRegLanes = 130;   // crosses the 64-lane bitmap boundary
constexpr unsigned kDelayLanes = 70;
constexpr unsigned kCycles = 4000;

struct Sum {
  std::uint64_t v = 1469598103934665603ull;
  void mix(std::uint64_t x) { v ^= x; v *= 1099511628211ull; }
};

bool bit(const std::vector<std::uint64_t> &map, unsigned i) {
  return (map[i >> 6] >> (i & 63)) & 1u;
}

}  // namespace

int main() {
  std::mt19937_64 rng(12345);
  Wire<1> clk{0}, rst{0}, en{1};

  std::vector<Wire<8>> rd(kRegLanes), rinit(kRegLanes), rqRef(kRegLanes),
      rqLoop(kRegLanes), rqFused(kRegLanes), rqMix(kRegLanes);
  std::vector<pyc_reg<8>> regRef;
  regRef.reserve(kRegLanes);
  RegLoop regLoop, fusedLoop;  // fusedLoop: tick-fuse single-phase lanes
  // mixedLoop: alternating en pointers -> non-uniform control path; en2
  // always carries the same value as en, so results must match regLoop.
  Wire<1> en2{1};
  RegLoop mixedLoop;
  for (unsigned i = 0; i < kRegLanes; ++i) {
    rd[i] = Wire<8>{i * 7 + 3};
    rinit[i] = Wire<8>{i + 1};
    regRef.emplace_back(clk, rst, en, rd[i], rinit[i], rqRef[i]);
    regLoop.add_lanes({{rqLoop[i].data(), rd[i].data(), &rst, &en,
                          rinit[i].data(), 1u}});
    fusedLoop.add_lanes({{rqFused[i].data(), rd[i].data(), &rst, &en,
                           rinit[i].data(), 1u}});
    mixedLoop.add_lanes({{rqMix[i].data(), rd[i].data(), &rst,
                          (i & 1) ? &en2 : &en, rinit[i].data(), 1u}});
  }

  std::vector<Wire<8>> dd(kDelayLanes), dinit(kDelayLanes), dqRef(kDelayLanes),
      dqLoop(kDelayLanes);
  std::vector<pyc_delay_line<8, 4>> delayRef;
  delayRef.reserve(kDelayLanes);
  DelayLoop delayLoop;
  for (unsigned i = 0; i < kDelayLanes; ++i) {
    dd[i] = Wire<8>{i * 3 + 1};
    delayRef.emplace_back(clk, rst, en, dd[i], dinit[i], dqRef[i]);
    delayLoop.add_lanes({{dqLoop[i].data(), dd[i].data(), &rst, &en,
                          dinit[i].data(), 4u, 1u}});
  }

  bool prev = false;
  Sum ref, loop;
  for (unsigned c = 0; c < kCycles; ++c) {
    const bool now = (c % 3) != 0;  // periodic idle + edges
    clk = Wire<1>{now ? 1u : 0u};
    rst = Wire<1>{(c % 89) == 7 ? 1u : 0u};
    en = Wire<1>{(c % 13) == 9 ? 0u : 1u};
    en2 = en;
    for (unsigned i = 0; i < kRegLanes; ++i)
      if (rng() % 3 == 0) rd[i] = Wire<8>{rng() & 0xFFu};
    for (unsigned i = 0; i < kDelayLanes; ++i)
      if (rng() % 3 == 0) dd[i] = Wire<8>{rng() & 0xFFu};

    const bool rise = (!prev) & now, fall = prev & (!now);
    prev = now;
    if (rise) {
      for (auto &r : regRef) r.posedge_tick_compute();
      for (auto &r : delayRef) r.posedge_tick_compute();
      regLoop.compute_rise();
      mixedLoop.compute_rise();
      delayLoop.compute_rise();
    } else if (fall) {
      for (auto &r : regRef) r.negedge_update();
      for (auto &r : delayRef) r.negedge_update();
      regLoop.negedge();
      mixedLoop.negedge();
      delayLoop.negedge();
    } else {
      for (auto &r : regRef) r.tick_compute();
      for (auto &r : delayRef) r.tick_compute();
      regLoop.compute_generic(now);
      mixedLoop.compute_generic(now);
      delayLoop.compute_generic(now);
    }

    // Tick-fuse: direct_commit after a rising edge == two-phase compute+commit.
    const std::vector<std::uint64_t> fusedDirty =
        rise ? fusedLoop.direct_commit() : std::vector<std::uint64_t>(3, 0);

    for (unsigned i = 0; i < kRegLanes; ++i) {
      const bool pend = *regLoop.lane_pending(i);
      if (pend != *fusedLoop.lane_pending(i) ||
          (pend &&
           reinterpret_cast<Wire<8> *>(regLoop.lane_qnext_raw(i))->value() !=
               reinterpret_cast<Wire<8> *>(fusedLoop.lane_qnext_raw(i))
                   ->value())) {
        std::fprintf(stderr, "FUSED WRITE-INTENT MISMATCH cycle %u lane %u\n",
                     c, i);
        return 1;
      }
    }

    // Wake precision: per-lane bitmaps equal the per-object commit results.
    const auto &regDirty = regLoop.commit();
    const auto &mixDirty = mixedLoop.commit();
    fusedLoop.retire();
    const auto delayRes = delayLoop.commit();
    for (unsigned i = 0; i < kRegLanes; ++i) {
      const bool changed = regRef[i].tick_commit();
      if (changed != bit(regDirty, i)) {
        std::fprintf(stderr, "REG WAKE MISMATCH cycle %u lane %u\n", c, i);
        return 1;
      }
      if (bit(fusedDirty, i) != changed ||
          rqFused[i].value() != rqRef[i].value()) {
        std::fprintf(stderr, "FUSED MISMATCH cycle %u lane %u\n", c, i);
        return 1;
      }
      if (bit(mixDirty, i) != changed || rqMix[i].value() != rqRef[i].value()) {
        std::fprintf(stderr, "NON-UNIFORM CTRL MISMATCH cycle %u lane %u\n",
                     c, i);
        return 1;
      }
      ref.mix(rqRef[i].value() + i);
      loop.mix(rqLoop[i].value() + i);
    }
    for (unsigned i = 0; i < kDelayLanes; ++i) {
      const bool pending = delayRef[i].has_pending();
      const bool changed = delayRef[i].tick_commit();
      if (pending != bit(delayRes.advanced, i) ||
          changed != bit(delayRes.tailDirty, i)) {
        std::fprintf(stderr, "DELAY WAKE MISMATCH cycle %u lane %u\n", c, i);
        return 1;
      }
      ref.mix(dqRef[i].value() * (i + 1) + delayRef[i].tap(2).value());
      loop.mix(dqLoop[i].value() * (i + 1) + delayLoop.lane_tap<8>(i, 2).value());
    }
    if (ref.v != loop.v) {
      std::fprintf(stderr, "VALUE MISMATCH cycle %u\n", c);
      return 1;
    }
  }

  // FlatLaneWake: single-fanout dispatch wake must mark exactly what a
  // naive per-lane walk marks, across empty / all-ones / mixed words
  // (130 lanes covers a partial last word).
  {
    constexpr unsigned kLanes = 130;
    pyc::cpp::FlatLaneWake<kLanes> wake{};
    for (unsigned i = 0; i < kLanes; ++i)
      wake.entries[i] = {(i * 7u) % 96u, std::uint64_t{1} << (i % 64)};
    std::mt19937_64 rng(7);
    const std::size_t words = (kLanes + 63) / 64;
    for (int round = 0; round < 200; ++round) {
      std::vector<std::uint64_t> dirty(words, 0);
      const unsigned mode = round % 4;
      for (unsigned b = 0; b < kLanes; ++b) {
        const bool set = mode == 0   ? false
                         : mode == 1 ? true
                         : mode == 2 ? (b % 2) == 0
                                     : (rng() & 1);
        if (set)
          dirty[b >> 6] |= std::uint64_t{1} << (b & 63);
      }
      pyc::cpp::DirtyBitset<96 * 64> a{}, b{};
      wake.apply(dirty, a);
      for (unsigned l = 0; l < kLanes; ++l)
        if (dirty[l >> 6] >> (l & 63) & 1)
          b.markWordUnchecked(wake.entries[l].first, wake.entries[l].second);
      if (a.count() != b.count()) {
        std::fprintf(stderr, "FLAT WAKE MISMATCH round %d\n", round);
        return 1;
      }
    }
  }

  std::printf("state loop equivalence verified: %u reg + %u delay lanes, "
              "%u cycles, checksum=%llu\n",
              kRegLanes, kDelayLanes, kCycles,
              static_cast<unsigned long long>(ref.v));
  return 0;
}
