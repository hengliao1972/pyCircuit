#include MODEL_HEADER

#include <cassert>
#include <cstdint>
#include <iostream>

// Independent model of the stage-gap + parallel-copy circuit:
//   q1' = a & b, q2' = q1, qc' = a, out = q1 ^ q2 ^ qc  (enable constant 1,
//   init 0; reset loads the init). Checked after the rising edge, matching
// the generated C++ step order.
int main() {
  pyc::gen::fork_merge_model model;
  std::uint8_t q1 = 0, q2 = 0, qc = 0;
  std::uint64_t checksum = 0;
  constexpr unsigned kCycles = 64;
  for (unsigned cycle = 0; cycle < kCycles; ++cycle) {
    const bool reset = cycle < 2 || cycle == 17 || cycle == 40;
    const std::uint8_t a = static_cast<std::uint8_t>(cycle * 3 + 1);
    const std::uint8_t b = static_cast<std::uint8_t>(cycle * 5 + 9);
    model.rst = pyc::cpp::Wire<1>(reset ? 1 : 0);
    model.a = pyc::cpp::Wire<8>(a);
    model.b = pyc::cpp::Wire<8>(b);
    model.clk = pyc::cpp::Wire<1>(0);
    model.step();
    model.clk = pyc::cpp::Wire<1>(1);
    model.step();

    if (reset) {
      q1 = 0;
      q2 = 0;
      qc = 0;
    } else {
      const std::uint8_t nextQ1 = static_cast<std::uint8_t>(a & b);
      q2 = q1;
      qc = a;
      q1 = nextQ1;
    }
    const auto expected =
        static_cast<std::uint8_t>(q1 ^ q2 ^ qc);
    assert(model.out.value() == expected);
    checksum = checksum * 131u + expected;
  }
  std::cout << "cycles=" << kCycles << " checksum=" << checksum << "\n";
  return 0;
}
