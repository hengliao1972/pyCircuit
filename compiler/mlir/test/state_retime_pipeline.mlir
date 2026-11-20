// RUN: pyc-opt %s --pass-pipeline='builtin.module(func.func(pyc-retime-pipelines))' | FileCheck %s --check-prefix=RETIME
// RETIME-LABEL: func.func @unary_pipeline
// RETIME-SAME: pyc.stats.retime_regions_rewritten = 1 : i64
// RETIME-SAME: pyc.stats.retime_regs_rewritten = 3 : i64
// RETIME-SAME: pyc.stats.retime_state_primitives_removed = 2 : i64
// RETIME: %[[LINE:.*]] = pyc.delay_line
// RETIME-SAME: depth = 3 : i64
// RETIME: %[[TAP1:.*]] = pyc.delay_tap %[[LINE]]
// RETIME-SAME: depth = 1 : i64
// RETIME: %[[SIDE0:.*]] = pyc.xor %[[TAP1]]
// RETIME: %[[TAP2:.*]] = pyc.delay_tap %[[LINE]]
// RETIME-SAME: depth = 2 : i64
// RETIME: %[[MID:.*]] = pyc.add %[[TAP2]]
// RETIME: %[[TAIL0:.*]] = pyc.add %[[LINE]]
// RETIME: %[[TAIL:.*]] = pyc.xor %[[TAIL0]]
// RETIME-NOT: pyc.reg
// RETIME: return %[[SIDE0]], %[[MID]], %[[TAIL]]

// RETIME-LABEL: func.func @different_widths
// RETIME: %[[WLINE:.*]] = pyc.delay_line
// RETIME-SAME: depth = 2 : i64
// RETIME-SAME: : i8
// RETIME: %[[WTAP:.*]] = pyc.zext %[[WLINE]]
// RETIME: return %[[WTAP]] : i16
// RETIME-NOT: pyc.reg

// RETIME-LABEL: func.func @pipeline_state_fanout
// RETIME-SAME: pyc.stats.retime_regions_rewritten = 1 : i64
// RETIME: %[[FLINE:.*]] = pyc.delay_line
// RETIME-SAME: depth = 2 : i64
// RETIME: %[[FTAP:.*]] = pyc.delay_tap %[[FLINE]]
// RETIME-SAME: depth = 1 : i64
// RETIME: %[[FADD:.*]] = pyc.add %[[FLINE]]
// RETIME: %[[BRANCH:.*]] = pyc.reg
// RETIME-SAME: %[[FTAP]]
// RETIME: return %[[FADD]], %[[BRANCH]] : i8, i8

// RETIME-LABEL: func.func @common_delay_sink
// RETIME-SAME: pyc.stats.retime_common_delay_sinks = 1 : i64
// RETIME-SAME: pyc.stats.retime_state_bits_removed = 15 : i64
// RETIME: %[[CMP:.*]] = pyc.ult
// RETIME: %[[CINIT:.*]] = pyc.constant 1 : i1
// RETIME: %[[CREG:.*]] = pyc.reg
// RETIME-SAME: %[[CMP]], %[[CINIT]]
// RETIME-SAME: pyc.optimized_by = "retime_common_delay_sink"
// RETIME-NOT: pyc.reg{{.*}} : i8
// RETIME: return %[[CREG]] : i1

// RETIME-LABEL: func.func @common_depth2_sink
// RETIME-SAME: pyc.stats.retime_common_delay_sinks = 1 : i64
// RETIME-SAME: pyc.stats.retime_state_bits_removed = 30 : i64
// RETIME: %[[EQ:.*]] = pyc.eq
// RETIME: %[[DINIT:.*]] = pyc.constant 1 : i1
// RETIME: %[[DLINE:.*]] = pyc.delay_line
// RETIME-SAME: %[[EQ]], %[[DINIT]]
// RETIME-SAME: depth = 2 : i64
// RETIME-NOT: pyc.delay_line{{.*}} : i8
// RETIME: return %[[DLINE]] : i1

// RETIME-LABEL: func.func @reject_common_sink_fanout
// RETIME-COUNT-2: pyc.reg
// RETIME-NOT: retime_common_delay_sink

// RETIME-LABEL: func.func @reject_common_sink_feedback
// RETIME: pyc.reg
// RETIME-NOT: retime_common_delay_sink

// RETIME-LABEL: func.func @reject_pipeline_feedback
// RETIME-COUNT-2: pyc.reg
// RETIME-NOT: retime_pipeline_history

// RETIME-LABEL: func.func @reject_dynamic_operand
// RETIME-COUNT-2: pyc.reg
// RETIME-NOT: pyc.delay_line

// RETIME-LABEL: func.func @reject_init_mismatch
// RETIME-SAME: pyc.stats.retime_blocked_init = 1 : i64
// RETIME-COUNT-2: pyc.reg
// RETIME-NOT: pyc.delay_line

// RETIME-LABEL: func.func @reject_enable_mismatch
// RETIME-COUNT-2: pyc.reg
// RETIME-NOT: pyc.delay_line

// RETIME-LABEL: func.func @preserve_named
// RETIME: pyc.delay_line
// RETIME: pyc.alias
// RETIME-SAME: pyc.name = "stage0"
// RETIME-NOT: pyc.reg
// RETIME-LABEL: func.func @named_common_sink
// RETIME: pyc.name = "lhs_hist"
// RETIME: pyc.name = "rhs_hist"
// RETIME: pyc.optimized_by = "retime_common_delay_sink"

module {
  func.func @unary_pipeline(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8)
      -> (i8, i8, i8) {
    %zero = pyc.constant 0 : i8
    %one = pyc.constant 1 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %zero : i8
    %side0 = pyc.xor %q0, %one : i8, i8 -> i8
    %add = pyc.add %q0, %one : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %en, %add, %one : i8
    %xor = pyc.xor %q1, %one : i8, i8 -> i8
    %q2 = pyc.reg %clk, %rst, %en, %xor, %zero : i8
    func.return %side0, %q1, %q2 : i8, i8, i8
  }

  func.func @different_widths(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8) -> i16 {
    %zero8 = pyc.constant 0 : i8
    %zero16 = pyc.constant 0 : i16
    %q0 = pyc.reg %clk, %rst, %en, %in, %zero8 : i8
    %wide = pyc.zext %q0 : i8 -> i16
    %q1 = pyc.reg %clk, %rst, %en, %wide, %zero16 : i16
    func.return %q1 : i16
  }

  func.func @pipeline_state_fanout(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8)
      -> (i8, i8) {
    %zero = pyc.constant 0 : i8
    %one = pyc.constant 1 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %zero : i8
    %next = pyc.add %q0, %one : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %en, %next, %one : i8
    %branch = pyc.reg %clk, %rst, %en, %q0, %zero : i8
    func.return %q1, %branch : i8, i8
  }

  func.func @common_delay_sink(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %lhs: i8, %rhs: i8)
      -> i1 {
    %lhsInit = pyc.constant 3 : i8
    %rhsInit = pyc.constant 9 : i8
    %lq = pyc.reg %clk, %rst, %en, %lhs, %lhsInit : i8
    %rq = pyc.reg %clk, %rst, %en, %rhs, %rhsInit : i8
    %less = pyc.ult %lq, %rq : i8, i8 -> i1
    func.return %less : i1
  }

  func.func @common_depth2_sink(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %lhs: i8, %rhs: i8)
      -> i1 {
    %zero = pyc.constant 0 : i8
    %lq = pyc.delay_line %clk, %rst, %en, %lhs, %zero {depth = 2 : i64} : i8
    %rq = pyc.delay_line %clk, %rst, %en, %rhs, %zero {depth = 2 : i64} : i8
    %equal = pyc.eq %lq, %rq : i8, i8 -> i1
    func.return %equal : i1
  }

  func.func @reject_common_sink_fanout(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %lhs: i8, %rhs: i8)
      -> (i8, i1) {
    %zero = pyc.constant 0 : i8
    %lq = pyc.reg %clk, %rst, %en, %lhs, %zero : i8
    %rq = pyc.reg %clk, %rst, %en, %rhs, %zero : i8
    %less = pyc.ult %lq, %rq : i8, i8 -> i1
    func.return %lq, %less : i8, i1
  }

  func.func @reject_common_sink_feedback(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1) -> i4 {
    %zero = pyc.constant 0 : i8
    %wire = pyc.wire : i8
    %q = pyc.reg %clk, %rst, %en, %wire, %zero : i8
    %low = pyc.extract %q {lsb = 0 : i64, msb = 3 : i64} : i8 -> i4
    %next = pyc.zext %low : i4 -> i8
    pyc.assign %wire, %next : i8
    func.return %low : i4
  }

  func.func @reject_pipeline_feedback(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1) -> i8 {
    %zero = pyc.constant 0 : i8
    %one = pyc.constant 1 : i8
    %wire = pyc.wire : i8
    %q0 = pyc.reg %clk, %rst, %en, %wire, %zero : i8
    pyc.assign %wire, %q0 : i8
    %next = pyc.add %q0, %one : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %en, %next, %one : i8
    func.return %q1 : i8
  }

  func.func @reject_dynamic_operand(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8, %side: i8)
      -> i8 {
    %zero = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %zero : i8
    %add = pyc.add %q0, %side : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %en, %add, %zero : i8
    func.return %q1 : i8
  }

  func.func @reject_init_mismatch(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8) -> i8 {
    %zero = pyc.constant 0 : i8
    %one = pyc.constant 1 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %zero : i8
    %add = pyc.add %q0, %one : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %en, %add, %zero : i8
    func.return %q1 : i8
  }

  func.func @reject_enable_mismatch(
      %clk: !pyc.clock, %rst: !pyc.reset, %en0: i1, %en1: i1, %in: i8)
      -> i8 {
    %zero = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en0, %in, %zero : i8
    %inv = pyc.not %q0 : i8
    %q1 = pyc.reg %clk, %rst, %en1, %inv, %zero : i8
    func.return %q1 : i8
  }

  func.func @preserve_named(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8) -> i8 {
    %zero = pyc.constant 0 : i8
    %ones = pyc.constant 255 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %zero {pyc.name = "stage0"} : i8
    %inv = pyc.xor %q0, %ones : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %en, %inv, %ones : i8
    func.return %q1 : i8
  }

  func.func @named_common_sink(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %lhs: i8, %rhs: i8)
      -> i1 {
    %lhsInit = pyc.constant 3 : i8
    %rhsInit = pyc.constant 9 : i8
    %lq = pyc.reg %clk, %rst, %en, %lhs, %lhsInit {pyc.name = "lhs_hist"} : i8
    %rq = pyc.reg %clk, %rst, %en, %rhs, %rhsInit {pyc.name = "rhs_hist"} : i8
    %less = pyc.ult %lq, %rq : i8, i8 -> i1
    func.return %less : i1
  }
}
