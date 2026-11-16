#!/usr/bin/env bash
# Fused-comb dirty scheduling: inactivity, semantic publish filtering, direct
# fanout/reconvergence, first eval, post-commit, and removed-mode hard break.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"
CXX="${CXX:-c++}"
INPUT="${ROOT}/compiler/mlir/test/Inputs/comb_dirty_scheduler.mlir"
DRIVER="${ROOT}/compiler/mlir/test/Inputs/comb_dirty_scheduler_driver.cpp"
STATE_PY="${ROOT}/compiler/mlir/test/Inputs/comb_dirty_state.py"
STATE_DRIVER="${ROOT}/compiler/mlir/test/Inputs/comb_dirty_state_driver.cpp"
INSTANCE_PY="${ROOT}/compiler/mlir/test/Inputs/comb_dirty_instance.py"
INSTANCE_DRIVER="${ROOT}/compiler/mlir/test/Inputs/comb_dirty_instance_driver.cpp"
PRIMITIVES_PY="${ROOT}/compiler/mlir/test/Inputs/comb_dirty_primitives.py"
OUT="${ROOT}/.pycircuit_out/gates/comb_dirty_scheduler"

if [[ ! -x "${PYCC}" ]]; then
  echo "skip: pycc not built at ${PYCC}" >&2
  exit 0
fi

if "${PYCC}" --help 2>&1 | grep -q -- "--comb-update"; then
  echo "fail: pycc still exposes removed --comb-update" >&2
  exit 1
fi
export PYTHONPATH="${ROOT}/compiler/frontend:${PYTHONPATH:-}"
if python3 -m pycircuit.cli build --help 2>&1 |
    grep -q -- "--comb-update"; then
  echo "fail: pycircuit build still exposes removed --comb-update" >&2
  exit 1
fi

rm -rf "${OUT}"
mkdir -p "${OUT}"

common=(--logic-depth=256 --build-profile=dev-fast
        --inline-policy=off --hierarchy-policy=strict)
if "${PYCC}" "${INPUT}" --emit=none --comb-update=dirty -o /dev/null \
    >"${OUT}/removed-pycc-option.stdout" \
    2>"${OUT}/removed-pycc-option.stderr"; then
  echo "fail: pycc accepted removed --comb-update" >&2
  exit 1
fi
grep -q "Unknown command line argument '--comb-update=dirty'" \
  "${OUT}/removed-pycc-option.stderr"
if python3 -m pycircuit.cli build "${INSTANCE_PY}" \
    --comb-update=dirty --out-dir "${OUT}/removed-frontend-option" \
    >"${OUT}/removed-frontend-option.stdout" \
    2>"${OUT}/removed-frontend-option.stderr"; then
  echo "fail: pycircuit build accepted removed --comb-update" >&2
  exit 1
fi
grep -q 'unrecognized arguments: --comb-update=dirty' \
  "${OUT}/removed-frontend-option.stderr"

frontend_build="${OUT}/frontend-build"
python3 -m pycircuit.cli build \
  "${ROOT}/designs/examples/arith/tb_arith.py" \
  --out-dir "${frontend_build}" --target cpp --jobs 1 --profile=dev
python3 - "${frontend_build}/.build_cache.json" <<'PY'
import json
import sys

cache = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("build_flags", "cpp_build_flags"):
    if "comb_update" in cache.get(key, {}):
        raise SystemExit(f"fail: stale comb_update remains in {key}")
PY

main_dir="${OUT}/main"
mkdir -p "${main_dir}"
dirty_cpp="${main_dir}/comb_dirty_scheduler.cpp"
"${PYCC}" "${INPUT}" --emit=cpp -o "${dirty_cpp}" "${common[@]}"
"${CXX}" -std=c++17 -O0 \
  -I"${main_dir}" -I"${ROOT}/.pycircuit_out/toolchain/install/include" \
  "${DRIVER}" -o "${OUT}/run_dirty"
"${OUT}/run_dirty"

"${PYCC}" "${INPUT}" --emit=verilog --include-primitives=false \
  "${common[@]}" -o "${OUT}/comb_dirty_scheduler.v"

grep -q '_pyc_change_schedule_schema = "pyc.change_schedule.v1"' \
  "${dirty_cpp}"
grep -q '_pyc_change_schedule_node_count = 5u' "${dirty_cpp}"
grep -q '_pyc_change_schedule_rank{{0u, 1u, 1u, 2u, 0u}}' \
  "${dirty_cpp}"
grep -q '_pyc_change_schedule_slot{{0u, 1u, 2u, 3u, 4u}}' \
  "${dirty_cpp}"
grep -q 'DirtyBitset<' "${dirty_cpp}"
# Fanout wakes emit as (word, mask) pairs ORed through DirtyBitset::markWordUnchecked;
# comb_1 fans out to combs {1, 2} = word 0, mask 0x6.
grep -q 'std::array<std::pair<unsigned, std::uint64_t>, 1> _pyc_direct_fanout_.*{{0u, 0x6ull}}' \
  "${dirty_cpp}"
test "$(grep -c '_pyc_direct_fanout_' "${dirty_cpp}")" -eq 6

python3 -m pycircuit.cli emit "${STATE_PY}" -o "${OUT}/comb_dirty_state.pyc"
state_dir="${OUT}/state"
mkdir -p "${state_dir}"
"${PYCC}" "${OUT}/comb_dirty_state.pyc" --emit=cpp \
  -o "${state_dir}/comb_dirty_state.cpp" "${common[@]}"
state_cpp="${state_dir}/comb_dirty_state.cpp"
if grep -q '_pyc_comb_1_input_0' "${state_cpp}"; then
  echo "fail: dirty comb still polls register Q instead of commit fanout" >&2
  exit 1
fi
# State-optimize shifts temp numbering, so match the pattern, not reg_4.
grep -qE '_pyc_commit_changed_pyc_reg_[0-9]+_inst' "${state_cpp}"
grep -qE 'if \(_pyc_commit_changed_pyc_reg_[0-9]+_inst\)' "${state_cpp}"
grep -q '_pyc_comb_dirty.markWordUnchecked' "${state_cpp}"
"${CXX}" -std=c++17 -O0 \
  -I"${state_dir}" -I"${ROOT}/.pycircuit_out/toolchain/install/include" \
  "${STATE_DRIVER}" \
  -o "${OUT}/run_state"
"${OUT}/run_state"

python3 -m pycircuit.cli emit "${INSTANCE_PY}" \
  -o "${OUT}/comb_dirty_instance.pyc"
instance_dir="${OUT}/instance"
mkdir -p "${instance_dir}"
"${PYCC}" "${OUT}/comb_dirty_instance.pyc" --emit=cpp \
  -o "${instance_dir}/comb_dirty_instance.cpp" "${common[@]}"
instance_cpp="${instance_dir}/comb_dirty_instance.cpp"
grep -q '_pyc_direct_fanout_' "${instance_cpp}"
grep -q '_pyc_comb_dirty.markWordUnchecked' "${instance_cpp}"
"${CXX}" -std=c++17 -O0 \
  -I"${instance_dir}" -I"${ROOT}/.pycircuit_out/toolchain/install/include" \
  "${INSTANCE_DRIVER}" -o "${OUT}/run_instance"
"${OUT}/run_instance"

python3 -m pycircuit.cli emit "${PRIMITIVES_PY}" \
  -o "${OUT}/comb_dirty_primitives.pyc"
primitive_dir="${OUT}/primitives"
mkdir -p "${primitive_dir}"
"${PYCC}" "${OUT}/comb_dirty_primitives.pyc" --emit=cpp \
  -o "${primitive_dir}/comb_dirty_primitives.cpp" "${common[@]}"
primitive_cpp="${primitive_dir}/comb_dirty_primitives.cpp"
for required in \
  'DirtyBitset<' \
  '_pyc_direct_fanout_' \
  'kInReadyChanged' \
  'kOutValidChanged' \
  'kOutDataChanged' \
  'kReadData0Changed' \
  'kReadData1Changed' \
  '_pyc_commit_changed_' \
  '_pyc_comb_dirty.markWordUnchecked'; do
  if ! grep -q "${required}" "${primitive_cpp}"; then
    echo "fail: primitive dirty source missing ${required}" >&2
    exit 1
  fi
done
cat >"${primitive_dir}/driver.cpp" <<'CPP'
#include "comb_dirty_primitives.cpp"

int main() {
  pyc::gen::comb_dirty_primitives dut;
  dut.clk = pyc::cpp::Wire<1>(0);
  dut.rst = pyc::cpp::Wire<1>(0);
  dut.data = pyc::cpp::Wire<8>(7);
  dut.comb();
  if (dut.reg_data.word(0) != 1)
    return 1;
  dut.clk = pyc::cpp::Wire<1>(1);
  dut.comb();
  dut.tick();
  dut.commit();
  dut.comb();
  return dut.reg_data.word(0) == 8 ? 0 : 2;
}
CPP
"${CXX}" -std=c++17 -O0 \
  -I"${primitive_dir}" -I"${ROOT}/.pycircuit_out/toolchain/install/include" \
  "${primitive_dir}/driver.cpp" -o "${OUT}/run_primitives"
"${OUT}/run_primitives"

if grep -R -E '_pyc_sim_stats|PYC_SIM_STATS|PYC_SIM_TIMING|dump_sim_stats' \
    "${OUT}" --include='*.cpp'; then
  echo "fail: generated C++ still contains simulator stats" >&2
  exit 1
fi

echo "ok: fused-comb dirty scheduler smoke"
