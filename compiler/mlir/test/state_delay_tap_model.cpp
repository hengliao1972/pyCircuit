#include MODEL_HEADER

#include <cassert>
#include <cstdint>
#include <iostream>
#include <cstdlib>

int main() {
  pyc::gen::state_delay_tap_codegen model;
  std::uint8_t history[4] = {0, 0, 0, 0};
  std::uint64_t checksum = 0x9e3779b97f4a7c15ULL;
  constexpr unsigned kCycles = 257;
  for (unsigned cycle = 0; cycle < kCycles; ++cycle) {
    const bool reset = cycle == 0 || cycle == 1 || cycle == 113;
    const bool enable = cycle % 9 != 4 && cycle % 9 != 5;
    const std::uint8_t input = static_cast<std::uint8_t>(cycle * 37 + 11);
    model.rst = pyc::cpp::Wire<1>(reset ? 1 : 0);
    model.en = pyc::cpp::Wire<1>(enable ? 1 : 0);
    model.in = pyc::cpp::Wire<8>(input);
    model.clk = pyc::cpp::Wire<1>(0);
    model.step();
    model.clk = pyc::cpp::Wire<1>(1);
    model.step();

    if (reset) {
      for (auto &value : history)
        value = 0;
    } else if (enable) {
      history[3] = history[2];
      history[2] = history[1];
      history[1] = history[0];
      history[0] = input;
    }
    if (getenv("TAPDBG") && cycle < 8)
      std::cerr << "c" << cycle << " tap=" << (unsigned)model.tap.value()
                << " exp=" << (unsigned)history[1]
                << " tail=" << (unsigned)model.tail.value()
                << " expt=" << (unsigned)history[3] << "\n";
    assert(model.tap.value() == history[1]);
    assert(model.tail.value() == history[3]);
    checksum ^= model.tap.value() + 0x9e3779b97f4a7c15ULL +
                (checksum << 6) + (checksum >> 2);
    checksum ^= model.tail.value() + 0x517cc1b727220a95ULL +
                (checksum << 6) + (checksum >> 2);
  }
  std::cout << "cycles=" << kCycles << " checksum=" << checksum << "\n";
  return 0;
}
