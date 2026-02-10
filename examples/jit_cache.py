from __future__ import annotations

from pycircuit import Circuit, ClockDomain, Reg, Wire


def clog2(x: int) -> int:
    """Ceil(log2(x)) for x>0, with a minimum width of 1."""
    if x <= 0:
        raise ValueError("clog2 expects x > 0")
    return max(1, (int(x) - 1).bit_length())


def bytes_of(bit_width: int) -> int:
    if bit_width <= 0 or (bit_width % 8) != 0:
        raise ValueError("bit_width must be a positive multiple of 8")
    return int(bit_width) // 8


def tag_width(addr_width: int, off_bits: int, set_bits: int) -> int:
    tag = int(addr_width) - int(off_bits) - int(set_bits)
    if tag <= 0:
        raise ValueError("invalid address split")
    return tag


def _mk_2d_regs(
    m: Circuit,
    name: str,
    dom: ClockDomain,
    *,
    sets: int,
    ways: int,
    width: int,
    init: int = 0,
) -> list[list[Reg]]:
    out: list[list[Reg]] = []
    with m.scope(name):
        for s in range(int(sets)):
            row: list[Reg] = []
            with m.scope(f"s{s}"):
                for w in range(int(ways)):
                    row.append(m.out(f"w{w}", domain=dom, width=width, init=init))
            out.append(row)
    return out


def _mk_1d_regs(
    m: Circuit,
    name: str,
    dom: ClockDomain,
    *,
    n: int,
    width: int,
    init: int = 0,
) -> list[Reg]:
    out: list[Reg] = []
    with m.scope(name):
        for i in range(int(n)):
            out.append(m.out(f"s{i}", domain=dom, width=width, init=init))
    return out


def _mux_by_index(m: Circuit, idx: Wire, options: list[Wire], *, default: Wire) -> Wire:
    if not options:
        raise ValueError("mux_by_index requires a non-empty options list")
    out = default
    for i, v in enumerate(options):
        out = idx.eq(i).select(v, out)
    return out


def _select_set_way(set_idx: Wire, table: list[list[Reg]], *, way: int) -> Wire:
    opts = [table[s][int(way)].out() for s in range(len(table))]
    return _mux_by_index(opts[0].m, set_idx, opts, default=opts[0].m.const(0, width=opts[0].width))


def _cache_lookup(
    set_idx: Wire,
    tag: Wire,
    *,
    valids: list[list[Reg]],
    tags: list[list[Reg]],
    datas: list[list[Reg]],
) -> tuple[Wire, Wire]:
    m = set_idx.m
    ways = len(valids[0])

    hit = m.const(0, width=1)
    rdata = m.const(0, width=datas[0][0].width)

    for w in range(ways):
        v = _select_set_way(set_idx, valids, way=w)
        t = _select_set_way(set_idx, tags, way=w)
        d = _select_set_way(set_idx, datas, way=w)

        hw = v & t.eq(tag)
        hit = hit | hw
        rdata = hw.select(d, rdata)

    return hit, rdata


def _cache_fill_on_miss(
    set_idx: Wire,
    repl_way: Wire,
    *,
    miss: Wire,
    tag: Wire,
    fill_data: Wire,
    valids: list[list[Reg]],
    tags: list[list[Reg]],
    datas: list[list[Reg]],
    rr_ptrs: list[Reg],
) -> None:
    m = set_idx.m
    sets = len(valids)
    ways = len(valids[0])

    one_i1 = m.const(1, width=1)
    zero_way = m.const(0, width=repl_way.width)

    for s in range(sets):
        set_sel = set_idx.eq(s)
        do_set = miss & set_sel

        # Round-robin pointer update for this set.
        rr = rr_ptrs[s]
        rr_inc = rr + 1
        if ways > 1:
            last = rr.eq(ways - 1)
            rr_next = last.select(zero_way, rr_inc)
        else:
            rr_next = zero_way
        rr.set(rr_next, when=do_set)

        # Fill the selected way (chosen by repl_way).
        for w in range(ways):
            way_sel = repl_way.eq(w) if ways > 1 else one_i1
            do = do_set & way_sel
            valids[s][w].set(1, when=do)
            tags[s][w].set(tag, when=do)
            datas[s][w].set(fill_data, when=do)


def _qs(regs: list[Reg]) -> list[Wire]:
    return [r.out() for r in regs]


def build(m: Circuit, SETS: int = 8, WAYS: int = 2) -> None:
    """A tiny set-associative cache (sets/ways are JIT-time params).

    - Request in via a ready/valid queue.
    - Response out via a ready/valid queue.
    - Miss fill from a byte-addressed backing memory (0-cycle read, prototype).
    """
    dom = m.domain("sys")

    ADDR_W = 32
    DATA_W = 32
    DATA_BYTES = bytes_of(DATA_W)

    set_bits = clog2(SETS)
    off_bits = clog2(DATA_BYTES)
    tag_bits = tag_width(ADDR_W, off_bits, set_bits)

    req_valid = m.input("req_valid", width=1)
    req_addr = m.input("req_addr", width=ADDR_W)
    rsp_ready = m.input("rsp_ready", width=1)

    with m.scope("cache"):
        # Backing memory (byte addressed).
        wvalid0 = m.const(0, width=1)
        waddr0 = m.const(0, width=ADDR_W)
        wdata0 = m.const(0, width=DATA_W)
        wstrb0 = m.const(0, width=DATA_BYTES)

        # Request/response queues (event-ish programming).
        req_q = m.queue("req_q", domain=dom, width=ADDR_W, depth=2)
        req_q.push(req_addr, when=req_valid)

        rsp_q = m.queue("rsp_q", domain=dom, width=1 + DATA_W, depth=2)
        rsp_pop = rsp_q.pop(when=rsp_ready)
        rsp_hit = rsp_pop.data[DATA_W]
        rsp_rdata = rsp_pop.data[0:DATA_W]

        # Cache state (valid/tag/data per set/way + per-set RR pointer).
        valids = _mk_2d_regs(m, "valid", dom, sets=SETS, ways=WAYS, width=1, init=0)
        tags = _mk_2d_regs(m, "tag", dom, sets=SETS, ways=WAYS, width=tag_bits, init=0)
        datas = _mk_2d_regs(m, "data", dom, sets=SETS, ways=WAYS, width=DATA_W, init=0)
        rr_ptrs = _mk_1d_regs(m, "rr", dom, n=SETS, width=clog2(WAYS), init=0)

        # Pop a request only when we can also enqueue a response.
        req_pop = req_q.pop(when=rsp_q.in_ready)
        req_fire = req_pop.fire

        addr = req_pop.data
        set_idx = addr[off_bits : off_bits + set_bits]
        tag = addr[off_bits + set_bits : ADDR_W]

        # Lookup.
        lookup = _cache_lookup(set_idx, tag, valids=valids, tags=tags, datas=datas)
        hit = lookup[0]
        hit_data = lookup[1]

        # Miss fill (read from backing mem, then write selected way).
        mem_rdata = m.byte_mem(
            dom.clk,
            dom.rst,
            raddr=addr,
            wvalid=wvalid0,
            waddr=waddr0,
            wdata=wdata0,
            wstrb=wstrb0,
            depth=4096,
            name="main_mem",
        )

        miss = req_fire & ~hit
        repl_way = _mux_by_index(m, set_idx, _qs(rr_ptrs), default=m.const(0, width=rr_ptrs[0].width))
        _cache_fill_on_miss(
            set_idx,
            repl_way,
            miss=miss,
            tag=tag,
            fill_data=mem_rdata,
            valids=valids,
            tags=tags,
            datas=datas,
            rr_ptrs=rr_ptrs,
        )

        rdata = hit.select(hit_data, mem_rdata)
        rsp_pkt = m.cat(hit, rdata)
        rsp_q.push(rsp_pkt, when=req_fire)

    m.output("req_ready", req_q.in_ready)
    m.output("rsp_valid", rsp_pop.valid)
    m.output("rsp_hit", rsp_hit)
    m.output("rsp_rdata", rsp_rdata)
