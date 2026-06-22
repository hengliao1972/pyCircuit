#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// Decision 0021: hash64 algorithm is xxHash64 (seed=0 by default).
inline std::uint64_t xxhash64(std::string_view data, std::uint64_t seed = 0) {
  constexpr std::uint64_t PRIME1 = 11400714785074694791ULL;
  constexpr std::uint64_t PRIME2 = 14029467366897019727ULL;
  constexpr std::uint64_t PRIME3 = 1609587929392839161ULL;
  constexpr std::uint64_t PRIME4 = 9650029242287828579ULL;
  constexpr std::uint64_t PRIME5 = 2870177450012600261ULL;

  auto rotl = [](std::uint64_t x, int r) -> std::uint64_t { return (x << r) | (x >> (64 - r)); };

  auto read64 = [](const std::uint8_t *p) -> std::uint64_t {
    std::uint64_t v = 0;
    std::memcpy(&v, p, sizeof(v));
#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)
    v = __builtin_bswap64(v);
#endif
    return v;
  };

  auto read32 = [](const std::uint8_t *p) -> std::uint32_t {
    std::uint32_t v = 0;
    std::memcpy(&v, p, sizeof(v));
#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)
    v = __builtin_bswap32(v);
#endif
    return v;
  };

  auto round = [&](std::uint64_t acc, std::uint64_t input) -> std::uint64_t {
    acc += input * PRIME2;
    acc = rotl(acc, 31);
    acc *= PRIME1;
    return acc;
  };

  auto mergeRound = [&](std::uint64_t acc, std::uint64_t val) -> std::uint64_t {
    val = round(0, val);
    acc ^= val;
    acc = acc * PRIME1 + PRIME4;
    return acc;
  };

  const std::uint8_t *p = reinterpret_cast<const std::uint8_t *>(data.data());
  const std::uint8_t *end = p + data.size();

  std::uint64_t h64 = 0;
  if (data.size() >= 32) {
    std::uint64_t v1 = seed + PRIME1 + PRIME2;
    std::uint64_t v2 = seed + PRIME2;
    std::uint64_t v3 = seed + 0;
    std::uint64_t v4 = seed - PRIME1;

    const std::uint8_t *limit = end - 32;
    do {
      v1 = round(v1, read64(p));
      p += 8;
      v2 = round(v2, read64(p));
      p += 8;
      v3 = round(v3, read64(p));
      p += 8;
      v4 = round(v4, read64(p));
      p += 8;
    } while (p <= limit);

    h64 = rotl(v1, 1) + rotl(v2, 7) + rotl(v3, 12) + rotl(v4, 18);
    h64 = mergeRound(h64, v1);
    h64 = mergeRound(h64, v2);
    h64 = mergeRound(h64, v3);
    h64 = mergeRound(h64, v4);
  } else {
    h64 = seed + PRIME5;
  }

  h64 += static_cast<std::uint64_t>(data.size());

  while (p + 8 <= end) {
    std::uint64_t k1 = read64(p);
    k1 *= PRIME2;
    k1 = rotl(k1, 31);
    k1 *= PRIME1;
    h64 ^= k1;
    h64 = rotl(h64, 27) * PRIME1 + PRIME4;
    p += 8;
  }

  if (p + 4 <= end) {
    h64 ^= static_cast<std::uint64_t>(read32(p)) * PRIME1;
    h64 = rotl(h64, 23) * PRIME2 + PRIME3;
    p += 4;
  }

  while (p < end) {
    h64 ^= static_cast<std::uint64_t>(*p) * PRIME5;
    h64 = rotl(h64, 11) * PRIME1;
    ++p;
  }

  // Avalanche.
  h64 ^= h64 >> 33;
  h64 *= PRIME2;
  h64 ^= h64 >> 29;
  h64 *= PRIME3;
  h64 ^= h64 >> 32;
  return h64;
}

// Decision 0017: Path shortening uses short_name first, then stable hashing if
// still too long. This helper implements the shortening for *instance paths*
// (the portion before ':' in Decision 0023 canonical paths).
struct InstancePathShorteningPolicy {
  std::size_t max_segments = 16;
  std::size_t max_chars = 240;
  std::size_t keep_head = 2;
  std::size_t keep_tail = 2;
};

inline std::string shortenInstancePath(std::string_view full_path,
                                       InstancePathShorteningPolicy policy = InstancePathShorteningPolicy{}) {
  // Defensive: if a caller accidentally passes a canonical_path, only shorten
  // the instance_path prefix.
  if (auto colon = full_path.find(':'); colon != std::string_view::npos) {
    std::string out = shortenInstancePath(full_path.substr(0, colon), policy);
    out.append(full_path.substr(colon));
    return out;
  }

  auto split = [](std::string_view s) -> std::vector<std::string_view> {
    std::vector<std::string_view> segs;
    std::size_t i = 0;
    while (i < s.size()) {
      std::size_t j = i;
      while (j < s.size() && s[j] != '.')
        ++j;
      if (j != i)
        segs.push_back(s.substr(i, j - i));
      i = j + 1;
    }
    return segs;
  };

  const auto segs = split(full_path);
  if (segs.size() <= policy.max_segments && full_path.size() <= policy.max_chars)
    return std::string(full_path);

  auto hex64 = [](std::uint64_t v) -> std::string {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.resize(16);
    for (int i = 15; i >= 0; --i) {
      out[15 - i] = kHex[(v >> (i * 4)) & 0xFULL];
    }
    return out;
  };

  const std::string hashSeg = std::string("_h") + hex64(xxhash64(full_path, /*seed=*/0));

  auto join = [&](std::size_t head, std::size_t tail) -> std::string {
    // Build: <head>.<hashSeg>.<tail>, dropping overlaps if head+tail > n.
    const std::size_t n = segs.size();
    if (n == 0)
      return std::string(full_path);
    if (head > n)
      head = n;
    if (tail > n - head)
      tail = n - head;

    std::size_t approx = hashSeg.size();
    for (std::size_t i = 0; i < head; ++i)
      approx += segs[i].size() + 1;
    for (std::size_t i = 0; i < tail; ++i)
      approx += segs[n - tail + i].size() + 1;

    std::string out;
    out.reserve(approx);
    bool first = true;
    auto appendSeg = [&](std::string_view seg) {
      if (!first)
        out.push_back('.');
      out.append(seg.data(), seg.size());
      first = false;
    };
    for (std::size_t i = 0; i < head; ++i)
      appendSeg(segs[i]);
    appendSeg(hashSeg);
    for (std::size_t i = 0; i < tail; ++i)
      appendSeg(segs[n - tail + i]);
    return out;
  };

  // Prefer readability: preserve head/tail segments, but keep within both
  // thresholds. The output segment count is <= head+1+tail.
  const std::size_t n = segs.size();
  const std::size_t maxOutSegs = std::max<std::size_t>(1, policy.max_segments);

  struct Cand {
    std::size_t head;
    std::size_t tail;
  };

  const std::size_t wantHead = std::max<std::size_t>(1, policy.keep_head);
  const std::size_t wantTail = std::max<std::size_t>(1, policy.keep_tail);

  const Cand cands[] = {
      {std::min(wantHead, n), std::min(wantTail, n)},
      {2, 2},
      {2, 1},
      {1, 2},
      {1, 1},
      {1, 0},
      {0, 1},
      {0, 0},
  };

  for (const auto &c : cands) {
    // For n==1, keep it (even if too long).
    if (n <= 1)
      return std::string(full_path);

    // Ensure at least one user segment is preserved when possible (keep root).
    std::size_t head = std::min<std::size_t>(c.head, n);
    std::size_t tail = std::min<std::size_t>(c.tail, n);
    if (head == 0 && n >= 1)
      head = 1; // keep root segment (usually "dut")

    // Ensure we don't exceed the segment budget.
    while (head + 1 + tail > maxOutSegs) {
      if (tail > 0)
        --tail;
      else if (head > 1)
        --head;
      else
        break;
    }

    std::string out = join(head, tail);
    if (out.size() <= policy.max_chars && split(out).size() <= policy.max_segments)
      return out;
  }

  // Worst-case fallback: root + hash.
  if (!segs.empty()) {
    std::string out;
    out.reserve(segs.front().size() + 1 + hashSeg.size());
    out.append(segs.front().data(), segs.front().size());
    out.push_back('.');
    out.append(hashSeg);
    return out;
  }
  return hashSeg;
}

enum class ProbeKind : std::uint8_t {
  Wire = 0,
  Reg = 1,
  Mem = 2,
  StateVar = 3,
};

// Decision 0004 / 0018-0021: Centralized probe registry with:
// - lookup by canonical path
// - wildcard/glob match
// - kind/type filtering
// - stable `probe_id = xxHash64(canonical_path)` (seed=0)
// Decision 0022: detect collisions and rehash using `path + "#<n>"`.
class ProbeRegistry {
public:
  struct Entry {
    std::string path{};
    std::uint64_t probe_id = 0;
    ProbeKind kind = ProbeKind::Wire;
    std::uint32_t width_bits = 0;
    void *ptr = nullptr;

    // Optional write-intent metadata (Decisions 0053-0055).
    bool *write_valid = nullptr;
    const void *write_data_ptr = nullptr;
    std::uint32_t write_width_bits = 0;
    const std::size_t *write_addr = nullptr;
    const void *write_mask_ptr = nullptr;
    std::uint32_t write_mask_width_bits = 0;

    const void *known_mask_ptr = nullptr;
    std::uint32_t known_mask_width_bits = 0;
    const void *z_mask_ptr = nullptr;
    std::uint32_t z_mask_width_bits = 0;
  };

  static constexpr std::uint64_t kProbeIdSeed = 0;

  static std::uint64_t hash64ForPath(std::string_view path) { return xxhash64(path, kProbeIdSeed); }

  void clear() {
    entries_.clear();
    by_id_.clear();
  }

  std::size_t size() const { return entries_.size(); }
  bool empty() const { return entries_.empty(); }

  template <unsigned W>
  std::uint64_t addWire(std::string path, Wire<W> *wire, ProbeKind kind = ProbeKind::Wire) {
    return addImpl(std::move(path), kind, /*width_bits=*/W, static_cast<void *>(wire));
  }

  // Register a scalar wire leaf (terminal case of the vector overload below).
  template <unsigned W>
  std::uint64_t addVec(std::string path, Wire<W> *wire, ProbeKind kind = ProbeKind::Wire) {
    return addWire<W>(std::move(path), wire, kind);
  }

  // Register every leaf lane of a (possibly nested) vector under indexed paths
  // (e.g. "a[0][1]"). The second template parameter constrains this to Vec-like
  // types (those exposing a static size()) so scalar Wire<W>* dispatches to the
  // leaf overload above. This stays a duck-typed member so the registry header
  // need not depend on (and include) the Vec header. Leaf paths/IDs match
  // per-element addWire() registration.
  template <typename VecT, typename = decltype(VecT::size())>
  void addVec(std::string path, VecT *vec, ProbeKind kind = ProbeKind::Wire) {
    for (std::size_t i = 0; i < VecT::size(); ++i)
      addVec(path + "[" + std::to_string(i) + "]", &(*vec)[i], kind);
  }

  template <unsigned W>
  std::uint64_t addReg(std::string path, Wire<W> *q, bool *write_valid, Wire<W> *write_data) {
    return addImpl(std::move(path),
                   ProbeKind::Reg,
                   /*width_bits=*/W,
                   static_cast<void *>(q),
                   write_valid,
                   static_cast<const void *>(write_data),
                   /*write_width_bits=*/W);
  }

  template <typename MemT>
  std::uint64_t addMem(std::string path, MemT *mem) {
    return addImpl(std::move(path), ProbeKind::Mem, /*width_bits=*/0, static_cast<void *>(mem));
  }

  template <typename MemT, unsigned DataW, unsigned MaskW>
  std::uint64_t addMem(std::string path,
                       MemT *mem,
                       bool *write_valid,
                       const std::size_t *write_addr,
                       Wire<DataW> *write_data,
                       Wire<MaskW> *write_mask) {
    return addImpl(std::move(path),
                   ProbeKind::Mem,
                   /*width_bits=*/0,
                   static_cast<void *>(mem),
                   write_valid,
                   static_cast<const void *>(write_data),
                   /*write_width_bits=*/DataW,
                   write_addr,
                   static_cast<const void *>(write_mask),
                   /*write_mask_width_bits=*/MaskW);
  }

  std::uint64_t addAlias(std::string path, const Entry &src) {
    return addImpl(std::move(path),
                   src.kind,
                   src.width_bits,
                   src.ptr,
                   src.write_valid,
                   src.write_data_ptr,
                   src.write_width_bits,
                   src.write_addr,
                   src.write_mask_ptr,
                   src.write_mask_width_bits,
                   src.known_mask_ptr,
                   src.known_mask_width_bits,
                   src.z_mask_ptr,
                   src.z_mask_width_bits);
  }

  const Entry *findByPath(std::string_view path) const {
    // Decision 0022: if a path's base hash collides, its id is rehashed with a
    // numeric suffix (path + "#<n>"). To find an entry by path, walk the same
    // candidate sequence until we either find the exact path or reach a free id
    // (meaning the path was never registered).
    std::uint64_t id = hash64ForPath(path);
    for (std::uint32_t suffix = 0;; ++suffix) {
      auto it = by_id_.find(id);
      if (it == by_id_.end())
        return nullptr;
      const auto &e = entries_[it->second];
      if (e.path == path)
        return &e;
      id = hash64ForPathWithSuffix(path, suffix + 1);
    }
  }

  const Entry *findById(std::uint64_t probe_id) const {
    auto it = by_id_.find(probe_id);
    if (it == by_id_.end())
      return nullptr;
    return &entries_[it->second];
  }

  std::vector<const Entry *> findByKind(ProbeKind kind) const {
    std::vector<const Entry *> out;
    for (const auto &e : entries_) {
      if (e.kind == kind)
        out.push_back(&e);
    }
    return out;
  }

  std::vector<const Entry *> findByGlob(std::string_view pat) const {
    std::vector<const Entry *> out;
    const auto patSegs = splitSegments(pat);
    for (const auto &e : entries_) {
      const auto pathSegs = splitSegments(e.path);
      if (matchHierGlob(patSegs, pathSegs))
        out.push_back(&e);
    }
    return out;
  }

  // Like findByGlob(), but filters by kind (Decision 0004).
  std::vector<const Entry *> findByGlobAndKind(std::string_view pat, ProbeKind kind) const {
    std::vector<const Entry *> out;
    const auto patSegs = splitSegments(pat);
    for (const auto &e : entries_) {
      if (e.kind != kind)
        continue;
      const auto pathSegs = splitSegments(e.path);
      if (matchHierGlob(patSegs, pathSegs))
        out.push_back(&e);
    }
    return out;
  }

private:
  static std::uint64_t hash64ForPathWithSuffix(std::string_view path, std::uint32_t suffix) {
    std::string tmp;
    tmp.reserve(path.size() + 1 + 10);
    tmp.append(path.data(), path.size());
    tmp.push_back('#');
    tmp.append(std::to_string(static_cast<unsigned>(suffix)));
    return hash64ForPath(tmp);
  }

  std::uint64_t addImpl(std::string path,
                        ProbeKind kind,
                        std::uint32_t width_bits,
                        void *ptr,
                        bool *write_valid = nullptr,
                        const void *write_data_ptr = nullptr,
                        std::uint32_t write_width_bits = 0,
                        const std::size_t *write_addr = nullptr,
                        const void *write_mask_ptr = nullptr,
                        std::uint32_t write_mask_width_bits = 0,
                        const void *known_mask_ptr = nullptr,
                        std::uint32_t known_mask_width_bits = 0,
                        const void *z_mask_ptr = nullptr,
                        std::uint32_t z_mask_width_bits = 0) {
    if (const Entry *existing = findByPath(path))
      return existing->probe_id;

    const std::uint64_t base = hash64ForPath(path);
    std::uint64_t id = base;

    if (auto it = by_id_.find(id); it != by_id_.end()) {
      const auto &other = entries_[it->second];
      std::uint32_t suffix = 1;
      do {
        id = hash64ForPathWithSuffix(path, suffix);
        ++suffix;
      } while (by_id_.find(id) != by_id_.end());
      std::fprintf(stderr,
                   "[pyc] ProbeRegistry hash collision: base=0x%016llx path='%s' collides with '%s'; "
                   "resolved=0x%016llx\n",
                   static_cast<unsigned long long>(base),
                   path.c_str(),
                   other.path.c_str(),
                   static_cast<unsigned long long>(id));
    }

    const std::size_t idx = entries_.size();
    entries_.push_back(Entry{std::move(path), id, kind, width_bits, ptr});
    Entry &e = entries_.back();
    e.write_valid = write_valid;
    e.write_data_ptr = write_data_ptr;
    e.write_width_bits = write_width_bits;
    e.write_addr = write_addr;
    e.write_mask_ptr = write_mask_ptr;
    e.write_mask_width_bits = write_mask_width_bits;
    e.known_mask_ptr = known_mask_ptr;
    e.known_mask_width_bits = known_mask_width_bits;
    e.z_mask_ptr = z_mask_ptr;
    e.z_mask_width_bits = z_mask_width_bits;
    by_id_.emplace(id, idx);
    return id;
  }

  static bool matchSegmentGlob(std::string_view pat, std::string_view text) {
    // `*` matches any substring, `?` matches any single char.
    std::size_t pi = 0;
    std::size_t ti = 0;
    std::size_t star = std::string_view::npos;
    std::size_t match = 0;

    while (ti < text.size()) {
      if (pi < pat.size() && (pat[pi] == '?' || pat[pi] == text[ti])) {
        ++pi;
        ++ti;
        continue;
      }
      if (pi < pat.size() && pat[pi] == '*') {
        star = pi++;
        match = ti;
        continue;
      }
      if (star != std::string_view::npos) {
        pi = star + 1;
        ti = ++match;
        continue;
      }
      return false;
    }

    while (pi < pat.size() && pat[pi] == '*')
      ++pi;
    return pi == pat.size();
  }

  static std::vector<std::string_view> splitSegments(std::string_view s) {
    std::vector<std::string_view> segs;
    std::size_t i = 0;
    while (i < s.size()) {
      std::size_t j = i;
      while (j < s.size() && s[j] != '.' && s[j] != ':')
        ++j;
      if (j != i)
        segs.push_back(s.substr(i, j - i));
      i = j + 1;
    }
    return segs;
  }

  static bool matchHierGlob(const std::vector<std::string_view> &patSegs,
                            const std::vector<std::string_view> &pathSegs,
                            std::size_t pi = 0,
                            std::size_t xi = 0) {
    if (pi == patSegs.size())
      return xi == pathSegs.size();
    if (patSegs[pi] == "**") {
      if (matchHierGlob(patSegs, pathSegs, pi + 1, xi))
        return true;
      return (xi < pathSegs.size()) && matchHierGlob(patSegs, pathSegs, pi, xi + 1);
    }
    if (xi >= pathSegs.size())
      return false;
    if (!matchSegmentGlob(patSegs[pi], pathSegs[xi]))
      return false;
    return matchHierGlob(patSegs, pathSegs, pi + 1, xi + 1);
  }

  std::vector<Entry> entries_{};
  std::unordered_map<std::uint64_t, std::size_t> by_id_{};
};

} // namespace pyc::cpp
