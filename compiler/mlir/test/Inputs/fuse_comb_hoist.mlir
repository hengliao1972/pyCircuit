// The register must not split the run: constants come first, the register
// follows reading its next state through a wire, and the logic fuses into one
// pyc.comb that keeps the constant it uses as a literal.
func.func @Hoist(%clk : !pyc.clock, %rst : !pyc.reset, %a : i1) -> i1 {
  %x = pyc.not %a : i1
  %t = pyc.constant 1 : i1
  %c0 = pyc.constant 0 : i1
  %q = pyc.reg %clk, %rst, %t, %x, %c0 : i1
  %y = pyc.not %q : i1
  %z = pyc.and %y, %x : i1, i1 -> i1
  %o = pyc.or %z, %t : i1, i1 -> i1
  return %o : i1
}
