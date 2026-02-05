// Synchronous 2R1W memory with registered read data (prototype).
//
// - `DEPTH` is in entries (not bytes).
// - Both reads are synchronous (registered outputs).
// - One write port with byte enables `wstrb`.
// - Read-during-write to the same address returns written data ("write-first")
//   via forwarding.
module pyc_sync_mem_dp #(
  parameter ADDR_WIDTH = 64,
  parameter DATA_WIDTH = 64,
  parameter DEPTH = 1024
) (
  input                   clk,
  input                   rst,

  input                   ren0,
  input  [ADDR_WIDTH-1:0] raddr0,
  output reg [DATA_WIDTH-1:0] rdata0,

  input                   ren1,
  input  [ADDR_WIDTH-1:0] raddr1,
  output reg [DATA_WIDTH-1:0] rdata1,

  input                   wvalid,
  input  [ADDR_WIDTH-1:0] waddr,
  input  [DATA_WIDTH-1:0] wdata,
  input  [(DATA_WIDTH+7)/8-1:0] wstrb
);
  `ifndef SYNTHESIS
  initial begin
    if (DEPTH <= 0) begin
      $display("ERROR: pyc_sync_mem_dp DEPTH must be > 0");
      $finish;
    end
    if ((DATA_WIDTH % 8) != 0) begin
      $display("ERROR: pyc_sync_mem_dp DATA_WIDTH must be divisible by 8");
      $finish;
    end
  end
  `endif

  localparam STRB_WIDTH = (DATA_WIDTH + 7) / 8;

  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

  integer i;
  integer ra0;
  integer ra1;
  integer wa;
  reg [DATA_WIDTH-1:0] rd0;
  reg [DATA_WIDTH-1:0] rd1;

  always @(posedge clk) begin
    if (rst) begin
      rdata0 <= {DATA_WIDTH{1'b0}};
      rdata1 <= {DATA_WIDTH{1'b0}};
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

      // Registered read port 0.
      if (ren0) begin
        ra0 = raddr0[31:0];
        if (ra0 < DEPTH) begin
          rd0 = mem[ra0];
          if (wvalid && (waddr[31:0] == raddr0[31:0])) begin
            for (i = 0; i < STRB_WIDTH; i = i + 1) begin
              if (wstrb[i])
                rd0[8 * i +: 8] = wdata[8 * i +: 8];
            end
          end
          rdata0 <= rd0;
        end else begin
          rdata0 <= {DATA_WIDTH{1'b0}};
        end
      end

      // Registered read port 1.
      if (ren1) begin
        ra1 = raddr1[31:0];
        if (ra1 < DEPTH) begin
          rd1 = mem[ra1];
          if (wvalid && (waddr[31:0] == raddr1[31:0])) begin
            for (i = 0; i < STRB_WIDTH; i = i + 1) begin
              if (wstrb[i])
                rd1[8 * i +: 8] = wdata[8 * i +: 8];
            end
          end
          rdata1 <= rd1;
        end else begin
          rdata1 <= {DATA_WIDTH{1'b0}};
        end
      end
    end
  end
endmodule

