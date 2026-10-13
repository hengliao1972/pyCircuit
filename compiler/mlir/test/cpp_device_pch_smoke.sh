#!/usr/bin/env bash
# Gate: --cpp-pch flag, manifest precompile_headers, and generated CMake PCH.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYCC="${PYCC:-${ROOT}/.pycircuit_out/toolchain/build/bin/pycc}"
EXAMPLE="${ROOT}/designs/examples/counter/counter.py"
OUT="${ROOT}/.pycircuit_out/gates/cpp_device_pch_smoke"

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

if ! "${PYCC}" --help 2>&1 | grep -q -- '--cpp-pch'; then
  echo "fail: pycc missing --cpp-pch flag" >&2
  exit 1
fi

if [[ ! -f "${EXAMPLE}" ]]; then
  echo "fail: example not found: ${EXAMPLE}" >&2
  exit 1
fi

rm -rf "${OUT}"
mkdir -p "${OUT}"

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

if "${PYCC}" "${OUT}/counter.pyc" --emit=cpp --out-dir "${OUT}/cpp_none" \
    --cpp-split=none --cpp-pch >/dev/null 2>"${OUT}/pch_none.stderr"; then
  echo "fail: --cpp-pch without --cpp-split=module unexpectedly succeeded" >&2
  exit 1
fi
if ! grep -q 'requires --cpp-split=module' "${OUT}/pch_none.stderr"; then
  echo "fail: missing --cpp-pch / --cpp-split diagnostic" >&2
  cat "${OUT}/pch_none.stderr" >&2
  exit 1
fi

if "${PYCC}" "${OUT}/counter.pyc" --emit=cpp --cpp-pch \
    -o "${OUT}/single.cpp" >/dev/null 2>"${OUT}/pch_single.stderr"; then
  echo "fail: --cpp-pch with single-file output unexpectedly succeeded" >&2
  exit 1
fi
if ! grep -q 'requires --out-dir' "${OUT}/pch_single.stderr"; then
  echo "fail: missing --cpp-pch / --out-dir diagnostic" >&2
  cat "${OUT}/pch_single.stderr" >&2
  exit 1
fi

"${PYCC}" "${OUT}/counter.pyc" \
  --emit=cpp \
  --out-dir "${OUT}/cpp" \
  --cpp-split=module \
  --cpp-pch \
  --build-profile=dev-fast \
  >/dev/null

manifest="${OUT}/cpp/cpp_compile_manifest.json"
if [[ ! -f "${manifest}" ]]; then
  echo "fail: missing manifest ${manifest}" >&2
  exit 1
fi

python3 - <<'PY' "${manifest}"
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
profile = data.get("profile_summary") or {}
if not profile.get("cpp_pch"):
    raise SystemExit(f"fail: profile_summary.cpp_pch not true in {path}")

pch = data.get("precompile_headers") or []
if not pch:
    raise SystemExit(f"fail: precompile_headers empty in {path}")
if data.get("precompile_headers_mode") != "device_hpp":
    raise SystemExit(f"fail: precompile_headers_mode != device_hpp in {path}")
header = Path(pch[0])
if not header.is_absolute():
    raise SystemExit(f"fail: precompile header is not absolute: {header}")
if not header.is_file():
    raise SystemExit(f"fail: missing precompile header file: {header}")
print("ok: cpp_pch and precompile_headers present in manifest")
PY

python3 - <<'PY' "${manifest}" "${OUT}/cpp_project_manifest.json" "${RUNTIME_INCLUDE}"
import json
import sys
from pathlib import Path

module_manifest = Path(sys.argv[1])
project_manifest = Path(sys.argv[2])
runtime_include = Path(sys.argv[3]).resolve()
data = json.loads(module_manifest.read_text(encoding="utf-8"))
cpp_dir = module_manifest.parent.resolve()
tb = project_manifest.parent / "pch_tb.cpp"
tb.write_text('#include "counter.hpp"\nint main() { return 0; }\n', encoding="utf-8")
headers = [str(Path(p).resolve()) for p in data.get("precompile_headers") or []]
if not headers:
    raise SystemExit("fail: no PCH headers to compile")
sources = sorted(str(p.resolve()) for p in cpp_dir.glob("*.cpp"))
if not sources:
    raise SystemExit("fail: no generated C++ translation units to compile with PCH")
project_manifest.write_text(
    json.dumps(
        {
            "version": 3,
            "sources": sources,
            "tb_cpp": str(tb.resolve()),
            "include_dirs": [str(cpp_dir), str(runtime_include)],
            "runtime": data.get("runtime") or {},
            "cxx_standard": "c++17",
            "precompile_headers": headers,
            "precompile_headers_mode": "device_hpp",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

python3 "${ROOT}/flows/tools/gen_cmake_from_manifest.py" \
  --manifest "${OUT}/cpp_project_manifest.json" \
  --out-dir "${OUT}/cmake_src" >/dev/null
cmake -S "${OUT}/cmake_src" -B "${OUT}/cmake_build" \
  -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "${OUT}/cmake_build" --parallel 2 >/dev/null
python3 - <<'PY' "${OUT}/cmake_build"
import sys
from pathlib import Path

build = Path(sys.argv[1])
artifacts = [p for p in build.rglob("*") if "cmake_pch" in p.name]
if not artifacts:
    raise SystemExit(f"fail: CMake produced no PCH artifacts under {build}")
print("ok: generated CMake compiled device-header PCH")
PY

echo "ok: cpp device pch smoke passed"
