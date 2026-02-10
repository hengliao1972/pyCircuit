module tb_fastfwd_pyc;
  // Exam-style top wrapper (generated): EXAM2021_TOP
  logic clk = 1'b0;
  logic rst_n = 1'b0;

  logic lane0_pkt_in_vld;
  logic lane1_pkt_in_vld;
  logic lane2_pkt_in_vld;
  logic lane3_pkt_in_vld;
  logic [127:0] lane0_pkt_in_data;
  logic [127:0] lane1_pkt_in_data;
  logic [127:0] lane2_pkt_in_data;
  logic [127:0] lane3_pkt_in_data;
  logic [4:0] lane0_pkt_in_ctrl;
  logic [4:0] lane1_pkt_in_ctrl;
  logic [4:0] lane2_pkt_in_ctrl;
  logic [4:0] lane3_pkt_in_ctrl;

  logic lane0_pkt_out_vld;
  logic lane1_pkt_out_vld;
  logic lane2_pkt_out_vld;
  logic lane3_pkt_out_vld;
  logic [127:0] lane0_pkt_out_data;
  logic [127:0] lane1_pkt_out_data;
  logic [127:0] lane2_pkt_out_data;
  logic [127:0] lane3_pkt_out_data;
  wire pkt_in_bkpr;

  EXAM2021_TOP dut (
      .clk(clk),
      .rst_n(rst_n),
      .lane0_pkt_in_vld(lane0_pkt_in_vld),
      .lane1_pkt_in_vld(lane1_pkt_in_vld),
      .lane2_pkt_in_vld(lane2_pkt_in_vld),
      .lane3_pkt_in_vld(lane3_pkt_in_vld),
      .lane0_pkt_in_data(lane0_pkt_in_data),
      .lane1_pkt_in_data(lane1_pkt_in_data),
      .lane2_pkt_in_data(lane2_pkt_in_data),
      .lane3_pkt_in_data(lane3_pkt_in_data),
      .lane0_pkt_in_ctrl(lane0_pkt_in_ctrl),
      .lane1_pkt_in_ctrl(lane1_pkt_in_ctrl),
      .lane2_pkt_in_ctrl(lane2_pkt_in_ctrl),
      .lane3_pkt_in_ctrl(lane3_pkt_in_ctrl),
      .lane0_pkt_out_vld(lane0_pkt_out_vld),
      .lane1_pkt_out_vld(lane1_pkt_out_vld),
      .lane2_pkt_out_vld(lane2_pkt_out_vld),
      .lane3_pkt_out_vld(lane3_pkt_out_vld),
      .lane0_pkt_out_data(lane0_pkt_out_data),
      .lane1_pkt_out_data(lane1_pkt_out_data),
      .lane2_pkt_out_data(lane2_pkt_out_data),
      .lane3_pkt_out_data(lane3_pkt_out_data),
      .pkt_in_bkpr(pkt_in_bkpr)
  );

  always #5 clk = ~clk;

  // Config (override with plusargs):
  // - +max_cycles=<N> : number of cycles to send traffic
  // - +max_pkts=<N>   : max packets to send
  int unsigned max_cycles = 2000;
  int unsigned max_pkts = 4000;
  int unsigned seed = 1;

  // Tracing / logging (default: enabled; disable with +notrace / +nolog).
  string vcd_path;
  string log_path;
  int log_fd;
  bit log_cycles;

  // Optional cross-reference traces.
  string stim_path;
  int stim_fd;
  bit use_stim;
  bit stim_eof;
  string out_trace_path;
  int out_trace_fd;
  int unsigned out_index;

  // Expected model (fixed-size ring to avoid SV queues for tool compatibility).
  localparam int unsigned MAX_PKTS = 32768;
  reg [127:0] expected_by_seq [0:MAX_PKTS-1];
  reg [127:0] expected_q [0:MAX_PKTS-1];
  reg [31:0] expected_seq_q [0:MAX_PKTS-1];
  int unsigned exp_head;
  int unsigned exp_tail;
  int unsigned exp_count;

  int unsigned sent;
  int unsigned got;
  int unsigned bkpr_cycles;
  int unsigned cyc;
  int unsigned out_ptr;

  // Scratch vars (declared at module scope for Icarus compatibility).
  integer lane;
  integer k;
  integer produced;
  int unsigned ptr;
  int unsigned lane2;
  reg heavy;
  reg do_send;
  int unsigned p;
  int unsigned seq;
  int unsigned maxDep;
  int unsigned dep;
  int unsigned total;
  int unsigned r;
  int unsigned lat;
  reg [4:0] ctrl;
  reg [127:0] data;
  reg [127:0] dp_data;
  reg [127:0] fwded;
  reg [127:0] exp;
  reg [127:0] got_data;
  reg [31:0] exp_seq;
  reg v_sel;
  reg [127:0] d_sel;
  reg [4:0] c_sel;
  real throughput;
  int unsigned stim_cyc;
  int unsigned stim_bkpr;
  int unsigned stim_v0, stim_v1, stim_v2, stim_v3;
  reg [127:0] stim_d0, stim_d1, stim_d2, stim_d3;
  reg [4:0] stim_c0, stim_c1, stim_c2, stim_c3;
  int stim_rc;

  function automatic logic out_vld(input int unsigned lane);
    case (lane & 3)
      0: out_vld = lane0_pkt_out_vld;
      1: out_vld = lane1_pkt_out_vld;
      2: out_vld = lane2_pkt_out_vld;
      default: out_vld = lane3_pkt_out_vld;
    endcase
  endfunction

  function automatic logic [127:0] out_data(input int unsigned lane);
    case (lane & 3)
      0: out_data = lane0_pkt_out_data;
      1: out_data = lane1_pkt_out_data;
      2: out_data = lane2_pkt_out_data;
      default: out_data = lane3_pkt_out_data;
    endcase
  endfunction

  task automatic drive_inputs_clear;
    lane0_pkt_in_vld = 1'b0;
    lane1_pkt_in_vld = 1'b0;
    lane2_pkt_in_vld = 1'b0;
    lane3_pkt_in_vld = 1'b0;
    lane0_pkt_in_data = 128'd0;
    lane1_pkt_in_data = 128'd0;
    lane2_pkt_in_data = 128'd0;
    lane3_pkt_in_data = 128'd0;
    lane0_pkt_in_ctrl = 5'd0;
    lane1_pkt_in_ctrl = 5'd0;
    lane2_pkt_in_ctrl = 5'd0;
    lane3_pkt_in_ctrl = 5'd0;
  endtask

  function automatic logic [127:0] rand128;
    rand128 = {$urandom, $urandom, $urandom, $urandom};
  endfunction

  initial begin
    drive_inputs_clear();

    if ($value$plusargs("max_cycles=%d", max_cycles)) begin end
    if ($value$plusargs("max_pkts=%d", max_pkts)) begin end
    if ($value$plusargs("seed=%d", seed)) begin end

    // Clamp.
    if (max_pkts > MAX_PKTS)
      max_pkts = MAX_PKTS;

    vcd_path = "examples/generated/fastfwd_pyc/tb_fastfwd_pyc_sv.vcd";
    log_path = "examples/generated/fastfwd_pyc/tb_fastfwd_pyc_sv.log";
    void'($value$plusargs("vcd=%s", vcd_path));
    void'($value$plusargs("log=%s", log_path));
    log_cycles = $test$plusargs("logcycles");

    if (!$test$plusargs("notrace")) begin
      $display("tb_fastfwd_pyc: dumping VCD to %s", vcd_path);
      $dumpfile(vcd_path);
      $dumpvars(0, tb_fastfwd_pyc);
    end

    log_fd = 0;
    if (!$test$plusargs("nolog")) begin
      log_fd = $fopen(log_path, "w");
      $fdisplay(log_fd, "tb_fastfwd_pyc(SV): seed=%0d max_cycles=%0d max_pkts=%0d", seed, max_cycles, max_pkts);
      $fdisplay(log_fd, "cycle,time,bkpr,sent,got,exp_depth,in0_v,in1_v,in2_v,in3_v,out0_v,out1_v,out2_v,out3_v");
    end

    use_stim = 0;
    stim_eof = 0;
    stim_fd = 0;
    if ($value$plusargs("stim=%s", stim_path)) begin
      use_stim = 1;
      stim_fd = $fopen(stim_path, "r");
      if (stim_fd == 0) begin
        $fatal(1, "FAIL: cannot open +stim=%s", stim_path);
      end
      $display("tb_fastfwd_pyc: using stimulus from %s", stim_path);
    end

    out_trace_fd = 0;
    out_index = 0;
    out_trace_path = "examples/generated/fastfwd_pyc/tb_fastfwd_pyc_sv.out.log";
    void'($value$plusargs("out_trace=%s", out_trace_path));
    if (!$test$plusargs("noouttrace")) begin
      out_trace_fd = $fopen(out_trace_path, "w");
      if (out_trace_fd == 0) begin
        $fatal(1, "FAIL: cannot open out_trace=%s", out_trace_path);
      end
    end

    exp_head = 0;
    exp_tail = 0;
    exp_count = 0;
    sent = 0;
    got = 0;
    bkpr_cycles = 0;
    cyc = 0;
    out_ptr = 0;

    // Reset: async assert, sync deassert.
    rst_n = 1'b0;
    repeat (5) @(posedge clk);
    rst_n = 1'b1;

    // Seed randomness.
    void'($urandom(seed));

    // Main loop: send for max_cycles, then drain until all expected packets are seen.
    while ((cyc < max_cycles) || (exp_count != 0)) begin
      // Drive inputs on negedge (so they are stable for the next posedge).
      @(negedge clk);
      drive_inputs_clear();

      if (!rst_n) begin
        // Nothing.
      end else if (use_stim && !stim_eof && (cyc < max_cycles)) begin
        stim_rc = $fscanf(
            stim_fd,
            "%d %d %d %h %h %d %h %h %d %h %h %d %h %h\n",
            stim_cyc,
            stim_bkpr,
            stim_v0,
            stim_d0,
            stim_c0,
            stim_v1,
            stim_d1,
            stim_c1,
            stim_v2,
            stim_d2,
            stim_c2,
            stim_v3,
            stim_d3,
            stim_c3
        );
        if (stim_rc != 14) begin
          stim_eof = 1;
        end else begin
          if (stim_cyc != cyc) begin
            $fatal(1, "FAIL: stim cycle mismatch: expected=%0d got=%0d", cyc, stim_cyc);
          end
          if (stim_bkpr != (pkt_in_bkpr ? 1 : 0)) begin
            $fatal(1, "FAIL: bkpr mismatch at cyc=%0d stim=%0d dut=%0d", cyc, stim_bkpr, (pkt_in_bkpr ? 1 : 0));
          end

          // Apply the recorded inputs.
          lane0_pkt_in_vld = (stim_v0 != 0);
          lane1_pkt_in_vld = (stim_v1 != 0);
          lane2_pkt_in_vld = (stim_v2 != 0);
          lane3_pkt_in_vld = (stim_v3 != 0);
          lane0_pkt_in_data = stim_d0;
          lane1_pkt_in_data = stim_d1;
          lane2_pkt_in_data = stim_d2;
          lane3_pkt_in_data = stim_d3;
          lane0_pkt_in_ctrl = stim_c0;
          lane1_pkt_in_ctrl = stim_c1;
          lane2_pkt_in_ctrl = stim_c2;
          lane3_pkt_in_ctrl = stim_c3;

          if (pkt_in_bkpr && (lane0_pkt_in_vld || lane1_pkt_in_vld || lane2_pkt_in_vld || lane3_pkt_in_vld)) begin
            $fatal(1, "FAIL: stim inject while backpressured at cyc=%0d", cyc);
          end

          // Build expected model in lane order.
          for (lane = 0; lane < 4; lane = lane + 1) begin
            v_sel = 1'b0;
            d_sel = 128'd0;
            c_sel = 5'd0;
            case (lane)
              0: begin v_sel = lane0_pkt_in_vld; d_sel = lane0_pkt_in_data; c_sel = lane0_pkt_in_ctrl; end
              1: begin v_sel = lane1_pkt_in_vld; d_sel = lane1_pkt_in_data; c_sel = lane1_pkt_in_ctrl; end
              2: begin v_sel = lane2_pkt_in_vld; d_sel = lane2_pkt_in_data; c_sel = lane2_pkt_in_ctrl; end
              default: begin v_sel = lane3_pkt_in_vld; d_sel = lane3_pkt_in_data; c_sel = lane3_pkt_in_ctrl; end
            endcase
            if (v_sel) begin
              if (sent >= max_pkts) begin
                $fatal(1, "FAIL: stim exceeds max_pkts at cyc=%0d", cyc);
              end
              seq = sent;
              dep = c_sel[4:2];
              if (dep != 0 && seq < dep) begin
                $fatal(1, "FAIL: invalid dep at cyc=%0d seq=%0d dep=%0d", cyc, seq, dep);
              end
              dp_data = (dep != 0) ? expected_by_seq[seq - dep] : 128'd0;
              fwded = d_sel + dp_data;
              expected_by_seq[seq] = fwded;
              expected_q[exp_tail] = fwded;
              expected_seq_q[exp_tail] = seq[31:0];
              exp_tail = (exp_tail + 1) % MAX_PKTS;
              exp_count++;
              sent++;
            end
          end
        end
      end else if (!pkt_in_bkpr && (cyc < max_cycles) && (sent < max_pkts)) begin
        // Each lane independently sends with some probability; distribution chosen
        // to exercise both medium and heavy cases without being too slow.
        heavy = (cyc >= (max_cycles / 2));

        for (lane = 0; lane < 4; lane = lane + 1) begin
          if (sent < max_pkts) begin
            p = heavy ? 85 : 45; // percent
            do_send = (($urandom % 100) < p);
            if (do_send) begin
              seq = sent;

              // dep distribution: 0 has weight 14, 1..maxDep each has weight 1.
              maxDep = (seq < 7) ? seq : 7;
              dep = 0;
              if (maxDep != 0) begin
                total = 14 + maxDep;
                r = ($urandom % total);
                dep = (r < 14) ? 0 : (r - 13); // 1..maxDep
              end

              lat = ($urandom % 4); // 0..3 => latency 1..4
              ctrl = {dep[2:0], lat[1:0]};

              data = rand128();
              dp_data = (dep != 0) ? expected_by_seq[seq - dep] : 128'd0;
              fwded = data + dp_data;

              expected_by_seq[seq] = fwded;
              expected_q[exp_tail] = fwded;
              expected_seq_q[exp_tail] = seq[31:0];
              exp_tail = (exp_tail + 1) % MAX_PKTS;
              exp_count++;
              sent++;

              case (lane)
                0: begin lane0_pkt_in_vld = 1'b1; lane0_pkt_in_data = data; lane0_pkt_in_ctrl = ctrl; end
                1: begin lane1_pkt_in_vld = 1'b1; lane1_pkt_in_data = data; lane1_pkt_in_ctrl = ctrl; end
                2: begin lane2_pkt_in_vld = 1'b1; lane2_pkt_in_data = data; lane2_pkt_in_ctrl = ctrl; end
                default: begin lane3_pkt_in_vld = 1'b1; lane3_pkt_in_data = data; lane3_pkt_in_ctrl = ctrl; end
              endcase
            end
          end
        end
      end

      // Sample outputs after the posedge (registered outputs).
      @(posedge clk);

      if (pkt_in_bkpr)
        bkpr_cycles++;

      // Output ordering requirement:
      // - Next expected appears on out_ptr (cyclic lane pointer)
      // - Up to 4 packets per cycle on consecutive lanes
      // - After first empty lane, remaining lanes must be empty
      ptr = out_ptr;
      produced = 0;
      while ((produced < 4) && out_vld(ptr)) begin

        if (exp_count == 0) begin
          $fatal(1, "FAIL: unexpected output (queue empty) at cyc=%0d lane=%0d", cyc, ptr);
        end

        exp = expected_q[exp_head];
        exp_seq = expected_seq_q[exp_head];
        exp_head = (exp_head + 1) % MAX_PKTS;
        exp_count--;

        got_data = out_data(ptr);
        if (got_data !== exp) begin
          $fatal(1, "FAIL: data mismatch at cyc=%0d lane=%0d got=0x%032x exp=0x%032x", cyc, ptr, got_data, exp);
        end
        got++;
        if (out_trace_fd != 0) begin
          $fdisplay(out_trace_fd, "%0d %0d %0d %0d %032h", out_index, cyc, ptr, exp_seq, got_data);
          out_index++;
        end
        ptr = (ptr + 1) & 3;
        produced = produced + 1;
      end

      // Verify no holes after first empty lane (rotated order).
      for (k = produced; k < 4; k = k + 1) begin
        lane2 = (out_ptr + k) & 3;
        if (out_vld(lane2)) begin
          $fatal(1, "FAIL: output hole at cyc=%0d out_ptr=%0d saw empty then valid on lane=%0d", cyc, out_ptr, lane2);
        end
      end

      out_ptr = ptr;

      if (log_fd != 0 && log_cycles) begin
        $fdisplay(
            log_fd,
            "%0d,%0t,%0b,%0d,%0d,%0d,%0b,%0b,%0b,%0b,%0b,%0b,%0b,%0b",
            cyc, $time, pkt_in_bkpr, sent, got, exp_count,
            lane0_pkt_in_vld, lane1_pkt_in_vld, lane2_pkt_in_vld, lane3_pkt_in_vld,
            lane0_pkt_out_vld, lane1_pkt_out_vld, lane2_pkt_out_vld, lane3_pkt_out_vld
        );
      end

      cyc++;
      if (cyc > (max_cycles + 20000) && exp_count != 0) begin
        $fatal(1, "FAIL: timeout draining outstanding packets (outstanding=%0d)", exp_count);
      end
    end

    if (log_fd != 0) begin
      throughput = (cyc == 0) ? 0.0 : (1.0 * got) / (1.0 * cyc);
      $fdisplay(
          log_fd,
          "PASS: sent=%0d got=%0d cycles=%0d throughput=%0f bkpr=%0f%%",
          sent, got, cyc, throughput, (100.0 * bkpr_cycles) / (1.0 * cyc)
      );
      $fclose(log_fd);
    end

    $display("PASS: FastFwd sent=%0d got=%0d cycles=%0d", sent, got, cyc);
    if (stim_fd != 0)
      $fclose(stim_fd);
    if (out_trace_fd != 0)
      $fclose(out_trace_fd);
    $finish;
  end
endmodule
