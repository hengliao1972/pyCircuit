// Synchronous NxM memory with registered read data (prototype).
//
// - `DEPTH` is in entries (not bytes).
// - R_PORTS read ports, W_PORTS write ports; write ports apply in index
//   order (higher index wins same-address same-cycle conflicts).
// - READ_MODE = 0 (forward): a same-edge write to a read address is
//   forwarded to that read output (register-file semantics).
//   READ_MODE = 1 (old): read-during-write returns the pre-write data
//   (plain SRAM semantics), like pyc_sync_mem.
// - Port groups are packed LSB-first: port k occupies bits
//   [k*W +: W] of the corresponding vector port.
module pyc_sync_mem_nm #(
  parameter ADDR_WIDTH = 64,
  parameter DATA_WIDTH = 64,
  parameter DEPTH = 1024,
  parameter R_PORTS = 1,
  parameter W_PORTS = 1,
  parameter READ_MODE = 0
) (
  input                               clk,
  input  [R_PORTS-1:0]                rens,
  input  [R_PORTS*ADDR_WIDTH-1:0]     raddrs,
  output reg [R_PORTS*DATA_WIDTH-1:0] rdatas,
  input  [W_PORTS-1:0]                wvalids,
  input  [W_PORTS*ADDR_WIDTH-1:0]     waddrs,
  input  [W_PORTS*DATA_WIDTH-1:0]     wdatas,
  input  [W_PORTS*((DATA_WIDTH+7)/8)-1:0] wstrbs
);
  localparam ADDR_BITS = (ADDR_WIDTH < 64) ? ADDR_WIDTH : $clog2(DEPTH) + 1;
  localparam STRB_WIDTH = (DATA_WIDTH + 7) / 8;
  localparam LAST_LANE_BITS = (DATA_WIDTH % 8 == 0) ? 8 : DATA_WIDTH % 8;

  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

  integer k, j, i;

  always @(posedge clk) begin
    // Write ports apply in index order; for same-address same-cycle
    // conflicts the higher port's byte wins (later NBA to the same bits).
    for (k = 0; k < W_PORTS; k = k + 1) begin
      if (wvalids[k]) begin
        for (i = 0; i < STRB_WIDTH - 1; i = i + 1)
          if (wstrbs[k*STRB_WIDTH + i])
            mem[waddrs[k*ADDR_WIDTH +: ADDR_BITS]][8*i +: 8]
              <= wdatas[k*DATA_WIDTH +: DATA_WIDTH][8*i +: 8];
        if (wstrbs[k*STRB_WIDTH + STRB_WIDTH-1])
          mem[waddrs[k*ADDR_WIDTH +: ADDR_BITS]][8*(STRB_WIDTH-1) +: LAST_LANE_BITS]
            <= wdatas[k*DATA_WIDTH +: DATA_WIDTH][8*(STRB_WIDTH-1) +: LAST_LANE_BITS];
      end
    end

    // Registered read ports. The plain read below is the old-data path
    // (NBA right-hand sides evaluate the pre-edge array). READ_MODE = 0
    // additionally forwards matching same-edge writes, port by port in
    // index order (later NBAs to the same bits win).
    for (j = 0; j < R_PORTS; j = j + 1) begin
      if (rens[j]) begin
        rdatas[j*DATA_WIDTH +: DATA_WIDTH] <= mem[raddrs[j*ADDR_WIDTH +: ADDR_BITS]];
        if (READ_MODE == 0) begin
          for (k = 0; k < W_PORTS; k = k + 1) begin
            if (wvalids[k] &&
                (waddrs[k*ADDR_WIDTH +: ADDR_BITS] ==
                 raddrs[j*ADDR_WIDTH +: ADDR_BITS])) begin
              for (i = 0; i < STRB_WIDTH - 1; i = i + 1)
                if (wstrbs[k*STRB_WIDTH + i])
                  rdatas[j*DATA_WIDTH + 8*i +: 8]
                    <= wdatas[k*DATA_WIDTH + 8*i +: 8];
              if (wstrbs[k*STRB_WIDTH + STRB_WIDTH-1])
                rdatas[j*DATA_WIDTH + 8*(STRB_WIDTH-1) +: LAST_LANE_BITS]
                  <= wdatas[k*DATA_WIDTH + 8*(STRB_WIDTH-1) +: LAST_LANE_BITS];
            end
          end
        end
      end
    end
  end
endmodule
