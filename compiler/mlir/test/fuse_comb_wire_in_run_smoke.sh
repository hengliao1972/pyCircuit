#!/usr/bin/env bash
# Gate: a run must not span a wire it drives, or the reader would see the
# pre-pass member value instead of what the run just computed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"

if [[ ! -x "${PYC_OPT}" ]]; then
  echo "fail: build pyc-opt before running this gate" >&2
  exit 1
fi

"${PYC_OPT}" "${ROOT}/compiler/mlir/test/Inputs/fuse_comb_wire_in_run.mlir" \
  --pyc-fuse-comb | python3 -c '
import sys
ir = sys.stdin.read()
if ir.count("pyc.comb(") != 2:
    raise SystemExit(f"fail: run was not cut at the wire read\n{ir}")
'

echo "fuse_comb_wire_in_run_smoke: PASS"
