#include "pyc_change_scheduler.hpp"
#include "pyc_sync_mem.hpp"

#include <cassert>
#include <cstdint>
#include <random>
#include <vector>

using pyc::cpp::DirtyBitset;
using pyc::cpp::FusedLaneWake;

namespace {

void testDirtyBitset() {
  DirtyBitset<130> dirty;
  static_assert(sizeof(dirty) == 3 * sizeof(std::uint64_t));

  std::size_t rank = 999;
  assert(dirty.empty());
  assert(!dirty.takeNext(rank));
  assert(rank == 999);

  assert(dirty.mark(65));
  assert(!dirty.mark(65));
  assert(dirty.mark(129));
  assert(dirty.mark(0));
  assert(dirty.mark(64));
  assert(dirty.count() == 4);
  assert(dirty.test(65));
  assert(dirty.take(65));
  assert(!dirty.test(65));
  assert(!dirty.take(65));
  assert(dirty.count() == 3);

  assert(dirty.takeNext(rank) && rank == 0);
  assert(dirty.takeNext(rank) && rank == 64);
  assert(dirty.takeNext(rank) && rank == 129);
  assert(dirty.empty());
  assert(!dirty.takeNext(rank));

  assert(dirty.mark(7));
  dirty.clear();
  assert(dirty.empty());
}

// 5 lanes: lane0 -> {(1,0x6)}, lane2 -> {(0,0x1),(1,0x30)}, others none.
void testFusedLaneWake() {
  static constexpr FusedLaneWake<5, 3> wake{
      {{0u, 1u, 1u, 3u, 3u, 3u}},
      {{{1u, 0x6ull}, {0u, 0x1ull}, {1u, 0x30ull}}}};
  static constexpr FusedLaneWake<2, 0> noFanout{{{0u, 0u, 0u}}, {{}}};

  DirtyBitset<128> comb;
  // Lanes 0, 2, 4 dirty: exactly their fanout pairs get marked, and the
  // consumed bitmap is cleared in place.
  std::vector<std::uint64_t> dirty{0b10101u};
  wake.apply(dirty, comb);
  assert(comb.test(0));
  assert(comb.test(65) && comb.test(66));
  assert(comb.test(68) && comb.test(69));
  assert(comb.count() == 5);
  assert(dirty[0] == 0);

  // Only lanes without fanout dirty: nothing marked, input still consumed.
  comb.clear();
  dirty[0] = 0b10010u;
  wake.apply(dirty, comb);
  assert(comb.empty());
  assert(dirty[0] == 0);

  // Empty table wakes nothing even with every lane dirty (and clears).
  dirty[0] = ~0ull;
  noFanout.apply(dirty, comb);
  assert(comb.empty());
  assert(dirty[0] == 0);
}

} // namespace

// The word-mask applyStrb must reproduce the strobe semantics bit-by-bit:
// lane i covers 8*i..8*i+bits(i); a set strobe bit takes newV's lane, a
// clear one keeps oldV's. Random strobes/values over widths that exercise
// the <=64 fast path, the >64 word path, and a trailing partial lane.
template <unsigned Width>
void checkApplyStrbWidth(std::mt19937_64 &rng) {
  using Mem = pyc::cpp::pycSyncMemNM<4, Width, 4, 1, 1, true>;
  constexpr unsigned kStrbWidth = Mem::StrbWidth;
  using D = pyc::cpp::Wire<Width>;
  using S = pyc::cpp::Wire<kStrbWidth>;
  for (int round = 0; round < 300; ++round) {
    D oldV(0), newV(0);
    for (unsigned b = 0; b < Width; ++b) {
      if (rng() & 1) oldV = pyc::cpp::detail::setBit(oldV, b);
      if (rng() & 1) newV = pyc::cpp::detail::setBit(newV, b);
    }
    S strb(0);
    for (unsigned i = 0; i < kStrbWidth; ++i) {
      if (rng() & 1)
        strb = pyc::cpp::detail::setBit(strb, i);
    }
    const D got = Mem::applyStrb(oldV, newV, strb);
    for (unsigned b = 0; b < Width; ++b) {
      const unsigned lane = b / 8;
      const bool laneOn = strb.bit(lane);
      const bool expect = laneOn ? newV.bit(b) : oldV.bit(b);
      assert(got.bit(b) == expect);
    }
  }
}

void testSyncMemApplyStrb() {
  std::mt19937_64 rng(1234);
  checkApplyStrbWidth<16u>(rng);   // single word, <=64 fast path
  checkApplyStrbWidth<64u>(rng);   // exactly one word, full lanes
  checkApplyStrbWidth<72u>(rng);   // two words, trailing partial lane
  checkApplyStrbWidth<128u>(rng);  // two full words
  checkApplyStrbWidth<520u>(rng);  // nine words, partial lane
  checkApplyStrbWidth<1024u>(rng); // sixteen full words
}

int main() {
  testDirtyBitset();
  testFusedLaneWake();
  testSyncMemApplyStrb();
  return 0;
}
