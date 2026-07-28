#!/usr/bin/env python3
"""Observable B4 demo: shrink a failing instruction stream to its minimal core.

A golden model and a buggy DUT model (both the B2 reference executor) agree on
every instruction except one poisoned opcode that mis-computes its writeback.
The demo drives a long stream, then delta-debugging (ddmin) minimizes it to the
single offending instruction while preserving the failure's identity -- exactly
the "these N instructions, and commit k's wb_data disagrees" counterexample the
agent wants instead of a giant waveform.

Usage:
  python3 tests/designs/run_mincex_demo.py [N] [BAD_INDEX]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "compiler" / "frontend"))

from pycircuit.cosim import Instr, ReferenceModel, minimize_lockstep  # noqa: E402

PROFILE = {
    "schema": "pyc-commit-demo-v1",
    "required": ["valid", "pc", "insn", "wb_valid"],
    "groups": {"wb": {"valid": "wb_valid", "members": ["wb_rd", "wb_data"]}},
}
BAD_OPCODE = 0xBAD


def _buggy(regs: list[int], instr: Instr) -> Mapping[str, int]:
    ops = instr.operands
    wb, rd, imm = int(ops.get("wb", 0)), int(ops.get("rd", 0)), int(ops.get("imm", 0))
    if instr.insn == BAD_OPCODE:
        imm ^= 1  # injected design bug
    return {"wb_valid": wb, "wb_rd": rd, "wb_data": imm}


def _program(n: int, bad_index: int) -> list[Instr]:
    return [
        Instr(pc=0x100 + 4 * k,
              insn=BAD_OPCODE if k == bad_index else 0x13,
              retire=True,
              operands={"wb": 1, "rd": k + 1, "imm": 0x100 + k})
        for k in range(n)
    ]


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    bad = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    program = _program(n, bad)

    print(f"[mincex] failing stream : {n} instructions (bug hidden at index {bad}, "
          f"pc=0x{0x100 + 4 * bad:X})")
    result = minimize_lockstep(
        program, golden=ReferenceModel(), dut=ReferenceModel(semantics=_buggy),
        profile=PROFILE,
    )

    if not result.reproduced:
        print("[mincex] no divergence -- nothing to minimize")
        return 0

    print(f"[mincex] oracle calls   : {result.oracle_calls}")
    print(f"[mincex] minimized      : {result.original_len} -> {result.minimal_len} "
          f"instruction(s)")
    for i, ins in enumerate(result.minimal):
        print(f"[mincex]   keep[{i}] pc=0x{ins.pc:X} insn=0x{ins.insn:X} "
              f"operands={dict(ins.operands)}")
    mm = result.first_mismatch
    if mm is not None:
        print(f"[mincex] MINIMAL REPRO  : commit #{mm.index} at pc=0x{mm.pc:X}: "
              f"{mm.field} golden={mm.golden} vs dut={mm.dut} ({mm.kind})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
