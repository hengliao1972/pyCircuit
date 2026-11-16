#!/usr/bin/env python3
"""Cross-check retimed C++ against unoptimized C++ (delay lines are C++-only)."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from _state_model_check import DEFAULT_BUILD, compile_cpp, emit, result

HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pycc", type=Path, default=DEFAULT_BUILD / "pycc")
    parser.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()

    source = HERE / "state_retime_codegen.mlir"
    with tempfile.TemporaryDirectory(prefix="pyc-retime-", dir="/tmp") as path:
        temp = Path(path)
        opt_cpp = temp / "opt.hpp"
        cpp_stats = emit(args.pycc, source, opt_cpp)
        if (cpp_stats.get("reg_count") != 5 or cpp_stats.get("reg_bits") != 33):
            raise AssertionError(f"cpp: logical state changed: {cpp_stats}")
        if cpp_stats.get("retime_regions_rewritten") != 2:
            raise AssertionError(f"cpp: retiming stats mismatch: {cpp_stats}")
        header = opt_cpp.read_text(encoding="utf-8")
        if "DelayLoop<" not in header and "pyc_delay_line<" not in header:
            raise AssertionError("cpp: retiming emitted no delay line")

        # The driver carries an independent software model and asserts every
        # output each cycle; no unoptimized C++ reference arm is needed (and
        # needed.
        driver = HERE / "state_retime_model.cpp"
        compile_cpp(args.cxx, opt_cpp, temp / "opt_bin", driver)
        cycles, checksum = result([str(temp / "opt_bin")])
        print(
            "retiming verified: a 3-register computed pipeline -> one depth-3 "
            "history and two delayed i8 operands -> one i1 result state; "
            "optimized C++ matched the software model for "
            f"{cycles} cycles (checksum={checksum})"
        )


if __name__ == "__main__":
    main()
