# Linx workspace on Windows + Zybo Z7-20 (local setup)

This is a practical checklist to get the **pyCircuit + LinxISA** toolchains and FPGA bring-up flow working on a Windows machine.

Expected workspace layout (matches the skills):

- `%USERPROFILE%\\linx\\`
  - `pyCircuit\\`
  - `linx-isa\\`
  - `llvm-project\\`
  - `qemu\\`
  - `linux\\` (optional)

## 1) Quick sanity: verify what is installed

Run:

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\linx_doctor.ps1
```

## 2) FPGA bring-up (Zybo Z7-20)

### 2.1 Known-good LED blink

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\zybo_z7_20_flow.ps1 -Program
```

### 2.2 LinxISA CPU bring-up demo (UART + exit MMIO)

Build/program:

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\zybo_z7_20_linx_cpu_flow.ps1 -Program
```

Behavior:
- `sw[0]=1` releases reset (runs the core)
- `btn[0]` resets
- `led[3:0]` shows a heartbeat while running; after halt it shows `exit_code[3:0]`

UART:
- The default constraints map `uart_tx` to **PMOD JE pin 1** (`boards/zybo_z7_20/constraints/zybo_z7_20_linx_cpu.xdc`).
- Connect an external USB-UART adapter RX to JE pin 1 and open a terminal at **115200 8N1**.

Program image:
- Default memory image is `boards/zybo_z7_20/programs/test_or.memh`.
- Replace it with a UART hello program by generating a new `.memh` and updating `defparam ... INIT_MEMH` in `boards/zybo_z7_20/rtl/zybo_linx_cpu_top.sv`.

## 3) Toolchains (recommended: WSL2 Ubuntu 22.04)

Install WSL2 + Ubuntu 22.04 (requires admin, may require reboot):

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\install_wsl2_ubuntu2204.ps1
```

If you want the Ubuntu rootfs from the Tsinghua (TUNA) mirror (offline-style `wsl --import`):

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\install_ubuntu2204_tuna_wsl.ps1
```

In WSL, bootstrap pyCircuit (LLVM/MLIR via apt.llvm.org) and build `pyc-compile`:

```bash
bash tools/wsl/bootstrap_pyc_ubuntu2204.sh
```

In WSL, install deps + print the suggested build commands for LLVM (LinxISA backend), QEMU, docs, and regression:

```bash
bash tools/wsl/bootstrap_linx_ubuntu2204.sh
```

Full WSL usage + build script:

- `docs/WSL_UBUNTU_ON_WINDOWS.md`

## 4) Benchmarks (memh images for bring-up)

Once you have a LinxISA clang + libc available, you can build `.memh` images used by Janus bring-up scripts:

```bash
cd /mnt/c/Users/<you>/pyCircuit
export LINXISA_DIR=/mnt/c/Users/<you>/linx/linx-isa
export LLVM_LINXISA_BIN=/mnt/c/Users/<you>/linx/llvm-project/build-linxisa-clang/bin
export LINX_LD_SCRIPT=/mnt/c/Users/<you>/linx/linx-isa/toolchain/libc/linx.ld
bash janus/tools/build_linxisa_benchmarks_memh.sh
```
