// Names, aliases and the broadcast must not split the run: all five ops fuse
// into one pyc.comb, "fwd" and "mid" survive on leaf aliases, and the unnamed
// alias disappears.
func.func @Obs(%a : vector<2xi1>) -> i1 {
  %b = pyc.v_broadcast_dim %a to 2, 0 : vector<2xi1> -> vector<2x2xi1>
  %c = pyc.v_or_reduce %b {mode = "tree"} : vector<2x2xi1> -> i1
  %x = pyc.not %c : i1
  %f = pyc.alias %x {pyc.name = "fwd"} : i1
  %u = pyc.alias %f : i1
  %y = pyc.not %u {pyc.name = "mid"} : i1
  %z = pyc.not %y : i1
  return %z : i1
}
