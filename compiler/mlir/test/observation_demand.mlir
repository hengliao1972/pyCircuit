// DEMAND-LABEL: func.func @observation_demand
// DEMAND: pyc.name = "keep_me"
// DEMAND-NOT: pyc.observe_lazy
// DEMAND: pyc.lazy_probe_slices
// DEMAND-SAME: pyc.name = "drop_me"
// DEMAND-SAME: pyc.observe_lazy
// DEMAND: pyc.debug_keep
// DEMAND-SAME: pyc.name = "debug_only"

module {
  func.func @observation_demand(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %a: i8, %b: i8)
      -> (i8, i8) {
    %init0 = pyc.constant 17 : i8
    %init1 = pyc.constant 34 : i8
    %q0 = pyc.reg %clk, %rst, %en, %a, %init0 {pyc.name = "keep_me"} : i8
    %q1 = pyc.reg %clk, %rst, %en, %b, %init1 {pyc.name = "drop_me"} : i8
    %tap = pyc.alias %q0 {pyc.name = "debug_only", pyc.debug_keep = true} : i8
    func.return %q0, %q1 : i8, i8
  }
}
