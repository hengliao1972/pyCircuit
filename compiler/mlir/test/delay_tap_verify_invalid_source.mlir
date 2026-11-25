module {
  func.func @bad_tap_source(%clk: !pyc.clock, %rst: !pyc.reset, %in: i8) -> i8 {
    %en = pyc.constant 1 : i1
    %init = pyc.constant 0 : i8
    %reg = pyc.reg %clk, %rst, %en, %in, %init : i8
    // expected-error@+1 {{'pyc.delay_tap' op line must be defined by pyc.delay_line}}
    %tap = pyc.delay_tap %reg {depth = 1 : i64} : i8
    func.return %tap : i8
  }
}
