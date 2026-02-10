#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

VERILATOR="${VERILATOR:-$(command -v verilator || true)}"
if [[ -z "${VERILATOR}" ]]; then
  echo "error: missing verilator (install with: brew install verilator)" >&2
  exit 1
fi

GEN_DIR="${ROOT_DIR}/janus/generated/janus_tmu_pyc"
VLOG="${GEN_DIR}/janus_tmu_pyc.v"
if [[ ! -f "${VLOG}" ]]; then
  bash "${ROOT_DIR}/janus/tools/update_tmu_generated.sh"
fi

TB_SV="${ROOT_DIR}/janus/tb/tb_janus_tmu_pyc.sv"
OBJ_DIR="${GEN_DIR}/verilator_obj"
EXE="${OBJ_DIR}/Vtb_janus_tmu_pyc"

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
    --top-module tb_janus_tmu_pyc \
    "${TB_SV}" \
    "${VLOG}" \
    --Mdir "${OBJ_DIR}"
fi

echo "[janus-vlt] tmu"
"${EXE}" "$@"
