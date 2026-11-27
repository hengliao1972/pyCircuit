#!/usr/bin/env bash
# Convergence gates for pyc-comb-canonicalize:
# 1) deep same-select mux nest (pattern-driven collapse),
# 2) serial trunc/zext folding chain (folder-driven collapse, propagating
#    through USES -- each level folds only after the deeper level folded).
# Both must collapse fully in a single pass regardless of chain depth.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"
PIPE='builtin.module(func.func(pyc-comb-canonicalize))'

if [[ ! -x "${PYC_OPT}" ]]; then
  echo "skip: pyc-opt not built at ${PYC_OPT}" >&2
  exit 0
fi

run_gate() {
  local input="$1" expect_mux="$2"
  local out="${ROOT}/.pycircuit_out/gates/comb_canonicalize/out_$(basename "${input}")"
  mkdir -p "$(dirname "${out}")"
  "${PYC_OPT}" --pass-pipeline="${PIPE}" "${input}" -o "${out}"

  if grep -qE 'pyc.(trunc|zext)' "${out}"; then
    echo "fail: $(basename "${input}") still has trunc/zext ops" >&2
    exit 1
  fi
  local n_mux
  n_mux=$(grep -c "pyc.mux" "${out}" || true)
  if [[ "${n_mux}" -ne "${expect_mux}" ]]; then
    echo "fail: $(basename "${input}") expected ${expect_mux} mux(es), got ${n_mux}" >&2
    exit 1
  fi
}

run_gate "${ROOT}/compiler/mlir/test/comb_canonicalize_deep_chain.mlir" 1
run_gate "${ROOT}/compiler/mlir/test/comb_canonicalize_fold_chain.mlir" 1
echo "ok: both chain fixtures collapsed"
