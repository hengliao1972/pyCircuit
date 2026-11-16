#!/usr/bin/env python3
"""Verify C++ delay taps against unoptimized C++ (delay lines are C++-only)."""

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

    source = HERE / "state_delay_tap_codegen.mlir"
    with tempfile.TemporaryDirectory(prefix="pyc-delay-tap-", dir="/tmp") as path:
        temp = Path(path)
        opt_cpp = temp / "opt.hpp"
        opt_stats = emit(args.pycc, source, opt_cpp)
        if "pyc_delay_line<" not in opt_cpp.read_text(encoding="utf-8"):
            raise AssertionError("opt: chain was not folded into a delay line")
        if opt_stats.get("reg_count") != 4 or opt_stats.get("reg_bits") != 32:
            raise AssertionError(f"opt: logical state changed: {opt_stats}")

        # The driver carries an independent software model and asserts the
        # optimized tap/tail values every cycle, so no unoptimized C++ arm is
        # needed.
        driver = HERE / "state_delay_tap_model.cpp"
        compile_cpp(args.cxx, opt_cpp, temp / "opt_bin", driver)
        cycles, checksum = result([str(temp / "opt_bin")])
        print(
            "delay tap verified: 4-reg chain -> one depth-4 history "
            f"with depth-2 tap; optimized C++ matched the software model for "
            f"{cycles} cycles "
            f"(checksum={checksum})"
        )


if __name__ == "__main__":
    main()
