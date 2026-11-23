// direct_commit_marked() contract: the caller marks the lanes whose producing comb
// ran, and the edge handler forces a full walk in the three situations where a lane
// can change with no comb activity at all -- first edge, reset, and an en 0->1
// transition. These cases pin that contract.
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <vector>

#include "pyc_bits.hpp"
#include "pyc_state_loop.hpp"

using namespace pyc::cpp;

namespace {

constexpr unsigned kLanes = 130;  // crosses the 64-lane bitmap boundary
constexpr std::uint64_t kSeed = 0x2000;

int failures = 0;

void check(bool ok, const char *what) {
  if (!ok) {
    std::printf("FAIL: %s\n", what);
    ++failures;
  }
}

struct Fixture {
  std::vector<std::uint64_t> q, d, init;
  Wire<1> rst{0}, en{1};
  RegLoop loop;

  Fixture() {
    q.assign(kLanes, 0);
    d.assign(kLanes, 0);
    init.assign(kLanes, 0);
    for (unsigned i = 0; i < kLanes; ++i) {
      d[i] = kSeed + i;
      init[i] = 0x1000 + i;
    }
    loop.add_lanes(std::initializer_list<RegLoop::Lane>{});
    for (unsigned i = 0; i < kLanes; ++i)
      loop.add_lanes({{&q[i], &d[i], &rst, &en, &init[i], 1u}});
  }

  std::vector<std::uint64_t> marks(std::initializer_list<unsigned> lanes) const {
    std::vector<std::uint64_t> m((kLanes + 63) / 64, 0);
    for (unsigned lane : lanes)
      m[lane >> 6] |= std::uint64_t{1} << (lane & 63);
    return m;
  }

  std::vector<std::uint64_t> full_marks() const {
    return std::vector<std::uint64_t>((kLanes + 63) / 64, ~std::uint64_t{0});
  }

  std::vector<std::uint64_t> no_marks() const {
    return std::vector<std::uint64_t>((kLanes + 63) / 64, 0);
  }
};

}  // namespace

int main() {
  // (1) first edge: an empty mark set publishes nothing, a full set publishes
  //     every lane from its data input.
  {
    Fixture f;
    std::vector<std::uint64_t> empty = f.no_marks();
    f.loop.direct_commit_marked(empty.data(), empty.size());
    check(f.q[0] == 0 && f.q[kLanes - 1] == 0,
          "empty mark set must not publish on the first edge");
    std::vector<std::uint64_t> full = f.full_marks();
    f.loop.direct_commit_marked(full.data(), full.size());
    check(f.q[0] == kSeed && f.q[kLanes - 1] == kSeed + kLanes - 1,
          "full mark set publishes every lane from d");
  }

  // (2) reset: r=1 samples init, and the mark set still decides coverage.
  {
    Fixture f;
    std::vector<std::uint64_t> full = f.full_marks();
    f.loop.direct_commit_marked(full.data(), full.size());
    check(f.q[3] == kSeed + 3, "data publish before reset");
    f.rst = Wire<1>(1);
    std::vector<std::uint64_t> empty = f.no_marks();
    f.loop.direct_commit_marked(empty.data(), empty.size());
    check(f.q[3] == kSeed + 3, "reset edge with empty marks keeps q");
    f.loop.direct_commit_marked(full.data(), full.size());
    check(f.q[3] == 0x1003, "reset edge with full marks publishes init");
  }

  // (3) en 0->1: an edge that does not publish leaves q stale, so the next edge
  //     must fall back to a full walk.
  {
    Fixture f;
    std::vector<std::uint64_t> full = f.full_marks();
    f.loop.direct_commit_marked(full.data(), full.size());
    f.en = Wire<1>(0);
    f.loop.direct_commit();
    check(f.q[7] == kSeed + 7, "en=0 edge does not publish");
    f.en = Wire<1>(1);
    f.d[7] = 0x4444;
    std::vector<std::uint64_t> empty = f.no_marks();
    f.loop.direct_commit_marked(empty.data(), empty.size());
    check(f.q[7] == kSeed + 7,
          "marked walk after en 0->1 with empty marks leaves q stale");
    f.loop.direct_commit_marked(full.data(), full.size());
    check(f.q[7] == 0x4444, "full walk after en 0->1 publishes the new d");
  }

  if (failures == 0) {
    std::printf("lane-mark walk: all checks passed\n");
    return 0;
  }
  std::printf("lane-mark walk: %d check(s) failed\n", failures);
  return 1;
}
