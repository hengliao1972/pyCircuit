#!/usr/bin/env bash
set -euo pipefail

PYC_ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

pyc_log() {
  echo "[pyc] $*"
}

pyc_warn() {
  echo "[pyc][warn] $*" >&2
}

pyc_die() {
  echo "[pyc][error] $*" >&2
  exit 1
}

pyc_find_pyc_compile() {
  if [[ -n "${PYC_COMPILE:-}" && -x "${PYC_COMPILE}" ]]; then
    return 0
  fi

  local candidates=(
    "${PYC_ROOT_DIR}/build/bin/pyc-compile"
    "${PYC_ROOT_DIR}/pyc/mlir/build/bin/pyc-compile"
    "${PYC_ROOT_DIR}/build-top/bin/pyc-compile"
  )

  # Pick the newest executable among the common build locations. This avoids
  # accidentally grabbing an older `pyc-compile` from a stale build directory.
  local best=""
  local best_mtime=0
  for c in "${candidates[@]}"; do
    if [[ -x "${c}" ]]; then
      local mtime=0
      if mtime="$(stat -f %m "${c}" 2>/dev/null)"; then
        :
      elif mtime="$(stat -c %Y "${c}" 2>/dev/null)"; then
        :
      else
        mtime=0
      fi
      if (( mtime > best_mtime )); then
        best="${c}"
        best_mtime="${mtime}"
      fi
    fi
  done
  if [[ -n "${best}" ]]; then
    export PYC_COMPILE="${best}"
    return 0
  fi

  if command -v pyc-compile >/dev/null 2>&1; then
    export PYC_COMPILE
    PYC_COMPILE="$(command -v pyc-compile)"
    return 0
  fi

  pyc_die "missing pyc-compile (set PYC_COMPILE=... or build it with: scripts/pyc build)"
}

pyc_pythonpath() {
  # Prefer editable install (`pip install -e .`), but fall back to PYTHONPATH for
  # repo-local runs.
  echo "${PYC_ROOT_DIR}/python"
}
