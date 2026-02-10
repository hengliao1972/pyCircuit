from __future__ import annotations

from pycircuit import CycleAwareCircuit, CycleAwareReg, CycleAwareSignal


def build_if_stage(
    m: CycleAwareCircuit,
    *,
    do_if: CycleAwareSignal,
    ifid_window: CycleAwareReg,
    ifid_pc: CycleAwareReg,
    fetch_pc: CycleAwareSignal,
    mem_rdata: CycleAwareSignal,
    do_id_enable: CycleAwareReg | None = None,
) -> None:
    # IF stage: latch instruction window and fetch PC.
    ifid_window.set(mem_rdata, when=do_if)
    ifid_pc.set(fetch_pc, when=do_if)
    # 与 ifid 同相位更新，使 C++ 仿真中 do_id 相对 do_if 只晚 1 个时钟周期
    if do_id_enable is not None:
        do_id_enable.set(do_if, when=do_if)