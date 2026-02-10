// AXI4-Lite control/status + UART FIFO + host memory loader port for Linx cores.
//
// This block is memory-mapped by the Zybo Z7-20 Zynq PS (M_AXI_GP0).
//
// Features:
// - core reset control + boot_pc/boot_sp registers
// - host write pulse port (boot-time memory loader; use while core held in reset)
// - UART FIFO for core->PS output (no backpressure to the core)
// - exit_code + cycles + halted status
module linx_platform_regs_axi #(
  parameter int unsigned AXI_ADDR_W = 32,
  // Default to a few KiB so CoreMark/Dhrystone banners won't overflow.
  parameter int unsigned UART_FIFO_DEPTH = 4096
) (
  input  logic                  aclk,
  input  logic                  aresetn,

  // AXI4-Lite slave
  input  logic [AXI_ADDR_W-1:0] s_axi_awaddr,
  input  logic                  s_axi_awvalid,
  output logic                  s_axi_awready,
  input  logic [31:0]           s_axi_wdata,
  input  logic [3:0]            s_axi_wstrb,
  input  logic                  s_axi_wvalid,
  output logic                  s_axi_wready,
  output logic [1:0]            s_axi_bresp,
  output logic                  s_axi_bvalid,
  input  logic                  s_axi_bready,

  input  logic [AXI_ADDR_W-1:0] s_axi_araddr,
  input  logic                  s_axi_arvalid,
  output logic                  s_axi_arready,
  output logic [31:0]           s_axi_rdata,
  output logic [1:0]            s_axi_rresp,
  output logic                  s_axi_rvalid,
  input  logic                  s_axi_rready,

  // Control outputs to core
  output logic                  core_reset,   // active-high
  output logic [63:0]           boot_pc,
  output logic [63:0]           boot_sp,

  // Host loader write port (pulse)
  output logic                  host_wvalid,
  output logic [63:0]           host_waddr,
  output logic [63:0]           host_wdata,
  output logic [7:0]            host_wstrb,

  // Status inputs from core
  input  logic                  core_halted,
  input  logic [31:0]           core_exit_code,
  input  logic [63:0]           core_cycles,

  // UART bytes from core (no backpressure)
  input  logic                  core_uart_valid,
  input  logic [7:0]            core_uart_byte
);

  // --- Register map (byte offsets) ---
  localparam logic [7:0] REG_CTRL         = 8'h00; // [0]=reset
  localparam logic [7:0] REG_STATUS       = 8'h04; // [0]=halted
  localparam logic [7:0] REG_BOOT_PC_LO   = 8'h08;
  localparam logic [7:0] REG_BOOT_PC_HI   = 8'h0C;
  localparam logic [7:0] REG_BOOT_SP_LO   = 8'h10;
  localparam logic [7:0] REG_BOOT_SP_HI   = 8'h14;

  localparam logic [7:0] REG_HOST_ADDR_LO = 8'h18;
  localparam logic [7:0] REG_HOST_ADDR_HI = 8'h1C;
  localparam logic [7:0] REG_HOST_DATA_LO = 8'h20;
  localparam logic [7:0] REG_HOST_DATA_HI = 8'h24;
  localparam logic [7:0] REG_HOST_STRB    = 8'h28; // [7:0]
  localparam logic [7:0] REG_HOST_CMD     = 8'h2C; // write 1 => host_wvalid pulse

  localparam logic [7:0] REG_UART_STATUS  = 8'h30; // [15:0]=count [16]=overflow (write 1 clears overflow)
  localparam logic [7:0] REG_UART_DATA    = 8'h34; // read => pop

  localparam logic [7:0] REG_EXIT_CODE    = 8'h38;
  localparam logic [7:0] REG_CYCLES_LO    = 8'h3C;
  localparam logic [7:0] REG_CYCLES_HI    = 8'h40;

  // --- UART FIFO ---
  localparam int unsigned UART_PTR_W = (UART_FIFO_DEPTH <= 2) ? 1 : $clog2(UART_FIFO_DEPTH);
  logic [7:0] uart_mem [0:UART_FIFO_DEPTH-1];
  logic [UART_PTR_W-1:0] uart_wr_ptr, uart_rd_ptr;
  logic [UART_PTR_W:0] uart_count;
  logic uart_overflow;

  wire uart_full  = (uart_count == UART_FIFO_DEPTH[UART_PTR_W:0]);
  wire uart_empty = (uart_count == '0);

  wire uart_push = core_uart_valid & ~uart_full;
  logic uart_pop;
  logic [7:0] uart_pop_data;

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      uart_wr_ptr <= '0;
      uart_rd_ptr <= '0;
      uart_count <= '0;
      uart_overflow <= 1'b0;
    end else begin
      if (core_uart_valid & uart_full)
        uart_overflow <= 1'b1;

      if (uart_push) begin
        uart_mem[uart_wr_ptr] <= core_uart_byte;
        uart_wr_ptr <= uart_wr_ptr + 1'b1;
      end

      if (uart_pop) begin
        uart_rd_ptr <= uart_rd_ptr + 1'b1;
      end

      unique case ({uart_push, uart_pop})
        2'b10: uart_count <= uart_count + 1'b1;
        2'b01: uart_count <= uart_count - 1'b1;
        default: uart_count <= uart_count;
      endcase
    end
  end

  assign uart_pop_data = uart_mem[uart_rd_ptr];

  // --- AXI-Lite (single outstanding write + read) ---
  logic [AXI_ADDR_W-1:0] awaddr_r, araddr_r;
  logic aw_captured, w_captured, ar_captured;
  logic [31:0] wdata_r;
  logic [3:0]  wstrb_r;

  // Write address/data capture
  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      aw_captured <= 1'b0;
      w_captured <= 1'b0;
      awaddr_r <= '0;
      wdata_r <= '0;
      wstrb_r <= '0;
    end else begin
      if (s_axi_awready && s_axi_awvalid) begin
        aw_captured <= 1'b1;
        awaddr_r <= s_axi_awaddr;
      end
      if (s_axi_wready && s_axi_wvalid) begin
        w_captured <= 1'b1;
        wdata_r <= s_axi_wdata;
        wstrb_r <= s_axi_wstrb;
      end

      if (s_axi_bvalid && s_axi_bready) begin
        aw_captured <= 1'b0;
        w_captured <= 1'b0;
      end
    end
  end

  assign s_axi_awready = aresetn && ~aw_captured && ~s_axi_bvalid;
  assign s_axi_wready  = aresetn && ~w_captured  && ~s_axi_bvalid;

  wire do_write = aw_captured && w_captured && ~s_axi_bvalid;

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      s_axi_bvalid <= 1'b0;
    end else begin
      if (do_write) begin
        s_axi_bvalid <= 1'b1;
      end else if (s_axi_bvalid && s_axi_bready) begin
        s_axi_bvalid <= 1'b0;
      end
    end
  end

  assign s_axi_bresp = 2'b00;

  // Read address capture + response
  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      ar_captured <= 1'b0;
      araddr_r <= '0;
    end else begin
      if (s_axi_arready && s_axi_arvalid) begin
        ar_captured <= 1'b1;
        araddr_r <= s_axi_araddr;
      end
      if (s_axi_rvalid && s_axi_rready) begin
        ar_captured <= 1'b0;
      end
    end
  end

  assign s_axi_arready = aresetn && ~ar_captured && ~s_axi_rvalid;

  // --- Control registers ---
  logic [31:0] ctrl_r;
  logic [63:0] host_addr_r, host_data_r;
  logic [7:0]  host_strb_r;

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      ctrl_r <= 32'h0000_0001; // reset asserted by default
      boot_pc <= 64'h0000_0000_0001_0000;
      // Default SP for a small BRAM-backed memory window (256 KiB => top ~0x3ff00).
      boot_sp <= 64'h0000_0000_0003_ff00;
      host_addr_r <= 64'd0;
      host_data_r <= 64'd0;
      host_strb_r <= 8'd0;
      host_wvalid <= 1'b0;
    end else begin
      host_wvalid <= 1'b0;
      if (do_write) begin
        unique case (awaddr_r[7:0])
          REG_CTRL: begin
            if (wstrb_r[0]) ctrl_r[7:0] <= wdata_r[7:0];
          end
          REG_BOOT_PC_LO: begin
            if (wstrb_r[0]) boot_pc[7:0]   <= wdata_r[7:0];
            if (wstrb_r[1]) boot_pc[15:8]  <= wdata_r[15:8];
            if (wstrb_r[2]) boot_pc[23:16] <= wdata_r[23:16];
            if (wstrb_r[3]) boot_pc[31:24] <= wdata_r[31:24];
          end
          REG_BOOT_PC_HI: begin
            if (wstrb_r[0]) boot_pc[39:32] <= wdata_r[7:0];
            if (wstrb_r[1]) boot_pc[47:40] <= wdata_r[15:8];
            if (wstrb_r[2]) boot_pc[55:48] <= wdata_r[23:16];
            if (wstrb_r[3]) boot_pc[63:56] <= wdata_r[31:24];
          end
          REG_BOOT_SP_LO: begin
            if (wstrb_r[0]) boot_sp[7:0]   <= wdata_r[7:0];
            if (wstrb_r[1]) boot_sp[15:8]  <= wdata_r[15:8];
            if (wstrb_r[2]) boot_sp[23:16] <= wdata_r[23:16];
            if (wstrb_r[3]) boot_sp[31:24] <= wdata_r[31:24];
          end
          REG_BOOT_SP_HI: begin
            if (wstrb_r[0]) boot_sp[39:32] <= wdata_r[7:0];
            if (wstrb_r[1]) boot_sp[47:40] <= wdata_r[15:8];
            if (wstrb_r[2]) boot_sp[55:48] <= wdata_r[23:16];
            if (wstrb_r[3]) boot_sp[63:56] <= wdata_r[31:24];
          end
          REG_HOST_ADDR_LO: begin
            if (wstrb_r[0]) host_addr_r[7:0]   <= wdata_r[7:0];
            if (wstrb_r[1]) host_addr_r[15:8]  <= wdata_r[15:8];
            if (wstrb_r[2]) host_addr_r[23:16] <= wdata_r[23:16];
            if (wstrb_r[3]) host_addr_r[31:24] <= wdata_r[31:24];
          end
          REG_HOST_ADDR_HI: begin
            if (wstrb_r[0]) host_addr_r[39:32] <= wdata_r[7:0];
            if (wstrb_r[1]) host_addr_r[47:40] <= wdata_r[15:8];
            if (wstrb_r[2]) host_addr_r[55:48] <= wdata_r[23:16];
            if (wstrb_r[3]) host_addr_r[63:56] <= wdata_r[31:24];
          end
          REG_HOST_DATA_LO: begin
            if (wstrb_r[0]) host_data_r[7:0]   <= wdata_r[7:0];
            if (wstrb_r[1]) host_data_r[15:8]  <= wdata_r[15:8];
            if (wstrb_r[2]) host_data_r[23:16] <= wdata_r[23:16];
            if (wstrb_r[3]) host_data_r[31:24] <= wdata_r[31:24];
          end
          REG_HOST_DATA_HI: begin
            if (wstrb_r[0]) host_data_r[39:32] <= wdata_r[7:0];
            if (wstrb_r[1]) host_data_r[47:40] <= wdata_r[15:8];
            if (wstrb_r[2]) host_data_r[55:48] <= wdata_r[23:16];
            if (wstrb_r[3]) host_data_r[63:56] <= wdata_r[31:24];
          end
          REG_HOST_STRB: begin
            if (wstrb_r[0]) host_strb_r <= wdata_r[7:0];
          end
          REG_HOST_CMD: begin
            if (wstrb_r[0] && wdata_r[0]) begin
              host_wvalid <= 1'b1;
            end
          end
          REG_UART_STATUS: begin
            // Write 1 to clear overflow.
            if (wstrb_r[0] && wdata_r[0]) uart_overflow <= 1'b0;
          end
          default: begin
          end
        endcase
      end
    end
  end

  assign core_reset = ctrl_r[0];

  assign host_waddr = host_addr_r;
  assign host_wdata = host_data_r;
  assign host_wstrb = host_strb_r;

  // --- Read responses ---
  logic [AXI_ADDR_W-1:0] araddr_sel;
  logic [31:0] rdata_next;
  always_comb begin
    araddr_sel = (s_axi_arready && s_axi_arvalid) ? s_axi_araddr : araddr_r;
    rdata_next = 32'd0;
    unique case (araddr_sel[7:0])
      REG_CTRL:         rdata_next = ctrl_r;
      REG_STATUS:       rdata_next = {31'd0, core_halted};
      REG_BOOT_PC_LO:   rdata_next = boot_pc[31:0];
      REG_BOOT_PC_HI:   rdata_next = boot_pc[63:32];
      REG_BOOT_SP_LO:   rdata_next = boot_sp[31:0];
      REG_BOOT_SP_HI:   rdata_next = boot_sp[63:32];
      REG_HOST_ADDR_LO: rdata_next = host_addr_r[31:0];
      REG_HOST_ADDR_HI: rdata_next = host_addr_r[63:32];
      REG_HOST_DATA_LO: rdata_next = host_data_r[31:0];
      REG_HOST_DATA_HI: rdata_next = host_data_r[63:32];
      REG_HOST_STRB:    rdata_next = {24'd0, host_strb_r};
      REG_UART_STATUS:  rdata_next = {15'd0, uart_overflow, uart_count[15:0]};
      REG_UART_DATA:    rdata_next = {24'd0, uart_pop_data};
      REG_EXIT_CODE:    rdata_next = core_exit_code;
      REG_CYCLES_LO:    rdata_next = core_cycles[31:0];
      REG_CYCLES_HI:    rdata_next = core_cycles[63:32];
      default:          rdata_next = 32'd0;
    endcase
  end

  // Pop UART on a successful read of REG_UART_DATA.
  always_comb begin
    uart_pop = 1'b0;
    if (s_axi_arready && s_axi_arvalid) begin
      if (s_axi_araddr[7:0] == REG_UART_DATA)
        uart_pop = ~uart_empty;
    end
  end

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      s_axi_rvalid <= 1'b0;
      s_axi_rdata <= 32'd0;
    end else begin
      if (s_axi_arready && s_axi_arvalid) begin
        s_axi_rvalid <= 1'b1;
        s_axi_rdata <= rdata_next;
      end else if (s_axi_rvalid && s_axi_rready) begin
        s_axi_rvalid <= 1'b0;
      end
    end
  end

  assign s_axi_rresp = 2'b00;

endmodule
