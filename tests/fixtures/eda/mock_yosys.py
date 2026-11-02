#!/usr/bin/env python3
"""Minimal Yosys command fixture for Nangate45 adapter contract tests."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "-s":
        return 2
    script = Path(sys.argv[2])
    if not script.is_file():
        return 2

    outputs: dict[str, Path] = {}
    top = "top"
    for line in script.read_text(encoding="utf-8").splitlines():
        words = shlex.split(line)
        if not words:
            continue
        if words[0] == "hierarchy" and "-top" in words:
            top = words[words.index("-top") + 1]
        if words[0] == "tee" and "-o" in words:
            destination = Path(words[words.index("-o") + 1])
            outputs["json" if destination.suffix == ".json" else "report"] = destination
        if words[0] == "write_verilog":
            outputs["netlist"] = Path(words[-1])

    exit_code = int(os.environ.get("PYC_MOCK_YOSYS_EXIT", "0"))
    if exit_code != 0:
        sys.stderr.write("mock Yosys failure\n")
        return exit_code

    for path in outputs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    outputs["netlist"].write_text(
        f"module {top}; NAND2_X1 mapped_cell (); endmodule\n",
        encoding="utf-8",
    )
    outputs["report"].write_text("Number of cells: 1\n", encoding="utf-8")
    outputs["json"].write_text(
        json.dumps(
            {
                "modules": {
                    f"\\{top}": {
                        "num_cells": 1,
                        "num_cells_by_type": {"NAND2_X1": 1},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    sys.stdout.write("mock Yosys success\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
