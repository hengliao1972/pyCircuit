#!/usr/bin/env bash
# Compare C++ emit size and g++ compile time: this tree vs an upstream/main worktree.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASELINE_ROOT=""
DESIGNS="counter,issue_queue_2picker"
REPEAT=3
OUT_DIR="${ROOT}/.pycircuit_out/perf/localize_vs_main"

usage() {
  cat <<EOF
Usage: $0 --baseline-root <upstream/main worktree> [--designs a,b] [--repeat N] [--out DIR]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --baseline-root) BASELINE_ROOT="${2:?}"; shift 2 ;;
    --designs) DESIGNS="${2:?}"; shift 2 ;;
    --repeat) REPEAT="${2:?}"; shift 2 ;;
    --out) OUT_DIR="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${BASELINE_ROOT}" || ! -d "${BASELINE_ROOT}" ]]; then
  echo "error: --baseline-root must be an existing worktree of upstream/main" >&2
  exit 2
fi
if ! [[ "${REPEAT}" =~ ^[1-9][0-9]*$ ]]; then
  echo "error: --repeat must be a positive integer" >&2
  exit 2
fi

find_pycc() {
  local root="$1"
  if [[ -x "${root}/.pycircuit_out/toolchain/install/bin/pycc" ]]; then
    echo "${root}/.pycircuit_out/toolchain/install/bin/pycc"
    return
  fi
  if [[ -x "${root}/.pycircuit_out/toolchain/build/bin/pycc" ]]; then
    echo "${root}/.pycircuit_out/toolchain/build/bin/pycc"
    return
  fi
  return 1
}

emit_one() {
  local label="$1" pycc="$2" frontend="$3" name="$4" py_file="$5" extra_args="$6"
  local dest="${OUT_DIR}/${label}/${name}${extra_args:+_${extra_args//--/_}}"
  rm -rf "${dest}"
  mkdir -p "${dest}"
  local pyc="${dest}/${name}.pyc"
  PYTHONPATH="${frontend}:${PYTHONPATH:-}" python3 - <<'PY' "${py_file}" "${pyc}" "${name}"
import importlib.util
import sys
from pathlib import Path

example, out, name = sys.argv[1], sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location("pyc_bench_example", example)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
from pycircuit import compile_cycle_aware

kwargs = {"name": name, "eager": True, "hierarchical": True}
if name == "counter":
    kwargs["width"] = 8
circuit = compile_cycle_aware(mod.build, **kwargs)
mlir_map = circuit._v5_design.emit_module_mlir_map()
# Prefer the named module; otherwise take the only entry.
text = mlir_map.get(name) or next(iter(mlir_map.values()))
Path(out).write_text(text, encoding="utf-8")
PY
  "${pycc}" "${pyc}" --emit=cpp --out-dir "${dest}/cpp" --cpp-split=module \
    --build-profile=dev-fast ${extra_args} >/dev/null
  local hpp_lines=0
  local all_hpp="${dest}/hpp_lines.txt"
  : > "${all_hpp}"
  while IFS= read -r f; do
    wc -l < "${f}" | awk -v p="${f}" '{print $1 "\t" p}' >> "${all_hpp}"
    hpp_lines=$((hpp_lines + $(wc -l < "${f}")))
  done < <(find "${dest}/cpp" -name '*.hpp' -print | sort)
  echo "${hpp_lines}" > "${dest}/hpp_total_lines.txt"
  echo "${dest}"
}

compile_all_tus() {
  local dest="$1"
  local include="${2:-}"
  local sources=()
  while IFS= read -r src; do
    sources+=("${src}")
  done < <(find "${dest}/cpp" -name '*.cpp' -type f -print | sort)
  if [[ "${#sources[@]}" -eq 0 ]]; then
    echo "error: no generated C++ translation units under ${dest}/cpp" >&2
    return 1
  fi
  local start end idx=0
  start="$(date +%s%N)"
  : >"${dest}/gxx.stdout"
  : >"${dest}/gxx.stderr"
  for src in "${sources[@]}"; do
    local obj="${dest}/compile_probe_${idx}.o"
    if ! g++ -std=c++17 -O0 -c "${src}" \
      -I"${dest}/cpp" \
      ${include:+-I"${include}"} \
      -o "${obj}" >>"${dest}/gxx.stdout" 2>>"${dest}/gxx.stderr"; then
      echo "error: failed to compile generated TU ${src}" >&2
      cat "${dest}/gxx.stderr" >&2
      return 1
    fi
    idx=$((idx + 1))
  done
  end="$(date +%s%N)"
  python3 - <<PY
start=int("${start}")
end=int("${end}")
print(f"{(end-start)/1e6:.1f}")
PY
}

mkdir -p "${OUT_DIR}"
NEW_PYCC="$(find_pycc "${ROOT}")" || { echo "error: build this branch first (flows/scripts/pyc build)" >&2; exit 1; }
BASE_PYCC="$(find_pycc "${BASELINE_ROOT}")" || { echo "error: build baseline worktree first" >&2; exit 1; }
NEW_INC="${ROOT}/.pycircuit_out/toolchain/install/include"
BASE_INC="${BASELINE_ROOT}/.pycircuit_out/toolchain/install/include"
NEW_FE="${ROOT}/compiler/frontend"
BASE_FE="${BASELINE_ROOT}/compiler/frontend"

declare -A DESIGN_PY
DESIGN_PY[counter]="${ROOT}/designs/examples/counter/counter.py"
DESIGN_PY[issue_queue_2picker]="${ROOT}/designs/examples/issue_queue_2picker/issue_queue_2picker.py"

{
  echo "baseline_root=${BASELINE_ROOT}"
  echo "new_root=${ROOT}"
  echo "new_pycc=${NEW_PYCC}"
  echo "base_pycc=${BASE_PYCC}"
  echo "repeat=${REPEAT}"
  echo "designs=${DESIGNS}"
} > "${OUT_DIR}/commands.txt"

echo "label,design,hpp_lines,gxx_all_tus_ms_median,tu_count,notes" > "${OUT_DIR}/summary.csv"

IFS=',' read -r -a names <<< "${DESIGNS}"
for name in "${names[@]}"; do
  py="${DESIGN_PY[$name]:-}"
  if [[ -z "${py}" || ! -f "${py}" ]]; then
    echo "skip unknown/missing design: ${name}" >&2
    continue
  fi
  base_dest="$(emit_one baseline "${BASE_PYCC}" "${BASE_FE}" "${name}" "${py}" "")"
  new_dest="$(emit_one localize "${NEW_PYCC}" "${NEW_FE}" "${name}" "${py}" "")"
  base_hpp="$(cat "${base_dest}/hpp_total_lines.txt")"
  new_hpp="$(cat "${new_dest}/hpp_total_lines.txt")"

  base_times=()
  new_times=()
  for ((i=1; i<=REPEAT; i++)); do
    base_times+=("$(compile_all_tus "${base_dest}" "${BASE_INC}")")
    new_times+=("$(compile_all_tus "${new_dest}" "${NEW_INC}")")
  done
  median() {
    python3 - <<PY
vals = """$*""".split()
nums = sorted(float(v) for v in vals)
if not nums:
    raise SystemExit("no successful compile measurements")
print(nums[len(nums)//2])
PY
  }
  base_med="$(median "${base_times[*]}")"
  new_med="$(median "${new_times[*]}")"
  base_tus="$(find "${base_dest}/cpp" -name '*.cpp' -type f -print | wc -l)"
  new_tus="$(find "${new_dest}/cpp" -name '*.cpp' -type f -print | wc -l)"
  echo "baseline,${name},${base_hpp},${base_med},${base_tus}," >> "${OUT_DIR}/summary.csv"
  echo "localize,${name},${new_hpp},${new_med},${new_tus}," >> "${OUT_DIR}/summary.csv"
  echo "[${name}] baseline hpp=${base_hpp} all-TU g++=${base_med}ms | localize hpp=${new_hpp} all-TU g++=${new_med}ms"
done

echo "wrote ${OUT_DIR}/summary.csv"
