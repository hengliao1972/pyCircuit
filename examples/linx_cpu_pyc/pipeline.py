from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Reg


@dataclass(frozen=True)
class CoreState:
    pc: Reg
    br_kind: Reg
    br_base_pc: Reg
    br_off: Reg
    commit_cond: Reg
    commit_tgt: Reg
    # Decoupled-block state (B.TEXT out-of-line bodies).
    dec_hdr_active: Reg
    in_body: Reg
    body_tpc: Reg
    return_pc: Reg
    exit_code: Reg
    cycles: Reg
    halted: Reg


@dataclass(frozen=True)
class IfIdRegs:
    valid: Reg
    pc: Reg
    window: Reg
    pred_next_pc: Reg


@dataclass(frozen=True)
class IdExRegs:
    valid: Reg
    pc: Reg
    window: Reg
    pred_next_pc: Reg
    op: Reg
    len_bytes: Reg
    regdst: Reg
    srcl: Reg
    srcr: Reg
    srcr_type: Reg
    shamt: Reg
    srcp: Reg
    imm: Reg
    srcl_val: Reg
    srcr_val: Reg
    srcp_val: Reg


@dataclass(frozen=True)
class ExMemRegs:
    valid: Reg
    pc: Reg
    window: Reg
    pred_next_pc: Reg
    op: Reg
    len_bytes: Reg
    regdst: Reg
    srcl: Reg
    srcr: Reg
    imm: Reg
    alu: Reg
    is_load: Reg
    is_store: Reg
    size: Reg
    addr: Reg
    wdata: Reg


@dataclass(frozen=True)
class MemWbRegs:
    valid: Reg
    pc: Reg
    window: Reg
    pred_next_pc: Reg
    op: Reg
    len_bytes: Reg
    regdst: Reg
    srcl: Reg
    srcr: Reg
    imm: Reg
    value: Reg
    # Memory commit fields (loads/stores commit in WB for precise flush behavior).
    is_load: Reg
    is_store: Reg
    size: Reg
    addr: Reg
    wdata: Reg


@dataclass(frozen=True)
class RegFiles:
    gpr: list[Reg]
    t: list[Reg]
    u: list[Reg]
