// %next feeds back through a reg and survives; %late is read before its driver
// is defined, so its reader moves after the driver and the wire is eliminated.
func.func @layout(%clk: !pyc.clock, %rst: !pyc.reset, %a: i1) -> i1 {
  %en = pyc.constant 1 : i1
  %init = pyc.constant 0 : i1
  %next = pyc.wire : i1
  %q = pyc.reg %clk, %rst, %en, %next, %init : i1
  %x = pyc.not %q : i1
  pyc.assign %next, %x : i1
  %late = pyc.wire : i1
  %y = pyc.not %late : i1
  %z = pyc.not %a : i1
  pyc.assign %late, %z : i1
  return %y : i1
}
