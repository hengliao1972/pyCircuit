#include "pyc_primitives.hpp"

#include <cassert>
#include <type_traits>

using pyc::cpp::Wire;
using pyc::cpp::pycDelayLine;
using pyc::cpp::BaseElement;
static_assert(std::is_same_v<decltype(std::declval<pycDelayLine<8, 2>&>().tick_commit()),
                             BaseElement::change_mask_t>);

namespace {

void posedge(pycDelayLine<8, 2> &delay, Wire<1> &clk) {
  clk = Wire<1>(0);
  delay.tick_sample();
  clk = Wire<1>(1);
  delay.tick_sample();
}

void testScalarCommitReportsTailChange() {
  Wire<1> clk{}, rst{}, en{};
  Wire<8> d{}, init{}, q{};
  pycDelayLine<8, 2> delay(clk, rst, en, d, init, q);

  // No pending edge: no wake for anyone.
  assert(!delay.has_pending());
  assert(!delay.tick_commit());

  rst = Wire<1>(1);
  en = Wire<1>(0);
  init = Wire<8>(0);
  posedge(delay, clk);
  // The commit advances state, so tap consumers must wake (has_pending);
  // tail-q consumers only wake when the tail value itself changes.
  assert(delay.has_pending());
  assert(!delay.tick_commit());
  assert(!delay.has_pending());
  assert(q.value() == 0);

  rst = Wire<1>(0);
  en = Wire<1>(1);
  d = Wire<8>(7);
  posedge(delay, clk);
  // Tail q is still 0, but tap(1) shifted from 0 to 7: tick_commit reports
  // no tail change while has_pending() (checked before the commit) is what
  // wakes tap consumers.
  assert(delay.has_pending());
  assert(!delay.tick_commit());
  assert(q.value() == 0);
  assert(delay.tap(1).value() == 7);

  d = Wire<8>(9);
  posedge(delay, clk);
  assert(delay.has_pending());
  assert(delay.tick_commit());
  assert(q.value() == 7);

  d = Wire<8>(11);
  posedge(delay, clk);
  assert(delay.tick_commit());
  assert(q.value() == 9);
}

} // namespace

int main() {
  testScalarCommitReportsTailChange();
  return 0;
}
