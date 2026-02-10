// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct issue_queue_2picker {
  pyc::cpp::Wire<1> sys_clk{};
  pyc::cpp::Wire<1> sys_rst{};
  pyc::cpp::Wire<1> in_valid{};
  pyc::cpp::Wire<8> in_data{};
  pyc::cpp::Wire<1> out0_ready{};
  pyc::cpp::Wire<1> out1_ready{};
  pyc::cpp::Wire<1> in_ready{};
  pyc::cpp::Wire<1> out0_valid{};
  pyc::cpp::Wire<8> out0_data{};
  pyc::cpp::Wire<1> out1_valid{};
  pyc::cpp::Wire<8> out1_data{};

  pyc::cpp::Wire<8> a1_d0__issue_queue_2picker__L55{};
  pyc::cpp::Wire<8> a1_d1__issue_queue_2picker__L56{};
  pyc::cpp::Wire<8> a1_d2__issue_queue_2picker__L57{};
  pyc::cpp::Wire<8> a1_d3__issue_queue_2picker__L58{};
  pyc::cpp::Wire<1> a1_v0__issue_queue_2picker__L51{};
  pyc::cpp::Wire<1> a1_v1__issue_queue_2picker__L52{};
  pyc::cpp::Wire<1> a1_v2__issue_queue_2picker__L53{};
  pyc::cpp::Wire<1> a1_v3__issue_queue_2picker__L54{};
  pyc::cpp::Wire<8> a2_d0__issue_queue_2picker__L73{};
  pyc::cpp::Wire<8> a2_d1__issue_queue_2picker__L74{};
  pyc::cpp::Wire<8> a2_d2__issue_queue_2picker__L75{};
  pyc::cpp::Wire<8> a2_d3__issue_queue_2picker__L76{};
  pyc::cpp::Wire<1> a2_v0__issue_queue_2picker__L69{};
  pyc::cpp::Wire<1> a2_v1__issue_queue_2picker__L70{};
  pyc::cpp::Wire<1> a2_v2__issue_queue_2picker__L71{};
  pyc::cpp::Wire<1> a2_v3__issue_queue_2picker__L72{};
  pyc::cpp::Wire<8> data0{};
  pyc::cpp::Wire<8> data0__issue_queue_2picker__L18{};
  pyc::cpp::Wire<8> data0__next{};
  pyc::cpp::Wire<8> data1{};
  pyc::cpp::Wire<8> data1__issue_queue_2picker__L19{};
  pyc::cpp::Wire<8> data1__next{};
  pyc::cpp::Wire<8> data2{};
  pyc::cpp::Wire<8> data2__issue_queue_2picker__L20{};
  pyc::cpp::Wire<8> data2__next{};
  pyc::cpp::Wire<8> data3{};
  pyc::cpp::Wire<8> data3__issue_queue_2picker__L21{};
  pyc::cpp::Wire<8> data3__next{};
  pyc::cpp::Wire<1> en0__issue_queue_2picker__L78{};
  pyc::cpp::Wire<1> en1__issue_queue_2picker__L79{};
  pyc::cpp::Wire<1> en2__issue_queue_2picker__L80{};
  pyc::cpp::Wire<1> en3__issue_queue_2picker__L81{};
  pyc::cpp::Wire<8> in_data__issue_queue_2picker__L10{};
  pyc::cpp::Wire<1> in_ready__issue_queue_2picker__L30{};
  pyc::cpp::Wire<1> in_valid__issue_queue_2picker__L9{};
  pyc::cpp::Wire<8> out0_data__issue_queue_2picker__L24{};
  pyc::cpp::Wire<1> out0_ready__issue_queue_2picker__L11{};
  pyc::cpp::Wire<1> out0_valid__issue_queue_2picker__L23{};
  pyc::cpp::Wire<8> out1_data__issue_queue_2picker__L26{};
  pyc::cpp::Wire<1> out1_ready__issue_queue_2picker__L12{};
  pyc::cpp::Wire<1> out1_valid__issue_queue_2picker__L25{};
  pyc::cpp::Wire<1> pop0__issue_queue_2picker__L28{};
  pyc::cpp::Wire<1> pop1__issue_queue_2picker__L29{};
  pyc::cpp::Wire<1> push__issue_queue_2picker__L31{};
  pyc::cpp::Wire<1> pyc_and_26{};
  pyc::cpp::Wire<1> pyc_and_27{};
  pyc::cpp::Wire<1> pyc_and_28{};
  pyc::cpp::Wire<1> pyc_and_31{};
  pyc::cpp::Wire<1> pyc_and_49{};
  pyc::cpp::Wire<1> pyc_and_50{};
  pyc::cpp::Wire<1> pyc_and_52{};
  pyc::cpp::Wire<1> pyc_and_53{};
  pyc::cpp::Wire<1> pyc_and_55{};
  pyc::cpp::Wire<1> pyc_and_56{};
  pyc::cpp::Wire<1> pyc_and_58{};
  pyc::cpp::Wire<1> pyc_comb_10{};
  pyc::cpp::Wire<1> pyc_comb_12{};
  pyc::cpp::Wire<1> pyc_comb_14{};
  pyc::cpp::Wire<1> pyc_comb_16{};
  pyc::cpp::Wire<1> pyc_comb_18{};
  pyc::cpp::Wire<8> pyc_comb_20{};
  pyc::cpp::Wire<8> pyc_comb_22{};
  pyc::cpp::Wire<8> pyc_comb_24{};
  pyc::cpp::Wire<8> pyc_comb_4{};
  pyc::cpp::Wire<1> pyc_comb_5{};
  pyc::cpp::Wire<1> pyc_comb_6{};
  pyc::cpp::Wire<1> pyc_comb_60{};
  pyc::cpp::Wire<8> pyc_comb_61{};
  pyc::cpp::Wire<1> pyc_comb_62{};
  pyc::cpp::Wire<8> pyc_comb_63{};
  pyc::cpp::Wire<1> pyc_comb_64{};
  pyc::cpp::Wire<1> pyc_comb_65{};
  pyc::cpp::Wire<1> pyc_comb_66{};
  pyc::cpp::Wire<1> pyc_comb_67{};
  pyc::cpp::Wire<8> pyc_comb_68{};
  pyc::cpp::Wire<8> pyc_comb_69{};
  pyc::cpp::Wire<1> pyc_comb_7{};
  pyc::cpp::Wire<8> pyc_comb_70{};
  pyc::cpp::Wire<8> pyc_comb_71{};
  pyc::cpp::Wire<1> pyc_comb_72{};
  pyc::cpp::Wire<1> pyc_comb_73{};
  pyc::cpp::Wire<1> pyc_comb_74{};
  pyc::cpp::Wire<1> pyc_comb_75{};
  pyc::cpp::Wire<1> pyc_comb_76{};
  pyc::cpp::Wire<8> pyc_comb_8{};
  pyc::cpp::Wire<1> pyc_comb_9{};
  pyc::cpp::Wire<8> pyc_constant_1{};
  pyc::cpp::Wire<1> pyc_constant_2{};
  pyc::cpp::Wire<1> pyc_constant_3{};
  pyc::cpp::Wire<1> pyc_mux_32{};
  pyc::cpp::Wire<1> pyc_mux_33{};
  pyc::cpp::Wire<1> pyc_mux_34{};
  pyc::cpp::Wire<1> pyc_mux_35{};
  pyc::cpp::Wire<8> pyc_mux_36{};
  pyc::cpp::Wire<8> pyc_mux_37{};
  pyc::cpp::Wire<8> pyc_mux_38{};
  pyc::cpp::Wire<8> pyc_mux_39{};
  pyc::cpp::Wire<1> pyc_mux_40{};
  pyc::cpp::Wire<1> pyc_mux_41{};
  pyc::cpp::Wire<1> pyc_mux_42{};
  pyc::cpp::Wire<1> pyc_mux_43{};
  pyc::cpp::Wire<8> pyc_mux_44{};
  pyc::cpp::Wire<8> pyc_mux_45{};
  pyc::cpp::Wire<8> pyc_mux_46{};
  pyc::cpp::Wire<8> pyc_mux_47{};
  pyc::cpp::Wire<8> pyc_mux_80{};
  pyc::cpp::Wire<8> pyc_mux_81{};
  pyc::cpp::Wire<8> pyc_mux_82{};
  pyc::cpp::Wire<8> pyc_mux_83{};
  pyc::cpp::Wire<1> pyc_not_29{};
  pyc::cpp::Wire<1> pyc_not_48{};
  pyc::cpp::Wire<1> pyc_not_51{};
  pyc::cpp::Wire<1> pyc_not_54{};
  pyc::cpp::Wire<1> pyc_not_57{};
  pyc::cpp::Wire<1> pyc_or_30{};
  pyc::cpp::Wire<1> pyc_or_59{};
  pyc::cpp::Wire<1> pyc_or_77{};
  pyc::cpp::Wire<1> pyc_or_78{};
  pyc::cpp::Wire<1> pyc_or_79{};
  pyc::cpp::Wire<1> pyc_reg_11{};
  pyc::cpp::Wire<1> pyc_reg_13{};
  pyc::cpp::Wire<1> pyc_reg_15{};
  pyc::cpp::Wire<1> pyc_reg_17{};
  pyc::cpp::Wire<8> pyc_reg_19{};
  pyc::cpp::Wire<8> pyc_reg_21{};
  pyc::cpp::Wire<8> pyc_reg_23{};
  pyc::cpp::Wire<8> pyc_reg_25{};
  pyc::cpp::Wire<8> s0_d0__issue_queue_2picker__L37{};
  pyc::cpp::Wire<8> s0_d1__issue_queue_2picker__L38{};
  pyc::cpp::Wire<8> s0_d2__issue_queue_2picker__L39{};
  pyc::cpp::Wire<8> s0_d3__issue_queue_2picker__L40{};
  pyc::cpp::Wire<1> s0_v0__issue_queue_2picker__L33{};
  pyc::cpp::Wire<1> s0_v1__issue_queue_2picker__L34{};
  pyc::cpp::Wire<1> s0_v2__issue_queue_2picker__L35{};
  pyc::cpp::Wire<1> s0_v3__issue_queue_2picker__L36{};
  pyc::cpp::Wire<8> s1_d0__issue_queue_2picker__L46{};
  pyc::cpp::Wire<8> s1_d1__issue_queue_2picker__L47{};
  pyc::cpp::Wire<8> s1_d2__issue_queue_2picker__L48{};
  pyc::cpp::Wire<8> s1_d3__issue_queue_2picker__L49{};
  pyc::cpp::Wire<1> s1_v0__issue_queue_2picker__L42{};
  pyc::cpp::Wire<1> s1_v1__issue_queue_2picker__L43{};
  pyc::cpp::Wire<1> s1_v2__issue_queue_2picker__L44{};
  pyc::cpp::Wire<1> s1_v3__issue_queue_2picker__L45{};
  pyc::cpp::Wire<8> s2_d0__issue_queue_2picker__L64{};
  pyc::cpp::Wire<8> s2_d1__issue_queue_2picker__L65{};
  pyc::cpp::Wire<8> s2_d2__issue_queue_2picker__L66{};
  pyc::cpp::Wire<8> s2_d3__issue_queue_2picker__L67{};
  pyc::cpp::Wire<1> s2_v0__issue_queue_2picker__L60{};
  pyc::cpp::Wire<1> s2_v1__issue_queue_2picker__L61{};
  pyc::cpp::Wire<1> s2_v2__issue_queue_2picker__L62{};
  pyc::cpp::Wire<1> s2_v3__issue_queue_2picker__L63{};
  pyc::cpp::Wire<1> val0{};
  pyc::cpp::Wire<1> val0__issue_queue_2picker__L14{};
  pyc::cpp::Wire<1> val0__next{};
  pyc::cpp::Wire<1> val1{};
  pyc::cpp::Wire<1> val1__issue_queue_2picker__L15{};
  pyc::cpp::Wire<1> val1__next{};
  pyc::cpp::Wire<1> val2{};
  pyc::cpp::Wire<1> val2__issue_queue_2picker__L16{};
  pyc::cpp::Wire<1> val2__next{};
  pyc::cpp::Wire<1> val3{};
  pyc::cpp::Wire<1> val3__issue_queue_2picker__L17{};
  pyc::cpp::Wire<1> val3__next{};

  pyc::cpp::pyc_reg<1> pyc_reg_11_inst;
  pyc::cpp::pyc_reg<1> pyc_reg_13_inst;
  pyc::cpp::pyc_reg<1> pyc_reg_15_inst;
  pyc::cpp::pyc_reg<1> pyc_reg_17_inst;
  pyc::cpp::pyc_reg<8> pyc_reg_19_inst;
  pyc::cpp::pyc_reg<8> pyc_reg_21_inst;
  pyc::cpp::pyc_reg<8> pyc_reg_23_inst;
  pyc::cpp::pyc_reg<8> pyc_reg_25_inst;

  issue_queue_2picker() :
      pyc_reg_11_inst(sys_clk, sys_rst, pyc_comb_6, val0__next, pyc_comb_5, pyc_reg_11),
      pyc_reg_13_inst(sys_clk, sys_rst, pyc_comb_6, val1__next, pyc_comb_5, pyc_reg_13),
      pyc_reg_15_inst(sys_clk, sys_rst, pyc_comb_6, val2__next, pyc_comb_5, pyc_reg_15),
      pyc_reg_17_inst(sys_clk, sys_rst, pyc_comb_6, val3__next, pyc_comb_5, pyc_reg_17),
      pyc_reg_19_inst(sys_clk, sys_rst, pyc_comb_6, data0__next, pyc_comb_4, pyc_reg_19),
      pyc_reg_21_inst(sys_clk, sys_rst, pyc_comb_6, data1__next, pyc_comb_4, pyc_reg_21),
      pyc_reg_23_inst(sys_clk, sys_rst, pyc_comb_6, data2__next, pyc_comb_4, pyc_reg_23),
      pyc_reg_25_inst(sys_clk, sys_rst, pyc_comb_6, data3__next, pyc_comb_4, pyc_reg_25) {
    eval();
  }

  inline void eval_comb_0() {
    val0 = pyc_reg_11;
    val0__issue_queue_2picker__L14 = val0;
    pyc_comb_12 = val0__issue_queue_2picker__L14;
  }

  inline void eval_comb_1() {
    val1 = pyc_reg_13;
    val1__issue_queue_2picker__L15 = val1;
    pyc_comb_14 = val1__issue_queue_2picker__L15;
  }

  inline void eval_comb_2() {
    val2 = pyc_reg_15;
    val2__issue_queue_2picker__L16 = val2;
    pyc_comb_16 = val2__issue_queue_2picker__L16;
  }

  inline void eval_comb_3() {
    val3 = pyc_reg_17;
    val3__issue_queue_2picker__L17 = val3;
    pyc_comb_18 = val3__issue_queue_2picker__L17;
  }

  inline void eval_comb_4() {
    data0 = pyc_reg_19;
    data0__issue_queue_2picker__L18 = data0;
    pyc_comb_20 = data0__issue_queue_2picker__L18;
  }

  inline void eval_comb_5() {
    data1 = pyc_reg_21;
    data1__issue_queue_2picker__L19 = data1;
    pyc_comb_22 = data1__issue_queue_2picker__L19;
  }

  inline void eval_comb_6() {
    data2 = pyc_reg_23;
    data2__issue_queue_2picker__L20 = data2;
    pyc_comb_24 = data2__issue_queue_2picker__L20;
  }

  inline void eval_comb_7() {
    pyc_constant_1 = pyc::cpp::Wire<8>({0x0ull});
    pyc_constant_2 = pyc::cpp::Wire<1>({0x0ull});
    pyc_constant_3 = pyc::cpp::Wire<1>({0x1ull});
    in_valid__issue_queue_2picker__L9 = in_valid;
    in_data__issue_queue_2picker__L10 = in_data;
    out0_ready__issue_queue_2picker__L11 = out0_ready;
    out1_ready__issue_queue_2picker__L12 = out1_ready;
    pyc_comb_4 = pyc_constant_1;
    pyc_comb_5 = pyc_constant_2;
    pyc_comb_6 = pyc_constant_3;
    pyc_comb_7 = in_valid__issue_queue_2picker__L9;
    pyc_comb_8 = in_data__issue_queue_2picker__L10;
    pyc_comb_9 = out0_ready__issue_queue_2picker__L11;
    pyc_comb_10 = out1_ready__issue_queue_2picker__L12;
  }

  inline void eval_comb_8() {
    data3 = pyc_reg_25;
    data3__issue_queue_2picker__L21 = data3;
    out0_valid__issue_queue_2picker__L23 = pyc_comb_12;
    out0_data__issue_queue_2picker__L24 = pyc_comb_20;
    out1_valid__issue_queue_2picker__L25 = pyc_comb_14;
    out1_data__issue_queue_2picker__L26 = pyc_comb_22;
    pyc_and_26 = (out0_valid__issue_queue_2picker__L23 & pyc_comb_9);
    pop0__issue_queue_2picker__L28 = pyc_and_26;
    pyc_and_27 = (out1_valid__issue_queue_2picker__L25 & pyc_comb_10);
    pyc_and_28 = (pyc_and_27 & pop0__issue_queue_2picker__L28);
    pop1__issue_queue_2picker__L29 = pyc_and_28;
    pyc_not_29 = (~pyc_comb_18);
    pyc_or_30 = (pyc_not_29 | pop0__issue_queue_2picker__L28);
    in_ready__issue_queue_2picker__L30 = pyc_or_30;
    pyc_and_31 = (pyc_comb_7 & in_ready__issue_queue_2picker__L30);
    push__issue_queue_2picker__L31 = pyc_and_31;
    s0_v0__issue_queue_2picker__L33 = pyc_comb_12;
    s0_v1__issue_queue_2picker__L34 = pyc_comb_14;
    s0_v2__issue_queue_2picker__L35 = pyc_comb_16;
    s0_v3__issue_queue_2picker__L36 = pyc_comb_18;
    s0_d0__issue_queue_2picker__L37 = pyc_comb_20;
    s0_d1__issue_queue_2picker__L38 = pyc_comb_22;
    s0_d2__issue_queue_2picker__L39 = pyc_comb_24;
    s0_d3__issue_queue_2picker__L40 = data3__issue_queue_2picker__L21;
    s1_v0__issue_queue_2picker__L42 = s0_v1__issue_queue_2picker__L34;
    s1_v1__issue_queue_2picker__L43 = s0_v2__issue_queue_2picker__L35;
    s1_v2__issue_queue_2picker__L44 = s0_v3__issue_queue_2picker__L36;
    s1_v3__issue_queue_2picker__L45 = pyc_comb_5;
    s1_d0__issue_queue_2picker__L46 = s0_d1__issue_queue_2picker__L38;
    s1_d1__issue_queue_2picker__L47 = s0_d2__issue_queue_2picker__L39;
    s1_d2__issue_queue_2picker__L48 = s0_d3__issue_queue_2picker__L40;
    s1_d3__issue_queue_2picker__L49 = s0_d3__issue_queue_2picker__L40;
    pyc_mux_32 = (pop0__issue_queue_2picker__L28.toBool() ? s1_v0__issue_queue_2picker__L42 : s0_v0__issue_queue_2picker__L33);
    a1_v0__issue_queue_2picker__L51 = pyc_mux_32;
    pyc_mux_33 = (pop0__issue_queue_2picker__L28.toBool() ? s1_v1__issue_queue_2picker__L43 : s0_v1__issue_queue_2picker__L34);
    a1_v1__issue_queue_2picker__L52 = pyc_mux_33;
    pyc_mux_34 = (pop0__issue_queue_2picker__L28.toBool() ? s1_v2__issue_queue_2picker__L44 : s0_v2__issue_queue_2picker__L35);
    a1_v2__issue_queue_2picker__L53 = pyc_mux_34;
    pyc_mux_35 = (pop0__issue_queue_2picker__L28.toBool() ? s1_v3__issue_queue_2picker__L45 : s0_v3__issue_queue_2picker__L36);
    a1_v3__issue_queue_2picker__L54 = pyc_mux_35;
    pyc_mux_36 = (pop0__issue_queue_2picker__L28.toBool() ? s1_d0__issue_queue_2picker__L46 : s0_d0__issue_queue_2picker__L37);
    a1_d0__issue_queue_2picker__L55 = pyc_mux_36;
    pyc_mux_37 = (pop0__issue_queue_2picker__L28.toBool() ? s1_d1__issue_queue_2picker__L47 : s0_d1__issue_queue_2picker__L38);
    a1_d1__issue_queue_2picker__L56 = pyc_mux_37;
    pyc_mux_38 = (pop0__issue_queue_2picker__L28.toBool() ? s1_d2__issue_queue_2picker__L48 : s0_d2__issue_queue_2picker__L39);
    a1_d2__issue_queue_2picker__L57 = pyc_mux_38;
    pyc_mux_39 = (pop0__issue_queue_2picker__L28.toBool() ? s1_d3__issue_queue_2picker__L49 : s0_d3__issue_queue_2picker__L40);
    a1_d3__issue_queue_2picker__L58 = pyc_mux_39;
    s2_v0__issue_queue_2picker__L60 = a1_v1__issue_queue_2picker__L52;
    s2_v1__issue_queue_2picker__L61 = a1_v2__issue_queue_2picker__L53;
    s2_v2__issue_queue_2picker__L62 = a1_v3__issue_queue_2picker__L54;
    s2_v3__issue_queue_2picker__L63 = pyc_comb_5;
    s2_d0__issue_queue_2picker__L64 = a1_d1__issue_queue_2picker__L56;
    s2_d1__issue_queue_2picker__L65 = a1_d2__issue_queue_2picker__L57;
    s2_d2__issue_queue_2picker__L66 = a1_d3__issue_queue_2picker__L58;
    s2_d3__issue_queue_2picker__L67 = a1_d3__issue_queue_2picker__L58;
    pyc_mux_40 = (pop1__issue_queue_2picker__L29.toBool() ? s2_v0__issue_queue_2picker__L60 : a1_v0__issue_queue_2picker__L51);
    a2_v0__issue_queue_2picker__L69 = pyc_mux_40;
    pyc_mux_41 = (pop1__issue_queue_2picker__L29.toBool() ? s2_v1__issue_queue_2picker__L61 : a1_v1__issue_queue_2picker__L52);
    a2_v1__issue_queue_2picker__L70 = pyc_mux_41;
    pyc_mux_42 = (pop1__issue_queue_2picker__L29.toBool() ? s2_v2__issue_queue_2picker__L62 : a1_v2__issue_queue_2picker__L53);
    a2_v2__issue_queue_2picker__L71 = pyc_mux_42;
    pyc_mux_43 = (pop1__issue_queue_2picker__L29.toBool() ? s2_v3__issue_queue_2picker__L63 : a1_v3__issue_queue_2picker__L54);
    a2_v3__issue_queue_2picker__L72 = pyc_mux_43;
    pyc_mux_44 = (pop1__issue_queue_2picker__L29.toBool() ? s2_d0__issue_queue_2picker__L64 : a1_d0__issue_queue_2picker__L55);
    a2_d0__issue_queue_2picker__L73 = pyc_mux_44;
    pyc_mux_45 = (pop1__issue_queue_2picker__L29.toBool() ? s2_d1__issue_queue_2picker__L65 : a1_d1__issue_queue_2picker__L56);
    a2_d1__issue_queue_2picker__L74 = pyc_mux_45;
    pyc_mux_46 = (pop1__issue_queue_2picker__L29.toBool() ? s2_d2__issue_queue_2picker__L66 : a1_d2__issue_queue_2picker__L57);
    a2_d2__issue_queue_2picker__L75 = pyc_mux_46;
    pyc_mux_47 = (pop1__issue_queue_2picker__L29.toBool() ? s2_d3__issue_queue_2picker__L67 : a1_d3__issue_queue_2picker__L58);
    a2_d3__issue_queue_2picker__L76 = pyc_mux_47;
    pyc_not_48 = (~a2_v0__issue_queue_2picker__L69);
    pyc_and_49 = (push__issue_queue_2picker__L31 & pyc_not_48);
    en0__issue_queue_2picker__L78 = pyc_and_49;
    pyc_and_50 = (push__issue_queue_2picker__L31 & a2_v0__issue_queue_2picker__L69);
    pyc_not_51 = (~a2_v1__issue_queue_2picker__L70);
    pyc_and_52 = (pyc_and_50 & pyc_not_51);
    en1__issue_queue_2picker__L79 = pyc_and_52;
    pyc_and_53 = (pyc_and_50 & a2_v1__issue_queue_2picker__L70);
    pyc_not_54 = (~a2_v2__issue_queue_2picker__L71);
    pyc_and_55 = (pyc_and_53 & pyc_not_54);
    en2__issue_queue_2picker__L80 = pyc_and_55;
    pyc_and_56 = (pyc_and_53 & a2_v2__issue_queue_2picker__L71);
    pyc_not_57 = (~a2_v3__issue_queue_2picker__L72);
    pyc_and_58 = (pyc_and_56 & pyc_not_57);
    en3__issue_queue_2picker__L81 = pyc_and_58;
    pyc_or_59 = (a2_v0__issue_queue_2picker__L69 | en0__issue_queue_2picker__L78);
    pyc_comb_60 = out0_valid__issue_queue_2picker__L23;
    pyc_comb_61 = out0_data__issue_queue_2picker__L24;
    pyc_comb_62 = out1_valid__issue_queue_2picker__L25;
    pyc_comb_63 = out1_data__issue_queue_2picker__L26;
    pyc_comb_64 = in_ready__issue_queue_2picker__L30;
    pyc_comb_65 = a2_v1__issue_queue_2picker__L70;
    pyc_comb_66 = a2_v2__issue_queue_2picker__L71;
    pyc_comb_67 = a2_v3__issue_queue_2picker__L72;
    pyc_comb_68 = a2_d0__issue_queue_2picker__L73;
    pyc_comb_69 = a2_d1__issue_queue_2picker__L74;
    pyc_comb_70 = a2_d2__issue_queue_2picker__L75;
    pyc_comb_71 = a2_d3__issue_queue_2picker__L76;
    pyc_comb_72 = en0__issue_queue_2picker__L78;
    pyc_comb_73 = en1__issue_queue_2picker__L79;
    pyc_comb_74 = en2__issue_queue_2picker__L80;
    pyc_comb_75 = en3__issue_queue_2picker__L81;
    pyc_comb_76 = pyc_or_59;
  }

  inline void eval_comb_pass() {
    eval_comb_0();
    eval_comb_1();
    eval_comb_2();
    eval_comb_3();
    eval_comb_4();
    eval_comb_5();
    eval_comb_6();
    eval_comb_7();
    eval_comb_8();
    pyc_mux_80 = (pyc_comb_72.toBool() ? pyc_comb_8 : pyc_comb_68);
    data0__next = pyc_mux_80;
    pyc_mux_81 = (pyc_comb_73.toBool() ? pyc_comb_8 : pyc_comb_69);
    data1__next = pyc_mux_81;
    pyc_mux_82 = (pyc_comb_74.toBool() ? pyc_comb_8 : pyc_comb_70);
    data2__next = pyc_mux_82;
    pyc_mux_83 = (pyc_comb_75.toBool() ? pyc_comb_8 : pyc_comb_71);
    data3__next = pyc_mux_83;
    pyc_or_77 = (pyc_comb_65 | pyc_comb_73);
    pyc_or_78 = (pyc_comb_66 | pyc_comb_74);
    pyc_or_79 = (pyc_comb_67 | pyc_comb_75);
    val0__next = pyc_comb_76;
    val1__next = pyc_or_77;
    val2__next = pyc_or_78;
    val3__next = pyc_or_79;
  }

  void eval() {
    eval_comb_0();
    eval_comb_1();
    eval_comb_2();
    eval_comb_3();
    eval_comb_4();
    eval_comb_5();
    eval_comb_6();
    eval_comb_7();
    eval_comb_8();
    pyc_mux_80 = (pyc_comb_72.toBool() ? pyc_comb_8 : pyc_comb_68);
    data0__next = pyc_mux_80;
    pyc_mux_81 = (pyc_comb_73.toBool() ? pyc_comb_8 : pyc_comb_69);
    data1__next = pyc_mux_81;
    pyc_mux_82 = (pyc_comb_74.toBool() ? pyc_comb_8 : pyc_comb_70);
    data2__next = pyc_mux_82;
    pyc_mux_83 = (pyc_comb_75.toBool() ? pyc_comb_8 : pyc_comb_71);
    data3__next = pyc_mux_83;
    pyc_or_77 = (pyc_comb_65 | pyc_comb_73);
    pyc_or_78 = (pyc_comb_66 | pyc_comb_74);
    pyc_or_79 = (pyc_comb_67 | pyc_comb_75);
    val0__next = pyc_comb_76;
    val1__next = pyc_or_77;
    val2__next = pyc_or_78;
    val3__next = pyc_or_79;
    in_ready = pyc_comb_64;
    out0_valid = pyc_comb_60;
    out0_data = pyc_comb_61;
    out1_valid = pyc_comb_62;
    out1_data = pyc_comb_63;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_reg_11_inst.tick_compute();
    pyc_reg_13_inst.tick_compute();
    pyc_reg_15_inst.tick_compute();
    pyc_reg_17_inst.tick_compute();
    pyc_reg_19_inst.tick_compute();
    pyc_reg_21_inst.tick_compute();
    pyc_reg_23_inst.tick_compute();
    pyc_reg_25_inst.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_reg_11_inst.tick_commit();
    pyc_reg_13_inst.tick_commit();
    pyc_reg_15_inst.tick_commit();
    pyc_reg_17_inst.tick_commit();
    pyc_reg_19_inst.tick_commit();
    pyc_reg_21_inst.tick_commit();
    pyc_reg_23_inst.tick_commit();
    pyc_reg_25_inst.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
