// A wire is a continuous assignment target: a second driver has no defined value.
func.func @multi_driver(%a: i1, %b: i1) -> i1 {
  // expected-error @+1 {{pyc.wire has 2 pyc.assign drivers; a wire must have a single driver}}
  %w = pyc.wire : i1
  pyc.assign %w, %a : i1
  pyc.assign %w, %b : i1
  return %w : i1
}
