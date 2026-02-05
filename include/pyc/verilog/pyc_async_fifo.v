// Async ready/valid FIFO with gray-code pointers (prototype).
//
// - Strict ready/valid handshake (no combinational cross-domain paths).
// - `DEPTH` must be a power of two and >= 2.
// - Synchronous resets (per domain).
//
// This is a minimal, synthesizable async FIFO suitable for CDC of data streams.
module pyc_async_fifo #(
  parameter WIDTH = 1,
  parameter DEPTH = 2
) (
  // Write domain (producer -> fifo)
  input               in_clk,
  input               in_rst,
  input               in_valid,
  output              in_ready,
  input  [WIDTH-1:0]  in_data,

  // Read domain (fifo -> consumer)
  input               out_clk,
  input               out_rst,
  output              out_valid,
  input               out_ready,
  output [WIDTH-1:0]  out_data
);
  `ifndef SYNTHESIS
  initial begin
    if (DEPTH < 2) begin
      $display("ERROR: pyc_async_fifo DEPTH must be >= 2");
      $finish;
    end
    if ((DEPTH & (DEPTH - 1)) != 0) begin
      $display("ERROR: pyc_async_fifo DEPTH must be a power of two");
      $finish;
    end
  end
  `endif

  function integer pyc_clog2;
    input integer value;
    integer i;
    begin
      pyc_clog2 = 0;
      for (i = value - 1; i > 0; i = i >> 1)
        pyc_clog2 = pyc_clog2 + 1;
    end
  endfunction

  localparam AW = pyc_clog2(DEPTH);

  // Storage.
  reg [WIDTH-1:0] mem [0:DEPTH-1];

  // --- pointer helpers ---
  function [AW:0] bin2gray;
    input [AW:0] b;
    begin
      bin2gray = (b >> 1) ^ b;
    end
  endfunction

  // --- write domain ---
  reg [AW:0] wptr_bin;
  reg [AW:0] wptr_gray;
  wire [AW:0] wptr_bin_next;
  wire [AW:0] wptr_gray_next;
  reg         wfull;

  // Read pointer gray (owned by read domain), referenced for synchronization.
  reg [AW:0] rptr_gray;

  reg [AW:0] rptr_gray_w1;
  reg [AW:0] rptr_gray_w2;

  wire wfull_next;
  wire do_push;

  assign in_ready = ~wfull;
  assign do_push = in_valid && in_ready;
  assign wptr_bin_next = wptr_bin + (do_push ? {{AW{1'b0}}, 1'b1} : {AW+1{1'b0}});
  assign wptr_gray_next = bin2gray(wptr_bin_next);
  assign wfull_next = (wptr_gray_next == {~rptr_gray_w2[AW:AW-1], rptr_gray_w2[AW-2:0]});

  integer wi;
  always @(posedge in_clk) begin
    if (in_rst) begin
      wptr_bin <= {AW+1{1'b0}};
      wptr_gray <= {AW+1{1'b0}};
      wfull <= 1'b0;
      rptr_gray_w1 <= {AW+1{1'b0}};
      rptr_gray_w2 <= {AW+1{1'b0}};
    end else begin
      // Sync read pointer into write clock domain.
      rptr_gray_w1 <= rptr_gray;
      rptr_gray_w2 <= rptr_gray_w1;

      if (do_push) begin
        mem[wptr_bin[AW-1:0]] <= in_data;
      end
      wptr_bin <= wptr_bin_next;
      wptr_gray <= wptr_gray_next;
      wfull <= wfull_next;
    end
  end

  reg [AW:0] rptr_bin;
  // rptr_gray is declared above (referenced by the write domain sync flops).
  wire [AW:0] rptr_bin_next;
  wire [AW:0] rptr_gray_next;

  reg [AW:0] wptr_gray_r1;
  reg [AW:0] wptr_gray_r2;

  reg out_valid_r;
  reg [WIDTH-1:0] out_data_r;

  wire empty_now;
  wire empty_next;
  wire do_pop;

  assign empty_now = (rptr_gray == wptr_gray_r2);
  assign out_valid = out_valid_r;
  assign out_data = out_data_r;

  assign do_pop = out_valid_r && out_ready;
  assign rptr_bin_next = rptr_bin + (do_pop ? {{AW{1'b0}}, 1'b1} : {AW+1{1'b0}});
  assign rptr_gray_next = bin2gray(rptr_bin_next);
  assign empty_next = (rptr_gray_next == wptr_gray_r2);

  integer ri;
  always @(posedge out_clk) begin
    if (out_rst) begin
      rptr_bin <= {AW+1{1'b0}};
      rptr_gray <= {AW+1{1'b0}};
      wptr_gray_r1 <= {AW+1{1'b0}};
      wptr_gray_r2 <= {AW+1{1'b0}};
      out_valid_r <= 1'b0;
      out_data_r <= {WIDTH{1'b0}};
    end else begin
      // Sync write pointer into read clock domain.
      wptr_gray_r1 <= wptr_gray;
      wptr_gray_r2 <= wptr_gray_r1;

      if (!out_valid_r) begin
        // Fill output register when data becomes available.
        if (!empty_now) begin
          out_valid_r <= 1'b1;
          out_data_r <= mem[rptr_bin[AW-1:0]];
        end
      end else if (do_pop) begin
        // Pop current word; either refill with next word or go empty.
        rptr_bin <= rptr_bin_next;
        rptr_gray <= rptr_gray_next;
        if (empty_next) begin
          out_valid_r <= 1'b0;
          out_data_r <= {WIDTH{1'b0}};
        end else begin
          out_valid_r <= 1'b1;
          out_data_r <= mem[rptr_bin_next[AW-1:0]];
        end
      end
    end
  end
endmodule
