// Zybo Z7-20 PS/PL platform wrapper for the in-order Linx bring-up core.
//
// - Zynq PS provides `aclk`/`aresetn` and drives AXI4-Lite.
// - PL runs `linx_cpu_pyc` and exposes UART/exit/cycle status over AXI-Lite.
// - PS monitor loads a program image via the host_w* port (while core held in reset).
module linx_platform_inorder_axi (
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
  logic [31:0] core_exit_code;
  logic [63:0] core_cycles;

  logic        uart_valid;
  logic [7:0]  uart_byte;

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
    .core_exit_code(core_exit_code),
    .core_cycles(core_cycles),

    .core_uart_valid(uart_valid),
    .core_uart_byte(uart_byte)
  );

  logic core_rst;
  assign core_rst = (~aresetn) | core_reset;

  linx_cpu_pyc u_core (
    .clk(aclk),
    .rst(core_rst),
    .boot_pc(boot_pc),
    .boot_sp(boot_sp),
    .irq(1'b0),
    .irq_vector(64'd0),
    .host_wvalid(host_wvalid),
    .host_waddr(host_waddr),
    .host_wdata(host_wdata),
    .host_wstrb(host_wstrb),
    .halted(core_halted),
    .exit_code(core_exit_code),
    .uart_valid(uart_valid),
    .uart_byte(uart_byte),
    .pc(),
    .stage(),
    .cycles(core_cycles),
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

  // FPGA-friendly memory sizing (default: 256 KiB each, mirrored writes).
  defparam u_core.imem.DEPTH = MEM_BYTES;
  defparam u_core.dmem.DEPTH = MEM_BYTES;

  always_comb begin
    if (core_halted) begin
      led = core_exit_code[3:0];
    end else begin
      led[0] = ~core_rst;
      led[1] = uart_valid;
      led[2] = host_wvalid;
      led[3] = core_cycles[24];
    end
  end

endmodule
