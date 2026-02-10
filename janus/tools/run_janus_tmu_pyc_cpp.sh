#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=../../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

GEN_DIR="${ROOT_DIR}/janus/generated/janus_tmu_pyc"
HDR="${GEN_DIR}/janus_tmu_pyc_gen.hpp"

need_regen=0
if [[ ! -f "${HDR}" ]]; then
  need_regen=1
elif find "${ROOT_DIR}/janus/pyc/janus/tmu" -name '*.py' -newer "${HDR}" | grep -q .; then
  need_regen=1
fi

if [[ "${need_regen}" -ne 0 ]]; then
  bash "${ROOT_DIR}/janus/tools/update_tmu_generated.sh"
fi

WORK_DIR="$(mktemp -d -t janus_tmu_pyc_tb.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

"${CXX:-clang++}" -std=c++17 -O2 \
  -I "${ROOT_DIR}/include" \
  -I "${GEN_DIR}" \
  -o "${WORK_DIR}/tb_janus_tmu_pyc" \
  "${ROOT_DIR}/janus/tb/tb_janus_tmu_pyc.cpp"

if [[ $# -gt 0 ]]; then
  "${WORK_DIR}/tb_janus_tmu_pyc" "$@"
else
  "${WORK_DIR}/tb_janus_tmu_pyc"
fi
