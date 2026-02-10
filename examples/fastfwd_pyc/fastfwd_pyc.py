from __future__ import annotations

from dataclasses import dataclass

from pycircuit import Circuit, Queue, Reg, Wire, cat


@dataclass(frozen=True)
class _Pipe:
    valid: list[Reg]
    seq: list[Reg]


@dataclass(frozen=True)
class _Rob:
    valid: list[Reg]
    data: list[Reg]


@dataclass(frozen=True)
class _Hist:
    valid: list[Reg]
    seq: list[Reg]
    data: list[Reg]


def _rob_update(
    rob: _Rob,
    *,
    exp_seq: Wire,
    commit_pop: Wire,
    ins_fire: Wire,
    ins_seq: Wire,
    ins_data: Wire,
    depth: int,
) -> None:
    """Update per-lane ROB (insert by seq delta, then optional pop/shift by 1)."""
    m = exp_seq.m  # type: ignore[assignment]
    if depth <= 0:
        raise ValueError("ROB depth must be > 0")
    if len(rob.valid) != depth or len(rob.data) != depth:
        raise ValueError("ROB depth mismatch")

    delta = (ins_seq - exp_seq) >> 2

    v_ins: list[Wire] = []
    d_ins: list[Wire] = []
    for i in range(depth):
        hit = ins_fire & delta.eq(i)
        v_ins.append(hit.select(m.const(1, width=1), rob.valid[i].out()))
        d_ins.append(hit.select(ins_data, rob.data[i].out()))

    for i in range(depth):
        if i + 1 < depth:
            v_next = commit_pop.select(v_ins[i + 1], v_ins[i])
            d_next = commit_pop.select(d_ins[i + 1], d_ins[i])
        else:
            v_next = commit_pop.select(m.const(0, width=1), v_ins[i])
            d_next = commit_pop.select(m.const(0, width=ins_data.width), d_ins[i])
        rob.valid[i].set(v_next)
        rob.data[i].set(d_next)


def _hist_shift_insert(hist: _Hist, *, k: Wire, seqs: list[Wire], datas: list[Wire]) -> None:
    """Shift history down by k (0..4) and insert up to 4 newest entries (seqs/datas)."""
    m = hist.valid[0].q.m  # type: ignore[assignment]
    depth = len(hist.valid)
    if depth != len(hist.seq) or depth != len(hist.data):
        raise ValueError("hist size mismatch")
    if depth < 8:
        raise ValueError("hist depth must be >= 8 (prototype)")
    if len(seqs) != 4 or len(datas) != 4:
        raise ValueError("expected exactly 4 commit slots")

    # k is i3 (0..4). Build per-index muxes for next state.
    for i in range(depth):
        v0 = hist.valid[i].out()
        s0 = hist.seq[i].out()
        d0 = hist.data[i].out()

        # Defaults: no shift (k=0).
        v_next = v0
        s_next = s0
        d_next = d0

        # k==1: [new3, old0, old1, ...]
        if i == 0:
            v_k1 = m.const(1, width=1)
            s_k1 = seqs[0]
            d_k1 = datas[0]
        else:
            v_k1 = hist.valid[i - 1].out()
            s_k1 = hist.seq[i - 1].out()
            d_k1 = hist.data[i - 1].out()

        # k==2: [new3, new2, old0, old1, ...]
        if i == 0:
            v_k2 = m.const(1, width=1)
            s_k2 = seqs[1]
            d_k2 = datas[1]
        elif i == 1:
            v_k2 = m.const(1, width=1)
            s_k2 = seqs[0]
            d_k2 = datas[0]
        else:
            v_k2 = hist.valid[i - 2].out()
            s_k2 = hist.seq[i - 2].out()
            d_k2 = hist.data[i - 2].out()

        # k==3: [new3, new2, new1, old0, ...]
        if i == 0:
            v_k3 = m.const(1, width=1)
            s_k3 = seqs[2]
            d_k3 = datas[2]
        elif i == 1:
            v_k3 = m.const(1, width=1)
            s_k3 = seqs[1]
            d_k3 = datas[1]
        elif i == 2:
            v_k3 = m.const(1, width=1)
            s_k3 = seqs[0]
            d_k3 = datas[0]
        else:
            v_k3 = hist.valid[i - 3].out()
            s_k3 = hist.seq[i - 3].out()
            d_k3 = hist.data[i - 3].out()

        # k==4: [new3, new2, new1, new0, old0, ...]
        if i == 0:
            v_k4 = m.const(1, width=1)
            s_k4 = seqs[3]
            d_k4 = datas[3]
        elif i == 1:
            v_k4 = m.const(1, width=1)
            s_k4 = seqs[2]
            d_k4 = datas[2]
        elif i == 2:
            v_k4 = m.const(1, width=1)
            s_k4 = seqs[1]
            d_k4 = datas[1]
        elif i == 3:
            v_k4 = m.const(1, width=1)
            s_k4 = seqs[0]
            d_k4 = datas[0]
        else:
            v_k4 = hist.valid[i - 4].out()
            s_k4 = hist.seq[i - 4].out()
            d_k4 = hist.data[i - 4].out()

        is1 = k.eq(1)
        is2 = k.eq(2)
        is3 = k.eq(3)
        is4 = k.eq(4)

        v_next = is4.select(v_k4, is3.select(v_k3, is2.select(v_k2, is1.select(v_k1, v_next))))
        s_next = is4.select(s_k4, is3.select(s_k3, is2.select(s_k2, is1.select(s_k1, s_next))))
        d_next = is4.select(d_k4, is3.select(d_k3, is2.select(d_k2, is1.select(d_k1, d_next))))

        hist.valid[i].set(v_next)
        hist.seq[i].set(s_next)
        hist.data[i].set(d_next)


def _build_fastfwd(
    m: Circuit,
    # Total number of Forwarding Engines (FE). Must be a multiple of 4 (one pool per lane).
    # If set, it overrides ENG_PER_LANE.
    N_FE: int | None = None,
    ENG_PER_LANE: int = 1,
    LANE_Q_DEPTH: int = 16,
    ENG_Q_DEPTH: int = 4,
    ROB_DEPTH: int = 16,
    SEQ_W: int = 16,
    HIST_DEPTH: int = 8,
    STASH_WIN: int = 6,
    BKPR_SLACK: int = 1,
) -> None:
    # ---- parameters (JIT-time) ----
    if N_FE is not None:
        total_eng = int(N_FE)
        if total_eng <= 0:
            raise ValueError("N_FE must be > 0")
        if total_eng > 32:
            raise ValueError("N_FE must be <= 32")
        if (total_eng % 4) != 0:
            raise ValueError("N_FE must be a multiple of 4 (one engine pool per lane)")
        eng_per_lane = total_eng // 4
    else:
        eng_per_lane = int(ENG_PER_LANE)
        if eng_per_lane <= 0:
            raise ValueError("ENG_PER_LANE must be > 0")
        total_eng = 4 * eng_per_lane
        if total_eng > 32:
            raise ValueError("total engines must be <= 32")

    lane_q_depth = int(LANE_Q_DEPTH)
    eng_q_depth = int(ENG_Q_DEPTH)
    rob_depth = int(ROB_DEPTH)
    seq_w = int(SEQ_W)
    hist_depth = int(HIST_DEPTH)
    stash_win = int(STASH_WIN)
    bkpr_slack = int(BKPR_SLACK)

    if lane_q_depth <= 0 or eng_q_depth <= 0 or rob_depth <= 0:
        raise ValueError("queue/rob depths must be > 0")
    if seq_w <= 1:
        raise ValueError("SEQ_W must be > 1")
    if hist_depth < 8:
        raise ValueError("HIST_DEPTH must be >= 8 (dependency window <= 7)")
    if stash_win < 0:
        raise ValueError("STASH_WIN must be >= 0")
    if bkpr_slack <= 0:
        raise ValueError("BKPR_SLACK must be > 0")
    if bkpr_slack > lane_q_depth:
        raise ValueError("BKPR_SLACK must be <= LANE_Q_DEPTH")

    bundle_w = seq_w + 128 + 5
    comp_w = seq_w + 128
    pipe_depth = 5

    # ---- ports ----
    clk = m.clock("clk")
    rst = m.reset("rst")

    pkt_in_vld = [m.input(f"lane{i}_pkt_in_vld", width=1) for i in range(4)]
    pkt_in_data = [m.input(f"lane{i}_pkt_in_data", width=128) for i in range(4)]
    pkt_in_ctrl = [m.input(f"lane{i}_pkt_in_ctrl", width=5) for i in range(4)]

    # Registered outputs (per spec).
    bkpr_r = m.out("pkt_in_bkpr", clk=clk, rst=rst, width=1, init=0)
    out_vld_r = [m.out(f"lane{i}_pkt_out_vld", clk=clk, rst=rst, width=1, init=0) for i in range(4)]
    out_data_r = [m.out(f"lane{i}_pkt_out_data", clk=clk, rst=rst, width=128, init=0) for i in range(4)]

    # Forwarding Engine interface (per-engine scalar ports).
    fwded_vld = [m.input(f"fwded{e}_pkt_data_vld", width=1) for e in range(total_eng)]
    fwded_data = [m.input(f"fwded{e}_pkt_data", width=128) for e in range(total_eng)]

    # ---- global regs ----
    with m.scope("TIME"):
        cycle = m.out("cycle", clk=clk, rst=rst, width=16, init=0)
        cycle.set(cycle.out() + 1)
        seq_alloc = m.out("seq_alloc", clk=clk, rst=rst, width=seq_w, init=0)

    commit_lane = m.out("commit_lane", clk=clk, rst=rst, width=2, init=0)

    # Expected seq per output lane (seq%4==lane).
    exp_seq = [m.out(f"lane{i}__exp_seq", clk=clk, rst=rst, width=seq_w, init=i) for i in range(4)]

    # ---- per-lane issue queues ----
    issue_q: list[Queue] = []
    for lane in range(4):
        issue_q.append(m.queue(f"lane{lane}__issue_q", clk=clk, rst=rst, width=bundle_w, depth=lane_q_depth))

    # ---- per-lane issue stash (bypass window) ----
    stash_v: list[list[Reg]] = []
    stash_b: list[list[Reg]] = []
    for lane in range(4):
        stash_v.append(
            [m.out(f"lane{lane}__stash_v{i}", clk=clk, rst=rst, width=1, init=0) for i in range(stash_win)]
        )
        stash_b.append(
            [m.out(f"lane{lane}__stash_b{i}", clk=clk, rst=rst, width=bundle_w, init=0) for i in range(stash_win)]
        )

    # ---- per-engine pipelines + completion queues ----
    pipes: list[_Pipe] = []
    comp_q: list[Queue] = []
    for e in range(total_eng):
        pv = [m.out(f"eng{e}__pipe_v{i}", clk=clk, rst=rst, width=1, init=0) for i in range(pipe_depth)]
        ps = [m.out(f"eng{e}__pipe_seq{i}", clk=clk, rst=rst, width=seq_w, init=0) for i in range(pipe_depth)]
        pipes.append(_Pipe(valid=pv, seq=ps))
        comp_q.append(m.queue(f"eng{e}__comp_q", clk=clk, rst=rst, width=comp_w, depth=eng_q_depth))

    # ---- per-lane ROBs ----
    robs: list[_Rob] = []
    for lane in range(4):
        rv = [m.out(f"lane{lane}__rob_v{i}", clk=clk, rst=rst, width=1, init=0) for i in range(rob_depth)]
        rd = [m.out(f"lane{lane}__rob_d{i}", clk=clk, rst=rst, width=128, init=0) for i in range(rob_depth)]
        robs.append(_Rob(valid=rv, data=rd))

    # ---- dependency history (global shift register) ----
    hist = _Hist(
        valid=[m.out(f"hist_v{i}", clk=clk, rst=rst, width=1, init=0) for i in range(hist_depth)],
        seq=[m.out(f"hist_seq{i}", clk=clk, rst=rst, width=seq_w, init=0) for i in range(hist_depth)],
        data=[m.out(f"hist_d{i}", clk=clk, rst=rst, width=128, init=0) for i in range(hist_depth)],
    )

    # ---- input accept (PKTIN -> issue queues) ----
    with m.scope("IN"):
        bkpr = bkpr_r.out()
        accept = ~bkpr

        eff_v = [pkt_in_vld[i] & accept for i in range(4)]
        inc = [eff_v[i].select(m.const(1, width=seq_w), m.const(0, width=seq_w)) for i in range(4)]

        base = seq_alloc.out()
        seq_lane = [base, base + inc[0], base + inc[0] + inc[1], base + inc[0] + inc[1] + inc[2]]
        total_inc = inc[0] + inc[1] + inc[2] + inc[3]
        seq_alloc.set(base + total_inc)

        # Map each accepted packet into its output-lane issue queue by seq%4.
        push_v = [m.const(0, width=1) for _ in range(4)]
        push_d = [m.const(0, width=bundle_w) for _ in range(4)]

        for i in range(4):
            seq_i = seq_lane[i]
            lane_i = seq_i[0:2]  # seq%4
            bundle_i = cat(seq_i, pkt_in_data[i], pkt_in_ctrl[i])
            for lane in range(4):
                hit = eff_v[i] & lane_i.eq(lane)
                push_v[lane] = push_v[lane] | hit
                push_d[lane] = hit.select(bundle_i, push_d[lane])

        push_fire = []
        for lane in range(4):
            push_fire.append(issue_q[lane].push(push_d[lane], when=push_v[lane]))

        # Conservative BKPR policy (registered):
        # Assert when any issue queue is "nearly full" after this cycle's push/pop.
        #
        # We approximate "nearly full" by tracking a shadow count for each issue
        # queue and asserting backpressure when count >= DEPTH-2.
        # (Leaves slack for the registered BKPR latency.)
        shadow_cnt = [m.out(f"lane{lane}__iq_cnt", clk=clk, rst=rst, width=16, init=0) for lane in range(4)]

    # ---- dispatch (issue queues -> FE inputs) ----
    with m.scope("DISPATCH"):
        # Default FE outputs.
        fwd_vld = [m.const(0, width=1) for _ in range(total_eng)]
        fwd_data = [m.const(0, width=128) for _ in range(total_eng)]
        fwd_lat = [m.const(0, width=2) for _ in range(total_eng)]
        fwd_dp_vld = [m.const(0, width=1) for _ in range(total_eng)]
        fwd_dp_data = [m.const(0, width=128) for _ in range(total_eng)]

        # Per-lane dispatch signals.
        lane_pop = [m.const(0, width=1) for _ in range(4)]

        # Dependency lookup helpers (FEOUT bypass + completion queues + ROBs + history).
        def dep_lookup(dep_seq: Wire) -> tuple[Wire, Wire]:
            found_fe = m.const(0, width=1)
            data_fe = m.const(0, width=128)
            for e in range(total_eng):
                match = pipes[e].valid[0].out() & fwded_vld[e] & pipes[e].seq[0].out().eq(dep_seq)
                found_fe = found_fe | match
                data_fe = match.select(fwded_data[e], data_fe)

            found_cq = m.const(0, width=1)
            data_cq = m.const(0, width=128)
            for e in range(total_eng):
                vb = comp_q[e].out_valid
                db = comp_q[e].out_data
                seq_c = db[128 : 128 + seq_w]
                match = vb & seq_c.eq(dep_seq)
                found_cq = found_cq | match
                data_cq = match.select(db[0:128], data_cq)

            found_h = m.const(0, width=1)
            data_h = m.const(0, width=128)
            for i in range(hist_depth):
                match = hist.valid[i].out() & (hist.seq[i].out().eq(dep_seq))
                found_h = found_h | match
                data_h = match.select(hist.data[i].out(), data_h)

            dep_lane = dep_seq[0:2]
            found_r = m.const(0, width=1)
            data_r = m.const(0, width=128)
            for lane in range(4):
                is_lane = dep_lane.eq(lane)
                delta = (dep_seq - exp_seq[lane].out()) >> 2
                for s in range(rob_depth):
                    match = is_lane & robs[lane].valid[s].out() & delta.eq(s)
                    found_r = found_r | match
                    data_r = match.select(robs[lane].data[s].out(), data_r)

            found = found_fe | found_cq | found_r | found_h
            data = found_fe.select(data_fe, found_cq.select(data_cq, found_r.select(data_r, data_h)))
            return found, data

        # Compute dispatch for each lane independently (one issue per lane per cycle).
        dispatch_fire_eng = [m.const(0, width=1) for _ in range(total_eng)]
        dispatch_seq_eng = [m.const(0, width=seq_w) for _ in range(total_eng)]

        for lane in range(4):
            eng_base = lane * eng_per_lane

            c0 = m.const(0, width=1)
            c1 = m.const(1, width=1)

            def eng_free(e: int, lat: Wire) -> Wire:
                """True when engine `e` can accept a dispatch with `lat` this cycle.

                We insert into stage (1+lat) *after* the posedge shift, so the
                collision check must look at stage (2+lat) in the current state.
                """
                # Be robust to FE implementations that may *stall* their output:
                # if we believe stage0 is occupied but FEOUT doesn't assert vld,
                # freeze this engine and block new dispatches until it drains.
                stall = pipes[e].valid[0].out() & ~fwded_vld[e]
                busy = lat.eq(0).select(pipes[e].valid[2].out(), c0)
                busy = lat.eq(1).select(pipes[e].valid[3].out(), busy)
                busy = lat.eq(2).select(pipes[e].valid[4].out(), busy)
                busy = lat.eq(3).select(c0, busy)  # stage5 doesn't exist => always free
                return ~stall & ~busy

            # ---- queue head candidate ----
            qv = issue_q[lane].out_valid
            qb = issue_q[lane].out_data

            q_ctrl = qb[0:5]
            q_data = qb[5:133]
            q_seq = qb[133 : 133 + seq_w]
            q_lat = q_ctrl[0:2]
            q_dp = q_ctrl[2:5]

            q_dp_present = ~q_dp.eq(0)
            q_dep_seq = q_seq - q_dp.zext(width=seq_w)
            q_dep_found, q_dep_data = dep_lookup(q_dep_seq)
            q_dep_ok = (~q_dp_present) | q_dep_found

            q_chosen = [m.const(0, width=1) for _ in range(eng_per_lane)]
            q_any_free = m.const(0, width=1)
            for k in range(eng_per_lane):
                e = eng_base + k
                free = eng_free(e, q_lat)
                take = free & ~q_any_free
                q_any_free = q_any_free | free
                q_chosen[k] = take

            q_delta = (q_seq - exp_seq[lane].out()) >> 2
            q_in_range = q_delta.ult(rob_depth)
            q_can = qv & q_dep_ok & q_any_free & q_in_range

            # ---- choose best candidate among stash window + queue head ----
            best_v = m.const(0, width=1)
            best_delta = m.const(0, width=seq_w)
            best_is_q = m.const(0, width=1)
            best_stash_sel = [m.const(0, width=1) for _ in range(stash_win)]

            best_seq = m.const(0, width=seq_w)
            best_data = m.const(0, width=128)
            best_lat = m.const(0, width=2)
            best_dp_present = m.const(0, width=1)
            best_dep_data = m.const(0, width=128)
            best_eng_sel = [m.const(0, width=1) for _ in range(eng_per_lane)]

            for s in range(stash_win):
                sv = stash_v[lane][s].out()
                sb = stash_b[lane][s].out()

                ctrl = sb[0:5]
                data_i = sb[5:133]
                seq_i = sb[133 : 133 + seq_w]
                lat = ctrl[0:2]
                dp = ctrl[2:5]

                dp_present = ~dp.eq(0)
                dep_seq = seq_i - dp.zext(width=seq_w)
                dep_found, dep_data = dep_lookup(dep_seq)
                dep_ok = (~dp_present) | dep_found

                chosen = [m.const(0, width=1) for _ in range(eng_per_lane)]
                any_free = m.const(0, width=1)
                for k in range(eng_per_lane):
                    e = eng_base + k
                    free = eng_free(e, lat)
                    take = free & ~any_free
                    any_free = any_free | free
                    chosen[k] = take

                delta = (seq_i - exp_seq[lane].out()) >> 2
                in_range = delta.ult(rob_depth)
                can = sv & dep_ok & any_free & in_range

                better = can & (~best_v | delta.ult(best_delta))
                best_v = best_v | better
                best_delta = better.select(delta, best_delta)
                best_is_q = better.select(c0, best_is_q)
                for j in range(stash_win):
                    best_stash_sel[j] = better.select(c1 if j == s else c0, best_stash_sel[j])

                best_seq = better.select(seq_i, best_seq)
                best_data = better.select(data_i, best_data)
                best_lat = better.select(lat, best_lat)
                best_dp_present = better.select(dp_present, best_dp_present)
                best_dep_data = better.select(dep_data, best_dep_data)
                for k in range(eng_per_lane):
                    best_eng_sel[k] = better.select(chosen[k], best_eng_sel[k])

            better_q = q_can & (~best_v | q_delta.ult(best_delta))
            best_v = best_v | better_q
            best_delta = better_q.select(q_delta, best_delta)
            best_is_q = better_q.select(c1, best_is_q)
            for j in range(stash_win):
                best_stash_sel[j] = better_q.select(c0, best_stash_sel[j])

            best_seq = better_q.select(q_seq, best_seq)
            best_data = better_q.select(q_data, best_data)
            best_lat = better_q.select(q_lat, best_lat)
            best_dp_present = better_q.select(q_dp_present, best_dp_present)
            best_dep_data = better_q.select(q_dep_data, best_dep_data)
            for k in range(eng_per_lane):
                best_eng_sel[k] = better_q.select(q_chosen[k], best_eng_sel[k])

            # ---- drive FE signals for the selected candidate ----
            for k in range(eng_per_lane):
                e = eng_base + k
                fire_e = best_v & best_eng_sel[k]
                dispatch_fire_eng[e] = fire_e
                dispatch_seq_eng[e] = best_seq

                fwd_vld[e] = fire_e
                fwd_data[e] = best_data
                fwd_lat[e] = best_lat
                fwd_dp_vld[e] = fire_e & best_dp_present
                fwd_dp_data[e] = best_dep_data

            # ---- update stash and issue_q pop for this lane ----
            stash_v_mid = [stash_v[lane][s].out() & ~best_stash_sel[s] for s in range(stash_win)]
            stash_free = m.const(0, width=1)
            for s in range(stash_win):
                stash_free = stash_free | ~stash_v_mid[s]

            q_pop_dispatch = best_v & best_is_q
            # If the queue head is already out of ROB range, later entries are even
            # further out-of-range; stashing would only consume the window and
            # risk deadlock. Stall instead and let BKPR regulate input.
            q_stash_pop = qv & ~q_can & stash_free & q_in_range
            q_pop = issue_q[lane].pop(when=q_pop_dispatch | q_stash_pop)
            lane_pop[lane] = q_pop.fire

            # Insert stashed head when we pop the issue queue in stash mode.
            stash_in_fire = q_stash_pop
            stash_in_bundle = q_pop.data

            stash_ins_sel = [m.const(0, width=1) for _ in range(stash_win)]
            any_free = m.const(0, width=1)
            for s in range(stash_win):
                free = ~stash_v_mid[s]
                take = free & ~any_free
                any_free = any_free | free
                stash_ins_sel[s] = take

            for s in range(stash_win):
                do_push = stash_in_fire & stash_ins_sel[s]
                v_next = do_push.select(c1, stash_v_mid[s])
                b_next = do_push.select(stash_in_bundle, stash_b[lane][s].out())
                stash_v[lane][s].set(v_next)
                stash_b[lane][s].set(b_next)

    # ---- select completions into per-lane ROBs (<=1 per lane per cycle) ----
    with m.scope("ROB"):
        comp_now_v = [m.const(0, width=1) for _ in range(total_eng)]
        comp_now_seq = [m.const(0, width=seq_w) for _ in range(total_eng)]
        comp_now_data = [m.const(0, width=128) for _ in range(total_eng)]
        comp_now_bus = [m.const(0, width=comp_w) for _ in range(total_eng)]

        for e in range(total_eng):
            comp_now_v[e] = pipes[e].valid[0].out() & fwded_vld[e]
            comp_now_seq[e] = pipes[e].seq[0].out()
            comp_now_data[e] = fwded_data[e]
            comp_now_bus[e] = cat(comp_now_seq[e], fwded_data[e])

        direct_take_eng = [m.const(0, width=1) for _ in range(total_eng)]

        ins_fire_lane = [m.const(0, width=1) for _ in range(4)]
        ins_seq_lane = [m.const(0, width=seq_w) for _ in range(4)]
        ins_data_lane = [m.const(0, width=128) for _ in range(4)]

        for lane in range(4):
            eng_base = lane * eng_per_lane

            c0 = m.const(0, width=1)
            c1 = m.const(1, width=1)

            best_v = m.const(0, width=1)
            best_delta = m.const(0, width=seq_w)
            best_seq = m.const(0, width=seq_w)
            best_data = m.const(0, width=128)
            best_sel_direct = [m.const(0, width=1) for _ in range(eng_per_lane)]
            best_sel_buf = [m.const(0, width=1) for _ in range(eng_per_lane)]

            for k in range(eng_per_lane):
                e = eng_base + k

                # Direct completion (current cycle) candidate.
                seq_d = comp_now_seq[e]
                delta_d = (seq_d - exp_seq[lane].out()) >> 2
                in_range_d = delta_d.ult(rob_depth)
                cand_d = comp_now_v[e] & in_range_d
                better_d = cand_d & (~best_v | delta_d.ult(best_delta))
                best_v = best_v | better_d
                best_delta = better_d.select(delta_d, best_delta)
                best_seq = better_d.select(seq_d, best_seq)
                best_data = better_d.select(comp_now_data[e], best_data)
                for j in range(eng_per_lane):
                    best_sel_direct[j] = better_d.select(c1 if j == k else c0, best_sel_direct[j])
                    best_sel_buf[j] = better_d.select(c0, best_sel_buf[j])

                # Buffered completion (comp_q head) candidate.
                vb = comp_q[e].out_valid
                db = comp_q[e].out_data
                seq_b = db[128 : 128 + seq_w]
                delta_b = (seq_b - exp_seq[lane].out()) >> 2
                in_range_b = delta_b.ult(rob_depth)
                cand_b = vb & in_range_b
                better_b = cand_b & (~best_v | delta_b.ult(best_delta))
                best_v = best_v | better_b
                best_delta = better_b.select(delta_b, best_delta)
                best_seq = better_b.select(seq_b, best_seq)
                best_data = better_b.select(db[0:128], best_data)
                for j in range(eng_per_lane):
                    best_sel_direct[j] = better_b.select(c0, best_sel_direct[j])
                    best_sel_buf[j] = better_b.select(c1 if j == k else c0, best_sel_buf[j])

            for k in range(eng_per_lane):
                e = eng_base + k
                comp_q[e].pop(when=best_sel_buf[k])
                direct_take_eng[e] = best_sel_direct[k]

            ins_fire_lane[lane] = best_v
            ins_seq_lane[lane] = best_seq
            ins_data_lane[lane] = best_data

    # ---- completions (FEOUT -> per-engine completion queue) + pipeline update ----
    with m.scope("COMPLETE"):
        c0 = m.const(0, width=1)
        c1 = m.const(1, width=1)
        zero_seq = m.const(0, width=seq_w)

        for e in range(total_eng):
            do_buf = comp_now_v[e] & ~direct_take_eng[e]
            comp_q[e].push(comp_now_bus[e], when=do_buf)

            # If FE stalls output, hold our internal pipe-tracking state so the
            # seq tag stays aligned with the eventual FEOUT beat.
            stall = pipes[e].valid[0].out() & ~fwded_vld[e]
            fire = dispatch_fire_eng[e] & ~stall
            lat = fwd_lat[e]
            seq_in = dispatch_seq_eng[e]

            # Shift pipeline stages (stage0 drops each cycle) then insert into stage (1+lat).
            for i in range(pipe_depth):
                if i + 1 < pipe_depth:
                    v_next = pipes[e].valid[i + 1].out()
                    s_next = pipes[e].seq[i + 1].out()
                else:
                    v_next = c0
                    s_next = zero_seq

                if i == 1:
                    do_set = fire & lat.eq(0)
                elif i == 2:
                    do_set = fire & lat.eq(1)
                elif i == 3:
                    do_set = fire & lat.eq(2)
                elif i == 4:
                    do_set = fire & lat.eq(3)
                else:
                    do_set = c0

                v_new = do_set.select(c1, v_next)
                s_new = do_set.select(seq_in, s_next)
                pipes[e].valid[i].set(stall.select(pipes[e].valid[i].out(), v_new))
                pipes[e].seq[i].set(stall.select(pipes[e].seq[i].out(), s_new))

    # ---- commit (ROB -> PKTOUT) + history ----
    with m.scope("COMMIT"):
        start = commit_lane.out()

        # Read lane0 entries for each output lane.
        lane_ready = [robs[l].valid[0].out() for l in range(4)]
        lane_data0 = [robs[l].data[0].out() for l in range(4)]

        # Compute commit prefixes for each possible start lane (0..3).
        def prefix(start_lane: int) -> tuple[list[Wire], Wire]:
            v = []
            ok = m.const(1, width=1)
            for k in range(4):
                lane = (start_lane + k) & 3
                ok = ok & lane_ready[lane]
                v.append(ok)
            # commit_count in i3 (0..4).
            cnt = v[0].select(
                m.const(1, width=3),
                m.const(0, width=3),
            )
            cnt = v[1].select(m.const(2, width=3), cnt)
            cnt = v[2].select(m.const(3, width=3), cnt)
            cnt = v[3].select(m.const(4, width=3), cnt)
            return v, cnt

        v0, c0 = prefix(0)
        v1, c1 = prefix(1)
        v2, c2 = prefix(2)
        v3, c3 = prefix(3)

        is0 = start.eq(0)
        is1 = start.eq(1)
        is2 = start.eq(2)
        is3 = start.eq(3)

        commit_cnt = is3.select(c3, is2.select(c2, is1.select(c1, c0)))

        # Output valids/data for physical lanes (registered).
        out_v = [m.const(0, width=1) for _ in range(4)]
        out_d = [m.const(0, width=128) for _ in range(4)]

        # commit_v_s[start_lane][k] means commit (k+1)th packet of this cycle (prefix) when starting at start_lane.
        commit_v_s = [v0, v1, v2, v3]
        for phys in range(4):
            # For each possible start, determine whether phys lane outputs.
            vv = m.const(0, width=1)
            dd = m.const(0, width=128)
            for s in range(4):
                for k in range(4):
                    lane = (s + k) & 3
                    if lane != phys:
                        continue
                    hit = commit_v_s[s][k]
                    vv_s = hit
                    dd_s = hit.select(lane_data0[phys], dd)
                    vv = (start.eq(s) & vv_s).select(m.const(1, width=1), vv)
                    dd = (start.eq(s) & vv_s).select(lane_data0[phys], dd)
            out_v[phys] = vv
            out_d[phys] = dd

        for i in range(4):
            out_vld_r[i].set(out_v[i])
            out_data_r[i].set(out_v[i].select(out_d[i], m.const(0, width=128)))

        # Per-lane pop (shift) when that lane committed.
        commit_pop = out_v

        # Advance commit lane pointer by commit_cnt.
        next_lane = (start + commit_cnt[0:2])[0:2]
        commit_lane.set(next_lane)

        # Update expected seq per lane.
        for lane in range(4):
            inc4 = commit_pop[lane].select(m.const(4, width=seq_w), m.const(0, width=seq_w))
            exp_seq[lane].set(exp_seq[lane].out() + inc4)

        # Update ROBs: insert completion then pop if committed.
        for lane in range(4):
            _rob_update(
                robs[lane],
                exp_seq=exp_seq[lane].out(),
                commit_pop=commit_pop[lane],
                ins_fire=ins_fire_lane[lane],
                ins_seq=ins_seq_lane[lane],
                ins_data=ins_data_lane[lane],
                depth=rob_depth,
            )

        # Build commit seq/data for up to 4 outputs in-order (for history shift).
        commit_seq_slots = [m.const(0, width=seq_w) for _ in range(4)]
        commit_data_slots = [m.const(0, width=128) for _ in range(4)]
        for k in range(4):
            lane_k = (start + m.const(k, width=2))[0:2]
            # Select exp_seq/data based on lane_k.
            s = m.const(0, width=seq_w)
            d = m.const(0, width=128)
            for lane in range(4):
                is_lane = lane_k.eq(lane)
                s = is_lane.select(exp_seq[lane].out(), s)
                d = is_lane.select(lane_data0[lane], d)
            commit_seq_slots[k] = s
            commit_data_slots[k] = d

        _hist_shift_insert(hist, k=commit_cnt, seqs=commit_seq_slots, datas=commit_data_slots)

    # ---- bkpr update (after shadow counts) ----
    with m.scope("BKPR"):
        # Update shadow counts for each issue queue: cnt += push - pop.
        # Note: we use the queue fires as the source of truth.
        bkpr_next = m.const(0, width=1)
        for lane in range(4):
            push_i = push_fire[lane].zext(width=16)
            pop_i = lane_pop[lane].zext(width=16)
            cnt_next = shadow_cnt[lane].out() + push_i - pop_i
            shadow_cnt[lane].set(cnt_next)

            # Assert when count is close to full (leave slack for registered BKPR).
            near_full = cnt_next.uge(lane_q_depth - bkpr_slack)
            bkpr_next = bkpr_next | near_full
        bkpr_r.set(bkpr_next)

    # ---- outputs ----
    m.output("pkt_in_bkpr", bkpr_r.out())
    for i in range(4):
        m.output(f"lane{i}_pkt_out_vld", out_vld_r[i].out())
        m.output(f"lane{i}_pkt_out_data", out_data_r[i].out())

    for e in range(total_eng):
        m.output(f"fwd{e}_pkt_data_vld", fwd_vld[e])
        m.output(f"fwd{e}_pkt_data", fwd_data[e])
        m.output(f"fwd{e}_pkt_lat", fwd_lat[e])
        m.output(f"fwd{e}_pkt_dp_vld", fwd_dp_vld[e])
        m.output(f"fwd{e}_pkt_dp_data", fwd_dp_data[e])


def build(
    m: Circuit,
    # Total number of Forwarding Engines (FE). Must be a multiple of 4.
    # This is the recommended knob when instantiating FE internally in the exam wrapper.
    N_FE: int | None = None,
    # Defaults are chosen to match the common exam-style wrapper (4 engines total).
    # For PPA sweeps, override via `--param` or use `tools/dse_fastfwd_pyc.sh`.
    ENG_PER_LANE: int = 1,
    LANE_Q_DEPTH: int = 16,
    ENG_Q_DEPTH: int = 4,
    ROB_DEPTH: int = 16,
    SEQ_W: int = 16,
    HIST_DEPTH: int = 8,
    STASH_WIN: int = 6,
    BKPR_SLACK: int = 1,
) -> None:
    # Wrapper kept tiny so the AST/JIT compiler executes the implementation as Python.
    _build_fastfwd(
        m,
        N_FE=N_FE,
        ENG_PER_LANE=ENG_PER_LANE,
        LANE_Q_DEPTH=LANE_Q_DEPTH,
        ENG_Q_DEPTH=ENG_Q_DEPTH,
        ROB_DEPTH=ROB_DEPTH,
        SEQ_W=SEQ_W,
        HIST_DEPTH=HIST_DEPTH,
        STASH_WIN=STASH_WIN,
        BKPR_SLACK=BKPR_SLACK,
    )


# Stable module name for codegen.
build.__pycircuit_name__ = "FastFwd"
