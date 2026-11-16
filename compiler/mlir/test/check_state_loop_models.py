#!/usr/bin/env python3
"""State-loop lowering must not change behavior: emit each state fixture with
and without --state-loop, compile the same driver against both headers, and
require identical cycles/checksum (the drivers also assert a software model)."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from _state_model_check import DEFAULT_BUILD, compile_cpp, emit, result

HERE = Path(__file__).resolve().parent
FIXTURES = (
    ("state_delay_tap_codegen.mlir", "state_delay_tap_model.cpp"),
    ("state_retime_codegen.mlir", "state_retime_model.cpp"),
    # Named stages become lazy tap probes (DelayLoop::TapView registration).
    ("state_loop_probe.mlir", "state_delay_tap_model.cpp"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pycc", type=Path, default=DEFAULT_BUILD / "pycc")
    parser.add_argument("--cxx", default=os.environ.get("CXX", "c++"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="pyc-state-loop-", dir="/tmp") as path:
        temp = Path(path)
        for source, driver in FIXTURES:
            outputs = {}
            for mode, flag in (("loop", "--state-loop=true"),
                               ("flat", "--state-loop=false")):
                header = temp / f"{source}.{mode}.hpp"
                emit(args.pycc, HERE / source, header, flag)
                text = header.read_text(encoding="utf-8")
                looped = "RegLoop<" in text or "DelayLoop<" in text
                if looped != (mode == "loop"):
                    raise AssertionError(f"{source} {mode}: loop lowering={looped}")
                binary = temp / f"{source}.{mode}.bin"
                compile_cpp(args.cxx, header, binary, HERE / driver)
                outputs[mode] = result([str(binary)])
            if outputs["loop"] != outputs["flat"]:
                raise AssertionError(f"{source}: loop/flat mismatch {outputs}")
            cycles, checksum = outputs["loop"]
            print(f"{source}: loop == flat over {cycles} cycles "
                  f"(checksum={checksum})")


if __name__ == "__main__":
    main()
