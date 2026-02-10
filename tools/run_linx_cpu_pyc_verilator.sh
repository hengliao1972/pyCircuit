#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

VERILATOR="${VERILATOR:-$(command -v verilator || true)}"
if [[ -z "${VERILATOR}" ]]; then
  echo "error: missing verilator (install with: apt-get install verilator  # or build a newer one)" >&2
  exit 1
fi

GEN_DIR="${ROOT_DIR}/examples/generated/linx_cpu_pyc"
VLOG="${GEN_DIR}/linx_cpu_pyc.v"
if [[ ! -f "${VLOG}" ]]; then
  echo "error: missing generated Verilog: ${VLOG}" >&2
  exit 1
fi

TB_SV="${ROOT_DIR}/examples/linx_cpu/tb_linx_cpu_pyc.sv"
OBJ_DIR="${GEN_DIR}/verilator_obj"
EXE="${OBJ_DIR}/Vtb_linx_cpu_pyc"

need_build=0
if [[ ! -x "${EXE}" ]]; then
  need_build=1
elif [[ "${TB_SV}" -nt "${EXE}" || "${VLOG}" -nt "${EXE}" ]]; then
  need_build=1
fi

if [[ "${need_build}" -ne 0 ]]; then
  mkdir -p "${OBJ_DIR}"
  "${VERILATOR}" \
    --binary \
    --timing \
    --trace \
    -Wno-fatal \
    -I"${ROOT_DIR}/include/pyc/verilog" \
    --top-module tb_linx_cpu_pyc \
    "${TB_SV}" \
    "${VLOG}" \
    --Mdir "${OBJ_DIR}"
fi

run_case() {
  local name="$1"
  shift
  echo "[cpu-vlt] ${name}"
  "${EXE}" "$@"
}

if [[ $# -gt 0 ]]; then
  run_case "program" "$@"
  exit 0
fi

run_case "test_or" \
  +memh="${ROOT_DIR}/examples/linx_cpu/programs/test_or.memh" \
  +expected=0000ff00 \
  +expected_exit=00000000

run_case "test_csel_fixed" \
  +memh="${ROOT_DIR}/examples/linx_cpu/programs/test_csel_fixed.memh" \
  +expected=00000064 \
  +expected_exit=00000000

run_case "test_branch2" \
  +no_memcheck \
  +memh="${ROOT_DIR}/examples/linx_cpu/programs/test_branch2.memh" \
  +expected_exit=00000000

run_case "test_call_simple" \
  +no_memcheck \
  +memh="${ROOT_DIR}/examples/linx_cpu/programs/test_call_simple.memh" \
  +expected_exit=00000000

run_case "test_jump" \
  +no_memcheck \
  +memh="${ROOT_DIR}/examples/linx_cpu/programs/test_jump.memh" \
  +expected_exit=00000000

run_case "test_pcrel" \
  +no_memcheck \
  +memh="${ROOT_DIR}/examples/linx_cpu/programs/test_pcrel.memh" \
  +expected_exit=00000000
