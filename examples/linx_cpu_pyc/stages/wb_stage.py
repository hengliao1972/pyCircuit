from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire, jit_inline

from ..isa import (
    BK_CALL,
    BK_COND,
    BK_ICALL,
    BK_IND,
    BK_DIRECT,
    BK_FALL,
    BK_RET,
    OP_BSTART_STD_CALL,
    OP_BSTART_STD_COND,
    OP_BSTART_STD_DIRECT,
    OP_BSTART_STD_FALL,
    OP_C_BSTART_COND,
    OP_C_BSTART_DIRECT,
    OP_C_BSTART_STD,
    OP_C_LWI,
    OP_C_SETC_EQ,
    OP_C_SETC_NE,
    OP_C_SETC_TGT,
    OP_C_BSTOP,
    OP_FENTRY,
    OP_FEXIT,
    OP_FRET_RA,
    OP_FRET_STK,
    OP_SETC_AND,
    OP_SETC_ANDI,
    OP_SETC_EQ,
    OP_SETC_EQI,
    OP_SETC_GE,
    OP_SETC_GEI,
    OP_SETC_GEU,
    OP_SETC_GEUI,
    OP_SETC_LT,
    OP_SETC_LTI,
    OP_SETC_LTU,
    OP_SETC_LTUI,
    OP_SETC_NE,
    OP_SETC_NEI,
    OP_SETC_OR,
    OP_SETC_ORI,
)
from ..pipeline import CoreState, MemWbRegs


@dataclass(frozen=True)
class WbControl:
    boundary_valid: Wire
    br_take: Wire
    next_pc: Wire
    target_pc: Wire
    ra_write_valid: Wire
    ra_write_value: Wire


@jit_inline
def build_wb_stage(
    m: Circuit,
    *,
    do_wb: Wire,
    state: CoreState,
    memwb: MemWbRegs,
) -> WbControl:
    boundary_valid = m.const(0, width=1)
    br_take = m.const(0, width=1)
    next_pc = m.const(0, width=64)
    target_pc = m.const(0, width=64)
    with m.scope("WB"):
        # Global control state.
        br_kind = state.br_kind.out()
        br_base_pc = state.br_base_pc.out()
        br_off = state.br_off.out()
        commit_cond = state.commit_cond.out()
        commit_tgt = state.commit_tgt.out()

        # Current retiring instruction.
        pc = memwb.pc.out()
        op = memwb.op.out()
        len_bytes = memwb.len_bytes.out()
        regdst = memwb.regdst.out()
        value = memwb.value.out()
        is_store = memwb.is_store.out()

        # --- BlockISA control flow ---
        op_c_bstart_std = op == OP_C_BSTART_STD
        op_c_bstart_cond = op == OP_C_BSTART_COND
        op_c_bstart_direct = op == OP_C_BSTART_DIRECT
        op_bstart_std_fall = op == OP_BSTART_STD_FALL
        op_bstart_std_direct = op == OP_BSTART_STD_DIRECT
        op_bstart_std_cond = op == OP_BSTART_STD_COND
        op_bstart_call = op == OP_BSTART_STD_CALL
        op_c_bstop = op == OP_C_BSTOP
        op_fentry = op == OP_FENTRY
        op_fexit = op == OP_FEXIT
        op_fret_ra = op == OP_FRET_RA
        op_fret_stk = op == OP_FRET_STK
        op_is_macro = op_fentry | op_fexit | op_fret_ra | op_fret_stk

        op_is_start_marker = (
            op_c_bstart_std
            | op_c_bstart_cond
            | op_c_bstart_direct
            | op_bstart_std_fall
            | op_bstart_std_direct
            | op_bstart_std_cond
            | op_bstart_call
            | op_is_macro
        )
        op_is_boundary = op_is_start_marker | op_c_bstop

        br_is_cond = br_kind == BK_COND
        br_is_call = br_kind == BK_CALL
        br_is_ret = br_kind == BK_RET
        br_is_direct = br_kind == BK_DIRECT
        br_is_ind = br_kind == BK_IND
        br_is_icall = br_kind == BK_ICALL

        br_target_pc = br_base_pc + br_off
        if br_is_ret | br_is_ind | br_is_icall:
            br_target_pc = commit_tgt
        # Allow SETC.TGT to override fixed targets for direct/call/cond blocks.
        if ~(br_is_ret | br_is_ind | br_is_icall) & (commit_tgt != 0):
            br_target_pc = commit_tgt

        br_take = br_is_call | br_is_direct | br_is_ind | br_is_icall | (br_is_cond & commit_cond) | (br_is_ret & commit_cond)

        boundary_valid = do_wb & op_is_boundary
        take_event = boundary_valid & br_take

        fallthrough_pc = pc + len_bytes.zext(width=64)
        next_pc = br_take.select(br_target_pc, fallthrough_pc)
        target_pc = br_target_pc

        # Call blocks also set RA to the fall-through block start marker.
        # - Boundary is a start marker: fall-through is the boundary PC itself.
        # - Boundary is C.BSTOP: fall-through is the next PC after BSTOP.
        ra_fallthrough = pc
        if op_c_bstop:
            ra_fallthrough = pc + len_bytes.zext(width=64)

        ra_write_valid = take_event & (br_is_call | br_is_icall)
        ra_write_value = ra_fallthrough

        # --- Block control state updates ---
        # Commit-argument setters.
        op_c_setc_eq = op == OP_C_SETC_EQ
        op_c_setc_ne = op == OP_C_SETC_NE
        op_setc_geui = op == OP_SETC_GEUI
        op_setc_eq = op == OP_SETC_EQ
        op_setc_ne = op == OP_SETC_NE
        op_setc_and = op == OP_SETC_AND
        op_setc_or = op == OP_SETC_OR
        op_setc_lt = op == OP_SETC_LT
        op_setc_ltu = op == OP_SETC_LTU
        op_setc_ge = op == OP_SETC_GE
        op_setc_geu = op == OP_SETC_GEU
        op_setc_eqi = op == OP_SETC_EQI
        op_setc_nei = op == OP_SETC_NEI
        op_setc_andi = op == OP_SETC_ANDI
        op_setc_ori = op == OP_SETC_ORI
        op_setc_lti = op == OP_SETC_LTI
        op_setc_gei = op == OP_SETC_GEI
        op_setc_ltui = op == OP_SETC_LTUI
        op_c_setc_tgt = op == OP_C_SETC_TGT
        op_setc_any = (
            op_c_setc_eq
            | op_c_setc_ne
            | op_setc_geui
            | op_setc_eq
            | op_setc_ne
            | op_setc_and
            | op_setc_or
            | op_setc_lt
            | op_setc_ltu
            | op_setc_ge
            | op_setc_geu
            | op_setc_eqi
            | op_setc_nei
            | op_setc_andi
            | op_setc_ori
            | op_setc_lti
            | op_setc_gei
            | op_setc_ltui
        )

        commit_cond_next = commit_cond
        commit_tgt_next = commit_tgt
        # Clear commit args at any boundary marker (start of a new basic block or an explicit stop).
        if do_wb & op_is_boundary:
            commit_cond_next = 0
            commit_tgt_next = 0
        if do_wb & op_setc_any:
            commit_cond_next = value[0]
        if do_wb & op_c_setc_tgt:
            commit_tgt_next = value
            commit_cond_next = 1
        state.commit_cond.set(commit_cond_next)
        state.commit_tgt.set(commit_tgt_next)

        # Block-transition kind for the *current* block is set by the most recently executed start marker.
        # When a branch/call/ret is taken at a boundary, reset br_kind to FALL so the next marker doesn't
        # immediately re-commit the previous transition.
        br_kind_next = br_kind
        br_base_next = br_base_pc
        br_off_next = br_off

        # Default reset when leaving a block via any boundary.
        if do_wb & op_is_boundary & br_take:
            br_kind_next = BK_FALL
            br_base_next = pc
            br_off_next = 0

        enter_new_block = do_wb & op_is_start_marker & (~br_take)

        # C.BSTART COND,label: conditional transition with PC-relative target offset (imm << 1).
        if enter_new_block & op_c_bstart_cond:
            br_kind_next = BK_COND
            br_base_next = pc
            br_off_next = value

        # C.BSTART DIRECT,label: unconditional direct transition with PC-relative target offset (imm << 1).
        if enter_new_block & op_c_bstart_direct:
            br_kind_next = BK_DIRECT
            br_base_next = pc
            br_off_next = value

        # BSTART.STD FALL: start a fall-through block.
        if enter_new_block & op_bstart_std_fall:
            br_kind_next = BK_FALL
            br_base_next = pc
            br_off_next = 0

        # BSTART.STD DIRECT,label: unconditional direct transition.
        if enter_new_block & op_bstart_std_direct:
            br_kind_next = BK_DIRECT
            br_base_next = pc
            br_off_next = value

        # BSTART.STD COND,label: conditional transition.
        if enter_new_block & op_bstart_std_cond:
            br_kind_next = BK_COND
            br_base_next = pc
            br_off_next = value

        # BSTART.STD CALL,label: unconditional call transition to PC-relative target offset (imm << 1).
        if enter_new_block & op_bstart_call:
            br_kind_next = BK_CALL
            br_base_next = pc
            br_off_next = value

        # Macro blocks (FENTRY/FEXIT/FRET.*) are treated as standalone fall-through blocks.
        if enter_new_block & op_is_macro:
            br_kind_next = BK_FALL
            br_base_next = pc
            br_off_next = 0

        # C.BSTART.STD BrType: fall-through (BrType=1) or return (BrType=7).
        brtype = value[0:3]
        kind_from_brtype = BK_FALL
        if brtype == 2:
            kind_from_brtype = BK_DIRECT
        if brtype == 3:
            kind_from_brtype = BK_COND
        if brtype == 4:
            kind_from_brtype = BK_CALL
        if brtype == 5:
            kind_from_brtype = BK_IND
        if brtype == 6:
            kind_from_brtype = BK_ICALL
        if brtype == 7:
            kind_from_brtype = BK_RET
        if enter_new_block & op_c_bstart_std:
            br_kind_next = kind_from_brtype
            br_base_next = pc
            br_off_next = 0

        # Explicit block stop ends the current block without starting a new one.
        if do_wb & op_c_bstop:
            br_kind_next = BK_FALL
            br_base_next = pc
            br_off_next = 0

        state.br_kind.set(br_kind_next)
        state.br_base_pc.set(br_base_next)
        state.br_off.set(br_off_next)

    return WbControl(
        boundary_valid=boundary_valid,
        br_take=br_take,
        next_pc=next_pc,
        target_pc=target_pc,
        ra_write_valid=ra_write_valid,
        ra_write_value=ra_write_value,
    )
