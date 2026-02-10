#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

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
  $0 --elf  <file> [--expected <hex>]   # convert ELF -> memh and run

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

WORK_DIR="$(mktemp -d -t linx_cpu_pyc_ca.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

cd "${ROOT_DIR}"

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
  if [[ -z "${PYC_MAX_CYCLES:-}" ]]; then
    export PYC_MAX_CYCLES="200000"
  fi
else
  if [[ -z "${PYC_MAX_CYCLES:-}" ]]; then
    export PYC_MAX_CYCLES="200000"
  fi
fi

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(pyc_pythonpath)" \
  python3 -m pycircuit.cli emit examples/linx_cpu_pyc_cycle_aware/linx_cpu_pyc.py -o "${WORK_DIR}/linx_cpu_pyc_cycle_aware.pyc"

"${PYC_COMPILE}" "${WORK_DIR}/linx_cpu_pyc_cycle_aware.pyc" --emit=cpp -o "${WORK_DIR}/linx_cpu_pyc_cycle_aware_gen.hpp"

"${CXX:-clang++}" -std=c++17 -O2 \
  -I "${ROOT_DIR}/include" \
  -I "${WORK_DIR}" \
  -o "${WORK_DIR}/tb_linx_cpu_pyc_cycle_aware" \
  "${ROOT_DIR}/examples/linx_cpu_pyc_cycle_aware/tb_linx_cpu_pyc.cpp"

if [[ -n "${MEMH}" ]]; then
  if [[ -n "${EXPECTED}" ]]; then
    "${WORK_DIR}/tb_linx_cpu_pyc_cycle_aware" "${MEMH}" "${EXPECTED}"
  else
    "${WORK_DIR}/tb_linx_cpu_pyc_cycle_aware" "${MEMH}"
  fi
else
  "${WORK_DIR}/tb_linx_cpu_pyc_cycle_aware"
fi
