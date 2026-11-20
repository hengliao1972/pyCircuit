// Synchronous 1R2W memory with registered read data (prototype).
//
// - `DEPTH` is in entries (not bytes).
// - One registered read port.
// - Two write ports with byte enables; on a same-address same-cycle
//   conflict, write port 1 wins (applied after port 0).
// - Read is write-first (transparent): a same-edge write to the read
//   address is forwarded to the read output (port 1 wins conflicts).
module pyc_sync_mem_2w #(
  parameter ADDR_WIDTH = 64,
  parameter DATA_WIDTH = 64,
  parameter DEPTH = 1024
) (
  input                   clk,

  input                   ren,
  input  [ADDR_WIDTH-1:0] raddr,
  output reg [DATA_WIDTH-1:0] rdata,

  input                   wvalid0,
  input  [ADDR_WIDTH-1:0] waddr0,
  input  [DATA_WIDTH-1:0] wdata0,
  input  [(DATA_WIDTH+7)/8-1:0] wstrb0,

  input                   wvalid1,
  input  [ADDR_WIDTH-1:0] waddr1,
  input  [DATA_WIDTH-1:0] wdata1,
  input  [(DATA_WIDTH+7)/8-1:0] wstrb1
);
  `ifndef SYNTHESIS
  initial begin
    if (DEPTH <= 0) begin
      $display("ERROR: pyc_sync_mem_2w DEPTH must be > 0");
      $finish;
    end
  end
  `endif

  localparam STRB_WIDTH = (DATA_WIDTH + 7) / 8;
  localparam LAST_LANE_BITS = DATA_WIDTH - 8 * (STRB_WIDTH - 1);
  localparam ADDR_BITS = (DEPTH <= 1) ? 1 : $clog2(DEPTH);

  `ifdef PYC_TARGET_FPGA
  (* ram_style = "block" *)
  (* ramstyle = "M20K" *)
  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];
  `else
  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];
  `endif

  `ifndef SYNTHESIS
  // Deterministic simulation init: keep C++/Verilog equivalence stable.
  integer init_i;
  initial begin
    for (init_i = 0; init_i < DEPTH; init_i = init_i + 1)
      mem[init_i] = {DATA_WIDTH{1'b0}};
  end
  `endif

  integer i;
  reg [DATA_WIDTH-1:0] rd;
  wire [ADDR_BITS-1:0] ra = raddr[ADDR_BITS-1:0];
  wire [ADDR_BITS-1:0] wa0 = waddr0[ADDR_BITS-1:0];
  wire [ADDR_BITS-1:0] wa1 = waddr1[ADDR_BITS-1:0];

  always @(posedge clk) begin
      // Write port 0 (applied first).
      if (wvalid0) begin
        for (i = 0; i < STRB_WIDTH - 1; i = i + 1) begin
          if (wstrb0[i])
            mem[wa0][8 * i +: 8] <= wdata0[8 * i +: 8];
        end
        if (wstrb0[STRB_WIDTH-1])
          mem[wa0][8*(STRB_WIDTH-1) +: LAST_LANE_BITS] <= wdata0[8*(STRB_WIDTH-1) +: LAST_LANE_BITS];
      end

      // Write port 1 (applied second; wins same-address conflicts).
      if (wvalid1) begin
        for (i = 0; i < STRB_WIDTH - 1; i = i + 1) begin
          if (wstrb1[i])
            mem[wa1][8 * i +: 8] <= wdata1[8 * i +: 8];
        end
        if (wstrb1[STRB_WIDTH-1])
          mem[wa1][8*(STRB_WIDTH-1) +: LAST_LANE_BITS] <= wdata1[8*(STRB_WIDTH-1) +: LAST_LANE_BITS];
      end

      // Registered write-first (transparent) read port: a same-edge write
      // to the read address is forwarded (port 1 wins), matching the C++
      // runtime and register-file Mem semantics.
      if (ren) begin
        if (ra < DEPTH) begin
          rd = mem[ra];
        end else begin
          rd = {DATA_WIDTH{1'b0}};
        end
        if (wvalid0 && (wa0 == ra)) begin
          for (i = 0; i < STRB_WIDTH - 1; i = i + 1)
            if (wstrb0[i]) rd[8*i +: 8] = wdata0[8*i +: 8];
          if (wstrb0[STRB_WIDTH-1])
            rd[8*(STRB_WIDTH-1) +: LAST_LANE_BITS] = wdata0[8*(STRB_WIDTH-1) +: LAST_LANE_BITS];
        end
        if (wvalid1 && (wa1 == ra)) begin
          for (i = 0; i < STRB_WIDTH - 1; i = i + 1)
            if (wstrb1[i]) rd[8*i +: 8] = wdata1[8*i +: 8];
          if (wstrb1[STRB_WIDTH-1])
            rd[8*(STRB_WIDTH-1) +: LAST_LANE_BITS] = wdata1[8*(STRB_WIDTH-1) +: LAST_LANE_BITS];
        end
        rdata <= rd;
      end
  end
endmodule
