#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { echo "[wsl] $*"; }

log "repo: ${ROOT_DIR}"

log "apt update"
sudo apt-get update

log "install base deps"
sudo apt-get install -y \
  build-essential cmake ninja-build git \
  python3 python3-venv python3-pip \
  iverilog verilator gtkwave \
  curl ca-certificates lsb-release

# LLVM/MLIR: use apt.llvm.org to get a recent version with MLIR dev packages.
LLVM_VER="${LLVM_VER:-17}"
if ! command -v "llvm-config-${LLVM_VER}" >/dev/null 2>&1; then
  log "installing LLVM/MLIR ${LLVM_VER} via apt.llvm.org"
  curl -fsSL https://apt.llvm.org/llvm.sh -o /tmp/llvm.sh
  chmod +x /tmp/llvm.sh
  sudo /tmp/llvm.sh "${LLVM_VER}"
fi

log "installing LLVM/MLIR dev packages"
sudo apt-get install -y \
  "llvm-${LLVM_VER}-dev" "llvm-${LLVM_VER}-tools" \
  "mlir-${LLVM_VER}-tools" "libmlir-${LLVM_VER}-dev"

LLVM_DIR="$("llvm-config-${LLVM_VER}" --cmakedir)"
MLIR_DIR="$(dirname "${LLVM_DIR}")/mlir"
export LLVM_DIR MLIR_DIR

log "LLVM_DIR=${LLVM_DIR}"
log "MLIR_DIR=${MLIR_DIR}"

log "build pyc-compile/pyc-opt"
cd "${ROOT_DIR}"
./scripts/pyc build

log "doctor"
python3 tools/pyc_flow.py doctor

log "emit+compile examples/counter.py (verilog)"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${ROOT_DIR}/python" \
  python3 -m pycircuit.cli emit "${ROOT_DIR}/examples/counter.py" -o /tmp/counter.pyc

"${ROOT_DIR}/build/bin/pyc-compile" /tmp/counter.pyc --emit=verilog -o /tmp/counter.v
log "wrote /tmp/counter.v"

