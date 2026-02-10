from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit
from pycircuit.dsl import Signal

from .exec import exec_uop_comb
from ..isa import (
    BK_CALL,
    BK_COND,
    BK_DIRECT,
    BK_FALL,
    BK_ICALL,
    BK_IND,
    BK_RET,
    OP_BSTART_STD_COND,
    OP_BSTART_STD_CALL,
    OP_BSTART_STD_DIRECT,
    OP_BSTART_STD_FALL,
    OP_C_BSTART_COND,
    OP_C_BSTART_DIRECT,
    OP_C_BSTART_STD,
    OP_C_BSTOP,
    OP_C_LDI,
    OP_C_LWI,
    OP_C_SETC_NE,
    OP_C_SDI,
    OP_C_SWI,
    OP_C_SETC_EQ,
    OP_C_SETC_TGT,
    OP_EBREAK,
    OP_FENTRY,
    OP_FEXIT,
    OP_FRET_RA,
    OP_FRET_STK,
    OP_INVALID,
    OP_HL_LB_PCR,
    OP_HL_LBU_PCR,
    OP_HL_LD_PCR,
    OP_HL_LH_PCR,
    OP_HL_LHU_PCR,
    OP_HL_LW_PCR,
    OP_HL_LWU_PCR,
    OP_HL_SB_PCR,
    OP_HL_SD_PCR,
    OP_HL_SH_PCR,
    OP_HL_SW_PCR,
    OP_LB,
    OP_LBI,
    OP_LBU,
    OP_LBUI,
    OP_LD,
    OP_LH,
    OP_LHI,
    OP_LHU,
    OP_LHUI,
    OP_LW,
    OP_LWU,
    OP_LWUI,
    OP_SB,
    OP_SBI,
    OP_SD,
    OP_SH,
    OP_SHI,
    OP_SW,
    OP_LWI,
    OP_LDI,
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
    OP_SDI,
    OP_SWI,
    REG_INVALID,
)
from ..util import make_consts
from .dec1 import decode_f4_bundle
from .helpers import alloc_from_free_mask, mask_bit, mux_by_uindex, onehot_from_tag
from .params import OooParams
from .state import make_core_ctrl_regs, make_ifu_regs, make_iq_regs, make_prf, make_rename_regs, make_rob_regs


@dataclass(frozen=True)
class BccOooExports:
    clk: Signal
    rst: Signal
    block_cmd_valid: Signal
    block_cmd_kind: Signal
    block_cmd_payload: Signal
    block_cmd_tile: Signal
    block_cmd_tag: Signal
    cycles: Signal
    halted: Signal


def build_bcc_ooo(m: Circuit, *, mem_bytes: int, params: OooParams | None = None) -> BccOooExports:
    p = params or OooParams()

    clk = m.clock("clk")
    rst = m.reset("rst")

    boot_pc = m.input("boot_pc", width=64)
    boot_sp = m.input("boot_sp", width=64)

    # Host loader write port (used by PS/PL FPGA bring-up). This is intended to
    # be driven while the core is held in reset.
    host_wvalid = m.input("host_wvalid", width=1)
    host_waddr = m.input("host_waddr", width=64)
    host_wdata = m.input("host_wdata", width=64)
    host_wstrb = m.input("host_wstrb", width=8)

    c = m.const
    consts = make_consts(m)

    def op_is(op, *codes: int):
        v = consts.zero1
        for code in codes:
            v = v | op.eq(c(code, width=12))
        return v

    tag0 = c(0, width=p.ptag_w)

    # --- core state (architectural) ---
    state = make_core_ctrl_regs(m, clk, rst, boot_pc=boot_pc, consts=consts)

    base_can_run = (~state.halted.out()) & (~state.flush_pending.out())
    do_flush = state.flush_pending.out()

    # --- IFU (bring-up): single-entry fetch queue (F4 bundle) ---
    ifu = make_ifu_regs(m, clk, rst, boot_pc=boot_pc, consts=consts)

    # --- physical register file (PRF) ---
    prf = make_prf(m, clk, rst, boot_sp=boot_sp, consts=consts, p=p)

    # --- rename state ---
    ren = make_rename_regs(m, clk, rst, consts=consts, p=p)

    # --- ROB (in-order commit) ---
    rob = make_rob_regs(m, clk, rst, consts=consts, p=p)

    # --- issue queues (bring-up split) ---
    iq_alu = make_iq_regs(m, clk, rst, consts=consts, p=p, name="iq_alu")
    iq_bru = make_iq_regs(m, clk, rst, consts=consts, p=p, name="iq_bru")
    iq_lsu = make_iq_regs(m, clk, rst, consts=consts, p=p, name="iq_lsu")

    # --- commit selection (up to commit_w, stop on redirect/store/halt) ---
    commit_idxs = []
    rob_valids = []
    rob_dones = []
    rob_ops = []
    rob_lens = []
    rob_dst_kinds = []
    rob_dst_aregs = []
    rob_pdsts = []
    rob_values = []
    rob_is_stores = []
    rob_st_addrs = []
    rob_st_datas = []
    rob_st_sizes = []
    rob_macro_begins = []
    rob_macro_ends = []
    for slot in range(p.commit_w):
        idx = rob.head.out() + c(slot, width=p.rob_w)
        commit_idxs.append(idx)
        rob_valids.append(mux_by_uindex(m, idx=idx, items=rob.valid, default=consts.zero1))
        rob_dones.append(mux_by_uindex(m, idx=idx, items=rob.done, default=consts.zero1))
        rob_ops.append(mux_by_uindex(m, idx=idx, items=rob.op, default=c(0, width=12)))
        rob_lens.append(mux_by_uindex(m, idx=idx, items=rob.len_bytes, default=consts.zero3))
        rob_dst_kinds.append(mux_by_uindex(m, idx=idx, items=rob.dst_kind, default=c(0, width=2)))
        rob_dst_aregs.append(mux_by_uindex(m, idx=idx, items=rob.dst_areg, default=c(REG_INVALID, width=6)))
        rob_pdsts.append(mux_by_uindex(m, idx=idx, items=rob.pdst, default=tag0))
        rob_values.append(mux_by_uindex(m, idx=idx, items=rob.value, default=consts.zero64))
        rob_is_stores.append(mux_by_uindex(m, idx=idx, items=rob.is_store, default=consts.zero1))
        rob_st_addrs.append(mux_by_uindex(m, idx=idx, items=rob.store_addr, default=consts.zero64))
        rob_st_datas.append(mux_by_uindex(m, idx=idx, items=rob.store_data, default=consts.zero64))
        rob_st_sizes.append(mux_by_uindex(m, idx=idx, items=rob.store_size, default=consts.zero4))
        rob_macro_begins.append(mux_by_uindex(m, idx=idx, items=rob.macro_begin, default=c(0, width=6)))
        rob_macro_ends.append(mux_by_uindex(m, idx=idx, items=rob.macro_end, default=c(0, width=6)))

    head_op = rob_ops[0]
    head_len = rob_lens[0]
    head_dst_kind = rob_dst_kinds[0]
    head_dst_areg = rob_dst_aregs[0]
    head_pdst = rob_pdsts[0]
    head_value = rob_values[0]
    head_is_store = rob_is_stores[0]
    head_st_addr = rob_st_addrs[0]
    head_st_data = rob_st_datas[0]
    head_st_size = rob_st_sizes[0]
    head_macro_begin = rob_macro_begins[0]
    head_macro_end = rob_macro_ends[0]

    # Commit-time branch/control decisions (BlockISA markers) for the head.
    head_is_macro = op_is(head_op, OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK)
    head_is_start_marker = (
        op_is(
            head_op,
            OP_C_BSTART_STD,
            OP_C_BSTART_COND,
            OP_C_BSTART_DIRECT,
            OP_BSTART_STD_FALL,
            OP_BSTART_STD_DIRECT,
            OP_BSTART_STD_COND,
            OP_BSTART_STD_CALL,
        )
        | head_is_macro
    )
    head_is_boundary = head_is_start_marker | op_is(head_op, OP_C_BSTOP)

    # Boundary markers are skipped when the previous block takes a control-flow
    # transition at that boundary. Skipped markers must not trigger template
    # macro expansion.
    br_kind_head = state.br_kind.out()
    br_is_cond_head = br_kind_head.eq(c(BK_COND, width=3))
    br_is_call_head = br_kind_head.eq(c(BK_CALL, width=3))
    br_is_ret_head = br_kind_head.eq(c(BK_RET, width=3))
    br_is_direct_head = br_kind_head.eq(c(BK_DIRECT, width=3))
    br_is_ind_head = br_kind_head.eq(c(BK_IND, width=3))
    br_is_icall_head = br_kind_head.eq(c(BK_ICALL, width=3))
    head_br_take = (
        br_is_call_head
        | br_is_direct_head
        | br_is_ind_head
        | br_is_icall_head
        | (br_is_cond_head & state.commit_cond.out())
        | (br_is_ret_head & state.commit_cond.out())
    )
    head_skip = head_is_boundary & head_br_take

    # Template macro blocks (FENTRY/FEXIT/FRET.*) are expanded by a small
    # microcode engine. Stall the normal pipeline in the cycle we start the
    # macro, and while the macro is active.
    macro_start = (
        base_can_run
        & (~state.macro_active.out())
        & (~state.macro_wait_commit.out())
        & head_is_macro
        & (~head_skip)
        & rob_valids[0]
        & rob_dones[0]
    )
    macro_block = state.macro_active.out() | macro_start

    can_run = base_can_run & (~macro_block)

    # Return target for FRET.* (via RA, possibly restored by the macro engine).
    ret_ra_tag = ren.cmap[10].out()
    ret_ra_val = mux_by_uindex(m, idx=ret_ra_tag, items=prf, default=consts.zero64)

    commit_allow = consts.one1
    commit_fires = []
    commit_pcs = []
    commit_enter_new_blocks = []

    commit_count = c(0, width=3)

    redirect_valid = consts.zero1
    redirect_pc = state.pc.out()

    commit_store_fire = consts.zero1
    commit_store_addr = consts.zero64
    commit_store_data = consts.zero64
    commit_store_size = consts.zero4
    ra_write_fire = consts.zero1
    ra_write_value = consts.zero64
    ra_tag_live = ren.cmap[10].out()
    ra_write_tag = ra_tag_live

    pc_live = state.pc.out()
    commit_cond_live = state.commit_cond.out()
    commit_tgt_live = state.commit_tgt.out()
    br_kind_live = state.br_kind.out()
    br_base_live = state.br_base_pc.out()
    br_off_live = state.br_off.out()

    for slot in range(p.commit_w):
        pc_this = pc_live
        commit_pcs.append(pc_this)
        op = rob_ops[slot]
        ln = rob_lens[slot]
        val = rob_values[slot]

        is_macro = op_is(op, OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK)
        is_start_marker = (
            op_is(
                op,
                OP_C_BSTART_STD,
                OP_C_BSTART_COND,
                OP_C_BSTART_DIRECT,
                OP_BSTART_STD_FALL,
                OP_BSTART_STD_DIRECT,
                OP_BSTART_STD_COND,
                OP_BSTART_STD_CALL,
            )
            | is_macro
        )
        is_boundary = is_start_marker | op_is(op, OP_C_BSTOP)

        br_is_fall = br_kind_live.eq(c(BK_FALL, width=3))
        br_is_cond = br_kind_live.eq(c(BK_COND, width=3))
        br_is_call = br_kind_live.eq(c(BK_CALL, width=3))
        br_is_ret = br_kind_live.eq(c(BK_RET, width=3))
        br_is_direct = br_kind_live.eq(c(BK_DIRECT, width=3))
        br_is_ind = br_kind_live.eq(c(BK_IND, width=3))
        br_is_icall = br_kind_live.eq(c(BK_ICALL, width=3))

        br_target = br_base_live + br_off_live
        # Dynamic target for RET/IND/ICALL blocks comes from commit_tgt.
        br_target = (br_is_ret | br_is_ind | br_is_icall).select(commit_tgt_live, br_target)
        # Allow SETC.TGT to override fixed targets for DIRECT/CALL/COND blocks.
        br_target = (~(br_is_ret | br_is_ind | br_is_icall) & (~commit_tgt_live.eq(consts.zero64))).select(commit_tgt_live, br_target)

        br_take = (
            br_is_call
            | br_is_direct
            | br_is_ind
            | br_is_icall
            | (br_is_cond & commit_cond_live)
            | (br_is_ret & commit_cond_live)
        )

        pc_inc = pc_this + ln.zext(width=64)
        pc_next = is_boundary.select(br_take.select(br_target, pc_inc), pc_inc)

        fire = can_run & commit_allow & rob_valids[slot] & rob_dones[slot]

        # Template macro blocks (FENTRY/FEXIT/FRET.*) must reach the head of the
        # ROB so the macro microcode engine can run before the macro commits.
        # With commit_w>1, a macro could otherwise commit in the same cycle as
        # an older non-macro (slot>0) and skip the required save/restore.
        if slot != 0:
            fire = fire & (~is_macro)

        # Stop commit on redirect, store, or halt.
        is_halt = op_is(op, OP_EBREAK, OP_INVALID)
        redirect = fire & is_boundary & br_take

        # FRET.* are explicit control-flow ops (return via RA). They behave like
        # a taken boundary when the marker is entered (i.e., not skipped by a
        # prior taken branch at this boundary).
        is_fret = op_is(op, OP_FRET_RA, OP_FRET_STK)
        fret_redirect = fire & is_fret & (~redirect)
        pc_next = fret_redirect.select(ret_ra_val, pc_next)
        redirect = redirect | fret_redirect

        # Call/ICALL blocks also set RA to the fall-through block start marker.
        # - Boundary is a start marker: fall-through is the boundary PC itself.
        # - Boundary is C.BSTOP: fall-through is the next PC after BSTOP.
        ra_fallthrough = op_is(op, OP_C_BSTOP).select(pc_inc, pc_this)
        ra_write = redirect & (br_is_call | br_is_icall)
        ra_write_fire = ra_write.select(consts.one1, ra_write_fire)
        ra_write_value = ra_write.select(ra_fallthrough, ra_write_value)

        store_commit = fire & rob_is_stores[slot]
        stop = redirect | store_commit | (fire & is_halt)

        commit_fires.append(fire)
        commit_count = commit_count + fire.zext(width=3)

        # Track the committed RA mapping across commit slots so CALL/ICALL can
        # update the correct physical reg even when RA is renamed by an older
        # instruction in the same cycle.
        ra_map_write = fire & rob_dst_kinds[slot].eq(c(1, width=2)) & rob_dst_aregs[slot].eq(c(10, width=6))
        ra_tag_live = ra_map_write.select(rob_pdsts[slot], ra_tag_live)

        redirect_valid = redirect.select(consts.one1, redirect_valid)
        redirect_pc = redirect.select(pc_next, redirect_pc)

        commit_store_fire = store_commit.select(consts.one1, commit_store_fire)
        commit_store_addr = store_commit.select(rob_st_addrs[slot], commit_store_addr)
        commit_store_data = store_commit.select(rob_st_datas[slot], commit_store_data)
        commit_store_size = store_commit.select(rob_st_sizes[slot], commit_store_size)

        ra_write_tag = ra_write.select(ra_tag_live, ra_write_tag)

        # --- sequential architectural state updates across commit slots ---
        op_setc_any = op_is(
            op,
            OP_C_SETC_EQ,
            OP_C_SETC_NE,
            OP_SETC_GEUI,
            OP_SETC_EQ,
            OP_SETC_NE,
            OP_SETC_AND,
            OP_SETC_OR,
            OP_SETC_LT,
            OP_SETC_LTU,
            OP_SETC_GE,
            OP_SETC_GEU,
            OP_SETC_EQI,
            OP_SETC_NEI,
            OP_SETC_ANDI,
            OP_SETC_ORI,
            OP_SETC_LTI,
            OP_SETC_GEI,
            OP_SETC_LTUI,
        )
        op_setc_tgt = op_is(op, OP_C_SETC_TGT)
        commit_cond_live = (fire & is_boundary).select(consts.zero1, commit_cond_live)
        commit_tgt_live = (fire & is_boundary).select(consts.zero64, commit_tgt_live)
        commit_cond_live = (fire & op_setc_any).select(val.trunc(width=1), commit_cond_live)
        commit_tgt_live = (fire & op_setc_tgt).select(val, commit_tgt_live)
        commit_cond_live = (fire & op_setc_tgt).select(consts.one1, commit_cond_live)

        br_kind_live = (fire & is_boundary & br_take).select(c(BK_FALL, width=3), br_kind_live)
        br_base_live = (fire & is_boundary & br_take).select(pc_this, br_base_live)
        br_off_live = (fire & is_boundary & br_take).select(consts.zero64, br_off_live)

        enter_new_block = fire & is_start_marker & (~br_take)
        commit_enter_new_blocks.append(enter_new_block)

        is_bstart_cond = op_is(op, OP_C_BSTART_COND)
        br_kind_live = (enter_new_block & is_bstart_cond).select(c(BK_COND, width=3), br_kind_live)
        br_base_live = (enter_new_block & is_bstart_cond).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_bstart_cond).select(val, br_off_live)

        is_bstart_direct = op_is(op, OP_C_BSTART_DIRECT)
        br_kind_live = (enter_new_block & is_bstart_direct).select(c(BK_DIRECT, width=3), br_kind_live)
        br_base_live = (enter_new_block & is_bstart_direct).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_bstart_direct).select(val, br_off_live)

        is_bstart_std_fall = op_is(op, OP_BSTART_STD_FALL)
        br_kind_live = (enter_new_block & is_bstart_std_fall).select(c(BK_FALL, width=3), br_kind_live)
        br_base_live = (enter_new_block & is_bstart_std_fall).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_bstart_std_fall).select(consts.zero64, br_off_live)

        is_bstart_std_direct = op_is(op, OP_BSTART_STD_DIRECT)
        br_kind_live = (enter_new_block & is_bstart_std_direct).select(c(BK_DIRECT, width=3), br_kind_live)
        br_base_live = (enter_new_block & is_bstart_std_direct).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_bstart_std_direct).select(val, br_off_live)

        is_bstart_std_cond = op_is(op, OP_BSTART_STD_COND)
        br_kind_live = (enter_new_block & is_bstart_std_cond).select(c(BK_COND, width=3), br_kind_live)
        br_base_live = (enter_new_block & is_bstart_std_cond).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_bstart_std_cond).select(val, br_off_live)

        is_bstart_call = op_is(op, OP_BSTART_STD_CALL)
        br_kind_live = (enter_new_block & is_bstart_call).select(c(BK_CALL, width=3), br_kind_live)
        br_base_live = (enter_new_block & is_bstart_call).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_bstart_call).select(val, br_off_live)

        brtype = val.trunc(width=3)
        kind_from_brtype = c(BK_FALL, width=3)
        kind_from_brtype = brtype.eq(c(2, width=3)).select(c(BK_DIRECT, width=3), kind_from_brtype)
        kind_from_brtype = brtype.eq(c(3, width=3)).select(c(BK_COND, width=3), kind_from_brtype)
        kind_from_brtype = brtype.eq(c(4, width=3)).select(c(BK_CALL, width=3), kind_from_brtype)
        kind_from_brtype = brtype.eq(c(5, width=3)).select(c(BK_IND, width=3), kind_from_brtype)
        kind_from_brtype = brtype.eq(c(6, width=3)).select(c(BK_ICALL, width=3), kind_from_brtype)
        kind_from_brtype = brtype.eq(c(7, width=3)).select(c(BK_RET, width=3), kind_from_brtype)
        is_bstart_std = op_is(op, OP_C_BSTART_STD)
        br_kind_live = (enter_new_block & is_bstart_std).select(kind_from_brtype, br_kind_live)
        br_base_live = (enter_new_block & is_bstart_std).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_bstart_std).select(consts.zero64, br_off_live)

        # Macro blocks (FENTRY/FEXIT/FRET.*) are treated as standalone fall-through blocks.
        br_kind_live = (enter_new_block & is_macro).select(c(BK_FALL, width=3), br_kind_live)
        br_base_live = (enter_new_block & is_macro).select(pc_this, br_base_live)
        br_off_live = (enter_new_block & is_macro).select(consts.zero64, br_off_live)

        is_bstop = op_is(op, OP_C_BSTOP)
        br_kind_live = (fire & is_bstop).select(c(BK_FALL, width=3), br_kind_live)
        br_base_live = (fire & is_bstop).select(pc_this, br_base_live)
        br_off_live = (fire & is_bstop).select(consts.zero64, br_off_live)

        pc_live = fire.select(pc_next, pc_live)

        commit_allow = commit_allow & fire & (~stop)

    commit_fire = commit_fires[0]
    commit_redirect = redirect_valid

    # --- store tracking (for conservative load ordering) ---
    store_pending = consts.zero1
    for i in range(p.rob_depth):
        store_pending = store_pending | (rob.valid[i].out() & rob.is_store[i].out())

    # --- issue selection (up to issue_w ready IQ entries) ---
    #
    # Conservative ordering for loads: do not issue a load if there is an older
    # store still in the ROB. This is evaluated per-IQ entry and excluded from
    # the pick candidates to avoid head-of-line blocking.
    sub_head = (~rob.head.out()) + c(1, width=p.rob_w)

    # --- IQ ready/can-issue ---
    alu_can_issue: list = []
    for i in range(p.iq_depth):
        v = iq_alu.valid[i].out()
        sl = iq_alu.srcl[i].out()
        sr = iq_alu.srcr[i].out()
        sp = iq_alu.srcp[i].out()
        sl_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sl, width=p.pregs)
        sr_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sr, width=p.pregs)
        sp_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sp, width=p.pregs)
        alu_can_issue.append(v & sl_rdy & sr_rdy & sp_rdy)

    bru_can_issue: list = []
    for i in range(p.iq_depth):
        v = iq_bru.valid[i].out()
        sl = iq_bru.srcl[i].out()
        sr = iq_bru.srcr[i].out()
        sp = iq_bru.srcp[i].out()
        sl_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sl, width=p.pregs)
        sr_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sr, width=p.pregs)
        sp_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sp, width=p.pregs)
        bru_can_issue.append(v & sl_rdy & sr_rdy & sp_rdy)

    lsu_is_load: list = []
    lsu_is_store: list = []
    lsu_older_store_pending: list = []
    lsu_can_issue: list = []
    for i in range(p.iq_depth):
        v = iq_lsu.valid[i].out()
        sl = iq_lsu.srcl[i].out()
        sr = iq_lsu.srcr[i].out()
        sp = iq_lsu.srcp[i].out()
        sl_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sl, width=p.pregs)
        sr_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sr, width=p.pregs)
        sp_rdy = mask_bit(m, mask=ren.ready_mask.out(), idx=sp, width=p.pregs)
        ready = v & sl_rdy & sr_rdy & sp_rdy

        op_i = iq_lsu.op[i].out()
        # Bring-up conservative ordering: do not issue *any* load when there is
        # an older store in the ROB. This avoids load-before-store hazards in
        # the simplified single-cycle LSU (no forwarding/replay).
        is_load_i = op_is(
            op_i,
            OP_LWI,
            OP_C_LWI,
            OP_LBI,
            OP_LBUI,
            OP_LHI,
            OP_LHUI,
            OP_LWUI,
            OP_LDI,
            OP_C_LDI,
            OP_LB,
            OP_LBU,
            OP_LH,
            OP_LHU,
            OP_LW,
            OP_LWU,
            OP_LD,
            OP_HL_LB_PCR,
            OP_HL_LBU_PCR,
            OP_HL_LH_PCR,
            OP_HL_LHU_PCR,
            OP_HL_LW_PCR,
            OP_HL_LWU_PCR,
            OP_HL_LD_PCR,
        )
        is_store_i = op_is(
            op_i,
            OP_SBI,
            OP_SHI,
            OP_SWI,
            OP_C_SWI,
            OP_SDI,
            OP_C_SDI,
            OP_SB,
            OP_SH,
            OP_SW,
            OP_SD,
            OP_HL_SB_PCR,
            OP_HL_SH_PCR,
            OP_HL_SW_PCR,
            OP_HL_SD_PCR,
        )

        uop_rob_i = iq_lsu.rob[i].out()
        uop_dist = uop_rob_i + sub_head  # (uop_rob - head) mod 2^ROB_W
        older_store = consts.zero1
        for j in range(p.rob_depth):
            idx = c(j, width=p.rob_w)
            dist = idx + sub_head  # (idx - head) mod 2^ROB_W
            is_older = dist.ult(uop_dist)
            older_store = older_store | (rob.valid[j].out() & rob.is_store[j].out() & is_older)

        ok = ready & (~(is_load_i & older_store))

        lsu_is_load.append(is_load_i)
        lsu_is_store.append(is_store_i)
        lsu_older_store_pending.append(older_store)
        lsu_can_issue.append(ok)

    # --- issue selection per IQ ---
    alu_issue_valids = []
    alu_issue_idxs = []
    for slot in range(p.alu_w):
        v = consts.zero1
        idx = c(0, width=p.iq_w)
        for i in range(p.iq_depth):
            cidx = c(i, width=p.iq_w)
            exclude = consts.zero1
            for prev in range(slot):
                exclude = exclude | (alu_issue_valids[prev] & alu_issue_idxs[prev].eq(cidx))
            cand = alu_can_issue[i] & (~exclude)
            take = cand & (~v)
            v = take.select(consts.one1, v)
            idx = take.select(cidx, idx)
        alu_issue_valids.append(v)
        alu_issue_idxs.append(idx)

    bru_issue_valids = []
    bru_issue_idxs = []
    for slot in range(p.bru_w):
        v = consts.zero1
        idx = c(0, width=p.iq_w)
        for i in range(p.iq_depth):
            cidx = c(i, width=p.iq_w)
            exclude = consts.zero1
            for prev in range(slot):
                exclude = exclude | (bru_issue_valids[prev] & bru_issue_idxs[prev].eq(cidx))
            cand = bru_can_issue[i] & (~exclude)
            take = cand & (~v)
            v = take.select(consts.one1, v)
            idx = take.select(cidx, idx)
        bru_issue_valids.append(v)
        bru_issue_idxs.append(idx)

    lsu_issue_valids = []
    lsu_issue_idxs = []
    for slot in range(p.lsu_w):
        v = consts.zero1
        idx = c(0, width=p.iq_w)
        for i in range(p.iq_depth):
            cidx = c(i, width=p.iq_w)
            exclude = consts.zero1
            for prev in range(slot):
                exclude = exclude | (lsu_issue_valids[prev] & lsu_issue_idxs[prev].eq(cidx))
            cand = lsu_can_issue[i] & (~exclude)
            take = cand & (~v)
            v = take.select(consts.one1, v)
            idx = take.select(cidx, idx)
        lsu_issue_valids.append(v)
        lsu_issue_idxs.append(idx)

    # Slot ordering: LSU, BRU, ALU (stable debug lane0 = LSU).
    issue_valids = lsu_issue_valids + bru_issue_valids + alu_issue_valids
    issue_idxs = lsu_issue_idxs + bru_issue_idxs + alu_issue_idxs
    issue_iqs = ([iq_lsu] * p.lsu_w) + ([iq_bru] * p.bru_w) + ([iq_alu] * p.alu_w)

    issue_fires = []
    for slot in range(p.issue_w):
        issue_fires.append(can_run & (~commit_redirect) & issue_valids[slot])

    # Lane0 retained for trace/debug outputs.
    issue_fire = issue_fires[0]
    issue_idx = issue_idxs[0]

    uop_robs = []
    uop_ops = []
    uop_pcs = []
    uop_imms = []
    uop_sls = []
    uop_srs = []
    uop_srcr_types = []
    uop_shamts = []
    uop_sps = []
    uop_pdsts = []
    uop_has_dsts = []
    for slot in range(p.issue_w):
        iq = issue_iqs[slot]
        idx = issue_idxs[slot]
        uop_robs.append(mux_by_uindex(m, idx=idx, items=iq.rob, default=c(0, width=p.rob_w)))
        uop_ops.append(mux_by_uindex(m, idx=idx, items=iq.op, default=c(0, width=12)))
        uop_pcs.append(mux_by_uindex(m, idx=idx, items=iq.pc, default=consts.zero64))
        uop_imms.append(mux_by_uindex(m, idx=idx, items=iq.imm, default=consts.zero64))
        uop_sls.append(mux_by_uindex(m, idx=idx, items=iq.srcl, default=tag0))
        uop_srs.append(mux_by_uindex(m, idx=idx, items=iq.srcr, default=tag0))
        uop_srcr_types.append(mux_by_uindex(m, idx=idx, items=iq.srcr_type, default=c(0, width=2)))
        uop_shamts.append(mux_by_uindex(m, idx=idx, items=iq.shamt, default=consts.zero6))
        uop_sps.append(mux_by_uindex(m, idx=idx, items=iq.srcp, default=tag0))
        uop_pdsts.append(mux_by_uindex(m, idx=idx, items=iq.pdst, default=tag0))
        uop_has_dsts.append(mux_by_uindex(m, idx=idx, items=iq.has_dst, default=consts.zero1))

    # Lane0 named views (stable trace hooks).
    uop_rob = uop_robs[0]
    uop_op = uop_ops[0]
    uop_pc = uop_pcs[0]
    uop_imm = uop_imms[0]
    uop_sl = uop_sls[0]
    uop_sr = uop_srs[0]
    uop_sp = uop_sps[0]
    uop_pdst = uop_pdsts[0]
    uop_has_dst = uop_has_dsts[0]

    # PRF reads + execute for each issued uop.
    sl_vals = []
    sr_vals = []
    sp_vals = []
    exs = []
    for slot in range(p.issue_w):
        sl_vals.append(mux_by_uindex(m, idx=uop_sls[slot], items=prf, default=consts.zero64))
        sr_vals.append(mux_by_uindex(m, idx=uop_srs[slot], items=prf, default=consts.zero64))
        sp_vals.append(mux_by_uindex(m, idx=uop_sps[slot], items=prf, default=consts.zero64))
        exs.append(
            exec_uop_comb(
                m,
                op=uop_ops[slot],
                pc=uop_pcs[slot],
                imm=uop_imms[slot],
                srcl_val=sl_vals[slot],
                srcr_val=sr_vals[slot],
                srcr_type=uop_srcr_types[slot],
                shamt=uop_shamts[slot],
                srcp_val=sp_vals[slot],
                consts=consts,
            )
        )

    # Lane0 values for debug/trace.
    sl_val = sl_vals[0]
    sr_val = sr_vals[0]
    sp_val = sp_vals[0]

    load_fires = []
    store_fires = []
    any_load_fire = consts.zero1
    load_addr = consts.zero64
    for slot in range(p.issue_w):
        ld = issue_fires[slot] & exs[slot].is_load
        st = issue_fires[slot] & exs[slot].is_store
        load_fires.append(ld)
        store_fires.append(st)
        any_load_fire = any_load_fire | ld
        load_addr = ld.select(exs[slot].addr, load_addr)

    issued_is_load = load_fires[0]
    issued_is_store = store_fires[0]
    older_store_pending = mux_by_uindex(m, idx=issue_idx, items=lsu_older_store_pending, default=consts.zero1)

    # --- template macro engine (FENTRY/FEXIT/FRET.*) ---
    macro_active = state.macro_active.out()
    macro_phase = state.macro_phase.out()
    macro_op = state.macro_op.out()
    macro_stacksize = state.macro_stacksize.out()
    macro_reg = state.macro_reg.out()
    macro_i = state.macro_i.out()
    macro_sp_base = state.macro_sp_base.out()

    macro_is_fentry = macro_op.eq(c(OP_FENTRY, width=12))
    macro_phase_mem = macro_phase.eq(c(1, width=2))

    macro_i1 = (macro_i + c(1, width=6)).zext(width=64)
    macro_bytes = macro_i1.shl(amount=3)  # (i + 1) * 8
    macro_off_ok = macro_bytes.ule(macro_stacksize)
    macro_off = macro_stacksize - macro_bytes
    macro_addr = macro_sp_base + macro_off

    # Memory port arbitration: macro restore-load > uop load > fetch.
    macro_mem_read = macro_active & macro_phase_mem & (~macro_is_fentry) & macro_off_ok
    mem_raddr = macro_mem_read.select(macro_addr, any_load_fire.select(load_addr, state.fpc.out()))

    # Macro save path (FENTRY): store one register per cycle.
    cmap_now = [ren.cmap[i].out() for i in range(p.aregs)]
    macro_reg_tag = mux_by_uindex(m, idx=macro_reg, items=cmap_now, default=tag0)
    macro_reg_val = mux_by_uindex(m, idx=macro_reg_tag, items=prf, default=consts.zero64)
    macro_sp_tag = ren.cmap[1].out()
    macro_sp_val = mux_by_uindex(m, idx=macro_sp_tag, items=prf, default=consts.zero64)
    macro_reg_is_gpr = macro_reg.ult(c(24, width=6))
    macro_reg_not_zero = ~macro_reg.eq(c(0, width=6))
    macro_store_fire = macro_active & macro_phase_mem & macro_is_fentry & macro_off_ok & macro_reg_is_gpr & macro_reg_not_zero
    macro_store_addr = macro_addr
    macro_store_data = macro_reg_val
    macro_store_size = c(8, width=4)

    # MMIO (QEMU virt compatibility).
    #
    # - UART data: 0x1000_0000 (write low byte)
    # - EXIT:      0x1000_0004 (write exit code; stop simulation)
    mmio_uart = commit_store_fire & commit_store_addr.eq(c(0x1000_0000, width=64))
    mmio_exit = commit_store_fire & commit_store_addr.eq(c(0x1000_0004, width=64))
    mmio_any = mmio_uart | mmio_exit

    mmio_uart_data = mmio_uart.select(commit_store_data.trunc(width=8), c(0, width=8))
    mmio_exit_code = mmio_exit.select(commit_store_data.trunc(width=32), c(0, width=32))

    mem_wvalid = (commit_store_fire & (~mmio_any)) | macro_store_fire
    mem_waddr = macro_store_fire.select(macro_store_addr, commit_store_addr)
    mem_wdata = macro_store_fire.select(macro_store_data, commit_store_data)
    mem_wsize = macro_store_fire.select(macro_store_size, commit_store_size)

    # Store write port (writes at clk edge). Stop-at-store ensures that at most
    # one store commits per cycle in this bring-up model; the macro engine
    # consumes the same single write port.
    wstrb = consts.zero8
    wstrb = mem_wsize.eq(c(1, width=4)).select(c(0x01, width=8), wstrb)
    wstrb = mem_wsize.eq(c(2, width=4)).select(c(0x03, width=8), wstrb)
    wstrb = mem_wsize.eq(c(4, width=4)).select(c(0x0F, width=8), wstrb)
    wstrb = mem_wsize.eq(c(8, width=4)).select(c(0xFF, width=8), wstrb)

    mem_wvalid_eff = mem_wvalid | host_wvalid
    mem_waddr_eff = host_wvalid.select(host_waddr, mem_waddr)
    mem_wdata_eff = host_wvalid.select(host_wdata, mem_wdata)
    wstrb_eff = host_wvalid.select(host_wstrb, wstrb)

    mem_rdata = m.byte_mem(
        clk,
        rst,
        raddr=mem_raddr,
        wvalid=mem_wvalid_eff,
        waddr=mem_waddr_eff,
        wdata=mem_wdata_eff,
        wstrb=wstrb_eff,
        depth=mem_bytes,
        name="mem",
    )

    macro_phase_init = macro_phase.eq(c(0, width=2))
    macro_phase_sp = macro_phase.eq(c(2, width=2))
    macro_is_restore = macro_active & (~macro_is_fentry)

    # Macro PRF write port (one write per cycle).
    macro_reg_write = macro_active & macro_phase_mem & macro_is_restore & macro_off_ok & macro_reg_is_gpr & macro_reg_not_zero
    macro_sp_write_init = macro_active & macro_phase_init & macro_is_fentry
    macro_sp_write_restore = macro_active & macro_phase_sp & macro_is_restore

    macro_prf_we = macro_reg_write | macro_sp_write_init | macro_sp_write_restore
    macro_prf_tag = macro_sp_tag
    macro_prf_data = consts.zero64
    macro_prf_tag = macro_reg_write.select(macro_reg_tag, macro_prf_tag)
    macro_prf_data = macro_reg_write.select(mem_rdata, macro_prf_data)
    macro_prf_data = macro_sp_write_restore.select(macro_sp_base + macro_stacksize, macro_prf_data)
    macro_prf_data = macro_sp_write_init.select(macro_sp_val - macro_stacksize, macro_prf_data)

    # Load result (uses mem_rdata in the same cycle raddr is set).
    load8 = mem_rdata.trunc(width=8)
    load16 = mem_rdata.trunc(width=16)
    load32 = mem_rdata.trunc(width=32)
    load_lb = load8.sext(width=64)
    load_lbu = load8.zext(width=64)
    load_lh = load16.sext(width=64)
    load_lhu = load16.zext(width=64)
    load_lw = load32.sext(width=64)
    load_lwu = load32.zext(width=64)
    load_ld = mem_rdata
    wb_fires = []
    wb_robs = []
    wb_pdsts = []
    wb_values = []
    wb_fire_has_dsts = []
    wb_onehots = []
    for slot in range(p.issue_w):
        wb_fire = issue_fires[slot]
        wb_rob = uop_robs[slot]
        wb_pdst = uop_pdsts[slot]
        op = uop_ops[slot]
        load_val = load_lw
        load_val = op_is(op, OP_LB, OP_LBI, OP_HL_LB_PCR).select(load_lb, load_val)
        load_val = op_is(op, OP_LBU, OP_LBUI, OP_HL_LBU_PCR).select(load_lbu, load_val)
        load_val = op_is(op, OP_LH, OP_LHI, OP_HL_LH_PCR).select(load_lh, load_val)
        load_val = op_is(op, OP_LHU, OP_LHUI, OP_HL_LHU_PCR).select(load_lhu, load_val)
        load_val = op_is(op, OP_LWI, OP_C_LWI, OP_LW, OP_HL_LW_PCR).select(load_lw, load_val)
        load_val = op_is(op, OP_LWU, OP_LWUI, OP_HL_LWU_PCR).select(load_lwu, load_val)
        load_val = op_is(op, OP_LD, OP_LDI, OP_C_LDI, OP_HL_LD_PCR).select(load_ld, load_val)
        wb_value = load_fires[slot].select(load_val, exs[slot].alu)
        wb_has_dst = uop_has_dsts[slot] & (~store_fires[slot])
        wb_fire_has_dst = wb_fire & wb_has_dst

        wb_fires.append(wb_fire)
        wb_robs.append(wb_rob)
        wb_pdsts.append(wb_pdst)
        wb_values.append(wb_value)
        wb_fire_has_dsts.append(wb_fire_has_dst)
        wb_onehots.append(onehot_from_tag(m, tag=wb_pdst, width=p.pregs, tag_width=p.ptag_w))

    # --- dispatch (decode + rename + enqueue) ---
    f4_valid = ifu.f4_valid.out()
    f4_pc = ifu.f4_pc.out()
    f4_window = ifu.f4_window.out()

    f4_bundle = decode_f4_bundle(m, f4_window)

    disp_valids = []
    disp_pcs = []
    disp_ops = []
    disp_lens = []
    disp_regdsts = []
    disp_srcls = []
    disp_srcrs = []
    disp_srcr_types = []
    disp_shamts = []
    disp_srcps = []
    disp_imms = []
    disp_is_start_marker = []
    disp_push_t = []
    disp_push_u = []
    disp_is_store = []
    disp_dst_is_gpr = []
    disp_need_pdst = []
    disp_dst_kind = []

    for slot in range(p.dispatch_w):
        dec = f4_bundle.dec[slot]
        v = f4_valid & f4_bundle.valid[slot]
        off = f4_bundle.off_bytes[slot]
        pc = f4_pc + off.zext(width=64)

        op = dec.op
        ln = dec.len_bytes
        regdst = dec.regdst
        srcl = dec.srcl
        srcr = dec.srcr
        srcr_type = dec.srcr_type
        shamt = dec.shamt
        srcp = dec.srcp
        imm = dec.imm

        is_macro = op_is(op, OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK)
        is_start = (
            op_is(
                op,
                OP_C_BSTART_STD,
                OP_C_BSTART_COND,
                OP_C_BSTART_DIRECT,
                OP_BSTART_STD_FALL,
                OP_BSTART_STD_DIRECT,
                OP_BSTART_STD_COND,
                OP_BSTART_STD_CALL,
            )
            | is_macro
        )
        push_t = regdst.eq(c(31, width=6)) | op.eq(c(OP_C_LWI, width=12))
        push_u = regdst.eq(c(30, width=6))
        is_store = op_is(
            op,
            OP_SBI,
            OP_SHI,
            OP_SWI,
            OP_C_SWI,
            OP_SDI,
            OP_C_SDI,
            OP_SB,
            OP_SH,
            OP_SW,
            OP_SD,
            OP_HL_SB_PCR,
            OP_HL_SH_PCR,
            OP_HL_SW_PCR,
            OP_HL_SD_PCR,
        )

        dst_is_invalid = regdst.eq(c(REG_INVALID, width=6))
        dst_is_zero = regdst.eq(c(0, width=6))
        dst_is_gpr_range = (~regdst[5]) & (~(regdst[4] & regdst[3]))
        dst_is_gpr = dst_is_gpr_range & (~dst_is_invalid) & (~dst_is_zero) & (~push_t) & (~push_u)
        need_pdst = dst_is_gpr | push_t | push_u

        dk = c(0, width=2)
        dk = dst_is_gpr.select(c(1, width=2), dk)
        dk = push_t.select(c(2, width=2), dk)
        dk = push_u.select(c(3, width=2), dk)

        disp_valids.append(v)
        disp_pcs.append(pc)
        disp_ops.append(op)
        disp_lens.append(ln)
        disp_regdsts.append(regdst)
        disp_srcls.append(srcl)
        disp_srcrs.append(srcr)
        disp_srcr_types.append(srcr_type)
        disp_shamts.append(shamt)
        disp_srcps.append(srcp)
        disp_imms.append(imm)
        disp_is_start_marker.append(is_start)
        disp_push_t.append(push_t)
        disp_push_u.append(push_u)
        disp_is_store.append(is_store)
        disp_dst_is_gpr.append(dst_is_gpr)
        disp_need_pdst.append(need_pdst)
        disp_dst_kind.append(dk)

    # Lane0 decode (stable trace hook).
    dec_op = disp_ops[0]

    # Dispatch count (0..dispatch_w).
    disp_count = c(0, width=3)
    for slot in range(p.dispatch_w):
        disp_count = disp_count + disp_valids[slot].zext(width=3)

    # ROB space check: rob.count + disp_count <= rob_depth.
    rob_cnt_after = rob.count.out() + disp_count.zext(width=p.rob_w + 1)
    rob_space_ok = rob_cnt_after.ult(c(p.rob_depth + 1, width=p.rob_w + 1))

    # IQ routing + allocation: pick distinct free slots per-IQ for each lane.
    disp_to_alu = []
    disp_to_bru = []
    disp_to_lsu = []
    for slot in range(p.dispatch_w):
        op = disp_ops[slot]
        is_macro = op_is(op, OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK)
        is_load = op_is(
            op,
            OP_LWI,
            OP_C_LWI,
            OP_LBI,
            OP_LBUI,
            OP_LHI,
            OP_LHUI,
            OP_LWUI,
            OP_LB,
            OP_LBU,
            OP_LH,
            OP_LHU,
            OP_LW,
            OP_LWU,
            OP_LD,
            OP_LDI,
            OP_C_LDI,
            OP_HL_LB_PCR,
            OP_HL_LBU_PCR,
            OP_HL_LH_PCR,
            OP_HL_LHU_PCR,
            OP_HL_LW_PCR,
            OP_HL_LWU_PCR,
            OP_HL_LD_PCR,
        )
        is_store = disp_is_store[slot]
        is_mem = is_load | is_store
        is_bru = op_is(
            op,
            OP_C_BSTART_STD,
            OP_C_BSTART_COND,
            OP_C_BSTART_DIRECT,
            OP_C_BSTOP,
            OP_BSTART_STD_FALL,
            OP_BSTART_STD_DIRECT,
            OP_BSTART_STD_COND,
            OP_BSTART_STD_CALL,
            OP_FENTRY,
            OP_FEXIT,
            OP_FRET_RA,
            OP_FRET_STK,
            OP_C_SETC_EQ,
            OP_C_SETC_NE,
            OP_C_SETC_TGT,
        )
        to_lsu = is_mem
        to_bru = (~to_lsu) & is_bru & (~is_macro)
        to_alu = (~to_lsu) & (~to_bru) & (~is_macro)
        disp_to_alu.append(to_alu)
        disp_to_bru.append(to_bru)
        disp_to_lsu.append(to_lsu)

    alu_alloc_valids = []
    alu_alloc_idxs = []
    bru_alloc_valids = []
    bru_alloc_idxs = []
    lsu_alloc_valids = []
    lsu_alloc_idxs = []
    for slot in range(p.dispatch_w):
        # --- ALU IQ ---
        req_alu = disp_valids[slot] & disp_to_alu[slot]
        v = consts.zero1
        idx = c(0, width=p.iq_w)
        for i in range(p.iq_depth):
            cidx = c(i, width=p.iq_w)
            free = ~iq_alu.valid[i].out()
            exclude = consts.zero1
            for prev in range(slot):
                prev_req = disp_valids[prev] & disp_to_alu[prev]
                exclude = exclude | (prev_req & alu_alloc_valids[prev] & alu_alloc_idxs[prev].eq(cidx))
            cand = req_alu & free & (~exclude)
            take = cand & (~v)
            v = take.select(consts.one1, v)
            idx = take.select(cidx, idx)
        alu_alloc_valids.append(v)
        alu_alloc_idxs.append(idx)

        # --- BRU IQ ---
        req_bru = disp_valids[slot] & disp_to_bru[slot]
        v = consts.zero1
        idx = c(0, width=p.iq_w)
        for i in range(p.iq_depth):
            cidx = c(i, width=p.iq_w)
            free = ~iq_bru.valid[i].out()
            exclude = consts.zero1
            for prev in range(slot):
                prev_req = disp_valids[prev] & disp_to_bru[prev]
                exclude = exclude | (prev_req & bru_alloc_valids[prev] & bru_alloc_idxs[prev].eq(cidx))
            cand = req_bru & free & (~exclude)
            take = cand & (~v)
            v = take.select(consts.one1, v)
            idx = take.select(cidx, idx)
        bru_alloc_valids.append(v)
        bru_alloc_idxs.append(idx)

        # --- LSU IQ ---
        req_lsu = disp_valids[slot] & disp_to_lsu[slot]
        v = consts.zero1
        idx = c(0, width=p.iq_w)
        for i in range(p.iq_depth):
            cidx = c(i, width=p.iq_w)
            free = ~iq_lsu.valid[i].out()
            exclude = consts.zero1
            for prev in range(slot):
                prev_req = disp_valids[prev] & disp_to_lsu[prev]
                exclude = exclude | (prev_req & lsu_alloc_valids[prev] & lsu_alloc_idxs[prev].eq(cidx))
            cand = req_lsu & free & (~exclude)
            take = cand & (~v)
            v = take.select(consts.one1, v)
            idx = take.select(cidx, idx)
        lsu_alloc_valids.append(v)
        lsu_alloc_idxs.append(idx)

    alu_alloc_ok = consts.one1
    bru_alloc_ok = consts.one1
    lsu_alloc_ok = consts.one1
    for slot in range(p.dispatch_w):
        req_alu = disp_valids[slot] & disp_to_alu[slot]
        req_bru = disp_valids[slot] & disp_to_bru[slot]
        req_lsu = disp_valids[slot] & disp_to_lsu[slot]
        alu_alloc_ok = alu_alloc_ok & ((~req_alu) | alu_alloc_valids[slot])
        bru_alloc_ok = bru_alloc_ok & ((~req_bru) | bru_alloc_valids[slot])
        lsu_alloc_ok = lsu_alloc_ok & ((~req_lsu) | lsu_alloc_valids[slot])
    iq_alloc_ok = alu_alloc_ok & bru_alloc_ok & lsu_alloc_ok

    # Physical register allocation (up to dispatch_w per cycle).
    preg_alloc_valids = []
    preg_alloc_tags = []
    preg_alloc_onehots = []
    free_mask_stage = ren.free_mask.out()
    for slot in range(p.dispatch_w):
        req = disp_valids[slot] & disp_need_pdst[slot]
        v, tag, oh = alloc_from_free_mask(m, free_mask=free_mask_stage, width=p.pregs, tag_width=p.ptag_w)
        free_mask_stage = req.select(free_mask_stage & (~oh), free_mask_stage)
        preg_alloc_valids.append(v)
        preg_alloc_tags.append(tag)
        preg_alloc_onehots.append(oh)

    preg_alloc_ok = consts.one1
    for slot in range(p.dispatch_w):
        req = disp_valids[slot] & disp_need_pdst[slot]
        preg_alloc_ok = preg_alloc_ok & ((~req) | preg_alloc_valids[slot])

    disp_pdsts = []
    disp_alloc_mask = c(0, width=p.pregs)
    for slot in range(p.dispatch_w):
        req = disp_valids[slot] & disp_need_pdst[slot]
        pdst = req.select(preg_alloc_tags[slot], tag0)
        oh = req.select(preg_alloc_onehots[slot], c(0, width=p.pregs))
        disp_pdsts.append(pdst)
        disp_alloc_mask = disp_alloc_mask | oh

    dispatch_fire = can_run & (~commit_redirect) & f4_valid & rob_space_ok & iq_alloc_ok & preg_alloc_ok

    # --- IFU updates (single-entry fetch queue) ---
    #
    # Fetch when the memory read port is available and the queue is empty (or
    # being consumed by dispatch in the same cycle).
    fetch_bundle = decode_f4_bundle(m, mem_rdata)
    fetch_len = fetch_bundle.total_len_bytes
    fetch_advance = fetch_bundle.total_len_bytes.zext(width=64)
    fetch_fire = can_run & (~commit_redirect) & (~any_load_fire) & ((~f4_valid) | dispatch_fire)

    f4_valid_next = f4_valid
    f4_valid_next = (dispatch_fire & (~fetch_fire)).select(consts.zero1, f4_valid_next)
    f4_valid_next = fetch_fire.select(consts.one1, f4_valid_next)
    f4_valid_next = commit_redirect.select(consts.zero1, f4_valid_next)
    f4_valid_next = do_flush.select(consts.zero1, f4_valid_next)
    ifu.f4_valid.set(f4_valid_next)
    ifu.f4_pc.set(state.fpc.out(), when=fetch_fire)
    ifu.f4_window.set(mem_rdata, when=fetch_fire)

    # Source PTAGs from SMAP with intra-cycle rename forwarding across lanes.
    smap_live = [ren.smap[i].out() for i in range(p.aregs)]
    disp_srcl_tags = []
    disp_srcr_tags = []
    disp_srcp_tags = []
    for slot in range(p.dispatch_w):
        srcl_areg = disp_srcls[slot]
        srcr_areg = disp_srcrs[slot]
        srcp_areg = disp_srcps[slot]

        srcl_tag = mux_by_uindex(m, idx=srcl_areg, items=smap_live, default=tag0)
        srcr_tag = mux_by_uindex(m, idx=srcr_areg, items=smap_live, default=tag0)
        srcp_tag = mux_by_uindex(m, idx=srcp_areg, items=smap_live, default=tag0)
        srcl_tag = srcl_areg.eq(c(REG_INVALID, width=6)).select(tag0, srcl_tag)
        srcr_tag = srcr_areg.eq(c(REG_INVALID, width=6)).select(tag0, srcr_tag)
        srcp_tag = srcp_areg.eq(c(REG_INVALID, width=6)).select(tag0, srcp_tag)

        disp_srcl_tags.append(srcl_tag)
        disp_srcr_tags.append(srcr_tag)
        disp_srcp_tags.append(srcp_tag)

        lane_fire = dispatch_fire & disp_valids[slot]

        # Snapshot old hand regs for push shifting (uses state after previous lanes).
        t0_old = smap_live[24]
        t1_old = smap_live[25]
        t2_old = smap_live[26]
        u0_old = smap_live[28]
        u1_old = smap_live[29]
        u2_old = smap_live[30]

        smap_next = []
        for i in range(p.aregs):
            nxt = smap_live[i]

            if 24 <= i <= 31:
                nxt = (lane_fire & disp_is_start_marker[slot]).select(tag0, nxt)

            if i == 24:
                nxt = (lane_fire & disp_push_t[slot]).select(disp_pdsts[slot], nxt)
            if i == 25:
                nxt = (lane_fire & disp_push_t[slot]).select(t0_old, nxt)
            if i == 26:
                nxt = (lane_fire & disp_push_t[slot]).select(t1_old, nxt)
            if i == 27:
                nxt = (lane_fire & disp_push_t[slot]).select(t2_old, nxt)

            if i == 28:
                nxt = (lane_fire & disp_push_u[slot]).select(disp_pdsts[slot], nxt)
            if i == 29:
                nxt = (lane_fire & disp_push_u[slot]).select(u0_old, nxt)
            if i == 30:
                nxt = (lane_fire & disp_push_u[slot]).select(u1_old, nxt)
            if i == 31:
                nxt = (lane_fire & disp_push_u[slot]).select(u2_old, nxt)

            if i < 24:
                dst_match = disp_regdsts[slot].eq(c(i, width=6))
                nxt = (lane_fire & disp_dst_is_gpr[slot] & dst_match).select(disp_pdsts[slot], nxt)

            if i == 0:
                nxt = tag0

            smap_next.append(nxt)
        smap_live = smap_next

    # --- ready table updates ---
    ready_next = ren.ready_mask.out()
    ready_next = dispatch_fire.select(ready_next & (~disp_alloc_mask), ready_next)

    wb_set_mask = c(0, width=p.pregs)
    for slot in range(p.issue_w):
        wb_set_mask = wb_fire_has_dsts[slot].select(wb_set_mask | wb_onehots[slot], wb_set_mask)
    ready_next = ready_next | wb_set_mask
    ready_next = do_flush.select(c((1 << p.pregs) - 1, width=p.pregs), ready_next)
    ren.ready_mask.set(ready_next)

    # PRF writes (up to issue_w writebacks per cycle).
    ra_tag = ra_write_tag
    for i in range(p.pregs):
        we = consts.zero1
        wdata = consts.zero64
        for slot in range(p.issue_w):
            hit = wb_fire_has_dsts[slot] & wb_pdsts[slot].eq(c(i, width=p.ptag_w))
            we = we | hit
            wdata = hit.select(wb_values[slot], wdata)
        hit_ra = ra_write_fire & ra_tag.eq(c(i, width=p.ptag_w))
        we = we | hit_ra
        wdata = hit_ra.select(ra_write_value, wdata)
        hit_macro = macro_prf_we & macro_prf_tag.eq(c(i, width=p.ptag_w))
        we = we | hit_macro
        wdata = hit_macro.select(macro_prf_data, wdata)
        prf[i].set(wdata, when=we)

    # --- ROB updates ---
    disp_rob_idxs = []
    disp_fires = []
    for slot in range(p.dispatch_w):
        disp_rob_idxs.append(rob.tail.out() + c(slot, width=p.rob_w))
        disp_fires.append(dispatch_fire & disp_valids[slot])

    for i in range(p.rob_depth):
        idx = c(i, width=p.rob_w)
        commit_hit = consts.zero1
        for slot in range(p.commit_w):
            commit_hit = commit_hit | (commit_fires[slot] & commit_idxs[slot].eq(idx))

        disp_hit = consts.zero1
        for slot in range(p.dispatch_w):
            disp_hit = disp_hit | (disp_fires[slot] & disp_rob_idxs[slot].eq(idx))

        wb_hit = consts.zero1
        for slot in range(p.issue_w):
            wb_hit = wb_hit | (wb_fires[slot] & wb_robs[slot].eq(idx))

        v_next = rob.valid[i].out()
        v_next = do_flush.select(consts.zero1, v_next)
        v_next = commit_hit.select(consts.zero1, v_next)
        v_next = disp_hit.select(consts.one1, v_next)
        rob.valid[i].set(v_next)

        done_next = rob.done[i].out()
        done_next = do_flush.select(consts.zero1, done_next)
        done_next = commit_hit.select(consts.zero1, done_next)
        done_next = disp_hit.select(consts.zero1, done_next)
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            is_macro = op_is(disp_ops[slot], OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK)
            done_next = (hit & is_macro).select(consts.one1, done_next)
        done_next = wb_hit.select(consts.one1, done_next)
        rob.done[i].set(done_next)

        op_next = rob.op[i].out()
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            op_next = hit.select(disp_ops[slot], op_next)
        rob.op[i].set(op_next)

        ln_next = rob.len_bytes[i].out()
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            ln_next = hit.select(disp_lens[slot], ln_next)
        rob.len_bytes[i].set(ln_next)

        dk_next = rob.dst_kind[i].out()
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            dk_next = hit.select(disp_dst_kind[slot], dk_next)
        rob.dst_kind[i].set(dk_next)

        da_next = rob.dst_areg[i].out()
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            da_next = hit.select(disp_regdsts[slot], da_next)
        rob.dst_areg[i].set(da_next)

        pd_next = rob.pdst[i].out()
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            pd_next = hit.select(disp_pdsts[slot], pd_next)
        rob.pdst[i].set(pd_next)

        val_next = rob.value[i].out()
        val_next = disp_hit.select(consts.zero64, val_next)
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            is_macro = op_is(disp_ops[slot], OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK)
            val_next = (hit & is_macro).select(disp_imms[slot], val_next)
        for slot in range(p.issue_w):
            hit = wb_fires[slot] & wb_robs[slot].eq(idx)
            val_next = hit.select(wb_values[slot], val_next)
        rob.value[i].set(val_next)

        is_store_next = rob.is_store[i].out()
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            is_store_next = hit.select(disp_is_store[slot], is_store_next)
        rob.is_store[i].set(is_store_next)

        st_addr_next = rob.store_addr[i].out()
        st_data_next = rob.store_data[i].out()
        st_size_next = rob.store_size[i].out()
        st_addr_next = disp_hit.select(consts.zero64, st_addr_next)
        st_data_next = disp_hit.select(consts.zero64, st_data_next)
        st_size_next = disp_hit.select(consts.zero4, st_size_next)
        for slot in range(p.issue_w):
            hit = store_fires[slot] & wb_robs[slot].eq(idx)
            st_addr_next = hit.select(exs[slot].addr, st_addr_next)
            st_data_next = hit.select(exs[slot].wdata, st_data_next)
            st_size_next = hit.select(exs[slot].size, st_size_next)
        rob.store_addr[i].set(st_addr_next)
        rob.store_data[i].set(st_data_next)
        rob.store_size[i].set(st_size_next)

        mb_next = rob.macro_begin[i].out()
        me_next = rob.macro_end[i].out()
        for slot in range(p.dispatch_w):
            hit = disp_fires[slot] & disp_rob_idxs[slot].eq(idx)
            mb_next = hit.select(disp_srcls[slot], mb_next)
            me_next = hit.select(disp_srcrs[slot], me_next)
        rob.macro_begin[i].set(mb_next)
        rob.macro_end[i].set(me_next)

    # ROB pointers/count.
    head_next = rob.head.out()
    tail_next = rob.tail.out()
    count_next = rob.count.out()

    head_next = do_flush.select(c(0, width=p.rob_w), head_next)
    tail_next = do_flush.select(c(0, width=p.rob_w), tail_next)
    count_next = do_flush.select(c(0, width=p.rob_w + 1), count_next)

    inc_head = commit_fire & (~do_flush)
    inc_tail = dispatch_fire & (~do_flush)

    head_inc = commit_count
    if p.rob_w > head_inc.width:
        head_inc = head_inc.zext(width=p.rob_w)
    elif p.rob_w < head_inc.width:
        head_inc = head_inc.trunc(width=p.rob_w)
    head_next = inc_head.select(rob.head.out() + head_inc, head_next)
    disp_tail_inc = disp_count
    if p.rob_w > disp_tail_inc.width:
        disp_tail_inc = disp_tail_inc.zext(width=p.rob_w)
    elif p.rob_w < disp_tail_inc.width:
        disp_tail_inc = disp_tail_inc.trunc(width=p.rob_w)
    tail_next = inc_tail.select(rob.tail.out() + disp_tail_inc, tail_next)

    commit_dec = commit_count.zext(width=p.rob_w + 1)
    commit_dec_neg = (~commit_dec) + c(1, width=p.rob_w + 1)
    count_next = inc_tail.select(count_next + disp_count.zext(width=p.rob_w + 1), count_next)
    count_next = inc_head.select(count_next + commit_dec_neg, count_next)

    rob.head.set(head_next)
    rob.tail.set(tail_next)
    rob.count.set(count_next)

    # --- IQ updates ---
    def update_iq(*, iq, disp_to: list, alloc_idxs: list, issue_fires_q: list, issue_idxs_q: list) -> None:
        for i in range(p.iq_depth):
            idx = c(i, width=p.iq_w)

            issue_clear = consts.zero1
            for slot in range(len(issue_fires_q)):
                issue_clear = issue_clear | (issue_fires_q[slot] & issue_idxs_q[slot].eq(idx))

            disp_alloc_hit = consts.zero1
            for slot in range(p.dispatch_w):
                disp_alloc_hit = disp_alloc_hit | (disp_fires[slot] & disp_to[slot] & alloc_idxs[slot].eq(idx))

            v_next = iq.valid[i].out()
            v_next = do_flush.select(consts.zero1, v_next)
            v_next = issue_clear.select(consts.zero1, v_next)
            v_next = disp_alloc_hit.select(consts.one1, v_next)
            iq.valid[i].set(v_next)

            robn = iq.rob[i].out()
            opn = iq.op[i].out()
            pcn = iq.pc[i].out()
            imn = iq.imm[i].out()
            sln = iq.srcl[i].out()
            srn = iq.srcr[i].out()
            stn = iq.srcr_type[i].out()
            shn = iq.shamt[i].out()
            spn = iq.srcp[i].out()
            pdn = iq.pdst[i].out()
            hdn = iq.has_dst[i].out()
            for slot in range(p.dispatch_w):
                hit = disp_fires[slot] & disp_to[slot] & alloc_idxs[slot].eq(idx)
                robn = hit.select(disp_rob_idxs[slot], robn)
                opn = hit.select(disp_ops[slot], opn)
                pcn = hit.select(disp_pcs[slot], pcn)
                imn = hit.select(disp_imms[slot], imn)
                sln = hit.select(disp_srcl_tags[slot], sln)
                srn = hit.select(disp_srcr_tags[slot], srn)
                stn = hit.select(disp_srcr_types[slot], stn)
                shn = hit.select(disp_shamts[slot], shn)
                spn = hit.select(disp_srcp_tags[slot], spn)
                pdn = hit.select(disp_pdsts[slot], pdn)
                hdn = hit.select(disp_need_pdst[slot], hdn)
            iq.rob[i].set(robn)
            iq.op[i].set(opn)
            iq.pc[i].set(pcn)
            iq.imm[i].set(imn)
            iq.srcl[i].set(sln)
            iq.srcr[i].set(srn)
            iq.srcr_type[i].set(stn)
            iq.shamt[i].set(shn)
            iq.srcp[i].set(spn)
            iq.pdst[i].set(pdn)
            iq.has_dst[i].set(hdn)

    lsu_base = 0
    bru_base = p.lsu_w
    alu_base = p.lsu_w + p.bru_w
    update_iq(iq=iq_lsu, disp_to=disp_to_lsu, alloc_idxs=lsu_alloc_idxs, issue_fires_q=issue_fires[lsu_base : lsu_base + p.lsu_w], issue_idxs_q=lsu_issue_idxs)
    update_iq(iq=iq_bru, disp_to=disp_to_bru, alloc_idxs=bru_alloc_idxs, issue_fires_q=issue_fires[bru_base : bru_base + p.bru_w], issue_idxs_q=bru_issue_idxs)
    update_iq(iq=iq_alu, disp_to=disp_to_alu, alloc_idxs=alu_alloc_idxs, issue_fires_q=issue_fires[alu_base : alu_base + p.alu_w], issue_idxs_q=alu_issue_idxs)

    # --- SMAP updates (rename) ---
    for i in range(p.aregs):
        nxt = smap_live[i]
        nxt = do_flush.select(ren.cmap[i].out(), nxt)
        if i == 0:
            nxt = tag0
        ren.smap[i].set(nxt)

    # --- CMAP + freelist updates (commit) ---
    #
    # Apply up to `commit_w` in-order updates, including the T/U hand-stack and
    # their corresponding freelist frees.
    cmap_live = [ren.cmap[i].out() for i in range(p.aregs)]

    free_after_dispatch = dispatch_fire.select(ren.free_mask.out() & (~disp_alloc_mask), ren.free_mask.out())
    free_live = free_after_dispatch

    for slot in range(p.commit_w):
        fire = commit_fires[slot]
        op = rob_ops[slot]
        dk = rob_dst_kinds[slot]
        areg = rob_dst_aregs[slot]
        pdst = rob_pdsts[slot]

        is_macro = op_is(op, OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK)
        is_start_marker = (
            op_is(
                op,
                OP_C_BSTART_STD,
                OP_C_BSTART_COND,
                OP_C_BSTART_DIRECT,
                OP_BSTART_STD_FALL,
                OP_BSTART_STD_DIRECT,
                OP_BSTART_STD_COND,
                OP_BSTART_STD_CALL,
            )
            | is_macro
        )

        # Snapshot old hand regs (from the pre-update state for this slot).
        old_t0 = cmap_live[24]
        old_t1 = cmap_live[25]
        old_t2 = cmap_live[26]
        old_t3 = cmap_live[27]
        old_u0 = cmap_live[28]
        old_u1 = cmap_live[29]
        old_u2 = cmap_live[30]
        old_u3 = cmap_live[31]

        # Start marker clears: free all old hand tags and clear mappings.
        if_free = commit_enter_new_blocks[slot]
        for old in [old_t0, old_t1, old_t2, old_t3, old_u0, old_u1, old_u2, old_u3]:
            oh = onehot_from_tag(m, tag=old, width=p.pregs, tag_width=p.ptag_w)
            free_live = (if_free & (~old.eq(tag0))).select(free_live | oh, free_live)
        for i in range(24, 32):
            cmap_live[i] = if_free.select(tag0, cmap_live[i])

        # Push T: free dropped T3 and shift [T0..T3].
        push_t = fire & dk.eq(c(2, width=2))
        t3_oh = onehot_from_tag(m, tag=old_t3, width=p.pregs, tag_width=p.ptag_w)
        free_live = (push_t & (~old_t3.eq(tag0))).select(free_live | t3_oh, free_live)
        cmap_live[24] = push_t.select(pdst, cmap_live[24])
        cmap_live[25] = push_t.select(old_t0, cmap_live[25])
        cmap_live[26] = push_t.select(old_t1, cmap_live[26])
        cmap_live[27] = push_t.select(old_t2, cmap_live[27])

        # Push U: free dropped U3 and shift [U0..U3].
        push_u = fire & dk.eq(c(3, width=2))
        u3_oh = onehot_from_tag(m, tag=old_u3, width=p.pregs, tag_width=p.ptag_w)
        free_live = (push_u & (~old_u3.eq(tag0))).select(free_live | u3_oh, free_live)
        cmap_live[28] = push_u.select(pdst, cmap_live[28])
        cmap_live[29] = push_u.select(old_u0, cmap_live[29])
        cmap_live[30] = push_u.select(old_u1, cmap_live[30])
        cmap_live[31] = push_u.select(old_u2, cmap_live[31])

        # Normal GPR writes: update mapping and free old dst tag (gpr only, not x0).
        is_gpr = fire & dk.eq(c(1, width=2))
        for i in range(24):
            hit = is_gpr & areg.eq(c(i, width=6))
            old = cmap_live[i]
            old_oh = onehot_from_tag(m, tag=old, width=p.pregs, tag_width=p.ptag_w)
            free_live = (hit & (~old.eq(tag0))).select(free_live | old_oh, free_live)
            cmap_live[i] = hit.select(pdst, cmap_live[i])

        # r0 hardwired to p0.
        cmap_live[0] = tag0

    for i in range(p.aregs):
        ren.cmap[i].set(cmap_live[i])

    # Flush recomputes freelist from CMAP to drop speculative allocations.
    used = c(0, width=p.pregs)
    for i in range(p.aregs):
        used = used | onehot_from_tag(m, tag=ren.cmap[i].out(), width=p.pregs, tag_width=p.ptag_w)
    free_recomputed = ~used
    free_next = do_flush.select(free_recomputed, free_live)
    ren.free_mask.set(free_next)

    # --- commit state updates (pc/br/control regs) ---
    state.pc.set(pc_live)

    # Fetch PC progression: advance on successful fetch.
    fpc_next = state.fpc.out()
    fpc_next = fetch_fire.select(state.fpc.out() + fetch_advance, fpc_next)
    fpc_next = commit_redirect.select(redirect_pc, fpc_next)
    fpc_next = do_flush.select(state.flush_pc.out(), fpc_next)
    state.fpc.set(fpc_next)

    # Redirect/flush pending.
    state.flush_pc.set(commit_redirect.select(redirect_pc, state.flush_pc.out()))
    flush_pend_next = state.flush_pending.out()
    flush_pend_next = do_flush.select(consts.zero1, flush_pend_next)
    flush_pend_next = commit_redirect.select(consts.one1, flush_pend_next)
    state.flush_pending.set(flush_pend_next)

    # Halt latch.
    halt_set = consts.zero1
    for slot in range(p.commit_w):
        op = rob_ops[slot]
        is_halt = op.eq(c(OP_EBREAK, width=12)) | op.eq(c(OP_INVALID, width=12))
        halt_set = halt_set | (commit_fires[slot] & is_halt)
    halt_set = halt_set | mmio_exit
    state.halted.set(consts.one1, when=halt_set)

    state.cycles.set(state.cycles.out() + consts.one64)
    state.commit_cond.set(commit_cond_live)
    state.commit_tgt.set(commit_tgt_live)
    state.br_kind.set(br_kind_live)
    state.br_base_pc.set(br_base_live)
    state.br_off.set(br_off_live)

    # --- template macro engine state updates ---
    #
    # Implements the bring-up ABI semantics used by QEMU/LLVM:
    # - SP adjust: adj = stacksize (+ callframe_size, currently 0).
    # - Save/restore regs [begin..end] at offsets (stacksize - (i+1)*8).
    # - FRET.* commits as an explicit return via restored RA.
    ph_init = c(0, width=2)
    ph_mem = c(1, width=2)
    ph_sp = c(2, width=2)

    macro_active_n = macro_active
    macro_phase_n = macro_phase
    macro_op_n = macro_op
    macro_begin_n = state.macro_begin.out()
    macro_end_n = state.macro_end.out()
    macro_stack_n = macro_stacksize
    macro_reg_n = macro_reg
    macro_i_n = macro_i
    macro_sp_base_n = macro_sp_base

    macro_active_n = do_flush.select(consts.zero1, macro_active_n)
    macro_phase_n = do_flush.select(ph_init, macro_phase_n)

    macro_active_n = macro_start.select(consts.one1, macro_active_n)
    macro_phase_n = macro_start.select(ph_init, macro_phase_n)
    macro_op_n = macro_start.select(head_op, macro_op_n)
    macro_begin_n = macro_start.select(head_macro_begin, macro_begin_n)
    macro_end_n = macro_start.select(head_macro_end, macro_end_n)
    macro_stack_n = macro_start.select(head_value, macro_stack_n)
    macro_reg_n = macro_start.select(head_macro_begin, macro_reg_n)
    macro_i_n = macro_start.select(c(0, width=6), macro_i_n)

    macro_phase_is_init = macro_phase.eq(ph_init)
    macro_phase_is_mem = macro_phase.eq(ph_mem)
    macro_phase_is_sp = macro_phase.eq(ph_sp)
    macro_is_restore = macro_active & (~macro_is_fentry)

    # Init: latch base SP and setup iteration.
    init_fire = macro_active & macro_phase_is_init
    sp_new_init = macro_sp_val - macro_stacksize
    macro_sp_base_n = (init_fire & macro_is_fentry).select(sp_new_init, macro_sp_base_n)
    macro_sp_base_n = (init_fire & macro_is_restore).select(macro_sp_val, macro_sp_base_n)
    macro_reg_n = init_fire.select(state.macro_begin.out(), macro_reg_n)
    macro_i_n = init_fire.select(c(0, width=6), macro_i_n)
    macro_phase_n = init_fire.select(ph_mem, macro_phase_n)

    # Mem loop: iterate regs and offsets; save uses store port, restore uses load port.
    step_fire = macro_active & macro_phase_is_mem
    step_done = step_fire & ((~macro_off_ok) | macro_reg.eq(state.macro_end.out()))

    reg_plus = macro_reg + c(1, width=6)
    reg_wrap = reg_plus.ugt(c(23, width=6))
    reg_next = reg_wrap.select(c(2, width=6), reg_plus)
    macro_reg_n = (step_fire & (~step_done)).select(reg_next, macro_reg_n)
    macro_i_n = (step_fire & (~step_done)).select((macro_i + c(1, width=6)), macro_i_n)

    macro_phase_n = (step_done & macro_is_restore).select(ph_sp, macro_phase_n)
    macro_active_n = (step_done & macro_is_fentry).select(consts.zero1, macro_active_n)
    macro_phase_n = (step_done & macro_is_fentry).select(ph_init, macro_phase_n)

    # Restore SP update phase.
    sp_fire = macro_active & macro_phase_is_sp
    macro_active_n = sp_fire.select(consts.zero1, macro_active_n)
    macro_phase_n = sp_fire.select(ph_init, macro_phase_n)

    macro_wait_n = state.macro_wait_commit.out()
    macro_wait_n = do_flush.select(consts.zero1, macro_wait_n)
    macro_wait_n = macro_start.select(consts.one1, macro_wait_n)
    macro_committed = consts.zero1
    for slot in range(p.commit_w):
        op = rob_ops[slot]
        fire = commit_fires[slot]
        macro_committed = macro_committed | (fire & op_is(op, OP_FENTRY, OP_FEXIT, OP_FRET_RA, OP_FRET_STK))
    macro_wait_n = macro_committed.select(consts.zero1, macro_wait_n)

    state.macro_active.set(macro_active_n)
    state.macro_wait_commit.set(macro_wait_n)
    state.macro_phase.set(macro_phase_n)
    state.macro_op.set(macro_op_n)
    state.macro_begin.set(macro_begin_n)
    state.macro_end.set(macro_end_n)
    state.macro_stacksize.set(macro_stack_n)
    state.macro_reg.set(macro_reg_n)
    state.macro_i.set(macro_i_n)
    state.macro_sp_base.set(macro_sp_base_n)

    # --- outputs ---
    a0_tag = ren.cmap[2].out()
    a1_tag = ren.cmap[3].out()
    ra_tag = ren.cmap[10].out()
    sp_tag = ren.cmap[1].out()

    m.output("halted", state.halted)
    m.output("cycles", state.cycles)
    m.output("pc", state.pc)
    m.output("fpc", state.fpc)
    m.output("a0", mux_by_uindex(m, idx=a0_tag, items=prf, default=consts.zero64))
    m.output("a1", mux_by_uindex(m, idx=a1_tag, items=prf, default=consts.zero64))
    m.output("ra", mux_by_uindex(m, idx=ra_tag, items=prf, default=consts.zero64))
    m.output("sp", mux_by_uindex(m, idx=sp_tag, items=prf, default=consts.zero64))
    m.output("commit_op", head_op)
    m.output("commit_fire", commit_fire)
    m.output("commit_value", head_value)
    m.output("commit_dst_kind", head_dst_kind)
    m.output("commit_dst_areg", head_dst_areg)
    m.output("commit_pdst", head_pdst)
    m.output("commit_cond", state.commit_cond)
    m.output("commit_tgt", state.commit_tgt)
    m.output("br_kind", state.br_kind)
    m.output("br_base_pc", state.br_base_pc)
    m.output("br_off", state.br_off)
    m.output("commit_store_fire", commit_store_fire)
    m.output("commit_store_addr", commit_store_addr)
    m.output("commit_store_data", commit_store_data)
    m.output("commit_store_size", commit_store_size)

    # Commit slot visibility (bring-up tracing): per-slot PC/op/value/fire.
    #
    # `commit_fire` / `commit_op` / `commit_value` remain lane0-compatible.
    # These additional signals help debug multi-commit cycles where older
    # commits may not appear in a slot0-only log.
    max_commit_slots = 4
    for slot in range(max_commit_slots):
        fire = consts.zero1
        pc = consts.zero64
        rob_idx = c(0, width=p.rob_w)
        op = c(0, width=12)
        val = consts.zero64
        if slot < p.commit_w:
            fire = commit_fires[slot]
            pc = commit_pcs[slot]
            rob_idx = commit_idxs[slot]
            op = rob_ops[slot]
            val = rob_values[slot]
        m.output(f"commit_fire{slot}", fire)
        m.output(f"commit_pc{slot}", pc)
        m.output(f"commit_rob{slot}", rob_idx)
        m.output(f"commit_op{slot}", op)
        m.output(f"commit_value{slot}", val)
    m.output("rob_count", rob.count)

    # Debug: committed vs speculative hand tops (T0/U0).
    ct0_tag = ren.cmap[24].out()
    cu0_tag = ren.cmap[28].out()
    st0_tag = ren.smap[24].out()
    su0_tag = ren.smap[28].out()
    m.output("ct0", mux_by_uindex(m, idx=ct0_tag, items=prf, default=consts.zero64))
    m.output("cu0", mux_by_uindex(m, idx=cu0_tag, items=prf, default=consts.zero64))
    m.output("st0", mux_by_uindex(m, idx=st0_tag, items=prf, default=consts.zero64))
    m.output("su0", mux_by_uindex(m, idx=su0_tag, items=prf, default=consts.zero64))

    # Debug: issue/memory arbitration visibility.
    m.output("issue_fire", issue_fire)
    m.output("issue_op", uop_op)
    m.output("issue_pc", uop_pc)
    m.output("issue_rob", uop_rob)
    m.output("issue_sl", uop_sl)
    m.output("issue_sr", uop_sr)
    m.output("issue_sp", uop_sp)
    m.output("issue_pdst", uop_pdst)
    m.output("issue_sl_val", sl_val)
    m.output("issue_sr_val", sr_val)
    m.output("issue_sp_val", sp_val)
    m.output("issue_is_load", issued_is_load)
    m.output("issue_is_store", issued_is_store)
    m.output("store_pending", store_pending)
    m.output("store_pending_older", older_store_pending)
    m.output("mem_raddr", mem_raddr)
    m.output("dispatch_fire", dispatch_fire)
    m.output("dec_op", dec_op)

    # Dispatch slot visibility (trace hook): per-slot PC/op/ROB for pipeview tools.
    max_disp_slots = 4
    for slot in range(max_disp_slots):
        fire = consts.zero1
        pc = consts.zero64
        rob_i = c(0, width=p.rob_w)
        op = c(0, width=12)
        if slot < p.dispatch_w:
            fire = disp_fires[slot]
            pc = disp_pcs[slot]
            rob_i = disp_rob_idxs[slot]
            op = disp_ops[slot]
        m.output(f"dispatch_fire{slot}", fire)
        m.output(f"dispatch_pc{slot}", pc)
        m.output(f"dispatch_rob{slot}", rob_i)
        m.output(f"dispatch_op{slot}", op)

    # Issue slot visibility (trace hook): per-slot PC/op/ROB for pipeview tools.
    max_issue_slots = 4
    for slot in range(max_issue_slots):
        fire = consts.zero1
        pc = consts.zero64
        rob_i = c(0, width=p.rob_w)
        op = c(0, width=12)
        if slot < p.issue_w:
            fire = issue_fires[slot]
            pc = uop_pcs[slot]
            rob_i = uop_robs[slot]
            op = uop_ops[slot]
        m.output(f"issue_fire{slot}", fire)
        m.output(f"issue_pc{slot}", pc)
        m.output(f"issue_rob{slot}", rob_i)
        m.output(f"issue_op{slot}", op)

    # MMIO visibility for testbenches (UART + exit).
    m.output("mmio_uart_valid", mmio_uart)
    m.output("mmio_uart_data", mmio_uart_data)
    m.output("mmio_exit_valid", mmio_exit)
    m.output("mmio_exit_code", mmio_exit_code)

    # Block command export for Janus BCtrl/TMU/PE top-level bring-up.
    block_cmd_valid = commit_fire & op_is(head_op, OP_C_BSTART_STD, OP_C_BSTART_COND, OP_BSTART_STD_CALL)
    block_cmd_kind = head_op.eq(c(OP_C_BSTART_COND, width=6)).select(c(1, width=2), c(0, width=2))
    block_cmd_kind = head_op.eq(c(OP_BSTART_STD_CALL, width=6)).select(c(2, width=2), block_cmd_kind)
    block_cmd_payload = head_value
    block_cmd_tile = head_value.trunc(width=6)
    block_cmd_tag = state.cycles.out().trunc(width=8)

    ooo_4wide = c(1 if (p.fetch_w == 4 and p.dispatch_w == 4 and p.issue_w == 4 and p.commit_w == 4) else 0, width=1)
    m.output("ooo_4wide", ooo_4wide)
    m.output("block_cmd_valid", block_cmd_valid)
    m.output("block_cmd_kind", block_cmd_kind)
    m.output("block_cmd_payload", block_cmd_payload)
    m.output("block_cmd_tile", block_cmd_tile)
    m.output("block_cmd_tag", block_cmd_tag)

    return BccOooExports(
        clk=clk,
        rst=rst,
        block_cmd_valid=block_cmd_valid.sig,
        block_cmd_kind=block_cmd_kind.sig,
        block_cmd_payload=block_cmd_payload.sig,
        block_cmd_tile=block_cmd_tile.sig,
        block_cmd_tag=block_cmd_tag.sig,
        cycles=state.cycles.out().sig,
        halted=state.halted.out().sig,
    )
