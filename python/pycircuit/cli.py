from __future__ import annotations

import argparse
import ast
import importlib.util
import inspect
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .dsl import Module
from .design import Design, DesignError
from .jit import compile_design
from .tb import Tb, TbError, _sanitize_id


def _default_top_name(src: Path) -> str:
    parts = [p for p in src.stem.replace("-", "_").split("_") if p]
    if not parts:
        return "Top"
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _load_py_file(path: Path) -> object:
    path = path.resolve()
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to import {path}")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _cmd_emit(args: argparse.Namespace) -> int:
    src_arg = args.python_file
    out = Path(args.output)
    # If argument looks like a module name (contains dot), import it (supports relative imports).
    if "." in src_arg and not Path(src_arg).exists():
        mod = importlib.import_module(src_arg)
        src = Path(src_arg.replace(".", "/") + ".py")  # for _default_top_name
    else:
        src = Path(src_arg)
        mod = _load_py_file(src)
    if not hasattr(mod, "build"):
        raise SystemExit(f"{src_arg} must define build() -> pycircuit.Module")
    build = getattr(mod, "build")

    # JIT-by-default behavior:
    # - If `build` is a function that accepts a builder arg (e.g. `build(m: Circuit, ...)`),
    #   compile it via the AST/JIT frontend using default parameter values.
    # - Otherwise, call it normally and expect a `Module` result (legacy path).
    if callable(build):
        sig = inspect.signature(build)
        params = list(sig.parameters.values())
        if params:
            # Collect JIT-time parameters from defaults.
            jit_params: dict[str, object] = {}
            missing: list[str] = []
            for p in params[1:]:
                if p.default is inspect._empty:
                    missing.append(p.name)
                else:
                    jit_params[p.name] = p.default
            if missing:
                raise SystemExit(
                    f"build() is treated as a JIT design function but missing default values for: {', '.join(missing)}"
                )

            # Apply CLI overrides.
            for spec in args.param:
                if "=" not in spec:
                    raise SystemExit(f"--param expects name=value, got: {spec!r}")
                name, raw = spec.split("=", 1)
                name = name.strip()
                raw = raw.strip()
                if not name:
                    raise SystemExit(f"--param expects name=value, got: {spec!r}")
                if name not in jit_params:
                    raise SystemExit(f"unknown JIT parameter: {name!r} (available: {', '.join(jit_params.keys())})")
                try:
                    val = ast.literal_eval(raw)
                except Exception:
                    val = raw
                jit_params[name] = val

            name = _default_top_name(src)
            override = getattr(build, "__pycircuit_name__", None)
            if isinstance(override, str) and override.strip():
                name = override.strip()
            try:
                m = compile_design(build, name=name, **jit_params)
            except DesignError as e:
                raise SystemExit(f"design compile failed: {e}") from e
        else:
            m = build()
    else:
        m = build

    if isinstance(m, Module):
        out.write_text(m.emit_mlir(), encoding="utf-8")
        return 0
    if isinstance(m, Design):
        out.write_text(m.emit_mlir(), encoding="utf-8")
        return 0

    raise SystemExit("build must be a pycircuit.Module, a build() -> Module function, or a JIT build(m, ...) function")
    return 0


def _detect_pyc_compile() -> Path:
    env = os.environ.get("PYC_COMPILE")
    if env:
        p = Path(env)
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise SystemExit(f"PYC_COMPILE is set but not executable: {p}")

    # Prefer in-tree builds when running from the repo.
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "build-top" / "bin" / "pyc-compile",
        root / "build" / "bin" / "pyc-compile",
        root / "pyc" / "mlir" / "build" / "bin" / "pyc-compile",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c

    found = shutil.which("pyc-compile")
    if found:
        return Path(found)

    raise SystemExit("missing pyc-compile (set PYC_COMPILE=... or build it with: scripts/pyc build)")


def _as_int_width(ty: str) -> int:
    if ty == "!pyc.clock" or ty == "!pyc.reset":
        return 1
    if not ty.startswith("i"):
        raise SystemExit(f"unsupported port type for TB generation: {ty!r}")
    return int(ty[1:])


def _collect_build(mod: object, src: Path, args: argparse.Namespace) -> Module | Design:
    if not hasattr(mod, "build"):
        raise SystemExit(f"{src} must define build() -> pycircuit.Module")
    build = getattr(mod, "build")

    if callable(build):
        sig = inspect.signature(build)
        params = list(sig.parameters.values())
        if params:
            # Collect JIT-time parameters from defaults.
            jit_params: dict[str, object] = {}
            missing: list[str] = []
            for p in params[1:]:
                if p.default is inspect._empty:
                    missing.append(p.name)
                else:
                    jit_params[p.name] = p.default
            if missing:
                raise SystemExit(
                    f"build() is treated as a JIT design function but missing default values for: {', '.join(missing)}"
                )

            # Apply CLI overrides.
            for spec in args.param:
                if "=" not in spec:
                    raise SystemExit(f"--param expects name=value, got: {spec!r}")
                name, raw = spec.split("=", 1)
                name = name.strip()
                raw = raw.strip()
                if not name:
                    raise SystemExit(f"--param expects name=value, got: {spec!r}")
                if name not in jit_params:
                    raise SystemExit(f"unknown JIT parameter: {name!r} (available: {', '.join(jit_params.keys())})")
                try:
                    val = ast.literal_eval(raw)
                except Exception:
                    val = raw
                jit_params[name] = val

            name = _default_top_name(src)
            override = getattr(build, "__pycircuit_name__", None)
            if isinstance(override, str) and override.strip():
                name = override.strip()
            try:
                return compile_design(build, name=name, **jit_params)
            except DesignError as e:
                raise SystemExit(f"design compile failed: {e}") from e
        return build()

    return build


class _TopIface:
    def __init__(self, *, sym: str, in_raw: list[str], in_tys: list[str], out_raw: list[str], out_tys: list[str]) -> None:
        self.sym = str(sym)
        self.in_raw = list(in_raw)
        self.in_tys = list(in_tys)
        self.out_raw = list(out_raw)
        self.out_tys = list(out_tys)

        all_raw = [*self.in_raw, *self.out_raw]
        if len(set(all_raw)) != len(all_raw):
            raise SystemExit("TB generation requires unique port names across inputs and outputs")

        used: dict[str, int] = {}
        all_names: list[str] = []
        for r in all_raw:
            base = _sanitize_id(r)
            n = used.get(base, 0) + 1
            used[base] = n
            all_names.append(base if n == 1 else f"{base}_{n}")
        self.in_names = all_names[: len(self.in_raw)]
        self.out_names = all_names[len(self.in_raw) :]

        self._by_raw: dict[str, tuple[str, str, str]] = {}
        for rn, sn, ty in zip(self.in_raw, self.in_names, self.in_tys):
            self._by_raw[rn] = ("in", sn, ty)
        for rn, sn, ty in zip(self.out_raw, self.out_names, self.out_tys):
            self._by_raw[rn] = ("out", sn, ty)

    def resolve(self, raw_name: str) -> tuple[str, str, str]:
        r = str(raw_name).strip()
        if r not in self._by_raw:
            raise SystemExit(f"unknown DUT port referenced by TB: {r!r}")
        return self._by_raw[r]


def _top_iface(design: Module | Design) -> _TopIface:
    if isinstance(design, Design):
        cm = design.lookup(design.top)
        if cm is None:
            raise SystemExit(f"internal: missing top module {design.top!r} in Design")
        return _TopIface(
            sym=cm.sym_name,
            in_raw=list(cm.arg_names),
            in_tys=list(cm.arg_types),
            out_raw=list(cm.result_names),
            out_tys=list(cm.result_types),
        )

    in_raw = [n for n, _ in getattr(design, "_args", [])]  # noqa: SLF001
    in_tys = [sig.ty for _, sig in getattr(design, "_args", [])]  # noqa: SLF001
    out_raw = [n for n, _ in getattr(design, "_results", [])]  # noqa: SLF001
    out_tys = [sig.ty for _, sig in getattr(design, "_results", [])]  # noqa: SLF001
    return _TopIface(sym=str(getattr(design, "name", "Top")), in_raw=in_raw, in_tys=in_tys, out_raw=out_raw, out_tys=out_tys)


def _render_tb_cpp(iface: _TopIface, t: Tb) -> str:
    if not t.clocks:
        raise SystemExit("tb() must specify at least one clock via t.clock(...)")
    if t.reset_spec is None:
        raise SystemExit("tb() must specify a reset via t.reset(...)")

    top = _sanitize_id(iface.sym)
    hdr = f"{iface.sym}.hpp"

    def mask_value(v: int | bool, width: int) -> int:
        if isinstance(v, bool):
            vv = 1 if v else 0
        else:
            vv = int(v)
        if width <= 0:
            raise SystemExit("internal: invalid width")
        if width > 64:
            if vv < 0 or vv > ((1 << 64) - 1):
                raise SystemExit(f"TB constant for i{width} must fit in 64 bits (prototype limitation)")
            return vv
        mask = (1 << width) - 1 if width < 64 else (1 << 64) - 1
        return vv & mask

    # Group actions by cycle for compact emission.
    drives_by: dict[int, list[tuple[str, int | bool, str]]] = {}
    expects_by: dict[int, list[tuple[str, int | bool, str | None, str]]] = {}
    for d in t.drives:
        dir_, sn, ty = iface.resolve(d.port)
        if dir_ != "in":
            raise SystemExit(f"drive() requires input port, got output: {d.port!r}")
        drives_by.setdefault(int(d.at), []).append((sn, d.value, ty))
    for e in t.expects:
        _dir, sn, ty = iface.resolve(e.port)
        expects_by.setdefault(int(e.at), []).append((sn, e.value, e.msg, ty))

    rand_specs: list[tuple[str, int, int, int, int]] = []
    if t.random_streams:
        used_ports: set[str] = set()
        for r in t.random_streams:
            dir_, sn, ty = iface.resolve(r.port)
            if dir_ != "in":
                raise SystemExit(f"random() requires input port, got output: {r.port!r}")
            if ty == "!pyc.clock" or ty == "!pyc.reset":
                raise SystemExit(f"random() cannot target clock/reset ports: {r.port!r}")
            if sn in used_ports:
                raise SystemExit(f"duplicate random() stream for port: {r.port!r}")
            used_ports.add(sn)
            w = _as_int_width(ty)
            if w > 64:
                raise SystemExit(f"random() for i{w} not supported in C++ TB generator (prototype limitation)")
            rand_specs.append((sn, w, int(r.seed), int(r.start), int(r.every)))

    clk = t.clocks[0].port
    rst = t.reset_spec.port
    _, clk_sn, _clk_ty = iface.resolve(clk)
    _, rst_sn, _rst_ty = iface.resolve(rst)

    ca = t.reset_spec.cycles_asserted
    cd = t.reset_spec.cycles_deasserted

    lines: list[str] = []
    lines.append("// Generated by pycircuit testgen (prototype)\n")
    lines.append("#include <cstdint>\n")
    lines.append("#include <cstdlib>\n")
    lines.append("#include <filesystem>\n")
    lines.append("#include <iostream>\n\n")
    lines.append("#include <pyc/cpp/pyc_tb.hpp>\n\n")
    lines.append(f"#include \"{hdr}\"\n\n")
    lines.append("using pyc::cpp::Testbench;\n\n")
    lines.append("int main() {\n")
    lines.append(f"  pyc::gen::{top} dut;\n")
    lines.append(f"  Testbench<pyc::gen::{top}> tb(dut);\n\n")
    if rand_specs:
        lines.append("  // Random streams (deterministic).\n")
        for sn, _w, seed, _st, _ev in rand_specs:
            seed64 = int(seed) & ((1 << 64) - 1)
            lines.append(f"  std::uint64_t rng_{sn} = 0x{seed64:x}ull;\n")
        lines.append("\n")
    lines.append("  // Optional traces.\n")
    lines.append("  const char *trace_dir_env = std::getenv(\"PYC_TRACE_DIR\");\n")
    lines.append(
        "  std::filesystem::path out_dir = trace_dir_env ? std::filesystem::path(trace_dir_env) : std::filesystem::path(\".\");\n"
    )
    lines.append(f"  out_dir /= \"tb_{iface.sym}\";\n")
    lines.append("  std::filesystem::create_directories(out_dir);\n")
    lines.append(f"  tb.enableVcd((out_dir / \"tb_{iface.sym}.vcd\").string(), /*top=*/\"tb_{iface.sym}\");\n")
    for sn in [*iface.in_names, *iface.out_names]:
        lines.append(f"  tb.vcdTrace(dut.{sn}, \"{sn}\");\n")
    lines.append("\n")

    for c in t.clocks:
        dir_, sn, _ = iface.resolve(c.port)
        if dir_ != "in":
            raise SystemExit(f"clock must be an input port, got output: {c.port!r}")
        lines.append(
            f"  tb.addClock(dut.{sn}, /*halfPeriodSteps=*/{int(c.half_period_steps)}, /*phaseSteps=*/{int(c.phase_steps)}, /*startHigh=*/{str(bool(c.start_high)).lower()});\n"
        )
    lines.append(f"  tb.reset(dut.{rst_sn}, /*cyclesAsserted=*/{int(ca)}, /*cyclesDeasserted=*/{int(cd)});\n\n")

    lines.append(f"  const std::uint64_t timeout_cycles = {int(t.timeout_cycles)}ull;\n")
    lines.append("  bool ok = false;\n")
    lines.append("  for (std::uint64_t cyc = 0; cyc < timeout_cycles; ++cyc) {\n")

    if rand_specs:
        lines.append("    // Random drives for this cycle (applied before explicit drives).\n")
        for sn, w, _seed, st, ev in rand_specs:
            mask = (1 << w) - 1 if w < 64 else (1 << 64) - 1
            lines.append(
                f"    if (cyc >= {int(st)}ull && ((cyc - {int(st)}ull) % {int(ev)}ull) == 0ull) {{\n"
                f"      rng_{sn} = rng_{sn} * 6364136223846793005ull + 1ull;\n"
                f"      dut.{sn} = pyc::cpp::Wire<{w}>(0x{mask:x}ull & rng_{sn});\n"
                f"    }}\n"
            )
        lines.append("\n")

    if drives_by:
        lines.append("    switch (cyc) {\n")
        for cyc in sorted(drives_by.keys()):
            lines.append(f"    case {cyc}:\n")
            for sn, val, ty in drives_by[cyc]:
                w = _as_int_width(ty)
                vv = mask_value(val, w)
                lines.append(f"      dut.{sn} = pyc::cpp::Wire<{w}>(0x{vv:x}ull);\n")
            lines.append("      break;\n")
        lines.append("    default: break;\n")
        lines.append("    }\n")

    lines.append("    dut.eval();\n")
    lines.append("    tb.runCycles(1);\n")

    if expects_by:
        lines.append("    switch (cyc) {\n")
        for cyc in sorted(expects_by.keys()):
            lines.append(f"    case {cyc}: {{\n")
            for sn, val, msg, ty in expects_by[cyc]:
                w = _as_int_width(ty)
                if w > 64:
                    raise SystemExit(f"expect() for i{w} not supported in C++ TB generator (prototype limitation)")
                vv = mask_value(val, w)
                m = msg if msg is not None else f"{sn} mismatch"
                # Print decimal for i1, hex for wider signals.
                if w == 1:
                    lines.append(
                        f"      if (dut.{sn}.value() != {vv}u) {{ std::cerr << \"ERROR: {m}: got=\" << dut.{sn}.value() << \" exp={vv}\\n\"; return 1; }}\n"
                    )
                else:
                    lines.append(
                        f"      if (dut.{sn}.value() != {vv}u) {{ std::cerr << \"ERROR: {m}: got=0x\" << std::hex << dut.{sn}.value() << \" exp=0x{vv:x}\" << std::dec << \"\\n\"; return 1; }}\n"
                    )
            lines.append("      break; }\n")
        lines.append("    default: break;\n")
        lines.append("    }\n")

    if t.finish_cycle is not None:
        lines.append(f"    if (cyc == {int(t.finish_cycle)}ull) {{ ok = true; break; }}\n")

    lines.append("  }\n")
    lines.append("  if (!ok) { std::cerr << \"TIMEOUT\\n\"; return 1; }\n")
    lines.append("  std::cerr << \"OK\\n\";\n")
    lines.append("  return 0;\n")
    lines.append("}\n")
    return "".join(lines)


def _render_tb_sv(iface: _TopIface, t: Tb) -> str:
    if not t.clocks:
        raise SystemExit("tb() must specify at least one clock via t.clock(...)")
    if t.reset_spec is None:
        raise SystemExit("tb() must specify a reset via t.reset(...)")

    top = str(iface.sym)
    mod_name = top  # func sym name is already a valid Verilog identifier in this repo.

    def sv_lit(width: int, v: int | bool) -> str:
        if isinstance(v, bool):
            vv = 1 if v else 0
        else:
            vv = int(v)
        if width <= 0:
            raise SystemExit("internal: invalid width")
        vv &= (1 << width) - 1
        if width == 1:
            return f"1'b{vv}"
        return f"{width}'h{vv:x}"

    def decl(name: str, ty: str) -> str:
        w = _as_int_width(ty)
        if w == 1:
            return f"  logic {name};\n"
        return f"  logic [{w - 1}:0] {name};\n"

    drives_by: dict[int, list[tuple[str, int | bool, str]]] = {}
    expects_by: dict[int, list[tuple[str, int | bool, str | None, str]]] = {}
    for d in t.drives:
        dir_, sn, ty = iface.resolve(d.port)
        if dir_ != "in":
            raise SystemExit(f"drive() requires input port, got output: {d.port!r}")
        drives_by.setdefault(int(d.at), []).append((sn, d.value, ty))
    for e in t.expects:
        _dir, sn, ty = iface.resolve(e.port)
        expects_by.setdefault(int(e.at), []).append((sn, e.value, e.msg, ty))

    rand_specs: list[tuple[str, int, int, int, int]] = []
    if t.random_streams:
        used_ports: set[str] = set()
        for r in t.random_streams:
            dir_, sn, ty = iface.resolve(r.port)
            if dir_ != "in":
                raise SystemExit(f"random() requires input port, got output: {r.port!r}")
            if ty == "!pyc.clock" or ty == "!pyc.reset":
                raise SystemExit(f"random() cannot target clock/reset ports: {r.port!r}")
            if sn in used_ports:
                raise SystemExit(f"duplicate random() stream for port: {r.port!r}")
            used_ports.add(sn)
            w = _as_int_width(ty)
            if w > 64:
                raise SystemExit(f"random() for i{w} not supported in SV TB generator (prototype limitation)")
            rand_specs.append((sn, w, int(r.seed), int(r.start), int(r.every)))

    clk = t.clocks[0].port
    rst = t.reset_spec.port
    _, clk_sn, _clk_ty = iface.resolve(clk)
    _, rst_sn, _rst_ty = iface.resolve(rst)

    ca = t.reset_spec.cycles_asserted
    cd = t.reset_spec.cycles_deasserted

    lines: list[str] = []
    lines.append("// Generated by pycircuit testgen (prototype)\n")
    lines.append("`timescale 1ns/1ps\n\n")
    lines.append(f"module tb_{top};\n")

    for n, ty in zip(iface.in_names, iface.in_tys):
        lines.append(decl(n, ty))
    for n, ty in zip(iface.out_names, iface.out_tys):
        lines.append(decl(n, ty))
    if rand_specs:
        lines.append("\n")
        lines.append("  // Random stream state.\n")
        for sn, _w, _seed, _st, _ev in rand_specs:
            lines.append(f"  longint unsigned rng_{sn};\n")
    lines.append("\n")

    lines.append(f"  {mod_name} dut (\n")
    conns = [f"    .{sn}({sn})" for sn in [*iface.in_names, *iface.out_names]]
    lines.append(",\n".join(conns))
    lines.append("\n  );\n\n")

    # Clock generation: currently only supports the first clock.
    hp = int(t.clocks[0].half_period_steps) if t.clocks else 1
    if hp != 1:
        lines.append("  // NOTE: half_period_steps != 1 is approximated by scaling delay.\n")
    lines.append("  initial begin\n")
    lines.append(f"    {clk_sn} = {1 if (t.clocks and t.clocks[0].start_high) else 0};\n")
    lines.append("  end\n")
    lines.append(f"  always #{hp} {clk_sn} = ~{clk_sn};\n\n")

    # Main stimulus loop.
    lines.append("  initial begin\n")
    # Initialize all driven inputs to 0.
    for sn, ty in zip(iface.in_names, iface.in_tys):
        if sn == clk_sn:
            continue
        w = _as_int_width(ty)
        lines.append(f"    {sn} = {w}'d0;\n")
    if rand_specs:
        lines.append("\n")
        lines.append("    // Random stream seeds.\n")
        for sn, _w, seed, _st, _ev in rand_specs:
            seed64 = int(seed) & ((1 << 64) - 1)
            lines.append(f"    rng_{sn} = 64'h{seed64:016x};\n")
    lines.append("\n")
    lines.append(f"    {rst_sn} = 1'b1;\n")
    lines.append(f"    repeat ({int(ca)}) @(posedge {clk_sn});\n")
    lines.append(f"    {rst_sn} = 1'b0;\n")
    lines.append(f"    repeat ({int(cd)}) @(posedge {clk_sn});\n\n")

    lines.append(f"    int unsigned timeout_cycles = {int(t.timeout_cycles)};\n")
    lines.append("    for (int unsigned cyc = 0; cyc < timeout_cycles; cyc++) begin\n")

    if rand_specs:
        lines.append("      // Random drives for this cycle (applied before explicit drives).\n")
        for sn, w, _seed, st, ev in rand_specs:
            hi = 63 if w >= 64 else (w - 1)
            lines.append(f"      if (cyc >= {int(st)} && (((cyc - {int(st)}) % {int(ev)}) == 0)) begin\n")
            lines.append("        // LCG: state = state * 6364136223846793005 + 1.\n")
            lines.append(f"        rng_{sn} = (rng_{sn} * 64'd6364136223846793005) + 64'd1;\n")
            lines.append(f"        {sn} = rng_{sn}[{hi}:0];\n")
            lines.append("      end\n")
        lines.append("\n")

    if drives_by:
        lines.append("      // Drives for this cycle (applied before posedge).\n")
        lines.append("      unique case (cyc)\n")
        for cyc in sorted(drives_by.keys()):
            lines.append(f"        {cyc}: begin\n")
            for sn, val, ty in drives_by[cyc]:
                w = _as_int_width(ty)
                lines.append(f"          {sn} = {sv_lit(w, val)};\n")
            lines.append("        end\n")
        lines.append("        default: begin end\n")
        lines.append("      endcase\n")

    lines.append(f"      @(posedge {clk_sn});\n")
    lines.append(f"      @(negedge {clk_sn});\n")

    if expects_by:
        lines.append("      // Expects for this cycle (checked after posedge updates).\n")
        lines.append("      unique case (cyc)\n")
        for cyc in sorted(expects_by.keys()):
            lines.append(f"        {cyc}: begin\n")
            for sn, val, msg, ty in expects_by[cyc]:
                w = _as_int_width(ty)
                m = msg if msg is not None else f"{sn} mismatch"
                lines.append(f"          if ({sn} !== {sv_lit(w, val)}) $fatal(1, \"{m}\");\n")
            lines.append("        end\n")
        lines.append("        default: begin end\n")
        lines.append("      endcase\n")

    if t.finish_cycle is not None:
        lines.append(f"      if (cyc == {int(t.finish_cycle)}) begin\n")
        lines.append("        $display(\"OK\");\n")
        lines.append("        $finish;\n")
        lines.append("      end\n")

    lines.append("    end\n")
    lines.append("    $fatal(1, \"TIMEOUT\");\n")
    lines.append("  end\n\n")

    # SVA assertions.
    if t.sva_asserts:
        lines.append("  // SVA assertions.\n")
        for i, a in enumerate(t.sva_asserts):
            nm = a.name or f"sva_{i}"
            clk_dir, clk_port, _ = iface.resolve(a.clock)
            if clk_dir != "in":
                raise SystemExit(f"sva_assert clock must be an input port, got output: {a.clock!r}")
            rst_expr = ""
            if a.reset:
                rst_dir, rst_port, _ = iface.resolve(a.reset)
                if rst_dir != "in":
                    raise SystemExit(f"sva_assert reset must be an input port, got output: {a.reset!r}")
                rst_expr = f" disable iff ({rst_port})"
            msg = a.msg or f"SVA {nm} failed"
            lines.append(f"  assert property (@(posedge {clk_port}){rst_expr} {a.expr}) else $fatal(1, \"{msg}\");\n")
        lines.append("\n")

    lines.append("endmodule\n")
    return "".join(lines)


def _cmd_testgen(args: argparse.Namespace) -> int:
    src = Path(args.python_file)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mod = _load_py_file(src)
    design = _collect_build(mod, src, args)
    iface = _top_iface(design)

    if not hasattr(mod, "tb") or not callable(getattr(mod, "tb")):
        raise SystemExit(f"{src} must define tb(t: pycircuit.tb.Tb) for testgen")
    tb_fn = getattr(mod, "tb")
    t = Tb()
    try:
        tb_fn(t)
    except TbError as e:
        raise SystemExit(f"tb() failed: {e}") from e

    # Emit design MLIR into out-dir.
    pyc_path = out_dir / f"{iface.sym}.pyc"
    if isinstance(design, Module):
        pyc_path.write_text(design.emit_mlir(), encoding="utf-8")
    else:
        pyc_path.write_text(design.emit_mlir(), encoding="utf-8")

    pyc_compile = _detect_pyc_compile()

    # Run pyc-compile twice (verilog + cpp) into the same out-dir.
    subprocess.run([str(pyc_compile), str(pyc_path), "--emit=verilog", f"--out-dir={out_dir}"], check=True)
    subprocess.run([str(pyc_compile), str(pyc_path), "--emit=cpp", f"--out-dir={out_dir}"], check=True)

    # Emit TB sources.
    tb_cpp = out_dir / f"tb_{iface.sym}.cpp"
    tb_sv = out_dir / f"tb_{iface.sym}.sv"
    tb_cpp.write_text(_render_tb_cpp(iface, t), encoding="utf-8")
    tb_sv.write_text(_render_tb_sv(iface, t), encoding="utf-8")

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pycircuit")
    sub = p.add_subparsers(dest="cmd", required=True)

    emit = sub.add_parser("emit", help="Emit PYC MLIR (*.pyc) from a Python design file.")
    emit.add_argument("python_file", help="Python source defining build() -> pycircuit.Module")
    emit.add_argument("-o", "--output", required=True, help="Output .pyc path")
    emit.add_argument(
        "--param",
        action="append",
        default=[],
        help="Override a JIT parameter (repeatable): name=value (parsed as a Python literal when possible)",
    )
    emit.set_defaults(fn=_cmd_emit)

    testgen = sub.add_parser("testgen", help="Generate per-module RTL + C++/SV testbench from a Python design file.")
    testgen.add_argument("python_file", help="Python source defining build() and tb(t: Tb)")
    testgen.add_argument("--out-dir", required=True, help="Output directory for RTL + testbench sources")
    testgen.add_argument(
        "--param",
        action="append",
        default=[],
        help="Override a JIT parameter (repeatable): name=value (parsed as a Python literal when possible)",
    )
    testgen.set_defaults(fn=_cmd_testgen)

    ns = p.parse_args(argv)
    return int(ns.fn(ns))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
