#!/usr/bin/env bash
# Gate for pipeline retiming: runs pyc-retime-pipelines over
# state_retime_pipeline.mlir and checks the result with FileCheck.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"
INPUT="${ROOT}/compiler/mlir/test/state_retime_pipeline.mlir"

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

TMP_DIR="$(mktemp -d /tmp/pyc-state-retime.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

"${PYC_OPT}" "${INPUT}" \
  --pass-pipeline='builtin.module(func.func(pyc-retime-pipelines))' \
  -o "${TMP_DIR}/retimed.mlir"
"${FILECHECK_BIN}" "${INPUT}" --check-prefix=RETIME \
  --input-file="${TMP_DIR}/retimed.mlir"

echo "state_retime_smoke: PASS"
