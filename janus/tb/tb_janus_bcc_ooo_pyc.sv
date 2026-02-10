module tb_janus_bcc_ooo_pyc;
  logic clk;
  logic rst;

  logic [63:0] boot_pc;
  logic [63:0] boot_sp;

  logic halted;
  logic [63:0] cycles;
  logic [63:0] pc;
  logic [63:0] fpc;
  logic [63:0] a0;
  logic [63:0] a1;
  logic [63:0] ra;
  logic [63:0] sp;

  logic [11:0] commit_op;
  logic commit_fire;
  logic [63:0] commit_value;
  logic [1:0] commit_dst_kind;
  logic [5:0] commit_dst_areg;
  logic [5:0] commit_pdst;
  logic [4:0] rob_count;
  logic [2:0] br_kind;
  logic commit_cond;
  logic [63:0] commit_tgt;
  logic [63:0] br_base_pc;
  logic [63:0] br_off;

  logic commit_store_fire;
  logic [63:0] commit_store_addr;
  logic [63:0] commit_store_data;
  logic [3:0] commit_store_size;

  logic commit_fire0;
  logic commit_fire1;
  logic commit_fire2;
  logic commit_fire3;
  logic [63:0] commit_pc0;
  logic [63:0] commit_pc1;
  logic [63:0] commit_pc2;
  logic [63:0] commit_pc3;
  logic [3:0] commit_rob0;
  logic [3:0] commit_rob1;
  logic [3:0] commit_rob2;
  logic [3:0] commit_rob3;
  logic [11:0] commit_op0;
  logic [11:0] commit_op1;
  logic [11:0] commit_op2;
  logic [11:0] commit_op3;
  logic [63:0] commit_value0;
  logic [63:0] commit_value1;
  logic [63:0] commit_value2;
  logic [63:0] commit_value3;

  logic [63:0] ct0;
  logic [63:0] cu0;
  logic [63:0] st0;
  logic [63:0] su0;

  logic issue_fire;
  logic [11:0] issue_op;
  logic [63:0] issue_pc;
  logic [3:0] issue_rob;
  logic issue_fire0;
  logic issue_fire1;
  logic issue_fire2;
  logic issue_fire3;
  logic [63:0] issue_pc0;
  logic [63:0] issue_pc1;
  logic [63:0] issue_pc2;
  logic [63:0] issue_pc3;
  logic [3:0] issue_rob0;
  logic [3:0] issue_rob1;
  logic [3:0] issue_rob2;
  logic [3:0] issue_rob3;
  logic [11:0] issue_op0;
  logic [11:0] issue_op1;
  logic [11:0] issue_op2;
  logic [11:0] issue_op3;
  logic [5:0] issue_sl;
  logic [5:0] issue_sr;
  logic [5:0] issue_sp;
  logic [5:0] issue_pdst;
  logic [63:0] issue_sl_val;
  logic [63:0] issue_sr_val;
  logic [63:0] issue_sp_val;
  logic issue_is_load;
  logic issue_is_store;
  logic store_pending;
  logic store_pending_older;
  logic [63:0] mem_raddr;
  logic dispatch_fire;
  logic [11:0] dec_op;
  logic dispatch_fire0;
  logic dispatch_fire1;
  logic dispatch_fire2;
  logic dispatch_fire3;
  logic [63:0] dispatch_pc0;
  logic [63:0] dispatch_pc1;
  logic [63:0] dispatch_pc2;
  logic [63:0] dispatch_pc3;
  logic [3:0] dispatch_rob0;
  logic [3:0] dispatch_rob1;
  logic [3:0] dispatch_rob2;
  logic [3:0] dispatch_rob3;
  logic [11:0] dispatch_op0;
  logic [11:0] dispatch_op1;
  logic [11:0] dispatch_op2;
  logic [11:0] dispatch_op3;

  logic mmio_uart_valid;
  logic [7:0] mmio_uart_data;
  logic mmio_exit_valid;
  logic [31:0] mmio_exit_code;

  janus_bcc_ooo_pyc dut (
      .clk(clk),
      .rst(rst),
      .boot_pc(boot_pc),
      .boot_sp(boot_sp),
      .host_wvalid(1'b0),
      .host_waddr(64'd0),
      .host_wdata(64'd0),
      .host_wstrb(8'd0),
      .halted(halted),
      .cycles(cycles),
      .pc(pc),
      .fpc(fpc),
      .a0(a0),
      .a1(a1),
      .ra(ra),
      .sp(sp),
      .commit_op(commit_op),
      .commit_fire(commit_fire),
      .commit_value(commit_value),
      .commit_dst_kind(commit_dst_kind),
      .commit_dst_areg(commit_dst_areg),
      .commit_pdst(commit_pdst),
      .br_kind(br_kind),
      .commit_cond(commit_cond),
      .commit_tgt(commit_tgt),
      .br_base_pc(br_base_pc),
      .br_off(br_off),
      .commit_store_fire(commit_store_fire),
      .commit_store_addr(commit_store_addr),
      .commit_store_data(commit_store_data),
      .commit_store_size(commit_store_size),
      .commit_fire0(commit_fire0),
      .commit_pc0(commit_pc0),
      .commit_rob0(commit_rob0),
      .commit_op0(commit_op0),
      .commit_value0(commit_value0),
      .commit_fire1(commit_fire1),
      .commit_pc1(commit_pc1),
      .commit_rob1(commit_rob1),
      .commit_op1(commit_op1),
      .commit_value1(commit_value1),
      .commit_fire2(commit_fire2),
      .commit_pc2(commit_pc2),
      .commit_rob2(commit_rob2),
      .commit_op2(commit_op2),
      .commit_value2(commit_value2),
      .commit_fire3(commit_fire3),
      .commit_pc3(commit_pc3),
      .commit_rob3(commit_rob3),
      .commit_op3(commit_op3),
      .commit_value3(commit_value3),
      .rob_count(rob_count),
      .ct0(ct0),
      .cu0(cu0),
      .st0(st0),
      .su0(su0),
      .issue_fire(issue_fire),
      .issue_op(issue_op),
      .issue_pc(issue_pc),
      .issue_rob(issue_rob),
      .issue_fire0(issue_fire0),
      .issue_pc0(issue_pc0),
      .issue_rob0(issue_rob0),
      .issue_op0(issue_op0),
      .issue_fire1(issue_fire1),
      .issue_pc1(issue_pc1),
      .issue_rob1(issue_rob1),
      .issue_op1(issue_op1),
      .issue_fire2(issue_fire2),
      .issue_pc2(issue_pc2),
      .issue_rob2(issue_rob2),
      .issue_op2(issue_op2),
      .issue_fire3(issue_fire3),
      .issue_pc3(issue_pc3),
      .issue_rob3(issue_rob3),
      .issue_op3(issue_op3),
      .issue_sl(issue_sl),
      .issue_sr(issue_sr),
      .issue_sp(issue_sp),
      .issue_pdst(issue_pdst),
      .issue_sl_val(issue_sl_val),
      .issue_sr_val(issue_sr_val),
      .issue_sp_val(issue_sp_val),
      .issue_is_load(issue_is_load),
      .issue_is_store(issue_is_store),
      .store_pending(store_pending),
      .store_pending_older(store_pending_older),
      .mem_raddr(mem_raddr),
      .dispatch_fire(dispatch_fire),
      .dec_op(dec_op),
      .dispatch_fire0(dispatch_fire0),
      .dispatch_pc0(dispatch_pc0),
      .dispatch_rob0(dispatch_rob0),
      .dispatch_op0(dispatch_op0),
      .dispatch_fire1(dispatch_fire1),
      .dispatch_pc1(dispatch_pc1),
      .dispatch_rob1(dispatch_rob1),
      .dispatch_op1(dispatch_op1),
      .dispatch_fire2(dispatch_fire2),
      .dispatch_pc2(dispatch_pc2),
      .dispatch_rob2(dispatch_rob2),
      .dispatch_op2(dispatch_op2),
      .dispatch_fire3(dispatch_fire3),
      .dispatch_pc3(dispatch_pc3),
      .dispatch_rob3(dispatch_rob3),
      .dispatch_op3(dispatch_op3),
      .mmio_uart_valid(mmio_uart_valid),
      .mmio_uart_data(mmio_uart_data),
      .mmio_exit_valid(mmio_exit_valid),
      .mmio_exit_code(mmio_exit_code)
  );

  always #5 clk = ~clk;

  function automatic logic [31:0] mem_read32(input int unsigned addr);
    mem_read32 = {dut.mem.mem[addr + 3], dut.mem.mem[addr + 2], dut.mem.mem[addr + 1], dut.mem.mem[addr + 0]};
  endfunction

  string memh_path;
  string vcd_path;
  string log_path;
  int log_fd;
  bit log_commits;
  string konata_path;
  int konata_fd;
  bit konata_on;
  longint unsigned konata_cur_cycle;
  longint unsigned konata_next_id;
  longint unsigned konata_id_by_rob[int];
  bit konata_stage_by_rob[int];
  int konata_lane_by_rob[int];

  longint unsigned max_cycles;
  longint unsigned expected_mem100;
  longint unsigned expected_a0;
  longint unsigned expected_exit;
  bit has_expected_mem100;
  bit has_expected_a0;
  bit has_expected_exit;
  bit has_expected_exit_pc;
  logic [31:0] got_mem100;
  logic [31:0] got_exit_code;
  longint unsigned i;
  longint unsigned expected_exit_pc;
  int unsigned exit_pc_stable_cycles;
  int unsigned exit_pc_stable;
  bit done;

  task automatic konata_at_cycle(input longint unsigned cyc);
    if (!konata_on)
      return;
    if (cyc < konata_cur_cycle)
      return;
    if (cyc == konata_cur_cycle)
      return;
    $fdisplay(konata_fd, "C\t%0d", (cyc - konata_cur_cycle));
    konata_cur_cycle = cyc;
  endtask

  task automatic konata_insn(input longint unsigned file_id, input longint unsigned sim_id, input longint unsigned thread_id);
    if (konata_on)
      $fdisplay(konata_fd, "I\t%0d\t%0d\t%0d", file_id, sim_id, thread_id);
  endtask

  task automatic konata_label(input longint unsigned id, input int kind, input string msg);
    if (konata_on)
      $fdisplay(konata_fd, "L\t%0d\t%0d\t%s", id, kind, msg);
  endtask

  task automatic konata_stage_start(input longint unsigned id, input int lane, input string stage);
    if (konata_on)
      $fdisplay(konata_fd, "S\t%0d\t%0d\t%s", id, lane, stage);
  endtask

  task automatic konata_stage_end(input longint unsigned id, input int lane, input string stage);
    if (konata_on)
      $fdisplay(konata_fd, "E\t%0d\t%0d\t%s", id, lane, stage);
  endtask

  task automatic konata_retire(input longint unsigned id, input longint unsigned retire_id, input int kind);
    if (konata_on)
      $fdisplay(konata_fd, "R\t%0d\t%0d\t%0d", id, retire_id, kind);
  endtask

  task automatic pv_squash(input int rob);
    longint unsigned id;
    int lane;
    if (!konata_on)
      return;
    if (!konata_id_by_rob.exists(rob))
      return;
    id = konata_id_by_rob[rob];
    lane = konata_lane_by_rob.exists(rob) ? konata_lane_by_rob[rob] : 0;
    if (konata_stage_by_rob[rob] == 0)
      konata_stage_end(id, lane, "S2");
    else
      konata_stage_end(id, lane, "ROB");
    konata_label(id, 1, "squash");
    konata_retire(id, id, 1);
    konata_id_by_rob.delete(rob);
    konata_stage_by_rob.delete(rob);
    konata_lane_by_rob.delete(rob);
  endtask

  task automatic pv_dispatch(input int slot, input int rob, input logic [63:0] pc, input logic [11:0] op);
    longint unsigned id;
    pv_squash(rob);
    id = konata_next_id;
    konata_next_id++;
    konata_id_by_rob[rob] = id;
    konata_stage_by_rob[rob] = 0;
    konata_lane_by_rob[rob] = slot;
    konata_insn(id, pc, 0);
    konata_label(id, 0, $sformatf("pc=0x%016x op=%0d rob=%0d disp_slot=%0d", pc, op, rob, slot));
    // Stage naming follows janus/PLAN.md (OOO bring-up uses dispatch/issue/commit hooks).
    konata_stage_start(id, slot, "F4");
    konata_stage_end(id, slot, "F4");
    konata_stage_start(id, slot, "D1");
    konata_stage_end(id, slot, "D1");
    konata_stage_start(id, slot, "D2");
    konata_stage_end(id, slot, "D2");
    konata_stage_start(id, slot, "D3");
    konata_stage_end(id, slot, "D3");
    konata_stage_start(id, slot, "S2");
  endtask

  task automatic pv_issue(input int slot, input int rob, input logic [63:0] pc, input logic [11:0] op);
    longint unsigned id;
    int lane;
    if (!konata_on)
      return;
    if (!konata_id_by_rob.exists(rob))
      return;
    if (konata_stage_by_rob[rob] != 0)
      return;
    id = konata_id_by_rob[rob];
    lane = konata_lane_by_rob.exists(rob) ? konata_lane_by_rob[rob] : 0;
    konata_stage_end(id, lane, "S2");
    konata_stage_start(id, lane, "ROB");
    konata_stage_by_rob[rob] = 1;
    konata_label(id, 1, $sformatf("issue pc=0x%016x op=%0d slot=%0d", pc, op, slot));
  endtask

  task automatic pv_commit(input int slot, input int rob, input logic [63:0] pc, input logic [11:0] op);
    longint unsigned id;
    int lane;
    if (!konata_on)
      return;
    if (!konata_id_by_rob.exists(rob))
      return;
    id = konata_id_by_rob[rob];
    lane = konata_lane_by_rob.exists(rob) ? konata_lane_by_rob[rob] : 0;
    if (konata_stage_by_rob[rob] == 0)
      konata_stage_end(id, lane, "S2");
    else
      konata_stage_end(id, lane, "ROB");
    konata_label(id, 1, $sformatf("commit pc=0x%016x op=%0d slot=%0d", pc, op, slot));
    konata_retire(id, id, 0);
    konata_id_by_rob.delete(rob);
    konata_stage_by_rob.delete(rob);
    konata_lane_by_rob.delete(rob);
  endtask

  initial begin
    clk = 1'b0;
    rst = 1'b1;

    boot_pc = 64'h0000_0000_0001_0000;
    boot_sp = 64'h0000_0000_0002_0000;
    max_cycles = 400000;

    if (!$value$plusargs("memh=%s", memh_path)) begin
      memh_path = "janus/programs/test_or.memh";
    end

    void'($value$plusargs("boot_pc=%h", boot_pc));
    void'($value$plusargs("boot_sp=%h", boot_sp));
    void'($value$plusargs("max_cycles=%d", max_cycles));

    has_expected_mem100 = $value$plusargs("expected_mem100=%h", expected_mem100);
    has_expected_a0 = $value$plusargs("expected_a0=%h", expected_a0);
    has_expected_exit = $value$plusargs("expected_exit=%h", expected_exit);
    has_expected_exit_pc = $value$plusargs("expected_exit_pc=%h", expected_exit_pc);
    exit_pc_stable_cycles = 8;
    void'($value$plusargs("exit_pc_stable=%d", exit_pc_stable_cycles));
    exit_pc_stable = 0;
    done = 0;
    got_exit_code = 32'd0;

    vcd_path = "janus/generated/janus_bcc_ooo_pyc/tb_janus_bcc_ooo_pyc_sv.vcd";
    log_path = "janus/generated/janus_bcc_ooo_pyc/tb_janus_bcc_ooo_pyc_sv.log";
    konata_path = "janus/generated/janus_bcc_ooo_pyc/tb_janus_bcc_ooo_pyc_sv.konata";
    void'($value$plusargs("vcd=%s", vcd_path));
    void'($value$plusargs("log=%s", log_path));
    void'($value$plusargs("konata=%s", konata_path));
    log_commits = $test$plusargs("logcommits");
    konata_on = !$test$plusargs("nokonata");
    konata_fd = 0;
    konata_next_id = 1;
    if (konata_on) begin
      konata_fd = $fopen(konata_path, "w");
      if (konata_fd == 0) begin
        $display("WARN: failed to open Konata log: %s", konata_path);
        konata_on = 0;
      end
    end

    if (!$test$plusargs("notrace")) begin
      $display("tb_janus_bcc_ooo_pyc: dumping VCD to %s", vcd_path);
      $dumpfile(vcd_path);
      $dumpvars(0, tb_janus_bcc_ooo_pyc);
    end

    if (!$test$plusargs("nolog")) begin
      log_fd = $fopen(log_path, "w");
      $fdisplay(log_fd, "tb_janus_bcc_ooo_pyc(SV): memh=%s", memh_path);
      $fdisplay(log_fd, "cycle,time,halted,slot,commit_pc,pc,fpc,cycles,rob_count,commit_op,commit_value,a0,a1,ra,sp,br_kind,commit_cond,commit_tgt,br_base_pc,br_off,st_fire,st_addr,st_data,st_size");
    end else begin
      log_fd = 0;
    end

    $display("tb_janus_bcc_ooo_pyc: memh=%s boot_pc=0x%016x max_cycles=%0d", memh_path, boot_pc, max_cycles);

    if ($test$plusargs("zeromem")) begin
      // Deterministic BSS/heap bring-up: clear backing RAM before loading memh.
      for (int unsigned k = 0; k < 1048576; k++) begin
        dut.mem.mem[k] = 8'h00;
      end
    end

    $readmemh(memh_path, dut.mem.mem);

    repeat (5) @(posedge clk);
    rst = 1'b0;

    if (konata_on) begin
      $fdisplay(konata_fd, "Kanata\t0004");
      $fdisplay(konata_fd, "C=\t%0d", cycles);
      konata_cur_cycle = cycles;
    end

    i = 0;
    while (i < max_cycles && !halted && !done) begin
      @(posedge clk);
      if (konata_on) begin
        konata_at_cycle(cycles);
        if (dispatch_fire0) pv_dispatch(0, dispatch_rob0, dispatch_pc0, dispatch_op0);
        if (dispatch_fire1) pv_dispatch(1, dispatch_rob1, dispatch_pc1, dispatch_op1);
        if (dispatch_fire2) pv_dispatch(2, dispatch_rob2, dispatch_pc2, dispatch_op2);
        if (dispatch_fire3) pv_dispatch(3, dispatch_rob3, dispatch_pc3, dispatch_op3);
        if (issue_fire0) pv_issue(0, issue_rob0, issue_pc0, issue_op0);
        if (issue_fire1) pv_issue(1, issue_rob1, issue_pc1, issue_op1);
        if (issue_fire2) pv_issue(2, issue_rob2, issue_pc2, issue_op2);
        if (issue_fire3) pv_issue(3, issue_rob3, issue_pc3, issue_op3);
        if (commit_fire0) pv_commit(0, commit_rob0, commit_pc0, commit_op0);
        if (commit_fire1) pv_commit(1, commit_rob1, commit_pc1, commit_op1);
        if (commit_fire2) pv_commit(2, commit_rob2, commit_pc2, commit_op2);
        if (commit_fire3) pv_commit(3, commit_rob3, commit_pc3, commit_op3);
      end
      if (mmio_uart_valid) begin
        $write("%c", mmio_uart_data);
      end
      if (mmio_exit_valid) begin
        got_exit_code = mmio_exit_code;
        done = 1;
      end
      if (log_fd != 0 && log_commits) begin
        if (commit_fire0) begin
          $fdisplay(log_fd,
                    "%0d,%0t,%0b,%0d,0x%016x,0x%016x,0x%016x,%0d,%0d,0x%03x,0x%016x,0x%016x,0x%016x,0x%016x,0x%016x,%0d,%0b,0x%016x,0x%016x,0x%016x,%0b,0x%016x,0x%016x,0x%01x",
                    i,
                    $time,
                    halted,
                    0,
                    commit_pc0,
                    pc,
                    fpc,
                    cycles,
                    rob_count,
                    commit_op0,
                    commit_value0,
                    a0,
                    a1,
                    ra,
                    sp,
                    br_kind,
                    commit_cond,
                    commit_tgt,
                    br_base_pc,
                    br_off,
                    commit_store_fire,
                    commit_store_addr,
                    commit_store_data,
                    commit_store_size);
        end
        if (commit_fire1) begin
          $fdisplay(log_fd,
                    "%0d,%0t,%0b,%0d,0x%016x,0x%016x,0x%016x,%0d,%0d,0x%03x,0x%016x,0x%016x,0x%016x,0x%016x,0x%016x,%0d,%0b,0x%016x,0x%016x,0x%016x,%0b,0x%016x,0x%016x,0x%01x",
                    i,
                    $time,
                    halted,
                    1,
                    commit_pc1,
                    pc,
                    fpc,
                    cycles,
                    rob_count,
                    commit_op1,
                    commit_value1,
                    a0,
                    a1,
                    ra,
                    sp,
                    br_kind,
                    commit_cond,
                    commit_tgt,
                    br_base_pc,
                    br_off,
                    commit_store_fire,
                    commit_store_addr,
                    commit_store_data,
                    commit_store_size);
        end
        if (commit_fire2) begin
          $fdisplay(log_fd,
                    "%0d,%0t,%0b,%0d,0x%016x,0x%016x,0x%016x,%0d,%0d,0x%03x,0x%016x,0x%016x,0x%016x,0x%016x,0x%016x,%0d,%0b,0x%016x,0x%016x,0x%016x,%0b,0x%016x,0x%016x,0x%01x",
                    i,
                    $time,
                    halted,
                    2,
                    commit_pc2,
                    pc,
                    fpc,
                    cycles,
                    rob_count,
                    commit_op2,
                    commit_value2,
                    a0,
                    a1,
                    ra,
                    sp,
                    br_kind,
                    commit_cond,
                    commit_tgt,
                    br_base_pc,
                    br_off,
                    commit_store_fire,
                    commit_store_addr,
                    commit_store_data,
                    commit_store_size);
        end
        if (commit_fire3) begin
          $fdisplay(log_fd,
                    "%0d,%0t,%0b,%0d,0x%016x,0x%016x,0x%016x,%0d,%0d,0x%03x,0x%016x,0x%016x,0x%016x,0x%016x,0x%016x,%0d,%0b,0x%016x,0x%016x,0x%016x,%0b,0x%016x,0x%016x,0x%01x",
                    i,
                    $time,
                    halted,
                    3,
                    commit_pc3,
                    pc,
                    fpc,
                    cycles,
                    rob_count,
                    commit_op3,
                    commit_value3,
                    a0,
                    a1,
                    ra,
                    sp,
                    br_kind,
                    commit_cond,
                    commit_tgt,
                    br_base_pc,
                    br_off,
                    commit_store_fire,
                    commit_store_addr,
                    commit_store_data,
                    commit_store_size);
        end
      end
      if (has_expected_exit_pc && pc === expected_exit_pc[63:0] && fpc === expected_exit_pc[63:0]) begin
        exit_pc_stable++;
        if (exit_pc_stable >= exit_pc_stable_cycles) begin
          done = 1;
        end
      end else begin
        exit_pc_stable = 0;
      end
      i++;
    end

    if (!halted && !done) begin
      $fatal(
          1,
          $sformatf(
              "FAIL: did not halt (pc=0x%016x fpc=0x%016x cycles=%0d rob_count=%0d halted=%0b done=%0b)\n  commit_fire=%0b commit_op=0x%03x commit_value=0x%016x commit_dst_kind=%0d commit_dst_areg=%0d commit_pdst=%0d\n  commit_store_fire=%0b commit_store_addr=0x%016x commit_store_data=0x%016x commit_store_size=0x%x\n  dispatch_fire=%0b dec_op=0x%03x\n  issue_fire=%0b issue_op=0x%03x issue_pc=0x%016x issue_rob=%0d issue_is_load=%0b issue_is_store=%0b store_pending=%0b store_pending_older=%0b mem_raddr=0x%016x\n  a0=0x%016x a1=0x%016x ra=0x%016x sp=0x%016x\n  mmio_exit_valid=%0b mmio_exit_code=0x%08x",
              pc,
              fpc,
              cycles,
              rob_count,
              halted,
              done,
              commit_fire,
              commit_op,
              commit_value,
              commit_dst_kind,
              commit_dst_areg,
              commit_pdst,
              commit_store_fire,
              commit_store_addr,
              commit_store_data,
              commit_store_size,
              dispatch_fire,
              dec_op,
              issue_fire,
              issue_op,
              issue_pc,
              issue_rob,
              issue_is_load,
              issue_is_store,
              store_pending,
              store_pending_older,
              mem_raddr,
              a0,
              a1,
              ra,
              sp,
              mmio_exit_valid,
              mmio_exit_code
          )
      );
    end

    got_mem100 = mem_read32(32'h0000_0100);

    if (has_expected_exit && got_exit_code !== expected_exit[31:0]) begin
      $fatal(1, "FAIL: exit_code=0x%08x expected_exit=0x%08x", got_exit_code, expected_exit[31:0]);
    end

    if (has_expected_mem100 && got_mem100 !== expected_mem100[31:0]) begin
      $fatal(1, "FAIL: mem[0x100]=0x%08x expected=0x%08x", got_mem100, expected_mem100[31:0]);
    end

    if (has_expected_a0 && a0 !== expected_a0[63:0]) begin
      $fatal(1, "FAIL: a0=0x%016x expected=0x%016x", a0, expected_a0[63:0]);
    end

    if (log_fd != 0) begin
      $fdisplay(log_fd, "PASS: cycles=%0d pc=0x%016x fpc=0x%016x a0=0x%016x mem100=0x%08x", cycles, pc, fpc, a0, got_mem100);
      $fclose(log_fd);
    end

    if (konata_fd != 0) begin
      if (konata_on) begin
        // Ensure the Konata log is structurally balanced for viewers that
        // require every stage-start to have a matching end before EOF.
        foreach (konata_id_by_rob[rob]) begin
          longint unsigned id;
          int lane;
          id = konata_id_by_rob[rob];
          lane = konata_lane_by_rob.exists(rob) ? konata_lane_by_rob[rob] : 0;
          if (konata_stage_by_rob[rob] == 0)
            konata_stage_end(id, lane, "S2");
          else
            konata_stage_end(id, lane, "ROB");
          konata_label(id, 1, "end_of_sim");
          konata_retire(id, id, 1);
        end
        konata_id_by_rob.delete();
        konata_stage_by_rob.delete();
        konata_lane_by_rob.delete();
      end
      $fclose(konata_fd);
    end

    $display("PASS: cycles=%0d pc=0x%016x a0=0x%016x mem[0x100]=0x%08x", cycles, pc, a0, got_mem100);
    $finish;
  end

endmodule
