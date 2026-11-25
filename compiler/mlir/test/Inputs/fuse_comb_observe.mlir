// Names, aliases and a second op must not split the run: all five ops fuse
// into one pyc.comb, "fwd" and "mid" survive on leaf aliases, and the unnamed
// alias disappears.
func.func @Obs(%a : i1, %b : i1) -> i1 {
  %c = pyc.and %a, %b : i1, i1 -> i1
  %x = pyc.not %c : i1
  %f = pyc.alias %x {pyc.name = "fwd"} : i1
  %u = pyc.alias %f : i1
  %y = pyc.not %u {pyc.name = "mid"} : i1
  %z = pyc.not %y : i1
  return %z : i1
}
