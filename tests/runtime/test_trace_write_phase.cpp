// Write-event observation points: a Write whose data is the state storage
// itself is only final post-transfer, so it must be emitted inside the commit
// phase block; edge-captured write data (memory ports, delay heads, per-object
// two-phase registers) stays in the tick block. Both kinds are registered here
// and must land in exactly one block per cycle, carrying the right probe id.
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>

#include "pyc_bits.hpp"
#include "pyc_primitives.hpp"
#include "pyc_probe_registry.hpp"
#include "pyc_trace_bin.hpp"

using namespace pyc::cpp;

namespace {

constexpr std::uint32_t kChunkCycleBegin = 2;
constexpr std::uint32_t kChunkWrite = 7;

std::uint32_t u32le(const std::vector<std::uint8_t> &d, std::size_t o) {
  return static_cast<std::uint32_t>(d[o]) |
         (static_cast<std::uint32_t>(d[o + 1]) << 8) |
         (static_cast<std::uint32_t>(d[o + 2]) << 16) |
         (static_cast<std::uint32_t>(d[o + 3]) << 24);
}

std::uint64_t u64le(const std::vector<std::uint8_t> &d, std::size_t o) {
  std::uint64_t v = 0;
  for (unsigned i = 0; i < 8; ++i)
    v |= static_cast<std::uint64_t>(d[o + i]) << (8 * i);
  return v;
}

struct Written {
  std::uint8_t phase = 0;
  std::uint64_t probe_id = 0;
};

// Minimal chunk reader: the only fields this test needs are the phase carried
// by each CycleBegin and the probe id at the head of each Write payload.
std::vector<Written> readWrites(const std::filesystem::path &path) {
  std::ifstream in(path, std::ios::binary);
  const std::vector<std::uint8_t> d((std::istreambuf_iterator<char>(in)),
                                    std::istreambuf_iterator<char>());
  std::size_t off = 8 + 4 + 4;  // magic + schema_version + flags
  std::uint8_t phase = 0;
  std::vector<Written> out;
  while (off + 8 <= d.size()) {
    const std::uint32_t len = u32le(d, off);
    const std::uint32_t ty = u32le(d, off + 4);
    off += 8;
    if (off + len > d.size())
      break;
    if (ty == kChunkCycleBegin && len >= 9)
      phase = d[off + 8];
    else if (ty == kChunkWrite && len >= 8)
      out.push_back({phase, u64le(d, off)});
    off += len;
  }
  return out;
}

}  // namespace

int main() {
  const std::filesystem::path trace_path = "trace_write_phase.pyctrace";
  constexpr std::uint64_t kCycles = 3;

  Wire<8> q_post{0x11}, q_next_pre{0x22}, q_pre{0x33};
  bool pend_post = true, pend_pre = true;
  ProbeRegistry reg;
  const std::uint64_t post_id = reg.addReg<8>(
      "post", &q_post, &pend_post, &q_post, /*write_post_transfer=*/true);
  const std::uint64_t pre_id = reg.addReg<8>(
      "pre", &q_pre, &pend_pre, &q_next_pre, /*write_post_transfer=*/false);
  const auto *post = reg.findByPath("post");
  const auto *pre = reg.findByPath("pre");
  if (!post || !pre) {
    std::fprintf(stderr, "probe registration failed\n");
    return 1;
  }

  {
    PycTraceBinWriter writer;
    if (!writer.open(trace_path, {post, pre})) {
      std::fprintf(stderr, "cannot open trace file\n");
      return 1;
    }
    for (std::uint64_t c = 0; c < kCycles; ++c) {
      writer.writeCombPhase(c);
      writer.writeTickPhase(c);
      writer.writeCommitPhase(c);
    }
    writer.close();
  }

  const std::vector<Written> writes = readWrites(trace_path);
  std::filesystem::remove(trace_path);
  if (writes.size() != 2 * kCycles) {
    std::fprintf(stderr, "expected %llu write events, got %zu\n",
                 static_cast<unsigned long long>(2 * kCycles), writes.size());
    return 1;
  }
  const auto tick = static_cast<std::uint8_t>(PycTraceBinWriter::Phase::Tick);
  const auto commit =
      static_cast<std::uint8_t>(PycTraceBinWriter::Phase::Commit);
  for (std::size_t i = 0; i < writes.size(); i += 2) {
    const Written &a = writes[i];
    const Written &b = writes[i + 1];
    // Edge-captured write data first (tick block), storage-aliased second.
    if (a.phase != tick || a.probe_id != pre_id || b.phase != commit ||
        b.probe_id != post_id) {
      std::fprintf(stderr,
                   "cycle %zu: write events out of place "
                   "(a: phase=%u id=%llu, b: phase=%u id=%llu)\n",
                   i / 2, a.phase, static_cast<unsigned long long>(a.probe_id),
                   b.phase, static_cast<unsigned long long>(b.probe_id));
      return 1;
    }
  }

  std::printf("trace write phases verified: %llu cycles, tick=%llu commit=%llu\n",
              static_cast<unsigned long long>(kCycles),
              static_cast<unsigned long long>(kCycles),
              static_cast<unsigned long long>(kCycles));
  return 0;
}
