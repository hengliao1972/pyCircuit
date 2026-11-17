// %n reads %w before its driver and feeds the reg, whose q drives %w.  SSA pins
// the reg between %n and its fanout, so its input is rewired through a new wire.
func.func @pinned(%clk: !pyc.clock, %rst: !pyc.reset) -> i1 {
  %en = pyc.constant 1 : i1
  %init = pyc.constant 0 : i1
  %w = pyc.wire : i1
  %n = pyc.not %w : i1
  %q = pyc.reg %clk, %rst, %en, %n, %init : i1
  %x = pyc.not %q : i1
  pyc.assign %w, %x : i1
  return %q : i1
}
