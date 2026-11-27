// Copyright (c) 2026, pyCircuit contributors.
// SPDX-License-Identifier: BSD-3-Clause
//
// Optional fork/join helper for the generated C++ models.
//
// The emitter can split a top-level eval (and the tick pair) so that sibling
// instances which have no same-tick dependency on each other are evaluated on
// separate threads. That is pure work sharing: every fork body touches only
// its own instance's state, and the caller merges the per-instance results
// after the join.
//
// Liveness first: a fork/join must always terminate, on any machine and under
// any scheduling. Two rules make that structural rather than probable:
//
//   1. Every fork owns a Job on the caller's stack. Workers take a Job pointer,
//      and only that Job's own counters are touched, so a worker can never
//      consume a task of a fork it does not belong to. A supervisor that
//      finishes a Job's tasks is by definition done; there is no shared
//      "remaining count" that a stale participant could unbalance.
//   2. The caller never waits for a worker to *be scheduled*. It claims tasks
//      itself, so the number of tasks another thread holds is bounded by the
//      handful it already claimed, and that thread is inside a job body, not
//      parked. A worker is only ever handed work while the caller is willing to
//      wait for it (the share window), and it is parked outside that window.
//
// A Job is withdrawn (job_ cleared) and the pool waits for the last worker to
// leave before the caller's stack frame dies, so no worker can observe a
// dangling Job.
//
// Threads are created lazily by the first fork, so a serial build (flag off)
// does not pay for them. Only <thread> is used: on glibc >= 2.34 pthread is
// part of libc, so the plain `clang++ -std=c++17 -O3 ...` line links.

#ifndef PYC_CPP_PYC_PARALLEL_HPP
#define PYC_CPP_PYC_PARALLEL_HPP

#if defined(__linux__)
#include <sched.h>
#endif

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <type_traits>
#include <vector>

namespace pyc {
namespace cpp {

namespace parallel_detail {

// Read the affinity mask instead of the core count: a process restricted by
// taskset/cpuset reports fewer usable CPUs here, which is exactly the case
// where a fork cannot pay off.
inline bool hasSpareCpu() {
#if defined(__linux__) && defined(_GNU_SOURCE)
  cpu_set_t set;
  CPU_ZERO(&set);
  if (sched_getaffinity(0, sizeof(set), &set) != 0)
    return true;
  return CPU_COUNT(&set) >= 2;
#else
  return true; // unknown platform: rely on the share window instead
#endif
}

inline bool disabledByEnv() {
  const char *flag = std::getenv("PYC_PARALLEL_DISABLE");
  return flag != nullptr && flag[0] != '\0' && flag[0] != '0';
}

inline void cpuRelax() noexcept {
#if defined(__x86_64__) || defined(__i386__)
  __builtin_ia32_pause();
#elif defined(__aarch64__)
  asm volatile("yield" ::: "memory");
#else
  std::atomic_signal_fence(std::memory_order_seq_cst);
#endif
}

// 0 while no fork is in flight anywhere in this process. A nested fork (a
// worker running a body that itself reaches a parallel top-level func, or the
// model being re-entered) must not publish a job, so it runs the tasks here.
inline std::atomic<unsigned> &forkDepth() {
  static std::atomic<unsigned> depth{0};
  return depth;
}

} // namespace parallel_detail

// One fork's work: the callback, its context, and this fork's own task
// counters. Lives on the caller's stack for the duration of run().
struct ParallelJob {
  const void *context = nullptr;
  void (*invoke)(const void *, unsigned) = nullptr;
  unsigned tasks = 0;
  std::atomic<unsigned> next{0};
  std::atomic<unsigned> done{0};

  void claimAndRun() {
    for (;;) {
      unsigned task = next.fetch_add(1, std::memory_order_acq_rel);
      if (task >= tasks)
        return;
      invoke(context, task);
      done.fetch_add(1, std::memory_order_acq_rel);
    }
  }
};

template <unsigned Threads> class ForkJoinPool {
public:
  static ForkJoinPool &instance() {
    static ForkJoinPool pool;
    return pool;
  }

  template <typename Body> void run(unsigned tasks, Body &&body) {
    if (tasks < 2 || parallel_detail::forkDepth().load(std::memory_order_acquire) != 0 ||
        !enabled()) {
      for (unsigned task = 0; task < tasks; ++task)
        body(task);
      return;
    }

    ParallelJob job;
    job.context = static_cast<const void *>(&body);
    job.invoke = &trampoline<typename std::remove_reference<Body>::type>;
    job.tasks = tasks;

    // Publishing under the mutex is what makes the park path correct: a worker
    // either observes the new job at its predicate check (and does not park),
    // or is already registered as a waiter that this fork's notify_all wakes.
    start();
    {
      std::lock_guard<std::mutex> lock(mutex_);
      job_.store(&job, std::memory_order_release);
      ++generation_;
      shareTasks_ = offerWork_;
    }
    if (shareTasks_)
      wakeWorkers();

    if (offerWork_)
      openShareWindow();
    else if (++forkCount_ % kProbeInterval == 0)
      offerWork_ = true; // retry parallelism occasionally

    parallel_detail::forkDepth().fetch_add(1, std::memory_order_acq_rel);
    job.claimAndRun();
    parallel_detail::forkDepth().fetch_sub(1, std::memory_order_acq_rel);

    while (job.done.load(std::memory_order_acquire) != job.tasks)
      parallel_detail::cpuRelax();

    retireJob(&job);
  }

private:
  ForkJoinPool() = default;

  ~ForkJoinPool() {
    if (!started_)
      return;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      stop_ = true;
      ++generation_;
    }
    wakeWorkers();
    for (std::thread &worker : workers_)
      if (worker.joinable())
        worker.join();
  }

  ForkJoinPool(const ForkJoinPool &) = delete;
  ForkJoinPool &operator=(const ForkJoinPool &) = delete;

  bool enabled() {
    static const bool ready = parallel_detail::hasSpareCpu() &&
                              !parallel_detail::disabledByEnv();
    return ready;
  }

  template <typename Body>
  static void trampoline(const void *context, unsigned task) {
    (*static_cast<Body *>(const_cast<void *>(context)))(task);
  }

  void start() {
    if (started_)
      return;
    started_ = true;
    workers_.reserve(Threads - 1);
    for (unsigned id = 1; id < Threads; ++id)
      workers_.emplace_back([this] { workerLoop(); });
  }

  void wakeWorkers() {
    std::lock_guard<std::mutex> lock(mutex_);
    cv_.notify_all();
  }

  // Wait briefly for a worker to show up. The window only opens while workers
  // are known to answer promptly: once several hand-offs in a row answered
  // late (host saturated, no free CPU) the caller stops offering work and runs
  // every task itself, which keeps a loaded machine at serial speed instead of
  // paying a scheduling round trip per fork. One fork in kProbeInterval tries
  // again, so a pool that regains spare CPUs goes back to parallel.
  void openShareWindow() {
    unsigned spins = 0;
    for (; spins < kHandoffSpins; ++spins) {
      if (inside_.load(std::memory_order_acquire) != 0)
        break;
      parallel_detail::cpuRelax();
    }
    if (spins > kSlowHandoffSpins) {
      if (++handoffMisses_ > kHandoffMissLimit) {
        offerWork_ = false;
        shareTasks_ = false;
        handoffMisses_ = 0;
      }
    } else {
      handoffMisses_ = 0;
    }
  }

  // Clear job_ and wait for the last worker to leave, so the caller's Job (and
  // the body it points at) outlives every worker that could still touch it.
  void retireJob(ParallelJob *job) {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (job_.load(std::memory_order_relaxed) == job)
        job_.store(nullptr, std::memory_order_release);
    }
    // A worker may pick the job in the window before job_ is cleared; it is
    // inside a task, so this wait is bounded by one task, not by scheduling.
    while (inside_.load(std::memory_order_acquire) != 0)
      parallel_detail::cpuRelax();
  }

  void workerLoop() {
    for (;;) {
      ParallelJob *job = nullptr;
      // A model forks several times per simulated cycle, so workers spin for a
      // while before parking; parking only keeps an idle process from burning
      // a core between runs.
      for (unsigned spins = 0;; ++spins) {
        job = job_.load(std::memory_order_acquire);
        if (job != nullptr && shareTasks_)
          break;
        if (spins < kIdleSpins) {
          parallel_detail::cpuRelax();
          continue;
        }
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [this] {
          return stop_ || (job_.load(std::memory_order_acquire) != nullptr && shareTasks_);
        });
        job = job_.load(std::memory_order_acquire);
        break;
      }
      if (stop_)
        return;
      if (job == nullptr)
        continue;
      // Enter the job, then re-check that the caller still wants help. Two
      // workers never touch the same Job (only the caller publishes), and the
      // caller waits for inside_ to drain before it drops the Job.
      inside_.fetch_add(1, std::memory_order_acq_rel);
      if (job_.load(std::memory_order_acquire) != job || !shareTasks_) {
        inside_.fetch_sub(1, std::memory_order_acq_rel);
        continue;
      }
      parallel_detail::forkDepth().fetch_add(1, std::memory_order_acq_rel);
      job->claimAndRun();
      parallel_detail::forkDepth().fetch_sub(1, std::memory_order_acq_rel);
      inside_.fetch_sub(1, std::memory_order_acq_rel);
    }
  }

  static constexpr unsigned kHandoffSpins = 512;
  static constexpr unsigned kSlowHandoffSpins = 96;
  static constexpr unsigned kHandoffMissLimit = 4;
  static constexpr unsigned kProbeInterval = 64;
  static constexpr unsigned kIdleSpins = 1u << 14;

  std::atomic<ParallelJob *> job_{nullptr};
  std::atomic<unsigned> inside_{0};
  std::atomic<unsigned> generation_{0};
  std::atomic<bool> shareTasks_{true};
  std::atomic<bool> stop_{false};
  bool started_ = false;
  bool offerWork_ = true;
  unsigned handoffMisses_ = 0;
  unsigned forkCount_ = 0;
  std::vector<std::thread> workers_;
  std::mutex mutex_;
  std::condition_variable cv_;
};

} // namespace cpp
} // namespace pyc

#endif // PYC_CPP_PYC_PARALLEL_HPP
