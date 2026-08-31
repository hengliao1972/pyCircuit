#!/usr/bin/env bash
# Gate wrapper for the per-pass IR dump smoke test.
# Runs the smoke script under the toolchain pycc and writes evidence to
# docs/gates/logs/<run-id>/, matching the cpp_*_gate.sh convention.
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

run_id="${PYC_GATE_RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
docs_dir="${PYC_ROOT_DIR}/docs/gates/logs/${run_id}"
mkdir -p "${docs_dir}"

cat >"${docs_dir}/commands.txt" <<EOF
bash flows/scripts/pyc build
bash compiler/mlir/test/pass_ir_dumper_smoke.sh
EOF

pyc_log "gate run-id=${run_id}"
pyc_log "docs evidence: ${docs_dir}"

# Track per-step status and always emit summary.json (including on failure).
status_build="pending"
status_smoke="pending"

write_summary() {
  python3 - <<'PY' "${docs_dir}/summary.json" "${run_id}" "${status_build}" "${status_smoke}"
import json
import sys

out, run_id, status_build, status_smoke = sys.argv[1:5]
json.dump(
    {
        "run_id": run_id,
        "gates": {
            "pyc_build": {"status": status_build},
            "pass_ir_dumper_smoke": {"status": status_smoke},
        },
        "decisions": [],
        "feature": "mlir-pass-ir-dump",
        "note": "tooling/diagnostics; no decision ID (per design doc)",
    },
    open(out, "w", encoding="utf-8"),
    indent=2,
)
print(f"wrote {out}")
PY
}

on_exit() {
  local rc=$?
  # Map anything still pending (interrupted before start) to fail if we exit non-zero.
  if [[ "${status_build}" == "pending" && "${rc}" -ne 0 ]]; then
    status_build="fail"
  fi
  if [[ "${status_smoke}" == "pending" && "${rc}" -ne 0 ]]; then
    status_smoke="fail"
  fi
  write_summary || true
  if [[ "${rc}" -eq 0 ]]; then
    pyc_log "ok: wrote ${docs_dir}/summary.json"
  else
    pyc_log "fail: wrote ${docs_dir}/summary.json (exit=${rc})"
  fi
  exit "${rc}"
}
trap on_exit EXIT

if bash "${PYC_ROOT_DIR}/flows/scripts/pyc" build \
  >"${docs_dir}/pyc_build.stdout" 2>"${docs_dir}/pyc_build.stderr"; then
  status_build="pass"
else
  status_build="fail"
  exit 1
fi

if PYCC="$(pyc_find_pycc)" bash "${PYC_ROOT_DIR}/compiler/mlir/test/pass_ir_dumper_smoke.sh" \
  >"${docs_dir}/pass_ir_dumper_smoke.stdout" \
  2>"${docs_dir}/pass_ir_dumper_smoke.stderr"; then
  status_smoke="pass"
else
  status_smoke="fail"
  exit 1
fi
