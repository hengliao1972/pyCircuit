## Zybo Z7-20 constraints for boards/zybo_z7_20/rtl/zybo_linx_cpu_top.sv
##
## Notes:
## - Clock/LED/switch/button pins match the minimal bring-up constraints.
## - UART TX is mapped to PMOD JE pin 1 by default (package pin V12).
##   Connect an external USB-UART adapter to PMOD JE:
##     - Adapter RX  <- JE pin 1 (TX)
##     - Adapter GND <- JE pin 5/11 (GND on the PMOD header)

## Clock (125 MHz)
set_property -dict { PACKAGE_PIN K17 IOSTANDARD LVCMOS33 } [get_ports { sysclk }]
create_clock -add -name sysclk -period 8.000 -waveform {0 4} [get_ports { sysclk }]

## LEDs
set_property -dict { PACKAGE_PIN M14 IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN M15 IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN G14 IOSTANDARD LVCMOS33 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN D18 IOSTANDARD LVCMOS33 } [get_ports { led[3] }]

## Switches
set_property -dict { PACKAGE_PIN G15 IOSTANDARD LVCMOS33 } [get_ports { sw[0] }]
set_property -dict { PACKAGE_PIN P15 IOSTANDARD LVCMOS33 } [get_ports { sw[1] }]
set_property -dict { PACKAGE_PIN W13 IOSTANDARD LVCMOS33 } [get_ports { sw[2] }]
set_property -dict { PACKAGE_PIN T16 IOSTANDARD LVCMOS33 } [get_ports { sw[3] }]

## Buttons
set_property -dict { PACKAGE_PIN K18 IOSTANDARD LVCMOS33 } [get_ports { btn[0] }]
set_property -dict { PACKAGE_PIN P16 IOSTANDARD LVCMOS33 } [get_ports { btn[1] }]
set_property -dict { PACKAGE_PIN K19 IOSTANDARD LVCMOS33 } [get_ports { btn[2] }]
set_property -dict { PACKAGE_PIN Y16 IOSTANDARD LVCMOS33 } [get_ports { btn[3] }]

## UART TX (PMOD JE pin 1)
set_property -dict { PACKAGE_PIN V12 IOSTANDARD LVCMOS33 } [get_ports { uart_tx }]

