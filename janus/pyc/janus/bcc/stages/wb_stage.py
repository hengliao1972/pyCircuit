from __future__ import annotations

from pycircuit import Circuit, Wire, jit_inline

from ..isa import (
    BK_CALL,
    BK_COND,
    BK_FALL,
    BK_RET,
    OP_BSTART_STD_CALL,
    OP_C_BSTART_COND,
    OP_C_BSTART_STD,
    OP_C_LWI,
    OP_C_SETC_EQ,
    OP_C_SETC_TGT,
    OP_C_SWI,
    OP_C_BSTOP,
    OP_SWI,
    REG_INVALID,
    ST_EX,
    ST_ID,
    ST_IF,
    ST_MEM,
    ST_WB,
)
from ..pipeline import CoreState, MemWbRegs, RegFiles
from ..regfile import commit_gpr, commit_stack, stack_next
from ..util import Consts


@jit_inline
def build_wb_stage(
    m: Circuit,
    *,
    do_wb: Wire,
    stage_is_if: Wire,
    stage_is_id: Wire,
    stage_is_ex: Wire,
    stage_is_mem: Wire,
    stage_is_wb: Wire,
    stop: Wire,
    halt_set: Wire,
    state: CoreState,
    memwb: MemWbRegs,
    rf: RegFiles,
    consts: Consts,
) -> None:
    with m.scope("WB"):
        c = m.const

        # Stage inputs.
        stage = state.stage.out()
        pc = state.pc.out()
        br_kind = state.br_kind.out()
        br_base_pc = state.br_base_pc.out()
        br_off = state.br_off.out()
        commit_cond = state.commit_cond.out()
        commit_tgt = state.commit_tgt.out()

        op = memwb.op.out()
        len_bytes = memwb.len_bytes.out()
        regdst = memwb.regdst.out()
        value = memwb.value.out()

        # Halt flag (latches even when stop inhibits do_wb).
        state.halted.set(consts.one1, when=halt_set)

        # --- BlockISA control flow ---
        op_c_bstart_std = op.eq(c(OP_C_BSTART_STD, width=6))
        op_c_bstart_cond = op.eq(c(OP_C_BSTART_COND, width=6))
        op_bstart_call = op.eq(c(OP_BSTART_STD_CALL, width=6))
        op_c_bstop = op.eq(c(OP_C_BSTOP, width=6))

        op_is_start_marker = op_c_bstart_std | op_c_bstart_cond | op_bstart_call
        op_is_boundary = op_is_start_marker | op_c_bstop

        br_is_fall = br_kind.eq(c(BK_FALL, width=2))
        br_is_cond = br_kind.eq(c(BK_COND, width=2))
        br_is_call = br_kind.eq(c(BK_CALL, width=2))
        br_is_ret = br_kind.eq(c(BK_RET, width=2))

        br_target_pc = br_base_pc + br_off
        br_target_pc = br_is_ret.select(commit_tgt, br_target_pc)

        br_take = br_is_call | br_is_ret | (br_is_cond & commit_cond)

        pc_inc = pc + len_bytes.zext(width=64)
        pc_next = op_is_boundary.select(br_take.select(br_target_pc, pc_inc), pc_inc)
        state.pc.set(pc_next, when=do_wb)

        # Stage machine: IF -> ID -> EX -> MEM -> WB -> IF, gated by stop.
        stage_seq = stage_is_if.select(c(ST_ID, width=3), stage)
        stage_seq = stage_is_id.select(c(ST_EX, width=3), stage_seq)
        stage_seq = stage_is_ex.select(c(ST_MEM, width=3), stage_seq)
        stage_seq = stage_is_mem.select(c(ST_WB, width=3), stage_seq)
        stage_seq = stage_is_wb.select(c(ST_IF, width=3), stage_seq)
        state.stage.set(stage_seq, when=~stop)

        # Cycle counter (always increments; TB stops on halt).
        state.cycles.set(state.cycles.out() + consts.one64)

        # --- Block control state updates ---
        # Commit-argument setters.
        op_c_setc_eq = op.eq(c(OP_C_SETC_EQ, width=6))
        op_c_setc_tgt = op.eq(c(OP_C_SETC_TGT, width=6))

        commit_cond_next = commit_cond
        commit_tgt_next = commit_tgt
        # Clear commit args at any boundary marker (start of a new basic block or an explicit stop).
        commit_cond_next = (do_wb & op_is_boundary).select(consts.zero1, commit_cond_next)
        commit_tgt_next = (do_wb & op_is_boundary).select(consts.zero64, commit_tgt_next)
        commit_cond_next = (do_wb & op_c_setc_eq).select(value.trunc(width=1), commit_cond_next)
        commit_tgt_next = (do_wb & op_c_setc_tgt).select(value, commit_tgt_next)
        state.commit_cond.set(commit_cond_next)
        state.commit_tgt.set(commit_tgt_next)

        # Block-transition kind for the *current* block is set by the most recently executed start marker.
        # When a branch/call/ret is taken at a boundary, reset br_kind to FALL so the next marker doesn't
        # immediately re-commit the previous transition.
        br_kind_next = br_kind
        br_base_next = br_base_pc
        br_off_next = br_off

        # Default reset when leaving a block via any boundary.
        br_kind_next = (do_wb & op_is_boundary & br_take).select(c(BK_FALL, width=2), br_kind_next)
        br_base_next = (do_wb & op_is_boundary & br_take).select(pc, br_base_next)
        br_off_next = (do_wb & op_is_boundary & br_take).select(consts.zero64, br_off_next)

        enter_new_block = do_wb & op_is_start_marker & (~br_take)

        # C.BSTART COND,label: conditional transition with PC-relative target offset (imm << 1).
        br_kind_next = (enter_new_block & op_c_bstart_cond).select(c(BK_COND, width=2), br_kind_next)
        br_base_next = (enter_new_block & op_c_bstart_cond).select(pc, br_base_next)
        br_off_next = (enter_new_block & op_c_bstart_cond).select(value, br_off_next)

        # BSTART.STD CALL,label: unconditional call transition to PC-relative target offset (imm << 1).
        br_kind_next = (enter_new_block & op_bstart_call).select(c(BK_CALL, width=2), br_kind_next)
        br_base_next = (enter_new_block & op_bstart_call).select(pc, br_base_next)
        br_off_next = (enter_new_block & op_bstart_call).select(value, br_off_next)

        # C.BSTART.STD BrType: fall-through (BrType=1) or return (BrType=7).
        brtype = value.trunc(width=3)
        kind_from_brtype = brtype.eq(c(7, width=3)).select(c(BK_RET, width=2), c(BK_FALL, width=2))
        br_kind_next = (enter_new_block & op_c_bstart_std).select(kind_from_brtype, br_kind_next)
        br_base_next = (enter_new_block & op_c_bstart_std).select(pc, br_base_next)
        br_off_next = (enter_new_block & op_c_bstart_std).select(consts.zero64, br_off_next)

        # Explicit block stop ends the current block without starting a new one.
        br_kind_next = (do_wb & op_c_bstop).select(c(BK_FALL, width=2), br_kind_next)
        br_base_next = (do_wb & op_c_bstop).select(pc, br_base_next)
        br_off_next = (do_wb & op_c_bstop).select(consts.zero64, br_off_next)

        state.br_kind.set(br_kind_next)
        state.br_base_pc.set(br_base_next)
        state.br_off.set(br_off_next)

        # Register writeback + T/U stacks.
        wb_is_store = op.eq(c(OP_SWI, width=6)) | op.eq(c(OP_C_SWI, width=6))
        do_reg_write = do_wb & (~wb_is_store) & (~regdst.eq(c(REG_INVALID, width=6)))

        do_clear_hands = do_wb & op_is_start_marker
        do_push_t = do_wb & op.eq(c(OP_C_LWI, width=6))

        do_push_t = do_push_t | (do_reg_write & regdst.eq(c(31, width=6)))
        do_push_u = do_reg_write & regdst.eq(c(30, width=6))

        commit_gpr(m, rf.gpr, do_reg_write=do_reg_write, regdst=memwb.regdst, value=memwb.value)

        t_next = stack_next(m, rf.t, do_push=do_push_t, do_clear=do_clear_hands, value=memwb.value)
        u_next = stack_next(m, rf.u, do_push=do_push_u, do_clear=do_clear_hands, value=memwb.value)
        commit_stack(m, rf.t, t_next)
        commit_stack(m, rf.u, u_next)
