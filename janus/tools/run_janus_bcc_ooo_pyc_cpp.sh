#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

# Default to generating a Konata pipeview trace for each run (disable with: PYC_KONATA=0).
export PYC_KONATA="${PYC_KONATA:-1}"

GEN_DIR="${ROOT_DIR}/janus/generated/janus_bcc_ooo_pyc"
HDR="${GEN_DIR}/janus_bcc_ooo_pyc_gen.hpp"

need_regen=0
if [[ ! -f "${HDR}" ]]; then
  need_regen=1
elif find "${ROOT_DIR}/janus/pyc/janus" -name '*.py' -newer "${HDR}" | grep -q .; then
  need_regen=1
elif ! grep -q "dispatch_fire0" "${HDR}" || ! grep -q "dispatch_pc0" "${HDR}" || ! grep -q "dispatch_rob0" "${HDR}" || ! grep -q "dispatch_op0" "${HDR}" || \
     ! grep -q "issue_fire0" "${HDR}" || ! grep -q "issue_pc0" "${HDR}" || ! grep -q "issue_rob0" "${HDR}" || ! grep -q "issue_op0" "${HDR}"; then
  # Header is older than the current TB's trace hooks; regenerate.
  need_regen=1
fi

if [[ "${need_regen}" -ne 0 ]]; then
  bash "${ROOT_DIR}/janus/update_generated.sh"
fi

TB_SRC="${ROOT_DIR}/janus/tb/tb_janus_bcc_ooo_pyc.cpp"
TB_EXE="${GEN_DIR}/tb_janus_bcc_ooo_pyc_cpp"

need_build=0
if [[ ! -x "${TB_EXE}" ]]; then
  need_build=1
elif [[ "${TB_SRC}" -nt "${TB_EXE}" || "${HDR}" -nt "${TB_EXE}" ]]; then
  need_build=1
fi

if [[ "${need_build}" -ne 0 ]]; then
  mkdir -p "${GEN_DIR}"
  tmp_exe="${TB_EXE}.tmp.$$"
  "${CXX:-clang++}" -std=c++17 -O3 -DNDEBUG \
    -I "${ROOT_DIR}/include" \
    -I "${GEN_DIR}" \
    -o "${tmp_exe}" \
    "${TB_SRC}"
  mv -f "${tmp_exe}" "${TB_EXE}"
fi

if [[ $# -gt 0 ]]; then
  "${TB_EXE}" "$@"
else
  "${TB_EXE}"
fi
