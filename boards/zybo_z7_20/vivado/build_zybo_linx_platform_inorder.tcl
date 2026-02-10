# Build a Zybo Z7-20 bitstream for a Zynq PS/PL Linx bring-up platform
# (in-order core: linx_cpu_pyc).
#
# Usage:
#   vivado -mode batch -source boards/zybo_z7_20/vivado/build_zybo_linx_platform_inorder.tcl
#
# Optional env vars:
#   PYC_PROGRAM=1     (program after build)
#   PYC_NO_RUNS=1     (skip synth/impl; validate sources only)

set script_dir [file dirname [file normalize [info script]]]
set repo_root  [file normalize [file join $script_dir .. .. ..]]

set build_dir  [file normalize [file join $script_dir build_linx_platform_inorder]]
file mkdir $build_dir

set proj_name "zybo_linx_platform_inorder"

create_project -force $proj_name $build_dir -part xc7z020clg400-1

set board_parts [get_board_parts -quiet *zybo-z7-20*]
if {[llength $board_parts] > 0} {
  set_property board_part [lindex $board_parts 0] [current_project]
}

# Sources: platform regs + wrapper + generated core.
add_files -norecurse [file join $repo_root boards zybo_z7_20 rtl linx_platform_regs_axi.sv]
add_files -norecurse [file join $repo_root boards zybo_z7_20 rtl linx_platform_inorder_axi.sv]
add_files -norecurse [file join $repo_root examples generated linx_cpu_pyc linx_cpu_pyc.v]

add_files -fileset constrs_1 -norecurse [file join $repo_root boards zybo_z7_20 constraints zybo_z7_20_leds_only.xdc]

# Generated netlists use `include "pyc_*.v"`; point the Verilog preprocessor at the primitives.
set_property include_dirs [list [file join $repo_root include pyc verilog]] [current_fileset]

# Create a block design: PS7 + AXI GP0 + platform wrapper.
create_bd_design "linx_platform"

set ps7 [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]

# Ensure required PS interfaces/clocks are enabled even without board presets.
set_property -dict [list \
  CONFIG.PCW_USE_M_AXI_GP0 {1} \
  CONFIG.PCW_EN_CLK0_PORT {1} \
  CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100.0} \
] $ps7

# Board automation (DDR + fixed IO). If board files are missing, user can open the
# project and configure PS manually.
catch {
  apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR" apply_board_preset "1"} $ps7
}

set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_ps7_0/slowest_sync_clk]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0/ext_reset_in]

set plat [create_bd_cell -type module -reference linx_platform_inorder_axi linx_plat_0]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins linx_plat_0/aclk]
connect_bd_net [get_bd_pins rst_ps7_0/peripheral_aresetn] [get_bd_pins linx_plat_0/aresetn]

# Make LEDs external (PL pins).
set led_port [make_bd_pins_external [get_bd_pins linx_plat_0/led]]
set_property name led $led_port

# Connect AXI GP0 -> platform regs.
catch {
  apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master "/processing_system7_0/M_AXI_GP0" Slave "/linx_plat_0/S_AXI" Clk "/processing_system7_0/FCLK_CLK0"} [get_bd_intf_pins linx_plat_0/S_AXI]
}

assign_bd_address
set segs [get_bd_addr_segs -quiet -filter {NAME =~ "*linx_plat_0*"}]
if {[llength $segs] > 0} {
  set_property offset 0x43C00000 [lindex $segs 0]
  set_property range  0x00010000 [lindex $segs 0]
}

validate_bd_design
save_bd_design

# Create HDL wrapper and use it as top.
set bd_file [get_files -quiet *linx_platform.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $build_dir ${proj_name}.srcs sources_1 bd linx_platform hdl linx_platform_wrapper.v]

set_property top linx_platform_wrapper [current_fileset]
update_compile_order -fileset sources_1

if {[info exists ::env(PYC_NO_RUNS)] && $::env(PYC_NO_RUNS) == "1"} {
  puts "PYC_NO_RUNS=1: skipping synth/impl; project created at: $build_dir"
  exit
}

launch_runs synth_1 -jobs 8
wait_on_run synth_1

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1

set bit_path [file normalize [file join $build_dir ${proj_name}.runs impl_1 linx_platform_wrapper.bit]]
if {[file exists $bit_path]} {
  file copy -force $bit_path [file join $build_dir linx_platform_inorder.bit]
  puts "Wrote: [file join $build_dir linx_platform_inorder.bit]"
} else {
  puts "Expected bitstream not found: $bit_path"
}

# Export an XSA (for PS app builds in Vitis/XSCT).
set xsa_path [file normalize [file join $build_dir linx_platform_inorder.xsa]]
catch {
  write_hw_platform -fixed -include_bit -force $xsa_path
  puts "Wrote: $xsa_path"
}

# Optional: program the FPGA if the cable is present.
if {[info exists ::env(PYC_PROGRAM)] && $::env(PYC_PROGRAM) == "1"} {
  if {![file exists $bit_path]} {
    puts "Bitstream missing; skipping program: $bit_path"
    exit
  }
  open_hw_manager
  connect_hw_server -allow_non_jtag
  open_hw_target

  set devs [get_hw_devices -quiet]
  if {[llength $devs] == 0} {
    puts "No hardware devices detected; skipping program."
  } else {
    set candidates [get_hw_devices -quiet -filter {PART =~ "xc7z020*"}]
    if {[llength $candidates] == 0} {
      set candidates [get_hw_devices -quiet -filter {NAME =~ "*xc7z020*"}]
    }
    if {[llength $candidates] == 0} {
      set candidates {}
      foreach d $devs {
        set n [get_property NAME $d]
        if {[string match "arm_dap*" $n]} {
          continue
        }
        lappend candidates $d
      }
    }

    if {[llength $candidates] == 0} {
      puts "No programmable FPGA device found in chain: $devs"
    } else {
      set hw_dev [lindex $candidates 0]
      current_hw_device $hw_dev
      refresh_hw_device $hw_dev
      set_property PROGRAM.FILE $bit_path $hw_dev
      program_hw_devices $hw_dev
      puts "Programmed: $hw_dev ([get_property NAME $hw_dev])"
    }
  }
}

exit

