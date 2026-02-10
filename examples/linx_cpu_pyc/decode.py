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
    OP_MCOPY,
    OP_MSET,
    OP_BSTART_TMA,
    OP_B_TEXT,
    OP_B_IOT,
    OP_B_IOTI,
    OP_B_IOR,
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
from .util import masked_eq


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


@jit_inline
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
    is_hl = low4 == 0xE

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
    if cond:
        op = OP_C_ADDI
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16
        imm = simm5_11_s64

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0008)
    if cond:
        op = OP_C_ADD
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16
        srcr = rd16

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0018)
    if cond:
        op = OP_C_SUB
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16
        srcr = rd16

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0028)
    if cond:
        op = OP_C_AND
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16
        srcr = rd16

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0016)
    if cond:
        op = OP_C_MOVI
        len_bytes = 2
        regdst = rd16
        imm = simm5_6_s64

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x5016)
    if cond:
        op = OP_C_SETRET
        len_bytes = 2
        regdst = 10  # ra
        imm = uimm5.zext(width=6).shl(amount=1)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x002A)
    if cond:
        op = OP_C_SWI
        len_bytes = 2
        srcl = rs16
        srcr = 24
        imm = simm5_11_s64

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x003A)
    if cond:
        op = OP_C_SDI
        len_bytes = 2
        srcl = rs16
        srcr = 24
        imm = simm5_11_s64

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x000A)
    if cond:
        op = OP_C_LWI
        len_bytes = 2
        srcl = rs16
        imm = simm5_11_s64

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x001A)
    if cond:
        op = OP_C_LDI
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16
        imm = simm5_11_s64

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0006)
    if cond:
        op = OP_C_MOVR
        len_bytes = 2
        regdst = rd16
        srcl = rs16

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0026)
    if cond:
        op = OP_C_SETC_EQ
        len_bytes = 2
        srcl = rs16
        srcr = rd16

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0036)
    if cond:
        op = OP_C_SETC_NE
        len_bytes = 2
        srcl = rs16
        srcr = rd16

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x001C)
    if cond:
        op = OP_C_SETC_TGT
        len_bytes = 2
        srcl = rs16

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0038)
    if cond:
        op = OP_C_OR
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16
        srcr = rd16

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x501C)
    if cond:
        op = OP_C_SEXT_W
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x681C)
    if cond:
        op = OP_C_ZEXT_W
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = rs16

    # C.CMP.EQI/NEI: compare t#1 against simm5, write 0/1 to t-hand.
    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x002C)
    if cond:
        op = OP_CMP_EQI
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = 24  # t#1
        imm = simm5_6_s64

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x082C)
    if cond:
        op = OP_CMP_NEI
        len_bytes = 2
        regdst = 31  # t-hand
        srcl = 24  # t#1
        imm = simm5_6_s64

    cond = in16 & masked_eq(insn16, mask=0x000F, match=0x0002)
    if cond:
        op = OP_C_BSTART_DIRECT
        len_bytes = 2
        imm = simm12_s64_c.shl(amount=1)

    cond = in16 & masked_eq(insn16, mask=0x000F, match=0x0004)
    if cond:
        op = OP_C_BSTART_COND
        len_bytes = 2
        imm = simm12_s64_c.shl(amount=1)

    cond = in16 & masked_eq(insn16, mask=0xC7FF, match=0x0000)
    if cond:
        op = OP_C_BSTART_STD
        len_bytes = 2
        imm = brtype

    cond = in16 & masked_eq(insn16, mask=0xFFFF, match=0x0000)
    if cond:
        op = OP_C_BSTOP
        len_bytes = 2

    # --- 32-bit decode (reverse priority; EBREAK highest) ---
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000041)
    if cond:
        op = OP_FENTRY
        len_bytes = 4
        srcl = insn32[15:20]  # begin
        srcr = insn32[20:25]  # end
        uimm_hi = insn32[7:12].zext(width=64)
        uimm_lo = insn32[25:32].zext(width=64)
        imm = uimm_hi.shl(amount=10) | uimm_lo.shl(amount=3)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001041)
    if cond:
        op = OP_FEXIT
        len_bytes = 4
        srcl = insn32[15:20]  # begin
        srcr = insn32[20:25]  # end
        uimm_hi = insn32[7:12].zext(width=64)
        uimm_lo = insn32[25:32].zext(width=64)
        imm = uimm_hi.shl(amount=10) | uimm_lo.shl(amount=3)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002041)
    if cond:
        op = OP_FRET_RA
        len_bytes = 4
        srcl = insn32[15:20]  # begin
        srcr = insn32[20:25]  # end
        uimm_hi = insn32[7:12].zext(width=64)
        uimm_lo = insn32[25:32].zext(width=64)
        imm = uimm_hi.shl(amount=10) | uimm_lo.shl(amount=3)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003041)
    if cond:
        op = OP_FRET_STK
        len_bytes = 4
        srcl = insn32[15:20]  # begin
        srcr = insn32[20:25]  # end
        uimm_hi = insn32[7:12].zext(width=64)
        uimm_lo = insn32[25:32].zext(width=64)
        imm = uimm_hi.shl(amount=10) | uimm_lo.shl(amount=3)

    # Memory templates (standalone blocks): MCOPY/MSET.
    #
    # Encoding (QEMU reference): opcode=0x31, funct7=0x10.
    # - MCOPY: funct3=0, rs1=dst, rs2=src, size_reg in bits[31:27]
    # - MSET : funct3=1, rs1=dst, rs2=value, size_reg in bits[31:27]
    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x20000031)
    if cond:
        op = OP_MCOPY
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        imm = srcp_32.zext(width=64)  # size reg index

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x20001031)
    if cond:
        op = OP_MSET
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        imm = srcp_32.zext(width=64)  # size reg index

    # Decoupled tile headers (bring-up subset): BSTART.TMA.
    # Match the low 15 bits of known encodings (TLOAD/TSTORE variants share it).
    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00001181)
    if cond:
        op = OP_BSTART_TMA
        len_bytes = 4

    # Decoupled body pointer: B.TEXT (simm25 in halfwords; target = PC + (simm25 << 1)).
    # simm25 occupies bits[31:7].
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001003)
    if cond:
        op = OP_B_TEXT
        len_bytes = 4
        imm = insn32[7:32].sext(width=64)

    # Tile header descriptors: B.IOT/B.IOTI/B.IOR.
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004013)
    if cond:
        op = OP_B_IOT
        len_bytes = 4

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006013)
    if cond:
        op = OP_B_IOTI
        len_bytes = 4

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000013)
    if cond:
        op = OP_B_IOR
        len_bytes = 4

    cond = in32 & masked_eq(insn32, mask=0x0000007F, match=0x00000017)
    if cond:
        op = OP_LUI
        len_bytes = 4
        regdst = rd32
        imm = imm20_s64.shl(amount=12)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000005)
    if cond:
        op = OP_ADD
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001005)
    if cond:
        op = OP_SUB
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002005)
    if cond:
        op = OP_AND
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003005)
    if cond:
        op = OP_OR
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004005)
    if cond:
        op = OP_XOR
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002015)
    if cond:
        op = OP_ANDI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002035)
    if cond:
        op = OP_ANDIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003015)
    if cond:
        op = OP_ORI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003035)
    if cond:
        op = OP_ORIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004035)
    if cond:
        op = OP_XORIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    # Mul/Div/Rem (benchmarks).
    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00000047)
    if cond:
        op = OP_MUL
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00002047)
    if cond:
        op = OP_MULW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    # MADD/MADDW: RegDst = SrcD + (SrcL * SrcR). SrcD is in bits[31:27] and is
    # carried through our pipeline in `srcp`.
    cond = in32 & masked_eq(insn32, mask=0x0600707F, match=0x00006047)
    if cond:
        op = OP_MADD
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcp = srcp_32

    cond = in32 & masked_eq(insn32, mask=0x0600707F, match=0x00007047)
    if cond:
        op = OP_MADDW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcp = srcp_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00000057)
    if cond:
        op = OP_DIV
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00001057)
    if cond:
        op = OP_DIVU
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00002057)
    if cond:
        op = OP_DIVW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00003057)
    if cond:
        op = OP_DIVUW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00004057)
    if cond:
        op = OP_REM
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00005057)
    if cond:
        op = OP_REMU
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00006057)
    if cond:
        op = OP_REMW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00007057)
    if cond:
        op = OP_REMUW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00007005)
    if cond:
        op = OP_SLL
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00005005)
    if cond:
        op = OP_SRL
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00006005)
    if cond:
        op = OP_SRA
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFC00707F, match=0x00007015)
    if cond:
        op = OP_SLLI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        shamt = shamt6_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00005035)
    if cond:
        op = OP_SRLIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        shamt = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00006035)
    if cond:
        op = OP_SRAIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        shamt = rs2_32

    cond = in32 & masked_eq(insn32, mask=0xFE00707F, match=0x00007035)
    if cond:
        op = OP_SLLIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        shamt = rs2_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000067)
    if cond:
        op = OP_BXS
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = insn32[26:32]  # imms (lsb)
        srcp = insn32[20:26]  # imml (width-1)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001067)
    if cond:
        op = OP_BXU
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = insn32[26:32]  # imms (lsb)
        srcp = insn32[20:26]  # imml (width-1)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00006045)
    if cond:
        op = OP_CMP_LTU
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    # CMP.* immediate variants.
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000055)
    if cond:
        op = OP_CMP_EQI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001055)
    if cond:
        op = OP_CMP_NEI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002055)
    if cond:
        op = OP_CMP_ANDI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003055)
    if cond:
        op = OP_CMP_ORI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004055)
    if cond:
        op = OP_CMP_LTI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005055)
    if cond:
        op = OP_CMP_GEI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006055)
    if cond:
        op = OP_CMP_LTUI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_u64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00007055)
    if cond:
        op = OP_CMP_GEUI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_u64

    # SETC.* immediate variants (opcode 0x75): update commit_cond (committed in WB).
    # These encode shamt in bits[11:7] (RegDst field in the ISA).
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000075)
    if cond:
        op = OP_SETC_EQI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32  # bits[11:7]
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001075)
    if cond:
        op = OP_SETC_NEI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002075)
    if cond:
        op = OP_SETC_ANDI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003075)
    if cond:
        op = OP_SETC_ORI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004075)
    if cond:
        op = OP_SETC_LTI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005075)
    if cond:
        op = OP_SETC_GEI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006075)
    if cond:
        op = OP_SETC_LTUI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32
        imm = imm12_u64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00007075)
    if cond:
        op = OP_SETC_GEUI
        len_bytes = 4
        srcl = rs1_32
        shamt = rd32  # bits[11:7]
        imm = imm12_u64

    # SETC.* (register forms): update commit_cond (committed in WB).
    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00000065)
    if cond:
        op = OP_SETC_EQ
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00001065)
    if cond:
        op = OP_SETC_NE
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00002065)
    if cond:
        op = OP_SETC_AND
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00003065)
    if cond:
        op = OP_SETC_OR
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00004065)
    if cond:
        op = OP_SETC_LT
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00006065)
    if cond:
        op = OP_SETC_LTU
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00005065)
    if cond:
        op = OP_SETC_GE
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00007065)
    if cond:
        op = OP_SETC_GEU
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    # SETC.TGT: set commit target to SrcL (legal only inside a non-FALL block in the ISA).
    cond = in32 & masked_eq(insn32, mask=0xFFF07FFF, match=0x0000403B)
    if cond:
        op = OP_C_SETC_TGT
        len_bytes = 4
        srcl = rs1_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004019)
    if cond:
        op = OP_LBUI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000019)
    if cond:
        op = OP_LBI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001019)
    if cond:
        op = OP_LHI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005019)
    if cond:
        op = OP_LHUI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006019)
    if cond:
        op = OP_LWUI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000009)
    if cond:
        op = OP_LB
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004009)
    if cond:
        op = OP_LBU
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001009)
    if cond:
        op = OP_LH
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00005009)
    if cond:
        op = OP_LHU
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002009)
    if cond:
        op = OP_LW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00006009)
    if cond:
        op = OP_LWU
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003009)
    if cond:
        op = OP_LD
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003019)
    if cond:
        op = OP_LDI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00001001)
    if cond:
        op = OP_BSTART_STD_FALL
        len_bytes = 4

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00002001)
    if cond:
        op = OP_BSTART_STD_DIRECT
        len_bytes = 4
        imm = simm17_s64.shl(amount=1)

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00003001)
    if cond:
        op = OP_BSTART_STD_COND
        len_bytes = 4
        imm = simm17_s64.shl(amount=1)

    # BSTART.STD IND/ICALL/RET: no embedded target; requires SETC.TGT within the block.
    # For these, reuse OP_C_BSTART_STD internal op and carry BrType in `imm`.
    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00005001)
    if cond:
        op = OP_C_BSTART_STD
        len_bytes = 4
        imm = 5

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00006001)
    if cond:
        op = OP_C_BSTART_STD
        len_bytes = 4
        imm = 6

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00007001)
    if cond:
        op = OP_C_BSTART_STD
        len_bytes = 4
        imm = 7

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004025)
    if cond:
        op = OP_XORW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002025)
    if cond:
        op = OP_ANDW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003025)
    if cond:
        op = OP_ORW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000025)
    if cond:
        op = OP_ADDW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001025)
    if cond:
        op = OP_SUBW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = shamt5_32

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000077)
    if cond:
        op = OP_CSEL
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        srcp = srcp_32

    # Stores (immediate offset).
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000059)
    if cond:
        op = OP_SBI
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        imm = simm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001059)
    if cond:
        op = OP_SHI
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        imm = simm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002059)
    if cond:
        op = OP_SWI
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        imm = simm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003059)
    if cond:
        op = OP_SDI
        len_bytes = 4
        srcl = rs1_32
        srcr = rs2_32
        imm = simm12_s64

    # Stores (indexed). Encoding uses SrcD in bits[31:27], SrcL base in bits[19:15], SrcR idx in bits[24:20].
    # We map: srcp=value (SrcD), srcl=base (SrcL), srcr=index (SrcR), srcr_type=SrcRType, shamt=fixed scale.
    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00000049)
    if cond:
        op = OP_SB
        len_bytes = 4
        srcp = insn32[27:32]
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = 0

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00001049)
    if cond:
        op = OP_SH
        len_bytes = 4
        srcp = insn32[27:32]
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = 1

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00002049)
    if cond:
        op = OP_SW
        len_bytes = 4
        srcp = insn32[27:32]
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = 2

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00003049)
    if cond:
        op = OP_SD
        len_bytes = 4
        srcp = insn32[27:32]
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32
        shamt = 3

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002019)
    if cond:
        op = OP_LWI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_s64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000035)
    if cond:
        op = OP_ADDIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_u64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001035)
    if cond:
        op = OP_SUBIW
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_u64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000015)
    if cond:
        op = OP_ADDI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_u64

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001015)
    if cond:
        op = OP_SUBI
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        imm = imm12_u64

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00000045)
    if cond:
        op = OP_CMP_EQ
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00001045)
    if cond:
        op = OP_CMP_NE
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00004045)
    if cond:
        op = OP_CMP_LT
        len_bytes = 4
        regdst = rd32
        srcl = rs1_32
        srcr = rs2_32
        srcr_type = srcr_type_32

    cond = in32 & masked_eq(insn32, mask=0xF0FFFFFF, match=0x0010102B)
    if cond:
        op = OP_EBREAK
        len_bytes = 4

    # Opcode overlap group (QEMU: insn32.decode):
    # - SETRET: specialized ADDTPC encoding with rd=RA (x10), but different semantics.
    # - ADDTPC: PC-relative page base for any other rd.
    cond = in32 & masked_eq(insn32, mask=0x0000007F, match=0x00000007)
    if cond:
        op = OP_ADDTPC
        len_bytes = 4
        regdst = rd32
        imm = imm20_s64.shl(amount=12)

        if rd32 == 10:
            op = OP_SETRET
            regdst = 10  # ra
            imm = imm20_u64.shl(amount=1)

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00004001)
    if cond:
        op = OP_BSTART_STD_CALL
        len_bytes = 4
        imm = simm17_s64.shl(amount=1)

    # --- 48-bit HL decode (highest priority overall) ---
    hl_bstart_hi12 = pfx16[4:16].zext(width=64)
    hl_bstart_lo17 = insn48[31:48].zext(width=64)
    hl_bstart_simm_hw = (hl_bstart_hi12.shl(amount=18) | hl_bstart_lo17.shl(amount=1)).trunc(width=30).sext(width=64)
    # HL.BSTART simm is in halfwords (QEMU: target = PC + (simm << 1)).
    # Decode emits a byte offset.
    hl_bstart_off = hl_bstart_simm_hw

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00001001000E)
    if cond:
        op = OP_BSTART_STD_FALL
        len_bytes = 6

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00002001000E)
    if cond:
        op = OP_BSTART_STD_DIRECT
        len_bytes = 6
        imm = hl_bstart_off

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00003001000E)
    if cond:
        op = OP_BSTART_STD_COND
        len_bytes = 6
        imm = hl_bstart_off

    cond = is_hl & masked_eq(insn48, mask=0x00007FFF000F, match=0x00004001000E)
    if cond:
        op = OP_BSTART_STD_CALL
        len_bytes = 6
        imm = hl_bstart_off

    # HL.<load>.PCR: PC-relative load, funct3 encodes width/signedness.
    cond = is_hl & masked_eq(insn48, mask=0x0000007F000F, match=0x00000039000E)
    if cond:
        len_bytes = 6
        regdst = insn48[23:28]
        simm_hi12 = pfx16[4:16].zext(width=64)
        simm_lo17 = insn48[31:48].zext(width=64)
        simm29 = (simm_hi12.shl(amount=17) | simm_lo17).trunc(width=29).sext(width=64)
        imm = simm29

        # RISC-V-like funct3: 0=LB,1=LH,2=LW,3=LD,4=LBU,5=LHU,6=LWU.
        funct3 = insn48[28:31]
        op = OP_HL_LW_PCR
        if funct3 == 0:
            op = OP_HL_LB_PCR
        if funct3 == 1:
            op = OP_HL_LH_PCR
        if funct3 == 2:
            op = OP_HL_LW_PCR
        if funct3 == 3:
            op = OP_HL_LD_PCR
        if funct3 == 4:
            op = OP_HL_LBU_PCR
        if funct3 == 5:
            op = OP_HL_LHU_PCR
        if funct3 == 6:
            op = OP_HL_LWU_PCR

    # HL.<store>.PCR: PC-relative store, funct3 encodes width.
    cond = is_hl & masked_eq(insn48, mask=0x0000007F000F, match=0x00000069000E)
    if cond:
        len_bytes = 6
        srcl = insn48[31:36]
        simm_hi12 = pfx16[4:16].zext(width=64)
        simm_mid5 = insn48[23:28].zext(width=64)
        simm_lo12 = insn48[36:48].zext(width=64)
        simm29 = (simm_hi12.shl(amount=17) | simm_mid5.shl(amount=12) | simm_lo12).trunc(width=29).sext(width=64)
        imm = simm29

        funct3 = insn48[28:31]
        op = OP_HL_SW_PCR
        if funct3 == 0:
            op = OP_HL_SB_PCR
        if funct3 == 1:
            op = OP_HL_SH_PCR
        if funct3 == 2:
            op = OP_HL_SW_PCR
        if funct3 == 3:
            op = OP_HL_SD_PCR

    # HL.ANDI: extended immediate variant of ANDI (simm24).
    cond = is_hl & masked_eq(insn48, mask=0x0000707F000F, match=0x00002015000E)
    if cond:
        op = OP_ANDI
        len_bytes = 6
        regdst = rd_hl
        srcl = main32[15:20]
        imm_hi12 = pfx16[4:16]
        imm_lo12 = main32[20:32]
        imm24 = imm_hi12.zext(width=24).shl(amount=12) | imm_lo12.zext(width=24)
        imm = imm24.sext(width=64)

    cond = is_hl & masked_eq(insn48, mask=0x0000007F000F, match=0x00000017000E)
    if cond:
        op = OP_HL_LUI
        len_bytes = 6
        regdst = rd_hl
        imm = imm_hl_lui

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
