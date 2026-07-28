// Zero-binding closed-loop driver: the commit trace is produced automatically.
//
// Contrast with commit_trace_sim_main.cpp, which hand-binds a
// PycCommitTraceWriter to the design's commit_* ports. Here the pycc CppEmitter
// has already woven a commit-trace sensor into the generated model straight from
// the design's `pyc.commit_iface` contract, so this testbench contains NO trace
// wiring at all: it only drives inputs and calls step(). Setting the
// PYC_COMMIT_TRACE environment variable to an output path makes the model
// sample its own commit interface every cycle and flush the JSONL on teardown.
//
// Build: g++ -std=c++17 -I <model_dir> -I <repo>/runtime this.cpp
//   where <model_dir> holds the pycc-emitted commit_demo.hpp.
// Run:   PYC_COMMIT_TRACE=<out.jsonl> ./a.out

#include <cstdint>

#include "commit_demo.hpp"  // pycc-generated design model (pyc::gen::commit_demo)

int main() {
  pyc::gen::commit_demo dut;

  auto drive = [&](std::uint64_t retire, std::uint64_t pc, std::uint64_t insn,
                   std::uint64_t wb_en, std::uint64_t wb_rd,
                   std::uint64_t wb_data) {
    dut.retire = pyc::cpp::Wire<1>(retire);
    dut.pc = pyc::cpp::Wire<32>(pc);
    dut.insn = pyc::cpp::Wire<32>(insn);
    dut.wb_en = pyc::cpp::Wire<1>(wb_en);
    dut.wb_rd = pyc::cpp::Wire<5>(wb_rd);
    dut.wb_data = pyc::cpp::Wire<32>(wb_data);
    dut.step();  // model auto-samples its commit interface at end of step()
  };

  // cycle 0: retire, writes x3 = 0xdead
  drive(/*retire=*/1, /*pc=*/0x100, /*insn=*/0x13, /*wb_en=*/1, /*wb_rd=*/3, /*wb_data=*/0xdead);
  // cycle 1: bubble -> design presents commit_valid=0 -> no row
  drive(0, 0, 0, 0, 0, 0);
  // cycle 2: retire, no writeback
  drive(1, 0x104, 0x63, 0, 0, 0);

  // JSONL is flushed automatically on dut destruction (PYC_COMMIT_TRACE).
  return 0;
}
