from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareDomain, CycleAwareSignal, mux

from examples.linx_cpu_pyc.isa import (
    OP_ADDTPC,
    OP_ADDI,
    OP_ADDIW,
    OP_ADDW,
    OP_ANDW,
    OP_BSTART_STD_CALL,
    OP_CMP_EQ,
    OP_C_BSTART_COND,
    OP_C_BSTART_STD,
    OP_CSEL,
    OP_C_LWI,
    OP_C_MOVI,
    OP_C_MOVR,
    OP_C_SETC_EQ,
    OP_C_SETC_TGT,
    OP_C_SETRET,
    OP_C_SWI,
    OP_HL_LUI,
    OP_LWI,
    OP_ORW,
    OP_SDI,
    OP_SUBI,
    OP_SWI,
    OP_XORW,
)
from examples.linx_cpu_pyc_cycle_aware.util import Consts


def ex_stage_logic(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    *,
    pc: CycleAwareSignal,
    op: CycleAwareSignal,
    len_bytes: CycleAwareSignal,
    regdst: CycleAwareSignal,
    srcl_val: CycleAwareSignal,
    srcr_val: CycleAwareSignal,
    srcp_val: CycleAwareSignal,
    imm: CycleAwareSignal,
    consts: Consts,
) -> dict:
    """纯组合逻辑：ID 级信号 -> EX 级输出（无 flop），供 domain.next() 流水线使用。"""
    z1, z3, z64 = consts.zero1, consts.zero3, consts.zero64
    one64 = consts.one64
    c = lambda v, w: domain.const(v, width=w)
    pc_val = pc

    op_c_bstart_std = op.eq(OP_C_BSTART_STD)
    op_c_bstart_cond = op.eq(OP_C_BSTART_COND)
    op_bstart_std_call = op.eq(OP_BSTART_STD_CALL)
    op_c_movr = op.eq(OP_C_MOVR)
    op_c_movi = op.eq(OP_C_MOVI)
    op_c_setret = op.eq(OP_C_SETRET)
    op_c_setc_eq = op.eq(OP_C_SETC_EQ)
    op_c_setc_tgt = op.eq(OP_C_SETC_TGT)
    op_addtpc = op.eq(OP_ADDTPC)
    op_addi = op.eq(OP_ADDI)
    op_subi = op.eq(OP_SUBI)
    op_addiw = op.eq(OP_ADDIW)
    op_addw = op.eq(OP_ADDW)
    op_orw = op.eq(OP_ORW)
    op_andw = op.eq(OP_ANDW)
    op_xorw = op.eq(OP_XORW)
    op_cmp_eq = op.eq(OP_CMP_EQ)
    op_csel = op.eq(OP_CSEL)
    op_hl_lui = op.eq(OP_HL_LUI)
    op_lwi = op.eq(OP_LWI)
    op_c_lwi = op.eq(OP_C_LWI)
    op_swi = op.eq(OP_SWI)
    op_c_swi = op.eq(OP_C_SWI)
    op_sdi = op.eq(OP_SDI)

    off = imm.shl(amount=2)
    alu = z64
    is_load, is_store, size, addr, wdata = z1, z1, z3, z64, z64
    is_block_marker = op_c_bstart_std | op_c_bstart_cond | op_bstart_std_call
    alu = mux(is_block_marker, imm, alu)
    alu = mux(op_c_movr, srcl_val, alu)
    alu = mux(op_c_movi, imm, alu)
    alu = mux(op_c_setret, pc_val + imm, alu)
    setc_eq_val = mux(srcl_val.eq(srcr_val), one64, z64)
    alu = mux(op_c_setc_eq, setc_eq_val, alu)
    alu = mux(op_c_setc_tgt, srcl_val, alu)
    pc_page = pc_val & 0xFFFF_FFFF_FFFF_F000
    alu = mux(op_addtpc, pc_page + imm, alu)
    alu = mux(op_addi, srcl_val + imm, alu)
    subi_val = srcl_val + ((~imm) + 1)
    alu = mux(op_subi, subi_val, alu)
    addiw_val = (srcl_val.trunc(width=32) + imm.trunc(width=32)).sext(width=64)
    alu = mux(op_addiw, addiw_val, alu)
    addw_val = (srcl_val.trunc(width=32) + srcr_val.trunc(width=32)).sext(width=64)
    orw_val = (srcl_val.trunc(width=32) | srcr_val.trunc(width=32)).sext(width=64)
    andw_val = (srcl_val.trunc(width=32) & srcr_val.trunc(width=32)).sext(width=64)
    xorw_val = (srcl_val.trunc(width=32) ^ srcr_val.trunc(width=32)).sext(width=64)
    alu = mux(op_addw, addw_val, alu)
    alu = mux(op_orw, orw_val, alu)
    alu = mux(op_andw, andw_val, alu)
    alu = mux(op_xorw, xorw_val, alu)
    cmp_val = mux(srcl_val.eq(srcr_val), one64, z64)
    alu = mux(op_cmp_eq, cmp_val, alu)
    alu = mux(op_hl_lui, imm, alu)
    csel_val = mux(srcp_val.ne(0), srcr_val, srcl_val)
    alu = mux(op_csel, csel_val, alu)
    is_lwi = op_lwi | op_c_lwi
    lwi_addr = srcl_val + off
    is_load = mux(is_lwi, consts.one1, is_load)
    size = mux(is_lwi, c(4, 3), size)
    addr = mux(is_lwi, lwi_addr, addr)
    store_addr_swi = srcr_val + off
    store_addr_c_swi = srcl_val + off
    store_addr = mux(op_swi, store_addr_swi, store_addr_c_swi)
    store_data = mux(op_swi, srcl_val, srcr_val)
    is_swi = op_swi | op_c_swi
    is_store = mux(is_swi, consts.one1, is_store)
    size = mux(is_swi, c(4, 3), size)
    addr = mux(is_swi, store_addr, addr)
    wdata = mux(is_swi, store_data, wdata)
    sdi_off = imm.shl(amount=3)
    sdi_addr = srcr_val + sdi_off
    is_store = mux(op_sdi, consts.one1, is_store)
    size = mux(op_sdi, c(8, 3), size)
    addr = mux(op_sdi, sdi_addr, addr)
    wdata = mux(op_sdi, srcl_val, wdata)

    return {
        "op": op, "len_bytes": len_bytes, "pc": pc_val, "regdst": regdst,
        "alu": alu, "is_load": is_load, "is_store": is_store, "size": size,
        "addr": addr, "wdata": wdata,
    }
