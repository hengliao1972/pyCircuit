// RegLoop / DelayLoop equivalence: drive the per-object primitives and the
// loop implementations with identical stimuli; checksums and per-lane wake
// bitmaps must match exactly.
#include <cstdint>
#include <cstdio>
#include <random>
#include <vector>

#include "pyc_bits.hpp"
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
      rqLoop(kRegLanes), rqFused(kRegLanes);
  std::vector<pyc_reg<8>> regRef;
  regRef.reserve(kRegLanes);
  RegLoop<8> regLoop, fusedLoop;  // fusedLoop: tick-fuse single-phase lanes
  for (unsigned i = 0; i < kRegLanes; ++i) {
    rd[i] = Wire<8>{i * 7 + 3};
    rinit[i] = Wire<8>{i + 1};
    regRef.emplace_back(clk, rst, en, rd[i], rinit[i], rqRef[i]);
    regLoop.add_lanes({{&rqLoop[i], &rd[i], &rst, &en, &rinit[i]}});
    fusedLoop.add_lanes({{&rqFused[i], &rd[i], &rst, &en, &rinit[i]}});
  }

  std::vector<Wire<8>> dd(kDelayLanes), dinit(kDelayLanes), dqRef(kDelayLanes),
      dqLoop(kDelayLanes);
  std::vector<pyc_delay_line<8, 4>> delayRef;
  delayRef.reserve(kDelayLanes);
  DelayLoop<8> delayLoop;
  for (unsigned i = 0; i < kDelayLanes; ++i) {
    dd[i] = Wire<8>{i * 3 + 1};
    delayRef.emplace_back(clk, rst, en, dd[i], dinit[i], dqRef[i]);
    delayLoop.add_lanes({{&dqLoop[i], &dd[i], &rst, &en, &dinit[i], 4u}});
  }

  bool prev = false;
  Sum ref, loop;
  for (unsigned c = 0; c < kCycles; ++c) {
    const bool now = (c % 3) != 0;  // periodic idle + edges
    clk = Wire<1>{now ? 1u : 0u};
    rst = Wire<1>{(c % 89) == 7 ? 1u : 0u};
    en = Wire<1>{(c % 13) == 9 ? 0u : 1u};
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
      delayLoop.compute_rise();
    } else if (fall) {
      for (auto &r : regRef) r.negedge_update();
      for (auto &r : delayRef) r.negedge_update();
      regLoop.negedge();
      delayLoop.negedge();
    } else {
      for (auto &r : regRef) r.tick_compute();
      for (auto &r : delayRef) r.tick_compute();
      regLoop.compute_generic(now);
      delayLoop.compute_generic(now);
    }

    // Tick-fuse: direct_commit after a rising edge == two-phase compute+commit.
    const std::vector<std::uint64_t> fusedDirty =
        rise ? fusedLoop.direct_commit() : std::vector<std::uint64_t>(3, 0);

    // Wake precision: per-lane bitmaps equal the per-object commit results.
    const auto &regDirty = regLoop.commit();
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
      loop.mix(dqLoop[i].value() * (i + 1) + delayLoop.lane_tap(i, 2).value());
    }
    if (ref.v != loop.v) {
      std::fprintf(stderr, "VALUE MISMATCH cycle %u\n", c);
      return 1;
    }
  }

  std::printf("state loop equivalence verified: %u reg + %u delay lanes, "
              "%u cycles, checksum=%llu\n",
              kRegLanes, kDelayLanes, kCycles,
              static_cast<unsigned long long>(ref.v));
  return 0;
}
