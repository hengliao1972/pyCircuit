#!/usr/bin/env bash
# Gate for delay-line C++ codegen, observation demand, and the Verilog
# diagnostic: FileCheck on the demand pass, pycc emit + runtime checks, and
# software-model comparison of delay taps and retimed pipelines.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST="${ROOT}/compiler/mlir/test"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"

if [[ ! -x "${PYC_OPT}" || ! -x "${PYCC}" ]]; then
  echo "fail: build pyc-opt and pycc before running this gate" >&2
  exit 1
fi

if [[ -n "${FILECHECK:-}" ]]; then
  FILECHECK_BIN="${FILECHECK}"
elif command -v FileCheck >/dev/null 2>&1; then
  FILECHECK_BIN="$(command -v FileCheck)"
else
  FILECHECK_BIN="$(ls -1v /usr/lib/llvm-*/bin/FileCheck /usr/local/opt/llvm/bin/FileCheck /opt/homebrew/opt/llvm/bin/FileCheck 2>/dev/null | tail -1)" || true
fi
if [[ -z "${FILECHECK_BIN}" || ! -x "${FILECHECK_BIN}" ]]; then
  echo "fail: FileCheck not found (install any llvm package or set FILECHECK)" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d /tmp/pyc-state-codegen.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# State with an external observation identity survives dead-state cleanup.
"${PYC_OPT}" "${TEST}/state_observability_performance.mlir" \
  --pass-pipeline='builtin.module(func.func(pyc-eliminate-dead-state))' \
  -o "${TMP_DIR}/kept-observability.mlir"
"${FILECHECK_BIN}" "${TEST}/state_observability_performance.mlir" \
  --check-prefix=KEEP --input-file="${TMP_DIR}/kept-observability.mlir"

# Observation demand: only demanded names stay eager.
"${PYC_OPT}" "${TEST}/observation_demand.mlir" \
  --pass-pipeline="builtin.module(pyc-apply-observation-demand{probe-plan=${TEST}/observation_demand_probe_plan.json})" \
  -o "${TMP_DIR}/observation-demand.mlir"
"${FILECHECK_BIN}" "${TEST}/observation_demand.mlir" --check-prefix=DEMAND \
  --input-file="${TMP_DIR}/observation-demand.mlir"
"${PYC_OPT}" "${TEST}/observation_demand_comb_module.mlir" \
  --pass-pipeline="builtin.module(pyc-apply-observation-demand)" \
  -o "${TMP_DIR}/observation-demand-comb.mlir"
"${FILECHECK_BIN}" "${TEST}/observation_demand_comb_module.mlir" \
  --check-prefix=COMB --input-file="${TMP_DIR}/observation-demand-comb.mlir"

"${PYCC}" "${TEST}/observation_demand_comb_module.mlir" \
  --emit=cpp --observe-named=demand \
  -o "${TMP_DIR}/observation-demand-comb.hpp" \
  2>"${TMP_DIR}/observation-demand-comb.stderr"
if grep -qE 'ready_state[[:space:]]*=|ready_alias[[:space:]]*=' \
  "${TMP_DIR}/observation-demand-comb.hpp"; then
  echo "fail: demand mode still assigned undeclared combinational names" >&2
  exit 1
fi
if ! grep -q 'addWire<8>(reg_path("ready_state")' \
  "${TMP_DIR}/observation-demand-comb.hpp" ||
   ! grep -q 'addWire<8>(reg_path("ready_alias")' \
  "${TMP_DIR}/observation-demand-comb.hpp"; then
  echo "fail: demand mode missing lazy combinational addWire lookups" >&2
  exit 1
fi
"${CXX:-c++}" -std=c++17 -O2 -I"${ROOT}/runtime" \
  -DMODEL_HEADER="\"${TMP_DIR}/observation-demand-comb.hpp\"" \
  "${TEST}/observation_demand_comb_runtime.cpp" \
  -o "${TMP_DIR}/observation-demand-comb-runtime"
if ! "${TMP_DIR}/observation-demand-comb-runtime"; then
  echo "fail: demand combinational findByPath/readU64 did not match AND" >&2
  exit 1
fi

# Delay lines are C++-only: the Verilog backend must reject them with a clear
# diagnostic instead of emitting primitives.
"${PYCC}" "${TEST}/delay_line_vector_tap.mlir" \
  --emit=verilog --include-primitives=false \
  -o "${TMP_DIR}/vector_tap.v" 2>"${TMP_DIR}/vector_tap.stderr" \
  && { echo "fail: verilog emit unexpectedly succeeded" >&2; exit 1; }
grep -q "not supported by the Verilog backend" "${TMP_DIR}/vector_tap.stderr"

# Generated C++ must match independent software models.
python3 "${TEST}/check_state_delay_tap_models.py" \
  --pycc "${PYCC}" --cxx "${CXX:-c++}"
python3 "${TEST}/check_state_retime_models.py" \
  --pycc "${PYCC}" --cxx "${CXX:-c++}"

echo "state_codegen_smoke: PASS"
