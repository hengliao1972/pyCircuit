module tb_janus_tmu_pyc;
  logic clk;
  logic rst;

  logic req_valid [0:7];
  logic req_write [0:7];
  logic [19:0] req_addr [0:7];
  logic [7:0] req_tag [0:7];
  logic [63:0] req_data [0:7][0:31];
  logic req_ready [0:7];

  logic resp_ready [0:7];
  logic resp_valid [0:7];
  logic [7:0] resp_tag [0:7];
  logic [63:0] resp_data [0:7][0:31];
  logic resp_is_write [0:7];

  logic [63:0] line_data [0:31];
  logic [63:0] line_zero [0:31];

  janus_tmu_pyc dut (
      .clk(clk),
      .rst(rst),
      .n0_req_valid(req_valid[0]),
      .n0_req_write(req_write[0]),
      .n0_req_addr(req_addr[0]),
      .n0_req_tag(req_tag[0]),
      .n0_req_data_w0(req_data[0][0]),
      .n0_req_data_w1(req_data[0][1]),
      .n0_req_data_w2(req_data[0][2]),
      .n0_req_data_w3(req_data[0][3]),
      .n0_req_data_w4(req_data[0][4]),
      .n0_req_data_w5(req_data[0][5]),
      .n0_req_data_w6(req_data[0][6]),
      .n0_req_data_w7(req_data[0][7]),
      .n0_req_data_w8(req_data[0][8]),
      .n0_req_data_w9(req_data[0][9]),
      .n0_req_data_w10(req_data[0][10]),
      .n0_req_data_w11(req_data[0][11]),
      .n0_req_data_w12(req_data[0][12]),
      .n0_req_data_w13(req_data[0][13]),
      .n0_req_data_w14(req_data[0][14]),
      .n0_req_data_w15(req_data[0][15]),
      .n0_req_data_w16(req_data[0][16]),
      .n0_req_data_w17(req_data[0][17]),
      .n0_req_data_w18(req_data[0][18]),
      .n0_req_data_w19(req_data[0][19]),
      .n0_req_data_w20(req_data[0][20]),
      .n0_req_data_w21(req_data[0][21]),
      .n0_req_data_w22(req_data[0][22]),
      .n0_req_data_w23(req_data[0][23]),
      .n0_req_data_w24(req_data[0][24]),
      .n0_req_data_w25(req_data[0][25]),
      .n0_req_data_w26(req_data[0][26]),
      .n0_req_data_w27(req_data[0][27]),
      .n0_req_data_w28(req_data[0][28]),
      .n0_req_data_w29(req_data[0][29]),
      .n0_req_data_w30(req_data[0][30]),
      .n0_req_data_w31(req_data[0][31]),
      .n0_req_ready(req_ready[0]),
      .n0_resp_ready(resp_ready[0]),
      .n0_resp_valid(resp_valid[0]),
      .n0_resp_tag(resp_tag[0]),
      .n0_resp_data_w0(resp_data[0][0]),
      .n0_resp_data_w1(resp_data[0][1]),
      .n0_resp_data_w2(resp_data[0][2]),
      .n0_resp_data_w3(resp_data[0][3]),
      .n0_resp_data_w4(resp_data[0][4]),
      .n0_resp_data_w5(resp_data[0][5]),
      .n0_resp_data_w6(resp_data[0][6]),
      .n0_resp_data_w7(resp_data[0][7]),
      .n0_resp_data_w8(resp_data[0][8]),
      .n0_resp_data_w9(resp_data[0][9]),
      .n0_resp_data_w10(resp_data[0][10]),
      .n0_resp_data_w11(resp_data[0][11]),
      .n0_resp_data_w12(resp_data[0][12]),
      .n0_resp_data_w13(resp_data[0][13]),
      .n0_resp_data_w14(resp_data[0][14]),
      .n0_resp_data_w15(resp_data[0][15]),
      .n0_resp_data_w16(resp_data[0][16]),
      .n0_resp_data_w17(resp_data[0][17]),
      .n0_resp_data_w18(resp_data[0][18]),
      .n0_resp_data_w19(resp_data[0][19]),
      .n0_resp_data_w20(resp_data[0][20]),
      .n0_resp_data_w21(resp_data[0][21]),
      .n0_resp_data_w22(resp_data[0][22]),
      .n0_resp_data_w23(resp_data[0][23]),
      .n0_resp_data_w24(resp_data[0][24]),
      .n0_resp_data_w25(resp_data[0][25]),
      .n0_resp_data_w26(resp_data[0][26]),
      .n0_resp_data_w27(resp_data[0][27]),
      .n0_resp_data_w28(resp_data[0][28]),
      .n0_resp_data_w29(resp_data[0][29]),
      .n0_resp_data_w30(resp_data[0][30]),
      .n0_resp_data_w31(resp_data[0][31]),
      .n0_resp_is_write(resp_is_write[0]),

      .n1_req_valid(req_valid[1]),
      .n1_req_write(req_write[1]),
      .n1_req_addr(req_addr[1]),
      .n1_req_tag(req_tag[1]),
      .n1_req_data_w0(req_data[1][0]),
      .n1_req_data_w1(req_data[1][1]),
      .n1_req_data_w2(req_data[1][2]),
      .n1_req_data_w3(req_data[1][3]),
      .n1_req_data_w4(req_data[1][4]),
      .n1_req_data_w5(req_data[1][5]),
      .n1_req_data_w6(req_data[1][6]),
      .n1_req_data_w7(req_data[1][7]),
      .n1_req_data_w8(req_data[1][8]),
      .n1_req_data_w9(req_data[1][9]),
      .n1_req_data_w10(req_data[1][10]),
      .n1_req_data_w11(req_data[1][11]),
      .n1_req_data_w12(req_data[1][12]),
      .n1_req_data_w13(req_data[1][13]),
      .n1_req_data_w14(req_data[1][14]),
      .n1_req_data_w15(req_data[1][15]),
      .n1_req_data_w16(req_data[1][16]),
      .n1_req_data_w17(req_data[1][17]),
      .n1_req_data_w18(req_data[1][18]),
      .n1_req_data_w19(req_data[1][19]),
      .n1_req_data_w20(req_data[1][20]),
      .n1_req_data_w21(req_data[1][21]),
      .n1_req_data_w22(req_data[1][22]),
      .n1_req_data_w23(req_data[1][23]),
      .n1_req_data_w24(req_data[1][24]),
      .n1_req_data_w25(req_data[1][25]),
      .n1_req_data_w26(req_data[1][26]),
      .n1_req_data_w27(req_data[1][27]),
      .n1_req_data_w28(req_data[1][28]),
      .n1_req_data_w29(req_data[1][29]),
      .n1_req_data_w30(req_data[1][30]),
      .n1_req_data_w31(req_data[1][31]),
      .n1_req_ready(req_ready[1]),
      .n1_resp_ready(resp_ready[1]),
      .n1_resp_valid(resp_valid[1]),
      .n1_resp_tag(resp_tag[1]),
      .n1_resp_data_w0(resp_data[1][0]),
      .n1_resp_data_w1(resp_data[1][1]),
      .n1_resp_data_w2(resp_data[1][2]),
      .n1_resp_data_w3(resp_data[1][3]),
      .n1_resp_data_w4(resp_data[1][4]),
      .n1_resp_data_w5(resp_data[1][5]),
      .n1_resp_data_w6(resp_data[1][6]),
      .n1_resp_data_w7(resp_data[1][7]),
      .n1_resp_data_w8(resp_data[1][8]),
      .n1_resp_data_w9(resp_data[1][9]),
      .n1_resp_data_w10(resp_data[1][10]),
      .n1_resp_data_w11(resp_data[1][11]),
      .n1_resp_data_w12(resp_data[1][12]),
      .n1_resp_data_w13(resp_data[1][13]),
      .n1_resp_data_w14(resp_data[1][14]),
      .n1_resp_data_w15(resp_data[1][15]),
      .n1_resp_data_w16(resp_data[1][16]),
      .n1_resp_data_w17(resp_data[1][17]),
      .n1_resp_data_w18(resp_data[1][18]),
      .n1_resp_data_w19(resp_data[1][19]),
      .n1_resp_data_w20(resp_data[1][20]),
      .n1_resp_data_w21(resp_data[1][21]),
      .n1_resp_data_w22(resp_data[1][22]),
      .n1_resp_data_w23(resp_data[1][23]),
      .n1_resp_data_w24(resp_data[1][24]),
      .n1_resp_data_w25(resp_data[1][25]),
      .n1_resp_data_w26(resp_data[1][26]),
      .n1_resp_data_w27(resp_data[1][27]),
      .n1_resp_data_w28(resp_data[1][28]),
      .n1_resp_data_w29(resp_data[1][29]),
      .n1_resp_data_w30(resp_data[1][30]),
      .n1_resp_data_w31(resp_data[1][31]),
      .n1_resp_is_write(resp_is_write[1]),

      .n2_req_valid(req_valid[2]),
      .n2_req_write(req_write[2]),
      .n2_req_addr(req_addr[2]),
      .n2_req_tag(req_tag[2]),
      .n2_req_data_w0(req_data[2][0]),
      .n2_req_data_w1(req_data[2][1]),
      .n2_req_data_w2(req_data[2][2]),
      .n2_req_data_w3(req_data[2][3]),
      .n2_req_data_w4(req_data[2][4]),
      .n2_req_data_w5(req_data[2][5]),
      .n2_req_data_w6(req_data[2][6]),
      .n2_req_data_w7(req_data[2][7]),
      .n2_req_data_w8(req_data[2][8]),
      .n2_req_data_w9(req_data[2][9]),
      .n2_req_data_w10(req_data[2][10]),
      .n2_req_data_w11(req_data[2][11]),
      .n2_req_data_w12(req_data[2][12]),
      .n2_req_data_w13(req_data[2][13]),
      .n2_req_data_w14(req_data[2][14]),
      .n2_req_data_w15(req_data[2][15]),
      .n2_req_data_w16(req_data[2][16]),
      .n2_req_data_w17(req_data[2][17]),
      .n2_req_data_w18(req_data[2][18]),
      .n2_req_data_w19(req_data[2][19]),
      .n2_req_data_w20(req_data[2][20]),
      .n2_req_data_w21(req_data[2][21]),
      .n2_req_data_w22(req_data[2][22]),
      .n2_req_data_w23(req_data[2][23]),
      .n2_req_data_w24(req_data[2][24]),
      .n2_req_data_w25(req_data[2][25]),
      .n2_req_data_w26(req_data[2][26]),
      .n2_req_data_w27(req_data[2][27]),
      .n2_req_data_w28(req_data[2][28]),
      .n2_req_data_w29(req_data[2][29]),
      .n2_req_data_w30(req_data[2][30]),
      .n2_req_data_w31(req_data[2][31]),
      .n2_req_ready(req_ready[2]),
      .n2_resp_ready(resp_ready[2]),
      .n2_resp_valid(resp_valid[2]),
      .n2_resp_tag(resp_tag[2]),
      .n2_resp_data_w0(resp_data[2][0]),
      .n2_resp_data_w1(resp_data[2][1]),
      .n2_resp_data_w2(resp_data[2][2]),
      .n2_resp_data_w3(resp_data[2][3]),
      .n2_resp_data_w4(resp_data[2][4]),
      .n2_resp_data_w5(resp_data[2][5]),
      .n2_resp_data_w6(resp_data[2][6]),
      .n2_resp_data_w7(resp_data[2][7]),
      .n2_resp_data_w8(resp_data[2][8]),
      .n2_resp_data_w9(resp_data[2][9]),
      .n2_resp_data_w10(resp_data[2][10]),
      .n2_resp_data_w11(resp_data[2][11]),
      .n2_resp_data_w12(resp_data[2][12]),
      .n2_resp_data_w13(resp_data[2][13]),
      .n2_resp_data_w14(resp_data[2][14]),
      .n2_resp_data_w15(resp_data[2][15]),
      .n2_resp_data_w16(resp_data[2][16]),
      .n2_resp_data_w17(resp_data[2][17]),
      .n2_resp_data_w18(resp_data[2][18]),
      .n2_resp_data_w19(resp_data[2][19]),
      .n2_resp_data_w20(resp_data[2][20]),
      .n2_resp_data_w21(resp_data[2][21]),
      .n2_resp_data_w22(resp_data[2][22]),
      .n2_resp_data_w23(resp_data[2][23]),
      .n2_resp_data_w24(resp_data[2][24]),
      .n2_resp_data_w25(resp_data[2][25]),
      .n2_resp_data_w26(resp_data[2][26]),
      .n2_resp_data_w27(resp_data[2][27]),
      .n2_resp_data_w28(resp_data[2][28]),
      .n2_resp_data_w29(resp_data[2][29]),
      .n2_resp_data_w30(resp_data[2][30]),
      .n2_resp_data_w31(resp_data[2][31]),
      .n2_resp_is_write(resp_is_write[2]),

      .n3_req_valid(req_valid[3]),
      .n3_req_write(req_write[3]),
      .n3_req_addr(req_addr[3]),
      .n3_req_tag(req_tag[3]),
      .n3_req_data_w0(req_data[3][0]),
      .n3_req_data_w1(req_data[3][1]),
      .n3_req_data_w2(req_data[3][2]),
      .n3_req_data_w3(req_data[3][3]),
      .n3_req_data_w4(req_data[3][4]),
      .n3_req_data_w5(req_data[3][5]),
      .n3_req_data_w6(req_data[3][6]),
      .n3_req_data_w7(req_data[3][7]),
      .n3_req_data_w8(req_data[3][8]),
      .n3_req_data_w9(req_data[3][9]),
      .n3_req_data_w10(req_data[3][10]),
      .n3_req_data_w11(req_data[3][11]),
      .n3_req_data_w12(req_data[3][12]),
      .n3_req_data_w13(req_data[3][13]),
      .n3_req_data_w14(req_data[3][14]),
      .n3_req_data_w15(req_data[3][15]),
      .n3_req_data_w16(req_data[3][16]),
      .n3_req_data_w17(req_data[3][17]),
      .n3_req_data_w18(req_data[3][18]),
      .n3_req_data_w19(req_data[3][19]),
      .n3_req_data_w20(req_data[3][20]),
      .n3_req_data_w21(req_data[3][21]),
      .n3_req_data_w22(req_data[3][22]),
      .n3_req_data_w23(req_data[3][23]),
      .n3_req_data_w24(req_data[3][24]),
      .n3_req_data_w25(req_data[3][25]),
      .n3_req_data_w26(req_data[3][26]),
      .n3_req_data_w27(req_data[3][27]),
      .n3_req_data_w28(req_data[3][28]),
      .n3_req_data_w29(req_data[3][29]),
      .n3_req_data_w30(req_data[3][30]),
      .n3_req_data_w31(req_data[3][31]),
      .n3_req_ready(req_ready[3]),
      .n3_resp_ready(resp_ready[3]),
      .n3_resp_valid(resp_valid[3]),
      .n3_resp_tag(resp_tag[3]),
      .n3_resp_data_w0(resp_data[3][0]),
      .n3_resp_data_w1(resp_data[3][1]),
      .n3_resp_data_w2(resp_data[3][2]),
      .n3_resp_data_w3(resp_data[3][3]),
      .n3_resp_data_w4(resp_data[3][4]),
      .n3_resp_data_w5(resp_data[3][5]),
      .n3_resp_data_w6(resp_data[3][6]),
      .n3_resp_data_w7(resp_data[3][7]),
      .n3_resp_data_w8(resp_data[3][8]),
      .n3_resp_data_w9(resp_data[3][9]),
      .n3_resp_data_w10(resp_data[3][10]),
      .n3_resp_data_w11(resp_data[3][11]),
      .n3_resp_data_w12(resp_data[3][12]),
      .n3_resp_data_w13(resp_data[3][13]),
      .n3_resp_data_w14(resp_data[3][14]),
      .n3_resp_data_w15(resp_data[3][15]),
      .n3_resp_data_w16(resp_data[3][16]),
      .n3_resp_data_w17(resp_data[3][17]),
      .n3_resp_data_w18(resp_data[3][18]),
      .n3_resp_data_w19(resp_data[3][19]),
      .n3_resp_data_w20(resp_data[3][20]),
      .n3_resp_data_w21(resp_data[3][21]),
      .n3_resp_data_w22(resp_data[3][22]),
      .n3_resp_data_w23(resp_data[3][23]),
      .n3_resp_data_w24(resp_data[3][24]),
      .n3_resp_data_w25(resp_data[3][25]),
      .n3_resp_data_w26(resp_data[3][26]),
      .n3_resp_data_w27(resp_data[3][27]),
      .n3_resp_data_w28(resp_data[3][28]),
      .n3_resp_data_w29(resp_data[3][29]),
      .n3_resp_data_w30(resp_data[3][30]),
      .n3_resp_data_w31(resp_data[3][31]),
      .n3_resp_is_write(resp_is_write[3]),

      .n4_req_valid(req_valid[4]),
      .n4_req_write(req_write[4]),
      .n4_req_addr(req_addr[4]),
      .n4_req_tag(req_tag[4]),
      .n4_req_data_w0(req_data[4][0]),
      .n4_req_data_w1(req_data[4][1]),
      .n4_req_data_w2(req_data[4][2]),
      .n4_req_data_w3(req_data[4][3]),
      .n4_req_data_w4(req_data[4][4]),
      .n4_req_data_w5(req_data[4][5]),
      .n4_req_data_w6(req_data[4][6]),
      .n4_req_data_w7(req_data[4][7]),
      .n4_req_data_w8(req_data[4][8]),
      .n4_req_data_w9(req_data[4][9]),
      .n4_req_data_w10(req_data[4][10]),
      .n4_req_data_w11(req_data[4][11]),
      .n4_req_data_w12(req_data[4][12]),
      .n4_req_data_w13(req_data[4][13]),
      .n4_req_data_w14(req_data[4][14]),
      .n4_req_data_w15(req_data[4][15]),
      .n4_req_data_w16(req_data[4][16]),
      .n4_req_data_w17(req_data[4][17]),
      .n4_req_data_w18(req_data[4][18]),
      .n4_req_data_w19(req_data[4][19]),
      .n4_req_data_w20(req_data[4][20]),
      .n4_req_data_w21(req_data[4][21]),
      .n4_req_data_w22(req_data[4][22]),
      .n4_req_data_w23(req_data[4][23]),
      .n4_req_data_w24(req_data[4][24]),
      .n4_req_data_w25(req_data[4][25]),
      .n4_req_data_w26(req_data[4][26]),
      .n4_req_data_w27(req_data[4][27]),
      .n4_req_data_w28(req_data[4][28]),
      .n4_req_data_w29(req_data[4][29]),
      .n4_req_data_w30(req_data[4][30]),
      .n4_req_data_w31(req_data[4][31]),
      .n4_req_ready(req_ready[4]),
      .n4_resp_ready(resp_ready[4]),
      .n4_resp_valid(resp_valid[4]),
      .n4_resp_tag(resp_tag[4]),
      .n4_resp_data_w0(resp_data[4][0]),
      .n4_resp_data_w1(resp_data[4][1]),
      .n4_resp_data_w2(resp_data[4][2]),
      .n4_resp_data_w3(resp_data[4][3]),
      .n4_resp_data_w4(resp_data[4][4]),
      .n4_resp_data_w5(resp_data[4][5]),
      .n4_resp_data_w6(resp_data[4][6]),
      .n4_resp_data_w7(resp_data[4][7]),
      .n4_resp_data_w8(resp_data[4][8]),
      .n4_resp_data_w9(resp_data[4][9]),
      .n4_resp_data_w10(resp_data[4][10]),
      .n4_resp_data_w11(resp_data[4][11]),
      .n4_resp_data_w12(resp_data[4][12]),
      .n4_resp_data_w13(resp_data[4][13]),
      .n4_resp_data_w14(resp_data[4][14]),
      .n4_resp_data_w15(resp_data[4][15]),
      .n4_resp_data_w16(resp_data[4][16]),
      .n4_resp_data_w17(resp_data[4][17]),
      .n4_resp_data_w18(resp_data[4][18]),
      .n4_resp_data_w19(resp_data[4][19]),
      .n4_resp_data_w20(resp_data[4][20]),
      .n4_resp_data_w21(resp_data[4][21]),
      .n4_resp_data_w22(resp_data[4][22]),
      .n4_resp_data_w23(resp_data[4][23]),
      .n4_resp_data_w24(resp_data[4][24]),
      .n4_resp_data_w25(resp_data[4][25]),
      .n4_resp_data_w26(resp_data[4][26]),
      .n4_resp_data_w27(resp_data[4][27]),
      .n4_resp_data_w28(resp_data[4][28]),
      .n4_resp_data_w29(resp_data[4][29]),
      .n4_resp_data_w30(resp_data[4][30]),
      .n4_resp_data_w31(resp_data[4][31]),
      .n4_resp_is_write(resp_is_write[4]),

      .n5_req_valid(req_valid[5]),
      .n5_req_write(req_write[5]),
      .n5_req_addr(req_addr[5]),
      .n5_req_tag(req_tag[5]),
      .n5_req_data_w0(req_data[5][0]),
      .n5_req_data_w1(req_data[5][1]),
      .n5_req_data_w2(req_data[5][2]),
      .n5_req_data_w3(req_data[5][3]),
      .n5_req_data_w4(req_data[5][4]),
      .n5_req_data_w5(req_data[5][5]),
      .n5_req_data_w6(req_data[5][6]),
      .n5_req_data_w7(req_data[5][7]),
      .n5_req_data_w8(req_data[5][8]),
      .n5_req_data_w9(req_data[5][9]),
      .n5_req_data_w10(req_data[5][10]),
      .n5_req_data_w11(req_data[5][11]),
      .n5_req_data_w12(req_data[5][12]),
      .n5_req_data_w13(req_data[5][13]),
      .n5_req_data_w14(req_data[5][14]),
      .n5_req_data_w15(req_data[5][15]),
      .n5_req_data_w16(req_data[5][16]),
      .n5_req_data_w17(req_data[5][17]),
      .n5_req_data_w18(req_data[5][18]),
      .n5_req_data_w19(req_data[5][19]),
      .n5_req_data_w20(req_data[5][20]),
      .n5_req_data_w21(req_data[5][21]),
      .n5_req_data_w22(req_data[5][22]),
      .n5_req_data_w23(req_data[5][23]),
      .n5_req_data_w24(req_data[5][24]),
      .n5_req_data_w25(req_data[5][25]),
      .n5_req_data_w26(req_data[5][26]),
      .n5_req_data_w27(req_data[5][27]),
      .n5_req_data_w28(req_data[5][28]),
      .n5_req_data_w29(req_data[5][29]),
      .n5_req_data_w30(req_data[5][30]),
      .n5_req_data_w31(req_data[5][31]),
      .n5_req_ready(req_ready[5]),
      .n5_resp_ready(resp_ready[5]),
      .n5_resp_valid(resp_valid[5]),
      .n5_resp_tag(resp_tag[5]),
      .n5_resp_data_w0(resp_data[5][0]),
      .n5_resp_data_w1(resp_data[5][1]),
      .n5_resp_data_w2(resp_data[5][2]),
      .n5_resp_data_w3(resp_data[5][3]),
      .n5_resp_data_w4(resp_data[5][4]),
      .n5_resp_data_w5(resp_data[5][5]),
      .n5_resp_data_w6(resp_data[5][6]),
      .n5_resp_data_w7(resp_data[5][7]),
      .n5_resp_data_w8(resp_data[5][8]),
      .n5_resp_data_w9(resp_data[5][9]),
      .n5_resp_data_w10(resp_data[5][10]),
      .n5_resp_data_w11(resp_data[5][11]),
      .n5_resp_data_w12(resp_data[5][12]),
      .n5_resp_data_w13(resp_data[5][13]),
      .n5_resp_data_w14(resp_data[5][14]),
      .n5_resp_data_w15(resp_data[5][15]),
      .n5_resp_data_w16(resp_data[5][16]),
      .n5_resp_data_w17(resp_data[5][17]),
      .n5_resp_data_w18(resp_data[5][18]),
      .n5_resp_data_w19(resp_data[5][19]),
      .n5_resp_data_w20(resp_data[5][20]),
      .n5_resp_data_w21(resp_data[5][21]),
      .n5_resp_data_w22(resp_data[5][22]),
      .n5_resp_data_w23(resp_data[5][23]),
      .n5_resp_data_w24(resp_data[5][24]),
      .n5_resp_data_w25(resp_data[5][25]),
      .n5_resp_data_w26(resp_data[5][26]),
      .n5_resp_data_w27(resp_data[5][27]),
      .n5_resp_data_w28(resp_data[5][28]),
      .n5_resp_data_w29(resp_data[5][29]),
      .n5_resp_data_w30(resp_data[5][30]),
      .n5_resp_data_w31(resp_data[5][31]),
      .n5_resp_is_write(resp_is_write[5]),

      .n6_req_valid(req_valid[6]),
      .n6_req_write(req_write[6]),
      .n6_req_addr(req_addr[6]),
      .n6_req_tag(req_tag[6]),
      .n6_req_data_w0(req_data[6][0]),
      .n6_req_data_w1(req_data[6][1]),
      .n6_req_data_w2(req_data[6][2]),
      .n6_req_data_w3(req_data[6][3]),
      .n6_req_data_w4(req_data[6][4]),
      .n6_req_data_w5(req_data[6][5]),
      .n6_req_data_w6(req_data[6][6]),
      .n6_req_data_w7(req_data[6][7]),
      .n6_req_data_w8(req_data[6][8]),
      .n6_req_data_w9(req_data[6][9]),
      .n6_req_data_w10(req_data[6][10]),
      .n6_req_data_w11(req_data[6][11]),
      .n6_req_data_w12(req_data[6][12]),
      .n6_req_data_w13(req_data[6][13]),
      .n6_req_data_w14(req_data[6][14]),
      .n6_req_data_w15(req_data[6][15]),
      .n6_req_data_w16(req_data[6][16]),
      .n6_req_data_w17(req_data[6][17]),
      .n6_req_data_w18(req_data[6][18]),
      .n6_req_data_w19(req_data[6][19]),
      .n6_req_data_w20(req_data[6][20]),
      .n6_req_data_w21(req_data[6][21]),
      .n6_req_data_w22(req_data[6][22]),
      .n6_req_data_w23(req_data[6][23]),
      .n6_req_data_w24(req_data[6][24]),
      .n6_req_data_w25(req_data[6][25]),
      .n6_req_data_w26(req_data[6][26]),
      .n6_req_data_w27(req_data[6][27]),
      .n6_req_data_w28(req_data[6][28]),
      .n6_req_data_w29(req_data[6][29]),
      .n6_req_data_w30(req_data[6][30]),
      .n6_req_data_w31(req_data[6][31]),
      .n6_req_ready(req_ready[6]),
      .n6_resp_ready(resp_ready[6]),
      .n6_resp_valid(resp_valid[6]),
      .n6_resp_tag(resp_tag[6]),
      .n6_resp_data_w0(resp_data[6][0]),
      .n6_resp_data_w1(resp_data[6][1]),
      .n6_resp_data_w2(resp_data[6][2]),
      .n6_resp_data_w3(resp_data[6][3]),
      .n6_resp_data_w4(resp_data[6][4]),
      .n6_resp_data_w5(resp_data[6][5]),
      .n6_resp_data_w6(resp_data[6][6]),
      .n6_resp_data_w7(resp_data[6][7]),
      .n6_resp_data_w8(resp_data[6][8]),
      .n6_resp_data_w9(resp_data[6][9]),
      .n6_resp_data_w10(resp_data[6][10]),
      .n6_resp_data_w11(resp_data[6][11]),
      .n6_resp_data_w12(resp_data[6][12]),
      .n6_resp_data_w13(resp_data[6][13]),
      .n6_resp_data_w14(resp_data[6][14]),
      .n6_resp_data_w15(resp_data[6][15]),
      .n6_resp_data_w16(resp_data[6][16]),
      .n6_resp_data_w17(resp_data[6][17]),
      .n6_resp_data_w18(resp_data[6][18]),
      .n6_resp_data_w19(resp_data[6][19]),
      .n6_resp_data_w20(resp_data[6][20]),
      .n6_resp_data_w21(resp_data[6][21]),
      .n6_resp_data_w22(resp_data[6][22]),
      .n6_resp_data_w23(resp_data[6][23]),
      .n6_resp_data_w24(resp_data[6][24]),
      .n6_resp_data_w25(resp_data[6][25]),
      .n6_resp_data_w26(resp_data[6][26]),
      .n6_resp_data_w27(resp_data[6][27]),
      .n6_resp_data_w28(resp_data[6][28]),
      .n6_resp_data_w29(resp_data[6][29]),
      .n6_resp_data_w30(resp_data[6][30]),
      .n6_resp_data_w31(resp_data[6][31]),
      .n6_resp_is_write(resp_is_write[6]),

      .n7_req_valid(req_valid[7]),
      .n7_req_write(req_write[7]),
      .n7_req_addr(req_addr[7]),
      .n7_req_tag(req_tag[7]),
      .n7_req_data_w0(req_data[7][0]),
      .n7_req_data_w1(req_data[7][1]),
      .n7_req_data_w2(req_data[7][2]),
      .n7_req_data_w3(req_data[7][3]),
      .n7_req_data_w4(req_data[7][4]),
      .n7_req_data_w5(req_data[7][5]),
      .n7_req_data_w6(req_data[7][6]),
      .n7_req_data_w7(req_data[7][7]),
      .n7_req_data_w8(req_data[7][8]),
      .n7_req_data_w9(req_data[7][9]),
      .n7_req_data_w10(req_data[7][10]),
      .n7_req_data_w11(req_data[7][11]),
      .n7_req_data_w12(req_data[7][12]),
      .n7_req_data_w13(req_data[7][13]),
      .n7_req_data_w14(req_data[7][14]),
      .n7_req_data_w15(req_data[7][15]),
      .n7_req_data_w16(req_data[7][16]),
      .n7_req_data_w17(req_data[7][17]),
      .n7_req_data_w18(req_data[7][18]),
      .n7_req_data_w19(req_data[7][19]),
      .n7_req_data_w20(req_data[7][20]),
      .n7_req_data_w21(req_data[7][21]),
      .n7_req_data_w22(req_data[7][22]),
      .n7_req_data_w23(req_data[7][23]),
      .n7_req_data_w24(req_data[7][24]),
      .n7_req_data_w25(req_data[7][25]),
      .n7_req_data_w26(req_data[7][26]),
      .n7_req_data_w27(req_data[7][27]),
      .n7_req_data_w28(req_data[7][28]),
      .n7_req_data_w29(req_data[7][29]),
      .n7_req_data_w30(req_data[7][30]),
      .n7_req_data_w31(req_data[7][31]),
      .n7_req_ready(req_ready[7]),
      .n7_resp_ready(resp_ready[7]),
      .n7_resp_valid(resp_valid[7]),
      .n7_resp_tag(resp_tag[7]),
      .n7_resp_data_w0(resp_data[7][0]),
      .n7_resp_data_w1(resp_data[7][1]),
      .n7_resp_data_w2(resp_data[7][2]),
      .n7_resp_data_w3(resp_data[7][3]),
      .n7_resp_data_w4(resp_data[7][4]),
      .n7_resp_data_w5(resp_data[7][5]),
      .n7_resp_data_w6(resp_data[7][6]),
      .n7_resp_data_w7(resp_data[7][7]),
      .n7_resp_data_w8(resp_data[7][8]),
      .n7_resp_data_w9(resp_data[7][9]),
      .n7_resp_data_w10(resp_data[7][10]),
      .n7_resp_data_w11(resp_data[7][11]),
      .n7_resp_data_w12(resp_data[7][12]),
      .n7_resp_data_w13(resp_data[7][13]),
      .n7_resp_data_w14(resp_data[7][14]),
      .n7_resp_data_w15(resp_data[7][15]),
      .n7_resp_data_w16(resp_data[7][16]),
      .n7_resp_data_w17(resp_data[7][17]),
      .n7_resp_data_w18(resp_data[7][18]),
      .n7_resp_data_w19(resp_data[7][19]),
      .n7_resp_data_w20(resp_data[7][20]),
      .n7_resp_data_w21(resp_data[7][21]),
      .n7_resp_data_w22(resp_data[7][22]),
      .n7_resp_data_w23(resp_data[7][23]),
      .n7_resp_data_w24(resp_data[7][24]),
      .n7_resp_data_w25(resp_data[7][25]),
      .n7_resp_data_w26(resp_data[7][26]),
      .n7_resp_data_w27(resp_data[7][27]),
      .n7_resp_data_w28(resp_data[7][28]),
      .n7_resp_data_w29(resp_data[7][29]),
      .n7_resp_data_w30(resp_data[7][30]),
      .n7_resp_data_w31(resp_data[7][31]),
      .n7_resp_is_write(resp_is_write[7])
  );

  function automatic [19:0] make_addr(input int index, input int pipe, input int offset);
    make_addr = {index[8:0], pipe[2:0], offset[7:0]};
  endfunction

  task automatic fill_data(output logic [63:0] data[0:31], input int seed);
    integer i;
    begin
      for (i = 0; i < 32; i = i + 1) begin
        data[i] = {seed[31:0], i[31:0]};
      end
    end
  endtask

  task automatic clear_line(output logic [63:0] data[0:31]);
    integer i;
    begin
      for (i = 0; i < 32; i = i + 1) begin
        data[i] = 64'd0;
      end
    end
  endtask

  task automatic clear_reqs();
    integer i;
    integer j;
    begin
      for (i = 0; i < 8; i = i + 1) begin
        req_valid[i] = 1'b0;
        req_write[i] = 1'b0;
        req_addr[i] = 20'd0;
        req_tag[i] = 8'd0;
        resp_ready[i] = 1'b1;
        for (j = 0; j < 32; j = j + 1) begin
          req_data[i][j] = 64'd0;
        end
      end
    end
  endtask

  task automatic send_req(
      input int node,
      input bit write,
      input logic [19:0] addr,
      input logic [7:0] tag,
      input logic [63:0] data[0:31]
  );
    integer i;
    begin
      req_write[node] = write;
      req_addr[node] = addr;
      req_tag[node] = tag;
      for (i = 0; i < 32; i = i + 1) begin
        req_data[node][i] = data[i];
      end
      req_valid[node] = 1'b1;
      while (req_ready[node] !== 1'b1) begin
        @(posedge clk);
      end
      @(posedge clk);
      req_valid[node] = 1'b0;
    end
  endtask

  task automatic wait_resp(
      input int node,
      input logic [7:0] tag,
      input bit expect_write,
      input logic [63:0] expect_data[0:31]
  );
    integer timeout;
    integer i;
    begin
      timeout = 2000;
      while (timeout > 0) begin
        @(posedge clk);
        if (resp_valid[node]) begin
          if (resp_tag[node] !== tag) $fatal(1, "tag mismatch");
          if (resp_is_write[node] !== expect_write) $fatal(1, "resp_is_write mismatch");
          for (i = 0; i < 32; i = i + 1) begin
            if (resp_data[node][i] !== expect_data[i]) $fatal(1, "resp_data mismatch");
          end
          return;
        end
        timeout = timeout - 1;
      end
      $fatal(1, "timeout waiting resp");
    end
  endtask

  initial begin
    clk = 1'b0;
    rst = 1'b1;
    clear_reqs();
    repeat (2) @(posedge clk);
    rst = 1'b0;
    repeat (1) @(posedge clk);

    for (int n = 0; n < 8; n = n + 1) begin
      fill_data(line_data, n + 1);
      clear_line(line_zero);
      send_req(n, 1'b1, make_addr(n, n, 0), n[7:0], line_data);
      wait_resp(n, n[7:0], 1'b1, line_data);
      send_req(n, 1'b0, make_addr(n, n, 0), (8'h80 | n[7:0]), line_zero);
      wait_resp(n, (8'h80 | n[7:0]), 1'b0, line_data);
    end

    begin
      fill_data(line_data, 8'hAA);
      clear_line(line_zero);
      send_req(0, 1'b1, make_addr(5, 2, 0), 8'h55, line_data);
      wait_resp(0, 8'h55, 1'b1, line_data);
      send_req(0, 1'b0, make_addr(5, 2, 0), 8'h56, line_zero);
      wait_resp(0, 8'h56, 1'b0, line_data);
    end

    $display("PASS: TMU tests");
    $finish;
  end

  always #1 clk = ~clk;

  initial begin
    if (!$test$plusargs("NOVCD")) begin
      $dumpfile("janus/generated/janus_tmu_pyc/tb_janus_tmu_pyc.vcd");
      $dumpvars(0, tb_janus_tmu_pyc);
    end
  end
endmodule
