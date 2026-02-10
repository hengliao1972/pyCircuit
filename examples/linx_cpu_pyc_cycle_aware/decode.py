from __future__ import annotations

from dataclasses import dataclass

from pycircuit import CycleAwareCircuit, CycleAwareSignal, mux

from examples.linx_cpu_pyc.isa import (
    OP_ADDTPC,
    OP_ADDI,
    OP_ADDIW,
    OP_ADDW,
    OP_ANDW,
    OP_BSTART_STD_CALL,
    OP_CMP_EQ,
    OP_CSEL,
    OP_C_BSTOP,
    OP_C_BSTART_COND,
    OP_C_BSTART_STD,
    OP_C_LWI,
    OP_C_MOVI,
    OP_C_MOVR,
    OP_C_SETC_EQ,
    OP_C_SETC_TGT,
    OP_C_SETRET,
    OP_C_SWI,
    OP_EBREAK,
    OP_HL_LUI,
    OP_INVALID,
    OP_LWI,
    OP_ORW,
    OP_SDI,
    OP_SUBI,
    OP_SWI,
    OP_XORW,
    REG_INVALID,
)
from examples.linx_cpu_pyc_cycle_aware.util import masked_eq


@dataclass(frozen=True)
class Decode:
    op: CycleAwareSignal
    len_bytes: CycleAwareSignal
    regdst: CycleAwareSignal
    srcl: CycleAwareSignal
    srcr: CycleAwareSignal
    srcp: CycleAwareSignal
    imm: CycleAwareSignal


def decode_window(m: CycleAwareCircuit, window: CycleAwareSignal) -> Decode:
    c = m.ca_const

    zero3 = c(0, width=3)
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
    srcp_32 = insn32[27:32]

    imm12_u64 = insn32[20:32]
    imm12_s64 = insn32[20:32].sext(width=64)

    imm20_s64 = insn32[12:32].sext(width=64)

    # SWI simm12 is split: {insn32[11:7], insn32[31:25]}.
    swi_lo5 = insn32[7:12]
    swi_hi7 = insn32[25:32]
    simm12_raw = swi_lo5.zext(width=12).shl(amount=7) | swi_hi7.zext(width=12)
    simm12_s64 = simm12_raw.sext(width=64)
    simm17_s64 = insn32[15:32].sext(width=64)

    # HL.LUI immediate packing (48-bit):
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
    simm5_11_s64 = insn16[11:16].sext(width=64)
    simm5_6_s64 = insn16[6:11].sext(width=64)
    simm12_s64_c = insn16[4:16].sext(width=64)
    uimm5 = insn16[6:11]
    brtype = insn16[11:14]

    # Default values
    op = c(OP_INVALID, width=6)
    len_bytes = zero3
    regdst = reg_invalid
    srcl = reg_invalid
    srcr = reg_invalid
    srcp = reg_invalid
    imm = zero64

    # --- 16-bit decode (using mux chain, reverse priority) ---
    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0016)
    op = mux(cond, c(OP_C_MOVI, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    regdst = mux(cond, rd16, regdst)
    imm = mux(cond, simm5_6_s64, imm)

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x5016)
    op = mux(cond, c(OP_C_SETRET, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    regdst = mux(cond, c(10, width=6), regdst)  # ra
    imm = mux(cond, uimm5.zext(width=6).shl(amount=1).zext(width=64), imm)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x002A)
    op = mux(cond, c(OP_C_SWI, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    srcl = mux(cond, rs16, srcl)
    srcr = mux(cond, c(24, width=6), srcr)
    imm = mux(cond, simm5_11_s64, imm)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x000A)
    op = mux(cond, c(OP_C_LWI, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    srcl = mux(cond, rs16, srcl)
    imm = mux(cond, simm5_11_s64, imm)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0006)
    op = mux(cond, c(OP_C_MOVR, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    regdst = mux(cond, rd16, regdst)
    srcl = mux(cond, rs16, srcl)

    cond = in16 & masked_eq(insn16, mask=0x003F, match=0x0026)
    op = mux(cond, c(OP_C_SETC_EQ, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    srcl = mux(cond, rs16, srcl)
    srcr = mux(cond, rd16, srcr)

    cond = in16 & masked_eq(insn16, mask=0xF83F, match=0x001C)
    op = mux(cond, c(OP_C_SETC_TGT, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    srcl = mux(cond, rs16, srcl)

    cond = in16 & masked_eq(insn16, mask=0x000F, match=0x0004)
    op = mux(cond, c(OP_C_BSTART_COND, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    imm = mux(cond, simm12_s64_c.shl(amount=1), imm)

    cond = in16 & masked_eq(insn16, mask=0xC7FF, match=0x0000)
    op = mux(cond, c(OP_C_BSTART_STD, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)
    imm = mux(cond, brtype.zext(width=64), imm)

    cond = in16 & masked_eq(insn16, mask=0xFFFF, match=0x0000)
    op = mux(cond, c(OP_C_BSTOP, width=6), op)
    len_bytes = mux(cond, c(2, width=3), len_bytes)

    # --- 32-bit decode (reverse priority) ---
    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00004025)
    op = mux(cond, c(OP_XORW, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002025)
    op = mux(cond, c(OP_ANDW, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003025)
    op = mux(cond, c(OP_ORW, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000025)
    op = mux(cond, c(OP_ADDW, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000077)
    op = mux(cond, c(OP_CSEL, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)
    srcp = mux(cond, srcp_32, srcp)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002059)
    op = mux(cond, c(OP_SWI, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)
    imm = mux(cond, simm12_s64, imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00003059)
    op = mux(cond, c(OP_SDI, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)
    imm = mux(cond, simm12_s64, imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00002019)
    op = mux(cond, c(OP_LWI, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    imm = mux(cond, imm12_s64, imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000035)
    op = mux(cond, c(OP_ADDIW, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    imm = mux(cond, imm12_u64.zext(width=64), imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00000015)
    op = mux(cond, c(OP_ADDI, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    imm = mux(cond, imm12_u64.zext(width=64), imm)

    cond = in32 & masked_eq(insn32, mask=0x0000707F, match=0x00001015)
    op = mux(cond, c(OP_SUBI, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    imm = mux(cond, imm12_u64.zext(width=64), imm)

    cond = in32 & masked_eq(insn32, mask=0xF800707F, match=0x00000045)
    op = mux(cond, c(OP_CMP_EQ, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    srcl = mux(cond, rs1_32, srcl)
    srcr = mux(cond, rs2_32, srcr)

    cond = in32 & masked_eq(insn32, mask=0xF0FFFFFF, match=0x0010102B)
    op = mux(cond, c(OP_EBREAK, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)

    cond = in32 & masked_eq(insn32, mask=0x0000007F, match=0x00000007)
    op = mux(cond, c(OP_ADDTPC, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    regdst = mux(cond, rd32, regdst)
    imm = mux(cond, imm20_s64.shl(amount=12), imm)

    cond = in32 & masked_eq(insn32, mask=0x00007FFF, match=0x00004001)
    op = mux(cond, c(OP_BSTART_STD_CALL, width=6), op)
    len_bytes = mux(cond, c(4, width=3), len_bytes)
    imm = mux(cond, simm17_s64.shl(amount=1), imm)

    # --- 48-bit HL.LUI (highest priority) ---
    cond = is_hl & masked_eq(insn48, mask=0x0000007F000F, match=0x00000017000E)
    op = mux(cond, c(OP_HL_LUI, width=6), op)
    len_bytes = mux(cond, c(6, width=3), len_bytes)
    regdst = mux(cond, rd_hl, regdst)
    imm = mux(cond, imm_hl_lui, imm)

    return Decode(op=op, len_bytes=len_bytes, regdst=regdst, srcl=srcl, srcr=srcr, srcp=srcp, imm=imm)
