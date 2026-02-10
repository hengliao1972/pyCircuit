from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire, jit_inline

from ..isa import (
    OP_ADDTPC,
    OP_ADDI,
    OP_ADDIW,
    OP_ADD,
    OP_ADDW,
    OP_AND,
    OP_ANDI,
    OP_ANDIW,
    OP_ANDW,
    OP_BSTART_STD_CALL,
    OP_BSTART_STD_COND,
    OP_BSTART_STD_DIRECT,
    OP_BSTART_STD_FALL,
    OP_BXS,
    OP_BXU,
    OP_CMP_EQ,
    OP_CMP_EQI,
    OP_CMP_NE,
    OP_CMP_NEI,
    OP_CMP_ANDI,
    OP_CMP_ORI,
    OP_CMP_LT,
    OP_CMP_LTI,
    OP_CMP_LTUI,
    OP_CMP_LTU,
    OP_CMP_GEI,
    OP_CMP_GEUI,
    OP_C_ADD,
    OP_C_ADDI,
    OP_C_AND,
    OP_C_OR,
    OP_C_SUB,
    OP_CSEL,
    OP_C_BSTART_DIRECT,
    OP_C_BSTART_COND,
    OP_C_BSTART_STD,
    OP_C_LDI,
    OP_C_LWI,
    OP_C_MOVI,
    OP_C_MOVR,
    OP_C_SETC_EQ,
    OP_C_SETC_NE,
    OP_C_SETC_TGT,
    OP_C_SDI,
    OP_C_SEXT_W,
    OP_C_SETRET,
    OP_C_SWI,
    OP_C_ZEXT_W,
    OP_FENTRY,
    OP_FEXIT,
    OP_FRET_RA,
    OP_FRET_STK,
    OP_HL_LB_PCR,
    OP_HL_LBU_PCR,
    OP_HL_LD_PCR,
    OP_HL_LH_PCR,
    OP_HL_LHU_PCR,
    OP_HL_LW_PCR,
    OP_HL_LUI,
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
    OP_LDI,
    OP_LUI,
    OP_LW,
    OP_LWI,
    OP_LWU,
    OP_LWUI,
    OP_MADD,
    OP_MADDW,
    OP_MUL,
    OP_MULW,
    OP_OR,
    OP_ORI,
    OP_ORIW,
    OP_ORW,
    OP_XOR,
    OP_XORIW,
    OP_DIV,
    OP_DIVU,
    OP_DIVW,
    OP_DIVUW,
    OP_REM,
    OP_REMU,
    OP_REMW,
    OP_REMUW,
    OP_SB,
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
    OP_SETRET,
    OP_SBI,
    OP_SD,
    OP_SH,
    OP_SHI,
    OP_SDI,
    OP_SLL,
    OP_SLLI,
    OP_SLLIW,
    OP_SRL,
    OP_SRA,
    OP_SRAIW,
    OP_SRLIW,
    OP_SW,
    OP_SUB,
    OP_SUBI,
    OP_SUBIW,
    OP_SUBW,
    OP_SWI,
    OP_XORW,
)
from ..util import Consts, ashr_var, lshr_var, shl_var


@dataclass(frozen=True)
class ExecOut:
    alu: Wire
    is_load: Wire
    is_store: Wire
    size: Wire
    addr: Wire
    wdata: Wire


def exec_uop_comb(
    m: Circuit,
    *,
    op: Wire,
    pc: Wire,
    imm: Wire,
    srcl_val: Wire,
    srcr_val: Wire,
    srcr_type: Wire,
    shamt: Wire,
    srcp_val: Wire,
    consts: Consts,
) -> ExecOut:
    """Combinational exec for python-mode builders (no `if Wire:` / no `==` on Wire).

    The OOO core builder (`ooo/linxcore.py`) executes as a normal Python helper
    during JIT compilation. That means any use of `if <Wire>:` will raise, and
    `wire == const` is Python dataclass equality (not a hardware compare).

    This function is written in "select-chain" style so it can be called from
    such helpers. It is still safe to inline in JIT mode if desired.
    """
    c = m.const

    z1 = consts.zero1
    z4 = consts.zero4
    z64 = consts.zero64
    one1 = consts.one1
    one64 = consts.one64

    # Sizes (bytes).
    sz1 = c(1, width=4)
    sz2 = c(2, width=4)
    sz4 = c(4, width=4)
    sz8 = c(8, width=4)

    pc = pc.out()
    op = op.out()
    imm = imm.out()
    srcl_val = srcl_val.out()
    srcr_val = srcr_val.out()
    srcr_type = srcr_type.out()
    shamt = shamt.out()
    srcp_val = srcp_val.out()

    # --- op predicates ---
    op_c_bstart_std = op.eq(OP_C_BSTART_STD)
    op_c_bstart_cond = op.eq(OP_C_BSTART_COND)
    op_c_bstart_direct = op.eq(OP_C_BSTART_DIRECT)
    op_bstart_std_fall = op.eq(OP_BSTART_STD_FALL)
    op_bstart_std_direct = op.eq(OP_BSTART_STD_DIRECT)
    op_bstart_std_cond = op.eq(OP_BSTART_STD_COND)
    op_bstart_std_call = op.eq(OP_BSTART_STD_CALL)
    op_fentry = op.eq(OP_FENTRY)
    op_fexit = op.eq(OP_FEXIT)
    op_fret_ra = op.eq(OP_FRET_RA)
    op_fret_stk = op.eq(OP_FRET_STK)
    op_c_movr = op.eq(OP_C_MOVR)
    op_c_movi = op.eq(OP_C_MOVI)
    op_c_setret = op.eq(OP_C_SETRET)
    op_c_setc_eq = op.eq(OP_C_SETC_EQ)
    op_c_setc_ne = op.eq(OP_C_SETC_NE)
    op_c_setc_tgt = op.eq(OP_C_SETC_TGT)
    op_setret = op.eq(OP_SETRET)
    op_addtpc = op.eq(OP_ADDTPC)
    op_lui = op.eq(OP_LUI)
    op_add = op.eq(OP_ADD)
    op_sub = op.eq(OP_SUB)
    op_and = op.eq(OP_AND)
    op_or = op.eq(OP_OR)
    op_xor = op.eq(OP_XOR)
    op_addi = op.eq(OP_ADDI)
    op_subi = op.eq(OP_SUBI)
    op_andi = op.eq(OP_ANDI)
    op_ori = op.eq(OP_ORI)
    op_addiw = op.eq(OP_ADDIW)
    op_subiw = op.eq(OP_SUBIW)
    op_andiw = op.eq(OP_ANDIW)
    op_oriw = op.eq(OP_ORIW)
    op_xoriw = op.eq(OP_XORIW)
    op_mul = op.eq(OP_MUL)
    op_mulw = op.eq(OP_MULW)
    op_madd = op.eq(OP_MADD)
    op_maddw = op.eq(OP_MADDW)
    op_div = op.eq(OP_DIV)
    op_divu = op.eq(OP_DIVU)
    op_divw = op.eq(OP_DIVW)
    op_divuw = op.eq(OP_DIVUW)
    op_rem = op.eq(OP_REM)
    op_remu = op.eq(OP_REMU)
    op_remw = op.eq(OP_REMW)
    op_remuw = op.eq(OP_REMUW)
    op_sll = op.eq(OP_SLL)
    op_srl = op.eq(OP_SRL)
    op_sra = op.eq(OP_SRA)
    op_slli = op.eq(OP_SLLI)
    op_slliw = op.eq(OP_SLLIW)
    op_sraiw = op.eq(OP_SRAIW)
    op_srliw = op.eq(OP_SRLIW)
    op_bxs = op.eq(OP_BXS)
    op_bxu = op.eq(OP_BXU)
    op_addw = op.eq(OP_ADDW)
    op_subw = op.eq(OP_SUBW)
    op_orw = op.eq(OP_ORW)
    op_andw = op.eq(OP_ANDW)
    op_xorw = op.eq(OP_XORW)
    op_cmp_eq = op.eq(OP_CMP_EQ)
    op_cmp_ne = op.eq(OP_CMP_NE)
    op_cmp_lt = op.eq(OP_CMP_LT)
    op_cmp_eqi = op.eq(OP_CMP_EQI)
    op_cmp_nei = op.eq(OP_CMP_NEI)
    op_cmp_andi = op.eq(OP_CMP_ANDI)
    op_cmp_ori = op.eq(OP_CMP_ORI)
    op_cmp_lti = op.eq(OP_CMP_LTI)
    op_cmp_ltu = op.eq(OP_CMP_LTU)
    op_cmp_ltui = op.eq(OP_CMP_LTUI)
    op_cmp_gei = op.eq(OP_CMP_GEI)
    op_cmp_geui = op.eq(OP_CMP_GEUI)
    op_setc_geui = op.eq(OP_SETC_GEUI)
    op_setc_eq = op.eq(OP_SETC_EQ)
    op_setc_ne = op.eq(OP_SETC_NE)
    op_setc_and = op.eq(OP_SETC_AND)
    op_setc_or = op.eq(OP_SETC_OR)
    op_setc_lt = op.eq(OP_SETC_LT)
    op_setc_ltu = op.eq(OP_SETC_LTU)
    op_setc_ge = op.eq(OP_SETC_GE)
    op_setc_geu = op.eq(OP_SETC_GEU)
    op_setc_eqi = op.eq(OP_SETC_EQI)
    op_setc_nei = op.eq(OP_SETC_NEI)
    op_setc_andi = op.eq(OP_SETC_ANDI)
    op_setc_ori = op.eq(OP_SETC_ORI)
    op_setc_lti = op.eq(OP_SETC_LTI)
    op_setc_gei = op.eq(OP_SETC_GEI)
    op_setc_ltui = op.eq(OP_SETC_LTUI)
    op_csel = op.eq(OP_CSEL)
    op_hl_lui = op.eq(OP_HL_LUI)
    op_hl_lb_pcr = op.eq(OP_HL_LB_PCR)
    op_hl_lbu_pcr = op.eq(OP_HL_LBU_PCR)
    op_hl_lh_pcr = op.eq(OP_HL_LH_PCR)
    op_hl_lhu_pcr = op.eq(OP_HL_LHU_PCR)
    op_hl_lw_pcr = op.eq(OP_HL_LW_PCR)
    op_hl_lwu_pcr = op.eq(OP_HL_LWU_PCR)
    op_hl_ld_pcr = op.eq(OP_HL_LD_PCR)
    op_hl_sb_pcr = op.eq(OP_HL_SB_PCR)
    op_hl_sh_pcr = op.eq(OP_HL_SH_PCR)
    op_hl_sw_pcr = op.eq(OP_HL_SW_PCR)
    op_hl_sd_pcr = op.eq(OP_HL_SD_PCR)
    op_lwi = op.eq(OP_LWI)
    op_c_lwi = op.eq(OP_C_LWI)
    op_lbi = op.eq(OP_LBI)
    op_lbui = op.eq(OP_LBUI)
    op_lhi = op.eq(OP_LHI)
    op_lhui = op.eq(OP_LHUI)
    op_lwui = op.eq(OP_LWUI)
    op_lb = op.eq(OP_LB)
    op_lbu = op.eq(OP_LBU)
    op_lh = op.eq(OP_LH)
    op_lhu = op.eq(OP_LHU)
    op_lw = op.eq(OP_LW)
    op_lwu = op.eq(OP_LWU)
    op_ld = op.eq(OP_LD)
    op_ldi = op.eq(OP_LDI)
    op_c_add = op.eq(OP_C_ADD)
    op_c_addi = op.eq(OP_C_ADDI)
    op_c_sub = op.eq(OP_C_SUB)
    op_c_and = op.eq(OP_C_AND)
    op_c_or = op.eq(OP_C_OR)
    op_c_ldi = op.eq(OP_C_LDI)
    op_sbi = op.eq(OP_SBI)
    op_shi = op.eq(OP_SHI)
    op_swi = op.eq(OP_SWI)
    op_c_swi = op.eq(OP_C_SWI)
    op_c_sdi = op.eq(OP_C_SDI)
    op_sb = op.eq(OP_SB)
    op_sh = op.eq(OP_SH)
    op_sw = op.eq(OP_SW)
    op_sd = op.eq(OP_SD)
    op_c_sext_w = op.eq(OP_C_SEXT_W)
    op_c_zext_w = op.eq(OP_C_ZEXT_W)
    op_sdi = op.eq(OP_SDI)

    # Defaults.
    alu = z64
    is_load = z1
    is_store = z1
    size = z4
    addr = z64
    wdata = z64

    # --- SrcR modifiers (srcr_type is a 2b mode code) ---
    st0 = srcr_type.eq(0)
    st1 = srcr_type.eq(1)
    st2 = srcr_type.eq(2)

    srcr_addsub = srcr_val
    srcr_addsub = st0.select(srcr_val.trunc(width=32).sext(width=64), srcr_addsub)
    srcr_addsub = st1.select(srcr_val.trunc(width=32).zext(width=64), srcr_addsub)
    srcr_addsub = st2.select((~srcr_val) + 1, srcr_addsub)

    srcr_logic = srcr_val
    srcr_logic = st0.select(srcr_val.trunc(width=32).sext(width=64), srcr_logic)
    srcr_logic = st1.select(srcr_val.trunc(width=32).zext(width=64), srcr_logic)
    srcr_logic = st2.select(~srcr_val, srcr_logic)

    srcr_addsub_nosh = srcr_addsub
    srcr_addsub_shl = shl_var(m, srcr_addsub, shamt)
    srcr_logic_shl = shl_var(m, srcr_logic, shamt)

    idx_mod = srcr_val.trunc(width=32).zext(width=64)
    idx_mod = st0.select(srcr_val.trunc(width=32).sext(width=64), idx_mod)
    idx_mod_shl = shl_var(m, idx_mod, shamt)

    # Common offsets.
    off_w = imm.shl(amount=2)
    h_off = imm.shl(amount=1)
    ldi_off = imm.shl(amount=3)

    # --- boundary/macro markers (treat as immediate producers) ---
    is_marker = (
        op_c_bstart_std
        | op_c_bstart_cond
        | op_c_bstart_direct
        | op_bstart_std_fall
        | op_bstart_std_direct
        | op_bstart_std_cond
        | op_bstart_std_call
        | op_fentry
        | op_fexit
        | op_fret_ra
        | op_fret_stk
    )
    alu = is_marker.select(imm, alu)

    # --- basic ALU ops ---
    alu = op_c_movr.select(srcl_val, alu)
    alu = op_c_movi.select(imm, alu)
    alu = op_c_setret.select(pc + imm, alu)

    setc_eq = srcl_val.eq(srcr_val).select(one64, z64)
    alu = op_c_setc_eq.select(setc_eq, alu)
    alu = op_c_setc_ne.select((~srcl_val.eq(srcr_val)).select(one64, z64), alu)
    alu = op_c_setc_tgt.select(srcl_val, alu)

    pc_page = pc & 0xFFFF_FFFF_FFFF_F000
    alu = op_addtpc.select(pc_page + imm, alu)

    alu = op_addi.select(srcl_val + imm, alu)
    alu = op_subi.select(srcl_val - imm, alu)

    addiw = (srcl_val.trunc(width=32) + imm.trunc(width=32)).sext(width=64)
    subiw = (srcl_val.trunc(width=32) - imm.trunc(width=32)).sext(width=64)
    alu = op_addiw.select(addiw, alu)
    alu = op_subiw.select(subiw, alu)

    alu = op_lui.select(imm, alu)
    alu = op_setret.select(pc + imm, alu)

    alu = op_add.select(srcl_val + srcr_addsub_shl, alu)
    alu = op_sub.select(srcl_val - srcr_addsub_shl, alu)
    alu = op_and.select(srcl_val & srcr_logic_shl, alu)
    alu = op_or.select(srcl_val | srcr_logic_shl, alu)
    alu = op_xor.select(srcl_val ^ srcr_logic_shl, alu)

    alu = op_andi.select(srcl_val & imm, alu)
    alu = op_ori.select(srcl_val | imm, alu)
    alu = op_andiw.select((srcl_val & imm).trunc(width=32).sext(width=64), alu)
    alu = op_oriw.select((srcl_val | imm).trunc(width=32).sext(width=64), alu)
    alu = op_xoriw.select((srcl_val ^ imm).trunc(width=32).sext(width=64), alu)

    alu = op_mul.select(srcl_val * srcr_val, alu)
    alu = op_mulw.select((srcl_val * srcr_val).trunc(width=32).sext(width=64), alu)
    alu = op_madd.select(srcp_val + (srcl_val * srcr_val), alu)
    alu = op_maddw.select((srcp_val + (srcl_val * srcr_val)).trunc(width=32).sext(width=64), alu)

    alu = op_div.select(srcl_val.as_signed() // srcr_val.as_signed(), alu)
    alu = op_divu.select(srcl_val.as_unsigned() // srcr_val.as_unsigned(), alu)

    divw_l32 = srcl_val.trunc(width=32).sext(width=64).as_signed()
    divw_r32 = srcr_val.trunc(width=32).sext(width=64).as_signed()
    alu = op_divw.select((divw_l32 // divw_r32).trunc(width=32).sext(width=64), alu)

    divuw_l32 = srcl_val.trunc(width=32).zext(width=64).as_unsigned()
    divuw_r32 = srcr_val.trunc(width=32).zext(width=64).as_unsigned()
    alu = op_divuw.select((divuw_l32 // divuw_r32).trunc(width=32).sext(width=64), alu)

    alu = op_rem.select(srcl_val.as_signed() % srcr_val.as_signed(), alu)
    alu = op_remu.select(srcl_val.as_unsigned() % srcr_val.as_unsigned(), alu)

    remw_l32 = srcl_val.trunc(width=32).sext(width=64).as_signed()
    remw_r32 = srcr_val.trunc(width=32).sext(width=64).as_signed()
    alu = op_remw.select((remw_l32 % remw_r32).trunc(width=32).sext(width=64), alu)

    remuw_l32 = srcl_val.trunc(width=32).zext(width=64).as_unsigned()
    remuw_r32 = srcr_val.trunc(width=32).zext(width=64).as_unsigned()
    alu = op_remuw.select((remuw_l32 % remuw_r32).trunc(width=32).sext(width=64), alu)

    alu = op_sll.select(shl_var(m, srcl_val, srcr_val), alu)
    alu = op_srl.select(lshr_var(m, srcl_val, srcr_val), alu)
    alu = op_sra.select(ashr_var(m, srcl_val, srcr_val), alu)
    alu = op_slli.select(shl_var(m, srcl_val, shamt), alu)

    sh5 = shamt & 0x1F
    slliw_val = shl_var(m, srcl_val.trunc(width=32).zext(width=64), sh5).trunc(width=32).sext(width=64)
    sraiw_val = ashr_var(m, srcl_val.trunc(width=32).sext(width=64), sh5).trunc(width=32).sext(width=64)
    srliw_val = lshr_var(m, srcl_val.trunc(width=32).zext(width=64), sh5).trunc(width=32).sext(width=64)
    alu = op_slliw.select(slliw_val, alu)
    alu = op_sraiw.select(sraiw_val, alu)
    alu = op_srliw.select(srliw_val, alu)

    # Bit extract ops (BXS: sign-ext, BXU: zero-ext).
    imms = srcr_val
    imml = srcp_val
    shifted = lshr_var(m, srcl_val, imms)
    sh_mask_amt = c(63, width=64) - imml.zext(width=64)
    mask = lshr_var(m, c(0xFFFF_FFFF_FFFF_FFFF, width=64), sh_mask_amt)
    extracted = shifted & mask
    valid_bx = (imms.zext(width=64) + imml.zext(width=64)).ule(63)
    sext_bxs = ashr_var(m, shl_var(m, extracted, sh_mask_amt), sh_mask_amt)
    alu = op_bxs.select(valid_bx.select(sext_bxs, z64), alu)
    alu = op_bxu.select(valid_bx.select(extracted, z64), alu)

    addw = (srcl_val + srcr_addsub_shl).trunc(width=32).sext(width=64)
    subw = (srcl_val - srcr_addsub_shl).trunc(width=32).sext(width=64)
    orw = (srcl_val | srcr_logic_shl).trunc(width=32).sext(width=64)
    andw = (srcl_val & srcr_logic_shl).trunc(width=32).sext(width=64)
    xorw = (srcl_val ^ srcr_logic_shl).trunc(width=32).sext(width=64)
    alu = op_addw.select(addw, alu)
    alu = op_subw.select(subw, alu)
    alu = op_orw.select(orw, alu)
    alu = op_andw.select(andw, alu)
    alu = op_xorw.select(xorw, alu)

    # --- CMP.* (write 0/1 to RegDst) ---
    alu = op_cmp_eq.select(srcl_val.eq(srcr_addsub_nosh).select(one64, z64), alu)
    alu = op_cmp_ne.select((~srcl_val.eq(srcr_addsub_nosh)).select(one64, z64), alu)
    alu = op_cmp_lt.select(srcl_val.slt(srcr_addsub_nosh).select(one64, z64), alu)
    alu = op_cmp_eqi.select(srcl_val.eq(imm).select(one64, z64), alu)
    alu = op_cmp_nei.select((~srcl_val.eq(imm)).select(one64, z64), alu)
    alu = op_cmp_andi.select((~(srcl_val & imm).eq(0)).select(one64, z64), alu)
    alu = op_cmp_ori.select((~(srcl_val | imm).eq(0)).select(one64, z64), alu)
    alu = op_cmp_lti.select(srcl_val.slt(imm).select(one64, z64), alu)
    alu = op_cmp_gei.select((~srcl_val.slt(imm)).select(one64, z64), alu)
    alu = op_cmp_ltu.select(srcl_val.ult(srcr_addsub_nosh).select(one64, z64), alu)
    alu = op_cmp_ltui.select(srcl_val.ult(imm).select(one64, z64), alu)
    alu = op_cmp_geui.select(srcl_val.uge(imm).select(one64, z64), alu)

    # --- SETC.* (write 0/1; commit stage consumes val[0]) ---
    uimm_sh = shl_var(m, imm, shamt)
    simm_sh = uimm_sh

    alu = op_setc_geui.select(srcl_val.uge(uimm_sh).select(one64, z64), alu)
    alu = op_setc_eqi.select(srcl_val.eq(simm_sh).select(one64, z64), alu)
    alu = op_setc_nei.select((~srcl_val.eq(simm_sh)).select(one64, z64), alu)
    alu = op_setc_andi.select((~(srcl_val & simm_sh).eq(0)).select(one64, z64), alu)
    alu = op_setc_ori.select((~(srcl_val | simm_sh).eq(0)).select(one64, z64), alu)
    alu = op_setc_lti.select(srcl_val.slt(simm_sh).select(one64, z64), alu)
    alu = op_setc_gei.select((~srcl_val.slt(simm_sh)).select(one64, z64), alu)
    alu = op_setc_ltui.select(srcl_val.ult(uimm_sh).select(one64, z64), alu)

    alu = op_setc_eq.select(srcl_val.eq(srcr_addsub_nosh).select(one64, z64), alu)
    alu = op_setc_ne.select((~srcl_val.eq(srcr_addsub_nosh)).select(one64, z64), alu)
    alu = op_setc_and.select((~(srcl_val & srcr_logic).eq(0)).select(one64, z64), alu)
    alu = op_setc_or.select((~(srcl_val | srcr_logic).eq(0)).select(one64, z64), alu)
    alu = op_setc_lt.select(srcl_val.slt(srcr_addsub_nosh).select(one64, z64), alu)
    alu = op_setc_ltu.select(srcl_val.ult(srcr_addsub_nosh).select(one64, z64), alu)
    alu = op_setc_ge.select((~srcl_val.slt(srcr_addsub_nosh)).select(one64, z64), alu)
    alu = op_setc_geu.select(srcl_val.uge(srcr_addsub_nosh).select(one64, z64), alu)

    alu = op_hl_lui.select(imm, alu)

    # CSEL: select srcr (shifted) when SrcP != 0.
    alu = op_csel.select((~srcp_val.eq(0)).select(srcr_addsub_nosh, srcl_val), alu)

    # --- memory ops (address/size/data) ---
    is_lwi = op_lwi | op_c_lwi
    lwi_addr = srcl_val + off_w
    is_load = is_lwi.select(one1, is_load)
    size = is_lwi.select(sz4, size)
    addr = is_lwi.select(lwi_addr, addr)

    is_load = op_lwui.select(one1, is_load)
    size = op_lwui.select(sz4, size)
    addr = op_lwui.select(lwi_addr, addr)

    is_lbi_any = op_lbi | op_lbui
    is_load = is_lbi_any.select(one1, is_load)
    size = is_lbi_any.select(sz1, size)
    addr = is_lbi_any.select(srcl_val + imm, addr)

    is_lhi_any = op_lhi | op_lhui
    is_load = is_lhi_any.select(one1, is_load)
    size = is_lhi_any.select(sz2, size)
    addr = is_lhi_any.select(srcl_val + h_off, addr)

    idx_addr = srcl_val + idx_mod_shl
    is_load = (op_lb | op_lbu).select(one1, is_load)
    size = (op_lb | op_lbu).select(sz1, size)
    addr = (op_lb | op_lbu).select(idx_addr, addr)

    is_load = (op_lh | op_lhu).select(one1, is_load)
    size = (op_lh | op_lhu).select(sz2, size)
    addr = (op_lh | op_lhu).select(idx_addr, addr)

    is_load = (op_lw | op_lwu).select(one1, is_load)
    size = (op_lw | op_lwu).select(sz4, size)
    addr = (op_lw | op_lwu).select(idx_addr, addr)

    is_load = op_ld.select(one1, is_load)
    size = op_ld.select(sz8, size)
    addr = op_ld.select(idx_addr, addr)

    is_load = (op_ldi | op_c_ldi).select(one1, is_load)
    size = (op_ldi | op_c_ldi).select(sz8, size)
    addr = (op_ldi | op_c_ldi).select(srcl_val + ldi_off, addr)

    # Stores.
    is_store = op_sbi.select(one1, is_store)
    size = op_sbi.select(sz1, size)
    addr = op_sbi.select(srcr_val + imm, addr)
    wdata = op_sbi.select(srcl_val, wdata)

    is_store = op_shi.select(one1, is_store)
    size = op_shi.select(sz2, size)
    addr = op_shi.select(srcr_val + h_off, addr)
    wdata = op_shi.select(srcl_val, wdata)

    store_addr_def = srcl_val + off_w
    store_data_def = srcr_val
    store_addr = op_swi.select(srcr_val + off_w, store_addr_def)
    store_data = op_swi.select(srcl_val, store_data_def)
    op_swi_any = op_swi | op_c_swi
    is_store = op_swi_any.select(one1, is_store)
    size = op_swi_any.select(sz4, size)
    addr = op_swi_any.select(store_addr, addr)
    wdata = op_swi_any.select(store_data, wdata)

    is_store = op_sb.select(one1, is_store)
    size = op_sb.select(sz1, size)
    addr = op_sb.select(idx_addr, addr)
    wdata = op_sb.select(srcp_val, wdata)

    is_store = op_sh.select(one1, is_store)
    size = op_sh.select(sz2, size)
    addr = op_sh.select(idx_addr, addr)
    wdata = op_sh.select(srcp_val, wdata)

    is_store = op_sw.select(one1, is_store)
    size = op_sw.select(sz4, size)
    addr = op_sw.select(idx_addr, addr)
    wdata = op_sw.select(srcp_val, wdata)

    is_store = op_sd.select(one1, is_store)
    size = op_sd.select(sz8, size)
    addr = op_sd.select(idx_addr, addr)
    wdata = op_sd.select(srcp_val, wdata)

    sdi_off = imm.shl(amount=3)
    is_store = op_c_sdi.select(one1, is_store)
    size = op_c_sdi.select(sz8, size)
    addr = op_c_sdi.select(srcl_val + sdi_off, addr)
    wdata = op_c_sdi.select(srcr_val, wdata)

    sdi_addr = srcr_val + sdi_off
    is_store = op_sdi.select(one1, is_store)
    size = op_sdi.select(sz8, size)
    addr = op_sdi.select(sdi_addr, addr)
    wdata = op_sdi.select(srcl_val, wdata)

    # HL loads/stores (PC-relative).
    hl_addr = pc + imm
    hl_load_b = op_hl_lb_pcr | op_hl_lbu_pcr
    hl_load_h = op_hl_lh_pcr | op_hl_lhu_pcr
    hl_load_w = op_hl_lw_pcr | op_hl_lwu_pcr
    is_load = hl_load_b.select(one1, is_load)
    size = hl_load_b.select(sz1, size)
    addr = hl_load_b.select(hl_addr, addr)
    is_load = hl_load_h.select(one1, is_load)
    size = hl_load_h.select(sz2, size)
    addr = hl_load_h.select(hl_addr, addr)
    is_load = hl_load_w.select(one1, is_load)
    size = hl_load_w.select(sz4, size)
    addr = hl_load_w.select(hl_addr, addr)
    is_load = op_hl_ld_pcr.select(one1, is_load)
    size = op_hl_ld_pcr.select(sz8, size)
    addr = op_hl_ld_pcr.select(hl_addr, addr)

    is_store = op_hl_sb_pcr.select(one1, is_store)
    size = op_hl_sb_pcr.select(sz1, size)
    addr = op_hl_sb_pcr.select(hl_addr, addr)
    wdata = op_hl_sb_pcr.select(srcl_val, wdata)
    is_store = op_hl_sh_pcr.select(one1, is_store)
    size = op_hl_sh_pcr.select(sz2, size)
    addr = op_hl_sh_pcr.select(hl_addr, addr)
    wdata = op_hl_sh_pcr.select(srcl_val, wdata)
    is_store = op_hl_sw_pcr.select(one1, is_store)
    size = op_hl_sw_pcr.select(sz4, size)
    addr = op_hl_sw_pcr.select(hl_addr, addr)
    wdata = op_hl_sw_pcr.select(srcl_val, wdata)
    is_store = op_hl_sd_pcr.select(one1, is_store)
    size = op_hl_sd_pcr.select(sz8, size)
    addr = op_hl_sd_pcr.select(hl_addr, addr)
    wdata = op_hl_sd_pcr.select(srcl_val, wdata)

    # Compressed integer ops.
    alu = op_c_addi.select(srcl_val + imm, alu)
    alu = op_c_add.select(srcl_val + srcr_val, alu)
    alu = op_c_sub.select(srcl_val - srcr_val, alu)
    alu = op_c_and.select(srcl_val & srcr_val, alu)
    alu = op_c_or.select(srcl_val | srcr_val, alu)
    alu = op_c_sext_w.select(srcl_val.trunc(width=32).sext(width=64), alu)
    alu = op_c_zext_w.select(srcl_val.trunc(width=32).zext(width=64), alu)

    return ExecOut(alu=alu, is_load=is_load, is_store=is_store, size=size, addr=addr, wdata=wdata)


@jit_inline
def exec_uop(
    m: Circuit,
    *,
    op: Wire,
    pc: Wire,
    imm: Wire,
    srcl_val: Wire,
    srcr_val: Wire,
    srcr_type: Wire,
    shamt: Wire,
    srcp_val: Wire,
    consts: Consts,
) -> ExecOut:
    with m.scope("exec"):
        z1 = consts.zero1
        z4 = consts.zero4
        z64 = consts.zero64

        pc = pc.out()
        op = op.out()
        imm = imm.out()
        srcl_val = srcl_val.out()
        srcr_val = srcr_val.out()
        srcr_type = srcr_type.out()
        shamt = shamt.out()
        srcp_val = srcp_val.out()

        op_c_bstart_std = op == OP_C_BSTART_STD
        op_c_bstart_cond = op == OP_C_BSTART_COND
        op_c_bstart_direct = op == OP_C_BSTART_DIRECT
        op_bstart_std_fall = op == OP_BSTART_STD_FALL
        op_bstart_std_direct = op == OP_BSTART_STD_DIRECT
        op_bstart_std_cond = op == OP_BSTART_STD_COND
        op_bstart_std_call = op == OP_BSTART_STD_CALL
        op_fentry = op == OP_FENTRY
        op_fexit = op == OP_FEXIT
        op_fret_ra = op == OP_FRET_RA
        op_fret_stk = op == OP_FRET_STK
        op_c_movr = op == OP_C_MOVR
        op_c_movi = op == OP_C_MOVI
        op_c_setret = op == OP_C_SETRET
        op_c_setc_eq = op == OP_C_SETC_EQ
        op_c_setc_ne = op == OP_C_SETC_NE
        op_c_setc_tgt = op == OP_C_SETC_TGT
        op_setret = op == OP_SETRET
        op_addtpc = op == OP_ADDTPC
        op_lui = op == OP_LUI
        op_add = op == OP_ADD
        op_sub = op == OP_SUB
        op_and = op == OP_AND
        op_or = op == OP_OR
        op_xor = op == OP_XOR
        op_addi = op == OP_ADDI
        op_subi = op == OP_SUBI
        op_andi = op == OP_ANDI
        op_ori = op == OP_ORI
        op_addiw = op == OP_ADDIW
        op_subiw = op == OP_SUBIW
        op_andiw = op == OP_ANDIW
        op_oriw = op == OP_ORIW
        op_xoriw = op == OP_XORIW
        op_mul = op == OP_MUL
        op_mulw = op == OP_MULW
        op_madd = op == OP_MADD
        op_maddw = op == OP_MADDW
        op_div = op == OP_DIV
        op_divu = op == OP_DIVU
        op_divw = op == OP_DIVW
        op_divuw = op == OP_DIVUW
        op_rem = op == OP_REM
        op_remu = op == OP_REMU
        op_remw = op == OP_REMW
        op_remuw = op == OP_REMUW
        op_sll = op == OP_SLL
        op_srl = op == OP_SRL
        op_sra = op == OP_SRA
        op_slli = op == OP_SLLI
        op_slliw = op == OP_SLLIW
        op_sraiw = op == OP_SRAIW
        op_srliw = op == OP_SRLIW
        op_bxs = op == OP_BXS
        op_bxu = op == OP_BXU
        op_addw = op == OP_ADDW
        op_subw = op == OP_SUBW
        op_orw = op == OP_ORW
        op_andw = op == OP_ANDW
        op_xorw = op == OP_XORW
        op_cmp_eq = op == OP_CMP_EQ
        op_cmp_ne = op == OP_CMP_NE
        op_cmp_lt = op == OP_CMP_LT
        op_cmp_eqi = op == OP_CMP_EQI
        op_cmp_nei = op == OP_CMP_NEI
        op_cmp_andi = op == OP_CMP_ANDI
        op_cmp_ori = op == OP_CMP_ORI
        op_cmp_lti = op == OP_CMP_LTI
        op_cmp_ltu = op == OP_CMP_LTU
        op_cmp_ltui = op == OP_CMP_LTUI
        op_cmp_gei = op == OP_CMP_GEI
        op_cmp_geui = op == OP_CMP_GEUI
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
        op_csel = op == OP_CSEL
        op_hl_lui = op == OP_HL_LUI
        op_hl_lb_pcr = op == OP_HL_LB_PCR
        op_hl_lbu_pcr = op == OP_HL_LBU_PCR
        op_hl_lh_pcr = op == OP_HL_LH_PCR
        op_hl_lhu_pcr = op == OP_HL_LHU_PCR
        op_hl_lw_pcr = op == OP_HL_LW_PCR
        op_hl_lwu_pcr = op == OP_HL_LWU_PCR
        op_hl_ld_pcr = op == OP_HL_LD_PCR
        op_hl_sb_pcr = op == OP_HL_SB_PCR
        op_hl_sh_pcr = op == OP_HL_SH_PCR
        op_hl_sw_pcr = op == OP_HL_SW_PCR
        op_hl_sd_pcr = op == OP_HL_SD_PCR
        op_lwi = op == OP_LWI
        op_c_lwi = op == OP_C_LWI
        op_lbi = op == OP_LBI
        op_lbui = op == OP_LBUI
        op_lhi = op == OP_LHI
        op_lhui = op == OP_LHUI
        op_lwui = op == OP_LWUI
        op_lb = op == OP_LB
        op_lbu = op == OP_LBU
        op_lh = op == OP_LH
        op_lhu = op == OP_LHU
        op_lw = op == OP_LW
        op_lwu = op == OP_LWU
        op_ld = op == OP_LD
        op_ldi = op == OP_LDI
        op_c_add = op == OP_C_ADD
        op_c_addi = op == OP_C_ADDI
        op_c_sub = op == OP_C_SUB
        op_c_and = op == OP_C_AND
        op_c_or = op == OP_C_OR
        op_c_ldi = op == OP_C_LDI
        op_sbi = op == OP_SBI
        op_shi = op == OP_SHI
        op_swi = op == OP_SWI
        op_c_swi = op == OP_C_SWI
        op_c_sdi = op == OP_C_SDI
        op_sb = op == OP_SB
        op_sh = op == OP_SH
        op_sw = op == OP_SW
        op_sd = op == OP_SD
        op_c_sext_w = op == OP_C_SEXT_W
        op_c_zext_w = op == OP_C_ZEXT_W
        op_sdi = op == OP_SDI

        off = imm.shl(amount=2)

        alu = z64
        is_load = z1
        is_store = z1
        size = z4
        addr = z64
        wdata = z64

        # SrcR modifiers.
        srcr_addsub = srcr_val
        if srcr_type == 0:
            srcr_addsub = srcr_val.trunc(width=32).sext(width=64)
        if srcr_type == 1:
            srcr_addsub = srcr_val.trunc(width=32).zext(width=64)
        if srcr_type == 2:
            srcr_addsub = (~srcr_val) + 1

        srcr_logic = srcr_val
        if srcr_type == 0:
            srcr_logic = srcr_val.trunc(width=32).sext(width=64)
        if srcr_type == 1:
            srcr_logic = srcr_val.trunc(width=32).zext(width=64)
        if srcr_type == 2:
            srcr_logic = ~srcr_val

        srcr_addsub_shl = shl_var(m, srcr_addsub, shamt)
        srcr_logic_shl = shl_var(m, srcr_logic, shamt)

        idx_mod = srcr_val.trunc(width=32).zext(width=64)
        if srcr_type == 0:
            idx_mod = srcr_val.trunc(width=32).sext(width=64)
        idx_mod_shl = shl_var(m, idx_mod, shamt)

        if (
            op_c_bstart_std
            | op_c_bstart_cond
            | op_c_bstart_direct
            | op_bstart_std_fall
            | op_bstart_std_direct
            | op_bstart_std_cond
            | op_bstart_std_call
            | op_fentry
            | op_fexit
            | op_fret_ra
            | op_fret_stk
        ):
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_c_movr:
            alu = srcl_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_c_movi:
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_c_setret:
            alu = pc + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        setc_eq = z64
        if srcl_val == srcr_val:
            setc_eq = 1
        if op_c_setc_eq:
            alu = setc_eq
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_c_setc_tgt:
            alu = srcl_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        pc_page = pc & 0xFFFF_FFFF_FFFF_F000
        if op_addtpc:
            alu = pc_page + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_addi:
            alu = srcl_val + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        subi = srcl_val + ((~imm) + 1)
        if op_subi:
            alu = subi
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        addiw = (srcl_val.trunc(width=32) + imm.trunc(width=32)).sext(width=64)
        if op_addiw:
            alu = addiw
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        subiw = (srcl_val.trunc(width=32) - imm.trunc(width=32)).sext(width=64)
        if op_subiw:
            alu = subiw
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_lui:
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_setret:
            alu = pc + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_add:
            alu = srcl_val + srcr_addsub_shl
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_sub:
            alu = srcl_val - srcr_addsub_shl
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_and:
            alu = srcl_val & srcr_logic_shl
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_or:
            alu = srcl_val | srcr_logic_shl
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_xor:
            alu = srcl_val ^ srcr_logic_shl
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_andi:
            alu = srcl_val & imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_ori:
            alu = srcl_val | imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_andiw:
            alu = (srcl_val & imm).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_oriw:
            alu = (srcl_val | imm).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_xoriw:
            alu = (srcl_val ^ imm).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_mul:
            alu = srcl_val * srcr_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_mulw:
            alu = (srcl_val * srcr_val).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_madd:
            alu = srcp_val + (srcl_val * srcr_val)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_maddw:
            alu = (srcp_val + (srcl_val * srcr_val)).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_div:
            alu = srcl_val.as_signed() // srcr_val.as_signed()
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_divu:
            alu = srcl_val.as_unsigned() // srcr_val.as_unsigned()
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_divw:
            l32 = srcl_val.trunc(width=32).sext(width=64).as_signed()
            r32 = srcr_val.trunc(width=32).sext(width=64).as_signed()
            alu = (l32 // r32).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_divuw:
            l32 = srcl_val.trunc(width=32).zext(width=64).as_unsigned()
            r32 = srcr_val.trunc(width=32).zext(width=64).as_unsigned()
            alu = (l32 // r32).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_rem:
            alu = srcl_val.as_signed() % srcr_val.as_signed()
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_remu:
            alu = srcl_val.as_unsigned() % srcr_val.as_unsigned()
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_remw:
            l32 = srcl_val.trunc(width=32).sext(width=64).as_signed()
            r32 = srcr_val.trunc(width=32).sext(width=64).as_signed()
            alu = (l32 % r32).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_remuw:
            l32 = srcl_val.trunc(width=32).zext(width=64).as_unsigned()
            r32 = srcr_val.trunc(width=32).zext(width=64).as_unsigned()
            alu = (l32 % r32).trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_sll:
            alu = shl_var(m, srcl_val, srcr_val)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_srl:
            alu = lshr_var(m, srcl_val, srcr_val)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_sra:
            alu = ashr_var(m, srcl_val, srcr_val)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_slli:
            alu = shl_var(m, srcl_val, shamt)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_slliw:
            l32 = srcl_val.trunc(width=32).zext(width=64)
            sh5 = shamt & 0x1F
            shifted = shl_var(m, l32, sh5)
            alu = shifted.trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_sraiw:
            l32 = srcl_val.trunc(width=32).sext(width=64)
            sh5 = shamt & 0x1F
            shifted = ashr_var(m, l32, sh5)
            alu = shifted.trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_srliw:
            l32 = srcl_val.trunc(width=32).zext(width=64)
            sh5 = shamt & 0x1F
            shifted = lshr_var(m, l32, sh5)
            alu = shifted.trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_bxs:
            imms = srcr_val
            imml = srcp_val
            shifted = lshr_var(m, srcl_val, imms)
            sh_mask_amt = m.const(63, width=64) - imml.zext(width=64)
            mask = lshr_var(m, m.const(0xFFFF_FFFF_FFFF_FFFF, width=64), sh_mask_amt)
            extracted = shifted & mask
            valid = (imms.zext(width=64) + imml.zext(width=64)).ule(63)
            sh_ext = sh_mask_amt
            sext = ashr_var(m, shl_var(m, extracted, sh_ext), sh_ext)
            alu = valid.select(sext, z64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_bxu:
            imms = srcr_val
            imml = srcp_val
            shifted = lshr_var(m, srcl_val, imms)
            sh_mask_amt = m.const(63, width=64) - imml.zext(width=64)
            mask = lshr_var(m, m.const(0xFFFF_FFFF_FFFF_FFFF, width=64), sh_mask_amt)
            extracted = shifted & mask
            valid = (imms.zext(width=64) + imml.zext(width=64)).ule(63)
            alu = valid.select(extracted, z64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        addw = (srcl_val + srcr_addsub_shl).trunc(width=32).sext(width=64)
        subw = (srcl_val - srcr_addsub_shl).trunc(width=32).sext(width=64)
        orw = (srcl_val | srcr_logic_shl).trunc(width=32).sext(width=64)
        andw = (srcl_val & srcr_logic_shl).trunc(width=32).sext(width=64)
        xorw = (srcl_val ^ srcr_logic_shl).trunc(width=32).sext(width=64)
        if op_addw:
            alu = addw
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_subw:
            alu = subw
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_orw:
            alu = orw
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_andw:
            alu = andw
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_xorw:
            alu = xorw
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        srcr_addsub_nosh = srcr_addsub
        cmp = z64
        if srcl_val == srcr_addsub_nosh:
            cmp = 1
        if op_cmp_eq:
            alu = cmp
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_ne = z64
        if srcl_val != srcr_addsub_nosh:
            cmp_ne = 1
        if op_cmp_ne:
            alu = cmp_ne
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_lt = z64
        if srcl_val.slt(srcr_addsub_nosh):
            cmp_lt = 1
        if op_cmp_lt:
            alu = cmp_lt
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_eqi = z64
        if srcl_val == imm:
            cmp_eqi = 1
        if op_cmp_eqi:
            alu = cmp_eqi
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_nei = z64
        if srcl_val != imm:
            cmp_nei = 1
        if op_cmp_nei:
            alu = cmp_nei
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_andi = z64
        if (srcl_val & imm) != 0:
            cmp_andi = 1
        if op_cmp_andi:
            alu = cmp_andi
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_ori = z64
        if (srcl_val | imm) != 0:
            cmp_ori = 1
        if op_cmp_ori:
            alu = cmp_ori
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_lti = z64
        if srcl_val.slt(imm):
            cmp_lti = 1
        if op_cmp_lti:
            alu = cmp_lti
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_gei = z64
        if ~srcl_val.slt(imm):
            cmp_gei = 1
        if op_cmp_gei:
            alu = cmp_gei
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_ltu = z64
        if srcl_val.ult(srcr_addsub_nosh):
            cmp_ltu = 1
        if op_cmp_ltu:
            alu = cmp_ltu
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_ltui = z64
        if srcl_val.ult(imm):
            cmp_ltui = 1
        if op_cmp_ltui:
            alu = cmp_ltui
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        cmp_geui = z64
        if srcl_val.uge(imm):
            cmp_geui = 1
        if op_cmp_geui:
            alu = cmp_geui
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_setc_geui:
            setc_bit = z64
            uimm = shl_var(m, imm, shamt)
            if srcl_val.uge(uimm):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_eqi:
            setc_bit = z64
            simm = shl_var(m, imm, shamt)
            if srcl_val == simm:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_nei:
            setc_bit = z64
            simm = shl_var(m, imm, shamt)
            if srcl_val != simm:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_andi:
            setc_bit = z64
            simm = shl_var(m, imm, shamt)
            if (srcl_val & simm) != 0:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_ori:
            setc_bit = z64
            simm = shl_var(m, imm, shamt)
            if (srcl_val | simm) != 0:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_lti:
            setc_bit = z64
            simm = shl_var(m, imm, shamt)
            if srcl_val.slt(simm):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_gei:
            setc_bit = z64
            simm = shl_var(m, imm, shamt)
            if ~srcl_val.slt(simm):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_ltui:
            setc_bit = z64
            uimm = shl_var(m, imm, shamt)
            if srcl_val.ult(uimm):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_eq:
            setc_bit = z64
            if srcl_val == srcr_addsub_nosh:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_ne:
            setc_bit = z64
            if srcl_val != srcr_addsub_nosh:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_and:
            setc_bit = z64
            if (srcl_val & srcr_logic) != 0:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_or:
            setc_bit = z64
            if (srcl_val | srcr_logic) != 0:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_lt:
            setc_bit = z64
            if srcl_val.slt(srcr_addsub_nosh):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_ltu:
            setc_bit = z64
            if srcl_val.ult(srcr_addsub_nosh):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_ge:
            setc_bit = z64
            if ~srcl_val.slt(srcr_addsub_nosh):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_setc_geu:
            setc_bit = z64
            if srcl_val.uge(srcr_addsub_nosh):
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_c_setc_ne:
            setc_bit = z64
            if srcl_val != srcr_val:
                setc_bit = 1
            alu = setc_bit
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_hl_lui:
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        csel_srcr = srcr_addsub_nosh
        csel_val = srcl_val
        if srcp_val != 0:
            csel_val = csel_srcr
        if op_csel:
            alu = csel_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        is_lwi = op_lwi | op_c_lwi
        lwi_addr = srcl_val + off
        if is_lwi:
            alu = z64
            is_load = 1
            is_store = z1
            size = 4
            addr = lwi_addr
            wdata = z64

        if op_lwui:
            alu = z64
            is_load = 1
            is_store = z1
            size = 4
            addr = lwi_addr
            wdata = z64

        if op_lbi | op_lbui:
            alu = z64
            is_load = 1
            is_store = z1
            size = 1
            addr = srcl_val + imm
            wdata = z64

        h_off = imm.shl(amount=1)
        if op_lhi | op_lhui:
            alu = z64
            is_load = 1
            is_store = z1
            size = 2
            addr = srcl_val + h_off
            wdata = z64

        idx_addr = srcl_val + idx_mod_shl
        if op_lb:
            alu = z64
            is_load = 1
            is_store = z1
            size = 1
            addr = idx_addr
            wdata = z64
        if op_lbu:
            alu = z64
            is_load = 1
            is_store = z1
            size = 1
            addr = idx_addr
            wdata = z64
        if op_lh:
            alu = z64
            is_load = 1
            is_store = z1
            size = 2
            addr = idx_addr
            wdata = z64
        if op_lhu:
            alu = z64
            is_load = 1
            is_store = z1
            size = 2
            addr = idx_addr
            wdata = z64
        if op_lw:
            alu = z64
            is_load = 1
            is_store = z1
            size = 4
            addr = idx_addr
            wdata = z64
        if op_lwu:
            alu = z64
            is_load = 1
            is_store = z1
            size = 4
            addr = idx_addr
            wdata = z64
        if op_ld:
            alu = z64
            is_load = 1
            is_store = z1
            size = 8
            addr = idx_addr
            wdata = z64

        ldi_off = imm.shl(amount=3)
        if op_ldi | op_c_ldi:
            alu = z64
            is_load = 1
            is_store = z1
            size = 8
            addr = srcl_val + ldi_off
            wdata = z64

        if op_sbi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 1
            addr = srcr_val + imm
            wdata = srcl_val

        if op_shi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 2
            addr = srcr_val + h_off
            wdata = srcl_val

        store_addr = srcl_val + off
        store_data = srcr_val
        if op_swi:
            store_addr = srcr_val + off
            store_data = srcl_val
        if op_swi | op_c_swi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 4
            addr = store_addr
            wdata = store_data

        if op_sb:
            alu = z64
            is_load = z1
            is_store = 1
            size = 1
            addr = idx_addr
            wdata = srcp_val
        if op_sh:
            alu = z64
            is_load = z1
            is_store = 1
            size = 2
            addr = idx_addr
            wdata = srcp_val
        if op_sw:
            alu = z64
            is_load = z1
            is_store = 1
            size = 4
            addr = idx_addr
            wdata = srcp_val
        if op_sd:
            alu = z64
            is_load = z1
            is_store = 1
            size = 8
            addr = idx_addr
            wdata = srcp_val

        sdi_off = imm.shl(amount=3)
        if op_c_sdi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 8
            addr = srcl_val + sdi_off
            wdata = srcr_val

        sdi_addr = srcr_val + sdi_off
        if op_sdi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 8
            addr = sdi_addr
            wdata = srcl_val

        if op_hl_lb_pcr | op_hl_lbu_pcr:
            alu = z64
            is_load = 1
            is_store = z1
            size = 1
            addr = pc + imm
            wdata = z64
        if op_hl_lh_pcr | op_hl_lhu_pcr:
            alu = z64
            is_load = 1
            is_store = z1
            size = 2
            addr = pc + imm
            wdata = z64
        if op_hl_lw_pcr | op_hl_lwu_pcr:
            alu = z64
            is_load = 1
            is_store = z1
            size = 4
            addr = pc + imm
            wdata = z64
        if op_hl_ld_pcr:
            alu = z64
            is_load = 1
            is_store = z1
            size = 8
            addr = pc + imm
            wdata = z64

        if op_hl_sb_pcr:
            alu = z64
            is_load = z1
            is_store = 1
            size = 1
            addr = pc + imm
            wdata = srcl_val
        if op_hl_sh_pcr:
            alu = z64
            is_load = z1
            is_store = 1
            size = 2
            addr = pc + imm
            wdata = srcl_val
        if op_hl_sw_pcr:
            alu = z64
            is_load = z1
            is_store = 1
            size = 4
            addr = pc + imm
            wdata = srcl_val
        if op_hl_sd_pcr:
            alu = z64
            is_load = z1
            is_store = 1
            size = 8
            addr = pc + imm
            wdata = srcl_val

        if op_c_addi:
            alu = srcl_val + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_c_add:
            alu = srcl_val + srcr_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_c_sub:
            alu = srcl_val - srcr_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_c_and:
            alu = srcl_val & srcr_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_c_or:
            alu = srcl_val | srcr_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        if op_c_sext_w:
            alu = srcl_val.trunc(width=32).sext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64
        if op_c_zext_w:
            alu = srcl_val.trunc(width=32).zext(width=64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        return ExecOut(alu=alu, is_load=is_load, is_store=is_store, size=size, addr=addr, wdata=wdata)
