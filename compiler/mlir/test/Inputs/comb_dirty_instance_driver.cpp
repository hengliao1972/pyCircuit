#include "comb_dirty_instance.cpp"

#include <iostream>

namespace {

using Dut = pyc::gen::comb_dirty_instance;

int fail(const char *message) {
  std::cerr << "comb dirty instance gate: " << message << "\n";
  return 1;
}

} // namespace

int main() {
  Dut dut;
  dut.a = pyc::cpp::Wire<8>(0x12);
  dut.mask = pyc::cpp::Wire<8>(0);
  dut.bias = pyc::cpp::Wire<8>(3);
  dut.eval();
  if (dut.child_out.word(0) != 0 || dut.result.word(0) != 3)
    return fail("first hierarchical evaluation produced the wrong value");

  dut.eval();
  if (dut.child_out.word(0) != 0 || dut.result.word(0) != 3)
    return fail("idle hierarchical evaluation changed outputs");

  // The child is conservatively revisited because an input changed, but its
  // unchanged output must not wake the post-instance parent comb consumer.
  // One pre-instance comb still runs to publish the changed child input.
  dut.a = pyc::cpp::Wire<8>(0x34);
  dut.eval();
  if (dut.child_out.word(0) != 0 || dut.result.word(0) != 3)
    return fail("unchanged child output changed the parent result");

  dut.mask = pyc::cpp::Wire<8>(0xff);
  dut.eval();
  if (dut.child_out.word(0) != 0x34 || dut.result.word(0) != 0x37)
    return fail("changed child output did not wake its parent comb consumer");

  std::cout << "ok hierarchical semantic publish\n";
  return 0;
}
