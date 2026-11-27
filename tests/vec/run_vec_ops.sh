#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${repo_root}/compiler/frontend"

python_bin="${PYTHON:-python3}"
"${python_bin}" -m pytest tests/vec -m "${PYC_VEC_TEST_MARK:-vec}"
