#!/usr/bin/env bash
# Guard the runtime header layout: every runtime/cpp header must be reachable
# through both compatibility include roots, and every component family must be
# named by a header of its own (not folded into pyc_primitives.hpp).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail=0

for header in "${repo_root}"/runtime/cpp/*.hpp; do
  base="$(basename "${header}")"
  for dir in include/cpp include/pyc/cpp; do
    link="${repo_root}/${dir}/${base}"
    if [[ ! -L "${link}" ]]; then
      echo "missing compatibility symlink: ${dir}/${base}"
      fail=1
      continue
    fi
    if [[ "$(readlink -f "${link}")" != "$(readlink -f "${header}")" ]]; then
      echo "wrong symlink target: ${dir}/${base} -> $(readlink "${link}")"
      fail=1
    fi
  done
done

for family in pyc_reg.hpp pyc_delay_line.hpp pyc_fifo.hpp pyc_cdc_sync.hpp \
              pyc_sync_mem.hpp pyc_byte_mem.hpp pyc_async_fifo.hpp; do
  if [[ ! -f "${repo_root}/runtime/cpp/${family}" ]]; then
    echo "missing component header: runtime/cpp/${family}"
    fail=1
  fi
done

if grep -q 'class RegBase\|class DelayBase\|class pycFifo' "${repo_root}/runtime/cpp/pyc_primitives.hpp"; then
  echo "component classes leaked back into pyc_primitives.hpp"
  fail=1
fi

# The vector runtime went away with the IR vector<> type: no vec header, no vec
# component classes, and no dangling compatibility symlink left behind.
if [[ -e "${repo_root}/runtime/cpp/pyc_vec.hpp" ]]; then
  echo "pyc_vec.hpp must stay deleted"
  fail=1
fi
if grep -rqE 'pycVecReg|pycVecDelayLine' --include='*.hpp' "${repo_root}/runtime/cpp"; then
  echo "vector component classes must stay deleted"
  fail=1
fi
for dir in include/cpp include/pyc/cpp; do
  if [[ -L "${repo_root}/${dir}/pyc_vec.hpp" || -e "${repo_root}/${dir}/pyc_vec.hpp" ]]; then
    echo "stale vector symlink: ${dir}/pyc_vec.hpp"
    fail=1
  fi
done

if [[ "${fail}" -ne 0 ]]; then
  echo "runtime header layout check FAILED"
  exit 1
fi
echo "runtime header layout check passed"
