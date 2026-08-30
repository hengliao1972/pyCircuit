#!/usr/bin/env bash
# Gate: per-pass IR dump instrumentation (`pycc --dump-pass-ir*`).
#
# Verifies:
#   - flags are registered
#   - default behavior produces no dump
#   - dump directory is populated with before/after pairs in lexical order
#   - filter regex narrows output
#   - phase=after halves the file count
#   - max-lines truncates with a marker
#   - coexists with --profile-pass-timing / --profile-json
#   - before/after pairs are diffable (the pass actually changed the IR)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"
EXAMPLE="${ROOT}/designs/examples/counter/counter.py"
OUT="${ROOT}/.pycircuit_out/gates/pass_ir_dumper_smoke"

if [[ ! -x "${PYCC}" ]]; then
  echo "skip: pycc not built at ${PYCC}" >&2
  exit 0
fi

for flag in dump-pass-ir dump-pass-ir-phase dump-pass-ir-filter dump-pass-ir-max-lines; do
  if ! "${PYCC}" --help 2>&1 | rg -q -- "--${flag}"; then
    echo "fail: pycc missing --${flag} flag" >&2
    exit 1
  fi
done

if [[ ! -f "${EXAMPLE}" ]]; then
  echo "skip: example not found: ${EXAMPLE}" >&2
  exit 0
fi

rm -rf "${OUT}"
mkdir -p "${OUT}"

# Emit a small .pyc so we don't depend on the frontend staying quiet on stderr.
export PYTHONPATH="${ROOT}/compiler/frontend:${PYTHONPATH:-}"
python3 - <<'PY' "${EXAMPLE}" "${OUT}/counter.pyc"
import importlib.util
import sys
from pathlib import Path

example, out = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("pyc_dump_smoke_example", example)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
from pycircuit import compile_cycle_aware

circuit = compile_cycle_aware(
    mod.build, name="counter", eager=True, width=8, hierarchical=True
)
mlir = circuit._v5_design.emit_module_mlir_map()["counter"]
Path(out).write_text(mlir, encoding="utf-8")
PY

INPUT="${OUT}/counter.pyc"

# --- AC5: default behavior produces no dump ---
"${PYCC}" "${INPUT}" --emit=none -o /dev/null >/dev/null 2>&1
if [[ -d "${OUT}/default_dump" ]]; then
  echo "fail: dump created without --dump-pass-ir" >&2
  exit 1
fi

# --- AC1/AC2: dump dir populated with before/after pairs ---
DUMP_BOTH="${OUT}/dump_both"
"${PYCC}" "${INPUT}" --emit=none -o /dev/null --dump-pass-ir="${DUMP_BOTH}" >/dev/null 2>&1
if [[ ! -d "${DUMP_BOTH}" ]]; then
  echo "fail: dump dir not created at ${DUMP_BOTH}" >&2
  exit 1
fi
shopt -s nullglob
before_files=("${DUMP_BOTH}"/*_before_*.mlir)
after_files=("${DUMP_BOTH}"/*_after_*.mlir)
shopt -u nullglob
n_before=${#before_files[@]}
n_after=${#after_files[@]}
if [[ "${n_before}" -eq 0 ]]; then
  echo "fail: no before files emitted" >&2
  exit 1
fi
if [[ "${n_before}" -ne "${n_after}" ]]; then
  echo "fail: before/after not paired (before=${n_before}, after=${n_after})" >&2
  exit 1
fi

# --- AC2 (lexical order = execution order): filenames strictly increase ---
python3 - <<'PY' "${DUMP_BOTH}"
import sys, pathlib
names = sorted(p.name for p in pathlib.Path(sys.argv[1]).glob("*.mlir"))
seqs = [int(n[:4]) for n in names]
if seqs != list(range(len(seqs))):
    raise SystemExit(f"fail: sequence numbers not contiguous: {seqs[:10]}...")
print(f"ok: {len(names)} files in lexical order")
PY

# --- AC3: filter regex ---
DUMP_FILT="${OUT}/dump_filter"
"${PYCC}" "${INPUT}" --emit=none -o /dev/null \
  --dump-pass-ir="${DUMP_FILT}" --dump-pass-ir-filter='eliminate-wires' >/dev/null 2>&1
shopt -s nullglob
filt_files=("${DUMP_FILT}"/*.mlir)
shopt -u nullglob
n_filt=${#filt_files[@]}
if [[ "${n_filt}" -eq 0 ]]; then
  echo "fail: filter produced no files (eliminate-wires should run)" >&2
  exit 1
fi
# Every file must mention eliminate-wires (otherwise the filter leaked).
for f in "${filt_files[@]}"; do
  if [[ "${f}" != *eliminate-wires* ]]; then
    echo "fail: filter leaked non-matching pass file: ${f##*/}" >&2
    exit 1
  fi
done

# --- AC3: phase=after halves file count ---
DUMP_AFTER="${OUT}/dump_after"
"${PYCC}" "${INPUT}" --emit=none -o /dev/null \
  --dump-pass-ir="${DUMP_AFTER}" --dump-pass-ir-phase=after >/dev/null 2>&1
shopt -s nullglob
after_files=("${DUMP_AFTER}"/*.mlir)
shopt -u nullglob
n_after_only=${#after_files[@]}
if [[ "${n_after_only}" -ne "${n_after}" ]]; then
  echo "fail: phase=after count ${n_after_only} != full after count ${n_after}" >&2
  exit 1
fi
for f in "${after_files[@]}"; do
  if [[ "${f}" == *_before_* ]]; then
    echo "fail: phase=after still produced before file: ${f##*/}" >&2
    exit 1
  fi
done

# --- AC6: max-lines truncates ---
DUMP_ML="${OUT}/dump_maxlines"
"${PYCC}" "${INPUT}" --emit=none -o /dev/null \
  --dump-pass-ir="${DUMP_ML}" --dump-pass-ir-max-lines=3 >/dev/null 2>&1
shopt -s nullglob
ml_files=("${DUMP_ML}"/*.mlir)
shopt -u nullglob
trunc_found=0
for f in "${ml_files[@]}"; do
  if rg -q '^// truncated at 3 lines' "$f"; then trunc_found=1; break; fi
done
if [[ "${trunc_found}" -eq 0 ]]; then
  echo "fail: no file carried the truncation marker" >&2
  exit 1
fi

# --- AC: at least one pass actually changed the IR (before/after diffable) ---
# We do NOT hard-code a specific pass name here: pipeline evolution (new
# upstream passes, frontend IR improvements) can turn any given pass into a
# no-op on a small example. The invariant we actually care about is that the
# dumper captures real before/after diffs, so scan all pairs in the full
# dump and require at least one to differ.
shopt -s nullglob
all_before=("${DUMP_BOTH}"/*_before_*.mlir)
shopt -u nullglob
diff_found=0
sample_pass=""
for b in "${all_before[@]}"; do
  # Pair a `NNNN_before_<NN>_<pass>...` with the matching
  # `MMMM_after_<NN>_<pass>...` (same <NN>, same pass suffix).
  base=$(basename "$b")
  nn_pass=${base#*_before_}      # strip "<seq>_before_"
  a=$(ls "${DUMP_BOTH}"/*_after_"${nn_pass}" 2>/dev/null | head -1)
  [[ -z "$a" ]] && continue
  if ! diff -q "$b" "$a" >/dev/null 2>&1; then
    diff_found=1
    sample_pass=${nn_pass#*_}   # strip "<NN>_" -> pass short name (+ level)
    sample_pass=${sample_pass%%__*}
    break
  fi
done
if [[ "${diff_found}" -eq 0 ]]; then
  echo "fail: no pass produced a before/after diff (pipeline had no effect?)" >&2
  exit 1
fi
echo "ok: at least one pass has a diffable before/after (e.g. ${sample_pass})"

# --- AC: coexists with --profile-pass-timing / --profile-json ---
DUMP_PROF="${OUT}/dump_with_profile"
"${PYCC}" "${INPUT}" --emit=none -o /dev/null \
  --dump-pass-ir="${DUMP_PROF}" \
  --profile-pass-timing --profile-json="${DUMP_PROF}/profile.json" >/dev/null 2>&1
if [[ ! -f "${DUMP_PROF}/profile.json" ]]; then
  echo "fail: profile.json not produced alongside dump" >&2
  exit 1
fi
python3 - <<'PY' "${DUMP_PROF}/profile.json"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
if not data.get("passes"):
    raise SystemExit("fail: profile.json has no passes[] (timing collector not run)")
print("ok: profile.json carries pass timing")
PY

echo "ok: per-pass IR dump smoke passed"
