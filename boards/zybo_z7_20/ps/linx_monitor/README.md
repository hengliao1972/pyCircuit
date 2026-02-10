# Zybo Z7-20 PS monitor (baremetal)

This is a minimal Zynq PS bring-up monitor for the Linx PS/PL platform wrappers:

- `boards/zybo_z7_20/rtl/linx_platform_inorder_axi.sv`
- `boards/zybo_z7_20/rtl/linx_platform_ooo_axi.sv`

It runs on the Zybo Z7-20 PS (Cortex-A9), prints to the PS UART (USB serial),
and controls the PL core via AXI4-Lite:

- Holds the core in reset
- Sets `boot_pc` / `boot_sp`
- Loads a `.memh` image via the core `host_w*` port (while core held in reset)
- Drains UART bytes and reports `exit_code` + `cycles`

## Build notes

This app is meant to be built with Xilinx tools (Vitis Classic / XSCT + standalone BSP).

- The PL register map is defined in `src/linx_platform.h`.
- The default AXI base address matches the Vivado BD scripts: `0x43C0_0000`.

If you change the BD address in Vivado, update `LINX_PLAT_BASE` accordingly.

## UART command protocol

The monitor implements a simple line-based protocol over the PS UART:

- `PING`
- `RESET 0|1`
- `BOOT <pc_hex> <sp_hex>`
- `LOAD_MEMH` then stream `.memh` lines, terminate with `END`
- `RUN` (release reset, drain UART FIFO until halt)
- `STATUS`

The `RUN` command prints a machine-parseable summary:

`HALT exit=0x........ cycles=...`

