#include "comb_dirty_state.cpp"

#include <cstdint>
#include <iostream>

namespace {

using Dut = pyc::gen::comb_dirty_state;

int fail(const char *message) {
  std::cerr << "comb dirty state gate: " << message << "\n";
  return 1;
}

void runPhase(Dut &dut) {
  dut.comb();
  dut.tick();
  dut.commit();
  dut.comb();
}

} // namespace

int main() {
  Dut dut;
  dut.clk = pyc::cpp::Wire<1>(0);
  dut.rst = pyc::cpp::Wire<1>(0);
  dut.enable = pyc::cpp::Wire<1>(0);

  dut.eval();
  dut.eval();
  if (dut.current.word(0) != 0)
    return fail("idle state design changed its output");

  dut.clk = pyc::cpp::Wire<1>(1);
  runPhase(dut);
  dut.clk = pyc::cpp::Wire<1>(0);
  runPhase(dut);
  dut.enable = pyc::cpp::Wire<1>(1);
  dut.clk = pyc::cpp::Wire<1>(1);
  runPhase(dut);
  if (dut.current.word(0) != 1)
    return fail("post-commit comb did not expose the new register value");

  dut.clk = pyc::cpp::Wire<1>(0);
  runPhase(dut);
  dut.enable = pyc::cpp::Wire<1>(0);
  dut.clk = pyc::cpp::Wire<1>(1);
  dut.comb();
  dut.tick();
  dut.commit();
  dut.comb();
  if (dut.current.word(0) != 1)
    return fail("same-value register commit changed the visible output");

  dut.clk = pyc::cpp::Wire<1>(0);
  runPhase(dut);
  dut.rst = pyc::cpp::Wire<1>(1);
  dut.clk = pyc::cpp::Wire<1>(1);
  runPhase(dut);
  if (dut.current.word(0) != 0)
    return fail("reset did not restore the visible register output");

  std::cout << "ok post-commit current=" << dut.current.word(0) << "\n";
  return 0;
}
