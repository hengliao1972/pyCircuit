#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT
cxx="${CXX:-c++}"
"${cxx}" -std=c++17 -Wall -Wextra -Werror -I"${repo_root}/runtime/cpp" \
  "${repo_root}/tests/runtime/test_component_protocol.cpp" -o "${build_dir}/t"
"${build_dir}/t"
echo "component protocol runtime tests passed"
