from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareDomain, CycleAwareSignal, mux

from examples.linx_cpu_pyc.isa import (
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
)
from examples.linx_cpu_pyc_cycle_aware.pipeline import CoreState, RegFiles
from examples.linx_cpu_pyc_cycle_aware.regfile import commit_gpr, commit_stack, stack_next


def wb_stage_updates(
    m: CycleAwareCircuit,
    *,
    state: CoreState,
    rf: RegFiles,
    op: CycleAwareSignal,
    len_bytes: CycleAwareSignal,
    pc: CycleAwareSignal,
    regdst: CycleAwareSignal,
    value: CycleAwareSignal,
    do_wb_arch: CycleAwareSignal,
    domain: CycleAwareDomain,
) -> dict:
    """架构状态与 RF 更新 + 返回 flush / redirect_pc 用于流水线冲刷。"""
    c = lambda v, w: domain.const(v, width=w)

    br_kind = state.br_kind
    br_base_pc = state.br_base_pc
    br_off = state.br_off
    commit_cond = state.commit_cond
    commit_tgt = state.commit_tgt

    op_c_bstart_std = op.eq(OP_C_BSTART_STD)
    op_c_bstart_cond = op.eq(OP_C_BSTART_COND)
    op_bstart_call = op.eq(OP_BSTART_STD_CALL)
    op_c_bstop = op.eq(OP_C_BSTOP)
    op_is_start_marker = op_c_bstart_std | op_c_bstart_cond | op_bstart_call
    op_is_boundary = op_is_start_marker | op_c_bstop

    br_is_cond = br_kind.eq(BK_COND)
    br_is_call = br_kind.eq(BK_CALL)
    br_is_ret = br_kind.eq(BK_RET)
    br_base_eff = mux(br_base_pc.eq(c(0, 64)), pc, br_base_pc)
    br_target_pc_base = br_base_eff + br_off
    br_target_pc = mux(br_is_ret, commit_tgt, br_target_pc_base)
    br_take = br_is_call | br_is_ret | (br_is_cond & commit_cond)

    pc_inc = pc + len_bytes
    pc_next = mux(op_is_boundary & br_take, br_target_pc, pc_inc)
    state.pc.set(pc_next, when=do_wb_arch)

    # --- Pipeline flush: taken branch at block boundary ---
    flush = do_wb_arch & op_is_boundary & br_take

    commit_cond_cleared = mux(do_wb_arch & op_is_boundary, c(0, 1), commit_cond)
    commit_tgt_cleared = mux(do_wb_arch & op_is_boundary, c(0, 64), commit_tgt)
    op_c_setc_eq = op.eq(OP_C_SETC_EQ)
    op_c_setc_tgt = op.eq(OP_C_SETC_TGT)
    commit_cond_next = mux(do_wb_arch & op_c_setc_eq, value[0], commit_cond_cleared)
    commit_tgt_next = mux(do_wb_arch & op_c_setc_tgt, value, commit_tgt_cleared)
    state.commit_cond.set(commit_cond_next)
    state.commit_tgt.set(commit_tgt_next)

    leave_block = do_wb_arch & op_is_boundary & br_take
    br_base_on_leave = mux(pc.eq(c(0, 64)), br_target_pc, pc)
    br_kind_base = mux(leave_block, c(BK_FALL, 2), br_kind)
    br_base_base = mux(leave_block, br_base_on_leave, br_base_pc)
    br_off_base = mux(leave_block, c(0, 64), br_off)
    enter_new_block = do_wb_arch & op_is_start_marker & (~br_take)

    br_kind_next = mux(enter_new_block & op_c_bstart_cond, c(BK_COND, 2), br_kind_base)
    br_base_next = mux(enter_new_block & op_c_bstart_cond, pc, br_base_base)
    br_off_next = mux(enter_new_block & op_c_bstart_cond, value, br_off_base)
    br_kind_next = mux(enter_new_block & op_bstart_call, c(BK_CALL, 2), br_kind_next)
    br_base_next = mux(enter_new_block & op_bstart_call, pc, br_base_next)
    br_off_next = mux(enter_new_block & op_bstart_call, value, br_off_next)
    brtype = value[0:3]
    kind_from_brtype = mux(brtype.eq(7), c(BK_RET, 2), c(BK_FALL, 2))
    br_kind_next = mux(enter_new_block & op_c_bstart_std, kind_from_brtype, br_kind_next)
    br_base_next = mux(enter_new_block & op_c_bstart_std, pc, br_base_next)
    br_off_next = mux(enter_new_block & op_c_bstart_std, c(0, 64), br_off_next)
    br_kind_next = mux(do_wb_arch & op_c_bstop, c(BK_FALL, 2), br_kind_next)
    br_base_next = mux(do_wb_arch & op_c_bstop, pc, br_base_next)
    br_off_next = mux(do_wb_arch & op_c_bstop, c(0, 64), br_off_next)

    state.br_kind.set(br_kind_next, when=do_wb_arch)
    state.br_base_pc.set(br_base_next, when=do_wb_arch)
    state.br_off.set(br_off_next, when=do_wb_arch)

    wb_is_store = op.eq(OP_SWI) | op.eq(OP_C_SWI)
    do_reg_write = do_wb_arch & (~wb_is_store) & regdst.ne(REG_INVALID)
    do_clear_hands = do_wb_arch & op_is_start_marker
    do_push_t = do_wb_arch & op.eq(OP_C_LWI)
    do_push_t = do_push_t | (do_reg_write & regdst.eq(31))
    do_push_u = do_reg_write & regdst.eq(30)
    commit_gpr(m, domain, rf.gpr, do_reg_write=do_reg_write, regdst=regdst, value=value)
    t_next = stack_next(m, domain, rf.t, do_push=do_push_t, do_clear=do_clear_hands, value=value)
    u_next = stack_next(m, domain, rf.u, do_push=do_push_u, do_clear=do_clear_hands, value=value)
    commit_stack(m, rf.t, t_next)
    commit_stack(m, rf.u, u_next)

    return {"flush": flush, "redirect_pc": pc_next}
