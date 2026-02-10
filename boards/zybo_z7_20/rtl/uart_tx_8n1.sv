// Simple UART TX (8N1) with ready/valid handshake.
//
// - `valid` is sampled only when `ready=1`.
// - `txd` idles high.
module uart_tx_8n1 #(
  parameter int unsigned CLK_HZ = 125_000_000,
  parameter int unsigned BAUD   = 115_200
) (
  input  logic       clk,
  input  logic       rst,
  input  logic       valid,
  input  logic [7:0] data,
  output logic       ready,
  output logic       txd
);

  localparam int unsigned BAUD_DIV = (CLK_HZ + (BAUD / 2)) / BAUD;
  localparam int unsigned BAUD_W   = (BAUD_DIV <= 2) ? 1 : $clog2(BAUD_DIV);

  logic busy;
  logic [3:0] bit_idx;
  logic [9:0] shreg; // {stop(1), data[7:0], start(0)} shifted LSB-first.
  logic [BAUD_W-1:0] baud_cnt;

  assign ready = ~busy;

  always_ff @(posedge clk) begin
    if (rst) begin
      busy <= 1'b0;
      bit_idx <= 4'd0;
      shreg <= 10'h3FF;
      baud_cnt <= '0;
      txd <= 1'b1;
    end else begin
      if (~busy) begin
        txd <= 1'b1;
        if (valid) begin
          busy <= 1'b1;
          bit_idx <= 4'd0;
          shreg <= {1'b1, data, 1'b0};
          baud_cnt <= BAUD_DIV[BAUD_W-1:0] - 1'b1;
          txd <= 1'b0; // start bit
        end
      end else begin
        if (baud_cnt != '0) begin
          baud_cnt <= baud_cnt - 1'b1;
        end else begin
          baud_cnt <= BAUD_DIV[BAUD_W-1:0] - 1'b1;
          shreg <= {1'b1, shreg[9:1]};
          bit_idx <= bit_idx + 1'b1;
          txd <= shreg[1]; // next bit after shift
          if (bit_idx == 4'd9) begin
            busy <= 1'b0;
            txd <= 1'b1;
          end
        end
      end
    end
  end

endmodule

