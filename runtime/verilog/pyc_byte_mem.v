// Byte-addressed memory (prototype).
//
// - `DEPTH` is in bytes.
// - Combinational little-endian read window.
// - Byte-enable write on posedge.
module pyc_byte_mem #(
  parameter ADDR_WIDTH = 64,
  parameter DATA_WIDTH = 64,
  parameter DEPTH = 1024,
  // Optional init file (Vivado: can be used for BRAM init; simulation: $readmemh).
  // If the file cannot be opened, initialization is skipped.
  parameter INIT_MEMH = ""
) (
  input                   clk,
  input                   rst,

  input  [ADDR_WIDTH-1:0] raddr,
  output reg [DATA_WIDTH-1:0] rdata,

  input                   wvalid,
  input  [ADDR_WIDTH-1:0] waddr,
  input  [DATA_WIDTH-1:0] wdata,
  input  [(DATA_WIDTH+7)/8-1:0] wstrb
);
  localparam STRB_WIDTH = (DATA_WIDTH + 7) / 8;

  // Byte storage.
  reg [7:0] mem [0:DEPTH-1];

  // Optional initialization.
  integer init_fd;
  integer init_i;
  initial begin
    `ifndef SYNTHESIS
    // Deterministic simulation init: keep C++/Verilog equivalence stable.
    for (init_i = 0; init_i < DEPTH; init_i = init_i + 1)
      mem[init_i] = 8'h00;
    `endif
    if (INIT_MEMH != "") begin
      init_fd = $fopen(INIT_MEMH, "r");
      if (init_fd != 0) begin
        $fclose(init_fd);
        $readmemh(INIT_MEMH, mem);
      end
    end
  end

  // Combinational read: assemble DATA_WIDTH bits from successive bytes.
  integer i;
  integer a;
  always @* begin
    a = raddr[31:0];
    rdata = {DATA_WIDTH{1'b0}};
    for (i = 0; i < STRB_WIDTH; i = i + 1) begin
      if ((a + i) < DEPTH)
        rdata[8 * i +: 8] = mem[a + i];
    end
  end

  // Byte-enable write.
  integer j;
  integer wa;
  always @(posedge clk) begin
    if (rst) begin
      // Reset does not clear memory (init happens via INIT_MEMH or sim-default 0s).
    end else if (wvalid) begin
      wa = waddr[31:0];
      for (j = 0; j < STRB_WIDTH; j = j + 1) begin
        if (wstrb[j] && ((wa + j) < DEPTH))
          mem[wa + j] <= wdata[8 * j +: 8];
      end
    end
  end
endmodule
