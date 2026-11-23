#!/usr/bin/env bash
# Gate: always-on C++ member placement, comb reorder-safety, and manifest fields.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"
PYC_OPT="${PYC_OPT:-${ROOT}/.pycircuit_out/toolchain/build/bin/pyc-opt}"
EXAMPLE="${ROOT}/designs/examples/counter/counter.py"
INVALID_COMB="${ROOT}/compiler/mlir/test/invalid_comb_effect.mlir"
NAMED_PROBE="${ROOT}/compiler/mlir/test/cpp_named_comb_probe.mlir"
OUT="${ROOT}/.pycircuit_out/gates/cpp_member_placement_smoke"

if [[ ! -x "${PYCC}" ]]; then
  echo "fail: pycc not built at ${PYCC}" >&2
  exit 1
fi
TOOLCHAIN_ROOT="$(cd -- "$(dirname -- "${PYCC}")/.." && pwd)"
RUNTIME_INCLUDE="${TOOLCHAIN_ROOT}/include"
if [[ ! -f "${RUNTIME_INCLUDE}/cpp/pyc_sim.hpp" ]]; then
  echo "fail: installed runtime headers missing under ${RUNTIME_INCLUDE}" >&2
  exit 1
fi

if "${PYCC}" --help 2>&1 | grep -q -- '--cpp-localize-members'; then
  echo "fail: --cpp-localize-members must stay removed (placement is always-on)" >&2
  exit 1
fi
if ! "${PYCC}" --help 2>&1 | grep -q -- '--cpp-compile-budget'; then
  echo "fail: pycc missing --cpp-compile-budget (Davinci passes =false on hierarchical cores)" >&2
  exit 1
fi

if [[ -x "${PYC_OPT}" ]]; then
  if ! "${PYC_OPT}" --help 2>&1 | grep -q 'pyc-cpp-placement'; then
    echo "fail: pyc-opt missing pyc-cpp-placement pass" >&2
    exit 1
  fi
else
  echo "note: pyc-opt unavailable; registration CLI check not run" >&2
fi

if [[ ! -f "${EXAMPLE}" ]]; then
  echo "fail: example not found: ${EXAMPLE}" >&2
  exit 1
fi

rm -rf "${OUT}"
mkdir -p "${OUT}"

if "${PYCC}" "${INVALID_COMB}" --emit=cpp -o "${OUT}/invalid.cpp" \
    >"${OUT}/invalid.stdout" 2>"${OUT}/invalid.stderr"; then
  echo "fail: side-effecting pyc.comb unexpectedly passed verification" >&2
  exit 1
fi
if ! grep -q 'must be memory-effect-free' "${OUT}/invalid.stderr"; then
  echo "fail: missing pyc.comb reorder-safety diagnostic" >&2
  cat "${OUT}/invalid.stderr" >&2
  exit 1
fi

export PYTHONPATH="${ROOT}/compiler/frontend:${PYTHONPATH:-}"
python3 - <<'PY' "${EXAMPLE}" "${OUT}/counter.pyc"
import importlib.util
import sys
from pathlib import Path

example, out = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("pyc_smoke_example", example)
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

"${PYCC}" "${OUT}/counter.pyc" \
  --emit=cpp \
  --out-dir "${OUT}/cpp" \
  --cpp-split=module \
  --cpp-shard-max-ast-nodes=2 \
  --build-profile=dev-fast \
  >/dev/null

mkdir -p "${OUT}/cpp_repeat"
"${PYCC}" "${OUT}/counter.pyc" \
  --emit=cpp \
  --out-dir "${OUT}/cpp_repeat" \
  --cpp-split=module \
  --cpp-shard-max-ast-nodes=2 \
  --build-profile=dev-fast \
  >/dev/null

mkdir -p "${OUT}/cpp_budget_off"
"${PYCC}" "${OUT}/counter.pyc" \
  --emit=cpp \
  --out-dir "${OUT}/cpp_budget_off" \
  --cpp-split=module \
  --cpp-shard-max-ast-nodes=2 \
  --build-profile=dev-fast \
  --cpp-compile-budget=false \
  >/dev/null

if [[ ! -f "${OUT}/cpp/counter.hpp" ]]; then
  echo "fail: missing ${OUT}/cpp/counter.hpp" >&2
  exit 1
fi
if [[ ! -f "${OUT}/cpp_budget_off/counter.hpp" ]]; then
  echo "fail: --cpp-compile-budget=false did not emit C++" >&2
  exit 1
fi
if ! diff -ru --exclude='*.json' "${OUT}/cpp" "${OUT}/cpp_repeat" >/dev/null; then
  echo "fail: full C++ placement output is not deterministic" >&2
  exit 1
fi

generated_sources=()
while IFS= read -r source; do
  generated_sources+=("${source}")
done < <(find "${OUT}/cpp" -name '*.cpp' -type f -print | sort)
if [[ "${#generated_sources[@]}" -eq 0 ]]; then
  echo "fail: placement smoke produced no C++ sources" >&2
  exit 1
fi
for source in "${generated_sources[@]}"; do
  g++ -std=c++17 -fsyntax-only "${source}" \
    -I"${OUT}/cpp" -I"${RUNTIME_INCLUDE}"
done

"${PYCC}" "${NAMED_PROBE}" \
  --emit=cpp \
  --out-dir "${OUT}/named_probe" \
  --cpp-split=module \
  --build-profile=dev-fast \
  >/dev/null

named_manifest="${OUT}/named_probe/cpp_compile_manifest.json"
named_hpp="${OUT}/named_probe/named_comb_probe.hpp"
named_cpp="${OUT}/named_probe/named_comb_probe.cpp"
python3 - <<'PY' "${named_manifest}" "${named_hpp}" "${named_cpp}"
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
hpp_path = Path(sys.argv[2])
cpp_path = Path(sys.argv[3])
data = json.loads(manifest.read_text(encoding="utf-8"))
hpp = hpp_path.read_text(encoding="utf-8")
cpp = cpp_path.read_text(encoding="utf-8")
placement = (data.get("profile_summary") or {}).get("cpp_placement") or {}
if int(placement.get("probe_pinned_struct", 0)) <= 0:
    raise SystemExit("fail: named comb probe was not pinned to struct storage")
if "debug_named" not in hpp or 'reg_path("debug_named")' not in cpp:
    raise SystemExit("fail: named comb probe missing from C++ ProbeRegistry")
print("ok: named comb probe is struct-pinned and registered")
PY

named_sources=()
while IFS= read -r source; do
  named_sources+=("${source}")
done < <(find "${OUT}/named_probe" -name '*.cpp' -type f -print | sort)
if [[ "${#named_sources[@]}" -eq 0 ]]; then
  echo "fail: named probe smoke produced no C++ sources" >&2
  exit 1
fi
for source in "${named_sources[@]}"; do
  g++ -std=c++17 -fsyntax-only "${source}" \
    -I"${OUT}/named_probe" -I"${RUNTIME_INCLUDE}"
done

manifest="${OUT}/cpp/cpp_compile_manifest.json"
if [[ ! -f "${manifest}" ]]; then
  echo "fail: missing manifest ${manifest}" >&2
  exit 1
fi

python3 - <<'PY' "${manifest}" "${OUT}/cpp/counter.hpp"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
hpp = Path(sys.argv[2]).read_text(encoding="utf-8")
data = json.loads(path.read_text(encoding="utf-8"))
profile = data.get("profile_summary") or {}
placement = profile.get("cpp_placement")
if not isinstance(placement, dict):
    raise SystemExit(f"fail: profile_summary.cpp_placement missing in {path}")

for field in (
    "struct_members",
    "local_in_method",
    "probe_pinned_struct",
    "cross_part_promoted",
    "scheduled_cross_method",
    "scheduled_cut_weight",
):
    if field not in placement:
        raise SystemExit(f"fail: manifest missing cpp_placement.{field}")

if placement["local_in_method"] <= 0:
    raise SystemExit("fail: expected local_in_method > 0 on counter")
if "eval_comb_" not in hpp:
    raise SystemExit("fail: generated hpp missing eval_comb_ helper")
print("ok: cpp_placement present; local_in_method=", placement["local_in_method"])
PY

echo "ok: cpp member placement smoke passed"
