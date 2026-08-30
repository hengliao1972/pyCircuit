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

bash "${PYC_ROOT_DIR}/flows/scripts/pyc" build \
  >"${docs_dir}/pyc_build.stdout" 2>"${docs_dir}/pyc_build.stderr"

PYCC="$(pyc_find_pycc)" bash "${PYC_ROOT_DIR}/compiler/mlir/test/pass_ir_dumper_smoke.sh" \
  >"${docs_dir}/pass_ir_dumper_smoke.stdout" \
  2>"${docs_dir}/pass_ir_dumper_smoke.stderr"

# This is a diagnostics/tooling feature (no semantic change), so it does not
# map to any pyc4.0 decision ID; "decisions" is intentionally empty.
python3 - <<'PY' "${docs_dir}/summary.json" "${run_id}"
import json
import sys

out, run_id = sys.argv[1], sys.argv[2]
json.dump(
    {
        "run_id": run_id,
        "gates": {
            "pyc_build": {"status": "pass"},
            "pass_ir_dumper_smoke": {"status": "pass"},
        },
        "decisions": [],
        "feature": "mlir-pass-ir-dump",
        "note": "tooling/diagnostics; no decision ID (per design doc)",
    },
    open(out, "w", encoding="utf-8"),
    indent=2,
)
PY

pyc_log "ok: wrote ${docs_dir}/summary.json"
