#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]


def log(msg: str) -> None:
    print(f"[pyc] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[pyc][warn] {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[pyc][error] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def which(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: Sequence[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    pretty = " ".join(subprocess.list2cmdline([c]) if " " in c else c for c in cmd)
    log(f"$ {pretty}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def detect_pyc_compile() -> Path:
    env_path = os.environ.get("PYC_COMPILE")
    if env_path:
        p = Path(env_path)
        if p.is_file() and os.access(p, os.X_OK):
            return p
        die(f"PYC_COMPILE is set but not executable: {p}")

    candidates = [
        ROOT / "build" / "bin" / "pyc-compile",
        ROOT / "pyc" / "mlir" / "build" / "bin" / "pyc-compile",
        ROOT / "build-top" / "bin" / "pyc-compile",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c

    found = which("pyc-compile")
    if found:
        return Path(found)

    die("missing pyc-compile (set PYC_COMPILE=... or build it with: scripts/pyc build)")


def try_detect_pyc_compile() -> Path | None:
    try:
        return detect_pyc_compile()
    except SystemExit:
        return None


def pythonpath_env() -> str:
    # Prefer editable install, but allow repo-local runs.
    return os.pathsep.join([str(ROOT / "python"), str(ROOT)])


@dataclass(frozen=True)
class VerilogSim:
    name: str
    top: str
    sources: tuple[Path, ...]
    include_dirs: tuple[Path, ...]
    trace_dir: Path


def verilog_sims() -> dict[str, VerilogSim]:
    return {
        "fastfwd_pyc": VerilogSim(
            name="fastfwd_pyc",
            top="tb_fastfwd_pyc",
            sources=(
                ROOT / "examples" / "fastfwd_pyc" / "tb_fastfwd_pyc.sv",
                ROOT / "examples" / "generated" / "fastfwd_pyc" / "exam2021_top.v",
                ROOT / "examples" / "generated" / "fastfwd_pyc" / "fastfwd_pyc.v",
                ROOT / "examples" / "generated" / "fastfwd_pyc" / "fe.v",
            ),
            include_dirs=(ROOT / "include" / "pyc" / "verilog",),
            trace_dir=ROOT / "examples" / "generated" / "fastfwd_pyc",
        ),
        "issue_queue_2picker": VerilogSim(
            name="issue_queue_2picker",
            top="tb_issue_queue_2picker",
            sources=(ROOT / "examples" / "issue_queue_2picker" / "tb_issue_queue_2picker.sv",),
            # The TB uses `include "../generated/..."` so add the TB directory to -I.
            include_dirs=(ROOT / "include" / "pyc" / "verilog", ROOT / "examples" / "issue_queue_2picker"),
            trace_dir=ROOT / "examples" / "generated" / "tb_issue_queue_2picker",
        ),
        "linx_cpu_pyc": VerilogSim(
            name="linx_cpu_pyc",
            top="tb_linx_cpu_pyc",
            sources=(
                ROOT / "examples" / "linx_cpu" / "tb_linx_cpu_pyc.sv",
                ROOT / "examples" / "generated" / "linx_cpu_pyc" / "linx_cpu_pyc.v",
            ),
            include_dirs=(ROOT / "include" / "pyc" / "verilog",),
            trace_dir=ROOT / "examples" / "generated" / "linx_cpu_pyc",
        ),
    }


def cmd_doctor(_: argparse.Namespace) -> int:
    log(f"root: {ROOT}")
    pyc_compile = try_detect_pyc_compile()
    if pyc_compile:
        log(f"tool: pyc-compile: {pyc_compile}")
    else:
        warn("tool: pyc-compile: MISSING (build with: scripts/pyc build)")

    tools = ["python3", "iverilog", "vvp", "verilator", "gtkwave"]
    for t in tools:
        p = which(t)
        if p:
            log(f"tool: {t}: {p}")
        else:
            warn(f"tool: {t}: MISSING")

    if not which("gtkwave"):
        warn("gtkwave is optional; install with: brew install gtkwave  (macOS) or apt-get install gtkwave (Ubuntu)")
    return 0


def cmd_regen(args: argparse.Namespace) -> int:
    pyc_compile = detect_pyc_compile()
    env = dict(os.environ)
    env["PYC_COMPILE"] = str(pyc_compile)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = pythonpath_env()

    if getattr(args, "fastfwd_nfe", None) is not None:
        env["FASTFWD_N_FE"] = str(args.fastfwd_nfe)

    if args.examples:
        run(["bash", str(ROOT / "examples" / "update_generated.sh")], env=env)
    if args.janus:
        run(["bash", str(ROOT / "janus" / "update_generated.sh")], env=env)
    return 0


def cmd_cpp_test(args: argparse.Namespace) -> int:
    pyc_compile = detect_pyc_compile()
    env = dict(os.environ)
    env["PYC_COMPILE"] = str(pyc_compile)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = pythonpath_env()

    if args.fastfwd:
        run(["bash", str(ROOT / "tools" / "run_fastfwd_pyc_cpp.sh")], env=env)
    if args.cpu:
        run(["bash", str(ROOT / "tools" / "run_linx_cpu_pyc_cpp.sh")], env=env)
    if args.janus:
        run(["bash", str(ROOT / "janus" / "tools" / "run_janus_bcc_pyc_cpp.sh")], env=env)
        run(["bash", str(ROOT / "janus" / "tools" / "run_janus_bcc_ooo_pyc_cpp.sh")], env=env)
    return 0


def cmd_verilog_sim(args: argparse.Namespace) -> int:
    sims = verilog_sims()
    if args.design not in sims:
        die(f"unknown design: {args.design} (choices: {', '.join(sorted(sims.keys()))})")
    sim = sims[args.design]

    sim.trace_dir.mkdir(parents=True, exist_ok=True)

    tool = args.tool
    if tool == "iverilog":
        iverilog = which("iverilog") or die("missing iverilog (install with: brew install icarus-verilog)")
        vvp = which("vvp") or die("missing vvp (install with: brew install icarus-verilog)")

        out_vvp = sim.trace_dir / f"{sim.top}.vvp"
        cmd = [iverilog, "-g2012"]
        for inc in sim.include_dirs:
            cmd += ["-I", str(inc)]
        cmd += ["-o", str(out_vvp)]
        cmd += [str(s) for s in sim.sources]
        run(cmd, cwd=ROOT)
        run([vvp, str(out_vvp), *args.sim_args], cwd=ROOT)
        return 0

    if tool == "verilator":
        verilator = which("verilator") or die("missing verilator (install with: brew install verilator)")
        build_dir = sim.trace_dir / "verilator"
        build_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            verilator,
            "--binary",
            "-Wall",
            "-Wno-fatal",
            "--quiet",
            "--quiet-build",
            "--timing",
            "--trace",
            "--top-module",
            sim.top,
            "--Mdir",
            str(build_dir),
        ]
        for inc in sim.include_dirs:
            cmd += ["-I" + str(inc)]
        cmd += [str(s) for s in sim.sources]
        run(cmd, cwd=ROOT)

        exe = build_dir / f"V{sim.top}"
        if not exe.is_file():
            die(f"verilator did not produce expected binary: {exe}")
        run([str(exe), *args.sim_args], cwd=ROOT)
        return 0

    die(f"unknown --tool: {tool} (expected: iverilog or verilator)")

def cmd_verilog_sim_manifest(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        die(f"missing manifest: {manifest_path}")
    out_dir = manifest_path.parent

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"failed to parse manifest.json: {e}")

    mods = manifest.get("verilog_modules")
    if not isinstance(mods, list) or not all(isinstance(x, str) for x in mods):
        die("manifest.json missing verilog_modules: expected list[str]")

    tb_path = Path(args.tb)
    if not tb_path.is_file():
        die(f"missing tb file: {tb_path}")

    include_dirs = [Path(p) for p in (args.include_dir or [])]
    include_dirs.append(out_dir)

    sources = [tb_path, *[out_dir / m for m in mods]]
    for s in sources:
        if not s.is_file():
            die(f"missing source referenced by manifest: {s}")

    top = args.top
    if not top:
        top_sym = manifest.get("top")
        top = f"tb_{top_sym}" if isinstance(top_sym, str) and top_sym else "tb"

    tool = args.tool
    if tool == "iverilog":
        iverilog = which("iverilog") or die("missing iverilog (install with: brew install icarus-verilog)")
        vvp = which("vvp") or die("missing vvp (install with: brew install icarus-verilog)")

        out_vvp = out_dir / f"{top}.vvp"
        cmd = [iverilog, "-g2012"]
        for inc in include_dirs:
            cmd += ["-I", str(inc)]
        cmd += ["-o", str(out_vvp)]
        cmd += [str(s) for s in sources]
        run(cmd, cwd=ROOT)
        run([vvp, str(out_vvp), *args.sim_args], cwd=ROOT)
        return 0

    if tool == "verilator":
        verilator = which("verilator") or die("missing verilator (install with: brew install verilator)")
        build_dir = out_dir / "verilator"
        build_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            verilator,
            "--binary",
            "-Wall",
            "-Wno-fatal",
            "--quiet",
            "--quiet-build",
            "--timing",
            "--trace",
            "--top-module",
            top,
            "--Mdir",
            str(build_dir),
        ]
        for inc in include_dirs:
            cmd += ["-I" + str(inc)]
        cmd += [str(s) for s in sources]
        run(cmd, cwd=ROOT)

        exe = build_dir / f"V{top}"
        if not exe.is_file():
            die(f"verilator did not produce expected binary: {exe}")
        run([str(exe), *args.sim_args], cwd=ROOT)
        return 0

    die(f"unknown --tool: {tool} (expected: iverilog or verilator)")


def cmd_verilog_lint(args: argparse.Namespace) -> int:
    sims = verilog_sims()
    if args.design not in sims:
        die(f"unknown design: {args.design} (choices: {', '.join(sorted(sims.keys()))})")
    sim = sims[args.design]
    verilator = which("verilator") or die("missing verilator (install with: brew install verilator)")

    # Lint the design sources (skip testbench by default).
    lint_sources: list[Path] = []
    if args.design == "fastfwd_pyc":
        lint_sources = [
            ROOT / "examples" / "generated" / "fastfwd_pyc" / "exam2021_top.v",
            ROOT / "examples" / "generated" / "fastfwd_pyc" / "fastfwd_pyc.v",
            ROOT / "examples" / "generated" / "fastfwd_pyc" / "fe.v",
        ]
    elif args.design == "issue_queue_2picker":
        lint_sources = [ROOT / "examples" / "generated" / "issue_queue_2picker" / "issue_queue_2picker.v"]
    elif args.design == "linx_cpu_pyc":
        lint_sources = [ROOT / "examples" / "generated" / "linx_cpu_pyc" / "linx_cpu_pyc.v"]

    top = {
        "fastfwd_pyc": "EXAM2021_TOP",
        "issue_queue_2picker": "issue_queue_2picker",
        "linx_cpu_pyc": "linx_cpu_pyc",
    }[args.design]

    cmd = [verilator, "--lint-only", "-Wall", "-Wno-fatal", "--quiet", "--top-module", top]
    for inc in sim.include_dirs:
        cmd += ["-I" + str(inc)]
    cmd += [str(s) for s in lint_sources]
    run(cmd, cwd=ROOT)
    return 0


def cmd_wave(args: argparse.Namespace) -> int:
    gtkwave = which("gtkwave")
    if not gtkwave:
        die("missing gtkwave (install with: brew install gtkwave)")
    run([gtkwave, args.vcd], cwd=ROOT)
    return 0


def _fastfwd_detect_nfe(vfile: Path) -> int:
    txt = vfile.read_text(encoding="utf-8", errors="ignore")
    idx = [int(m.group(1)) for m in re.finditer(r"\bfwded(\d+)_pkt_data_vld\b", txt)]
    return (max(idx) + 1) if idx else 0


def _fastfwd_write_exam_top(path: Path, nfe: int) -> None:
    lines: list[str] = []
    lines.append("// Generated by pyc_flow.py (fastfwd-crosscheck)")
    lines.append("// Top wrapper: EXAM2021_TOP")
    lines.append("module EXAM2021_TOP(")
    lines.append("  input clk,")
    lines.append("  input rst_n,")
    for i in range(4):
        lines.append(f"  input lane{i}_pkt_in_vld,")
    for i in range(4):
        lines.append(f"  input [127:0] lane{i}_pkt_in_data,")
    for i in range(4):
        comma = "," if i != 3 else ","
        lines.append(f"  input [4:0] lane{i}_pkt_in_ctrl{comma}")
    for i in range(4):
        lines.append(f"  output lane{i}_pkt_out_vld,")
    for i in range(4):
        comma = "," if i != 3 else ","
        lines.append(f"  output [127:0] lane{i}_pkt_out_data{comma}")
    lines.append("  output reg pkt_in_bkpr")
    lines.append(");")
    lines.append(f"  localparam integer N_FE = {nfe};")
    lines.append("")
    lines.append("  wire rst;")
    lines.append("  assign rst = ~rst_n;")
    lines.append("")
    lines.append("  // Internal FE buses.")
    lines.append("  wire [N_FE-1:0]       fwd_pkt_data_vld;")
    lines.append("  wire [N_FE*128-1:0]   fwd_pkt_data;")
    lines.append("  wire [N_FE*2-1:0]     fwd_pkt_lat;")
    lines.append("  wire [N_FE-1:0]       fwd_pkt_dp_vld;")
    lines.append("  wire [N_FE*128-1:0]   fwd_pkt_dp_data;")
    lines.append("  wire [N_FE-1:0]       fwded_pkt_data_vld;")
    lines.append("  wire [N_FE*128-1:0]   fwded_pkt_data;")
    lines.append("")
    lines.append("  wire pkt_in_bkpr_w;")
    lines.append("")
    lines.append("  FastFwd U_CORE (")
    lines.append("    .clk(clk),")
    lines.append("    .rst(rst),")
    for i in range(4):
        lines.append(f"    .lane{i}_pkt_in_vld(lane{i}_pkt_in_vld),")
    for i in range(4):
        lines.append(f"    .lane{i}_pkt_in_data(lane{i}_pkt_in_data),")
    for i in range(4):
        lines.append(f"    .lane{i}_pkt_in_ctrl(lane{i}_pkt_in_ctrl),")
    for e in range(nfe):
        lines.append(f"    .fwded{e}_pkt_data_vld(fwded_pkt_data_vld[{e}]),")
        lines.append(f"    .fwded{e}_pkt_data(fwded_pkt_data[{e}*128+127:{e}*128]),")
    lines.append("    .pkt_in_bkpr(pkt_in_bkpr_w),")
    for i in range(4):
        lines.append(f"    .lane{i}_pkt_out_vld(lane{i}_pkt_out_vld),")
        lines.append(f"    .lane{i}_pkt_out_data(lane{i}_pkt_out_data),")
    for e in range(nfe):
        tail = "," if e != (nfe - 1) else ""
        lines.append(f"    .fwd{e}_pkt_data_vld(fwd_pkt_data_vld[{e}]),")
        lines.append(f"    .fwd{e}_pkt_data(fwd_pkt_data[{e}*128+127:{e}*128]),")
        lines.append(f"    .fwd{e}_pkt_lat(fwd_pkt_lat[{e}*2+1:{e}*2]),")
        lines.append(f"    .fwd{e}_pkt_dp_vld(fwd_pkt_dp_vld[{e}]),")
        lines.append(f"    .fwd{e}_pkt_dp_data(fwd_pkt_dp_data[{e}*128+127:{e}*128]){tail}")
    lines.append("  );")
    lines.append("")
    lines.append("  always @(*) begin")
    lines.append("    pkt_in_bkpr = pkt_in_bkpr_w;")
    lines.append("  end")
    lines.append("")
    lines.append("  genvar i;")
    lines.append("  generate")
    lines.append("    for (i = 0; i < N_FE; i = i + 1) begin: FE")
    lines.append("      FE U_FE (")
    lines.append("        .clk(clk),")
    lines.append("        .rst_n(rst_n),")
    lines.append("        .fwd_pkt_data_vld(fwd_pkt_data_vld[i]),")
    lines.append("        .fwd_pkt_data(fwd_pkt_data[i*128+127:i*128]),")
    lines.append("        .fwd_pkt_lat(fwd_pkt_lat[i*2+1:i*2]),")
    lines.append("        .fwd_pkt_dp_vld(fwd_pkt_dp_vld[i]),")
    lines.append("        .fwd_pkt_dp_data(fwd_pkt_dp_data[i*128+127:i*128]),")
    lines.append("        .fwded_pkt_data_vld(fwded_pkt_data_vld[i]),")
    lines.append("        .fwded_pkt_data(fwded_pkt_data[i*128+127:i*128])")
    lines.append("      );")
    lines.append("    end")
    lines.append("  endgenerate")
    lines.append("endmodule")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_fastfwd_out_trace(path: Path) -> list[tuple[int, int, int, int]]:
    events: list[tuple[int, int, int, int]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 5:
            continue
        cyc = int(parts[1], 0)
        lane = int(parts[2], 0)
        seq = int(parts[3], 0)
        data = int(parts[4], 16)
        events.append((cyc, lane, seq, data))
    return events


def cmd_fastfwd_crosscheck(args: argparse.Namespace) -> int:
    pyc_compile = detect_pyc_compile()

    cxx = os.environ.get("CXX", "clang++")
    if not which(cxx):
        warn(f"CXX={cxx!r} not found on PATH; falling back to clang++")
        cxx = "clang++"

    sim_tool = args.tool
    if sim_tool == "iverilog":
        iverilog = which("iverilog") or die("missing iverilog (install with: brew install icarus-verilog)")
        vvp = which("vvp") or die("missing vvp (install with: brew install icarus-verilog)")
    else:
        verilator = which("verilator") or die("missing verilator (install with: brew install verilator)")

    out_dir = ROOT / "examples" / "generated" / "fastfwd_pyc" / "crosscheck"
    out_dir.mkdir(parents=True, exist_ok=True)

    stim_path = out_dir / "stim.log"
    cpp_out = out_dir / "cpp_out.log"
    sv_out = out_dir / "sv_out.log"

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = pythonpath_env()

    with tempfile.TemporaryDirectory(prefix="pyc_fastfwd_xchk.") as td_str:
        td = Path(td_str)
        pyc_path = td / "fastfwd_pyc.pyc"
        v_path = td / "fastfwd_pyc.v"
        h_path = td / "fastfwd_pyc_gen.hpp"
        top_path = td / "exam2021_top.v"
        fe_path = td / "fe.v"

        emit_cmd = [
            "python3",
            "-m",
            "pycircuit.cli",
            "emit",
            str(ROOT / "examples" / "fastfwd_pyc" / "fastfwd_pyc.py"),
        ]
        for p in args.param:
            emit_cmd += ["--param", p]
        emit_cmd += ["-o", str(pyc_path)]
        run(emit_cmd, cwd=ROOT, env=env)

        run([str(pyc_compile), str(pyc_path), "--emit=verilog", "-o", str(v_path)], cwd=ROOT, env=env)
        run([str(pyc_compile), str(pyc_path), "--emit=cpp", "-o", str(h_path)], cwd=ROOT, env=env)

        nfe = _fastfwd_detect_nfe(v_path)
        if nfe <= 0:
            die("failed to detect FE count from generated fastfwd_pyc.v")
        if nfe > 32:
            die(f"invalid FE count (must be <= 32): {nfe}")

        _fastfwd_write_exam_top(top_path, nfe)
        shutil.copyfile(str(ROOT / "examples" / "fastfwd_pyc" / "fe.v"), str(fe_path))

        # Build + run C++ tick model: generate stim + output trace.
        tb_cpp = td / "tb_fastfwd_pyc_cpp"
        run(
            [
                cxx,
                "-std=c++17",
                "-O2",
                f"-DFASTFWD_TOTAL_ENG={nfe}",
                "-I",
                str(ROOT / "include"),
                "-I",
                str(td),
                "-o",
                str(tb_cpp),
                str(ROOT / "examples" / "fastfwd_pyc" / "tb_fastfwd_pyc.cpp"),
            ],
            cwd=ROOT,
            env=env,
        )
        run(
            [
                str(tb_cpp),
                "--seed",
                str(args.seed),
                "--cycles",
                str(args.cycles),
                "--packets",
                str(args.packets),
                "--stim-out",
                str(stim_path),
                "--out-trace",
                str(cpp_out),
            ],
            cwd=ROOT,
            env=env,
        )

        # Build + run Verilog sim (SV TB + generated RTL) using the same stim.
        if sim_tool == "iverilog":
            vvp_out = td / "tb_fastfwd_pyc.vvp"
            cmd = [iverilog, "-g2012", "-I", str(ROOT / "include" / "pyc" / "verilog")]
            cmd += ["-o", str(vvp_out)]
            cmd += [
                str(ROOT / "examples" / "fastfwd_pyc" / "tb_fastfwd_pyc.sv"),
                str(top_path),
                str(v_path),
                str(fe_path),
            ]
            run(cmd, cwd=ROOT, env=env)
            run(
                [
                    vvp,
                    str(vvp_out),
                    f"+max_cycles={args.cycles}",
                    f"+max_pkts={args.packets}",
                    f"+stim={stim_path}",
                    f"+out_trace={sv_out}",
                    "+notrace",
                    "+nolog",
                ],
                cwd=ROOT,
                env=env,
            )
        else:
            build_dir = td / "verilator"
            build_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                verilator,
                "--binary",
                "-Wall",
                "-Wno-fatal",
                "--quiet",
                "--quiet-build",
                "--timing",
                "--top-module",
                "tb_fastfwd_pyc",
                "--Mdir",
                str(build_dir),
                "-I" + str(ROOT / "include" / "pyc" / "verilog"),
                str(ROOT / "examples" / "fastfwd_pyc" / "tb_fastfwd_pyc.sv"),
                str(top_path),
                str(v_path),
                str(fe_path),
            ]
            run(cmd, cwd=ROOT, env=env)
            exe = build_dir / "Vtb_fastfwd_pyc"
            if not exe.is_file():
                die(f"verilator did not produce expected binary: {exe}")
            run(
                [
                    str(exe),
                    f"+max_cycles={args.cycles}",
                    f"+max_pkts={args.packets}",
                    f"+stim={stim_path}",
                    f"+out_trace={sv_out}",
                    "+notrace",
                    "+nolog",
                ],
                cwd=ROOT,
                env=env,
            )

    # Compare output streams (lane/seq/data), and require a constant cycle offset.
    cpp_events = _parse_fastfwd_out_trace(cpp_out)
    sv_events = _parse_fastfwd_out_trace(sv_out)
    if len(cpp_events) != len(sv_events):
        die(f"FastFwd crosscheck mismatch: cpp_events={len(cpp_events)} sv_events={len(sv_events)}")

    cycle_deltas: set[int] = set()
    for i, (ce, ve) in enumerate(zip(cpp_events, sv_events)):
        # ce/ve: (cyc, lane, seq, data)
        if ce[1:] != ve[1:]:
            die(f"FastFwd crosscheck mismatch at out_index={i}: cpp={ce} sv={ve}")
        cycle_deltas.add(ve[0] - ce[0])
    if len(cycle_deltas) > 1:
        die(f"FastFwd timing mismatch: non-constant sv_cycle-cpp_cycle deltas={sorted(cycle_deltas)}")
    delta = next(iter(cycle_deltas)) if cycle_deltas else 0

    log(f"ok: fastfwd-crosscheck (events={len(cpp_events)} sv_cycle-cpp_cycle={delta})")
    log(f"stim: {stim_path}")
    log(f"cpp_out: {cpp_out}")
    log(f"sv_out: {sv_out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pyc-flow", description="pyCircuit universal flow runner (regen, sim, lint).")
    sub = p.add_subparsers(dest="cmd", required=True)

    doctor = sub.add_parser("doctor", help="Check for required external tools.")
    doctor.set_defaults(fn=cmd_doctor)

    regen = sub.add_parser("regen", help="Regenerate checked-in outputs (examples/ + janus/).")
    regen.add_argument("--examples", action="store_true", help="Regenerate examples/generated/*")
    regen.add_argument("--janus", action="store_true", help="Regenerate janus/generated/*")
    regen.add_argument("--fastfwd-nfe", type=int, default=None, help="Override FastFwd FE count (multiple of 4, <= 32)")
    regen.set_defaults(fn=cmd_regen)

    cpp = sub.add_parser("cpp-test", help="Run C++ tick-model regressions (CA models).")
    cpp.add_argument("--fastfwd", action="store_true", help="Run FastFwd C++ regression")
    cpp.add_argument("--cpu", action="store_true", help="Run Linx CPU C++ regression")
    cpp.add_argument("--janus", action="store_true", help="Run Janus C++ regressions")
    cpp.set_defaults(fn=cmd_cpp_test)

    vsim = sub.add_parser("verilog-sim", help="Simulate generated Verilog with Icarus or Verilator.")
    vsim.add_argument("design", choices=sorted(verilog_sims().keys()))
    vsim.add_argument("--tool", choices=["iverilog", "verilator"], default="iverilog")
    vsim.add_argument("sim_args", nargs="*", help="Arguments passed to the simulator (e.g. +vcd=...)")
    vsim.set_defaults(fn=cmd_verilog_sim)

    vsimm = sub.add_parser("verilog-sim-manifest", help="Simulate Verilog using a manifest.json from --out-dir emission.")
    vsimm.add_argument("--manifest", required=True, help="Path to out-dir manifest.json")
    vsimm.add_argument("--tb", required=True, help="Path to SystemVerilog testbench (.sv)")
    vsimm.add_argument("--top", default=None, help="Top module name (default: tb_<top> from manifest)")
    vsimm.add_argument("--tool", choices=["iverilog", "verilator"], default="iverilog")
    vsimm.add_argument("--include-dir", action="append", default=[], help="Extra include directory (repeatable)")
    vsimm.add_argument("sim_args", nargs="*", help="Arguments passed to the simulator (e.g. +vcd=...)")
    vsimm.set_defaults(fn=cmd_verilog_sim_manifest)

    vlint = sub.add_parser("verilog-lint", help="Lint generated Verilog with Verilator.")
    vlint.add_argument("design", choices=sorted(verilog_sims().keys()))
    vlint.set_defaults(fn=cmd_verilog_lint)

    wave = sub.add_parser("wave", help="Open a VCD in GTKWave.")
    wave.add_argument("vcd", help="Path to a .vcd file")
    wave.set_defaults(fn=cmd_wave)

    xchk = sub.add_parser("fastfwd-crosscheck", help="Cross-check FastFwd C++ vs Verilog using identical stimulus.")
    xchk.add_argument("--tool", choices=["iverilog", "verilator"], default="iverilog", help="Verilog simulator backend")
    xchk.add_argument("--seed", type=int, default=1)
    xchk.add_argument("--cycles", type=int, default=200)
    xchk.add_argument("--packets", type=int, default=400)
    xchk.add_argument("--param", action="append", default=[], help="JIT param override for FastFwd (repeatable): name=value")
    xchk.set_defaults(fn=cmd_fastfwd_crosscheck)

    ns = p.parse_args(argv)

    # Convenience defaults.
    if ns.cmd == "regen" and not (ns.examples or ns.janus):
        ns.examples = True
        ns.janus = True
    if ns.cmd == "cpp-test" and not (ns.fastfwd or ns.cpu or ns.janus):
        ns.fastfwd = True
        ns.cpu = True
        ns.janus = True

    return int(ns.fn(ns))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
