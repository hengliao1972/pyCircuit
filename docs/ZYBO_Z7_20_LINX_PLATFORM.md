# Zybo Z7-20: Linx PS/PL platform (in-order + OOO)

This repo provides two Zybo Z7-20 PS/PL bitstreams:

- **In-order** core: `linx_cpu_pyc` (from `examples/generated/linx_cpu_pyc/`)
- **OOO** core: `janus_bcc_ooo_pyc` (from `janus/generated/janus_bcc_ooo_pyc/`)

Both use the same PL control/status block (AXI4-Lite) so a PS “monitor” app can:

- hold the core in reset
- set `boot_pc` / `boot_sp`
- load a program image via `host_w*` (while core held in reset)
- drain UART bytes and report `exit_code` + `cycles`

## Build/program bitstreams (Windows + Vivado)

Build + program the in-order platform:

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\zybo_z7_20_linx_platform_flow.ps1 -Core InOrder -Program
```

Build + program the OOO platform:

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\zybo_z7_20_linx_platform_flow.ps1 -Core OOO -Program
```

Each build also exports an `.xsa` in the Vivado build directory, so you can build the PS app in Vitis/XSCT.

## PL register map

Implemented in `boards/zybo_z7_20/rtl/linx_platform_regs_axi.sv`.

Default base address (Vivado scripts): `0x43C0_0000`.

- `0x00` `CTRL` bit0 = reset (1=assert)
- `0x04` `STATUS` bit0 = halted
- `0x08/0x0C` `BOOT_PC` (lo/hi)
- `0x10/0x14` `BOOT_SP` (lo/hi)
- `0x18/0x1C` `HOST_ADDR` (lo/hi)
- `0x20/0x24` `HOST_DATA` (lo/hi)
- `0x28` `HOST_STRB` (bits[7:0])
- `0x2C` `HOST_CMD` write 1 => host write pulse
- `0x30` `UART_STATUS` count[15:0], overflow[16] (write 1 clears overflow)
- `0x34` `UART_DATA` read pops next byte
- `0x38` `EXIT_CODE`
- `0x3C/0x40` `CYCLES` (lo/hi)

## FPGA regression runner

After programming a platform bitstream and running the PS monitor app, you can run a suite from a PC:

```powershell
python tools\zybo\run_fpga_suite.py --port COM5 --manifest boards\zybo_z7_20\tests\fpga_manifest_inorder.json
python tools\zybo\run_fpga_suite.py --port COM5 --manifest boards\zybo_z7_20\tests\fpga_manifest_ooo.json
```

