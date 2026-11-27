module attributes {pyc.frontend.contract = "pycircuit", pyc.top = @deep_chain} {
  func.func @deep_chain(%arg0: i1, %arg1: i117) -> i117 {
    %c0 = "pyc.constant"() {value = 0 : i117} : () -> i117
    %m1 = pyc.mux %arg0, %c0, %arg1 : i1, i117, i117 -> i117
    %m2 = pyc.mux %arg0, %c0, %m1 : i1, i117, i117 -> i117
    %m3 = pyc.mux %arg0, %c0, %m2 : i1, i117, i117 -> i117
    %m4 = pyc.mux %arg0, %c0, %m3 : i1, i117, i117 -> i117
    %m5 = pyc.mux %arg0, %c0, %m4 : i1, i117, i117 -> i117
    %m6 = pyc.mux %arg0, %c0, %m5 : i1, i117, i117 -> i117
    %m7 = pyc.mux %arg0, %c0, %m6 : i1, i117, i117 -> i117
    %m8 = pyc.mux %arg0, %c0, %m7 : i1, i117, i117 -> i117
    %m9 = pyc.mux %arg0, %c0, %m8 : i1, i117, i117 -> i117
    %m10 = pyc.mux %arg0, %c0, %m9 : i1, i117, i117 -> i117
    %m11 = pyc.mux %arg0, %c0, %m10 : i1, i117, i117 -> i117
    %m12 = pyc.mux %arg0, %c0, %m11 : i1, i117, i117 -> i117
    %m13 = pyc.mux %arg0, %c0, %m12 : i1, i117, i117 -> i117
    %m14 = pyc.mux %arg0, %c0, %m13 : i1, i117, i117 -> i117
    %m15 = pyc.mux %arg0, %c0, %m14 : i1, i117, i117 -> i117
    %m16 = pyc.mux %arg0, %c0, %m15 : i1, i117, i117 -> i117
    %m17 = pyc.mux %arg0, %c0, %m16 : i1, i117, i117 -> i117
    %m18 = pyc.mux %arg0, %c0, %m17 : i1, i117, i117 -> i117
    %m19 = pyc.mux %arg0, %c0, %m18 : i1, i117, i117 -> i117
    %m20 = pyc.mux %arg0, %c0, %m19 : i1, i117, i117 -> i117
    %m21 = pyc.mux %arg0, %c0, %m20 : i1, i117, i117 -> i117
    %m22 = pyc.mux %arg0, %c0, %m21 : i1, i117, i117 -> i117
    %m23 = pyc.mux %arg0, %c0, %m22 : i1, i117, i117 -> i117
    %m24 = pyc.mux %arg0, %c0, %m23 : i1, i117, i117 -> i117
    %m25 = pyc.mux %arg0, %c0, %m24 : i1, i117, i117 -> i117
    %m26 = pyc.mux %arg0, %c0, %m25 : i1, i117, i117 -> i117
    %m27 = pyc.mux %arg0, %c0, %m26 : i1, i117, i117 -> i117
    %m28 = pyc.mux %arg0, %c0, %m27 : i1, i117, i117 -> i117
    %m29 = pyc.mux %arg0, %c0, %m28 : i1, i117, i117 -> i117
    %m30 = pyc.mux %arg0, %c0, %m29 : i1, i117, i117 -> i117
    %m31 = pyc.mux %arg0, %c0, %m30 : i1, i117, i117 -> i117
    %m32 = pyc.mux %arg0, %c0, %m31 : i1, i117, i117 -> i117
    %m33 = pyc.mux %arg0, %c0, %m32 : i1, i117, i117 -> i117
    %m34 = pyc.mux %arg0, %c0, %m33 : i1, i117, i117 -> i117
    %m35 = pyc.mux %arg0, %c0, %m34 : i1, i117, i117 -> i117
    %m36 = pyc.mux %arg0, %c0, %m35 : i1, i117, i117 -> i117
    %m37 = pyc.mux %arg0, %c0, %m36 : i1, i117, i117 -> i117
    %m38 = pyc.mux %arg0, %c0, %m37 : i1, i117, i117 -> i117
    %m39 = pyc.mux %arg0, %c0, %m38 : i1, i117, i117 -> i117
    %m40 = pyc.mux %arg0, %c0, %m39 : i1, i117, i117 -> i117
    return %m40 : i117
  }
}
