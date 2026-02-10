from __future__ import annotations

from pycircuit import Circuit

from janus.bcc.bctrl.bctrl import build_bctrl
from janus.bcc.ooo.core import build_bcc_ooo
from janus.cube.cube import build_cube
from janus.tau.tau import build_tau
from janus.tma.tma import build_tma
from janus.tmu.noc.node import build_ring_node
from janus.tmu.noc.pipe import build_ring_pipe
from janus.tmu.sram.tilereg import build_tilereg


def build(m: Circuit, *, mem_bytes: int = (1 << 20)) -> None:
    """Janus top-level bring-up entry.

    - BCC: 4-wide OOO LinxISA core (primary executable core).
    - BCtrl: block-command queue/rename/retire path.
    - TMU: shared tile register file with ring-style interconnect.
    - PEs: TMA/CUBE/TAU engines that consume block commands and return done.
    """

    c = m.const
    bcc = build_bcc_ooo(m, mem_bytes=mem_bytes)
    clk = bcc.clk
    rst = bcc.rst

    with m.scope("top_fb"):
        tma_ready = m.out("tma_ready", clk=clk, rst=rst, width=1, init=1, en=1)
        cube_ready = m.out("cube_ready", clk=clk, rst=rst, width=1, init=1, en=1)
        tau_ready = m.out("tau_ready", clk=clk, rst=rst, width=1, init=1, en=1)
        tma_done_valid = m.out("tma_done_valid", clk=clk, rst=rst, width=1, init=0, en=1)
        tma_done_brob = m.out("tma_done_brob", clk=clk, rst=rst, width=8, init=0, en=1)
        cube_done_valid = m.out("cube_done_valid", clk=clk, rst=rst, width=1, init=0, en=1)
        cube_done_brob = m.out("cube_done_brob", clk=clk, rst=rst, width=8, init=0, en=1)
        tau_done_valid = m.out("tau_done_valid", clk=clk, rst=rst, width=1, init=0, en=1)
        tau_done_brob = m.out("tau_done_brob", clk=clk, rst=rst, width=8, init=0, en=1)

    bctrl = build_bctrl(
        m,
        clk=clk,
        rst=rst,
        cmd_valid=m.wire(bcc.block_cmd_valid),
        cmd_kind=m.wire(bcc.block_cmd_kind),
        cmd_payload=m.wire(bcc.block_cmd_payload),
        cmd_tile=m.wire(bcc.block_cmd_tile),
        cmd_tag=m.wire(bcc.block_cmd_tag),
        tma_ready=tma_ready.out(),
        cube_ready=cube_ready.out(),
        tau_ready=tau_ready.out(),
        tma_done_valid=tma_done_valid.out(),
        tma_done_brob=tma_done_brob.out(),
        cube_done_valid=cube_done_valid.out(),
        cube_done_brob=cube_done_brob.out(),
        tau_done_valid=tau_done_valid.out(),
        tau_done_brob=tau_done_brob.out(),
    )

    # JIT restriction: bitwise `~`, unary `-`, and int<<int are not supported in
    # expressions. Use a concrete 64-bit constant mask (clears bits[7:6]).
    mask_target = c(0xFFFF_FFFF_FFFF_FF3F, width=64)
    # JIT restriction: int<<int is not supported in expressions.
    tma_payload = (bctrl.dispatch_tma_payload & mask_target) | c(0, width=64)
    cube_payload = (bctrl.dispatch_cube_payload & mask_target) | c(64, width=64)
    tau_payload = (bctrl.dispatch_tau_payload & mask_target) | c(128, width=64)

    node0 = build_ring_node(
        m,
        in_valid=c(0, width=1),
        in_brob=c(0, width=8),
        in_payload=c(0, width=64),
        inject_valid=bctrl.dispatch_tma_valid,
        inject_brob=bctrl.dispatch_tma_brob,
        inject_payload=tma_payload,
        node_id=0,
        local_ready=tma_ready.out(),
    )
    pipe0 = build_ring_pipe(m, clk=clk, rst=rst, in_valid=node0.out_valid, in_brob=node0.out_brob, in_payload=node0.out_payload, name="noc_p0")
    node1 = build_ring_node(
        m,
        in_valid=pipe0.valid,
        in_brob=pipe0.brob,
        in_payload=pipe0.payload,
        inject_valid=bctrl.dispatch_cube_valid,
        inject_brob=bctrl.dispatch_cube_brob,
        inject_payload=cube_payload,
        node_id=1,
        local_ready=cube_ready.out(),
    )
    pipe1 = build_ring_pipe(m, clk=clk, rst=rst, in_valid=node1.out_valid, in_brob=node1.out_brob, in_payload=node1.out_payload, name="noc_p1")
    node2 = build_ring_node(
        m,
        in_valid=pipe1.valid,
        in_brob=pipe1.brob,
        in_payload=pipe1.payload,
        inject_valid=bctrl.dispatch_tau_valid,
        inject_brob=bctrl.dispatch_tau_brob,
        inject_payload=tau_payload,
        node_id=2,
        local_ready=tau_ready.out(),
    )
    _pipe2 = build_ring_pipe(m, clk=clk, rst=rst, in_valid=node2.out_valid, in_brob=node2.out_brob, in_payload=node2.out_payload, name="noc_p2")

    tma = build_tma(
        m,
        clk=clk,
        rst=rst,
        launch_valid=node0.local_valid,
        launch_brob=node0.local_brob,
        launch_payload=node0.local_payload,
    )
    cube = build_cube(
        m,
        clk=clk,
        rst=rst,
        launch_valid=node1.local_valid,
        launch_brob=node1.local_brob,
        launch_payload=node1.local_payload,
    )
    tau = build_tau(
        m,
        clk=clk,
        rst=rst,
        launch_valid=node2.local_valid,
        launch_brob=node2.local_brob,
        launch_payload=node2.local_payload,
    )

    tma_ready.set(tma.ready)
    cube_ready.set(cube.ready)
    tau_ready.set(tau.ready)
    tma_done_valid.set(tma.done_valid)
    tma_done_brob.set(tma.done_brob)
    cube_done_valid.set(cube.done_valid)
    cube_done_brob.set(cube.done_brob)
    tau_done_valid.set(tau.done_valid)
    tau_done_brob.set(tau.done_brob)

    tile_write_valid = tma.tile_write_valid | cube.tile_write_valid | tau.tile_write_valid
    tile_write_idx = tma.tile_write_valid.select(tma.tile_write_idx, cube.tile_write_valid.select(cube.tile_write_idx, tau.tile_write_idx))
    tile_write_data = tma.tile_write_valid.select(tma.tile_write_data, cube.tile_write_valid.select(cube.tile_write_data, tau.tile_write_data))
    tile_regs = build_tilereg(
        m,
        clk=clk,
        rst=rst,
        read_idx=m.wire(bcc.block_cmd_tile),
        write_valid=tile_write_valid,
        write_idx=tile_write_idx,
        write_data=tile_write_data,
    )

    m.output("janus_cmd_accept", bctrl.cmd_accept)
    m.output("janus_bisq_count", bctrl.bisq_count)
    m.output("janus_brob_count", bctrl.brob_count)
    m.output("janus_block_retire_valid", bctrl.retire_valid)
    m.output("janus_block_retire_tag", bctrl.retire_tag)
    m.output("janus_block_retire_tile", bctrl.retire_tile)
    m.output("janus_block_retire_pe", bctrl.retire_pe)
    m.output("janus_tile_read", tile_regs.read_data)
    m.output("janus_tma_done", tma.done_valid)
    m.output("janus_cube_done", cube.done_valid)
    m.output("janus_tau_done", tau.done_valid)


build.__pycircuit_name__ = "janus_top_pyc"
