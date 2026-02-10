#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

# Default to generating a Konata pipeview trace for each run (disable with: PYC_KONATA=0).
export PYC_KONATA="${PYC_KONATA:-1}"

MEMH=""
ELF=""
EXPECTED=""
ELF_TEXT_BASE="0x10000"
ELF_DATA_BASE="0x20000"
ELF_PAGE_ALIGN="0x1000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --memh)
      MEMH="${2:?missing value for --memh}"
      shift 2
      ;;
    --elf)
      ELF="${2:?missing value for --elf}"
      shift 2
      ;;
    --expected)
      EXPECTED="${2:?missing value for --expected}"
      shift 2
      ;;
    --base)
      ELF_TEXT_BASE="${2:?missing value for --base}"
      shift 2
      ;;
    --text-base)
      ELF_TEXT_BASE="${2:?missing value for --text-base}"
      shift 2
      ;;
    --data-base)
      ELF_DATA_BASE="${2:?missing value for --data-base}"
      shift 2
      ;;
    --page-align)
      ELF_PAGE_ALIGN="${2:?missing value for --page-align}"
      shift 2
      ;;
    -h|--help)
      cat <<EOF
Usage:
  $0                     # run built-in regression memh tests
  $0 --memh <file> [--expected <hex>]   # run one memh program
  $0 --elf  <file> [--expected <hex>]   # convert ELF -> memh (apply relocs, load .data/.bss) and run

ELF options:
  --base <addr>       Alias for --text-base (default: 0x10000)
  --text-base <addr>  (default: 0x10000)
  --data-base <addr>  (default: 0x20000)
  --page-align <addr> (default: 0x1000)
EOF
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

WORK_DIR="$(mktemp -d -t linx_cpu_pyc.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

cd "${ROOT_DIR}"

EMIT_PARAMS=()

if [[ -n "${ELF}" ]]; then
  MEMH="${WORK_DIR}/program.memh"
  META="$(PYTHONDONTWRITEBYTECODE=1 python3 tools/linxisa/elf_to_memh.py "${ELF}" --text-base "${ELF_TEXT_BASE}" --data-base "${ELF_DATA_BASE}" --page-align "${ELF_PAGE_ALIGN}" -o "${MEMH}" --print-start --print-max)"
  START_PC="$(printf "%s\n" "${META}" | sed -n '1p')"
  MAX_END="$(printf "%s\n" "${META}" | sed -n '2p')"
  if [[ -z "${PYC_BOOT_PC:-}" ]]; then
    export PYC_BOOT_PC="${START_PC}"
  fi
  if [[ -z "${PYC_MEM_BYTES:-}" ]]; then
    MEM_BYTES="$(
      PYTHONDONTWRITEBYTECODE=1 python3 - "${MAX_END}" <<'PY'
import sys

end = int(sys.argv[1], 0)
min_size = 1 << 20
size = min_size
while size < end:
    size <<= 1
print(size)
PY
    )"
    export PYC_MEM_BYTES="${MEM_BYTES}"
  fi
fi

if [[ -n "${MEMH}" ]]; then
  # If the user didn't provide a memory size, compute the minimum power-of-two
  # depth that covers the memh image.
  if [[ -z "${PYC_MEM_BYTES:-}" ]]; then
    MAX_END="$(
      PYTHONDONTWRITEBYTECODE=1 python3 - "${MEMH}" <<'PY'
import sys

path = sys.argv[1]
max_end = 0
addr = 0
with open(path, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        if line[0] == "@":
            addr = int(line[1:], 16)
            if addr > max_end:
                max_end = addr
            continue
        # One byte token per line.
        addr += 1
        if addr > max_end:
            max_end = addr
print(hex(max_end))
PY
    )"
    MEM_BYTES="$(
      PYTHONDONTWRITEBYTECODE=1 python3 - "${MAX_END}" <<'PY'
import sys

end = int(sys.argv[1], 0)
min_size = 1 << 20
size = min_size
while size < end:
    size <<= 1
print(size)
PY
    )"
    export PYC_MEM_BYTES="${MEM_BYTES}"
  fi

  # Default the boot SP to the top of the modeled RAM so stack doesn't overlap
  # large .bss reservations used by benchmarks.
  if [[ -z "${PYC_BOOT_SP:-}" ]]; then
    export PYC_BOOT_SP="$(
      PYTHONDONTWRITEBYTECODE=1 python3 - "${PYC_MEM_BYTES}" <<'PY'
import sys

mem = int(sys.argv[1], 0)
print(hex(max(0, mem - 0x100)))
PY
    )"
  fi

  # Benchmarks can run longer than tiny bring-up tests. Allow override.
  if [[ -z "${PYC_MAX_CYCLES:-}" ]]; then
    export PYC_MAX_CYCLES="10000000"
  fi

  EMIT_PARAMS+=(--param "mem_bytes=${PYC_MEM_BYTES}")
fi

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(pyc_pythonpath)" \
  python3 -m pycircuit.cli emit ${EMIT_PARAMS[@]+"${EMIT_PARAMS[@]}"} examples/linx_cpu_pyc/linx_cpu_pyc.py -o "${WORK_DIR}/linx_cpu_pyc.pyc"

"${PYC_COMPILE}" "${WORK_DIR}/linx_cpu_pyc.pyc" --emit=cpp -o "${WORK_DIR}/linx_cpu_pyc_gen.hpp"

"${CXX:-clang++}" -std=c++17 -O2 \
  -I "${ROOT_DIR}/include" \
  -I "${WORK_DIR}" \
  -o "${WORK_DIR}/tb_linx_cpu_pyc" \
  "${ROOT_DIR}/examples/linx_cpu_pyc/tb_linx_cpu_pyc.cpp"

if [[ -n "${MEMH}" ]]; then
  if [[ -n "${EXPECTED}" ]]; then
    "${WORK_DIR}/tb_linx_cpu_pyc" "${MEMH}" "${EXPECTED}"
  else
    "${WORK_DIR}/tb_linx_cpu_pyc" "${MEMH}"
  fi
else
  "${WORK_DIR}/tb_linx_cpu_pyc"
fi
