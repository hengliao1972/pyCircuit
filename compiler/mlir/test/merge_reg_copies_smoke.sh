#!/usr/bin/env bash
# Gate for register-copy merging: FileCheck on the rewrite, then a generated
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

TMP_DIR="$(mktemp -d /tmp/pyc-merge-reg-copies.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

"${PYC_OPT}" "${TEST}/merge_reg_copies.mlir" \
  --pass-pipeline='builtin.module(func.func(pyc-merge-reg-copies))' \
  -o "${TMP_DIR}/merged.mlir"
"${FILECHECK_BIN}" "${TEST}/merge_reg_copies.mlir" \
  --input-file="${TMP_DIR}/merged.mlir"

"${PYCC}" "${TEST}/merge_reg_copies_model.mlir" --emit=cpp \
  -o "${TMP_DIR}/model.hpp"
"${CXX}" -std=c++17 -O2 -I"${ROOT}/runtime" \
  -DMODEL_HEADER="\"${TMP_DIR}/model.hpp\"" \
  "${TEST}/merge_reg_copies_model.cpp" \
  -o "${TMP_DIR}/model"
"${TMP_DIR}/model" >"${TMP_DIR}/model.out"
grep -q '^cycles=64 checksum=' "${TMP_DIR}/model.out"

echo "merge_reg_copies_smoke: PASS"
