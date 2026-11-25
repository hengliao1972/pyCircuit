from __future__ import annotations

import sys
from pathlib import Path

from pycircuit import CycleAwareTb, Tb, compile_cycle_aware, testbench

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from sync_mem_nm_ports import build  # noqa: E402
from sync_mem_nm_ports_config import DEFAULT_PARAMS, TB_PRESETS  # noqa: E402


@testbench
def tb(t: Tb) -> None:
    tb = CycleAwareTb(t)
    p = TB_PRESETS["smoke"]
    tb.clock("clk")
    tb.reset("rst", cycles_asserted=2, cycles_deasserted=1)
    tb.timeout(int(p["timeout"]))

    # Default drives.
    for i in range(3):
        tb.drive(f"ren{i}", 0)
        tb.drive(f"raddr{i}", 0)
    for i in range(2):
        tb.drive(f"wvalid{i}", 0)
        tb.drive(f"waddr{i}", 0)
        tb.drive(f"wdata{i}", 0)
        tb.drive(f"wstrb{i}", 0)

    # --- cycle 0 ---: write 0xAAAAAAAA into entry 1 through write port 0.
    tb.drive("wvalid0", 1)
    tb.drive("waddr0", 1)
    tb.drive("wdata0", 0xAAAAAAAA)
    tb.drive("wstrb0", 0xF)

    tb.next()  # --- cycle 1 ---
    # All three read ports read entry 1.
    for i in range(3):
        tb.drive(f"ren{i}", 1)
        tb.drive(f"raddr{i}", 1)
    tb.drive("wvalid0", 0)
    tb.expect("rdata0", 0xAAAAAAAA, phase="post")
    tb.expect("rdata1", 0xAAAAAAAA, phase="post")
    tb.expect("rdata2", 0xAAAAAAAA, phase="post")

    tb.next()  # --- cycle 2 ---
    # Both write ports hit entry 2 while reading it: the higher write index
    # wins, and read_mode="forward" forwards that same-edge write.
    for i in range(3):
        tb.drive(f"ren{i}", 0)
    tb.drive("wvalid0", 1)
    tb.drive("waddr0", 2)
    tb.drive("wdata0", 0x11111111)
    tb.drive("wstrb0", 0xF)
    tb.drive("wvalid1", 1)
    tb.drive("waddr1", 2)
    tb.drive("wdata1", 0x22222222)
    tb.drive("wstrb1", 0xF)
    tb.drive("ren0", 1)
    tb.drive("raddr0", 2)
    tb.expect("rdata0", 0x22222222, phase="post", msg="higher write port must win")

    tb.next()  # --- cycle 3 ---
    # Read back the committed entry.
    tb.drive("wvalid0", 0)
    tb.drive("wvalid1", 0)
    tb.drive("ren0", 1)
    tb.drive("raddr0", 2)
    tb.expect("rdata0", 0x22222222, phase="post")

    tb.next()  # --- cycle 4 ---
    # Byte strobe on write port 1 touches lane 0 only.
    tb.drive("ren0", 0)
    tb.drive("wvalid1", 1)
    tb.drive("waddr1", 2)
    tb.drive("wdata1", 0xDEADBEEF)
    tb.drive("wstrb1", 0x1)

    tb.next()  # --- cycle 5 ---
    tb.drive("wvalid1", 0)
    tb.drive("wstrb1", 0)
    tb.drive("ren0", 1)
    tb.drive("raddr0", 2)
    tb.expect("rdata0", 0x222222EF, phase="post")

    tb.finish(at=int(p["finish"]))


if __name__ == "__main__":
    print(compile_cycle_aware(build, name="tb_sync_mem_nm_ports_top", eager=True, **DEFAULT_PARAMS).emit_mlir())
