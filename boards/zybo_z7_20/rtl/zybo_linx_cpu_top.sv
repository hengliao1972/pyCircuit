// Zybo Z7-20 bring-up top for the pyCircuit-generated LinxISA CPU (`linx_cpu_pyc`).
//
// IO:
// - clk: Zybo Z7 sysclk (125 MHz)
// - btn[0]: synchronous reset (active-high, 2FF synced)
// - sw[0]: run enable (when 0, core held in reset)
// - led[3:0]:
//     - while running: heartbeat + UART activity/overflow indicators
//     - after halt: exit_code[3:0]
// - uart_tx: 8N1 UART TX (default constraints map to PMOD JE pin 1)
module zybo_linx_cpu_top (
  input  logic       sysclk,
  input  logic [3:0] btn,
  input  logic [3:0] sw,
  output logic [3:0] led,
  output logic       uart_tx
);

  // 2FF synchronize btn[0] into sysclk domain.
  (* ASYNC_REG = "TRUE" *) logic [1:0] btn0_sync = 2'b00;
  always_ff @(posedge sysclk) begin
    btn0_sync <= {btn0_sync[0], btn[0]};
  end
  logic rst_btn;
  assign rst_btn = btn0_sync[1];

  logic core_rst;
  assign core_rst = rst_btn | ~sw[0];

  // Boot defaults aligned to the C++ TB conventions.
  logic [63:0] boot_pc;
  logic [63:0] boot_sp;
  assign boot_pc = 64'h0000_0000_0001_0000;
  // For a 256 KiB memory window, keep SP near the top (0x3ff00).
  assign boot_sp = 64'h0000_0000_0003_ff00;

  logic        halted;
  logic [31:0] exit_code;
  logic        cpu_uart_valid;
  logic [7:0]  cpu_uart_byte;
  logic [63:0] cycles;

  linx_cpu_pyc u_cpu (
    .clk(sysclk),
    .rst(core_rst),
    .boot_pc(boot_pc),
    .boot_sp(boot_sp),
    .irq(1'b0),
    .irq_vector(64'd0),
    .host_wvalid(1'b0),
    .host_waddr(64'd0),
    .host_wdata(64'd0),
    .host_wstrb(8'd0),
    .halted(halted),
    .exit_code(exit_code),
    .uart_valid(cpu_uart_valid),
    .uart_byte(cpu_uart_byte),
    .pc(),
    .stage(),
    .cycles(cycles),
    .a0(),
    .a1(),
    .ra(),
    .sp(),
    .br_kind(),
    .wb0_valid(),
    .wb1_valid(),
    .wb0_pc(),
    .wb1_pc(),
    .wb0_op(),
    .wb1_op(),
    .if_window(),
    .wb_op(),
    .wb_regdst(),
    .wb_value(),
    .commit_cond(),
    .commit_tgt()
  );

  // Shrink the inferred memories for FPGA BRAM-friendly bring-up.
  // (Both ports are mirrored writes in the CPU model; init both from the same image.)
  defparam u_cpu.imem.DEPTH = 262144; // 256 KiB
  defparam u_cpu.dmem.DEPTH = 262144; // 256 KiB
  defparam u_cpu.imem.INIT_MEMH = "boards/zybo_z7_20/programs/test_or.memh";
  defparam u_cpu.dmem.INIT_MEMH = "boards/zybo_z7_20/programs/test_or.memh";

  // --- UART byte FIFO (lossy on overflow; core has no backpressure) ---
  localparam int unsigned FIFO_DEPTH = 16;
  localparam int unsigned FIFO_W = $clog2(FIFO_DEPTH);
  logic [7:0] fifo_mem [0:FIFO_DEPTH-1];
  logic [FIFO_W-1:0] wr_ptr, rd_ptr;
  logic [FIFO_W:0]   count;

  logic push, pop;
  logic [7:0] pop_data;
  logic fifo_full, fifo_empty;
  assign fifo_full  = (count == FIFO_DEPTH[FIFO_W:0]);
  assign fifo_empty = (count == '0);

  assign push = cpu_uart_valid & ~fifo_full;

  logic uart_overflow;
  always_ff @(posedge sysclk) begin
    if (core_rst) begin
      wr_ptr <= '0;
      rd_ptr <= '0;
      count <= '0;
      uart_overflow <= 1'b0;
    end else begin
      if (cpu_uart_valid & fifo_full)
        uart_overflow <= 1'b1;

      if (push) begin
        fifo_mem[wr_ptr] <= cpu_uart_byte;
        wr_ptr <= wr_ptr + 1'b1;
      end
      if (pop) begin
        rd_ptr <= rd_ptr + 1'b1;
      end
      unique case ({push, pop})
        2'b10: count <= count + 1'b1;
        2'b01: count <= count - 1'b1;
        default: count <= count;
      endcase
    end
  end

  // UART transmitter.
  logic tx_ready;
  logic tx_valid;
  logic [7:0] tx_data;
  assign pop = tx_ready & ~fifo_empty;

  always_ff @(posedge sysclk) begin
    if (core_rst) begin
      tx_valid <= 1'b0;
      tx_data <= 8'd0;
    end else begin
      tx_valid <= pop;
      if (pop)
        tx_data <= fifo_mem[rd_ptr];
    end
  end

  uart_tx_8n1 #(
    .CLK_HZ(125_000_000),
    .BAUD(115_200)
  ) u_uart (
    .clk(sysclk),
    .rst(core_rst),
    .valid(tx_valid),
    .data(tx_data),
    .ready(tx_ready),
    .txd(uart_tx)
  );

  // LED indicators.
  logic [23:0] uart_blink;
  logic [23:0] ovf_blink;
  always_ff @(posedge sysclk) begin
    if (core_rst) begin
      uart_blink <= 24'd0;
      ovf_blink <= 24'd0;
    end else begin
      if (cpu_uart_valid)
        uart_blink <= 24'hFF_FFFF;
      else if (uart_blink != 0)
        uart_blink <= uart_blink - 1'b1;

      if (uart_overflow)
        ovf_blink <= 24'hFF_FFFF;
      else if (ovf_blink != 0)
        ovf_blink <= ovf_blink - 1'b1;
    end
  end

  always_comb begin
    if (halted) begin
      led = exit_code[3:0];
    end else begin
      led[0] = sw[0];
      led[1] = |uart_blink;
      led[2] = |ovf_blink;
      led[3] = cycles[24]; // heartbeat
    end
  end

endmodule
