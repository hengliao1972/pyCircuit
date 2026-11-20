// RUN: pyc-opt %s --pass-pipeline='builtin.module(func.func(pyc-fork-merge-retime))' | FileCheck %s

// Parallel copies with identical control and data collapse into one register.
// CHECK-LABEL: func.func @merge_copies
// CHECK-SAME: pyc.stats.fork_merge_merged = 1
// CHECK: %[[Q:.*]] = pyc.reg %arg0, %arg1, %arg2, %arg3
// CHECK-NOT: pyc.reg

// Same data but different enable: not the same flip-flop, keep both.
// CHECK-LABEL: func.func @distinct_en
// CHECK-SAME: pyc.stats.fork_merge_merged = 0
// CHECK: pyc.reg %arg0, %arg1, %arg2, %arg3
// CHECK: pyc.reg %arg0, %arg1, %{{.*}}, %arg3

// Different init: different power-up value, keep both.
// CHECK-LABEL: func.func @distinct_init
// CHECK-SAME: pyc.stats.fork_merge_merged = 0
// CHECK: pyc.reg
// CHECK: pyc.reg

// A register that follows another register is not a duplicate of it.
// CHECK-LABEL: func.func @follows_register
// CHECK-SAME: pyc.stats.fork_merge_merged = 0
// CHECK: %[[A:.*]] = pyc.reg %arg0, %arg1, %arg2, %arg3
// CHECK: pyc.reg %arg0, %arg1, %arg2, %[[A]]

// Hold loops stay untouched.
// CHECK-LABEL: func.func @hold_loop
// CHECK-SAME: pyc.stats.fork_merge_merged = 0
// CHECK: pyc.mux

// Non-constant init is out of scope.
// CHECK-LABEL: func.func @nonconst_init
// CHECK-SAME: pyc.stats.fork_merge_merged = 0
// CHECK: pyc.reg

// An externally observable copy still merges: its name survives as a
// read-only alias of the survivor, so a probe reads the identical value.
// CHECK-LABEL: func.func @named_copies
// CHECK-SAME: pyc.stats.fork_merge_merged = 1
// CHECK: %[[Q:.*]] = pyc.reg %arg0, %arg1, %arg2, %arg3
// CHECK: pyc.alias %[[Q]] {pyc.name = "keep_me"}
// CHECK-NOT: pyc.reg

module {
  func.func @merge_copies(%clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %a: i8) -> i8 {
    %zero = pyc.constant 0 : i8
    %q1 = pyc.reg %clk, %rst, %en, %a, %zero : i8
    %q2 = pyc.reg %clk, %rst, %en, %a, %zero : i8
    %u = pyc.xor %q1, %q2 : i8, i8 -> i8
    func.return %u : i8
  }

  func.func @distinct_en(%clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %a: i8) -> i8 {
    %one = pyc.constant 1 : i1
    %zero = pyc.constant 0 : i8
    %q1 = pyc.reg %clk, %rst, %en, %a, %zero : i8
    %q2 = pyc.reg %clk, %rst, %one, %a, %zero : i8
    %u = pyc.xor %q1, %q2 : i8, i8 -> i8
    func.return %u : i8
  }

  func.func @distinct_init(%clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %a: i8) -> i8 {
    %zero = pyc.constant 0 : i8
    %one = pyc.constant 1 : i8
    %q1 = pyc.reg %clk, %rst, %en, %a, %zero : i8
    %q2 = pyc.reg %clk, %rst, %en, %a, %one : i8
    %u = pyc.xor %q1, %q2 : i8, i8 -> i8
    func.return %u : i8
  }

  func.func @follows_register(%clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %a: i8) -> i8 {
    %zero = pyc.constant 0 : i8
    %q1 = pyc.reg %clk, %rst, %en, %a, %zero : i8
    %q2 = pyc.reg %clk, %rst, %en, %q1, %zero : i8
    %q3 = pyc.reg %clk, %rst, %en, %q1, %zero : i8
    %u = pyc.xor %q2, %q3 : i8, i8 -> i8
    func.return %u : i8
  }

  func.func @hold_loop(%clk: !pyc.clock, %rst: !pyc.reset, %cond: i1, %in: i8) -> i8 {
    %w = pyc.wire : i8
    %one = pyc.constant 1 : i1
    %zero = pyc.constant 0 : i8
    %q = pyc.reg %clk, %rst, %one, %w, %zero : i8
    %n = pyc.xor %q, %in : i8, i8 -> i8
    %m = pyc.mux %cond, %n, %q : i1, i8, i8 -> i8
    pyc.assign %w, %m : i8
    %u = pyc.and %q, %in : i8, i8 -> i8
    func.return %u : i8
  }

  func.func @nonconst_init(%clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %a: i8, %k: i8) -> i8 {
    %q1 = pyc.reg %clk, %rst, %en, %a, %k : i8
    %q2 = pyc.reg %clk, %rst, %en, %a, %k : i8
    %u = pyc.xor %q1, %q2 : i8, i8 -> i8
    func.return %u : i8
  }

  func.func @named_copies(%clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %a: i8) -> i8 {
    %zero = pyc.constant 0 : i8
    %q1 = pyc.reg %clk, %rst, %en, %a, %zero : i8
    %q2 = pyc.reg %clk, %rst, %en, %a, %zero {pyc.name = "keep_me"} : i8
    %u = pyc.xor %q1, %q2 : i8, i8 -> i8
    func.return %u : i8
  }
}
