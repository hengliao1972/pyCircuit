#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/install/bin/pycc}"
OUT="${PYC_INSTANCE_VECTOR_CACHE_OUT:-${ROOT}/.pycircuit_out/gates/instance_vector_cache}"
INPUT="${ROOT}/compiler/mlir/test/Inputs/instance_vector_cache.mlir"

rm -rf "${OUT}"
mkdir -p "${OUT}"

"${PYCC}" "${INPUT}" --emit=cpp --logic-depth=256 \
  -o "${OUT}/instance_vector_cache.cpp"

python3 - "${OUT}/instance_vector_cache.cpp" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
matches = re.findall(
    r"std::array<std::uint64_t,\s*(\d+)>\s+\w+_eval_cache_words",
    text,
)
if matches != ["48"]:
    raise SystemExit(
        f"expected one 48-word packed vector cache, got {matches!r}"
    )
PY

"${CXX:-c++}" -std=c++17 -O0 \
  -I"${ROOT}/.pycircuit_out/toolchain/install/include" \
  -c "${OUT}/instance_vector_cache.cpp" \
  -o "${OUT}/instance_vector_cache.o"

echo "ok: nested vector instance cache uses 48 packed words"
