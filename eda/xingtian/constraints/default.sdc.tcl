# Generic XingTian 2 ns bring-up constraint for designs with a `clk` port.
# Override this file for production QoR or designs with different clocking.
set period 2.0

set clk_port [get_obj_ports clk]
if {[llength $clk_port] == 0} {
  error "Cannot find port clk"
}
create_clock -period $period -name clk_main $clk_port

set all_in [all_obj_inputs]
set data_in {}
foreach p $all_in {
  set pn [get_obj_name $p]
  if {[string match "*clk*" [string tolower $pn]]} {
    continue
  }
  if {[string match "*rst*" [string tolower $pn]]} {
    continue
  }
  lappend data_in $p
}
if {[llength $data_in] > 0} {
  set_input_delay -max [expr {$period * 0.3}] -clock clk_main $data_in
}

set all_out [all_obj_outputs]
if {[llength $all_out] > 0} {
  set_output_delay -max [expr {$period * 0.3}] -clock clk_main $all_out
}
