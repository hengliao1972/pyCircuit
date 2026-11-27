// %y reads a wire that %x2 drives from inside the same run.  Fusing both into
// one comb would read the wire member before the comb publishes %x2, so the
// run must be cut at %y.
func.func @WireInRun(%a : i1) -> i1 {
  %w = pyc.wire : i1
  %x1 = pyc.not %a : i1
  %x2 = pyc.not %x1 : i1
  %y = pyc.not %w : i1
  %z = pyc.not %y : i1
  pyc.assign %w, %x2 : i1
  return %z : i1
}
