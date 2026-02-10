`include "../generated/issue_queue_2picker/issue_queue_2picker.v"

module tb_issue_queue_2picker;
  logic sys_clk;
  logic sys_rst;
  logic in_valid;
  logic [7:0] in_data;
  logic out0_ready;
  logic out1_ready;
  logic in_ready;
  logic out0_valid;
  logic [7:0] out0_data;
  logic out1_valid;
  logic [7:0] out1_data;

  issue_queue_2picker dut (
    .sys_clk(sys_clk),
    .sys_rst(sys_rst),
    .in_valid(in_valid),
    .in_data(in_data),
    .out0_ready(out0_ready),
    .out1_ready(out1_ready),
    .in_ready(in_ready),
    .out0_valid(out0_valid),
    .out0_data(out0_data),
    .out1_valid(out1_valid),
    .out1_data(out1_data)
  );

  int unsigned expected[$];

  string trace_dir;
  string vcd_path;
  string log_path;
  int log_fd;
  bit log_cycles;
  int unsigned cyc;

  task automatic cycle(input bit v, input logic [7:0] d, input bit r0, input bit r1);
    in_valid = v;
    in_data = d;
    out0_ready = r0;
    out1_ready = r1;
    #1;

    if (out0_valid && out0_ready) begin
      if (expected.size() == 0)
        $fatal(1, "unexpected pop0");
      if (out0_data !== expected[0])
        $fatal(1, "pop0 mismatch: got=0x%0h exp=0x%0h", out0_data, expected[0]);
      expected.pop_front();
    end

    if (out1_valid && out1_ready && out0_valid && out0_ready) begin
      if (expected.size() == 0)
        $fatal(1, "unexpected pop1");
      if (out1_data !== expected[0])
        $fatal(1, "pop1 mismatch: got=0x%0h exp=0x%0h", out1_data, expected[0]);
      expected.pop_front();
    end

    if (in_valid && in_ready)
      expected.push_back(in_data);

    if (log_fd != 0 && log_cycles) begin
      $fdisplay(
          log_fd,
          "%0d,%0t,%0b,%0b,0x%0h,%0b,%0b,%0b,0x%0h,%0b,0x%0h,%0d",
          cyc,
          $time,
          in_valid,
          in_ready,
          in_data,
          out0_valid,
          out0_ready,
          out1_valid,
          out0_data,
          out1_ready,
          out1_data,
          expected.size()
      );
    end
    cyc++;

    #4 sys_clk = ~sys_clk;
    #4 sys_clk = ~sys_clk;
  endtask

  initial begin
    sys_clk = 0;
    sys_rst = 1;
    in_valid = 0;
    in_data = 8'h00;
    out0_ready = 0;
    out1_ready = 0;
    log_fd = 0;
    cyc = 0;

    trace_dir = "examples/generated/tb_issue_queue_2picker";
    void'($value$plusargs("trace_dir=%s", trace_dir));

    // Tracing / logging (default: enabled; disable with +notrace / +nolog).
    vcd_path = {trace_dir, "/tb_issue_queue_2picker_sv.vcd"};
    log_path = {trace_dir, "/tb_issue_queue_2picker_sv.log"};
    void'($value$plusargs("vcd=%s", vcd_path));
    void'($value$plusargs("log=%s", log_path));
    log_cycles = $test$plusargs("logcycles");

    if (!$test$plusargs("notrace")) begin
      $display("tb_issue_queue_2picker: dumping VCD to %s", vcd_path);
      $dumpfile(vcd_path);
      $dumpvars(0, tb_issue_queue_2picker);
    end

    if (!$test$plusargs("nolog")) begin
      log_fd = $fopen(log_path, "w");
      $fdisplay(log_fd, "tb_issue_queue_2picker(SV)");
      $fdisplay(log_fd, "cycle,time,in_valid,in_ready,in_data,out0_valid,out0_ready,out1_valid,out0_data,out1_ready,out1_data,expected_depth");
    end

    #10;
    sys_rst = 0;

    cycle(1'b1, 8'h11, 1'b0, 1'b0);
    cycle(1'b1, 8'h22, 1'b0, 1'b0);
    cycle(1'b1, 8'h33, 1'b0, 1'b0);
    cycle(1'b1, 8'h44, 1'b0, 1'b0);
    cycle(1'b1, 8'h55, 1'b0, 1'b0);

    cycle(1'b0, 8'h00, 1'b1, 1'b1);
    cycle(1'b1, 8'h66, 1'b1, 1'b1);
    cycle(1'b0, 8'h00, 1'b1, 1'b0);
    cycle(1'b0, 8'h00, 1'b1, 1'b1);

    while (expected.size() != 0)
      cycle(1'b0, 8'h00, 1'b1, 1'b1);

    if (out0_valid || out1_valid)
      $fatal(1, "queue not empty at end");

    if (log_fd != 0) begin
      $fdisplay(log_fd, "PASS");
      $fclose(log_fd);
    end

    $display("tb_issue_queue_2picker: OK");
    $finish;
  end
endmodule
