#!/usr/bin/env bash
# Gate wrapper for optional --cpp-pch smoke.
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

run_id="${PYC_GATE_RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
docs_dir="${PYC_ROOT_DIR}/docs/gates/logs/${run_id}"
mkdir -p "${docs_dir}"

build_command="bash flows/scripts/pyc build"
if [[ "${PYC_GATE_SKIP_BUILD:-0}" == "1" ]]; then
  build_command="# build skipped; use prebuilt PYCC"
fi
cat >"${docs_dir}/commands.txt" <<EOF
${build_command}
bash compiler/mlir/test/cpp_device_pch_smoke.sh
EOF

pyc_log "gate run-id=${run_id}"
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
            "cpp_device_pch_smoke": {"status": status_smoke},
        },
        "decisions": ["0141"],
        "feature": "cpp-pch",
        "note": "C++ compile-layer acceleration for incremental builds",
    },
    open(out, "w", encoding="utf-8"),
    indent=2,
)
PY
}

on_exit() {
  local rc=$?
  if [[ "${status_build}" == "pending" && "${rc}" -ne 0 ]]; then
    status_build="fail"
  fi
  if [[ "${status_smoke}" == "pending" && "${rc}" -ne 0 ]]; then
    status_smoke="fail"
  fi
  write_summary || true
  exit "${rc}"
}
trap on_exit EXIT

if [[ "${PYC_GATE_SKIP_BUILD:-0}" == "1" ]]; then
  status_build="skipped"
  : >"${docs_dir}/pyc_build.stdout"
  echo "build skipped; using prebuilt PYCC=${PYCC:-<auto>}" >"${docs_dir}/pyc_build.stderr"
else
  if bash "${PYC_ROOT_DIR}/flows/scripts/pyc" build \
    >"${docs_dir}/pyc_build.stdout" 2>"${docs_dir}/pyc_build.stderr"; then
    status_build="pass"
  else
    status_build="fail"
    exit 1
  fi
fi

pyc_find_pycc
if PYCC="${PYCC}" bash "${PYC_ROOT_DIR}/compiler/mlir/test/cpp_device_pch_smoke.sh" \
  >"${docs_dir}/cpp_device_pch_smoke.stdout" \
  2>"${docs_dir}/cpp_device_pch_smoke.stderr"; then
  status_smoke="pass"
else
  status_smoke="fail"
  exit 1
fi
