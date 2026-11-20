#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT
cxx="${CXX:-c++}"
source_file="${repo_root}/tests/runtime/test_sync_mem_2w.cpp"
"${cxx}" -std=c++17 -Wall -Wextra -Werror -I"${repo_root}/runtime/cpp" "${source_file}" -o "${build_dir}/test_sync_mem_2w"
"${build_dir}/test_sync_mem_2w"
echo "sync_mem_2w runtime tests passed"
