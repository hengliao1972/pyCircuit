// Simple synchronous reset register (prototype).
//
// When init is a *constant*, the VerilogEmitter passes it as the INIT
// *parameter* so synthesis tools can constant-fold the reset-value mux
// away.
// When init is *non-constant*, the old init *port* is used (fallback).
module pyc_reg #(
  parameter WIDTH = 1,
  parameter [WIDTH-1:0] INIT = {WIDTH{1'b0}},
  parameter USE_INIT_PORT = 0
) (
  input             clk,
  input             rst,
  input             en,
  input  [WIDTH-1:0] d,
  input  [WIDTH-1:0] init,
  output reg [WIDTH-1:0] q
);
  generate
    if (USE_INIT_PORT) begin : g_port
      always @(posedge clk) begin
        if (rst)
          q <= init;
        else if (en)
          q <= d;
      end
    end else begin : g_param
      always @(posedge clk) begin
        if (rst)
          q <= INIT;
        else if (en)
          q <= d;
      end
    end
  endgenerate
endmodule
