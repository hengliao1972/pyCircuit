#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_DIR="${BACKEND_DIR}/vendor"
ORFS_COMMIT="2d29bdaf8deac950a317b16b547034829e3fbe0c"
BASE_URL="https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/${ORFS_COMMIT}/flow/platforms/nangate45"

LIB_NAME="NangateOpenCellLibrary_typical.lib"
LIB_SHA256="8d540a4d4cf6d09d27c87ad067857a9c0c2eeb023ab7a56e058cd3113db4e9b1"
LICENSE_SHA256="0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594"

command -v curl >/dev/null 2>&1 || {
  echo "error: curl is required" >&2
  exit 2
}
command -v sha256sum >/dev/null 2>&1 || {
  echo "error: sha256sum is required" >&2
  exit 2
}

mkdir -p "${VENDOR_DIR}"

fetch_checked() {
  local url="$1"
  local destination="$2"
  local expected="$3"

  if [[ -f "${destination}" ]] &&
     printf '%s  %s\n' "${expected}" "${destination}" | sha256sum --check --status; then
    echo "[nangate45-setup] up to date: ${destination}"
    return
  fi

  local temporary
  temporary="$(mktemp "${destination}.tmp.XXXXXX")"
  trap 'rm -f "${temporary}"' RETURN
  curl -fsSL "${url}" -o "${temporary}"
  printf '%s  %s\n' "${expected}" "${temporary}" | sha256sum --check --status || {
    echo "error: SHA-256 mismatch for ${url}" >&2
    exit 3
  }
  chmod 0644 "${temporary}"
  mv "${temporary}" "${destination}"
  trap - RETURN
  echo "[nangate45-setup] installed: ${destination}"
}

fetch_checked \
  "${BASE_URL}/lib/${LIB_NAME}" \
  "${VENDOR_DIR}/${LIB_NAME}" \
  "${LIB_SHA256}"
fetch_checked \
  "${BASE_URL}/LICENSE" \
  "${VENDOR_DIR}/LICENSE" \
  "${LICENSE_SHA256}"

echo "[nangate45-setup] ORFS commit: ${ORFS_COMMIT}"
