// The %p and %q cones are interleaved but read different registers, so they
// fuse into separate combs that each wake on their own register.  Both read
// the port %b, which enters through a leading comb that only forwards it.
func.func @Split(%clk : !pyc.clock, %rst : !pyc.reset, %a : i1, %b : i1) -> (i1, i1) {
  %t = pyc.constant 1 : i1
  %c0 = pyc.constant 0 : i1
  %p = pyc.reg %clk, %rst, %t, %a, %c0 : i1
  %q = pyc.reg %clk, %rst, %t, %a, %c0 : i1
  %p1 = pyc.and %p, %b : i1, i1 -> i1
  %q1 = pyc.or %q, %b : i1, i1 -> i1
  %p2 = pyc.not %p1 : i1
  %q2 = pyc.not %q1 : i1
  return %p2, %q2 : i1, i1
}
