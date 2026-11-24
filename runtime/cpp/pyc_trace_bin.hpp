#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include "pyc_probe_registry.hpp"

namespace pyc::cpp {

class PycTraceBinWriter {
public:
  enum class SampleAt : std::uint8_t {
    Auto = 0,
    Comb = 1,
    Tick = 2,
    Commit = 3,
  };

  enum class Phase : std::uint8_t {
    Comb = 0,
    Tick = 1,
    Commit = 2,
  };

  enum class LogLevel : std::uint8_t {
    Trace = 0,
    Debug = 1,
    Info = 2,
    Warn = 3,
    Error = 4,
    Fatal = 5,
  };

  enum class ResetKind : std::uint8_t {
    Warm = 1,
    Flush = 2,
  };

  enum class InvalidateReason : std::uint8_t {
    WarmReset = 1,
    FlushReset = 2,
    Other = 255,
  };

  PycTraceBinWriter() = default;
  PycTraceBinWriter(const PycTraceBinWriter &) = delete;
  PycTraceBinWriter &operator=(const PycTraceBinWriter &) = delete;
  PycTraceBinWriter(PycTraceBinWriter &&) = default;
  PycTraceBinWriter &operator=(PycTraceBinWriter &&) = default;

  ~PycTraceBinWriter() { close(); }

  bool isOpen() const { return out_.is_open(); }

  bool open(const std::filesystem::path &path,
            std::vector<const ProbeRegistry::Entry *> probes,
            bool external_manifest = false,
            std::vector<SampleAt> sample_at = {}) {
    close();
    out_.open(path, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!out_.is_open())
      return false;

    probes_.clear();
    probes_.reserve(probes.size());
    std::size_t idx = 0;
    for (const auto *e : probes) {
      if (!e)
        continue;
      Traced t;
      t.probe_id = e->probe_id;
      t.kind = e->kind;
      t.width_bits = e->width_bits;
      t.path = e->path;
      t.ptr = e->ptr;
      t.write_valid = e->write_valid;
      t.write_data_ptr = e->write_data_ptr;
      t.write_width_bits = e->write_width_bits;
      t.write_storage_width_bits = e->write_storage_width_bits;
      t.write_lsb_bits = e->write_lsb_bits;
      t.write_addr = e->write_addr;
      t.write_mask_ptr = e->write_mask_ptr;
      t.write_mask_width_bits = e->write_mask_width_bits;
      t.known_mask_ptr = e->known_mask_ptr;
      t.known_mask_width_bits = e->known_mask_width_bits;
      t.z_mask_ptr = e->z_mask_ptr;
      t.z_mask_width_bits = e->z_mask_width_bits;
      if (idx < sample_at.size())
        t.sample_at = sample_at[idx];
      t.byte_count = bytesForWidth(t.width_bits);
      t.word_count = wordsForWidth(t.width_bits);
      t.last_value_bytes.resize(t.byte_count, 0);
      t.last_known_mask_bytes.resize(t.byte_count, 0);
      t.last_z_mask_bytes.resize(t.byte_count, 0);
      t.has_last = false;
      probes_.push_back(std::move(t));
      ++idx;
    }

    writeHeader(external_manifest);
    out_.flush();
    return out_.good();
  }

  void close() {
    if (out_.is_open()) {
      out_.flush();
      out_.close();
    }
    probes_.clear();
    reset_state_.clear();
    pre_phase_order_.clear();
  }

  void writeCombPhase(std::uint64_t cycle) { writePhase(cycle, Phase::Comb); }
  void writeTickPhase(std::uint64_t cycle) { writePhase(cycle, Phase::Tick); }
  void writeCommitPhase(std::uint64_t cycle) { writePhase(cycle, Phase::Commit); }

  void writeLog(LogLevel level, std::string_view message) {
    if (!out_.is_open())
      return;
    std::vector<std::uint8_t> payload;
    payload.reserve(1 + 4 + message.size());
    payload.push_back(static_cast<std::uint8_t>(level));
    appendU32LE(payload, static_cast<std::uint32_t>(message.size()));
    payload.insert(payload.end(), message.begin(), message.end());
    writeChunk(TraceChunkType::Log, payload);
  }

  void writeAssert(std::string_view message, bool fatal = true) {
    if (!out_.is_open())
      return;
    std::vector<std::uint8_t> payload;
    payload.reserve(1 + 4 + message.size());
    payload.push_back(static_cast<std::uint8_t>(fatal ? 1u : 0u));
    appendU32LE(payload, static_cast<std::uint32_t>(message.size()));
    payload.insert(payload.end(), message.begin(), message.end());
    writeChunk(TraceChunkType::Assert, payload);
  }

  bool writeInvalidate(std::uint64_t cycle,
                       std::string_view domain,
                       InvalidateReason reason,
                       std::string_view scope = {},
                       std::string_view reason_text = {}) {
    return writeInvalidateInternal(cycle, std::nullopt, domain, reason, scope, reason_text);
  }

  bool writeInvalidate(std::uint64_t cycle,
                       Phase phase,
                       std::string_view domain,
                       InvalidateReason reason,
                       std::string_view scope = {},
                       std::string_view reason_text = {}) {
    return writeInvalidateInternal(cycle, std::optional<Phase>(phase), domain, reason, scope, reason_text);
  }

  bool writeResetAssert(std::uint64_t cycle, std::string_view domain, ResetKind kind) {
    return writeResetInternal(cycle, std::nullopt, domain, kind, ResetEdge::Assert);
  }

  bool writeResetAssert(std::uint64_t cycle, Phase phase, std::string_view domain, ResetKind kind) {
    return writeResetInternal(cycle, std::optional<Phase>(phase), domain, kind, ResetEdge::Assert);
  }

  bool writeResetDeassert(std::uint64_t cycle, std::string_view domain, ResetKind kind) {
    return writeResetInternal(cycle, std::nullopt, domain, kind, ResetEdge::Deassert);
  }

  bool writeResetDeassert(std::uint64_t cycle, Phase phase, std::string_view domain, ResetKind kind) {
    return writeResetInternal(cycle, std::optional<Phase>(phase), domain, kind, ResetEdge::Deassert);
  }

private:
  enum class TraceChunkType : std::uint32_t {
    ProbeDeclare = 1,
    CycleBegin = 2,
    CycleEnd = 3,
    ValueChange = 4,
    Log = 5,
    Assert = 6,
    Write = 7,
    Reset = 8,
    Invalidate = 9,
  };

  enum class TraceProbeKind : std::uint8_t {
    Comb = 0,
    State = 1,
  };

  enum class TraceProbeSubkind : std::uint8_t {
    None = 0,
    Wire = 1,
    Reg = 2,
    Mem = 3,
    StateVar = 4,
  };

  enum class ResetEdge : std::uint8_t {
    Assert = 1,
    Deassert = 2,
  };

  struct ResetState {
    bool active = false;
    ResetKind kind = ResetKind::Warm;
  };

  struct Traced {
    std::string path{};
    std::uint64_t probe_id = 0;
    ProbeKind kind = ProbeKind::Wire;
    std::uint32_t width_bits = 0;
    void *ptr = nullptr;
    SampleAt sample_at = SampleAt::Auto;
    bool *write_valid = nullptr;
    const void *write_data_ptr = nullptr;
    std::uint32_t write_width_bits = 0;
    std::uint32_t write_storage_width_bits = 0;
    std::uint32_t write_lsb_bits = 0;
    const std::size_t *write_addr = nullptr;
    const void *write_mask_ptr = nullptr;
    std::uint32_t write_mask_width_bits = 0;
    const void *known_mask_ptr = nullptr;
    std::uint32_t known_mask_width_bits = 0;
    const void *z_mask_ptr = nullptr;
    std::uint32_t z_mask_width_bits = 0;
    std::uint32_t byte_count = 0;
    std::uint32_t word_count = 0;
    std::vector<std::uint8_t> last_value_bytes{};
    std::vector<std::uint8_t> last_known_mask_bytes{};
    std::vector<std::uint8_t> last_z_mask_bytes{};
    bool has_last = false;
  };

  static std::uint32_t wordsForWidth(std::uint32_t width_bits) {
    if (width_bits == 0)
      return 0;
    return (width_bits + 63u) / 64u;
  }

  static std::uint32_t bytesForWidth(std::uint32_t width_bits) {
    if (width_bits == 0)
      return 0;
    return (width_bits + 7u) / 8u;
  }

  static TraceProbeSubkind subkindForProbeKind(ProbeKind k) {
    switch (k) {
    case ProbeKind::Wire:
      return TraceProbeSubkind::Wire;
    case ProbeKind::Reg:
      return TraceProbeSubkind::Reg;
    case ProbeKind::Mem:
      return TraceProbeSubkind::Mem;
    case ProbeKind::StateVar:
      return TraceProbeSubkind::StateVar;
    }
    return TraceProbeSubkind::None;
  }

  static Phase phaseForSampleAt(SampleAt at, ProbeKind k) {
    switch (at) {
    case SampleAt::Auto:
      return (k == ProbeKind::Wire) ? Phase::Comb : Phase::Commit;
    case SampleAt::Comb:
      return Phase::Comb;
    case SampleAt::Tick:
      return Phase::Tick;
    case SampleAt::Commit:
      return Phase::Commit;
    }
    return (k == ProbeKind::Wire) ? Phase::Comb : Phase::Commit;
  }

  static std::string toString(ResetKind kind) {
    switch (kind) {
    case ResetKind::Warm:
      return "warm";
    case ResetKind::Flush:
      return "flush";
    }
    return "warm";
  }

  static std::string toString(InvalidateReason reason) {
    switch (reason) {
    case InvalidateReason::WarmReset:
      return "warm_reset";
    case InvalidateReason::FlushReset:
      return "flush_reset";
    case InvalidateReason::Other:
      return "other";
    }
    return "other";
  }

  static void applyTopBitsMask(std::vector<std::uint8_t> &bytes, std::uint32_t width_bits) {
    if (bytes.empty() || width_bits == 0)
      return;
    const std::uint32_t used_in_last = width_bits & 7u;
    if (used_in_last == 0)
      return;
    const std::uint8_t keep = static_cast<std::uint8_t>((1u << used_in_last) - 1u);
    bytes.back() &= keep;
  }

  static std::vector<std::uint8_t> onesMask(std::uint32_t width_bits) {
    std::vector<std::uint8_t> out(bytesForWidth(width_bits), 0xffu);
    applyTopBitsMask(out, width_bits);
    return out;
  }

  static std::vector<std::uint8_t> zerosMask(std::uint32_t width_bits) {
    return std::vector<std::uint8_t>(bytesForWidth(width_bits), 0x00u);
  }

  std::vector<std::uint8_t> readValueBytes(const void *ptr, std::uint32_t width_bits) {
    const std::uint32_t byte_count = bytesForWidth(width_bits);
    const std::uint32_t word_count = wordsForWidth(width_bits);
    if (!ptr || byte_count == 0 || word_count == 0)
      return {};

    std::vector<std::uint64_t> words(word_count, 0);
    std::memcpy(words.data(), ptr, static_cast<std::size_t>(word_count) * sizeof(std::uint64_t));
    std::vector<std::uint8_t> out(byte_count, 0);
    for (std::uint32_t i = 0; i < byte_count; ++i) {
      const std::uint32_t wi = i / 8u;
      const std::uint32_t bi = i % 8u;
      const std::uint64_t w = (wi < word_count) ? words[wi] : 0;
      out[i] = static_cast<std::uint8_t>((w >> (8u * bi)) & 0xffu);
    }
    applyTopBitsMask(out, width_bits);
    return out;
  }

  std::vector<std::uint8_t> readValueSliceBytes(
      const void *ptr, std::uint32_t storage_width_bits,
      std::uint32_t lsb_bits, std::uint32_t width_bits) {
    if (lsb_bits == 0 && storage_width_bits == width_bits)
      return readValueBytes(ptr, width_bits);
    if (!ptr || width_bits == 0 || storage_width_bits == 0 ||
        lsb_bits + width_bits > storage_width_bits)
      return {};

    const std::vector<std::uint8_t> storage =
        readValueBytes(ptr, storage_width_bits);
    std::vector<std::uint8_t> out(bytesForWidth(width_bits), 0);
    for (std::uint32_t bit = 0; bit < width_bits; ++bit) {
      const std::uint32_t source_bit = lsb_bits + bit;
      if ((storage[source_bit / 8u] >> (source_bit % 8u)) & 1u)
        out[bit / 8u] |= static_cast<std::uint8_t>(1u << (bit % 8u));
    }
    applyTopBitsMask(out, width_bits);
    return out;
  }

  std::vector<std::uint8_t> readMaskBytes(const void *ptr,
                                          std::uint32_t mask_width_bits,
                                          std::uint32_t value_width_bits,
                                          bool default_all_ones) {
    if (!ptr) {
      return default_all_ones ? onesMask(value_width_bits) : zerosMask(value_width_bits);
    }
    const std::uint32_t src_width = (mask_width_bits == 0) ? value_width_bits : mask_width_bits;
    std::vector<std::uint8_t> src = readValueBytes(ptr, src_width);
    std::vector<std::uint8_t> out(bytesForWidth(value_width_bits), 0);
    for (std::size_t i = 0; i < out.size() && i < src.size(); ++i) {
      out[i] = src[i];
    }
    if (default_all_ones && src.empty()) {
      out = onesMask(value_width_bits);
    }
    applyTopBitsMask(out, value_width_bits);
    return out;
  }

  void sampleDelta(Traced &t) {
    if (!t.ptr || t.byte_count == 0 || t.word_count == 0)
      return;

    const std::vector<std::uint8_t> cur_value = readValueBytes(t.ptr, t.width_bits);
    const std::vector<std::uint8_t> cur_known = readMaskBytes(t.known_mask_ptr, t.known_mask_width_bits, t.width_bits, true);
    const std::vector<std::uint8_t> cur_z = readMaskBytes(t.z_mask_ptr, t.z_mask_width_bits, t.width_bits, false);

    const bool changed =
        (!t.has_last) ||
        (cur_value != t.last_value_bytes) ||
        (cur_known != t.last_known_mask_bytes) ||
        (cur_z != t.last_z_mask_bytes);
    if (!changed)
      return;

    t.last_value_bytes = cur_value;
    t.last_known_mask_bytes = cur_known;
    t.last_z_mask_bytes = cur_z;
    t.has_last = true;
    writeValueChange(t.probe_id, t.width_bits, t.last_value_bytes, t.last_known_mask_bytes, t.last_z_mask_bytes);
  }

  void writeWriteEvent(const Traced &t) {
    if (!t.write_valid || !(*t.write_valid))
      return;
    if (!t.write_data_ptr || t.write_width_bits == 0)
      return;

    const TraceProbeSubkind subkind = subkindForProbeKind(t.kind);
    if (subkind == TraceProbeSubkind::None || subkind == TraceProbeSubkind::Wire)
      return;

    std::uint8_t flags = 0;
    if (t.write_addr)
      flags |= 1u << 0u;
    if (t.write_mask_ptr && t.write_mask_width_bits)
      flags |= 1u << 1u;

    const std::uint32_t storage_width = t.write_storage_width_bits == 0
                                            ? t.write_width_bits
                                            : t.write_storage_width_bits;
    std::vector<std::uint8_t> data = readValueSliceBytes(
        t.write_data_ptr, storage_width, t.write_lsb_bits,
        t.write_width_bits);
    std::vector<std::uint8_t> mask;
    if (flags & (1u << 1u))
      mask = readValueBytes(t.write_mask_ptr, t.write_mask_width_bits);

    std::vector<std::uint8_t> payload;
    payload.reserve(8 + 1 + 1 + 8 + 4 + data.size() + 4 + mask.size());
    appendU64LE(payload, t.probe_id);
    payload.push_back(static_cast<std::uint8_t>(subkind));
    payload.push_back(flags);
    if (flags & (1u << 0u)) {
      const std::uint64_t addr = static_cast<std::uint64_t>(*t.write_addr);
      appendU64LE(payload, addr);
    }
    appendU32LE(payload, t.write_width_bits);
    payload.insert(payload.end(), data.begin(), data.end());
    if (flags & (1u << 1u)) {
      appendU32LE(payload, t.write_mask_width_bits);
      payload.insert(payload.end(), mask.begin(), mask.end());
    }
    writeChunk(TraceChunkType::Write, payload);
  }

  void writePhase(std::uint64_t cycle, Phase phase) {
    if (!out_.is_open())
      return;
    writeCycleBoundary(TraceChunkType::CycleBegin, cycle, phase);
    if (phase == Phase::Tick) {
      for (const auto &t : probes_)
        writeWriteEvent(t);
    }
    for (auto &t : probes_) {
      if (phaseForSampleAt(t.sample_at, t.kind) != phase)
        continue;
      sampleDelta(t);
    }
    writeCycleBoundary(TraceChunkType::CycleEnd, cycle, phase);
    if (phase == Phase::Commit)
      pre_phase_order_.erase(cycle);
  }

  void writeHeader(bool external_manifest) {
    static constexpr char kMagic[8] = {'P', 'Y', 'C', '4', 'T', 'R', 'C', '3'};
    out_.write(kMagic, sizeof(kMagic));
    writeU32LE(3);

    std::uint32_t flags = 0;
    flags |= 1u << 0u;
    if (external_manifest)
      flags |= 1u << 1u;
    writeU32LE(flags);

    if (external_manifest)
      return;

    for (const auto &t : probes_) {
      const TraceProbeKind kind = (t.kind == ProbeKind::Wire) ? TraceProbeKind::Comb : TraceProbeKind::State;
      const TraceProbeSubkind subkind = subkindForProbeKind(t.kind);

      std::uint8_t type_sig[6];
      type_sig[0] = 0;
      const std::uint32_t w = t.width_bits;
      type_sig[1] = static_cast<std::uint8_t>((w >> 0) & 0xffu);
      type_sig[2] = static_cast<std::uint8_t>((w >> 8) & 0xffu);
      type_sig[3] = static_cast<std::uint8_t>((w >> 16) & 0xffu);
      type_sig[4] = static_cast<std::uint8_t>((w >> 24) & 0xffu);
      type_sig[5] = 0;

      std::vector<std::uint8_t> payload;
      payload.reserve(8 + 1 + 1 + 4 + t.path.size() + 4 + 4 + sizeof(type_sig));
      appendU64LE(payload, t.probe_id);
      payload.push_back(static_cast<std::uint8_t>(kind));
      payload.push_back(static_cast<std::uint8_t>(subkind));
      appendU32LE(payload, static_cast<std::uint32_t>(t.path.size()));
      payload.insert(payload.end(), t.path.begin(), t.path.end());
      appendU32LE(payload, 0);
      appendU32LE(payload, static_cast<std::uint32_t>(sizeof(type_sig)));
      payload.insert(payload.end(), type_sig, type_sig + sizeof(type_sig));
      writeChunk(TraceChunkType::ProbeDeclare, payload);
    }
  }

  static std::uint8_t phaseToByte(std::optional<Phase> phase) {
    return phase.has_value() ? static_cast<std::uint8_t>(*phase) : 0u;
  }

  bool enforcePreOrder(std::uint64_t cycle, std::optional<Phase> phase, std::uint8_t ord) {
    if (!phase.has_value() || *phase != Phase::Tick)
      return true;
    auto it = pre_phase_order_.find(cycle);
    if (it == pre_phase_order_.end()) {
      pre_phase_order_.emplace(cycle, ord);
      return true;
    }
    if (ord < it->second)
      return false;
    it->second = ord;
    return true;
  }

  bool writeInvalidateInternal(std::uint64_t cycle,
                               std::optional<Phase> phase,
                               std::string_view domain,
                               InvalidateReason reason,
                               std::string_view scope,
                               std::string_view reason_text) {
    if (!out_.is_open())
      return false;
    if (!enforcePreOrder(cycle, phase, 0u)) {
      writeLog(LogLevel::Warn, "invalidate ordering violation");
      return false;
    }

    std::vector<std::uint8_t> payload;
    payload.reserve(8 + 1 + 1 + 1 + 4 + domain.size() + 4 + scope.size() + 4 + reason_text.size());
    appendU64LE(payload, cycle);
    payload.push_back(phase.has_value() ? 1u : 0u);
    payload.push_back(phaseToByte(phase));
    payload.push_back(static_cast<std::uint8_t>(reason));
    appendU32LE(payload, static_cast<std::uint32_t>(domain.size()));
    payload.insert(payload.end(), domain.begin(), domain.end());
    appendU32LE(payload, static_cast<std::uint32_t>(scope.size()));
    payload.insert(payload.end(), scope.begin(), scope.end());
    appendU32LE(payload, static_cast<std::uint32_t>(reason_text.size()));
    payload.insert(payload.end(), reason_text.begin(), reason_text.end());
    writeChunk(TraceChunkType::Invalidate, payload);
    return true;
  }

  bool writeResetInternal(std::uint64_t cycle,
                          std::optional<Phase> phase,
                          std::string_view domain,
                          ResetKind kind,
                          ResetEdge edge) {
    if (!out_.is_open())
      return false;

    const std::uint8_t ord = (edge == ResetEdge::Assert) ? 1u : 2u;
    if (!enforcePreOrder(cycle, phase, ord)) {
      writeLog(LogLevel::Warn, "reset ordering violation");
      return false;
    }

    std::string domain_key(domain);
    auto &state = reset_state_[domain_key];
    if (edge == ResetEdge::Assert) {
      if (state.active) {
        writeLog(LogLevel::Warn, "reset assert while active");
        return false;
      }
      state.active = true;
      state.kind = kind;
    } else {
      if (!state.active) {
        writeLog(LogLevel::Warn, "reset deassert without active domain");
        return false;
      }
      if (state.kind != kind) {
        writeLog(LogLevel::Warn, "reset kind mismatch on deassert");
        return false;
      }
      state.active = false;
    }

    std::vector<std::uint8_t> payload;
    payload.reserve(8 + 1 + 1 + 1 + 1 + 4 + domain.size());
    appendU64LE(payload, cycle);
    payload.push_back(phase.has_value() ? 1u : 0u);
    payload.push_back(phaseToByte(phase));
    payload.push_back(static_cast<std::uint8_t>(edge));
    payload.push_back(static_cast<std::uint8_t>(kind));
    appendU32LE(payload, static_cast<std::uint32_t>(domain.size()));
    payload.insert(payload.end(), domain.begin(), domain.end());
    writeChunk(TraceChunkType::Reset, payload);
    return true;
  }

  void writeCycleBoundary(TraceChunkType ty, std::uint64_t cycle, Phase phase) {
    std::vector<std::uint8_t> payload;
    payload.reserve(8 + 1);
    appendU64LE(payload, cycle);
    payload.push_back(static_cast<std::uint8_t>(phase));
    writeChunk(ty, payload);
  }

  void writeValueChange(std::uint64_t probe_id,
                        std::uint32_t width_bits,
                        const std::vector<std::uint8_t> &value_bytes,
                        const std::vector<std::uint8_t> &known_mask_bytes,
                        const std::vector<std::uint8_t> &z_mask_bytes) {
    std::vector<std::uint8_t> payload;
    payload.reserve(8 + 4 + value_bytes.size() + 4 + known_mask_bytes.size() + 4 + z_mask_bytes.size());
    appendU64LE(payload, probe_id);
    appendU32LE(payload, width_bits);
    payload.insert(payload.end(), value_bytes.begin(), value_bytes.end());
    appendU32LE(payload, width_bits);
    payload.insert(payload.end(), known_mask_bytes.begin(), known_mask_bytes.end());
    appendU32LE(payload, width_bits);
    payload.insert(payload.end(), z_mask_bytes.begin(), z_mask_bytes.end());
    writeChunk(TraceChunkType::ValueChange, payload);
  }

  void writeChunk(TraceChunkType ty, const std::vector<std::uint8_t> &payload) {
    writeU32LE(static_cast<std::uint32_t>(payload.size()));
    writeU32LE(static_cast<std::uint32_t>(ty));
    if (!payload.empty())
      out_.write(reinterpret_cast<const char *>(payload.data()), static_cast<std::streamsize>(payload.size()));
  }

  static void appendU32LE(std::vector<std::uint8_t> &dst, std::uint32_t v) {
    dst.push_back(static_cast<std::uint8_t>((v >> 0) & 0xffu));
    dst.push_back(static_cast<std::uint8_t>((v >> 8) & 0xffu));
    dst.push_back(static_cast<std::uint8_t>((v >> 16) & 0xffu));
    dst.push_back(static_cast<std::uint8_t>((v >> 24) & 0xffu));
  }

  static void appendU64LE(std::vector<std::uint8_t> &dst, std::uint64_t v) {
    dst.push_back(static_cast<std::uint8_t>((v >> 0) & 0xffull));
    dst.push_back(static_cast<std::uint8_t>((v >> 8) & 0xffull));
    dst.push_back(static_cast<std::uint8_t>((v >> 16) & 0xffull));
    dst.push_back(static_cast<std::uint8_t>((v >> 24) & 0xffull));
    dst.push_back(static_cast<std::uint8_t>((v >> 32) & 0xffull));
    dst.push_back(static_cast<std::uint8_t>((v >> 40) & 0xffull));
    dst.push_back(static_cast<std::uint8_t>((v >> 48) & 0xffull));
    dst.push_back(static_cast<std::uint8_t>((v >> 56) & 0xffull));
  }

  void writeU32LE(std::uint32_t v) {
    std::uint8_t b[4];
    b[0] = static_cast<std::uint8_t>((v >> 0) & 0xffu);
    b[1] = static_cast<std::uint8_t>((v >> 8) & 0xffu);
    b[2] = static_cast<std::uint8_t>((v >> 16) & 0xffu);
    b[3] = static_cast<std::uint8_t>((v >> 24) & 0xffu);
    out_.write(reinterpret_cast<const char *>(b), sizeof(b));
  }

  std::ofstream out_{};
  std::vector<Traced> probes_{};
  std::unordered_map<std::string, ResetState> reset_state_{};
  std::unordered_map<std::uint64_t, std::uint8_t> pre_phase_order_{};
};

} // namespace pyc::cpp
