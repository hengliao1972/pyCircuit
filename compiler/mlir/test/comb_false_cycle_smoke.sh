#!/usr/bin/env bash
# Gate for the block-order fallback: one eval() must settle a comb-level false
# cycle.  o = a & !!a... : with a=1 comb A runs before comb B drives its wire,
# so a single pass would leave o stale at 0.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"
OUT="${ROOT}/.pycircuit_out/gates/comb_false_cycle"

if [[ ! -x "${PYCC}" ]]; then
  echo "fail: pycc not built at ${PYCC}" >&2
  exit 1
fi

rm -rf "${OUT}"
mkdir -p "${OUT}"
"${PYCC}" "${ROOT}/compiler/mlir/test/Inputs/comb_false_cycle.mlir" \
  --emit=cpp --out-dir="${OUT}"

grep -q "failed to converge" "${OUT}/FalseCycle.cpp"

cat > "${OUT}/main.cpp" <<'EOF'
#include "FalseCycle.hpp"
#include <cstdio>
int main() {
  pyc::gen::FalseCycle dut;
  dut.a = pyc::cpp::Wire<1>({1});
  dut.eval();
  if (dut.o.toBool())
    return 0;
  std::printf("fail: stale output after one eval()\n");
  return 1;
}
EOF
g++ -std=c++20 -O1 -I "${OUT}" -I "${ROOT}/runtime" \
  "${OUT}/main.cpp" "${OUT}/FalseCycle.cpp" -o "${OUT}/run"
"${OUT}/run"

echo "comb_false_cycle_smoke: PASS"
