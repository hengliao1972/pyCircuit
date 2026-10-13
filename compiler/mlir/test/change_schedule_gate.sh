#!/usr/bin/env bash
# Gate for the phase-one canonical graph and change-driven schedule contract.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"
INPUT="${ROOT}/compiler/mlir/test/Inputs/change_schedule_chain.mlir"
OUT="${ROOT}/.pycircuit_out/gates/change_schedule"

if [[ ! -x "${PYCC}" ]]; then
  echo "fail: pycc not built at ${PYCC}" >&2
  exit 1
fi

rm -rf "${OUT}"
mkdir -p "${OUT}/first" "${OUT}/second"

"${PYCC}" \
  "${ROOT}/compiler/mlir/test/Inputs/comb_cycle_reg_feedback.mlir" \
  --emit=none -o /dev/null

true_cycle_log="${OUT}/comb_cycle_true.log"
if "${PYCC}" "${ROOT}/compiler/mlir/test/Inputs/comb_cycle_true.mlir" \
    --emit=none -o /dev/null >"${true_cycle_log}" 2>&1; then
  echo "fail: true local combinational cycle was accepted" >&2
  exit 1
fi
grep -q "combinational cycle detected" "${true_cycle_log}"

"${PYCC}" \
  "${ROOT}/compiler/mlir/test/Inputs/comb_cycle_cross_instance_false_scc.mlir" \
  --emit=none -o /dev/null

cross_cycle_log="${OUT}/comb_cycle_cross_instance_true.log"
if "${PYCC}" \
    "${ROOT}/compiler/mlir/test/Inputs/comb_cycle_cross_instance_true.mlir" \
    --emit=none -o /dev/null >"${cross_cycle_log}" 2>&1; then
  echo "fail: true cross-instance combinational cycle was accepted" >&2
  exit 1
fi
grep -q "combinational cycle detected" "${cross_cycle_log}"
grep -q "a_input" "${cross_cycle_log}"
grep -q "b_input" "${cross_cycle_log}"

"${PYCC}" \
  "${ROOT}/compiler/mlir/test/Inputs/change_schedule_missing_summary.mlir" \
  --emit=none -o /dev/null

run_schedule() {
  local dump_dir=$1
  "${PYCC}" "${INPUT}" --emit=none -o /dev/null \
    --dump-pass-ir="${dump_dir}" --dump-pass-ir-phase=after \
    --dump-pass-ir-filter='change-driven-schedule' >/dev/null
}

run_schedule "${OUT}/first"
run_schedule "${OUT}/second"

first_dump=("${OUT}/first"/*_after_*change-driven-schedule*.mlir)
second_dump=("${OUT}/second"/*_after_*change-driven-schedule*.mlir)
if [[ ${#first_dump[@]} -ne 1 || ${#second_dump[@]} -ne 1 ]]; then
  echo "fail: expected one schedule dump per run" >&2
  exit 1
fi
cmp "${first_dump[0]}" "${second_dump[0]}"

python3 - "${first_dump[0]}" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
summaries = re.findall(r"pyc\.change_schedule\.summary = \{([^}]+)\}", text)
if len(summaries) != 2:
    raise SystemExit(f"fail: expected 2 function summaries, got {len(summaries)}")
if 'node_count = 1 : i64' not in summaries[0]:
    raise SystemExit("fail: child summary node count is not 1")
if 'node_count = 3 : i64' not in summaries[1]:
    raise SystemExit("fail: top summary node count is not 3")
if 'edge_count = 2 : i64' not in summaries[1]:
    raise SystemExit("fail: top summary edge count is not 2")
if 'rank_count = 3 : i64' not in summaries[1]:
    raise SystemExit("fail: top summary rank count is not 3")

instance = next(
    (line for line in text.splitlines() if "pyc.instance" in line), None
)
if instance is None:
    raise SystemExit("fail: planned IR has no instance")
for expected in (
    "pyc.change_schedule.node = [1]",
    "pyc.change_schedule.rank = [1]",
    "pyc.change_schedule.slot = [1]",
    "pyc.change_schedule.fanout = [[2]]",
):
    if expected not in instance:
        raise SystemExit(f"fail: instance missing {expected!r}")

node_attrs = re.findall(r"pyc\.change_schedule\.node = \[[^\]]+\]", text)
if len(node_attrs) != 4:
    raise SystemExit(f"fail: expected 4 scheduled nodes, got {len(node_attrs)}")
print("ok: canonical schedule metadata is stable and verified")
PY

expect_pycc_failure() {
  local input=$1
  local diagnostic=$2
  local log="${OUT}/$(basename "${input}").log"
  if "${PYCC}" "${input}" --emit=none -o /dev/null >"${log}" 2>&1; then
    echo "fail: expected ${input} to be rejected" >&2
    exit 1
  fi
  if ! grep -q "${diagnostic}" "${log}"; then
    echo "fail: ${input} did not report '${diagnostic}'" >&2
    cat "${log}" >&2
    exit 1
  fi
}

expect_pycc_failure \
  "${ROOT}/compiler/mlir/test/Inputs/change_schedule_unsupported.mlir" \
  "no registered canonical per-result combinational dependency transfer"

echo "ok: change-driven schedule gate passed"
