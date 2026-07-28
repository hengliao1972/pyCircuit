// Self-contained C++ driver for the B1 commit-trace collector (TODO B1).
//
// Binds the same field vocabulary as tests/designs/commit_demo.py to a
// PycCommitTraceWriter, drives a few cycles (including a bubble that must be
// skipped), and writes a commit-bundle JSONL to argv[1]. The regression test
// reads the JSONL back and validates it -- no external tool involved.

#include <cstdint>
#include <iostream>
#include <string>

#include "cpp/pyc_commit_trace.hpp"

int main(int argc, char **argv) {
  if (argc < 2) {
    std::cerr << "usage: commit_trace_main <out.jsonl>\n";
    return 2;
  }

  pyc::cpp::PycCommitTraceWriter w;
  w.setSchema("pyc-commit-demo-v1");  // the design owns its schema id
  w.setStage("commit");

  std::uint64_t valid = 0, pc = 0, insn = 0, wb_valid = 0, wb_rd = 0, wb_data = 0;
  w.bind("valid", [&] { return valid; });
  w.bind("pc", [&] { return pc; });
  w.bind("insn", [&] { return insn; });
  w.bind("wb_valid", [&] { return wb_valid; });
  w.bind("wb_rd", [&] { return wb_rd; });
  w.bind("wb_data", [&] { return wb_data; });

  // cycle 0: retire, writes x3 = 0xdead
  valid = 1; pc = 0x100; insn = 0x13; wb_valid = 1; wb_rd = 3; wb_data = 0xdead;
  w.sample(0);
  // cycle 1: bubble -> no row emitted
  valid = 0;
  w.sample(1);
  // cycle 2: retire, no writeback
  valid = 1; pc = 0x104; insn = 0x63; wb_valid = 0; wb_rd = 0; wb_data = 0;
  w.sample(2);

  if (!w.writeJsonl(argv[1])) {
    std::cerr << "write failed\n";
    return 3;
  }
  std::cout << "rows=" << w.size() << "\n";
  return 0;
}
