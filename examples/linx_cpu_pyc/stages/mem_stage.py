from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareDomain, CycleAwareSignal, mux


def mem_stage_logic(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    ex_out: dict,
    mem_rdata: CycleAwareSignal,
) -> dict:
    """纯组合：EX 级输出 + 读出的 mem_rdata -> MEM/WB 级 value（无 flop）。"""
    op = ex_out["op"]
    len_bytes = ex_out["len_bytes"]
    pc = ex_out["pc"]
    regdst = ex_out["regdst"]
    alu = ex_out["alu"]
    is_load = ex_out["is_load"]
    is_store = ex_out["is_store"]
    load32 = mem_rdata.trunc(width=32)
    load64 = load32.sext(width=64)
    mem_val = alu
    mem_val = mux(is_load, load64, mem_val)
    mem_val = mux(is_store, domain.const(0, width=64), mem_val)
    return {"op": op, "len_bytes": len_bytes, "pc": pc, "regdst": regdst, "value": mem_val}
