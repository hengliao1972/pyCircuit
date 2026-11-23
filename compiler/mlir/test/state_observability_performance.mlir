// KEEP-LABEL: func.func @drop_observation_only_state
// KEEP: pyc.reg
// KEEP: pyc.alias
// KEEP-SAME: pyc.name = "debug_tap"
// KEEP: return

// KEEP-LABEL: func.func @keep_functional_state
// KEEP: %[[Q:.*]] = pyc.reg
// KEEP-SAME: pyc.name = "functional_state"
// KEEP: return %[[Q]]

module {
  func.func @drop_observation_only_state(
      %clk: !pyc.clock, %rst: !pyc.reset, %in: i8) {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q = pyc.reg %clk, %rst, %en, %in, %init
        {pyc.debug_keep = true, pyc.observable = true} : i8
    %tap = pyc.alias %q
        {pyc.name = "debug_tap", pyc.probe.internal = true} : i8
    func.return
  }

  func.func @keep_functional_state(
      %clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q = pyc.reg %clk, %rst, %en, %in, %init
        {pyc.debug_keep = true, pyc.name = "functional_state"} : i8
    func.return %q : i8
  }
}
