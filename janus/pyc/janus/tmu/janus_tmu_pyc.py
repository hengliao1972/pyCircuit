from __future__ import annotations

import os
from dataclasses import dataclass

from pycircuit import Circuit, Reg, Wire
from pycircuit.hw import cat

from janus.bcc.ooo.helpers import mux_by_uindex


RING_ORDER = [0, 1, 3, 5, 7, 6, 4, 2]
NODE_COUNT = 8


def _build_cw_pref() -> list[list[int]]:
    order = RING_ORDER
    n = len(order)
    pos = {node: i for i, node in enumerate(order)}
    prefs: list[list[int]] = [[0 for _ in range(n)] for _ in range(n)]
    for s in range(n):
        for d in range(n):
            if s == d:
                prefs[s][d] = 1
                continue
            s_pos = pos[s]
            d_pos = pos[d]
            cw = (d_pos - s_pos) % n
            cc = (s_pos - d_pos) % n
            prefs[s][d] = 1 if cw <= cc else 0
    return prefs


CW_PREF = _build_cw_pref()


def _dir_cw(m: Circuit, *, src: int, dst: Wire) -> Wire:
    c = m.const
    items = [c(1 if CW_PREF[src][i] else 0, width=1) for i in range(NODE_COUNT)]
    return mux_by_uindex(m, idx=dst, items=items, default=c(1, width=1))


def _field(w: Wire, *, lsb: int, width: int) -> Wire:
    return w.slice(lsb=lsb, width=width)


def _and_all(m: Circuit, items: list[Wire]) -> Wire:
    out = m.const(1, width=1)
    for it in items:
        out = out & it
    return out


def _select_words(sel: Wire, a_words: list[Wire], b_words: list[Wire]) -> list[Wire]:
    return [sel.select(a, b) for a, b in zip(a_words, b_words)]


def _select4_words(
    sel_a: Wire,
    sel_b: Wire,
    sel_c: Wire,
    sel_d: Wire,
    wa: list[Wire],
    wb: list[Wire],
    wc: list[Wire],
    wd: list[Wire],
) -> list[Wire]:
    out: list[Wire] = []
    for a, b, c, d in zip(wa, wb, wc, wd):
        out.append(sel_a.select(a, sel_b.select(b, sel_c.select(c, d))))
    return out


@dataclass(frozen=True)
class BundleFifo:
    in_ready: Wire
    out_valid: Wire
    out_meta: Wire
    out_data: list[Wire]


def _build_bundle_fifo(
    m: Circuit,
    *,
    clk: Wire,
    rst: Wire,
    in_valid: Wire,
    in_meta: Wire,
    in_data: list[Wire],
    out_ready: Wire,
    depth: int,
    name: str,
) -> BundleFifo:
    push = m.named_wire(f"{name}__push", width=1)
    pop = m.named_wire(f"{name}__pop", width=1)

    meta_in_ready, meta_out_valid, meta_out_data = m.fifo(
        clk,
        rst,
        in_valid=push,
        in_data=in_meta,
        out_ready=pop,
        depth=depth,
    )

    data_in_ready: list[Wire] = []
    data_out_valid: list[Wire] = []
    data_out_data: list[Wire] = []

    for wi, word in enumerate(in_data):
        in_ready_w, out_valid_w, out_data_w = m.fifo(
            clk,
            rst,
            in_valid=push,
            in_data=word,
            out_ready=pop,
            depth=depth,
        )
        data_in_ready.append(in_ready_w)
        data_out_valid.append(out_valid_w)
        data_out_data.append(out_data_w)

    bundle_in_ready = _and_all(m, [meta_in_ready, *data_in_ready])
    bundle_out_valid = _and_all(m, [meta_out_valid, *data_out_valid])

    m.assign(push, in_valid & bundle_in_ready)
    m.assign(pop, out_ready & bundle_out_valid)

    return BundleFifo(in_ready=bundle_in_ready, out_valid=bundle_out_valid, out_meta=meta_out_data, out_data=data_out_data)


@dataclass(frozen=True)
class NodeIo:
    req_valid: Wire
    req_write: Wire
    req_addr: Wire
    req_tag: Wire
    req_data_words: list[Wire]
    req_ready: Wire
    resp_ready: Wire
    resp_valid: Wire
    resp_tag: Wire
    resp_data_words: list[Wire]
    resp_is_write: Wire


def build(
    m: Circuit,
    *,
    tile_bytes: int | None = None,
    tag_bits: int = 8,
    spb_depth: int = 4,
    mgb_depth: int = 4,
) -> None:
    if tile_bytes is None:
        tile_bytes = int(os.getenv("JANUS_TMU_TILE_BYTES", 1 << 20))
    if tile_bytes <= 0:
        raise ValueError("tile_bytes must be > 0")

    line_bytes = 256
    line_words = line_bytes // 8
    pipe_count = NODE_COUNT

    if tile_bytes % (pipe_count * line_bytes) != 0:
        raise ValueError("tile_bytes must be divisible by 8 * 256")

    addr_bits = (tile_bytes - 1).bit_length()
    offset_bits = (line_bytes - 1).bit_length()
    pipe_bits = (pipe_count - 1).bit_length()
    if addr_bits < offset_bits + pipe_bits:
        raise ValueError("tile_bytes too small for pipe addressing")

    index_bits = addr_bits - offset_bits - pipe_bits
    lines_per_pipe = tile_bytes // (pipe_count * line_bytes)

    c = m.const
    node_bits = pipe_bits

    clk = m.clock("clk")
    rst = m.reset("rst")

    # Meta layouts (packed into 64-bit).
    REQ_WRITE_LSB = 0
    REQ_SRC_LSB = REQ_WRITE_LSB + 1
    REQ_DST_LSB = REQ_SRC_LSB + node_bits
    REQ_TAG_LSB = REQ_DST_LSB + node_bits
    REQ_ADDR_LSB = REQ_TAG_LSB + tag_bits

    RSP_WRITE_LSB = 0
    RSP_SRC_LSB = RSP_WRITE_LSB + 1
    RSP_DST_LSB = RSP_SRC_LSB + node_bits
    RSP_TAG_LSB = RSP_DST_LSB + node_bits

    def pack_req_meta(write: Wire, src: Wire, dst: Wire, tag: Wire, addr: Wire) -> Wire:
        meta = cat(addr, tag, dst, src, write)
        return meta.zext(width=64)

    def pack_rsp_meta(write: Wire, src: Wire, dst: Wire, tag: Wire) -> Wire:
        meta = cat(tag, dst, src, write)
        return meta.zext(width=64)

    # --- Node IOs ---
    nodes: list[NodeIo] = []
    for i in range(NODE_COUNT):
        req_valid = m.input(f"n{i}_req_valid", width=1)
        req_write = m.input(f"n{i}_req_write", width=1)
        req_addr = m.input(f"n{i}_req_addr", width=addr_bits)
        req_tag = m.input(f"n{i}_req_tag", width=tag_bits)
        req_data_words = [m.input(f"n{i}_req_data_w{wi}", width=64) for wi in range(line_words)]
        resp_ready = m.input(f"n{i}_resp_ready", width=1)

        req_ready = m.named_wire(f"n{i}_req_ready", width=1)
        resp_valid = m.named_wire(f"n{i}_resp_valid", width=1)
        resp_tag = m.named_wire(f"n{i}_resp_tag", width=tag_bits)
        resp_data_words = [m.named_wire(f"n{i}_resp_data_w{wi}", width=64) for wi in range(line_words)]
        resp_is_write = m.named_wire(f"n{i}_resp_is_write", width=1)

        nodes.append(
            NodeIo(
                req_valid=req_valid,
                req_write=req_write,
                req_addr=req_addr,
                req_tag=req_tag,
                req_data_words=req_data_words,
                req_ready=req_ready,
                resp_ready=resp_ready,
                resp_valid=resp_valid,
                resp_tag=resp_tag,
                resp_data_words=resp_data_words,
                resp_is_write=resp_is_write,
            )
        )

    # --- Build SPB bundles per node (cw/cc) ---
    spb_cw: list[BundleFifo] = []
    spb_cc: list[BundleFifo] = []
    spb_cw_out_ready: list[Wire] = []
    spb_cc_out_ready: list[Wire] = []

    req_meta: list[Wire] = []
    req_words: list[list[Wire]] = []
    req_dir_cw: list[Wire] = []

    for i, node in enumerate(nodes):
        dst = node.req_addr.slice(lsb=offset_bits, width=pipe_bits)
        src = c(i, width=node_bits)
        meta = pack_req_meta(node.req_write, src, dst, node.req_tag, node.req_addr)
        req_meta.append(meta)
        words = node.req_data_words
        req_words.append(words)

        dir_cw = _dir_cw(m, src=i, dst=dst)
        req_dir_cw.append(dir_cw)

        in_valid_cw = node.req_valid & dir_cw
        in_valid_cc = node.req_valid & (~dir_cw)

        cw_ready = m.named_wire(f"spb{i}_cw_out_ready", width=1)
        cc_ready = m.named_wire(f"spb{i}_cc_out_ready", width=1)
        spb_cw_out_ready.append(cw_ready)
        spb_cc_out_ready.append(cc_ready)

        spb_cw.append(
            _build_bundle_fifo(
                m,
                clk=clk,
                rst=rst,
                in_valid=in_valid_cw,
                in_meta=meta,
                in_data=words,
                out_ready=cw_ready,
                depth=spb_depth,
                name=f"spb{i}_cw",
            )
        )
        spb_cc.append(
            _build_bundle_fifo(
                m,
                clk=clk,
                rst=rst,
                in_valid=in_valid_cc,
                in_meta=meta,
                in_data=words,
                out_ready=cc_ready,
                depth=spb_depth,
                name=f"spb{i}_cc",
            )
        )

        m.assign(node.req_ready, dir_cw.select(spb_cw[i].in_ready, spb_cc[i].in_ready))

    # --- Ring link registers (request + response, cw/cc) ---
    req_cw_link_valid: list[Reg] = []
    req_cw_link_meta: list[Reg] = []
    req_cw_link_data: list[list[Reg]] = []
    req_cc_link_valid: list[Reg] = []
    req_cc_link_meta: list[Reg] = []
    req_cc_link_data: list[list[Reg]] = []

    rsp_cw_link_valid: list[Reg] = []
    rsp_cw_link_meta: list[Reg] = []
    rsp_cw_link_data: list[list[Reg]] = []
    rsp_cc_link_valid: list[Reg] = []
    rsp_cc_link_meta: list[Reg] = []
    rsp_cc_link_data: list[list[Reg]] = []

    with m.scope("req_ring"):
        for i in range(NODE_COUNT):
            req_cw_link_valid.append(m.out(f"cw_v{i}", clk=clk, rst=rst, width=1, init=0, en=1))
            req_cw_link_meta.append(m.out(f"cw_m{i}", clk=clk, rst=rst, width=64, init=0, en=1))
            req_cw_link_data.append(
                [m.out(f"cw_d{i}_w{wi}", clk=clk, rst=rst, width=64, init=0, en=1) for wi in range(line_words)]
            )
            req_cc_link_valid.append(m.out(f"cc_v{i}", clk=clk, rst=rst, width=1, init=0, en=1))
            req_cc_link_meta.append(m.out(f"cc_m{i}", clk=clk, rst=rst, width=64, init=0, en=1))
            req_cc_link_data.append(
                [m.out(f"cc_d{i}_w{wi}", clk=clk, rst=rst, width=64, init=0, en=1) for wi in range(line_words)]
            )

    with m.scope("rsp_ring"):
        for i in range(NODE_COUNT):
            rsp_cw_link_valid.append(m.out(f"cw_v{i}", clk=clk, rst=rst, width=1, init=0, en=1))
            rsp_cw_link_meta.append(m.out(f"cw_m{i}", clk=clk, rst=rst, width=64, init=0, en=1))
            rsp_cw_link_data.append(
                [m.out(f"cw_d{i}_w{wi}", clk=clk, rst=rst, width=64, init=0, en=1) for wi in range(line_words)]
            )
            rsp_cc_link_valid.append(m.out(f"cc_v{i}", clk=clk, rst=rst, width=1, init=0, en=1))
            rsp_cc_link_meta.append(m.out(f"cc_m{i}", clk=clk, rst=rst, width=64, init=0, en=1))
            rsp_cc_link_data.append(
                [m.out(f"cc_d{i}_w{wi}", clk=clk, rst=rst, width=64, init=0, en=1) for wi in range(line_words)]
            )

    # --- Pipe request wires ---
    pipe_req_valid: list[Wire] = [c(0, width=1) for _ in range(NODE_COUNT)]
    pipe_req_meta: list[Wire] = [c(0, width=64) for _ in range(NODE_COUNT)]
    pipe_req_data: list[list[Wire]] = [[c(0, width=64) for _ in range(line_words)] for _ in range(NODE_COUNT)]

    # --- Request ring traversal + ejection to pipes ---
    for pos in range(NODE_COUNT):
        nid = RING_ORDER[pos]
        node_const = c(nid, width=node_bits)

        prev_pos = (pos - 1) % NODE_COUNT
        next_pos = (pos + 1) % NODE_COUNT

        cw_in_valid = req_cw_link_valid[prev_pos].out()
        cw_in_meta = req_cw_link_meta[prev_pos].out()
        cw_in_data = [r.out() for r in req_cw_link_data[prev_pos]]

        cc_in_valid = req_cc_link_valid[next_pos].out()
        cc_in_meta = req_cc_link_meta[next_pos].out()
        cc_in_data = [r.out() for r in req_cc_link_data[next_pos]]

        cw_in_dst = _field(cw_in_meta, lsb=REQ_DST_LSB, width=node_bits)
        cc_in_dst = _field(cc_in_meta, lsb=REQ_DST_LSB, width=node_bits)

        ring_cw_local = cw_in_valid & cw_in_dst.eq(node_const)
        ring_cc_local = cc_in_valid & cc_in_dst.eq(node_const)

        spb_cw_head_meta = spb_cw[nid].out_meta
        spb_cc_head_meta = spb_cc[nid].out_meta
        spb_cw_head_data = spb_cw[nid].out_data
        spb_cc_head_data = spb_cc[nid].out_data

        spb_cw_dst = _field(spb_cw_head_meta, lsb=REQ_DST_LSB, width=node_bits)
        spb_cc_dst = _field(spb_cc_head_meta, lsb=REQ_DST_LSB, width=node_bits)

        spb_cw_local = spb_cw[nid].out_valid & spb_cw_dst.eq(node_const)
        spb_cc_local = spb_cc[nid].out_valid & spb_cc_dst.eq(node_const)

        sel_ring_cw = ring_cw_local
        sel_ring_cc = (~sel_ring_cw) & ring_cc_local
        sel_spb_cw = (~sel_ring_cw) & (~sel_ring_cc) & spb_cw_local
        sel_spb_cc = (~sel_ring_cw) & (~sel_ring_cc) & (~sel_spb_cw) & spb_cc_local

        pipe_req_valid[nid] = sel_ring_cw | sel_ring_cc | sel_spb_cw | sel_spb_cc
        pipe_req_meta[nid] = sel_ring_cw.select(
            cw_in_meta,
            sel_ring_cc.select(cc_in_meta, sel_spb_cw.select(spb_cw_head_meta, spb_cc_head_meta)),
        )
        pipe_req_data[nid] = _select4_words(sel_ring_cw, sel_ring_cc, sel_spb_cw, sel_spb_cc, cw_in_data, cc_in_data, spb_cw_head_data, spb_cc_head_data)

        cw_forward_valid = cw_in_valid & (~sel_ring_cw)
        cw_can_inject = ~cw_forward_valid
        cw_inject_valid = spb_cw[nid].out_valid & (~spb_cw_local) & cw_can_inject
        cw_out_valid = cw_forward_valid | cw_inject_valid
        cw_out_meta = cw_forward_valid.select(cw_in_meta, spb_cw_head_meta)
        cw_out_data = _select_words(cw_forward_valid, cw_in_data, spb_cw_head_data)

        cc_forward_valid = cc_in_valid & (~sel_ring_cc)
        cc_can_inject = ~cc_forward_valid
        cc_inject_valid = spb_cc[nid].out_valid & (~spb_cc_local) & cc_can_inject
        cc_out_valid = cc_forward_valid | cc_inject_valid
        cc_out_meta = cc_forward_valid.select(cc_in_meta, spb_cc_head_meta)
        cc_out_data = _select_words(cc_forward_valid, cc_in_data, spb_cc_head_data)

        req_cw_link_valid[pos].set(cw_out_valid)
        req_cw_link_meta[pos].set(cw_out_meta)
        for wi in range(line_words):
            req_cw_link_data[pos][wi].set(cw_out_data[wi])

        req_cc_link_valid[pos].set(cc_out_valid)
        req_cc_link_meta[pos].set(cc_out_meta)
        for wi in range(line_words):
            req_cc_link_data[pos][wi].set(cc_out_data[wi])

        m.assign(spb_cw_out_ready[nid], sel_spb_cw | cw_inject_valid)
        m.assign(spb_cc_out_ready[nid], sel_spb_cc | cc_inject_valid)

    # --- Pipe stage regs ---
    pipe_stage_valid: list[Reg] = []
    pipe_stage_meta: list[Reg] = []
    pipe_stage_data: list[list[Reg]] = []

    for p in range(pipe_count):
        with m.scope(f"pipe{p}_stage"):
            pipe_stage_valid.append(m.out("v", clk=clk, rst=rst, width=1, init=0, en=1))
            pipe_stage_meta.append(m.out("m", clk=clk, rst=rst, width=64, init=0, en=1))
            pipe_stage_data.append(
                [m.out(f"d_w{wi}", clk=clk, rst=rst, width=64, init=0, en=1) for wi in range(line_words)]
            )

        pipe_stage_valid[p].set(pipe_req_valid[p])
        pipe_stage_meta[p].set(pipe_req_meta[p])
        for wi in range(line_words):
            pipe_stage_data[p][wi].set(pipe_req_data[p][wi])

    # --- Response inject bundles (per pipe, cw/cc) ---
    rsp_cw: list[BundleFifo] = []
    rsp_cc: list[BundleFifo] = []
    rsp_cw_out_ready: list[Wire] = []
    rsp_cc_out_ready: list[Wire] = []

    for p in range(pipe_count):
        st_valid = pipe_stage_valid[p].out()
        st_meta = pipe_stage_meta[p].out()
        st_data_words = [r.out() for r in pipe_stage_data[p]]

        st_write = _field(st_meta, lsb=REQ_WRITE_LSB, width=1)
        st_src = _field(st_meta, lsb=REQ_SRC_LSB, width=node_bits)
        st_tag = _field(st_meta, lsb=REQ_TAG_LSB, width=tag_bits)
        st_addr = _field(st_meta, lsb=REQ_ADDR_LSB, width=addr_bits)

        line_idx = st_addr.slice(lsb=offset_bits + pipe_bits, width=index_bits)
        byte_addr = cat(line_idx, c(0, width=3))
        depth_bytes = lines_per_pipe * 8

        read_words: list[Wire] = []
        wvalid = st_valid & st_write
        wstrb = c(0xFF, width=8)

        for wi in range(line_words):
            rdata = m.byte_mem(
                clk=clk,
                rst=rst,
                raddr=byte_addr,
                wvalid=wvalid,
                waddr=byte_addr,
                wdata=st_data_words[wi],
                wstrb=wstrb,
                depth=depth_bytes,
                name=f"tmu_p{p}_w{wi}",
            )
            read_words.append(rdata)

        rsp_meta = pack_rsp_meta(st_write, c(p, width=node_bits), st_src, st_tag)
        rsp_words = [st_write.select(st_data_words[wi], read_words[wi]) for wi in range(line_words)]

        rsp_dir = _dir_cw(m, src=p, dst=st_src)
        in_valid_cw = st_valid & rsp_dir
        in_valid_cc = st_valid & (~rsp_dir)

        cw_ready = m.named_wire(f"rsp{p}_cw_out_ready", width=1)
        cc_ready = m.named_wire(f"rsp{p}_cc_out_ready", width=1)
        rsp_cw_out_ready.append(cw_ready)
        rsp_cc_out_ready.append(cc_ready)

        rsp_cw.append(
            _build_bundle_fifo(
                m,
                clk=clk,
                rst=rst,
                in_valid=in_valid_cw,
                in_meta=rsp_meta,
                in_data=rsp_words,
                out_ready=cw_ready,
                depth=spb_depth,
                name=f"rsp{p}_cw",
            )
        )
        rsp_cc.append(
            _build_bundle_fifo(
                m,
                clk=clk,
                rst=rst,
                in_valid=in_valid_cc,
                in_meta=rsp_meta,
                in_data=rsp_words,
                out_ready=cc_ready,
                depth=spb_depth,
                name=f"rsp{p}_cc",
            )
        )

    # --- Response ring traversal + MGB buffers ---
    for pos in range(NODE_COUNT):
        nid = RING_ORDER[pos]
        node_const = c(nid, width=node_bits)

        prev_pos = (pos - 1) % NODE_COUNT
        next_pos = (pos + 1) % NODE_COUNT

        cw_in_valid = rsp_cw_link_valid[prev_pos].out()
        cw_in_meta = rsp_cw_link_meta[prev_pos].out()
        cw_in_data = [r.out() for r in rsp_cw_link_data[prev_pos]]

        cc_in_valid = rsp_cc_link_valid[next_pos].out()
        cc_in_meta = rsp_cc_link_meta[next_pos].out()
        cc_in_data = [r.out() for r in rsp_cc_link_data[next_pos]]

        cw_in_dst = _field(cw_in_meta, lsb=RSP_DST_LSB, width=node_bits)
        cc_in_dst = _field(cc_in_meta, lsb=RSP_DST_LSB, width=node_bits)

        ring_cw_local = cw_in_valid & cw_in_dst.eq(node_const)
        ring_cc_local = cc_in_valid & cc_in_dst.eq(node_const)

        rsp_cw_head_meta = rsp_cw[nid].out_meta
        rsp_cc_head_meta = rsp_cc[nid].out_meta
        rsp_cw_head_data = rsp_cw[nid].out_data
        rsp_cc_head_data = rsp_cc[nid].out_data

        rsp_cw_dst = _field(rsp_cw_head_meta, lsb=RSP_DST_LSB, width=node_bits)
        rsp_cc_dst = _field(rsp_cc_head_meta, lsb=RSP_DST_LSB, width=node_bits)

        rsp_cw_local = rsp_cw[nid].out_valid & rsp_cw_dst.eq(node_const)
        rsp_cc_local = rsp_cc[nid].out_valid & rsp_cc_dst.eq(node_const)

        cw_local_valid = ring_cw_local | rsp_cw_local
        cc_local_valid = ring_cc_local | rsp_cc_local
        cw_local_meta = ring_cw_local.select(cw_in_meta, rsp_cw_head_meta)
        cc_local_meta = ring_cc_local.select(cc_in_meta, rsp_cc_head_meta)
        cw_local_data = _select_words(ring_cw_local, cw_in_data, rsp_cw_head_data)
        cc_local_data = _select_words(ring_cc_local, cc_in_data, rsp_cc_head_data)

        # MGB buffers.
        mgb_cw_ready = m.named_wire(f"mgb{nid}_cw_out_ready", width=1)
        mgb_cc_ready = m.named_wire(f"mgb{nid}_cc_out_ready", width=1)

        mgb_cw = _build_bundle_fifo(
            m,
            clk=clk,
            rst=rst,
            in_valid=cw_local_valid,
            in_meta=cw_local_meta,
            in_data=cw_local_data,
            out_ready=mgb_cw_ready,
            depth=mgb_depth,
            name=f"mgb{nid}_cw",
        )
        mgb_cc = _build_bundle_fifo(
            m,
            clk=clk,
            rst=rst,
            in_valid=cc_local_valid,
            in_meta=cc_local_meta,
            in_data=cc_local_data,
            out_ready=mgb_cc_ready,
            depth=mgb_depth,
            name=f"mgb{nid}_cc",
        )

        rr = m.out(f"mgb{nid}_rr", clk=clk, rst=rst, width=1, init=0, en=1)

        any_cw = mgb_cw.out_valid
        any_cc = mgb_cc.out_valid
        both = any_cw & any_cc
        pick_cw = (any_cw & (~any_cc)) | (both & (~rr.out()))
        pick_cc = (any_cc & (~any_cw)) | (both & rr.out())

        resp_ready = nodes[nid].resp_ready
        resp_fire = (pick_cw | pick_cc) & resp_ready

        m.assign(mgb_cw_ready, pick_cw & resp_ready)
        m.assign(mgb_cc_ready, pick_cc & resp_ready)

        rr_next = rr.out()
        rr_next = resp_fire.select(~rr_next, rr_next)
        rr.set(rr_next)

        resp_meta = pick_cw.select(mgb_cw.out_meta, mgb_cc.out_meta)
        resp_words = _select_words(pick_cw, mgb_cw.out_data, mgb_cc.out_data)

        m.assign(nodes[nid].resp_valid, resp_fire)
        m.assign(nodes[nid].resp_tag, _field(resp_meta, lsb=RSP_TAG_LSB, width=tag_bits))
        m.assign(nodes[nid].resp_is_write, _field(resp_meta, lsb=RSP_WRITE_LSB, width=1))
        for wi in range(line_words):
            m.assign(nodes[nid].resp_data_words[wi], resp_words[wi])

        # Forward or inject on response cw lane.
        cw_forward_valid = cw_in_valid & (~ring_cw_local)
        cc_forward_valid = cc_in_valid & (~ring_cc_local)

        cw_can_inject = ~cw_forward_valid
        cc_can_inject = ~cc_forward_valid

        cw_inject_valid = rsp_cw[nid].out_valid & (~rsp_cw_local) & cw_can_inject
        cc_inject_valid = rsp_cc[nid].out_valid & (~rsp_cc_local) & cc_can_inject

        cw_out_valid = cw_forward_valid | cw_inject_valid
        cc_out_valid = cc_forward_valid | cc_inject_valid

        cw_out_meta = cw_forward_valid.select(cw_in_meta, rsp_cw_head_meta)
        cc_out_meta = cc_forward_valid.select(cc_in_meta, rsp_cc_head_meta)
        cw_out_data = _select_words(cw_forward_valid, cw_in_data, rsp_cw_head_data)
        cc_out_data = _select_words(cc_forward_valid, cc_in_data, rsp_cc_head_data)

        rsp_cw_link_valid[pos].set(cw_out_valid)
        rsp_cw_link_meta[pos].set(cw_out_meta)
        for wi in range(line_words):
            rsp_cw_link_data[pos][wi].set(cw_out_data[wi])

        rsp_cc_link_valid[pos].set(cc_out_valid)
        rsp_cc_link_meta[pos].set(cc_out_meta)
        for wi in range(line_words):
            rsp_cc_link_data[pos][wi].set(cc_out_data[wi])

        rsp_cw_local_pop = rsp_cw_local & (~ring_cw_local) & mgb_cw.in_ready
        rsp_cc_local_pop = rsp_cc_local & (~ring_cc_local) & mgb_cc.in_ready
        m.assign(rsp_cw_out_ready[nid], rsp_cw_local_pop | cw_inject_valid)
        m.assign(rsp_cc_out_ready[nid], rsp_cc_local_pop | cc_inject_valid)

    # --- Debug ring metadata outputs (for visualization) ---
    for pos in range(NODE_COUNT):
        nid = RING_ORDER[pos]
        req_meta = req_cw_link_meta[pos].out().slice(lsb=0, width=REQ_ADDR_LSB + addr_bits)
        req_meta_cc = req_cc_link_meta[pos].out().slice(lsb=0, width=REQ_ADDR_LSB + addr_bits)
        rsp_meta = rsp_cw_link_meta[pos].out().slice(lsb=0, width=RSP_TAG_LSB + tag_bits)
        rsp_meta_cc = rsp_cc_link_meta[pos].out().slice(lsb=0, width=RSP_TAG_LSB + tag_bits)
        m.output(f"dbg_req_cw_v{nid}", req_cw_link_valid[pos].out())
        m.output(f"dbg_req_cc_v{nid}", req_cc_link_valid[pos].out())
        m.output(f"dbg_req_cw_meta{nid}", req_meta)
        m.output(f"dbg_req_cc_meta{nid}", req_meta_cc)
        m.output(f"dbg_rsp_cw_v{nid}", rsp_cw_link_valid[pos].out())
        m.output(f"dbg_rsp_cc_v{nid}", rsp_cc_link_valid[pos].out())
        m.output(f"dbg_rsp_cw_meta{nid}", rsp_meta)
        m.output(f"dbg_rsp_cc_meta{nid}", rsp_meta_cc)

    for i, node in enumerate(nodes):
        m.output(f"n{i}_req_ready", node.req_ready)
        m.output(f"n{i}_resp_valid", node.resp_valid)
        m.output(f"n{i}_resp_tag", node.resp_tag)
        for wi in range(line_words):
            m.output(f"n{i}_resp_data_w{wi}", node.resp_data_words[wi])
        m.output(f"n{i}_resp_is_write", node.resp_is_write)


build.__pycircuit_name__ = "janus_tmu_pyc"
