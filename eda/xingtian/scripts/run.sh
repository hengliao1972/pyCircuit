#!/usr/bin/env bash
set -euo pipefail

runner="$(
  python3 -c 'from pycircuit.eda import xingtian_runner_path; print(xingtian_runner_path())'
)"
exec "$runner" "$@"
