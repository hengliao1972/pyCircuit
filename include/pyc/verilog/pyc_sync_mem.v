// Synchronous 1R1W memory with registered read data (prototype).
//
// - `DEPTH` is in entries (not bytes).
// - Read is synchronous: when `ren` is asserted, `rdata` updates on the next
//   rising edge of `clk`.
// - Write is synchronous with byte enables `wstrb`.
//
// Note: Read-during-write to the same address returns the written data
// ("write-first") via simple forwarding.
module pyc_sync_mem #(
  parameter ADDR_WIDTH = 64,
  parameter DATA_WIDTH = 64,
  parameter DEPTH = 1024
) (
  input                   clk,
  input                   rst,

  input                   ren,
  input  [ADDR_WIDTH-1:0] raddr,
  output reg [DATA_WIDTH-1:0] rdata,

  input                   wvalid,
  input  [ADDR_WIDTH-1:0] waddr,
  input  [DATA_WIDTH-1:0] wdata,
  input  [(DATA_WIDTH+7)/8-1:0] wstrb
);
  `ifndef SYNTHESIS
  initial begin
    if (DEPTH <= 0) begin
      $display("ERROR: pyc_sync_mem DEPTH must be > 0");
      $finish;
    end
    if ((DATA_WIDTH % 8) != 0) begin
      $display("ERROR: pyc_sync_mem DATA_WIDTH must be divisible by 8");
      $finish;
    end
  end
  `endif

  localparam STRB_WIDTH = (DATA_WIDTH + 7) / 8;

  // Storage.
  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

  integer i;
  integer ra;
  integer wa;
  reg [DATA_WIDTH-1:0] rd_word;

  always @(posedge clk) begin
    if (rst) begin
      rdata <= {DATA_WIDTH{1'b0}};
    end else begin
      // Write.
      if (wvalid) begin
        wa = waddr[31:0];
        if (wa < DEPTH) begin
          for (i = 0; i < STRB_WIDTH; i = i + 1) begin
            if (wstrb[i])
              mem[wa][8 * i +: 8] <= wdata[8 * i +: 8];
          end
        end
      end

      // Registered read.
      if (ren) begin
        ra = raddr[31:0];
        if (ra < DEPTH) begin
          rd_word = mem[ra];
          // Forward in-cycle writes to the read result when addresses match.
          if (wvalid && (waddr[31:0] == raddr[31:0])) begin
            for (i = 0; i < STRB_WIDTH; i = i + 1) begin
              if (wstrb[i])
                rd_word[8 * i +: 8] = wdata[8 * i +: 8];
            end
          end
          rdata <= rd_word;
        end else begin
          rdata <= {DATA_WIDTH{1'b0}};
        end
      end
    end
  end
endmodule

