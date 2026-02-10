#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


def load_events(path: Path):
    events = []
    max_cycle = 0
    max_node = 0
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cycle = int(row.get("cycle", "0"))
                node = int(row.get("node", "0"))
            except ValueError:
                continue
            event = row.get("event", "")
            tag = row.get("tag", "")
            write = row.get("write", "")
            events.append((cycle, node, event, tag, write))
            if cycle > max_cycle:
                max_cycle = cycle
            if node > max_node:
                max_node = node
    return events, max_cycle, max_node


def render_svg(events, max_cycle, max_node, scale, lane_h, out_path: Path):
    margin_x = 70
    margin_top = 60
    margin_bottom = 30
    width = margin_x * 2 + max_cycle * scale + 1
    height = margin_top + margin_bottom + (max_node + 1) * lane_h

    def y_for(node, event):
        base = margin_top + node * lane_h
        if event == "resp":
            return base + int(lane_h * 0.68)
        return base + int(lane_h * 0.28)

    lines = []
    lines.append(
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
    )
    lines.append("<rect x='0' y='0' width='100%' height='100%' fill='#f9fafb' />")
    lines.append(
        "<style>"
        "text{font-family:monospace;font-size:12px;fill:#111827;}"
        ".grid{stroke:#e5e7eb;stroke-width:1;}"
        ".lane{fill:#ffffff;}"
        ".lane-alt{fill:#f3f4f6;}"
        ".mid{stroke:#d1d5db;stroke-dasharray:2 3;}"
        ".label{fill:#111827;font-size:12px;}"
        ".title{font-size:14px;font-weight:bold;}"
        ".legend{font-size:11px;fill:#374151;}"
        "</style>"
    )

    lines.append(
        f"<text class='title' x='{margin_x}' y='{margin_top - 30}'>TMU trace timeline</text>"
    )
    lines.append(
        f"<text class='legend' x='{margin_x}' y='{margin_top - 12}'>accept = blue circle, resp = green diamond</text>"
    )

    if max_cycle <= 50:
        tick_step = 5
    elif max_cycle <= 200:
        tick_step = 10
    elif max_cycle <= 500:
        tick_step = 20
    else:
        tick_step = 50

    for n in range(max_node + 1):
        y = margin_top + n * lane_h
        lane_cls = "lane" if (n % 2 == 0) else "lane-alt"
        lines.append(
            f"<rect class='{lane_cls}' x='{margin_x}' y='{y}' width='{width - margin_x * 2}' height='{lane_h}' />"
        )
        mid_y = y + int(lane_h * 0.5)
        lines.append(f"<line class='mid' x1='{margin_x}' y1='{mid_y}' x2='{width - margin_x}' y2='{mid_y}' />")
        lines.append(f"<text class='label' x='8' y='{y + 14}'>node{n}</text>")

    for cyc in range(0, max_cycle + 1, tick_step):
        x = margin_x + cyc * scale
        lines.append(f"<line class='grid' x1='{x}' y1='{margin_top}' x2='{x}' y2='{height - margin_bottom}' />")
        lines.append(f"<text class='legend' x='{x + 2}' y='{height - 8}'>{cyc}</text>")

    for cycle, node, event, tag, write in events:
        x = margin_x + cycle * scale
        y = y_for(node, event)
        is_accept = event == "accept"
        color = "#2563eb" if is_accept else "#16a34a"
        label = f"{event} node={node} tag={tag} w={write} cycle={cycle}"
        if is_accept:
            lines.append(f"<circle cx='{x}' cy='{y}' r='3.5' fill='{color}' stroke='#1e3a8a' stroke-width='0.5'>")
            lines.append(f"<title>{label}</title>")
            lines.append("</circle>")
        else:
            size = 4
            points = [
                f"{x},{y - size}",
                f"{x + size},{y}",
                f"{x},{y + size}",
                f"{x - size},{y}",
            ]
            lines.append(
                f"<polygon points='{' '.join(points)}' fill='{color}' stroke='#14532d' stroke-width='0.5'>"
            )
            lines.append(f"<title>{label}</title>")
            lines.append("</polygon>")

    lines.append("</svg>")
    out_path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Render TMU trace CSV into SVG timeline.")
    parser.add_argument("csv", type=Path, help="Path to tmu_trace.csv")
    parser.add_argument("-o", "--out", type=Path, default=Path("tmu_trace.svg"), help="Output SVG path")
    parser.add_argument("--scale", type=int, default=4, help="Pixels per cycle")
    parser.add_argument("--lane", type=int, default=30, help="Pixels per node lane")
    args = parser.parse_args()

    events, max_cycle, max_node = load_events(args.csv)
    if not events:
        raise SystemExit("no events found in CSV")
    events.sort(key=lambda e: (e[0], e[1], 0 if e[2] == "accept" else 1))
    render_svg(events, max_cycle, max_node, args.scale, args.lane, args.out)


if __name__ == "__main__":
    main()
