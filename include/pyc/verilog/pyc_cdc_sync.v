// CDC synchronizer (prototype).
//
// This is a simple multi-stage flop pipeline in the destination clock domain.
// It is suitable for single-bit control signals. For multi-bit buses, prefer a
// proper CDC protocol (async FIFO, handshake, etc).
module pyc_cdc_sync #(
  parameter WIDTH = 1,
  parameter STAGES = 2
) (
  input               clk,
  input               rst,
  input  [WIDTH-1:0]  in,
  output [WIDTH-1:0]  out
);
  `ifndef SYNTHESIS
  initial begin
    if (STAGES < 1) begin
      $display("ERROR: pyc_cdc_sync STAGES must be >= 1");
      $finish;
    end
  end
  `endif

  reg [WIDTH-1:0] pipe [0:STAGES-1];

  integer i;
  always @(posedge clk) begin
    if (rst) begin
      for (i = 0; i < STAGES; i = i + 1)
        pipe[i] <= {WIDTH{1'b0}};
    end else begin
      pipe[0] <= in;
      for (i = 1; i < STAGES; i = i + 1)
        pipe[i] <= pipe[i - 1];
    end
  end

  assign out = pipe[STAGES-1];
endmodule

