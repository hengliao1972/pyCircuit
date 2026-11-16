// Delay-line chain formation regression fixture (pyc-state-optimize without
// retiming): serial register chains become pyc.delay_line, read-only fanout
// becomes pyc.delay_tap, and observation identities are remapped, not dropped.
//
// CHECK-LABEL: func.func @form_untagged_chain
// CHECK: pyc.delay_line
// CHECK-SAME: depth = 3 : i64
// CHECK-SAME: pyc.optimized_by = "combine_delay_chains_structural"
// CHECK-NOT: pyc.reg
// CHECK-LABEL: func.func @keep_named_intermediate
// CHECK: pyc.delay_line
// CHECK-SAME: depth = 2 : i64
// CHECK: pyc.delay_tap
// CHECK: pyc.alias
// CHECK-SAME: pyc.name = "architectural_tap"
// CHECK-NOT: pyc.reg
// CHECK-LABEL: func.func @keep_fanout
// CHECK: pyc.delay_line
// CHECK: pyc.delay_tap
// CHECK-SAME: depth = 1 : i64
// CHECK-NOT: pyc.reg
// CHECK-LABEL: func.func @keep_stateful_fanout
// CHECK-COUNT-3: pyc.reg
// CHECK-NOT: pyc.delay_line
// CHECK-LABEL: func.func @keep_debug_state
// CHECK: pyc.delay_line
// CHECK-SAME: depth = 2 : i64
// CHECK: pyc.alias
// CHECK-SAME: pyc.debug_keep = true
// CHECK-NOT: pyc.reg
// CHECK-LABEL: func.func @keep_control_mismatch
// CHECK: pyc.reg
// CHECK: pyc.reg
// CHECK-NOT: pyc.delay_line
// CHECK-LABEL: func.func @form_generated_chain
// CHECK: pyc.delay_line
// CHECK-SAME: depth = 2 : i64
// CHECK-SAME: pyc.generated = "cycle_balance"
// CHECK-LABEL: func.func @share_equal_delay_lines
// CHECK-COUNT-1: pyc.delay_line
// CHECK-NOT: pyc.delay_line
// CHECK-LABEL: func.func @drop_dead_state
// CHECK-NOT: pyc.reg
// CHECK-NOT: pyc.delay_line

module {
  func.func @form_untagged_chain(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %q1 = pyc.reg %clk, %rst, %en, %q0, %init : i8
    %q2 = pyc.reg %clk, %rst, %en, %q1, %init : i8
    func.return %q2 : i8
  }

  func.func @keep_named_intermediate(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %tap = pyc.alias %q0 {pyc.name = "architectural_tap"} : i8
    %q1 = pyc.reg %clk, %rst, %en, %tap, %init : i8
    func.return %q1 : i8
  }

  func.func @keep_fanout(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> (i8, i8) {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %q1 = pyc.reg %clk, %rst, %en, %q0, %init : i8
    func.return %q0, %q1 : i8, i8
  }

  func.func @keep_stateful_fanout(
      %clk: !pyc.clock, %rst: !pyc.reset, %in: i8, %en2: i1) -> (i8, i8) {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %q1 = pyc.reg %clk, %rst, %en, %q0, %init : i8
    %q2 = pyc.reg %clk, %rst, %en2, %q0, %init : i8
    func.return %q1, %q2 : i8, i8
  }

  func.func @keep_debug_state(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init {pyc.debug_keep = true} : i8
    %q1 = pyc.reg %clk, %rst, %en, %q0, %init : i8
    func.return %q1 : i8
  }

  func.func @keep_control_mismatch(%clk: !pyc.clock, %rst: !pyc.reset,
                                   %in: i8, %en0: i1, %en1: i1) -> i8 {
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en0, %in, %init : i8
    %q1 = pyc.reg %clk, %rst, %en1, %q0, %init : i8
    func.return %q1 : i8
  }

  func.func @form_generated_chain(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init {pyc.generated = "cycle_balance"} : i8
    %q1 = pyc.reg %clk, %rst, %en, %q0, %init {pyc.generated = "cycle_balance"} : i8
    func.return %q1 : i8
  }

  // Two chains with identical controls and input share one history.
  func.func @share_equal_delay_lines(
      %clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> (i8, i8) {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %a0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %a1 = pyc.reg %clk, %rst, %en, %a0, %init : i8
    %b0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %b1 = pyc.reg %clk, %rst, %en, %b0, %init : i8
    func.return %a1, %b1 : i8, i8
  }

  func.func @drop_dead_state(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %q1 = pyc.reg %clk, %rst, %en, %q0, %init : i8
    func.return %in : i8
  }
}
