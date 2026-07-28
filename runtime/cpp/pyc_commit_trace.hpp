#pragma once

// PyCircuit commit/retire trace collector (TODO B1; Decision 0142).
//
// A first-class, schema-agnostic sensor for the optimization loop: given a set
// of field getters bound to a design's commit/retire ports (declared in the
// frontend via `m.commit_interface({...})` and recorded in the
// `pyc.commit_iface` MLIR attribute), this collector samples one row per
// retiring instruction and serializes a commit-bundle JSONL stream.
//
// The framework fixes no field vocabulary: field names come from the design's
// own schema profile. Any compatible commit-trace differ can consume the output
// (e.g. contrib/linx/flows/tools/linx_trace_diff.py for the LinxCore profile).
//
// Header-only, standard-library only, so it can be dropped into any generated
// C++ testbench without extra dependencies.

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <string>
#include <vector>

namespace pyc::cpp {

class PycCommitTraceWriter {
public:
  using Getter = std::function<std::uint64_t()>;

  // The per-cycle commit strobe field. When bound, a row is captured only on
  // cycles where it reads non-zero (one retiring instruction per row).
  static constexpr const char *kValidField = "valid";

  struct Row {
    std::uint64_t cycle = 0;
    std::map<std::string, std::uint64_t> vals;  // field -> value (excludes strobe)
  };

  PycCommitTraceWriter() = default;

  void setSchema(std::string id) { schema_ = std::move(id); }
  const std::string &schema() const { return schema_; }

  void setStage(std::string stage) { stage_ = std::move(stage); }
  const std::string &stage() const { return stage_; }

  // Bind a commit field name (from the design's own schema vocabulary) to a
  // getter reading the current value of the corresponding design port.
  void bind(const std::string &field, Getter g) { fields_[field] = std::move(g); }

  bool hasField(const std::string &field) const {
    return fields_.find(field) != fields_.end();
  }

  // Capture one row for `cycle` iff the strobe (if bound) is non-zero.
  void sample(std::uint64_t cycle) {
    auto vit = fields_.find(kValidField);
    if (vit != fields_.end() && vit->second && vit->second() == 0)
      return;  // no instruction retired this cycle
    Row r;
    r.cycle = cycle;
    for (const auto &kv : fields_) {
      if (kv.first == kValidField)
        continue;  // strobe gates the row; it is not itself a bundle field
      if (kv.second)
        r.vals[kv.first] = kv.second();
    }
    rows_.push_back(std::move(r));
  }

  std::size_t size() const { return rows_.size(); }
  const std::vector<Row> &rows() const { return rows_; }
  void clear() { rows_.clear(); }

  // Serialize to commit-bundle JSONL: a `start` header carrying the schema id
  // (Decision 0142 versioning) followed by one JSON object per retired
  // instruction. Values are emitted as decimal integers; unknown fields are
  // ignored and groups obey validity gating on the consumer side (Decision 0146).
  bool writeJsonl(const std::filesystem::path &path) const {
    std::ofstream out(path, std::ios::out | std::ios::trunc);
    if (!out.is_open())
      return false;
    out << "{\"type\":\"start\",\"commit_schema_id\":\"" << jsonEscape(schema_) << "\"}\n";
    for (const auto &r : rows_) {
      out << "{\"cycle\":" << r.cycle;
      if (!stage_.empty())
        out << ",\"stage\":\"" << jsonEscape(stage_) << "\"";
      for (const auto &kv : r.vals)
        out << ",\"" << jsonEscape(kv.first) << "\":" << kv.second;
      out << "}\n";
    }
    return out.good();
  }

  // Compact self-describing binary form (magic + schema + stage + rows). Not a
  // stability contract yet; the JSONL form above is the interchange format.
  bool writeBinary(const std::filesystem::path &path) const {
    std::ofstream out(path, std::ios::binary | std::ios::out | std::ios::trunc);
    if (!out.is_open())
      return false;
    const char magic[8] = {'P', 'Y', 'C', 'C', 'M', 'T', '0', '1'};
    out.write(magic, sizeof(magic));
    writeStr(out, schema_);
    writeStr(out, stage_);
    writeU64(out, rows_.size());
    for (const auto &r : rows_) {
      writeU64(out, r.cycle);
      writeU64(out, r.vals.size());
      for (const auto &kv : r.vals) {
        writeStr(out, kv.first);
        writeU64(out, kv.second);
      }
    }
    return out.good();
  }

private:
  static std::string jsonEscape(const std::string &s) {
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
      switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default: out += c; break;
      }
    }
    return out;
  }

  static void writeU64(std::ofstream &out, std::uint64_t v) {
    out.write(reinterpret_cast<const char *>(&v), sizeof(v));
  }

  static void writeStr(std::ofstream &out, const std::string &s) {
    writeU64(out, s.size());
    if (!s.empty())
      out.write(s.data(), static_cast<std::streamsize>(s.size()));
  }

  std::string schema_ = "pyc-commit-v1";
  std::string stage_;
  std::map<std::string, Getter> fields_;
  std::vector<Row> rows_;
};

}  // namespace pyc::cpp
