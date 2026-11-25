// Verifier rejection: a tap must reference a delay line and stay in range.
module {
  func.func @bad_tap_depth(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %line = pyc.delay_line %clk, %rst, %en, %in, %init {depth = 3 : i64} : i8
    // expected-error@+1 {{'pyc.delay_tap' op tap depth must not exceed delay_line depth}}
    %tap = pyc.delay_tap %line {depth = 4 : i64} : i8
    func.return %tap : i8
  }
}
