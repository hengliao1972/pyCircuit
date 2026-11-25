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
      rqLoop(kRegLanes), rqFused(kRegLanes);
  std::vector<pycReg<8>> regRef;
  regRef.reserve(kRegLanes);
  RegLoop regLoop, fusedLoop;  // fusedLoop: tick-fuse single-phase lanes
  for (unsigned i = 0; i < kRegLanes; ++i) {
    rd[i] = Wire<8>{i * 7 + 3};
    rinit[i] = Wire<8>{i + 1};
    regRef.emplace_back(clk, rst, en, rd[i], rinit[i], rqRef[i]);
    regLoop.add_lanes({{rqLoop[i].data(), rd[i].data(), &rst, &en,
                          rinit[i].data(), 1u}});
    fusedLoop.add_lanes({{rqFused[i].data(), rd[i].data(), &rst, &en,
                           rinit[i].data(), 1u}});
  }

  std::vector<Wire<8>> dd(kDelayLanes), dinit(kDelayLanes), dqRef(kDelayLanes),
      dqLoop(kDelayLanes);
  std::vector<pycDelayLine<8, 4>> delayRef;
  delayRef.reserve(kDelayLanes);
  DelayLoop delayLoop;
  for (unsigned i = 0; i < kDelayLanes; ++i) {
    dd[i] = Wire<8>{i * 3 + 1};
    delayRef.emplace_back(clk, rst, en, dd[i], dinit[i], dqRef[i]);
    delayLoop.add_lanes({{dqLoop[i].data(), dd[i].data(), &rst, &en,
                          dinit[i].data(), 4u, 1u}});
  }

  // Write-intent flags are materialized from the first lane_pending access on
  // (lanes nobody binds cost nothing), so a consumer binds before the first
  // tick — exactly like pyc_register_probes. The write data a probe reads is
  // the lane's own q, after commit, so nothing else has to be bound.
  for (unsigned i = 0; i < kRegLanes; ++i) {
    fusedLoop.lane_pending(i);
    regLoop.lane_pending(i);
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
      for (auto &r : regRef) r.tick_sample();
      for (auto &r : delayRef) r.tick_sample();
      regLoop.compute_rise();
      delayLoop.compute_rise();
    } else if (fall) {
      for (auto &r : regRef) r.tick_sample();
      for (auto &r : delayRef) r.tick_sample();
      regLoop.negedge();
      delayLoop.negedge();
    } else {
      for (auto &r : regRef) r.tick_sample();
      for (auto &r : delayRef) r.tick_sample();
      regLoop.compute_generic(now);
      delayLoop.compute_generic(now);
    }

    // Tick-fuse: direct_commit after a rising edge == two-phase compute+commit.
    const std::vector<std::uint64_t> fusedDirty =
        rise ? fusedLoop.direct_commit() : std::vector<std::uint64_t>(3, 0);

    for (unsigned i = 0; i < kRegLanes; ++i) {
      // Write data is the lane's own q storage for both the two-phase and the
      // fused loop: the trace writer reads it post-transfer, when q is final.
      if (regLoop.lane_write_data(i) != rqLoop[i].data() ||
          fusedLoop.lane_write_data(i) != rqFused[i].data()) {
        std::fprintf(stderr, "WRITE DATA IS NOT Q cycle %u lane %u\n", c, i);
        return 1;
      }
      // The flag is only defined at an edge's post-transfer observation
      // point: fused lanes keep it from their in-compute commit until the
      // next edge, phased lanes clear it on a non-edge tick.
      if (rise && *regLoop.lane_pending(i) != *fusedLoop.lane_pending(i)) {
        std::fprintf(stderr, "FUSED WRITE-INTENT MISMATCH cycle %u lane %u\n",
                     c, i);
        return 1;
      }
    }

    // Wake precision: per-lane bitmaps equal the per-object commit results.
    const auto &regDirty = regLoop.commit();
    const auto delayRes = delayLoop.commit();
    // The write-intent flag must outlive commit: it is sampled at the
    // post-transfer observation point. (Fused lanes keep the flag from their
    // in-compute commit until the next edge overwrites it.)
    if (rise) {
      const bool wrote = rst.toBool() | en.toBool();
      for (unsigned i = 0; i < kRegLanes; ++i) {
        if (*regLoop.lane_pending(i) != wrote ||
            *fusedLoop.lane_pending(i) != wrote) {
          std::fprintf(stderr, "PENDING LOST AFTER COMMIT cycle %u lane %u\n",
                       c, i);
          return 1;
        }
      }
    }
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
      loop.mix(dqLoop[i].value() * (i + 1) + delayLoop.lane_tap<8>(i, 2).value());
    }
    if (ref.v != loop.v) {
      std::fprintf(stderr, "VALUE MISMATCH cycle %u\n", c);
      return 1;
    }
    // The dirty bitmaps are consumed in place by the wake walk in generated
    // code; this test reads them directly, so reset them explicitly.
    regLoop.clearDirty();
    fusedLoop.clearDirty();
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
      const std::vector<std::uint64_t> roundDirty = dirty;
      wake.apply(dirty, a);
      // Consuming wake: the bitmap must be fully cleared after apply.
      for (std::size_t w = 0; w < dirty.size(); ++w)
        if (dirty[w] != 0) {
          std::fprintf(stderr, "FLAT WAKE NOT CONSUMED round %d\n", round);
          return 1;
        }
      for (unsigned l = 0; l < kLanes; ++l)
        if (roundDirty[l >> 6] >> (l & 63) & 1)
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
