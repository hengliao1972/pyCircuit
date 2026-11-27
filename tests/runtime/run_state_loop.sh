#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

cxx="${CXX:-c++}"
source_file="${repo_root}/tests/runtime/test_state_loop.cpp"
commit_shrink_file="${repo_root}/tests/runtime/test_commit_shrink.cpp"

for include_dir in \
  "${repo_root}/runtime/cpp" \
  "${repo_root}/include/pyc/cpp" \
  "${repo_root}/include/cpp"; do
  include_name="${include_dir#${repo_root}/}"
  output_name="${include_name//\//_}"
  "${cxx}" -std=c++17 -Wall -Wextra -Werror \
    -I"${include_dir}" "${source_file}" \
    -o "${build_dir}/${output_name}"
  "${build_dir}/${output_name}"
  "${cxx}" -std=c++17 -Wall -Wextra -Werror \
    -I"${include_dir}" "${commit_shrink_file}" \
    -o "${build_dir}/${output_name}_commit_shrink"
  "${build_dir}/${output_name}_commit_shrink"
done

mark_src="${repo_root}/tests/runtime/test_lane_mark_walk.cpp"
for include_dir in \
  "${repo_root}/runtime/cpp" \
  "${repo_root}/include/pyc/cpp" \
  "${repo_root}/include/cpp"; do
  include_name="${include_dir#${repo_root}/}"
  output_name="${include_name//\//_}_lane_mark"
  "${cxx}" -std=c++17 -Wall -Wextra -Werror \
    -I"${include_dir}" "${mark_src}" \
    -o "${build_dir}/${output_name}"
  "${build_dir}/${output_name}"
done

echo "state loop runtime tests passed"
