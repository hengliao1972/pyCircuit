#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("manifest must be a JSON object")
    return data


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except Exception:
        return str(path.resolve())


def _cmake_str(value: str) -> str:
    return value.replace("\\", "/")


def _cmake_list(values: list[Path]) -> str:
    return ";".join(_cmake_str(str(v)) for v in values)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate CMake project from pyCircuit cpp manifest")
    ap.add_argument("--manifest", required=True, help="Path to cpp_project_manifest.json")
    ap.add_argument("--out-dir", required=True, help="Directory to write CMakeLists.txt")
    args = ap.parse_args()

    manifest_path = Path(args.manifest).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data = _load(manifest_path)

    srcs = [Path(s).resolve() for s in data.get("sources", []) if isinstance(s, str) and s]
    tb_cpp = Path(str(data.get("tb_cpp", ""))).resolve()
    incs = [Path(s).resolve() for s in data.get("include_dirs", []) if isinstance(s, str) and s]
    runtime_srcs = [Path(s).resolve() for s in data.get("runtime_sources", []) if isinstance(s, str) and s]
    runtime_incs = [Path(s).resolve() for s in data.get("runtime_include_dirs", []) if isinstance(s, str) and s]
    runtime = data.get("runtime", {}) if isinstance(data.get("runtime", {}), dict) else {}
    runtime_incs.extend([Path(s).resolve() for s in runtime.get("include_dirs", []) if isinstance(s, str) and s])
    runtime_lib_files = [Path(s).resolve() for s in runtime.get("library_files", []) if isinstance(s, str) and s]
    runtime_target = str(runtime.get("cmake_target", "pycircuit::pyc4_runtime"))
    runtime_pkg = str(runtime.get("cmake_package", "pycircuit"))
    runtime_cfg = str(runtime.get("cmake_config_dir", ""))
    runtime_cfg_exists = bool(runtime_cfg) and (Path(runtime_cfg) / "pycircuitConfig.cmake").is_file()
    runtime_toolchain_root = str(runtime.get("toolchain_root_hint", ""))
    std = str(data.get("cxx_standard", "c++17"))
    pch_headers = [
        Path(s).resolve()
        for s in data.get("precompile_headers", [])
        if isinstance(s, str) and s
    ]

    if not srcs:
        raise SystemExit("manifest missing `sources`")
    if not tb_cpp.is_file():
        raise SystemExit(f"missing tb cpp: {tb_cpp}")
    for s in srcs:
        if not s.is_file():
            raise SystemExit(f"missing source: {s}")

    lines: list[str] = []
    lines.append("cmake_minimum_required(VERSION 3.20)\n")
    lines.append("project(pyc_tb LANGUAGES CXX)\n")
    lines.append(f"set(CMAKE_CXX_STANDARD {std.replace('c++', '')})\n")
    lines.append("set(CMAKE_CXX_STANDARD_REQUIRED ON)\n")
    lines.append("set(CMAKE_CXX_EXTENSIONS OFF)\n\n")

    lines.append("set(PYC_TB_SOURCES\n")
    for s in srcs:
        lines.append(f"  \"{_rel(s, out_dir)}\"\n")
    lines.append(f"  \"{_rel(tb_cpp, out_dir)}\"\n")
    lines.append(")\n\n")

    lines.append("add_executable(pyc_tb ${PYC_TB_SOURCES})\n")
    if incs:
        lines.append("target_include_directories(pyc_tb PRIVATE\n")
        for i in incs:
            lines.append(f"  \"{_rel(i, out_dir)}\"\n")
        lines.append(")\n")
    if runtime_cfg_exists:
        if runtime_toolchain_root:
            lines.append(f"list(PREPEND CMAKE_PREFIX_PATH \"{_cmake_str(runtime_toolchain_root)}\")\n")
        lines.append(
            f"find_package({runtime_pkg} CONFIG REQUIRED PATHS \"{_cmake_str(runtime_cfg)}\" NO_DEFAULT_PATH)\n"
        )
        lines.append(f"target_link_libraries(pyc_tb PRIVATE {runtime_target})\n")
    elif runtime_lib_files:
        lines.append("add_library(pyc4_runtime_prebuilt STATIC IMPORTED GLOBAL)\n")
        lines.append("set_target_properties(pyc4_runtime_prebuilt PROPERTIES\n")
        lines.append(f"  IMPORTED_LOCATION \"{_cmake_str(str(runtime_lib_files[0]))}\"\n")
        if runtime_incs:
            lines.append(f"  INTERFACE_INCLUDE_DIRECTORIES \"{_cmake_list(runtime_incs)}\"\n")
        lines.append(")\n")
        lines.append("target_link_libraries(pyc_tb PRIVATE pyc4_runtime_prebuilt)\n")
    elif runtime_srcs:
        lines.append("set(PYC_RUNTIME_SOURCES\n")
        for s in runtime_srcs:
            lines.append(f"  \"{_rel(s, out_dir)}\"\n")
        lines.append(")\n")
        lines.append("add_library(pyc4_runtime STATIC ${PYC_RUNTIME_SOURCES})\n")
        if runtime_incs:
            lines.append("target_include_directories(pyc4_runtime PUBLIC\n")
            for i in runtime_incs:
                lines.append(f"  \"{_rel(i, out_dir)}\"\n")
            lines.append(")\n")
        lines.append("target_link_libraries(pyc_tb PRIVATE pyc4_runtime)\n")
    if pch_headers:
        for h in pch_headers:
            if not h.is_file():
                raise SystemExit(f"missing precompile header: {h}")
        lines.append("target_precompile_headers(pyc_tb PRIVATE\n")
        for h in pch_headers:
            lines.append(f"  \"{_cmake_str(_rel(h, out_dir))}\"\n")
        lines.append(")\n")
    lines.append("\n")

    out = out_dir / "CMakeLists.txt"
    text = "".join(lines)
    if out.is_file():
        try:
            if out.read_text(encoding="utf-8") == text:
                print(str(out))
                return 0
        except OSError:
            pass
    out.write_text(text, encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
