#!/usr/bin/env bash
# Gate for delay-line chain formation: runs pyc-state-optimize (and the
# standalone pyc-combine-delay-chains pass) over state_delay_chain.mlir and
# checks the result with FileCheck.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"
INPUT="${ROOT}/compiler/mlir/test/state_delay_chain.mlir"

if [[ ! -x "${PYC_OPT}" ]]; then
  echo "fail: build pyc-opt before running this gate" >&2
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

TMP_DIR="$(mktemp -d /tmp/pyc-state-delay-chain.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

"${PYC_OPT}" "${INPUT}" \
  --pass-pipeline='builtin.module(func.func(pyc-state-optimize))' \
  -o "${TMP_DIR}/state_optimize.mlir"
"${FILECHECK_BIN}" "${INPUT}" --input-file="${TMP_DIR}/state_optimize.mlir"

# The standalone chain pass must form the same delay lines (dead state aside).
"${PYC_OPT}" "${INPUT}" \
  --pass-pipeline='builtin.module(func.func(pyc-combine-delay-chains))' \
  -o "${TMP_DIR}/combine.mlir"
grep -q 'pyc.delay_line' "${TMP_DIR}/combine.mlir"

echo "state_delay_chain_smoke: PASS"
