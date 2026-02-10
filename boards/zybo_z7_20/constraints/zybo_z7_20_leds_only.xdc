## Zybo Z7-20 LED-only constraints (for PS/PL BD-based platforms).
## Derived from Digilent's Zybo-Z7-Master.xdc.
##
## Intended for designs where the clock/reset come from the Zynq PS (FCLK),
## so we only constrain the PL LEDs here.

## LEDs
set_property -dict { PACKAGE_PIN M14 IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN M15 IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN G14 IOSTANDARD LVCMOS33 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN D18 IOSTANDARD LVCMOS33 } [get_ports { led[3] }]

