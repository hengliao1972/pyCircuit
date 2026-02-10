#!/usr/bin/env bash
set -euo pipefail

log() { echo "[linx-build] $*"; }

WIN_USER="${WIN_USER:-}"
if [[ -z "$WIN_USER" ]]; then
  WIN_USER="$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r' | tail -n 1)"
fi
if [[ -z "$WIN_USER" ]]; then
  log "error: could not determine Windows username; set WIN_USER=..."
  exit 1
fi

WIN_LINX="/mnt/c/Users/${WIN_USER}/linx"
SRC_ROOT="${SRC_ROOT:-$HOME/linx/src}"
BUILD_ROOT="${BUILD_ROOT:-$HOME/linx/build}"

log "WIN_LINX=${WIN_LINX}"
log "SRC_ROOT=${SRC_ROOT}"
log "BUILD_ROOT=${BUILD_ROOT}"

sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential cmake ninja-build git pkg-config \
  python3 python3-venv python3-pip python3-tomli \
  curl ca-certificates \
  flex bison bc libssl-dev libelf-dev dwarves rsync ccache \
  libglib2.0-dev libpixman-1-dev zlib1g-dev libfdt-dev libslirp-dev \
  libxml2-dev libedit-dev

mkdir -p "${SRC_ROOT}" "${BUILD_ROOT}"

clone_if_missing() {
  local name="$1"
  local src="${WIN_LINX}/${name}"
  local dst="${SRC_ROOT}/${name}"
  if [[ -d "${dst}/.git" ]]; then
    log "src exists: ${dst}"
    return 0
  fi
  if [[ ! -d "${src}/.git" ]]; then
    log "error: missing Windows repo: ${src}"
    exit 1
  fi
  log "clone ${name} -> ext4 (${dst})"
  git clone "${src}" "${dst}"
}

clone_if_missing llvm-project
clone_if_missing qemu
clone_if_missing linx-isa
clone_if_missing pyCircuit

LLVM_BUILD="${BUILD_ROOT}/llvm-linxisa-clang"
log "build LLVM+Clang+LLD+MLIR: ${LLVM_BUILD}"
rm -rf "${LLVM_BUILD}"
cmake -G Ninja -S "${SRC_ROOT}/llvm-project/llvm" -B "${LLVM_BUILD}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLVM_ENABLE_PROJECTS=clang\;lld\;mlir \
  -DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=LinxISA \
  -DLLVM_TARGETS_TO_BUILD=X86 \
  -DLLVM_ENABLE_ASSERTIONS=ON \
  -DLLVM_CCACHE_BUILD=ON \
  -DLLVM_ENABLE_TERMINFO=OFF

# Keep parallelism conservative to avoid OOM on smaller machines.
ninja -C "${LLVM_BUILD}" -j 2 \
  clang llc lld llvm-mc llvm-objdump llvm-objcopy llvm-readobj llvm-config mlir-opt

# Fix: avoid non-portable TLS init dependency in llvm-objdump (WSL link).
log "patch llvm-objdump LinxPrettyAddrToSym (avoid thread_local)"
sed -i 's/^thread_local const std::unordered_map<uint64_t, StringRef> \\*LinxPrettyAddrToSym/static const std::unordered_map<uint64_t, StringRef> *LinxPrettyAddrToSym/' \
  "${SRC_ROOT}/llvm-project/llvm/tools/llvm-objdump/llvm-objdump.cpp"
sed -i '/extern thread_local const std::unordered_map<uint64_t, StringRef>/d; /^[[:space:]]*LinxPrettyAddrToSym;[[:space:]]*$/d' \
  "${SRC_ROOT}/llvm-project/llvm/tools/llvm-objdump/llvm-objdump.cpp"
ninja -C "${LLVM_BUILD}" -j 2 llvm-objdump

log "upgrade meson (QEMU requires >= 1.5.0)"
python3 -m pip install --user --upgrade 'meson>=1.5.0'

log "patch QEMU pythondeps.toml (use system ninja, keep offline wheels)"
if grep -q '^ninja = ' "${SRC_ROOT}/qemu/pythondeps.toml"; then
  cp "${SRC_ROOT}/qemu/pythondeps.toml" "${SRC_ROOT}/qemu/pythondeps.toml.bak"
  sed -i '/^ninja = /d' "${SRC_ROOT}/qemu/pythondeps.toml"
fi

log "build QEMU linx64-softmmu"
(cd "${SRC_ROOT}/qemu" && rm -rf build pyvenv && ./configure --target-list=linx64-softmmu)
ninja -C "${SRC_ROOT}/qemu/build" -j 2 qemu-system-linx64

log "build pyCircuit (pyc-compile/pyc-opt) against built MLIR"
PYC_BUILD="${BUILD_ROOT}/pyCircuit"
rm -rf "${PYC_BUILD}"
cmake -G Ninja -S "${SRC_ROOT}/pyCircuit" -B "${PYC_BUILD}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLVM_DIR="${LLVM_BUILD}/lib/cmake/llvm" \
  -DMLIR_DIR="${LLVM_BUILD}/lib/cmake/mlir" \
  -DPYC_BUILD_CPP_EXAMPLES=OFF
ninja -C "${PYC_BUILD}" -j 2 pyc-compile pyc-opt

log "sanity: run pyCircuit Linx CPU C++ regression"
(cd "${SRC_ROOT}/pyCircuit" && env PYC_COMPILE="${PYC_BUILD}/bin/pyc-compile" CXX=/usr/bin/g++ bash tools/run_linx_cpu_pyc_cpp.sh)

log "note: Linux-for-Linx build requires a kernel tree with arch/linx (not included in this workspace by default)."

log "done"

