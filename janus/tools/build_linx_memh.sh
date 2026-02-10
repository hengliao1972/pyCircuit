#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <program.{S|c}> <out.memh>" >&2
  exit 2
fi

SRC="$1"
OUT_MEMH="$2"

LLVM_BIN="${LLVM_LINXISA_BIN:-${HOME}/llvm-project/build-linxisa-clang/bin}"
CLANG="${LLVM_BIN}/clang"
LD="${LLVM_BIN}/ld.lld"
OBJCOPY="${LLVM_BIN}/llvm-objcopy"
LINX_LD_SCRIPT="${LINX_LD_SCRIPT:-${HOME}/linx-libc/linx.ld}"

if [[ ! -x "${CLANG}" ]]; then
  echo "error: missing clang at ${CLANG}" >&2
  exit 1
fi
if [[ ! -x "${LD}" ]]; then
  echo "error: missing ld.lld at ${LD}" >&2
  exit 1
fi
if [[ ! -x "${OBJCOPY}" ]]; then
  echo "error: missing llvm-objcopy at ${OBJCOPY}" >&2
  exit 1
fi
if [[ ! -f "${LINX_LD_SCRIPT}" ]]; then
  echo "error: missing linker script at ${LINX_LD_SCRIPT}" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d -t linx_memh.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

OBJ="${WORK_DIR}/prog.o"
ELF="${WORK_DIR}/prog.elf"
HEX="${WORK_DIR}/prog.hex"

"${CLANG}" --target=linx64-unknown-elf -nostdlib -ffreestanding -c -o "${OBJ}" "${SRC}"
"${LD}" -m elf64linx -T "${LINX_LD_SCRIPT}" -o "${ELF}" "${OBJ}"
"${OBJCOPY}" -O ihex "${ELF}" "${HEX}"

mkdir -p "$(dirname -- "${OUT_MEMH}")"
python3 "${ROOT_DIR}/janus/tools/ihex_to_memh.py" "${HEX}" "${OUT_MEMH}"
echo "${OUT_MEMH}"

