module attributes {pyc.top = @invalid_comb_effect, pyc.frontend.contract = "pycircuit"} {
  func.func @invalid_comb_effect(%cond: i1) -> i1 {
    %result = "pyc.comb"(%cond) ({
    ^bb0(%arg: i1):
      "pyc.assert"(%arg) {msg = "side effect inside comb"} : (i1) -> ()
      "pyc.yield"(%arg) : (i1) -> ()
    }) : (i1) -> i1
    return %result : i1
  }
}
