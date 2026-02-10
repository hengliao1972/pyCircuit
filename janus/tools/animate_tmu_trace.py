#!/usr/bin/env python3
import argparse
import csv
import math
from collections import defaultdict, deque
from pathlib import Path

RING_ORDER = [0, 1, 3, 5, 7, 6, 4, 2]


def parse_int(text: str) -> int:
    text = text.strip()
    if text.startswith("0x") or text.startswith("0X"):
        return int(text, 16)
    return int(text, 10)


def load_transactions(path: Path):
    accepts = defaultdict(deque)
    transactions = []
    max_cycle = 0

    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            try:
                cycle = int(row.get("cycle", "0"))
                node = int(row.get("node", "0"))
                tag = int(row.get("tag", "0"))
                write = int(row.get("write", "0"))
            except ValueError:
                continue
            event = row.get("event", "")
            if cycle > max_cycle:
                max_cycle = cycle
            if event == "accept":
                addr_text = row.get("addr_or_word0", "0")
                try:
                    addr = parse_int(addr_text)
                except ValueError:
                    addr = 0
                accepts[(node, tag)].append({
                    "cycle": cycle,
                    "node": node,
                    "tag": tag,
                    "write": write,
                    "addr": addr,
                })
            elif event == "resp":
                key = (node, tag)
                if not accepts[key]:
                    continue
                acc = accepts[key].popleft()
                transactions.append({
                    "src": acc["node"],
                    "dst": (acc["addr"] >> 8) & 0x7,
                    "cycle_accept": acc["cycle"],
                    "cycle_resp": cycle,
                    "tag": tag,
                    "write": acc["write"],
                })

    return transactions, max_cycle


def ring_positions(center_x, center_y, radius):
    positions = {}
    n = len(RING_ORDER)
    for i, node in enumerate(RING_ORDER):
        angle = (2.0 * math.pi * i / n) - (math.pi / 2.0)
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        positions[node] = (x, y)
    return positions


def path_nodes(src, dst):
    if src == dst:
        return [src]
    n = len(RING_ORDER)
    pos = {node: i for i, node in enumerate(RING_ORDER)}
    s = pos[src]
    d = pos[dst]
    cw = (d - s) % n
    cc = (s - d) % n
    if cw <= cc:
        step = 1
        dist = cw
    else:
        step = -1
        dist = cc
    nodes = []
    idx = s
    for _ in range(dist + 1):
        nodes.append(RING_ORDER[idx])
        idx = (idx + step) % n
    return nodes


def ensure_anim_coords(coords):
    if len(coords) == 1:
        return [coords[0], coords[0]]
    return coords


def emit_token(lines, token_id, coords, begin_s, dur_s, color, shape, label):
    coords = ensure_anim_coords(coords)
    xs = ";".join(f"{x:.2f}" for x, _ in coords)
    ys = ";".join(f"{y:.2f}" for _, y in coords)
    key_times = ";".join(f"{i / (len(coords) - 1):.3f}" for i in range(len(coords)))
    if shape == "circle":
        lines.append(f"<circle id='{token_id}' cx='{coords[0][0]:.2f}' cy='{coords[0][1]:.2f}' r='6' fill='{color}' stroke='#111827' stroke-width='1'>")
    else:
        size = 7
        x0, y0 = coords[0]
        points = [
            f"{x0:.2f},{y0 - size:.2f}",
            f"{x0 + size:.2f},{y0:.2f}",
            f"{x0:.2f},{y0 + size:.2f}",
            f"{x0 - size:.2f},{y0:.2f}",
        ]
        lines.append(f"<polygon id='{token_id}' points='{' '.join(points)}' fill='{color}' stroke='#111827' stroke-width='1'>")
    lines.append(f"<title>{label}</title>")
    lines.append(
        f"<animate attributeName='opacity' values='0;1;1;0' keyTimes='0;0.02;0.98;1' begin='{begin_s:.2f}s' dur='{dur_s:.2f}s' fill='freeze' />"
    )
    lines.append(
        f"<animate attributeName='cx' values='{xs}' keyTimes='{key_times}' begin='{begin_s:.2f}s' dur='{dur_s:.2f}s' fill='freeze' />"
    )
    lines.append(
        f"<animate attributeName='cy' values='{ys}' keyTimes='{key_times}' begin='{begin_s:.2f}s' dur='{dur_s:.2f}s' fill='freeze' />"
    )
    lines.append("</circle>" if shape == "circle" else "</polygon>")


def render_svg(transactions, max_cycle, out_path: Path, cycle_time):
    width = 900
    height = 650
    cx = width / 2
    cy = height / 2
    radius = 230

    positions = ring_positions(cx, cy, radius)

    lines = []
    lines.append(
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
    )
    lines.append("<rect x='0' y='0' width='100%' height='100%' fill='#0f172a' />")
    lines.append(
        "<style>"
        "text{font-family:monospace;fill:#e2e8f0;}"
        ".title{font-size:16px;font-weight:bold;}"
        ".legend{font-size:12px;fill:#cbd5f5;}"
        ".node{fill:#111827;stroke:#94a3b8;stroke-width:2;}"
        ".ring{stroke:#334155;stroke-width:8;fill:none;}"
        ".edge{stroke:#1f2937;stroke-width:2;opacity:0.6;}"
        "</style>"
    )
    lines.append("<circle class='ring' cx='{:.2f}' cy='{:.2f}' r='{:.2f}' />".format(cx, cy, radius))
    lines.append("<text class='title' x='30' y='36'>TMU ring flow animation</text>")
    lines.append("<text class='legend' x='30' y='56'>blue=accept(req), green=resp</text>")

    for i in range(len(RING_ORDER)):
        a = RING_ORDER[i]
        b = RING_ORDER[(i + 1) % len(RING_ORDER)]
        x1, y1 = positions[a]
        x2, y2 = positions[b]
        lines.append(f"<line class='edge' x1='{x1:.2f}' y1='{y1:.2f}' x2='{x2:.2f}' y2='{y2:.2f}' />")

    for node, (x, y) in positions.items():
        lines.append(f"<circle class='node' cx='{x:.2f}' cy='{y:.2f}' r='26' />")
        lines.append(f"<text x='{x - 12:.2f}' y='{y + 4:.2f}'>n{node}</text>")

    tpc = cycle_time
    for idx, tr in enumerate(transactions):
        src = tr["src"]
        dst = tr["dst"]
        c0 = tr["cycle_accept"]
        c1 = tr["cycle_resp"]
        tag = tr["tag"]
        write = tr["write"]

        req_nodes = path_nodes(src, dst)
        req_coords = [positions[n] for n in req_nodes]
        req_hops = max(len(req_coords) - 1, 1)
        req_dur = req_hops * tpc
        req_begin = c0 * tpc
        req_label = f"req tag={tag} src={src} dst={dst} w={write}"
        emit_token(
            lines,
            f"req_{idx}",
            req_coords,
            req_begin,
            req_dur,
            "#38bdf8",
            "circle",
            req_label,
        )

        rsp_nodes = path_nodes(dst, src)
        rsp_coords = [positions[n] for n in rsp_nodes]
        rsp_hops = max(len(rsp_coords) - 1, 1)
        rsp_dur = rsp_hops * tpc
        rsp_end = c1 * tpc
        rsp_begin = max(req_begin + req_dur, rsp_end - rsp_dur)
        rsp_label = f"resp tag={tag} src={dst} dst={src} w={write}"
        emit_token(
            lines,
            f"rsp_{idx}",
            rsp_coords,
            rsp_begin,
            rsp_dur,
            "#22c55e",
            "diamond",
            rsp_label,
        )

    lines.append("</svg>")
    out_path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Render animated SVG for TMU ring flows.")
    parser.add_argument("csv", type=Path, help="Path to tmu_trace.csv")
    parser.add_argument("-o", "--out", type=Path, default=Path("tmu_flow.svg"), help="Output SVG")
    parser.add_argument("--cycle", type=float, default=0.06, help="Seconds per cycle")
    args = parser.parse_args()

    transactions, max_cycle = load_transactions(args.csv)
    if not transactions:
        raise SystemExit("no transactions found in CSV")
    render_svg(transactions, max_cycle, args.out, args.cycle)


if __name__ == "__main__":
    main()
