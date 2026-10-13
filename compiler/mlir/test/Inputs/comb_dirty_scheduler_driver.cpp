#include "comb_dirty_scheduler.cpp"

#include <cstdint>
#include <iostream>

namespace {

using Dut = pyc::gen::comb_dirty_scheduler;

int fail(const char *message) {
  std::cerr << "comb dirty scheduler gate: " << message << "\n";
  return 1;
}

bool lowEq(const pyc::cpp::Wire<8> &value, std::uint64_t expected) {
  return value.word(0) == expected;
}

} // namespace

int main() {
  Dut dut;
  dut.a = pyc::cpp::Wire<8>(0x12);
  dut.mask = pyc::cpp::Wire<8>(0x00);
  dut.b = pyc::cpp::Wire<8>(0x03);
  dut.c = pyc::cpp::Wire<8>(0x05);

  dut.eval();
  if (!lowEq(dut.producer, 0) || !lowEq(dut.result, 6) ||
      !lowEq(dut.constant_out, 0x5a))
    return fail("first evaluation did not initialize every comb group");

  dut.eval();
  if (!lowEq(dut.producer, 0) || !lowEq(dut.result, 6) ||
      !lowEq(dut.constant_out, 0x5a))
    return fail("idle evaluation changed a stable result");

  // The producer runs, but its semantic output remains zero. No downstream
  // result is allowed to change.
  dut.a = pyc::cpp::Wire<8>(0x34);
  dut.eval();
  if (!lowEq(dut.producer, 0) || !lowEq(dut.result, 6))
    return fail("unchanged producer output changed downstream results");

  dut.mask = pyc::cpp::Wire<8>(0xff);
  dut.eval();
  if (!lowEq(dut.producer, 0x34) || !lowEq(dut.result, 0x0e))
    return fail("fanout/reconvergence produced the wrong value");

  dut.b = pyc::cpp::Wire<8>(0x04);
  dut.eval();
  if (!lowEq(dut.producer, 0x34) || !lowEq(dut.result, 0x01))
    return fail("single-branch update did not reach reconvergence");

  std::cout << "ok dirty-only scheduler\n";
  return 0;
}
