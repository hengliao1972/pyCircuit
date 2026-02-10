// Zybo Z7-20 PS/PL platform wrapper for the Janus BCC OOO Linx core.
//
// Same AXI-Lite register map as the in-order wrapper, with UART/EXIT sourced
// from the core's exported MMIO signals.
module linx_platform_ooo_axi (
  parameter int unsigned MEM_BYTES = 262144,
  (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI,ASSOCIATED_RESET aresetn" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
  input  logic        aclk,
  (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
  input  logic        aresetn,

  // AXI4-Lite slave
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
  input  logic [31:0] s_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
  input  logic        s_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
  output logic        s_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
  input  logic [31:0] s_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
  input  logic [3:0]  s_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
  input  logic        s_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
  output logic        s_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
  output logic [1:0]  s_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
  output logic        s_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
  input  logic        s_axi_bready,

  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
  input  logic [31:0] s_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
  input  logic        s_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
  output logic        s_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
  output logic [31:0] s_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
  output logic [1:0]  s_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
  output logic        s_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
  input  logic        s_axi_rready,

  // Debug LEDs (PL)
  (* X_INTERFACE_INFO = "xilinx.com:interface:gpio:1.0 led TRI_O" *)
  output logic [3:0]  led
);

  logic        core_reset;
  logic [63:0] boot_pc, boot_sp;

  logic        host_wvalid;
  logic [63:0] host_waddr;
  logic [63:0] host_wdata;
  logic [7:0]  host_wstrb;

  logic        core_halted;
  logic [63:0] core_cycles;

  logic        mmio_uart_valid;
  logic [7:0]  mmio_uart_data;
  logic        mmio_exit_valid;
  logic [31:0] mmio_exit_code;

  logic [31:0] exit_code_latched;
  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      exit_code_latched <= 32'd0;
    end else if (core_reset) begin
      exit_code_latched <= 32'd0;
    end else if (mmio_exit_valid) begin
      exit_code_latched <= mmio_exit_code;
    end
  end

  linx_platform_regs_axi u_regs (
    .aclk(aclk),
    .aresetn(aresetn),

    .s_axi_awaddr(s_axi_awaddr),
    .s_axi_awvalid(s_axi_awvalid),
    .s_axi_awready(s_axi_awready),
    .s_axi_wdata(s_axi_wdata),
    .s_axi_wstrb(s_axi_wstrb),
    .s_axi_wvalid(s_axi_wvalid),
    .s_axi_wready(s_axi_wready),
    .s_axi_bresp(s_axi_bresp),
    .s_axi_bvalid(s_axi_bvalid),
    .s_axi_bready(s_axi_bready),

    .s_axi_araddr(s_axi_araddr),
    .s_axi_arvalid(s_axi_arvalid),
    .s_axi_arready(s_axi_arready),
    .s_axi_rdata(s_axi_rdata),
    .s_axi_rresp(s_axi_rresp),
    .s_axi_rvalid(s_axi_rvalid),
    .s_axi_rready(s_axi_rready),

    .core_reset(core_reset),
    .boot_pc(boot_pc),
    .boot_sp(boot_sp),

    .host_wvalid(host_wvalid),
    .host_waddr(host_waddr),
    .host_wdata(host_wdata),
    .host_wstrb(host_wstrb),

    .core_halted(core_halted),
    .core_exit_code(exit_code_latched),
    .core_cycles(core_cycles),

    .core_uart_valid(mmio_uart_valid),
    .core_uart_byte(mmio_uart_data)
  );

  logic core_rst;
  assign core_rst = (~aresetn) | core_reset;

  janus_bcc_ooo_pyc u_core (
    .clk(aclk),
    .rst(core_rst),
    .boot_pc(boot_pc),
    .boot_sp(boot_sp),
    .host_wvalid(host_wvalid),
    .host_waddr(host_waddr),
    .host_wdata(host_wdata),
    .host_wstrb(host_wstrb),
    .halted(core_halted),
    .cycles(core_cycles),
    .pc(),
    .fpc(),
    .a0(),
    .a1(),
    .ra(),
    .sp(),
    .commit_op(),
    .commit_fire(),
    .commit_value(),
    .commit_dst_kind(),
    .commit_dst_areg(),
    .commit_pdst(),
    .commit_cond(),
    .commit_tgt(),
    .br_kind(),
    .br_base_pc(),
    .br_off(),
    .commit_store_fire(),
    .commit_store_addr(),
    .commit_store_data(),
    .commit_store_size(),
    .commit_fire0(),
    .commit_pc0(),
    .commit_op0(),
    .commit_value0(),
    .commit_fire1(),
    .commit_pc1(),
    .commit_op1(),
    .commit_value1(),
    .commit_fire2(),
    .commit_pc2(),
    .commit_op2(),
    .commit_value2(),
    .commit_fire3(),
    .commit_pc3(),
    .commit_op3(),
    .commit_value3(),
    .rob_count(),
    .ct0(),
    .cu0(),
    .st0(),
    .su0(),
    .issue_fire(),
    .issue_op(),
    .issue_pc(),
    .issue_rob(),
    .issue_sl(),
    .issue_sr(),
    .issue_sp(),
    .issue_pdst(),
    .issue_sl_val(),
    .issue_sr_val(),
    .issue_sp_val(),
    .issue_is_load(),
    .issue_is_store(),
    .store_pending(),
    .store_pending_older(),
    .mem_raddr(),
    .dispatch_fire(),
    .dec_op(),
    .mmio_uart_valid(mmio_uart_valid),
    .mmio_uart_data(mmio_uart_data),
    .mmio_exit_valid(mmio_exit_valid),
    .mmio_exit_code(mmio_exit_code),
    .ooo_4wide(),
    .block_cmd_valid(),
    .block_cmd_kind(),
    .block_cmd_payload(),
    .block_cmd_tile(),
    .block_cmd_tag()
  );

  // FPGA-friendly memory sizing (default: 256 KiB).
  defparam u_core.mem.DEPTH = MEM_BYTES;

  always_comb begin
    if (core_halted) begin
      led = exit_code_latched[3:0];
    end else begin
      led[0] = ~core_rst;
      led[1] = mmio_uart_valid;
      led[2] = mmio_exit_valid;
      led[3] = core_cycles[24];
    end
  end

endmodule
