#!/usr/bin/env bash
# Gate for fork-merge retiming: FileCheck on the rewrite, then a generated
# C++ model checked against an independent cycle model.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST="${ROOT}/compiler/mlir/test"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"
CXX="${CXX:-c++}"

if [[ ! -x "${PYC_OPT}" || ! -x "${PYCC}" ]]; then
  echo "fail: build pyc-opt and pycc before running this gate" >&2
  exit 1
fi

if [[ -n "${FILECHECK:-}" ]]; then
  FILECHECK_BIN="${FILECHECK}"
elif command -v FileCheck >/dev/null 2>&1; then
  FILECHECK_BIN="$(command -v FileCheck)"
else
  FILECHECK_BIN="$(ls -1v /usr/lib/llvm-*/bin/FileCheck /usr/lib64/llvm*/bin/FileCheck /usr/local/opt/llvm/bin/FileCheck /opt/homebrew/opt/llvm/bin/FileCheck /opt/llvm19/bin/FileCheck 2>/dev/null | tail -1)" || true
fi
if [[ -z "${FILECHECK_BIN}" || ! -x "${FILECHECK_BIN}" ]]; then
  echo "fail: FileCheck not found (install any llvm package or set FILECHECK)" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d /tmp/pyc-fork-merge.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

"${PYC_OPT}" "${TEST}/fork_merge_retime.mlir" \
  --pass-pipeline='builtin.module(func.func(pyc-fork-merge-retime))' \
  -o "${TMP_DIR}/merged.mlir"
"${FILECHECK_BIN}" "${TEST}/fork_merge_retime.mlir" \
  --input-file="${TMP_DIR}/merged.mlir"

"${PYCC}" "${TEST}/fork_merge_retime_model.mlir" --emit=cpp \
  -o "${TMP_DIR}/model.hpp"
"${CXX}" -std=c++17 -O2 -I"${ROOT}/runtime" \
  -DMODEL_HEADER="\"${TMP_DIR}/model.hpp\"" \
  "${TEST}/fork_merge_retime_model.cpp" \
  -o "${TMP_DIR}/model"
"${TMP_DIR}/model" >"${TMP_DIR}/model.out"
grep -q '^cycles=64 checksum=' "${TMP_DIR}/model.out"

echo "fork_merge_retime_smoke: PASS"
