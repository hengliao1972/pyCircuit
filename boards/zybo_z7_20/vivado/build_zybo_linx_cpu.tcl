# Build a Zybo Z7-20 bitstream for the pyCircuit-generated LinxISA bring-up CPU.
#
# Usage:
#   vivado -mode batch -source boards/zybo_z7_20/vivado/build_zybo_linx_cpu.tcl
#
# Optional:
#   set PYC_PROGRAM=1     (program after build)
#   set PYC_NO_RUNS=1     (skip synth/impl; validate sources/constraints only)

set script_dir [file dirname [file normalize [info script]]]
set repo_root  [file normalize [file join $script_dir .. .. ..]]

set build_dir  [file normalize [file join $script_dir build_linx_cpu]]
file mkdir $build_dir

set proj_name "zybo_linx_cpu"
set top_name  "zybo_linx_cpu_top"

# Always set the part so builds work even without Digilent board files installed.
create_project -force $proj_name $build_dir -part xc7z020clg400-1

# If Digilent board files are installed, attach the board part for nicer UX.
set board_parts [get_board_parts -quiet *zybo-z7-20*]
if {[llength $board_parts] > 0} {
  set_property board_part [lindex $board_parts 0] [current_project]
}

add_files -norecurse [file join $repo_root boards zybo_z7_20 rtl uart_tx_8n1.sv]
add_files -norecurse [file join $repo_root boards zybo_z7_20 rtl zybo_linx_cpu_top.sv]
add_files -norecurse [file join $repo_root examples generated linx_cpu_pyc linx_cpu_pyc.v]
add_files -fileset constrs_1 -norecurse [file join $repo_root boards zybo_z7_20 constraints zybo_z7_20_linx_cpu.xdc]

# linx_cpu_pyc.v uses `include "pyc_*.v"`; point the Verilog preprocessor at the primitives.
set_property include_dirs [list [file join $repo_root include pyc verilog]] [current_fileset]

set_property top $top_name [current_fileset]
update_compile_order -fileset sources_1

if {[info exists ::env(PYC_NO_RUNS)] && $::env(PYC_NO_RUNS) == "1"} {
  puts "PYC_NO_RUNS=1: skipping synth/impl; project created at: $build_dir"
  exit
}

launch_runs synth_1 -jobs 8
wait_on_run synth_1

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1

set bit_path [file normalize [file join $build_dir ${proj_name}.runs impl_1 ${top_name}.bit]]
if {[file exists $bit_path]} {
  file copy -force $bit_path [file join $build_dir ${top_name}.bit]
  puts "Wrote: [file join $build_dir ${top_name}.bit]"
} else {
  puts "Expected bitstream not found: $bit_path"
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

