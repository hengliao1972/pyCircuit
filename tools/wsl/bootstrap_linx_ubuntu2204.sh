#!/usr/bin/env bash
set -euo pipefail

log() { echo "[wsl-linx] $*"; }

LINX_ROOT="${LINX_ROOT:-$HOME/linx}"

log "LINX_ROOT=${LINX_ROOT}"
log "apt update"
sudo apt-get update

log "install deps (llvm/qemu/docs)"
sudo apt-get install -y \
  build-essential cmake ninja-build git pkg-config \
  python3 python3-venv python3-pip \
  curl ca-certificates \
  meson \
  libglib2.0-dev libpixman-1-dev zlib1g-dev \
  ruby-full ruby-bundler

if [[ ! -d "${LINX_ROOT}/llvm-project" ]]; then
  log "warning: missing ${LINX_ROOT}/llvm-project (set LINX_ROOT or clone first)"
fi
if [[ ! -d "${LINX_ROOT}/qemu" ]]; then
  log "warning: missing ${LINX_ROOT}/qemu (set LINX_ROOT or clone first)"
fi
if [[ ! -d "${LINX_ROOT}/linx-isa" ]]; then
  log "warning: missing ${LINX_ROOT}/linx-isa (set LINX_ROOT or clone first)"
fi

log "suggested builds (run manually; these are large):"
cat <<EOF

LLVM (LinxISA backend):
  cmake -G Ninja -S "${LINX_ROOT}/llvm-project/llvm" -B "${LINX_ROOT}/llvm-project/build-linxisa-clang" \\
    -DCMAKE_BUILD_TYPE=Release \\
    -DLLVM_ENABLE_PROJECTS=clang \\
    -DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=LinxISA \\
    -DLLVM_TARGETS_TO_BUILD=X86 \\
    -DLLVM_ENABLE_ASSERTIONS=ON
  ninja -C "${LINX_ROOT}/llvm-project/build-linxisa-clang" clang llc llvm-objdump llvm-objcopy llvm-readobj lld

QEMU (LinxISA virt machine):
  meson setup "${LINX_ROOT}/qemu/build-linx64" "${LINX_ROOT}/qemu" -Dtarget_list=linx64-softmmu
  ninja -C "${LINX_ROOT}/qemu/build-linx64" qemu-system-linx64

LinxISA ISA manual:
  (cd "${LINX_ROOT}/linx-isa/docs/architecture/isa-manual" && make pdf)

LinxISA regression (uses built clang+qemu):
  export CLANG="${LINX_ROOT}/llvm-project/build-linxisa-clang/bin/clang"
  export LLD="${LINX_ROOT}/llvm-project/build-linxisa-clang/bin/ld.lld"
  export QEMU="${LINX_ROOT}/qemu/build-linx64/qemu-system-linx64"
  (cd "${LINX_ROOT}/linx-isa" && bash tools/regression/run.sh)

EOF

log "done"

