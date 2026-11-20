#include MODEL_HEADER

#include <cassert>
#include <cstdint>
#include <iostream>

int main() {
  pyc::gen::state_retime_codegen model;
  std::uint8_t q0 = 3;
  std::uint8_t q1 = 8;
  std::uint8_t q2 = 173;
  std::uint8_t branch = 3;
  std::uint8_t lhsQ = 7;
  std::uint8_t rhsQ = 19;
  std::uint64_t checksum = 0x9e3779b97f4a7c15ULL;
  constexpr unsigned kCycles = 613;
  for (unsigned cycle = 0; cycle < kCycles; ++cycle) {
    const bool reset = cycle < 2 || cycle == 109 || cycle == 397;
    const bool enable = cycle % 11 != 3 && cycle % 11 != 4 && cycle % 17 != 8;
    const std::uint8_t input =
        static_cast<std::uint8_t>(cycle * 73 + cycle / 3 + 19);
    const std::uint8_t rhs =
        static_cast<std::uint8_t>(cycle * 29 + cycle / 5 + 211);
    model.rst = pyc::cpp::Wire<1>(reset ? 1 : 0);
    model.en = pyc::cpp::Wire<1>(enable ? 1 : 0);
    model.in = pyc::cpp::Wire<8>(input);
    model.rhs = pyc::cpp::Wire<8>(rhs);
    model.clk = pyc::cpp::Wire<1>(0);
    model.step();
    model.clk = pyc::cpp::Wire<1>(1);
    model.step();

    if (reset) {
      q0 = 3;
      q1 = 8;
      q2 = 173;
      branch = 3;
      lhsQ = 7;
      rhsQ = 19;
    } else if (enable) {
      q2 = static_cast<std::uint8_t>(q1 ^ 165);
      branch = q0;
      q1 = static_cast<std::uint8_t>(q0 + 5);
      q0 = input;
      lhsQ = input;
      rhsQ = rhs;
    }
    const std::uint8_t expected0 = static_cast<std::uint8_t>(q0 ^ 60);
    const std::uint8_t expected1 = static_cast<std::uint8_t>(q1 + 7);
    assert(model.stage0.value() == expected0);
    assert(model.stage1.value() == expected1);
    assert(model.tail.value() == q2);
    assert(model.branch.value() == branch);
    assert(model.less.value() == (lhsQ < rhsQ));
    for (std::uint64_t value :
         {model.stage0.value(), model.stage1.value(), model.tail.value(),
          model.branch.value(), model.less.value()})
      checksum ^=
          value + 0x9e3779b97f4a7c15ULL + (checksum << 6) + (checksum >> 2);
  }
  std::cout << "cycles=" << kCycles << " checksum=" << checksum << "\n";
  return 0;
}
