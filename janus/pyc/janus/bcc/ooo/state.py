from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.dsl import Signal

from ..isa import BK_FALL
from ..util import Consts
from .params import OooParams


@dataclass(frozen=True)
class CoreCtrlRegs:
    halted: Reg
    cycles: Reg
    pc: Reg
    fpc: Reg
    br_kind: Reg
    br_base_pc: Reg
    br_off: Reg
    commit_cond: Reg
    commit_tgt: Reg
    flush_pending: Reg
    flush_pc: Reg
    macro_active: Reg
    macro_wait_commit: Reg
    macro_phase: Reg
    macro_op: Reg
    macro_begin: Reg
    macro_end: Reg
    macro_stacksize: Reg
    macro_reg: Reg
    macro_i: Reg
    macro_sp_base: Reg


@dataclass(frozen=True)
class IfuRegs:
    f4_valid: Reg
    f4_pc: Reg
    f4_window: Reg


@dataclass(frozen=True)
class RenameRegs:
    smap: list[Reg]
    cmap: list[Reg]
    free_mask: Reg
    ready_mask: Reg


@dataclass(frozen=True)
class RobRegs:
    head: Reg
    tail: Reg
    count: Reg

    valid: list[Reg]
    done: list[Reg]
    op: list[Reg]
    len_bytes: list[Reg]

    dst_kind: list[Reg]
    dst_areg: list[Reg]
    pdst: list[Reg]
    value: list[Reg]

    store_addr: list[Reg]
    store_data: list[Reg]
    store_size: list[Reg]
    is_store: list[Reg]
    macro_begin: list[Reg]
    macro_end: list[Reg]


@dataclass(frozen=True)
class IqRegs:
    valid: list[Reg]
    rob: list[Reg]
    op: list[Reg]
    pc: list[Reg]
    imm: list[Reg]
    srcl: list[Reg]
    srcr: list[Reg]
    srcr_type: list[Reg]
    shamt: list[Reg]
    srcp: list[Reg]
    pdst: list[Reg]
    has_dst: list[Reg]


def make_core_ctrl_regs(m: Circuit, clk: Signal, rst: Signal, *, boot_pc: Wire, consts: Consts) -> CoreCtrlRegs:
    c = m.const
    with m.scope("state"):
        halted = m.out("halted", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1)
        cycles = m.out("cycles", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1)

        # Commit PC (architectural PC of the next commit).
        pc = m.out("pc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1)

        # Fetch PC (fall-through only; redirected on commit-time boundary).
        fpc = m.out("fpc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1)

        # Block transition kind (must cover BK_DIRECT/BK_IND/BK_ICALL).
        br_kind = m.out("br_kind", clk=clk, rst=rst, width=3, init=c(BK_FALL, width=3), en=consts.one1)
        br_base_pc = m.out("br_base_pc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1)
        br_off = m.out("br_off", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1)
        commit_cond = m.out("commit_cond", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1)
        commit_tgt = m.out("commit_tgt", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1)

        # Redirect handling (one-cycle "bubble flush" after a taken boundary).
        flush_pending = m.out("flush_pending", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1)
        flush_pc = m.out("flush_pc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1)

        # Template macro blocks (FENTRY/FEXIT/FRET.*) microcode.
        macro_active = m.out("macro_active", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1)
        macro_wait_commit = m.out("macro_wait_commit", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1)
        macro_phase = m.out("macro_phase", clk=clk, rst=rst, width=2, init=consts.zero1.zext(width=2), en=consts.one1)
        macro_op = m.out("macro_op", clk=clk, rst=rst, width=12, init=c(0, width=12), en=consts.one1)
        macro_begin = m.out("macro_begin", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1)
        macro_end = m.out("macro_end", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1)
        macro_stacksize = m.out("macro_stacksize", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1)
        macro_reg = m.out("macro_reg", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1)
        macro_i = m.out("macro_i", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1)
        macro_sp_base = m.out("macro_sp_base", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1)

    return CoreCtrlRegs(
        halted=halted,
        cycles=cycles,
        pc=pc,
        fpc=fpc,
        br_kind=br_kind,
        br_base_pc=br_base_pc,
        br_off=br_off,
        commit_cond=commit_cond,
        commit_tgt=commit_tgt,
        flush_pending=flush_pending,
        flush_pc=flush_pc,
        macro_active=macro_active,
        macro_wait_commit=macro_wait_commit,
        macro_phase=macro_phase,
        macro_op=macro_op,
        macro_begin=macro_begin,
        macro_end=macro_end,
        macro_stacksize=macro_stacksize,
        macro_reg=macro_reg,
        macro_i=macro_i,
        macro_sp_base=macro_sp_base,
    )


def make_ifu_regs(m: Circuit, clk: Signal, rst: Signal, *, boot_pc: Wire, consts: Consts) -> IfuRegs:
    with m.scope("ifu"):
        f4_valid = m.out("f4_valid", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1)
        f4_pc = m.out("f4_pc", clk=clk, rst=rst, width=64, init=boot_pc, en=consts.one1)
        f4_window = m.out("f4_window", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1)

    return IfuRegs(f4_valid=f4_valid, f4_pc=f4_pc, f4_window=f4_window)


def make_prf(m: Circuit, clk: Signal, rst: Signal, *, boot_sp: Wire, consts: Consts, p: OooParams) -> list[Reg]:
    with m.scope("prf"):
        prf: list[Reg] = []
        for i in range(p.pregs):
            init = consts.zero64
            if i == 1:
                init = boot_sp
            prf.append(m.out(f"p{i}", clk=clk, rst=rst, width=64, init=init, en=consts.one1))
        return prf


def make_rename_regs(m: Circuit, clk: Signal, rst: Signal, *, consts: Consts, p: OooParams) -> RenameRegs:
    c = m.const
    with m.scope("rename"):
        smap: list[Reg] = []
        cmap: list[Reg] = []
        for i in range(p.aregs):
            smap.append(m.out(f"smap{i}", clk=clk, rst=rst, width=p.ptag_w, init=c(i, width=p.ptag_w), en=consts.one1))
            cmap.append(m.out(f"cmap{i}", clk=clk, rst=rst, width=p.ptag_w, init=c(i, width=p.ptag_w), en=consts.one1))

        free_init = ((1 << p.pregs) - 1) ^ ((1 << p.aregs) - 1)
        free_mask = m.out("free_mask", clk=clk, rst=rst, width=p.pregs, init=c(free_init, width=p.pregs), en=consts.one1)
        ready_mask = m.out("ready_mask", clk=clk, rst=rst, width=p.pregs, init=c((1 << p.pregs) - 1, width=p.pregs), en=consts.one1)

    return RenameRegs(smap=smap, cmap=cmap, free_mask=free_mask, ready_mask=ready_mask)


def make_rob_regs(m: Circuit, clk: Signal, rst: Signal, *, consts: Consts, p: OooParams) -> RobRegs:
    c = m.const
    tag0 = c(0, width=p.ptag_w)

    with m.scope("rob"):
        head = m.out("head", clk=clk, rst=rst, width=p.rob_w, init=c(0, width=p.rob_w), en=consts.one1)
        tail = m.out("tail", clk=clk, rst=rst, width=p.rob_w, init=c(0, width=p.rob_w), en=consts.one1)
        count = m.out("count", clk=clk, rst=rst, width=p.rob_w + 1, init=c(0, width=p.rob_w + 1), en=consts.one1)

        valid: list[Reg] = []
        done: list[Reg] = []
        op: list[Reg] = []
        len_bytes: list[Reg] = []
        dst_kind: list[Reg] = []
        dst_areg: list[Reg] = []
        pdst: list[Reg] = []
        value: list[Reg] = []
        store_addr: list[Reg] = []
        store_data: list[Reg] = []
        store_size: list[Reg] = []
        is_store: list[Reg] = []
        macro_begin: list[Reg] = []
        macro_end: list[Reg] = []

        for i in range(p.rob_depth):
            valid.append(m.out(f"v{i}", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1))
            done.append(m.out(f"done{i}", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1))
            op.append(m.out(f"op{i}", clk=clk, rst=rst, width=12, init=c(0, width=12), en=consts.one1))
            len_bytes.append(m.out(f"len{i}", clk=clk, rst=rst, width=3, init=consts.zero3, en=consts.one1))
            dst_kind.append(m.out(f"dk{i}", clk=clk, rst=rst, width=2, init=c(0, width=2), en=consts.one1))
            dst_areg.append(m.out(f"da{i}", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1))
            pdst.append(m.out(f"pd{i}", clk=clk, rst=rst, width=p.ptag_w, init=tag0, en=consts.one1))
            value.append(m.out(f"val{i}", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1))
            store_addr.append(m.out(f"sta{i}", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1))
            store_data.append(m.out(f"std{i}", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1))
            store_size.append(m.out(f"sts{i}", clk=clk, rst=rst, width=4, init=consts.zero4, en=consts.one1))
            is_store.append(m.out(f"isst{i}", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1))
            macro_begin.append(m.out(f"mb{i}", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1))
            macro_end.append(m.out(f"me{i}", clk=clk, rst=rst, width=6, init=c(0, width=6), en=consts.one1))

    return RobRegs(
        head=head,
        tail=tail,
        count=count,
        valid=valid,
        done=done,
        op=op,
        len_bytes=len_bytes,
        dst_kind=dst_kind,
        dst_areg=dst_areg,
        pdst=pdst,
        value=value,
        store_addr=store_addr,
        store_data=store_data,
        store_size=store_size,
        is_store=is_store,
        macro_begin=macro_begin,
        macro_end=macro_end,
    )


def make_iq_regs(m: Circuit, clk: Signal, rst: Signal, *, consts: Consts, p: OooParams, name: str = "iq") -> IqRegs:
    c = m.const
    tag0 = c(0, width=p.ptag_w)

    with m.scope(name):
        valid: list[Reg] = []
        rob: list[Reg] = []
        op: list[Reg] = []
        pc: list[Reg] = []
        imm: list[Reg] = []
        srcl: list[Reg] = []
        srcr: list[Reg] = []
        srcr_type: list[Reg] = []
        shamt: list[Reg] = []
        srcp: list[Reg] = []
        pdst: list[Reg] = []
        has_dst: list[Reg] = []
        for i in range(p.iq_depth):
            valid.append(m.out(f"v{i}", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1))
            rob.append(m.out(f"rob{i}", clk=clk, rst=rst, width=p.rob_w, init=c(0, width=p.rob_w), en=consts.one1))
            op.append(m.out(f"op{i}", clk=clk, rst=rst, width=12, init=c(0, width=12), en=consts.one1))
            pc.append(m.out(f"pc{i}", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1))
            imm.append(m.out(f"imm{i}", clk=clk, rst=rst, width=64, init=consts.zero64, en=consts.one1))
            srcl.append(m.out(f"sl{i}", clk=clk, rst=rst, width=p.ptag_w, init=tag0, en=consts.one1))
            srcr.append(m.out(f"sr{i}", clk=clk, rst=rst, width=p.ptag_w, init=tag0, en=consts.one1))
            srcr_type.append(m.out(f"st{i}", clk=clk, rst=rst, width=2, init=consts.zero1.zext(width=2), en=consts.one1))
            shamt.append(m.out(f"sh{i}", clk=clk, rst=rst, width=6, init=consts.zero6, en=consts.one1))
            srcp.append(m.out(f"sp{i}", clk=clk, rst=rst, width=p.ptag_w, init=tag0, en=consts.one1))
            pdst.append(m.out(f"pd{i}", clk=clk, rst=rst, width=p.ptag_w, init=tag0, en=consts.one1))
            has_dst.append(m.out(f"hd{i}", clk=clk, rst=rst, width=1, init=consts.zero1, en=consts.one1))

    return IqRegs(
        valid=valid,
        rob=rob,
        op=op,
        pc=pc,
        imm=imm,
        srcl=srcl,
        srcr=srcr,
        srcr_type=srcr_type,
        shamt=shamt,
        srcp=srcp,
        pdst=pdst,
        has_dst=has_dst,
    )
