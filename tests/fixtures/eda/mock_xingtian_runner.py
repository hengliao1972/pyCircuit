#!/usr/bin/env python3
"""Local-only runner fixture implementing the XingTian command contract."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--filelist", required=True)
    parser.add_argument("--sdc", required=True)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--host")
    parser.add_argument("--lib-variant")
    parser.add_argument("--sdc-style")
    parser.add_argument("--ssh-config")
    parser.add_argument("--identity-file")
    args = parser.parse_args()

    design = Path(args.design)
    if not (design / args.filelist).is_file() or not (design / args.sdc).is_file():
        return 4
    for line in (design / args.filelist).read_text(encoding="utf-8").splitlines():
        if line and not (design / line).is_file():
            return 4

    result = Path(args.results_dir) / "20260922-120000"
    (result / "output").mkdir(parents=True, exist_ok=True)
    (result / "rpt").mkdir(exist_ok=True)
    (result / "log").mkdir(exist_ok=True)
    (result / "log" / "xt_run.log").write_text("mock log\n", encoding="utf-8")

    exit_code = int(os.environ.get("PYC_MOCK_XINGTIAN_EXIT", "0"))
    if exit_code == 0:
        (result / "output" / f"{args.top}.mapped.v").write_text(
            f"module {args.top}; endmodule\n",
            encoding="utf-8",
        )
        (result / "rpt" / "qor.rpt").write_text("mock qor\n", encoding="utf-8")
        (result / "rpt" / "metrics.json").write_text(
            json.dumps({"Checkpoints": []}),
            encoding="utf-8",
        )

    latest = Path(args.results_dir) / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(result.name)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
