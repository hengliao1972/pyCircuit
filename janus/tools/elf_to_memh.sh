#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <program.elf> <out.memh>" >&2
  exit 2
fi

ELF="$1"
OUT_MEMH="$2"

LLVM_BIN="${LLVM_LINXISA_BIN:-${HOME}/llvm-project/build-linxisa-clang/bin}"
OBJCOPY="${LLVM_BIN}/llvm-objcopy"

if [[ ! -f "${ELF}" ]]; then
  echo "error: missing ELF: ${ELF}" >&2
  exit 1
fi
if [[ ! -x "${OBJCOPY}" ]]; then
  echo "error: missing llvm-objcopy at ${OBJCOPY}" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d -t linx_elf_memh.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

HEX="${WORK_DIR}/prog.hex"

# Keep load addresses (ihex preserves section placement).
"${OBJCOPY}" -O ihex "${ELF}" "${HEX}"

mkdir -p "$(dirname -- "${OUT_MEMH}")"
python3 "${ROOT_DIR}/janus/tools/ihex_to_memh.py" "${HEX}" "${OUT_MEMH}"
echo "${OUT_MEMH}"

