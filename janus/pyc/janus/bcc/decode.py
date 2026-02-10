from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Wire, jit_inline

from .isa import (
    OP_ADDTPC,
    OP_ADDI,
    OP_ADDIW,
    OP_ADD,
    OP_ADDW,
    OP_AND,
    OP_ANDI,
    OP_ANDIW,
    OP_BSTART_STD_COND,
    OP_BSTART_STD_DIRECT,
    OP_BSTART_STD_FALL,
    OP_ANDW,
    OP_BXS,
    OP_BXU,
    OP_BSTART_STD_CALL,
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
    OP_C_BSTOP,
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
    OP_EBREAK,
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
    OP_INVALID,
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
    OP_SETC_EQ,
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
    OP_SETC_ANDI,
    OP_SETC_EQI,
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
    REG_INVALID,
)
from .util import lshr_var, masked_eq


@dataclass(frozen=True)
class Decode:
    op: Wire
    len_bytes: Wire
    regdst: Wire
    srcl: Wire
    srcr: Wire
    srcr_type: Wire
    shamt: Wire
    srcp: Wire
    imm: Wire


@dataclass(frozen=True)
class DecodeBundle:
    valid: list[Wire]
    off_bytes: list[Wire]
    dec: list[Decode]
    total_len_bytes: Wire


def decode_window(m: Circuit, window: Wire) -> Decode:
    c = m.const

    zero3 = c(0, width=3)
    zero2 = c(0, width=2)
    zero6 = c(0, width=6)
    zero64 = c(0, width=64)
    reg_invalid = c(REG_INVALID, width=6)

    insn16 = window.trunc(width=16)
    insn32 = window.trunc(width=32)
    insn48 = window.trunc(width=48)

    low4 = insn16[0:4]
    is_hl = low4.eq(0xE)

    is32 = insn16[0]
    in32 = (~is_hl) & is32
    in16 = (~is_hl) & (~is32)

    rd32 = insn32[7:12]
    rs1_32 = insn32[15:20]
    rs2_32 = insn32[20:25]
    srcr_type_32 = insn32[25:27]
    shamt5_32 = insn32[27:32]
    srcp_32 = insn32[27:32]
    shamt6_32 = insn32[20:26]

    imm12_u64 = insn32[20:32]
    imm12_s64 = insn32[20:32].sext(width=64)

    imm20_s64 = insn32[12:32].sext(width=64)
    imm20_u64 = insn32[12:32].zext(width=64)

    # SWI simm12 is split: {insn32[11:7], insn32[31:25]}.
    swi_lo5 = insn32[7:12]
    swi_hi7 = insn32[25:32]
    simm12_raw = swi_lo5.zext(width=12).shl(amount=7) | swi_hi7.zext(width=12)
    simm12_s64 = simm12_raw.sext(width=64)
    simm17_s64 = insn32[15:32].sext(width=64)

    # HL.LUI immediate packing (48-bit):
    # pfx = insn48[15:0]; main = insn48[47:16]
    pfx16 = insn48.trunc(width=16)
    main32 = insn48[16:48]
    imm_hi12 = pfx16[4:16]
    imm_lo20 = main32[12:32]
    imm32 = imm_hi12.zext(width=32).shl(amount=20) | imm_lo20.zext(width=32)
    imm_hl_lui = imm32.sext(width=64)

    rd_hl = main32[7:12]

    # 16-bit fields.
    rd16 = insn16[11:16]
    rs16 = insn16[6:11]
    # Immediate fields:
    # - simm5_11_s5: bits[15:11] (used by loads/stores)
    # - simm5_6_s5: bits[10:6] (used by C.MOVI / C.SETRET)
    simm5_11_s64 = insn16[11:16].sext(width=64)
    simm5_6_s64 = insn16[6:11].sext(width=64)
    simm12_s64_c = insn16[4:16].sext(width=64)
    uimm5 = insn16[6:11]
    brtype = insn16[11:14]

    op = c(OP_INVALID, width=12)
    len_bytes = zero3
    regdst = reg_invalid
    srcl = reg_invalid
    srcr = reg_invalid
    srcr_type = zero2
    shamt = zero6
    srcp = reg_invalid
    imm = zero64

    # --- 16-bit decode (reverse priority; C.BSTOP highest) ---
    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x000C)
    def aw(x: Wire | int, width: int) -> Wire:
        if isinstance(x, Wire):
            if x.width == width:
                return x
            if x.width < width:
                return x.zext(width=width)
            return x.trunc(width=width)
        return c(int(x), width=width)

    def set_if(
        cond: Wire,
        *,
        op_v: Wire | int | None = None,
        len_v: Wire | int | None = None,
        regdst_v: Wire | int | None = None,
        srcl_v: Wire | int | None = None,
        srcr_v: Wire | int | None = None,
        srcr_type_v: Wire | int | None = None,
        shamt_v: Wire | int | None = None,
        srcp_v: Wire | int | None = None,
        imm_v: Wire | int | None = None,
    ) -> None:
        nonlocal op, len_bytes, regdst, srcl, srcr, srcr_type, shamt, srcp, imm
        cond = m.wire(cond)
        if op_v is not None:
            op = cond.select(aw(op_v, 12), op)
        if len_v is not None:
            len_bytes = cond.select(aw(len_v, 3), len_bytes)
        if regdst_v is not None:
            regdst = cond.select(aw(regdst_v, 6), regdst)
        if srcl_v is not None:
            srcl = cond.select(aw(srcl_v, 6), srcl)
        if srcr_v is not None:
            srcr = cond.select(aw(srcr_v, 6), srcr)
        if srcr_type_v is not None:
            srcr_type = cond.select(aw(srcr_type_v, 2), srcr_type)
        if shamt_v is not None:
            shamt = cond.select(aw(shamt_v, 6), shamt)
        if srcp_v is not None:
            srcp = cond.select(aw(srcp_v, 6), srcp)
        if imm_v is not None:
            imm = cond.select(aw(imm_v, 64), imm)

    set_if(cond, op_v=OP_C_ADDI, len_v=2, regdst_v=31, srcl_v=rs16, imm_v=simm5_11_s64)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0008)
    set_if(cond, op_v=OP_C_ADD, len_v=2, regdst_v=31, srcl_v=rs16, srcr_v=rd16)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0018)
    set_if(cond, op_v=OP_C_SUB, len_v=2, regdst_v=31, srcl_v=rs16, srcr_v=rd16)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0028)
    set_if(cond, op_v=OP_C_AND, len_v=2, regdst_v=31, srcl_v=rs16, srcr_v=rd16)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0016)
    set_if(cond, op_v=OP_C_MOVI, len_v=2, regdst_v=rd16, imm_v=simm5_6_s64)

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x5016)
    set_if(cond, op_v=OP_C_SETRET, len_v=2, regdst_v=10, imm_v=uimm5.zext(width=6).shl(amount=1))

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x002A)
    set_if(cond, op_v=OP_C_SWI, len_v=2, srcl_v=rs16, srcr_v=24, imm_v=simm5_11_s64)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x003A)
    set_if(cond, op_v=OP_C_SDI, len_v=2, srcl_v=rs16, srcr_v=24, imm_v=simm5_11_s64)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x000A)
    set_if(cond, op_v=OP_C_LWI, len_v=2, srcl_v=rs16, imm_v=simm5_11_s64)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x001A)
    set_if(cond, op_v=OP_C_LDI, len_v=2, regdst_v=31, srcl_v=rs16, imm_v=simm5_11_s64)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0006)
    set_if(cond, op_v=OP_C_MOVR, len_v=2, regdst_v=rd16, srcl_v=rs16)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0026)
    set_if(cond, op_v=OP_C_SETC_EQ, len_v=2, srcl_v=rs16, srcr_v=rd16)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0036)
    set_if(cond, op_v=OP_C_SETC_NE, len_v=2, srcl_v=rs16, srcr_v=rd16)

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x001C)
    set_if(cond, op_v=OP_C_SETC_TGT, len_v=2, srcl_v=rs16)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0038)
    set_if(cond, op_v=OP_C_OR, len_v=2, regdst_v=31, srcl_v=rs16, srcr_v=rd16)

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x501C)
    set_if(cond, op_v=OP_C_SEXT_W, len_v=2, regdst_v=31, srcl_v=rs16)

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x681C)
    set_if(cond, op_v=OP_C_ZEXT_W, len_v=2, regdst_v=31, srcl_v=rs16)

    # C.CMP.EQI/NEI: compare t#1 against simm5, write 0/1 to t-hand.
    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x002C)
    set_if(cond, op_v=OP_CMP_EQI, len_v=2, regdst_v=31, srcl_v=24, imm_v=simm5_6_s64)

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x082C)
    set_if(cond, op_v=OP_CMP_NEI, len_v=2, regdst_v=31, srcl_v=24, imm_v=simm5_6_s64)

    cond = in16 & masked_eq(insn16, mask=0x000F, match=0x0002)
    set_if(cond, op_v=OP_C_BSTART_DIRECT, len_v=2, imm_v=simm12_s64_c.shl(amount=1))

    cond = in16 & masked_eq(insn16, mask=0x000F, match=0x0004)
    set_if(cond, op_v=OP_C_BSTART_COND, len_v=2, imm_v=simm12_s64_c.shl(amount=1))

    cond = in16 & masked_eq(insn16, mask=0xC7FF, match=0x0000)
    set_if(cond, op_v=OP_C_BSTART_STD, len_v=2, imm_v=brtype)

    cond = in16 & masked_eq(insn16, mask=0xFFFF, match=0x0000)
    set_if(cond, op_v=OP_C_BSTOP, len_v=2)

    # --- 32-bit decode (reverse priority; EBREAK highest) ---
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000041)
    uimm_hi = insn32[7:12].zext(width=64)
    uimm_lo = insn32[25:32].zext(width=64)
    macro_imm = uimm_hi.shl(amount=10) | uimm_lo.shl(amount=3)
    set_if(cond, op_v=OP_FENTRY, len_v=4, srcl_v=insn32[15:20], srcr_v=insn32[20:25], imm_v=macro_imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001041)
    set_if(cond, op_v=OP_FEXIT, len_v=4, srcl_v=insn32[15:20], srcr_v=insn32[20:25], imm_v=macro_imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002041)
    set_if(cond, op_v=OP_FRET_RA, len_v=4, srcl_v=insn32[15:20], srcr_v=insn32[20:25], imm_v=macro_imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003041)
    set_if(cond, op_v=OP_FRET_STK, len_v=4, srcl_v=insn32[15:20], srcr_v=insn32[20:25], imm_v=macro_imm)

    cond = in32 & masked_eq(insn32, mask=0x0000007F, match=0x00000017)
    set_if(cond, op_v=OP_LUI, len_v=4, regdst_v=rd32, imm_v=imm20_s64.shl(amount=12))

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000005)
    set_if(
        cond,
        op_v=OP_ADD,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001005)
    set_if(
        cond,
        op_v=OP_SUB,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002005)
    set_if(
        cond,
        op_v=OP_AND,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003005)
    set_if(
        cond,
        op_v=OP_OR,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004005)
    set_if(
        cond,
        op_v=OP_XOR,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002015)
    set_if(cond, op_v=OP_ANDI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002035)
    set_if(cond, op_v=OP_ANDIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003015)
    set_if(cond, op_v=OP_ORI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003035)
    set_if(cond, op_v=OP_ORIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004035)
    set_if(cond, op_v=OP_XORIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    # Mul/Div/Rem (benchmarks).
    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00000047)
    set_if(cond, op_v=OP_MUL, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00002047)
    set_if(cond, op_v=OP_MULW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    # MADD/MADDW: RegDst = SrcD + (SrcL * SrcR). SrcD is in bits[31:27] and is
    # carried through our pipeline in `srcp`.
    cond = in32 & masked_eq(insn32, mask=0x0600707F, match=0x00006047)
    set_if(cond, op_v=OP_MADD, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32, srcp_v=srcp_32)

    cond = in32 & masked_eq(insn32, mask=0x0600707F, match=0x00007047)
    set_if(cond, op_v=OP_MADDW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32, srcp_v=srcp_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00000057)
    set_if(cond, op_v=OP_DIV, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00001057)
    set_if(cond, op_v=OP_DIVU, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00002057)
    set_if(cond, op_v=OP_DIVW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00003057)
    set_if(cond, op_v=OP_DIVUW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00004057)
    set_if(cond, op_v=OP_REM, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00005057)
    set_if(cond, op_v=OP_REMU, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00006057)
    set_if(cond, op_v=OP_REMW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00007057)
    set_if(cond, op_v=OP_REMUW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00007005)
    set_if(cond, op_v=OP_SLL, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00005005)
    set_if(cond, op_v=OP_SRL, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00006005)
    set_if(cond, op_v=OP_SRA, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFC00707F, match=0x00007015)
    set_if(cond, op_v=OP_SLLI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, shamt_v=shamt6_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00005035)
    set_if(cond, op_v=OP_SRLIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, shamt_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00006035)
    set_if(cond, op_v=OP_SRAIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, shamt_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00007035)
    set_if(cond, op_v=OP_SLLIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, shamt_v=rs2_32)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000067)
    set_if(cond, op_v=OP_BXS, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=insn32[26:32], srcp_v=insn32[20:26])

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001067)
    set_if(cond, op_v=OP_BXU, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=insn32[26:32], srcp_v=insn32[20:26])

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00006045)
    set_if(cond, op_v=OP_CMP_LTU, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    # CMP.* immediate variants.
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000055)
    set_if(cond, op_v=OP_CMP_EQI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001055)
    set_if(cond, op_v=OP_CMP_NEI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002055)
    set_if(cond, op_v=OP_CMP_ANDI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003055)
    set_if(cond, op_v=OP_CMP_ORI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004055)
    set_if(cond, op_v=OP_CMP_LTI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005055)
    set_if(cond, op_v=OP_CMP_GEI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006055)
    set_if(cond, op_v=OP_CMP_LTUI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_u64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00007055)
    set_if(cond, op_v=OP_CMP_GEUI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_u64)

    # SETC.* immediate variants (opcode 0x75): update commit_cond (committed in WB).
    # These encode shamt in bits[11:7] (RegDst field in the ISA).
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000075)
    set_if(cond, op_v=OP_SETC_EQI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001075)
    set_if(cond, op_v=OP_SETC_NEI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002075)
    set_if(cond, op_v=OP_SETC_ANDI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003075)
    set_if(cond, op_v=OP_SETC_ORI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004075)
    set_if(cond, op_v=OP_SETC_LTI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005075)
    set_if(cond, op_v=OP_SETC_GEI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006075)
    set_if(cond, op_v=OP_SETC_LTUI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_u64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00007075)
    set_if(cond, op_v=OP_SETC_GEUI, len_v=4, srcl_v=rs1_32, shamt_v=rd32, imm_v=imm12_u64)

    # SETC.* (register forms): update commit_cond (committed in WB).
    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00000065)
    set_if(cond, op_v=OP_SETC_EQ, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00001065)
    set_if(cond, op_v=OP_SETC_NE, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00002065)
    set_if(cond, op_v=OP_SETC_AND, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00003065)
    set_if(cond, op_v=OP_SETC_OR, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00004065)
    set_if(cond, op_v=OP_SETC_LT, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00006065)
    set_if(cond, op_v=OP_SETC_LTU, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00005065)
    set_if(cond, op_v=OP_SETC_GE, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00007065)
    set_if(cond, op_v=OP_SETC_GEU, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    # SETC.TGT: set commit target to SrcL (legal only inside a non-FALL block in the ISA).
    cond = in32 & masked_eq(insn32, mask=0xFFF07FFF, match=0x0000403B)
    set_if(cond, op_v=OP_C_SETC_TGT, len_v=4, srcl_v=rs1_32)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004019)
    set_if(cond, op_v=OP_LBUI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000019)
    set_if(cond, op_v=OP_LBI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001019)
    set_if(cond, op_v=OP_LHI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005019)
    set_if(cond, op_v=OP_LHUI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006019)
    set_if(cond, op_v=OP_LWUI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000009)
    set_if(
        cond,
        op_v=OP_LB,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004009)
    set_if(
        cond,
        op_v=OP_LBU,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001009)
    set_if(
        cond,
        op_v=OP_LH,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005009)
    set_if(
        cond,
        op_v=OP_LHU,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002009)
    set_if(
        cond,
        op_v=OP_LW,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006009)
    set_if(
        cond,
        op_v=OP_LWU,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003009)
    set_if(
        cond,
        op_v=OP_LD,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003019)
    set_if(cond, op_v=OP_LDI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00001001)
    set_if(cond, op_v=OP_BSTART_STD_FALL, len_v=4)

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00002001)
    set_if(cond, op_v=OP_BSTART_STD_DIRECT, len_v=4, imm_v=simm17_s64.shl(amount=1))

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00003001)
    set_if(cond, op_v=OP_BSTART_STD_COND, len_v=4, imm_v=simm17_s64.shl(amount=1))

    # BSTART.STD IND/ICALL/RET: no embedded target; requires SETC.TGT within the block.
    # For these, reuse OP_C_BSTART_STD internal op and carry BrType in `imm`.
    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00005001)
    set_if(cond, op_v=OP_C_BSTART_STD, len_v=4, imm_v=5)

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00006001)
    set_if(cond, op_v=OP_C_BSTART_STD, len_v=4, imm_v=6)

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00007001)
    set_if(cond, op_v=OP_C_BSTART_STD, len_v=4, imm_v=7)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004025)
    set_if(
        cond,
        op_v=OP_XORW,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002025)
    set_if(
        cond,
        op_v=OP_ANDW,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003025)
    set_if(
        cond,
        op_v=OP_ORW,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000025)
    set_if(
        cond,
        op_v=OP_ADDW,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001025)
    set_if(
        cond,
        op_v=OP_SUBW,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=shamt5_32,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000077)
    set_if(
        cond,
        op_v=OP_CSEL,
        len_v=4,
        regdst_v=rd32,
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        srcp_v=srcp_32,
    )

    # Stores (immediate offset).
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000059)
    set_if(cond, op_v=OP_SBI, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, imm_v=simm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001059)
    set_if(cond, op_v=OP_SHI, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, imm_v=simm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002059)
    set_if(cond, op_v=OP_SWI, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, imm_v=simm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003059)
    set_if(cond, op_v=OP_SDI, len_v=4, srcl_v=rs1_32, srcr_v=rs2_32, imm_v=simm12_s64)

    # Stores (indexed). Encoding uses SrcD in bits[31:27], SrcL base in bits[19:15], SrcR idx in bits[24:20].
    # We map: srcp=value (SrcD), srcl=base (SrcL), srcr=index (SrcR), srcr_type=SrcRType, shamt=fixed scale.
    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00000049)
    set_if(
        cond,
        op_v=OP_SB,
        len_v=4,
        srcp_v=insn32[27:32],
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=0,
    )

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00001049)
    set_if(
        cond,
        op_v=OP_SH,
        len_v=4,
        srcp_v=insn32[27:32],
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=1,
    )

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00002049)
    set_if(
        cond,
        op_v=OP_SW,
        len_v=4,
        srcp_v=insn32[27:32],
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=2,
    )

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00003049)
    set_if(
        cond,
        op_v=OP_SD,
        len_v=4,
        srcp_v=insn32[27:32],
        srcl_v=rs1_32,
        srcr_v=rs2_32,
        srcr_type_v=srcr_type_32,
        shamt_v=3,
    )

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002019)
    set_if(cond, op_v=OP_LWI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_s64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000035)
    set_if(cond, op_v=OP_ADDIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_u64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001035)
    set_if(cond, op_v=OP_SUBIW, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_u64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000015)
    set_if(cond, op_v=OP_ADDI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_u64)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001015)
    set_if(cond, op_v=OP_SUBI, len_v=4, regdst_v=rd32, srcl_v=rs1_32, imm_v=imm12_u64)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00000045)
    set_if(cond, op_v=OP_CMP_EQ, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00001045)
    set_if(cond, op_v=OP_CMP_NE, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00004045)
    set_if(cond, op_v=OP_CMP_LT, len_v=4, regdst_v=rd32, srcl_v=rs1_32, srcr_v=rs2_32, srcr_type_v=srcr_type_32)

    cond = in32 & masked_eq(insn32, mask=0xF0FFFFFF, match=0x0010102B)
    set_if(cond, op_v=OP_EBREAK, len_v=4)

    # Opcode overlap group (QEMU: insn32.decode):
    # - SETRET: specialized ADDTPC encoding with rd=RA (x10), but different semantics.
    # - ADDTPC: PC-relative page base for any other rd.
    cond = in32 & masked_eq(insn32, mask=0x0000007F, match=0x00000007)
    set_if(cond, op_v=OP_ADDTPC, len_v=4, regdst_v=rd32, imm_v=imm20_s64.shl(amount=12))
    set_if(cond & rd32.eq(10), op_v=OP_SETRET, len_v=4, regdst_v=10, imm_v=imm20_u64.shl(amount=1))

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00004001)
    set_if(cond, op_v=OP_BSTART_STD_CALL, len_v=4, imm_v=simm17_s64.shl(amount=1))

    # --- 48-bit HL decode (highest priority overall) ---
    hl_bstart_hi12 = pfx16[4:16].zext(width=64)
    hl_bstart_lo17 = insn48[31:48].zext(width=64)
    hl_bstart_simm_hw = (hl_bstart_hi12.shl(amount=18) | hl_bstart_lo17.shl(amount=1)).trunc(width=30).sext(width=64)
    # HL.BSTART simm is in halfwords (QEMU: target = PC + (simm << 1)).
    # Decode emits a byte offset.
    hl_bstart_off = hl_bstart_simm_hw

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00001001000E)
    set_if(cond, op_v=OP_BSTART_STD_FALL, len_v=6)

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00002001000E)
    set_if(cond, op_v=OP_BSTART_STD_DIRECT, len_v=6, imm_v=hl_bstart_off)

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00003001000E)
    set_if(cond, op_v=OP_BSTART_STD_COND, len_v=6, imm_v=hl_bstart_off)

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00004001000E)
    set_if(cond, op_v=OP_BSTART_STD_CALL, len_v=6, imm_v=hl_bstart_off)

    # HL.<load>.PCR: PC-relative load, funct3 encodes width/signedness.
    cond = is_hl & masked_eq(insn48, mask=0x0000007F000F, match=0x00000039000E)
    hl_load_regdst = insn48[23:28]
    hl_load_simm_hi12 = pfx16[4:16].zext(width=64)
    hl_load_simm_lo17 = insn48[31:48].zext(width=64)
    hl_load_simm29 = (hl_load_simm_hi12.shl(amount=17) | hl_load_simm_lo17).trunc(width=29).sext(width=64)
    hl_load_funct3 = insn48[28:31]
    set_if(cond, len_v=6, regdst_v=hl_load_regdst, imm_v=hl_load_simm29)
    set_if(cond, op_v=OP_HL_LW_PCR)
    set_if(cond & hl_load_funct3.eq(0), op_v=OP_HL_LB_PCR)
    set_if(cond & hl_load_funct3.eq(1), op_v=OP_HL_LH_PCR)
    set_if(cond & hl_load_funct3.eq(2), op_v=OP_HL_LW_PCR)
    set_if(cond & hl_load_funct3.eq(3), op_v=OP_HL_LD_PCR)
    set_if(cond & hl_load_funct3.eq(4), op_v=OP_HL_LBU_PCR)
    set_if(cond & hl_load_funct3.eq(5), op_v=OP_HL_LHU_PCR)
    set_if(cond & hl_load_funct3.eq(6), op_v=OP_HL_LWU_PCR)

    # HL.<store>.PCR: PC-relative store, funct3 encodes width.
    cond = is_hl & masked_eq(insn48, mask=0x0000007F000F, match=0x00000069000E)
    hl_store_srcl = insn48[31:36]
    hl_store_simm_hi12 = pfx16[4:16].zext(width=64)
    hl_store_simm_mid5 = insn48[23:28].zext(width=64)
    hl_store_simm_lo12 = insn48[36:48].zext(width=64)
    hl_store_simm29 = (
        hl_store_simm_hi12.shl(amount=17) | hl_store_simm_mid5.shl(amount=12) | hl_store_simm_lo12
    ).trunc(width=29).sext(width=64)
    hl_store_funct3 = insn48[28:31]
    set_if(cond, len_v=6, srcl_v=hl_store_srcl, imm_v=hl_store_simm29)
    set_if(cond, op_v=OP_HL_SW_PCR)
    set_if(cond & hl_store_funct3.eq(0), op_v=OP_HL_SB_PCR)
    set_if(cond & hl_store_funct3.eq(1), op_v=OP_HL_SH_PCR)
    set_if(cond & hl_store_funct3.eq(2), op_v=OP_HL_SW_PCR)
    set_if(cond & hl_store_funct3.eq(3), op_v=OP_HL_SD_PCR)

    # HL.ANDI: extended immediate variant of ANDI (simm24).
    cond = is_hl & masked_eq(insn48, mask=0x0000707F000F, match=0x00002015000E)
    imm_hi12 = pfx16[4:16]
    imm_lo12 = main32[20:32]
    imm24 = imm_hi12.zext(width=24).shl(amount=12) | imm_lo12.zext(width=24)
    set_if(cond, op_v=OP_ANDI, len_v=6, regdst_v=rd_hl, srcl_v=main32[15:20], imm_v=imm24.sext(width=64))

    cond = is_hl & masked_eq(insn48, mask=0x0000007F000F, match=0x00000017000E)
    set_if(cond, op_v=OP_HL_LUI, len_v=6, regdst_v=rd_hl, imm_v=imm_hl_lui)

    return Decode(
        op=op,
        len_bytes=len_bytes,
        regdst=regdst,
        srcl=srcl,
        srcr=srcr,
        srcr_type=srcr_type,
        shamt=shamt,
        srcp=srcp,
        imm=imm,
    )


@jit_inline
def decode_bundle_8B(m: Circuit, window: Wire) -> DecodeBundle:
    """Decode up to 4 sequential instructions from an 8-byte fetch window.

    Returns per-slot byte offsets (from the window base) and a total length
    suitable for advancing the fetch PC.
    """
    c = m.const

    z4 = c(0, width=4)
    b8 = c(8, width=4)
    b2 = c(2, width=4)

    # Slot 0.
    win0 = window
    dec0 = decode_window(m, win0)
    len0_4 = dec0.len_bytes.zext(width=4)
    off0 = z4
    v0 = ~len0_4.eq(z4)

    # Template macro blocks (FENTRY/FEXIT/FRET.*) must execute as standalone
    # blocks at the front-end, so do not include following instructions in the
    # same fetch bundle.
    is_macro0 = (
        dec0.op.eq(OP_FENTRY)
        | dec0.op.eq(OP_FEXIT)
        | dec0.op.eq(OP_FRET_RA)
        | dec0.op.eq(OP_FRET_STK)
    )

    # Slot 1.
    sh0 = len0_4.zext(width=6).shl(amount=3)
    win1 = lshr_var(m, win0, sh0)
    dec1 = decode_window(m, win1)
    len1_4 = dec1.len_bytes.zext(width=4)
    off1 = len0_4
    rem0 = b8 - len0_4
    v1 = v0 & (~is_macro0) & rem0.uge(b2) & (~len1_4.eq(z4)) & len1_4.ule(rem0)

    # Slot 2.
    off2 = off1 + len1_4
    sh1 = off2.zext(width=6).shl(amount=3)
    win2 = lshr_var(m, win0, sh1)
    dec2 = decode_window(m, win2)
    len2_4 = dec2.len_bytes.zext(width=4)
    rem1 = rem0 - len1_4
    v2 = v1 & rem1.uge(b2) & (~len2_4.eq(z4)) & len2_4.ule(rem1)

    # Slot 3.
    off3 = off2 + len2_4
    sh2 = off3.zext(width=6).shl(amount=3)
    win3 = lshr_var(m, win0, sh2)
    dec3 = decode_window(m, win3)
    len3_4 = dec3.len_bytes.zext(width=4)
    rem2 = rem1 - len2_4
    v3 = v2 & rem2.uge(b2) & (~len3_4.eq(z4)) & len3_4.ule(rem2)

    total = len0_4
    total = v1.select(off2, total)
    total = v2.select(off3, total)
    total = v3.select(off3 + len3_4, total)

    return DecodeBundle(
        valid=[v0, v1, v2, v3],
        off_bytes=[off0, off1, off2, off3],
        dec=[dec0, dec1, dec2, dec3],
        total_len_bytes=total,
    )
