#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "compiler" / "frontend"
sys.path.insert(0, str(FRONTEND))

from pycircuit.cli import _backend_build_flag_hashes


class TestCppPchCacheKeys(unittest.TestCase):
    def test_pch_only_invalidates_cpp_key(self) -> None:
        shared_flags = {
            "pycc": "/toolchain/bin/pycc",
            "logic_depth": 32,
            "target": "both",
        }

        shared_off, cpp_off = _backend_build_flag_hashes(shared_flags, cpp_pch=False)
        shared_on, cpp_on = _backend_build_flag_hashes(shared_flags, cpp_pch=True)

        self.assertEqual(shared_off, shared_on)
        self.assertNotEqual(cpp_off, cpp_on)
        self.assertNotIn("cpp_pch", shared_flags)


if __name__ == "__main__":
    unittest.main()
