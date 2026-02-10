#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

usage() {
  cat <<EOF
Usage:
  $0 <konata.app|konata-darwin-dir|app.asar>

Patches Konata's Electron open-file dialog filter to allow selecting
Konata/Kanata log files with extensions:
  .kanata, .konata

This is needed because some Konata builds ship with a restrictive filter
that only shows: .txt/.log/.out/.gz.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 ]]; then
  usage
  exit 0
fi

IN="${1}"

ASAR=""
if [[ -f "${IN}" && "${IN##*.}" == "asar" ]]; then
  ASAR="${IN}"
elif [[ -d "${IN}" ]]; then
  # Accept either a konata.app bundle or an extracted release directory.
  if [[ -f "${IN}/Contents/Resources/app.asar" ]]; then
    ASAR="${IN}/Contents/Resources/app.asar"
  else
    ASAR="$(find "${IN}" -maxdepth 6 -path '*/konata.app/Contents/Resources/app.asar' -type f 2>/dev/null | head -n 1 || true)"
  fi
fi

if [[ -z "${ASAR}" || ! -f "${ASAR}" ]]; then
  echo "error: couldn't locate app.asar under: ${IN}" >&2
  exit 2
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "error: missing npx (Node.js) required to patch asar archive" >&2
  exit 2
fi

tmp="$(mktemp -d -t konata_asar_patch.XXXXXX)"
trap 'rm -rf "${tmp}"' EXIT

npx --yes asar extract "${ASAR}" "${tmp}" >/dev/null

 old_lit='extensions: ["txt", "text", "log", "out", "gz"]'
 new_lit='extensions: ["txt", "text", "log", "out", "gz", "kanata", "konata"]'

if rg -q -F "${new_lit}" "${tmp}"; then
  echo "ok: already patched ${ASAR}"
  exit 0
fi

# Patch any file that contains the known filter list.
patched=0
while IFS= read -r -d '' f; do
  PYTHONDONTWRITEBYTECODE=1 python3 - "${f}" <<'PY'
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8", errors="strict")
old = 'extensions: ["txt", "text", "log", "out", "gz"]'
new = 'extensions: ["txt", "text", "log", "out", "gz", "kanata", "konata"]'
if old not in s:
    raise SystemExit("pattern not found: " + str(p))
p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("patched:", p)
PY
  patched=$((patched + 1))
done < <(rg -l -0 -F "${old_lit}" "${tmp}" || true)

if [[ "${patched}" -eq 0 ]]; then
  echo "error: didn't find Konata open-dialog filter in extracted app.asar" >&2
  exit 2
fi

if [[ ! -f "${ASAR}.bak" ]]; then
  cp -f "${ASAR}" "${ASAR}.bak"
fi

npx --yes asar pack "${tmp}" "${ASAR}" >/dev/null
echo "ok: patched ${ASAR} (backup: ${ASAR}.bak)"
