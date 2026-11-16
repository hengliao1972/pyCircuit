#include "pyc_change_scheduler.hpp"

#include <cassert>
#include <cstdint>

using pyc::cpp::DirtyBitset;

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

} // namespace

int main() {
  testDirtyBitset();
  return 0;
}
