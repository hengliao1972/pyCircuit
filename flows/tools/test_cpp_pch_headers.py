#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from cpp_pch_headers import select_device_hpp_headers


class TestCppPchHeaders(unittest.TestCase):
    def test_selects_module_primary_hpp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = root / "topk_histogram"
            mod.mkdir()
            hpp = mod / "topk_histogram.hpp"
            hpp.write_text("#pragma once\n", encoding="utf-8")
            (mod / "helper.hpp").write_text("#pragma once\n", encoding="utf-8")
            got = select_device_hpp_headers([str(hpp), str(mod / "helper.hpp")])
            self.assertEqual(got, [str(hpp.resolve())])

    def test_multiple_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths: list[str] = []
            for name in ("foo", "bar"):
                mod = root / name
                mod.mkdir()
                hpp = mod / f"{name}.hpp"
                hpp.write_text("#pragma once\n", encoding="utf-8")
                paths.append(str(hpp))
            got = select_device_hpp_headers(paths)
            self.assertEqual(got, sorted(str(Path(p).resolve()) for p in paths))

    def test_empty(self) -> None:
        self.assertEqual(select_device_hpp_headers([]), [])


if __name__ == "__main__":
    unittest.main()
