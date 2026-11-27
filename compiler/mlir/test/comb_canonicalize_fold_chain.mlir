module attributes {pyc.frontend.contract = "pycircuit", pyc.top = @fold_chain} {
  func.func @fold_chain(%arg0: i1, %arg1: i117) -> i117 {
    %c0 = "pyc.constant"() {value = 255 : i511} : () -> i511
    %t1 = pyc.trunc %c0 : i511 -> i1
    %z1 = pyc.zext %t1 : i1 -> i117
    %t2 = pyc.trunc %z1 : i117 -> i1
    %z2 = pyc.zext %t2 : i1 -> i117
    %t3 = pyc.trunc %z2 : i117 -> i1
    %z3 = pyc.zext %t3 : i1 -> i117
    %t4 = pyc.trunc %z3 : i117 -> i1
    %z4 = pyc.zext %t4 : i1 -> i117
    %t5 = pyc.trunc %z4 : i117 -> i1
    %z5 = pyc.zext %t5 : i1 -> i117
    %t6 = pyc.trunc %z5 : i117 -> i1
    %z6 = pyc.zext %t6 : i1 -> i117
    %t7 = pyc.trunc %z6 : i117 -> i1
    %z7 = pyc.zext %t7 : i1 -> i117
    %t8 = pyc.trunc %z7 : i117 -> i1
    %z8 = pyc.zext %t8 : i1 -> i117
    %t9 = pyc.trunc %z8 : i117 -> i1
    %z9 = pyc.zext %t9 : i1 -> i117
    %t10 = pyc.trunc %z9 : i117 -> i1
    %z10 = pyc.zext %t10 : i1 -> i117
    %m = pyc.mux %arg0, %z10, %arg1 : i1, i117, i117 -> i117
    return %m : i117
  }
}
