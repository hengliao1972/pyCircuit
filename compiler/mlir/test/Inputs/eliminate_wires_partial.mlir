// The reg reads %w before its driver, so the wire stays; %y follows the driver
// and must read %x directly instead of the wire.
func.func @partial(%clk: !pyc.clock, %rst: !pyc.reset, %a: i1) -> i1 {
  %en = pyc.constant 1 : i1
  %init = pyc.constant 0 : i1
  %w = pyc.wire : i1
  %q = pyc.reg %clk, %rst, %en, %w, %init : i1
  %x = pyc.not %a : i1
  pyc.assign %w, %x : i1
  %y = pyc.not %w : i1
  %o = pyc.and %y, %q : i1, i1 -> i1
  return %o : i1
}
