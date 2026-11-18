#!/usr/bin/env bash
# Gate for fuse-comb barriers: a broadcast, an observed name or a state op must not split
# a run; the name moves to a leaf alias of the fused result.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"

if [[ ! -x "${PYC_OPT}" ]]; then
  echo "fail: build pyc-opt before running this gate" >&2
  exit 1
fi

"${PYC_OPT}" "${ROOT}/compiler/mlir/test/Inputs/fuse_comb_observe.mlir" \
  --pyc-fuse-comb | python3 -c '
import re, sys
ir = sys.stdin.read()
combs = ir.count("pyc.comb(")
body = re.search(r"pyc\.comb\(.*?\n    \}", ir, re.S).group(0)
if (combs != 1 or body.count("pyc.not") != 3 or "v_broadcast_dim" not in body
        or "pyc.alias" in body):
    raise SystemExit(f"fail: run did not fuse into one comb\n{ir}")
leaves = re.findall(r"pyc\.alias %0#\d+ \{pyc\.name = \"(\w+)\"\}", ir)
if sorted(leaves) != ["fwd", "mid"] or ir.count("pyc.alias") != 2:
    raise SystemExit(f"fail: names are not leaf aliases\n{ir}")
'

"${PYC_OPT}" "${ROOT}/compiler/mlir/test/Inputs/fuse_comb_hoist.mlir" \
  --pyc-fuse-comb | python3 -c '
import re, sys
ir = sys.stdin.read()
reg = re.search(r"= pyc\.reg %\w+, %\w+, (%\d+), (%\d+), (%\d+)", ir)
ok = (ir.count("pyc.comb(") == 1 and reg and ir.index("pyc.reg") < ir.index("pyc.comb(")
      and re.search(reg.group(2) + r" = pyc\.wire", ir)
      and all(re.search(reg.group(i) + r" = pyc\.constant", ir) for i in (1, 3))
      and "pyc.constant" in ir[ir.index("pyc.comb("):])
if not ok:
    raise SystemExit(f"fail: state op was not hoisted ahead of one comb\n{ir}")
'

echo "fuse_comb_observe_smoke: PASS"
