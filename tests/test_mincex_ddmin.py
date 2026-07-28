"""Regression gate for the B4 counterexample minimizer (delta-debugging).

Self-contained (no pycc / Verilator): a golden model and a *buggy* DUT model
(both built from the B2 reference executor) diverge only on one poisoned
instruction. The minimizer must shrink a long failing stream down to just that
instruction while preserving the failure's identity.

Covered:
  * ENGINE   : the generic ddmin engine is 1-minimal and order-preserving.
  * MINIMIZE : a 6-instruction failing stream reduces to the single offending
               instruction, with the correct first-mismatch (field/pc) intact.
  * SIGNATURE: minimization locks onto the original failure (kind/field/pc).
  * NO-REPRO : a non-failing stream reports reproduced=False and is untouched.
"""

from __future__ import annotations

from typing import Mapping

from pycircuit.cosim import (
    CommitProfile,
    Instr,
    ReferenceModel,
    ddmin,
    minimize_lockstep,
)

PROFILE = {
    "schema": "pyc-commit-demo-v1",
    "required": ["valid", "pc", "insn", "wb_valid"],
    "groups": {"wb": {"valid": "wb_valid", "members": ["wb_rd", "wb_data"]}},
}

BAD_OPCODE = 0xBAD


def _buggy(regs: list[int], instr: Instr) -> Mapping[str, int]:
    """Immediate-writeback, but poisons wb_data for one specific opcode."""
    ops = instr.operands
    wb = int(ops.get("wb", 0))
    rd = int(ops.get("rd", 0))
    imm = int(ops.get("imm", 0))
    if instr.insn == BAD_OPCODE:
        imm ^= 1  # design bug: off-by-one writeback
    return {"wb_valid": wb, "wb_rd": rd, "wb_data": imm}


def _program(bad_index: int = 3, n: int = 6) -> list[Instr]:
    prog = []
    for k in range(n):
        prog.append(Instr(
            pc=0x100 + 4 * k,
            insn=BAD_OPCODE if k == bad_index else 0x13,
            retire=True,
            operands={"wb": 1, "rd": k + 1, "imm": 0x100 + k},
        ))
    return prog


# --------------------------------------------------------------------------- #
# Generic engine.
# --------------------------------------------------------------------------- #
def test_ddmin_single_trigger() -> None:
    items = list(range(8))
    minimal = ddmin(items, lambda s: 5 in s)
    assert minimal == [5]


def test_ddmin_two_triggers_is_1_minimal_and_ordered() -> None:
    items = list(range(8))
    minimal = ddmin(items, lambda s: (2 in s) and (6 in s))
    assert minimal == [2, 6]


def test_ddmin_returns_input_when_not_reproducing() -> None:
    items = [1, 2, 3]
    assert ddmin(items, lambda s: False) == [1, 2, 3]


# --------------------------------------------------------------------------- #
# Lockstep-backed minimization.
# --------------------------------------------------------------------------- #
def test_minimize_to_single_offending_instruction() -> None:
    program = _program(bad_index=3, n=6)
    result = minimize_lockstep(
        program,
        golden=ReferenceModel(),
        dut=ReferenceModel(semantics=_buggy),
        profile=PROFILE,
    )
    assert result.reproduced
    assert result.original_len == 6
    assert result.minimal_len == 1
    only = result.minimal[0]
    assert only.insn == BAD_OPCODE and only.pc == 0x100 + 4 * 3

    mm = result.first_mismatch
    assert mm is not None
    assert mm.kind == "field" and mm.field == "wb_data"
    assert mm.pc == 0x10C
    # golden imm=0x103; buggy dut flips low bit -> 0x102
    assert mm.golden == 0x103 and mm.dut == 0x102


def test_signature_is_preserved() -> None:
    program = _program(bad_index=2, n=5)
    result = minimize_lockstep(
        program,
        golden=ReferenceModel(),
        dut=ReferenceModel(semantics=_buggy),
        profile=CommitProfile.from_dict(PROFILE),
    )
    assert result.reproduced and result.minimal_len == 1
    sig = result.signature
    assert sig is not None
    assert sig.kind == "field" and sig.field == "wb_data" and sig.pc == 0x108
    payload = result.to_dict()
    assert payload["minimal_len"] == 1
    assert payload["first_mismatch"]["field"] == "wb_data"
    assert len(payload["minimal_program"]) == 1


def test_no_repro_when_dut_matches_golden() -> None:
    program = _program(bad_index=99, n=4)  # no BAD opcode present
    result = minimize_lockstep(
        program,
        golden=ReferenceModel(),
        dut=ReferenceModel(semantics=_buggy),
        profile=PROFILE,
    )
    assert not result.reproduced
    assert result.minimal_len == result.original_len == 4
    assert result.report is not None and result.report.ok
