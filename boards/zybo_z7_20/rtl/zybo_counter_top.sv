// Minimal Zybo Z7-20 bring-up top: drive LEDs from a pyCircuit-generated counter.
//
// - clk: Zybo Z7 sysclk (125 MHz)
// - btn[0]: synchronous reset (active-high, 2FF synced)
// - sw[0]: enable counting
// - led[3:0]: counter[3:0]
module zybo_counter_top (
  input  logic        sysclk,
  input  logic [3:0]  btn,
  input  logic [3:0]  sw,
  output logic [3:0]  led
);

  // 2FF synchronize btn[0] into sysclk domain.
  (* ASYNC_REG = "TRUE" *) logic [1:0] btn0_sync = 2'b00;
  always_ff @(posedge sysclk) begin
    btn0_sync <= {btn0_sync[0], btn[0]};
  end
  logic rst;
  assign rst = btn0_sync[1];

  // Clock divider: 125 MHz / 2^26 ≈ 1.86 Hz tick (single-cycle enable).
  logic [25:0] div = 26'd1;
  always_ff @(posedge sysclk) begin
    if (rst)
      div <= 26'd1;
    else
      div <= div + 26'd1;
  end
  logic tick;
  assign tick = (div == 26'd0);

  logic en;
  assign en = tick & sw[0];

  logic [7:0] count;
  Counter u_counter (
    .clk(sysclk),
    .rst(rst),
    .en(en),
    .count(count)
  );

  always_comb begin
    led = count[3:0];
  end

endmodule

