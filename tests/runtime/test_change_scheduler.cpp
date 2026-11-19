#include "pyc_change_scheduler.hpp"

#include <cassert>
#include <cstdint>
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
  // Lanes 0, 2, 4 dirty: exactly their fanout pairs get marked.
  wake.apply(std::vector<std::uint64_t>{0b10101u}, comb);
  assert(comb.test(0));
  assert(comb.test(65) && comb.test(66));
  assert(comb.test(68) && comb.test(69));
  assert(comb.count() == 5);

  // Only lanes without fanout dirty: nothing marked.
  comb.clear();
  wake.apply(std::vector<std::uint64_t>{0b10010u}, comb);
  assert(comb.empty());

  // Empty table wakes nothing even with every lane dirty.
  noFanout.apply(std::vector<std::uint64_t>{~0ull}, comb);
  assert(comb.empty());
}

} // namespace

int main() {
  testDirtyBitset();
  testFusedLaneWake();
  return 0;
}
