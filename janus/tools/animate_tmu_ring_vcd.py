#!/usr/bin/env python3
import argparse
import math
from pathlib import Path

RING_ORDER = [0, 1, 3, 5, 7, 6, 4, 2]


def ring_positions(center_x, center_y, radius):
    positions = {}
    n = len(RING_ORDER)
    for i, node in enumerate(RING_ORDER):
        angle = (2.0 * math.pi * i / n) - (math.pi / 2.0)
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        positions[node] = (x, y)
    return positions


def parse_vcd(path: Path, watch_names, max_cycles=None, skip_cycles=0):
    watch_names = set(watch_names)
    id_to_name = {}
    values = {name: "0" for name in watch_names}
    snapshots = []

    with path.open() as f:
        in_header = True
        for line in f:
            line = line.strip()
            if not line:
                continue
            if in_header:
                if line.startswith("$var"):
                    parts = line.split()
                    if len(parts) >= 5:
                        code = parts[3]
                        name = parts[4]
                        if name in watch_names:
                            id_to_name[code] = name
                elif line.startswith("$enddefinitions"):
                    in_header = False
                continue

            # body parsing
            if line[0] == "#":
                time = int(line[1:])
                continue
            val = line[0]
            if val not in "01xXzZ":
                continue
            code = line[1:]
            name = id_to_name.get(code)
            if name is None:
                continue
            values[name] = "0" if val in "xXzZ" else val

            # detect posedge from clk updates
            if name == "clk" and val == "1":
                if skip_cycles > 0:
                    skip_cycles -= 1
                    continue
                snap = {k: values.get(k, "0") for k in watch_names}
                snapshots.append(snap)
                if max_cycles is not None and len(snapshots) >= max_cycles:
                    break

    return snapshots


def emit_token(lines, token_id, start_xy, end_xy, begin_s, dur_s, color, shape, label, glow_id):
    x0, y0 = start_xy
    x1, y1 = end_xy
    if shape == "circle":
        lines.append(
            f"<circle id='{token_id}' cx='{x0:.2f}' cy='{y0:.2f}' r='7' fill='{color}' filter='url(#{glow_id})' stroke='#0f172a' stroke-width='1'>"
        )
    else:
        size = 8
        points = [
            f"{x0:.2f},{y0 - size:.2f}",
            f"{x0 + size:.2f},{y0:.2f}",
            f"{x0:.2f},{y0 + size:.2f}",
            f"{x0 - size:.2f},{y0:.2f}",
        ]
        lines.append(
            f"<polygon id='{token_id}' points='{' '.join(points)}' fill='{color}' filter='url(#{glow_id})' stroke='#0f172a' stroke-width='1'>"
        )
    lines.append(f"<title>{label}</title>")
    lines.append(
        f"<animate attributeName='opacity' values='0;1;1;0' keyTimes='0;0.02;0.98;1' begin='{begin_s:.2f}s' dur='{dur_s:.2f}s' fill='freeze' />"
    )
    lines.append(
        f"<animate attributeName='cx' values='{x0:.2f};{x1:.2f}' keyTimes='0;1' begin='{begin_s:.2f}s' dur='{dur_s:.2f}s' fill='freeze' />"
    )
    lines.append(
        f"<animate attributeName='cy' values='{y0:.2f};{y1:.2f}' keyTimes='0;1' begin='{begin_s:.2f}s' dur='{dur_s:.2f}s' fill='freeze' />"
    )
    lines.append("</circle>" if shape == "circle" else "</polygon>")


def render_svg(snapshots, out_path: Path, cycle_time):
    width = 980
    height = 720
    cx = width / 2
    cy = height / 2 + 10

    req_radius = 230
    rsp_radius = 280

    req_pos = ring_positions(cx, cy, req_radius)
    rsp_pos = ring_positions(cx, cy, rsp_radius)

    next_map = {RING_ORDER[i]: RING_ORDER[(i + 1) % len(RING_ORDER)] for i in range(len(RING_ORDER))}
    prev_map = {RING_ORDER[i]: RING_ORDER[(i - 1) % len(RING_ORDER)] for i in range(len(RING_ORDER))}

    lines = []
    lines.append(
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
    )
    lines.append("<rect x='0' y='0' width='100%' height='100%' fill='#0b1020' />")
    lines.append(
        "<style>"
        "text{font-family:monospace;fill:#e2e8f0;}"
        ".title{font-size:18px;font-weight:bold;}"
        ".legend{font-size:12px;fill:#c7d2fe;}"
        ".node{fill:#111827;stroke:#94a3b8;stroke-width:2;}"
        ".ring{stroke:#1e293b;stroke-width:10;fill:none;}"
        ".ring-rsp{stroke:#243247;stroke-width:10;fill:none;}"
        ".edge{stroke:#1f2937;stroke-width:2;opacity:0.6;}"
        "</style>"
    )

    lines.append(
        "<defs>"
        "<filter id='glow_req' x='-50%' y='-50%' width='200%' height='200%'>"
        "<feGaussianBlur stdDeviation='3' result='blur'/>"
        "<feMerge><feMergeNode in='blur'/><feMergeNode in='SourceGraphic'/></feMerge>"
        "</filter>"
        "<filter id='glow_rsp' x='-50%' y='-50%' width='200%' height='200%'>"
        "<feGaussianBlur stdDeviation='3' result='blur'/>"
        "<feMerge><feMergeNode in='blur'/><feMergeNode in='SourceGraphic'/></feMerge>"
        "</filter>"
        "</defs>"
    )

    lines.append(f"<text class='title' x='30' y='36'>TMU ring flow (from VCD)</text>")
    lines.append(
        f"<text class='legend' x='30' y='58'>req cw/cc = blue/cyan • rsp cw/cc = green/lime • {cycle_time:.2f}s per cycle</text>"
    )

    lines.append(f"<circle class='ring' cx='{cx:.2f}' cy='{cy:.2f}' r='{req_radius:.2f}' />")
    lines.append(f"<circle class='ring-rsp' cx='{cx:.2f}' cy='{cy:.2f}' r='{rsp_radius:.2f}' />")

    for i in range(len(RING_ORDER)):
        a = RING_ORDER[i]
        b = RING_ORDER[(i + 1) % len(RING_ORDER)]
        x1, y1 = req_pos[a]
        x2, y2 = req_pos[b]
        lines.append(f"<line class='edge' x1='{x1:.2f}' y1='{y1:.2f}' x2='{x2:.2f}' y2='{y2:.2f}' />")

    for node, (x, y) in req_pos.items():
        lines.append(f"<circle class='node' cx='{x:.2f}' cy='{y:.2f}' r='26' />")
        lines.append(f"<text x='{x - 12:.2f}' y='{y + 4:.2f}'>n{node}</text>")

    for cyc, snap in enumerate(snapshots):
        begin = cyc * cycle_time
        dur = cycle_time
        for nid in range(8):
            # requests on inner ring
            if snap.get(f"dbg_req_cw_v{nid}") == "1":
                start = req_pos[nid]
                end = req_pos[next_map[nid]]
                emit_token(
                    lines,
                    f"req_cw_{cyc}_{nid}",
                    start,
                    end,
                    begin,
                    dur,
                    "#38bdf8",
                    "circle",
                    f"req cw node={nid} cycle={cyc}",
                    "glow_req",
                )
            if snap.get(f"dbg_req_cc_v{nid}") == "1":
                start = req_pos[nid]
                end = req_pos[prev_map[nid]]
                emit_token(
                    lines,
                    f"req_cc_{cyc}_{nid}",
                    start,
                    end,
                    begin,
                    dur,
                    "#22d3ee",
                    "circle",
                    f"req cc node={nid} cycle={cyc}",
                    "glow_req",
                )

            # responses on outer ring
            if snap.get(f"dbg_rsp_cw_v{nid}") == "1":
                start = rsp_pos[nid]
                end = rsp_pos[next_map[nid]]
                emit_token(
                    lines,
                    f"rsp_cw_{cyc}_{nid}",
                    start,
                    end,
                    begin,
                    dur,
                    "#22c55e",
                    "diamond",
                    f"rsp cw node={nid} cycle={cyc}",
                    "glow_rsp",
                )
            if snap.get(f"dbg_rsp_cc_v{nid}") == "1":
                start = rsp_pos[nid]
                end = rsp_pos[prev_map[nid]]
                emit_token(
                    lines,
                    f"rsp_cc_{cyc}_{nid}",
                    start,
                    end,
                    begin,
                    dur,
                    "#a3e635",
                    "diamond",
                    f"rsp cc node={nid} cycle={cyc}",
                    "glow_rsp",
                )

    lines.append("</svg>")
    out_path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Animate TMU ring flows from VCD debug signals.")
    parser.add_argument("vcd", type=Path, help="Path to VCD (tb_janus_tmu_pyc_cpp.vcd)")
    parser.add_argument("-o", "--out", type=Path, default=Path("tmu_flow_real.svg"), help="Output SVG")
    parser.add_argument("--cycle", type=float, default=0.20, help="Seconds per cycle")
    parser.add_argument("--max-cycles", type=int, default=None, help="Limit cycles")
    parser.add_argument("--skip-cycles", type=int, default=0, help="Skip initial cycles")
    args = parser.parse_args()

    watch = ["clk"]
    for n in range(8):
        watch.append(f"dbg_req_cw_v{n}")
        watch.append(f"dbg_req_cc_v{n}")
        watch.append(f"dbg_rsp_cw_v{n}")
        watch.append(f"dbg_rsp_cc_v{n}")

    snapshots = parse_vcd(args.vcd, watch, max_cycles=args.max_cycles, skip_cycles=args.skip_cycles)
    if not snapshots:
        raise SystemExit("no snapshots found (check VCD path or signals)")

    render_svg(snapshots, args.out, args.cycle)


if __name__ == "__main__":
    main()
