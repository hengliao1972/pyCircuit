# Zybo Z7-20 bring-up (minimal LED blink)

This folder adds a tiny, known-good Vivado build that uses the checked-in
pyCircuit-generated `Counter` RTL to blink the Zybo Z7-20 user LEDs.

## What it builds

- Top: `boards/zybo_z7_20/rtl/zybo_counter_top.sv`
- Generated design: `examples/generated/counter/counter.v`
- Constraints: `boards/zybo_z7_20/constraints/zybo_z7_20_minimal.xdc`
- Vivado script: `boards/zybo_z7_20/vivado/build_zybo_counter.tcl`

Behavior on hardware:

- Set `sw[0]=1` to enable counting.
- `led[3:0]` increments about ~2 times per second.
- Hold `btn[0]` to reset (synchronous reset after 2FF sync).

## Build (Windows)

From repo root:

```powershell
vivado -mode batch -source boards/zybo_z7_20/vivado/build_zybo_counter.tcl
```

Optional (program automatically after build):

```powershell
$env:PYC_PROGRAM=1
vivado -mode batch -source boards/zybo_z7_20/vivado/build_zybo_counter.tcl
```

The bitstream is written to:

- `boards/zybo_z7_20/vivado/build/zybo_counter_top.bit`

## LinxISA CPU bring-up demo (UART + exit MMIO)

This repo also includes a Zybo top that instantiates the pyCircuit-generated
`linx_cpu_pyc` bring-up core and routes UART bytes to a simple PL UART TX.

- Top: `boards/zybo_z7_20/rtl/zybo_linx_cpu_top.sv`
- Constraints: `boards/zybo_z7_20/constraints/zybo_z7_20_linx_cpu.xdc`
- Vivado script: `boards/zybo_z7_20/vivado/build_zybo_linx_cpu.tcl`
- Windows helper: `tools/windows/zybo_z7_20_linx_cpu_flow.ps1`

Build/program:

```powershell
powershell -ExecutionPolicy Bypass -File tools\\windows\\zybo_z7_20_linx_cpu_flow.ps1 -Program
```

## Linx PS/PL platform (AXI-Lite monitor-driven bring-up)

For bring-up that uses a Zynq PS app to load programs and stream UART output,
use the PS/PL platform wrappers (in-order + OOO):

- In-order wrapper: `boards/zybo_z7_20/rtl/linx_platform_inorder_axi.sv`
- OOO wrapper: `boards/zybo_z7_20/rtl/linx_platform_ooo_axi.sv`
- AXI regs + UART FIFO: `boards/zybo_z7_20/rtl/linx_platform_regs_axi.sv`
- Constraints: `boards/zybo_z7_20/constraints/zybo_z7_20_leds_only.xdc`
- Vivado scripts:
  - `boards/zybo_z7_20/vivado/build_zybo_linx_platform_inorder.tcl`
  - `boards/zybo_z7_20/vivado/build_zybo_linx_platform_ooo.tcl`
- Windows helper: `tools/windows/zybo_z7_20_linx_platform_flow.ps1`

Build/program:

```powershell
powershell -ExecutionPolicy Bypass -File tools\\windows\\zybo_z7_20_linx_platform_flow.ps1 -Core InOrder -Program
```
