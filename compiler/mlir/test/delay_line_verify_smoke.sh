#!/usr/bin/env bash
# Gate for the pyc.delay_line / pyc.delay_tap verifiers (expected-error fixtures).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"

if [[ ! -x "${PYC_OPT}" ]]; then
  echo "fail: build pyc-opt before running this gate" >&2
  exit 1
fi

# The delay-line fixture combines a valid module with a rejected depth; the two
# tap fixtures each carry one expected-error directive.
for verify_case in delay_line_verify delay_tap_verify_invalid \
                   delay_tap_verify_invalid_source; do
  "${PYC_OPT}" "${ROOT}/compiler/mlir/test/${verify_case}.mlir" \
    --verify-diagnostics -o /dev/null
done

echo "delay_line_verify_smoke: PASS"
