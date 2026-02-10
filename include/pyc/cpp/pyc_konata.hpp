#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>

namespace pyc::cpp {

class KonataWriter {
public:
  bool open(const std::filesystem::path &path, std::uint64_t startCycle) {
    out_.open(path, std::ios::out | std::ios::trunc);
    if (!out_.is_open())
      return false;
    out_ << "Kanata\t0004\n";
    out_ << "C=\t" << startCycle << "\n";
    cur_cycle_ = startCycle;
    opened_ = true;
    return true;
  }

  bool isOpen() const { return opened_ && out_.is_open(); }

  void atCycle(std::uint64_t cycle) {
    if (!isOpen())
      return;
    if (cycle < cur_cycle_) {
      // Konata time must be monotonic; ignore out-of-order writes.
      return;
    }
    if (cycle == cur_cycle_)
      return;
    out_ << "C\t" << (cycle - cur_cycle_) << "\n";
    cur_cycle_ = cycle;
  }

  void insn(std::uint64_t fileId, std::uint64_t simId, std::uint64_t threadId) {
    if (!isOpen())
      return;
    out_ << "I\t" << fileId << "\t" << simId << "\t" << threadId << "\n";
  }

  void label(std::uint64_t id, int type, const std::string &text) {
    if (!isOpen())
      return;
    out_ << "L\t" << id << "\t" << type << "\t" << sanitizeText(text) << "\n";
  }

  void stageStart(std::uint64_t id, int laneId, const std::string &stage) {
    if (!isOpen())
      return;
    out_ << "S\t" << id << "\t" << laneId << "\t" << stage << "\n";
  }

  void stageEnd(std::uint64_t id, int laneId, const std::string &stage) {
    if (!isOpen())
      return;
    out_ << "E\t" << id << "\t" << laneId << "\t" << stage << "\n";
  }

  void retire(std::uint64_t id, std::uint64_t retireId, int type) {
    if (!isOpen())
      return;
    out_ << "R\t" << id << "\t" << retireId << "\t" << type << "\n";
  }

  void dep(std::uint64_t consumerId, std::uint64_t producerId, int type) {
    if (!isOpen())
      return;
    out_ << "W\t" << consumerId << "\t" << producerId << "\t" << type << "\n";
  }

private:
  static std::string sanitizeText(const std::string &s) {
    std::string out = s;
    for (char &c : out) {
      if (c == '\t' || c == '\n' || c == '\r')
        c = ' ';
    }
    return out;
  }

  std::ofstream out_{};
  bool opened_ = false;
  std::uint64_t cur_cycle_ = 0;
};

} // namespace pyc::cpp
