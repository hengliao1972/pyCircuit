// RegLoop::direct_commit fast-path equivalence: single-word lanes, 2-word and
// multi-word lanes, idle edges (no rising edge, en=0), reset, and the opt-in
// write-intent probe mirror. Values, per-lane wake bits, pending flags and the
// qNext mirror must match the per-object pyc_reg semantics exactly.
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

constexpr unsigned kCycles = 2000;

bool bit(const std::vector<std::uint64_t> &map, unsigned i) {
  return (map[i >> 6] >> (i & 63)) & 1u;
}

template <unsigned W>
struct Lane {
  Wire<W> q, qRef, d, init;
};

// Drives one lane group through the generated-code shape: direct_commit() only
// on the rising edge (tick-fuse phase), per-object pyc_reg as the reference.
template <unsigned W>
int runGroup(const char *name, unsigned lanes, RegLoop &loop, Wire<1> &clk,
             Wire<1> &rst, Wire<1> &en, bool probe) {
  std::mt19937_64 rng(0xC0FFEEu + W);
  std::vector<Lane<W>> l(lanes);
  std::vector<pyc_reg<W>> ref;
  ref.reserve(lanes);
  const unsigned words = (W + 63) / 64;
  for (unsigned i = 0; i < lanes; ++i) {
    l[i].d = Wire<W>{static_cast<std::uint64_t>(i * 3 + 1)};
    l[i].init = Wire<W>{static_cast<std::uint64_t>(i + 7)};
    ref.emplace_back(clk, rst, en, l[i].d, l[i].init, l[i].qRef);
    loop.add_lanes({{l[i].q.data(), l[i].d.data(), &rst, &en,
                     l[i].init.data(), words}});
  }

  if (probe)
    // Bind before the first tick: write-intent storage is materialized from
    // the first accessor call on (unbound probes are dead stores).
    for (unsigned i = 0; i < lanes; ++i) {
      loop.lane_pending(i);
      loop.lane_qnext_raw(i);
    }

  bool prev = false;
  for (unsigned c = 0; c < kCycles; ++c) {
    const bool now = (c % 4) != 1;  // periodic idle + edges
    clk = Wire<1>{now ? 1u : 0u};
    rst = Wire<1>{(c % 97) == 13 ? 1u : 0u};
    en = Wire<1>{(c % 17) == 5 ? 0u : 1u};
    for (unsigned i = 0; i < lanes; ++i)
      if (rng() % 3 == 0)
        l[i].d = Wire<W>{rng()};
    const bool rise = (!prev) & now;
    prev = now;
    const bool go = rise && (rst.toBool() | en.toBool());

    // Reference: per-object sampling on the edge, commit right after.
    if (rise)
      for (auto &r : ref)
        r.tick_sample();
    else
      for (auto &r : ref)
        r.tick_sample();
    // Fused path: the generated code only calls direct_commit() on the edge.
    const std::vector<std::uint64_t> *dirty =
        rise ? &loop.direct_commit() : nullptr;
    for (unsigned i = 0; i < lanes; ++i) {
      const bool changed = ref[i].tick_commit();
      const bool wakeBit = dirty != nullptr && bit(*dirty, i);
      if (changed != wakeBit) {
        std::fprintf(stderr, "%s: wake mismatch cycle %u lane %u\n", name, c, i);
        return 1;
      }
      for (unsigned w = 0; w < words; ++w)
        if (l[i].q.data()[w] != l[i].qRef.data()[w]) {
          std::fprintf(stderr, "%s: value mismatch cycle %u lane %u\n", name,
                       c, i);
          return 1;
        }
      if (probe) {
        // Write-intent mirror: pending == "sampled this edge"; qNext == the
        // sampled value whenever the edge fired.
        if (*loop.lane_pending(i) != go) {
          std::fprintf(stderr, "%s: pending mismatch cycle %u lane %u\n", name,
                       c, i);
          return 1;
        }
        if (go) {
          const auto *next =
              reinterpret_cast<const Wire<W> *>(loop.lane_qnext_raw(i));
          const std::uint64_t *src =
              rst.toBool() ? l[i].init.data() : l[i].d.data();
          for (unsigned w = 0; w < words; ++w)
            if (next->data()[w] != src[w]) {
              std::fprintf(stderr, "%s: qnext mismatch cycle %u lane %u\n",
                           name, c, i);
              return 1;
            }
        }
      }
    }
    loop.retire();
    // The wake walk consumes (clears) the bitmap; mirror that here so the
    // next edge's staged mask is OR-ed into a clean word.
    loop.clearDirty();
  }
  std::printf("%s: %u lanes x %u words verified\n", name, lanes, words);
  return 0;
}

// The staged dirty mask must survive the #36 consume-in-place contract: the
// wake walk clears every word it reads, so the next edge's mask is OR-ed into
// a clean bitmap (no stale bit may leak into a later cycle's wake).
int runWakeConsume() {
  constexpr unsigned kLanes = 130;  // crosses the 64-lane word boundary
  Wire<1> clk{0}, rst{0}, en{1};
  std::vector<Wire<8>> q(kLanes), qRef(kLanes), d(kLanes), init(kLanes);
  RegLoop loop;
  std::vector<pyc_reg<8>> ref;
  ref.reserve(kLanes);
  for (unsigned i = 0; i < kLanes; ++i) {
    d[i] = Wire<8>{static_cast<std::uint64_t>(i * 5 + 1)};
    init[i] = Wire<8>{static_cast<std::uint64_t>(i + 3)};
    ref.emplace_back(clk, rst, en, d[i], init[i], qRef[i]);
    loop.add_lanes({{q[i].data(), d[i].data(), &rst, &en, init[i].data(), 1u}});
  }
  std::mt19937_64 rng(0x5EED);
  FusedLaneWake<kLanes, 0> wake{};  // empty fanout: apply() only consumes
  DirtyBitset<256> comb;
  bool prev = false;
  unsigned dirtyEdges = 0;
  for (unsigned c = 0; c < kCycles; ++c) {
    const bool now = (c % 4) != 1;
    clk = Wire<1>{now ? 1u : 0u};
    rst = Wire<1>{(c % 89) == 11 ? 1u : 0u};
    en = Wire<1>{(c % 19) == 7 ? 0u : 1u};
    for (unsigned i = 0; i < kLanes; ++i)
      if (rng() % 3 == 0)
        d[i] = Wire<8>{rng()};
    const bool rise = (!prev) & now;
    prev = now;
    // Mirror the generated drive: the reference sees every clock level, so its
    // previous-level latch stays in sync across falling edges too.
    for (auto &r : ref)
      r.tick_sample();
    if (!rise)
      continue;
    std::vector<std::uint64_t> &bits = loop.direct_commit();
    const std::vector<std::uint64_t> expected = [&] {
      std::vector<std::uint64_t> m((kLanes + 63) / 64, std::uint64_t{0});
      for (unsigned i = 0; i < kLanes; ++i)
        if (ref[i].tick_commit())
          m[i >> 6] |= std::uint64_t{1} << (i & 63);
      return m;
    }();
    if (bits != expected) {
      std::fprintf(stderr, "wake-consume: bit mismatch cycle %u\n", c);
      return 1;
    }
    for (std::uint64_t w : bits)
      dirtyEdges += (w != 0);
    wake.apply(bits, comb);
    for (std::uint64_t w : bits)
      if (w != 0) {
        std::fprintf(stderr, "wake-consume: apply left bits cycle %u\n", c);
        return 1;
      }
    loop.retire();
  }
  if (dirtyEdges == 0) {
    std::fprintf(stderr, "wake-consume: no dirty edge observed\n");
    return 1;
  }
  std::printf("wake-consume: %u lanes, %u dirty words verified over %u cycles\n",
              kLanes, (kLanes + 63) / 64, kCycles);
  return 0;
}

}  // namespace

int main() {
  Wire<1> clk{0}, rst{0}, en{1};
  int rc = 0;

  // No trace writer attached: the fused path skips the qNext arena and the
  // pending flags (dead stores); values and wake bits must still match.
  {
    RegLoop loop;
    rc |= runGroup<8>("single-word", 130, loop, clk, rst, en, false);
  }
  {
    RegLoop loop;
    rc |= runGroup<128>("two-word", 70, loop, clk, rst, en, false);
  }
  {
    RegLoop loop;
    rc |= runGroup<192>("three-word", 65, loop, clk, rst, en, false);
  }
  {
    RegLoop loop;
    rc |= runGroup<256>("four-word", 30, loop, clk, rst, en, false);
  }
  {
    RegLoop loop;
    rc |= runGroup<1024>("sixteen-word", 20, loop, clk, rst, en, false);
  }
  // Write-intent probes enabled: the mirror must stay exact.
  {
    RegLoop loop;
    rc |= runGroup<8>("probe-single-word", 130, loop, clk, rst, en, true);
  }
  {
    RegLoop loop;
    rc |= runGroup<128>("probe-two-word", 70, loop, clk, rst, en, true);
  }
  {
    RegLoop loop;
    rc |= runGroup<1024>("probe-sixteen-word", 20, loop, clk, rst, en, true);
  }

  rc |= runWakeConsume();

  if (rc == 0)
    std::printf("commit-shrink fast path verified\n");
  return rc;
}
