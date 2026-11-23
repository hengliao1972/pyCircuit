// ForkJoinPool contract: every worker index runs exactly once per fork, the
// caller's body(0) participates, and worker state never bleeds across forks.
#include "pyc_parallel.hpp"

#include <cassert>
#include <cstdint>
#include <numeric>
#include <vector>

using pyc::cpp::ForkJoinPool;

namespace {

void testEveryWorkerRunsOnce() {
  using Pool = ForkJoinPool<4>;
  std::vector<unsigned> seen(4, 0);
  for (unsigned iter = 0; iter < 1000; ++iter) {
    Pool::instance().run(4, [&](unsigned worker) {
      assert(worker < 4);
      ++seen[worker];
    });
  }
  for (unsigned worker = 0; worker < 4; ++worker)
    assert(seen[worker] == 1000);
}

void testPartialFanout() {
  using Pool = ForkJoinPool<2>;
  // Two tasks on a two-way pool: worker 1 must run, worker 0 is the caller.
  unsigned helper = 0;
  unsigned caller = 0;
  for (unsigned iter = 0; iter < 1000; ++iter) {
    Pool::instance().run(2, [&](unsigned worker) {
      if (worker == 0)
        ++caller;
      else
        ++helper;
    });
  }
  assert(caller == 1000);
  assert(helper == 1000);

  // A single task stays on the caller and never starts a helper.
  unsigned solo = 0;
  for (unsigned iter = 0; iter < 100; ++iter) {
    Pool::instance().run(1, [&](unsigned worker) {
      assert(worker == 0);
      ++solo;
    });
  }
  assert(solo == 100);
}

void testMoreTasksThanWorkers() {
  // Every task index must run even when the pool is narrower than the task
  // count (a generated module forks all of its submodules at once).
  using Pool = ForkJoinPool<2>;
  std::vector<unsigned> seen(7, 0);
  for (unsigned iter = 0; iter < 200; ++iter) {
    Pool::instance().run(7, [&](unsigned task) {
      assert(task < 7);
      ++seen[task];
    });
  }
  for (unsigned task = 0; task < 7; ++task)
    assert(seen[task] == 200);
}

void testPrivateAccumulators() {
  // The intended generated-code shape: each worker writes only its own slot,
  // the caller merges after the join.
  using Pool = ForkJoinPool<4>;
  constexpr unsigned kSlots = 4;
  std::vector<std::uint64_t> slots(kSlots, 0);
  for (unsigned iter = 0; iter < 500; ++iter) {
    std::fill(slots.begin(), slots.end(), 0);
    Pool::instance().run(kSlots, [&](unsigned worker) {
      std::uint64_t acc = 0;
      for (unsigned k = 0; k < 1000; ++k)
        acc += worker + k;
      slots[worker] = acc;
    });
    std::uint64_t merged = 0;
    for (std::uint64_t slot : slots)
      merged += slot;
    std::uint64_t expected = 0;
    for (unsigned worker = 0; worker < kSlots; ++worker)
      expected += 1000u * worker + 999u * 1000u / 2u;
    assert(merged == expected);
  }
}

} // namespace

int main() {
  testEveryWorkerRunsOnce();
  testPartialFanout();
  testMoreTasksThanWorkers();
  testPrivateAccumulators();
  return 0;
}
