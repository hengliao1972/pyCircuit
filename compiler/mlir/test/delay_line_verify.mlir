// Positive/negative coverage for the pyc.delay_line verifier.
module {
  // A valid fixed-latency sequential delay line.
  func.func @delay_line_valid(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %q = pyc.delay_line %clk, %rst, %en, %in, %init {depth = 4 : i64} : i8
    func.return %q : i8
  }

  // A delay line represents at least two register stages.
  func.func @depth_one(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    // expected-error@+1 {{'pyc.delay_line' op depth must be > 1}}
    %q = pyc.delay_line %clk, %rst, %en, %in, %init {depth = 1 : i64} : i8
    func.return %q : i8
  }
}
