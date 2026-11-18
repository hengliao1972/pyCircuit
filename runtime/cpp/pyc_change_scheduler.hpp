#pragma once

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

namespace pyc::cpp {

// Fixed-capacity dirty set keyed by precomputed topological rank. Marking an
// already-dirty rank coalesces the duplicate, and takeNext always removes the
// smallest rank. All storage is inline; no operation allocates.
template <std::size_t Capacity>
class DirtyBitset {
  static_assert(Capacity > 0, "DirtyBitset requires non-zero capacity");

public:
  static constexpr std::size_t kCapacity = Capacity;
  static constexpr std::size_t kWordBits = 64;
  static constexpr std::size_t kWords =
      (kCapacity + kWordBits - 1) / kWordBits;

  // Returns true only when rank transitions from clean to dirty.
  bool mark(std::size_t rank) noexcept {
    assert(rank < kCapacity && "dirty rank exceeds fixed capacity");
    if (rank >= kCapacity)
      return false;
    const std::size_t word = rank / kWordBits;
    const std::uint64_t mask = std::uint64_t{1} << (rank % kWordBits);
    const bool inserted = (words_[word] & mask) == 0;
    words_[word] |= mask;
    return inserted;
  }

  bool test(std::size_t rank) const noexcept {
    assert(rank < kCapacity && "dirty rank exceeds fixed capacity");
    if (rank >= kCapacity)
      return false;
    const std::size_t word = rank / kWordBits;
    const std::uint64_t mask = std::uint64_t{1} << (rank % kWordBits);
    return (words_[word] & mask) != 0;
  }

  // Removes one known rank. Returns true only if it was dirty.
  bool take(std::size_t rank) noexcept {
    assert(rank < kCapacity && "dirty rank exceeds fixed capacity");
    if (rank >= kCapacity)
      return false;
    const std::size_t word = rank / kWordBits;
    const std::uint64_t mask = std::uint64_t{1} << (rank % kWordBits);
    const bool present = (words_[word] & mask) != 0;
    words_[word] &= ~mask;
    return present;
  }

  // Removes the minimum topological rank. Returns false when the set is empty.
  bool takeNext(std::size_t &rank) noexcept {
    for (std::size_t word = 0; word < kWords; ++word) {
      if (words_[word] == 0)
        continue;
      const unsigned bit = lowestSetBit(words_[word]);
      words_[word] &= ~(std::uint64_t{1} << bit);
      rank = word * kWordBits + bit;
      return true;
    }
    return false;
  }

  bool empty() const noexcept {
    for (std::uint64_t word : words_) {
      if (word != 0)
        return false;
    }
    return true;
  }

  // Bulk wake: OR a precomputed (word, mask) pair in one store. Generated
  // simulators emit these pairs as compile-time constants so a whole fanout
  // group collapses to one load+or+store instead of a mark loop.
  void markWordUnchecked(std::size_t word, std::uint64_t mask) noexcept {
    assert(word < kWords && "dirty word index exceeds fixed capacity");
    words_[word] |= mask;
  }

  // True when no rank in [word*64, word*64+64) is dirty. Generated guard
  // groups test this once to skip up to 64 per-comb checks.
  bool wordClean(std::size_t word) const noexcept {
    assert(word < kWords && "dirty word index exceeds fixed capacity");
    return words_[word] == 0;
  }

  std::size_t count() const noexcept {
    std::size_t total = 0;
    for (std::uint64_t word : words_) {
      while (word != 0) {
        word &= word - 1;
        ++total;
      }
    }
    return total;
  }

  void clear() noexcept { words_.fill(0); }

private:
  static unsigned lowestSetBit(std::uint64_t word) noexcept {
    assert(word != 0);
    unsigned bit = 0;
    while ((word & std::uint64_t{1}) == 0) {
      word >>= 1;
      ++bit;
    }
    return bit;
  }

  std::array<std::uint64_t, kWords> words_{};
};

// Tick-fuse table-driven wake: iterate only the set bits of a fused loop's
// per-lane dirty bitmap and replay each dirty lane's precomputed fanout
// (word, mask) pairs, one load+or+store per pair. Generated simulators emit
// `offsets`/`entries` as compile-time constants so the fused commit walk is
// one tiny loop instead of one emitted branch per lane.
template <std::size_t Lanes, std::size_t Entries>
struct FusedLaneWake {
  // Prefix bounds into `entries`: lane i's fanout is entries[offsets[i],
  // offsets[i+1]). Lanes with no comb fanout contribute nothing.
  std::array<unsigned, Lanes + 1> offsets{};
  std::array<std::pair<unsigned, std::uint64_t>, Entries> entries{};

  template <std::size_t Capacity>
  void apply(const std::vector<std::uint64_t> &dirty,
             DirtyBitset<Capacity> &comb) const noexcept {
    constexpr std::size_t kWords = (Lanes + 63) / 64;
    // Bits above Lanes in the last word address no offsets entry; mask them
    // so apply stays in-bounds whatever the dirty bitmap contains.
    constexpr std::uint64_t kValidBits =
        (Lanes % 64) == 0 ? ~std::uint64_t{0}
                          : (std::uint64_t{1} << (Lanes % 64)) - 1;
    for (std::size_t w = 0; w < kWords; ++w) {
      std::uint64_t bits =
          dirty[w] & (w + 1 == kWords ? kValidBits : ~std::uint64_t{0});
      while (bits) {
        const unsigned lane =
            static_cast<unsigned>(w * 64) +
            static_cast<unsigned>(__builtin_ctzll(bits));
        bits &= bits - 1;
        for (unsigned k = offsets[lane]; k != offsets[lane + 1]; ++k)
          comb.markWordUnchecked(entries[k].first, entries[k].second);
      }
    }
  }
};

} // namespace pyc::cpp
