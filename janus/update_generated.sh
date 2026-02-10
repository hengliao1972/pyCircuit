#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

OUT_ROOT="${ROOT_DIR}/janus/generated"
mkdir -p "${OUT_ROOT}"

emit_one() {
  local name="$1"
  local src="$2"
  local outdir="${OUT_ROOT}/${name}"

  mkdir -p "${outdir}"
  pyc_log "emit ${name}: ${src}"

  local tmp_pyc
  tmp_pyc="$(mktemp -t "pycircuit.janus.${name}.pyc")"

  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(pyc_pythonpath):${ROOT_DIR}/janus/pyc" \
    python3 -m pycircuit.cli emit "${src}" -o "${tmp_pyc}"

  "${PYC_COMPILE}" "${tmp_pyc}" --emit=verilog -o "${outdir}/${name}.v"
  "${PYC_COMPILE}" "${tmp_pyc}" --emit=cpp -o "${outdir}/${name}.hpp"
}

emit_one janus_bcc_pyc "${ROOT_DIR}/janus/pyc/janus/bcc/janus_bcc_pyc.py"
emit_one janus_bcc_ooo_pyc "${ROOT_DIR}/janus/pyc/janus/bcc/janus_bcc_ooo_pyc.py"
emit_one janus_top_pyc "${ROOT_DIR}/janus/pyc/janus/top.py"

mv -f "${OUT_ROOT}/janus_bcc_pyc/janus_bcc_pyc.hpp" "${OUT_ROOT}/janus_bcc_pyc/janus_bcc_pyc_gen.hpp"
mv -f "${OUT_ROOT}/janus_bcc_ooo_pyc/janus_bcc_ooo_pyc.hpp" "${OUT_ROOT}/janus_bcc_ooo_pyc/janus_bcc_ooo_pyc_gen.hpp"
mv -f "${OUT_ROOT}/janus_top_pyc/janus_top_pyc.hpp" "${OUT_ROOT}/janus_top_pyc/janus_top_pyc_gen.hpp"

pyc_log "ok: wrote outputs under ${OUT_ROOT}"
