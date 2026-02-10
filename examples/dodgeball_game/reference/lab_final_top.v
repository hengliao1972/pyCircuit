`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2018/06/09 20:25:15
// Design Name: 
// Module Name: lab_final_top
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module top(
    input wire CLK_in,             // board clock: 100 MHz
    input wire RST_BTN,         // reset button
    input wire START,           //game start
    output wire VGA_HS_O,       // horizontal sync output
    output wire VGA_VS_O,       // vertical sync output
    output wire [3:0] VGA_R,    // 4-bit VGA red output
    output wire [3:0] VGA_G,    // 4-bit VGA green output
    output wire [3:0] VGA_B,     // 4-bit VGA blue output
    input wire left,
    input wire right
    );

//    wire rst = ~RST_BTN;  // reset is active low on Arty

    // generate a 25 MHz pixel strobe
    reg [15:0] cnt = 0;
    reg pix_stb = 0;
    reg [24:0]MAIN_CLK = 0;
    always@(posedge CLK_in)
            MAIN_CLK <= MAIN_CLK + 1;
    always @(posedge CLK_in)
        {pix_stb, cnt} <= cnt + 16'h4000;  // divide clock by 4: (2^16)/4 = 0x4000

    wire [9:0] x;  // current pixel x position: 10-bit value: 0-1023
    wire [8:0] y;  // current pixel y position:  9-bit value: 0-511

    vga display (
        .i_clk(CLK_in),
        .i_pix_stb(pix_stb),
        .o_hs(VGA_HS_O), 
        .o_vs(VGA_VS_O), 
        .o_x(x), 
        .o_y(y)
    );

    wire sq_player;
    wire sq_object1;
    wire sq_object2;
    wire sq_object3;
    wire over_wire;
    wire down;
    wire up;
        
    reg [3:0]i=8;
    reg [4:0]j=0;
    
    reg [3:0]MAIN_OB_1_x=1;
    reg [3:0]MAIN_OB_2_x=4;
    reg [3:0]MAIN_OB_3_x=7;
    reg [3:0]MAIN_OB_1_y=0;
    reg [3:0]MAIN_OB_2_y=0;
    reg [3:0]MAIN_OB_3_y=0;
    reg [2:0]FSM_state;
    //0  initial
    //1 gaming
    //2 over
    always@(posedge MAIN_CLK[22])begin
        case(FSM_state)
        0:
        begin
            if (START == 1)begin
                FSM_state <= 1;
            end
        end
        1:
        begin
            if (RST_BTN == 1)begin
                FSM_state <= 0;
                j <= 0;    
                MAIN_OB_1_y <= 0;
                MAIN_OB_2_y <= 0;
                MAIN_OB_3_y <= 0;
            end
            if ((MAIN_OB_1_x == i && MAIN_OB_1_y == 10) || (MAIN_OB_2_x == i && MAIN_OB_2_y == 10) || (MAIN_OB_3_x == i && MAIN_OB_3_y == 10))
                FSM_state <= 2;
            if (j == 20)begin
                j <= 0;
                MAIN_OB_1_y <= 0;
                MAIN_OB_2_y <= 0;
                MAIN_OB_3_y <= 0;
            end 
            begin
                j <= j+1;                      
                MAIN_OB_1_y <= MAIN_OB_1_y + ((j>0)&&(j<13));
                MAIN_OB_2_y <= MAIN_OB_2_y + ((j>3)&&(j<16));
                MAIN_OB_3_y <= MAIN_OB_3_y + ((j>7)&&(j<20));
            end
         end
         2:
         begin
            if (RST_BTN == 1)begin
                FSM_state <= 0;
                j <= 0;    
                MAIN_OB_1_y <= 0;
                MAIN_OB_2_y <= 0;
                MAIN_OB_3_y <= 0;
             end
         end
         endcase
     end

    wire circle;

    assign sq_player=((x > 40*i) & (y >  400) & (x < 40*(i+1)) & (y < 440)) ? 1 : 0;
    assign sq_object1=((x > 40*MAIN_OB_1_x) & (y >  40*MAIN_OB_1_y) & (x < 40*(MAIN_OB_1_x+1)) & (y < 40*(MAIN_OB_1_y+1))) ? 1 : 0;
    assign sq_object2=((x > 40*MAIN_OB_2_x) & (y >  40*MAIN_OB_2_y) & (x < 40*(MAIN_OB_2_x+1)) & (y < 40*(MAIN_OB_2_y+1))) ? 1 : 0;
    assign sq_object3=((x > 40*MAIN_OB_3_x) & (y >  40*MAIN_OB_3_y) & (x < 40*(MAIN_OB_3_x+1)) & (y < 40*(MAIN_OB_3_y+1))) ? 1 : 0;
    assign over_wire=((x > 0) & (y >  0) & (x < 640) & (y < 480)) ? 1 : 0;
    assign down=((x > 0) & (y >  440) & (x < 640) & (y < 480)) ? 1 : 0;
    assign down=((x > 0) & (y >  0) & (x < 640) & (y < 40)) ? 1 : 0;
    
    assign VGA_R[3] = (sq_player & ~(FSM_state == 2));         // square b is red
    assign VGA_B[3] = ((sq_object1|sq_object2|sq_object3|down|up) & ~(FSM_state == 2)); 
    assign VGA_G[3] = (circle|(over_wire & (FSM_state == 2)));
    
endmodule