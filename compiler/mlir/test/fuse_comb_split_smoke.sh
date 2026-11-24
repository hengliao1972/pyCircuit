#!/usr/bin/env bash
# Gate: fuse-comb splits a run into combs by the inputs each op reads, and
# ports reach them through one forwarding comb instead of being polled by each.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"

if [[ ! -x "${PYC_OPT}" ]]; then
  echo "fail: build pyc-opt before running this gate" >&2
  exit 1
fi

"${PYC_OPT}" "${ROOT}/compiler/mlir/test/Inputs/fuse_comb_split.mlir" \
  --pyc-fuse-comb | python3 -c '
import re, sys
ir = sys.stdin.read()
bodies = re.findall(r"= pyc\.comb\(([^)]*)\).*?\n    \}", ir, re.S)
regs = re.findall(r"(%\d+) = pyc\.reg", ir)
latch = re.findall(r"(%\d+) = pyc\.comb\(%arg3\)", ir)
cones = [b for b in bodies if b != "%arg3"]
ok = (len(bodies) == 3 and len(latch) == 1
      and sorted(sorted(c.split(", ")) for c in cones) == sorted(sorted([r, latch[0]]) for r in regs))
if not ok:
    raise SystemExit(f"fail: cones were not split by their inputs\n{ir}")
'

echo "fuse_comb_split_smoke: PASS"
