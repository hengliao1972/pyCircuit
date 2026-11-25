#!/usr/bin/env bash
# Gate for state-loop lowering: FileCheck on the partition pass, then
# loop-vs-flat equivalence of generated C++ on the state fixtures.
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

TMP_DIR="$(mktemp -d /tmp/pyc-state-loop.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

"${PYC_OPT}" "${TEST}/state_loop_partition.mlir" \
  --pass-pipeline='builtin.module(func.func(pyc-state-loop-partition))' \
  -o "${TMP_DIR}/partition.mlir"
"${FILECHECK_BIN}" "${TEST}/state_loop_partition.mlir" \
  --input-file="${TMP_DIR}/partition.mlir"

python3 "${TEST}/check_state_loop_models.py" \
  --pycc "${PYCC}" --cxx "${CXX:-c++}"

echo "state_loop_smoke: PASS"
