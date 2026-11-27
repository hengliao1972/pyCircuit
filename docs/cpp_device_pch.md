# C++ device hpp precompiled headers (`--cpp-pch`)

Optional CMake integration that records device module top-level `.hpp` paths in
`cpp_compile_manifest.json` so `gen_cmake_from_manifest.py` can emit
`target_precompile_headers`. This can speed up cold builds of very large
headers. PCH does not change C++ emit text.

## Requirements

| Flag | Value | Notes |
|------|-------|-------|
| `--cpp-pch` | (flag) | Records PCH intent in the manifest |
| `--cpp-split` | `module` | Required when PCH is on |
| `--emit` | `cpp` | Manifest is written on C++ emission |

```bash
python3 -m pycircuit.cli build <design.py> --out-dir <dir> --target cpp --cpp-pch
pycc design.pyc --emit=cpp --out-dir <dir> --cpp-split=module --cpp-pch
```

## Manifest

When enabled:

- `profile_summary.cpp_pch`: `true`
- `precompile_headers`: absolute path(s) to `<module>/<module>.hpp`
- `precompile_headers_mode`: `"device_hpp"`

`cli build` aggregates those headers into `cpp_project_manifest.json`
(fallback: `flows/tools/cpp_pch_headers.py`).

## Orthogonal to member placement

`--cpp-pch` is independent of always-on placement. On medium headers the PCH
generation cost can offset TU savings; large unoptimized headers benefit more.

## Gate

```bash
bash compiler/mlir/test/cpp_device_pch_smoke.sh
```
