#!/usr/bin/env bash
# Gate for pyc-eliminate-wires: single-driver wires, combinational readers after
# the wire driver, wires declared first and driven last.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"

if [[ ! -x "${PYC_OPT}" ]]; then
  echo "fail: build pyc-opt before running this gate" >&2
  exit 1
fi

"${PYC_OPT}" "${ROOT}/compiler/mlir/test/eliminate_wires_multi_driver.mlir" \
  --pyc-eliminate-wires --verify-diagnostics -o /dev/null

check_layout() {
  "${PYC_OPT}" "${ROOT}/compiler/mlir/test/Inputs/$1.mlir" \
    --pyc-eliminate-wires | python3 -c '
import re, sys
ops = [m.group(1) for line in sys.stdin
       if (m := re.match(r"\s*(?:%\S+ = )?(pyc\.\w+|return)\b", line))]
if ops != sys.argv[1].split():
    raise SystemExit(f"fail: unexpected wire/assign layout {ops}")
' "$2"
}

check_layout eliminate_wires_layout \
  "pyc.wire pyc.constant pyc.constant pyc.reg pyc.not pyc.not pyc.not pyc.assign return"
check_layout eliminate_wires_pinned_reg \
  "pyc.wire pyc.constant pyc.constant pyc.reg pyc.not pyc.not pyc.assign return"

echo "eliminate_wires_layout_smoke: PASS"
