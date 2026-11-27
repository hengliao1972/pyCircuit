#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

cxx="${CXX:-c++}"
source_file="${repo_root}/tests/runtime/test_parallel_pool.cpp"

for include_dir in \
  "${repo_root}/runtime/cpp" \
  "${repo_root}/include/pyc/cpp" \
  "${repo_root}/include/cpp"; do
  [ -d "${include_dir}" ] || continue
  include_name="${include_dir#${repo_root}/}"
  output_name="${include_name//\//_}"
  # No -pthread: the generated models are compiled without it, and glibc folds
  # libpthread into libc, so the pool must link on the plain command line.
  "${cxx}" -std=c++17 -O2 -Wall -Wextra -Werror \
    -I"${include_dir}" "${source_file}" \
    -o "${build_dir}/${output_name}"
  "${build_dir}/${output_name}"
done

echo "parallel pool runtime tests passed"
