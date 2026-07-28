// True closed-loop driver: the commit trace comes from the *simulated design*.
//
// Unlike commit_trace_main.cpp (which hand-feeds values to the collector), this
// instantiates the pycc-generated C++ model of tests/designs/commit_demo.py,
// drives its inputs cycle by cycle, evaluates the design, and binds the
// PycCommitTraceWriter to the design's own commit_* OUTPUT ports. So every row
// in the JSONL is what the compiled design actually presented on its commit
// interface -- not a testbench-authored value.
//
// Build: g++ -std=c++17 -I <model_dir> -I <repo>/runtime this.cpp
//   where <model_dir> holds the pycc-emitted commit_demo.hpp.

#include <cstdint>
#include <iostream>
#include <string>

#include "commit_demo.hpp"          // pycc-generated design model (pyc::gen::commit_demo)
#include "cpp/pyc_commit_trace.hpp"  // commit-trace collector

int main(int argc, char **argv) {
  if (argc < 2) {
    std::cerr << "usage: commit_trace_sim <out.jsonl>\n";
    return 2;
  }

  pyc::gen::commit_demo dut;

  pyc::cpp::PycCommitTraceWriter w;
  w.setSchema("pyc-commit-demo-v1");
  w.setStage("commit");
  // The sensor observes the DESIGN's commit_* output ports.
  w.bind("valid", [&] { return dut.commit_valid.value(); });
  w.bind("pc", [&] { return dut.commit_pc.value(); });
  w.bind("insn", [&] { return dut.commit_insn.value(); });
  w.bind("wb_valid", [&] { return dut.commit_wb_valid.value(); });
  w.bind("wb_rd", [&] { return dut.commit_wb_rd.value(); });
  w.bind("wb_data", [&] { return dut.commit_wb_data.value(); });

  auto drive = [&](std::uint64_t cyc, std::uint64_t retire, std::uint64_t pc,
                   std::uint64_t insn, std::uint64_t wb_en, std::uint64_t wb_rd,
                   std::uint64_t wb_data) {
    dut.retire = pyc::cpp::Wire<1>(retire);
    dut.pc = pyc::cpp::Wire<32>(pc);
    dut.insn = pyc::cpp::Wire<32>(insn);
    dut.wb_en = pyc::cpp::Wire<1>(wb_en);
    dut.wb_rd = pyc::cpp::Wire<5>(wb_rd);
    dut.wb_data = pyc::cpp::Wire<32>(wb_data);
    dut.step();          // comb + tick + commit + comb (design evaluates)
    w.sample(cyc);       // read the design's commit_* ports
  };

  // cycle 0: retire, writes x3 = 0xdead
  drive(0, /*retire=*/1, /*pc=*/0x100, /*insn=*/0x13, /*wb_en=*/1, /*wb_rd=*/3, /*wb_data=*/0xdead);
  // cycle 1: bubble -> design presents commit_valid=0 -> no row
  drive(1, 0, 0, 0, 0, 0, 0);
  // cycle 2: retire, no writeback
  drive(2, 1, 0x104, 0x63, 0, 0, 0);

  if (!w.writeJsonl(argv[1])) {
    std::cerr << "write failed\n";
    return 3;
  }
  std::cout << "rows=" << w.size() << "\n";
  return 0;
}
