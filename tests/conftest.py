from __future__ import annotations

import sys
from pathlib import Path

# Make the in-tree frontend package importable without requiring callers to set
# PYTHONPATH, so root-level gates (e.g. test_cut_point_static_contract) run under
# a plain `pytest` invocation.
_FRONTEND = Path(__file__).resolve().parents[1] / "compiler" / "frontend"
if _FRONTEND.is_dir():
    p = str(_FRONTEND)
    if p not in sys.path:
        sys.path.insert(0, p)

# Also expose this tests/ dir so shared helpers (e.g. ``_toolchain``) import
# regardless of pytest's import mode.
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
