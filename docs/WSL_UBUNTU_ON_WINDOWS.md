# Ubuntu on Windows (WSL2) for Linx bring-up

This doc shows how to install **Ubuntu 22.04** into **WSL2** from the **Tsinghua (TUNA) mirror**, then build the Linx bring-up toolchains (LLVM/Clang, QEMU, pyCircuit) under WSL.

## 0) Pre-reqs (Windows)

- WSL2 enabled (`wsl.exe --status`)
- A distro install path, e.g. `%USERPROFILE%\\wsl\\`
- Linx workspace (recommended):
  - `%USERPROFILE%\\linx\\pyCircuit\\`
  - `%USERPROFILE%\\linx\\linx-isa\\`
  - `%USERPROFILE%\\linx\\llvm-project\\`
  - `%USERPROFILE%\\linx\\qemu\\`

## 1) Install Ubuntu 22.04 from TUNA (recommended)

Run from `pyCircuit` repo root (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows\install_ubuntu2204_tuna_wsl.ps1
```

What it does:
- Downloads `ubuntu-jammy-wsl-amd64-ubuntu22.04lts.rootfs.tar.gz` from TUNA
- Verifies SHA256 using the mirror `SHA256SUMS`
- Imports the distro via `wsl.exe --import`
- Creates a Linux user matching your Windows username
- (Default) switches `apt` to `mirrors.tuna.tsinghua.edu.cn`

After install:

```powershell
wsl.exe -d Ubuntu2204TUNA
```

## 2) Build toolchains in WSL (ext4 recommended)

Building large projects on `/mnt/c` is slow and may hit CRLF issues. The build script below clones your Windows repos into WSL’s ext4 filesystem (`~/linx/src/`) and builds there.

In Ubuntu (WSL):

```bash
bash /mnt/c/Users/$USER/linx/pyCircuit/tools/wsl/build_linx_toolchains_ubuntu2204.sh
```

Outputs (defaults):
- LLVM/Clang/LLD/MLIR: `~/linx/build/llvm-linxisa-clang/`
- QEMU (linx64): `~/linx/src/qemu/build/qemu-system-linx64`
- pyCircuit tools: `~/linx/build/pyCircuit/bin/pyc-compile`, `~/linx/build/pyCircuit/bin/pyc-opt`

## 3) Run a quick smoke

### 3.1 pyCircuit Linx CPU (C++ TB)

```bash
cd ~/linx/src/pyCircuit
env PYC_COMPILE=~/linx/build/pyCircuit/bin/pyc-compile CXX=/usr/bin/g++ \
  bash tools/run_linx_cpu_pyc_cpp.sh
```

### 3.2 QEMU binary

```bash
~/linx/src/qemu/build/qemu-system-linx64 --version
```

## 4) Linux kernel for Linx (status)

Your current `linux/` tree must contain `arch/linx/` to build a Linx kernel (`ARCH=linx`). If it does not, you can still:
- run baremetal tests on QEMU
- iterate compiler + QEMU + RTL/FPGA bring-up

When you have a Linx-kernel tree, the QEMU note `~/linx/src/qemu/docs/linxisa/kernel-build.md` is the reference build recipe.

