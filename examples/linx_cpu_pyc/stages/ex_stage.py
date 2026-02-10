from __future__ import annotations

from pycircuit import Circuit, Wire, jit_inline

from ..isa import (
    OP_ADDTPC,
    OP_ADDI,
    OP_ADDIW,
    OP_ADD,
    OP_AND,
    OP_ANDI,
    OP_ANDIW,
    OP_BSTART_STD_COND,
    OP_BSTART_STD_DIRECT,
    OP_BSTART_STD_FALL,
    OP_ADDW,
    OP_ANDW,
    OP_BXS,
    OP_BXU,
    OP_BSTART_STD_CALL,
    OP_CMP_EQ,
    OP_CMP_EQI,
    OP_CMP_LT,
    OP_CMP_NE,
    OP_CMP_NEI,
    OP_CMP_ANDI,
    OP_CMP_ORI,
    OP_CMP_LTI,
    OP_CMP_LTUI,
    OP_CMP_LTU,
    OP_CMP_GEI,
    OP_CMP_GEUI,
    OP_C_ADD,
    OP_C_ADDI,
    OP_C_AND,
    OP_C_BSTART_COND,
    OP_C_BSTART_DIRECT,
    OP_C_BSTART_STD,
    OP_C_OR,
    OP_C_SUB,
    OP_CSEL,
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
    OP_HL_LB_PCR,
    OP_HL_LBU_PCR,
    OP_HL_LD_PCR,
    OP_HL_LH_PCR,
    OP_HL_LHU_PCR,
    OP_HL_LUI,
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
    OP_SLL,
    OP_SLLI,
    OP_SLLIW,
    OP_SDI,
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
    REG_INVALID,
)
from ..pipeline import ExMemRegs, IdExRegs
from ..util import Consts, ashr_var, lshr_var, shl_var


@jit_inline
def build_ex_stage(
    m: Circuit,
    *,
    do_ex: Wire,
    idex: IdExRegs,
    exmem: ExMemRegs,
    consts: Consts,
    # Forwarding sources (from MEM and WB stages, both lanes).
    mem0_fwd_valid: Wire,
    mem0_fwd_regdst: Wire,
    mem0_fwd_value: Wire,
    mem1_fwd_valid: Wire,
    mem1_fwd_regdst: Wire,
    mem1_fwd_value: Wire,
    wb0_fwd_valid: Wire,
    wb0_fwd_regdst: Wire,
    wb0_fwd_value: Wire,
    wb1_fwd_valid: Wire,
    wb1_fwd_regdst: Wire,
    wb1_fwd_value: Wire,
    # Forwarded T/U stack views for this EX stage (after applying older in-flight
    # pushes/clears from WB+MEM).
    t0_fwd: Wire,
    t1_fwd: Wire,
    t2_fwd: Wire,
    t3_fwd: Wire,
    u0_fwd: Wire,
    u1_fwd: Wire,
    u2_fwd: Wire,
    u3_fwd: Wire,
) -> None:
    with m.scope("EX"):
        z1 = consts.zero1
        z4 = consts.zero4
        z64 = consts.zero64

        # Stage inputs.
        pc = idex.pc.out()
        window = idex.window.out()
        pred_next_pc = idex.pred_next_pc.out()
        op = idex.op.out()
        len_bytes = idex.len_bytes.out()
        regdst = idex.regdst.out()
        srcl = idex.srcl.out()
        srcr = idex.srcr.out()
        srcr_type = idex.srcr_type.out()
        shamt = idex.shamt.out()
        srcp = idex.srcp.out()
        srcl_val = idex.srcl_val.out()
        srcr_val = idex.srcr_val.out()
        srcp_val = idex.srcp_val.out()
        imm = idex.imm.out()

        # Operand forwarding (priority: younger > older; MEM stage > WB stage; lane1 > lane0).
        can_fwd_mem0 = mem0_fwd_valid & (mem0_fwd_regdst != REG_INVALID) & (mem0_fwd_regdst != 0)
        can_fwd_mem1 = mem1_fwd_valid & (mem1_fwd_regdst != REG_INVALID) & (mem1_fwd_regdst != 0)
        can_fwd_wb0 = wb0_fwd_valid & (wb0_fwd_regdst != REG_INVALID) & (wb0_fwd_regdst != 0)
        can_fwd_wb1 = wb1_fwd_valid & (wb1_fwd_regdst != REG_INVALID) & (wb1_fwd_regdst != 0)

        # Apply sources from oldest -> youngest so later matches override.
        if can_fwd_wb0 & (wb0_fwd_regdst == srcl):
            srcl_val = wb0_fwd_value
        if can_fwd_wb1 & (wb1_fwd_regdst == srcl):
            srcl_val = wb1_fwd_value
        if can_fwd_mem0 & (mem0_fwd_regdst == srcl):
            srcl_val = mem0_fwd_value
        if can_fwd_mem1 & (mem1_fwd_regdst == srcl):
            srcl_val = mem1_fwd_value

        if can_fwd_wb0 & (wb0_fwd_regdst == srcr):
            srcr_val = wb0_fwd_value
        if can_fwd_wb1 & (wb1_fwd_regdst == srcr):
            srcr_val = wb1_fwd_value
        if can_fwd_mem0 & (mem0_fwd_regdst == srcr):
            srcr_val = mem0_fwd_value
        if can_fwd_mem1 & (mem1_fwd_regdst == srcr):
            srcr_val = mem1_fwd_value

        if can_fwd_wb0 & (wb0_fwd_regdst == srcp):
            srcp_val = wb0_fwd_value
        if can_fwd_wb1 & (wb1_fwd_regdst == srcp):
            srcp_val = wb1_fwd_value
        if can_fwd_mem0 & (mem0_fwd_regdst == srcp):
            srcp_val = mem0_fwd_value
        if can_fwd_mem1 & (mem1_fwd_regdst == srcp):
            srcp_val = mem1_fwd_value

        # T/U stack bypass (codes: T0..T3 = 24..27, U0..U3 = 28..31).
        if srcl == 24:
            srcl_val = t0_fwd
        if srcl == 25:
            srcl_val = t1_fwd
        if srcl == 26:
            srcl_val = t2_fwd
        if srcl == 27:
            srcl_val = t3_fwd
        if srcl == 28:
            srcl_val = u0_fwd
        if srcl == 29:
            srcl_val = u1_fwd
        if srcl == 30:
            srcl_val = u2_fwd
        if srcl == 31:
            srcl_val = u3_fwd

        if srcr == 24:
            srcr_val = t0_fwd
        if srcr == 25:
            srcr_val = t1_fwd
        if srcr == 26:
            srcr_val = t2_fwd
        if srcr == 27:
            srcr_val = t3_fwd
        if srcr == 28:
            srcr_val = u0_fwd
        if srcr == 29:
            srcr_val = u1_fwd
        if srcr == 30:
            srcr_val = u2_fwd
        if srcr == 31:
            srcr_val = u3_fwd

        if srcp == 24:
            srcp_val = t0_fwd
        if srcp == 25:
            srcp_val = t1_fwd
        if srcp == 26:
            srcp_val = t2_fwd
        if srcp == 27:
            srcp_val = t3_fwd
        if srcp == 28:
            srcp_val = u0_fwd
        if srcp == 29:
            srcp_val = u1_fwd
        if srcp == 30:
            srcp_val = u2_fwd
        if srcp == 31:
            srcp_val = u3_fwd

        op_c_bstart_std = op == OP_C_BSTART_STD
        op_c_bstart_cond = op == OP_C_BSTART_COND
        op_c_bstart_direct = op == OP_C_BSTART_DIRECT
        op_bstart_std_fall = op == OP_BSTART_STD_FALL
        op_bstart_std_direct = op == OP_BSTART_STD_DIRECT
        op_bstart_std_cond = op == OP_BSTART_STD_COND
        op_bstart_std_call = op == OP_BSTART_STD_CALL
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

        # --- SrcR modifiers (QEMU reference: linx_srcR_addsub / linx_srcR_logic) ---
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

        # Address-index modifier (QEMU reference: linx_addr_add_reg): idx_type 0 => .sw else .uw.
        idx_mod = srcr_val.trunc(width=32).zext(width=64)
        if srcr_type == 0:
            idx_mod = srcr_val.trunc(width=32).sext(width=64)
        idx_mod_shl = shl_var(m, idx_mod, shamt)

        # Block markers: forward imm through ALU (used for control-state updates in WB).
        if (
            op_c_bstart_std
            | op_c_bstart_cond
            | op_c_bstart_direct
            | op_bstart_std_fall
            | op_bstart_std_direct
            | op_bstart_std_cond
            | op_bstart_std_call
        ):
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # MOVR: pass-through.
        if op_c_movr:
            alu = srcl_val
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # MOVI: immediate.
        if op_c_movi:
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # SETRET: ra = PC + off (off already shifted by 1 in decode).
        if op_c_setret:
            alu = pc + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # SETC.EQ / SETC.TGT: internal control state updates (committed in WB).
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

        # ADDTPC: PC + (imm<<12) (imm already shifted by 12 in decode).
        pc_page = pc & 0xFFFF_FFFF_FFFF_F000
        if op_addtpc:
            alu = pc_page + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # ADDI/SUBI/ADDIW: srcl +/- imm.
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

        # LUI: immediate (already shifted by 12 in decode).
        if op_lui:
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # SETRET: ra = pc + (imm<<1) (imm already shifted by 1 in decode).
        if op_setret:
            alu = pc + imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # ADD/SUB/AND/OR/XOR: 64-bit ops with SrcR modifiers + optional shift.
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

        # ANDI/ORI: immediate logic ops.
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

        # ANDIW/ORIW: word immediate ops (sign-extend low32).
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

        # Mul/Div/Rem.
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

        # Shifts.
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

        # BXS: signed bit-field extract.
        if op_bxs:
            imms = srcr
            imml = srcp
            shifted = lshr_var(m, srcl_val, imms)
            sh_mask_amt = m.const(63, width=64) - imml.zext(width=64)
            mask = lshr_var(m, m.const(0xFFFF_FFFF_FFFF_FFFF, width=64), sh_mask_amt)
            extracted = shifted & mask
            valid = (imms.zext(width=64) + imml.zext(width=64)).ule(63)
            # Sign-extend N=(imml+1) bits: (extracted << (63-imml)) >> (63-imml).
            sh_ext = sh_mask_amt
            sext = ashr_var(m, shl_var(m, extracted, sh_ext), sh_ext)
            alu = valid.select(sext, z64)
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # BXU: unsigned bit-field extract.
        if op_bxu:
            imms = srcr
            imml = srcp
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

        # ADDW/ORW/ANDW/XORW: word ops with SrcR modifiers + optional shift.
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

        # CMP_EQ: (srcl == SrcR_mod) ? 1 : 0 (SrcR_mod uses addsub modifiers, no shift).
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

        # CMP_NE: (srcl != SrcR_mod) ? 1 : 0 (same SrcR_mod as CMP_EQ).
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

        # CMP_LT: signed compare (srcl < SrcR_mod) ? 1 : 0.
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

        # CMP.*I: compare SrcL against immediate.
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

        # CMP.LTU: unsigned compare against modified SrcR.
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

        # CMP.LTUI: unsigned compare against immediate.
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

        # CMP.GEUI: unsigned compare against immediate.
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

        # SETC.*: update commit_cond (committed in WB).
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

        # HL.LUI: imm.
        if op_hl_lui:
            alu = imm
            is_load = z1
            is_store = z1
            size = z4
            addr = z64
            wdata = z64

        # CSEL: (srcp != 0) ? srcr : srcl.
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

        # LWI / C.LWI: load word, address = srcl + (imm << 2)
        is_lwi = op_lwi | op_c_lwi
        lwi_addr = srcl_val + off
        if is_lwi:
            alu = z64
            is_load = 1
            is_store = z1
            size = 4
            addr = lwi_addr
            wdata = z64

        # LWUI: load unsigned word, address = srcl + (imm << 2)
        if op_lwui:
            alu = z64
            is_load = 1
            is_store = z1
            size = 4
            addr = lwi_addr
            wdata = z64

        # LBI/LBUI: load (signed/unsigned) byte, address = srcl + simm12.
        if op_lbi | op_lbui:
            alu = z64
            is_load = 1
            is_store = z1
            size = 1
            addr = srcl_val + imm
            wdata = z64

        # LHI/LHUI: load (signed/unsigned) halfword, address = srcl + (simm12 << 1).
        h_off = imm.shl(amount=1)
        if op_lhi | op_lhui:
            alu = z64
            is_load = 1
            is_store = z1
            size = 2
            addr = srcl_val + h_off
            wdata = z64

        # LB/LBU/LH/LHU/LW/LWU/LD: indexed loads.
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

        # LDI / C.LDI: load 8B (unsigned).
        ldi_off = imm.shl(amount=3)
        if op_ldi | op_c_ldi:
            alu = z64
            is_load = 1
            is_store = z1
            size = 8
            addr = srcl_val + ldi_off
            wdata = z64

        # SBI: store byte (1 byte), addr = SrcR + simm12.
        if op_sbi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 1
            addr = srcr_val + imm
            wdata = srcl_val

        # SHI: store halfword (2 bytes), addr = SrcR + (simm12 << 1).
        if op_shi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 2
            addr = srcr_val + h_off
            wdata = srcl_val

        # SWI / C.SWI: store word (4 bytes)
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

        # SB/SH/SW/SD: indexed stores (addr = SrcL + (idx_mod << shamt)).
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

        # C.SDI: store double word from T0 at base SrcL + simm5*8.
        if op_c_sdi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 8
            addr = srcl_val + sdi_off
            wdata = srcr_val

        # SDI: store double word (8 bytes), addr = SrcR + (simm12<<3)
        sdi_addr = srcr_val + sdi_off
        if op_sdi:
            alu = z64
            is_load = z1
            is_store = 1
            size = 8
            addr = sdi_addr
            wdata = srcl_val

        # HL.<load>.PCR: PC-relative loads (imm already sign-extended in decode).
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

        # HL.<store>.PCR: PC-relative stores.
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

        # C.ADDI / C.ADD / C.SUB / C.AND / C.OR: push to T-hand.
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

        # C.SEXT.W / C.ZEXT.W: push word-extended to T-hand.
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

        # Pipeline regs: EX/MEM.
        exmem.pc.set(pc, when=do_ex)
        exmem.window.set(window, when=do_ex)
        exmem.pred_next_pc.set(pred_next_pc, when=do_ex)
        exmem.op.set(op, when=do_ex)
        exmem.len_bytes.set(len_bytes, when=do_ex)
        exmem.regdst.set(regdst, when=do_ex)
        exmem.srcl.set(srcl, when=do_ex)
        exmem.srcr.set(srcr, when=do_ex)
        exmem.imm.set(imm, when=do_ex)
        exmem.alu.set(alu, when=do_ex)
        exmem.is_load.set(is_load, when=do_ex)
        exmem.is_store.set(is_store, when=do_ex)
        exmem.size.set(size, when=do_ex)
        exmem.addr.set(addr, when=do_ex)
        exmem.wdata.set(wdata, when=do_ex)
