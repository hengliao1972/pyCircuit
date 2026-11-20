"""Stacked ForwardSignal.assign must fold into one next and match on both backends.

A stripped-down tile free list: four enqueue ports each do
``fifo[s].assign(tag, when=hit & valid)``. Those calls accumulate in order;
``when=0`` keeps the already-decided next, and codegen emits one driver.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# fifo[2] reset is 130; only port2 writes 274 into that slot.
RESET_TAG = 130
PORT2_TAG = 274
OBS_SLOT = 2
DEPTH = 32
PTR_W = 5
TAIL_RESET = 1  # first enqueue goes to slot tail+1 == 2
OBS_LINE = re.compile(rf"fifo_{OBS_SLOT}=([0-9a-fA-Fx]+)")

SOURCE = textwrap.dedent(
    f"""
    from pycircuit import (
        CycleAwareCircuit,
        CycleAwareDomain,
        CycleAwareTb,
        Tb,
        cas,
        compile_cycle_aware,
        mux,
        testbench,
        u,
        wire_of,
    )

    DEPTH = {DEPTH}
    PTR_W = {PTR_W}


    def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
        fifo = [
            domain.signal(width=9, reset_value=128 + i, name=f"fifo_{{i}}")
            for i in range(DEPTH)
        ]
        tail = domain.signal(width=PTR_W, reset_value={TAIL_RESET}, name="tail")
        valids = [cas(domain, m.input(f"valid{{i}}", width=1), cycle=0) for i in range(4)]
        tags = [cas(domain, m.input(f"tag{{i}}", width=9), cycle=0) for i in range(4)]
        new_tail = tail
        one = cas(domain, u(PTR_W, 1), cycle=0)
        for i in range(4):
            slot = (new_tail + one)[:PTR_W]
            for s in range(DEPTH):
                hit = slot == cas(domain, u(PTR_W, s), cycle=0)
                fifo[s].assign(tags[i], when=hit & valids[i])
            new_tail = mux(valids[i], slot, new_tail)
        tail <<= new_tail
        m.output("fifo_{OBS_SLOT}", wire_of(fifo[{OBS_SLOT}]))


    build.__pycircuit_name__ = "multi_assign_repro"


    @testbench
    def tb(t: Tb) -> None:
        bench = CycleAwareTb(t)
        bench.clock("clk")
        bench.reset("rst", cycles_asserted=1, cycles_deasserted=1)
        bench.timeout(8)
        for i in range(4):
            bench.drive(f"valid{{i}}", 1 if i == 2 else 0)
            bench.drive(f"tag{{i}}", {PORT2_TAG} if i == 2 else 0)
        bench.print("PYC_OBS", ports=["fifo_{OBS_SLOT}"])
        bench.finish()


    if __name__ == "__main__":
        print(compile_cycle_aware(build, name="multi_assign_repro", eager=True).emit_mlir())
    """
).lstrip()


def _tool(name: str, repo_candidate: Path | None = None) -> str | None:
    configured = os.environ.get(name.upper())
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which(name)
    if found:
        return found
    if repo_candidate is not None and repo_candidate.is_file():
        return str(repo_candidate)
    return None


def _require_tools() -> None:
    pycc = _tool("pycc", ROOT / ".pycircuit_out" / "toolchain" / "install" / "bin" / "pycc")
    verilator = _tool("verilator")
    if pycc is None or verilator is None:
        pytest.skip("requires built pycc and Verilator")


def _write_source(tmp_path: Path) -> Path:
    source = tmp_path / "multi_assign_repro.py"
    source.write_text(SOURCE, encoding="utf-8")
    return source


def _run_cli(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "compiler" / "frontend")
    pycc = _tool("pycc", ROOT / ".pycircuit_out" / "toolchain" / "install" / "bin" / "pycc")
    if pycc is not None:
        env["PYCC"] = pycc
    return subprocess.run(
        [sys.executable, "-m", "pycircuit.cli", *args],
        cwd=ROOT,
        env=env,
        check=check,
        text=True,
        capture_output=True,
    )


def _build(source: Path, out_dir: Path, *, target: str, run_verilator: bool) -> subprocess.CompletedProcess[str]:
    args = [
        "build",
        str(source),
        "--out-dir",
        str(out_dir),
        "--target",
        target,
        "--jobs",
        "2",
        "--logic-depth",
        "128",
        "--profile",
        "dev",
    ]
    if run_verilator:
        args.append("--run-verilator")
    return _run_cli(args, check=False)


def _combined_output(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def _run_cpp_tb(out_dir: Path) -> subprocess.CompletedProcess[str]:
    manifest = json.loads((out_dir / "project_manifest.json").read_text(encoding="utf-8"))
    exe = manifest.get("cpp_executable")
    if not exe:
        raise AssertionError("project_manifest.json missing cpp_executable")
    return subprocess.run([exe], cwd=out_dir, check=False, text=True, capture_output=True)


def _parse_obs(text: str) -> int | None:
    match = OBS_LINE.search(text)
    if match is None:
        return None
    raw = match.group(1)
    return int(raw, 0 if raw.lower().startswith("0x") else 16)


def _next_assign_counts(text: str, *, pattern: str) -> dict[str, int]:
    return dict(Counter(re.findall(pattern, text)))


def _frontend_on_path() -> None:
    frontend = str(ROOT / "compiler" / "frontend")
    if frontend not in sys.path:
        sys.path.insert(0, frontend)


def _run_both(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    source = _write_source(tmp_path)
    cpp_dir = tmp_path / "cpp"
    rtl_dir = tmp_path / "rtl"
    cpp_build = _build(source, cpp_dir, target="cpp", run_verilator=False)
    assert cpp_build.returncode == 0, _combined_output(cpp_build)
    cpp_run = _run_cpp_tb(cpp_dir)
    rtl_run = _build(source, rtl_dir, target="verilator", run_verilator=True)
    return cpp_run, rtl_run


def test_multi_assign_emit_has_single_next_driver(tmp_path: Path) -> None:
    """Each register next has exactly one C++ / Verilog driver after folding."""
    _require_tools()
    source = _write_source(tmp_path)
    out_dir = tmp_path / "emit"
    proc = _build(source, out_dir, target="both", run_verilator=False)
    assert proc.returncode == 0, _combined_output(proc)

    verilog_files = list((out_dir / "device" / "verilog").rglob("*.v"))
    assert verilog_files, "expected generated Verilog"
    verilog = "\n".join(p.read_text(encoding="utf-8") for p in verilog_files)
    v_counts = _next_assign_counts(verilog, pattern=r"assign\s+(\w+__next)\s*=")
    obs_v = f"fifo_{OBS_SLOT}__next"
    assert v_counts.get(obs_v) == 1, f"Verilog drivers for {obs_v}: {v_counts}"
    assert all(n == 1 for n in v_counts.values()), f"Verilog multi-driven next: {v_counts}"

    cpp_files = list((out_dir / "device").rglob("*.cpp"))
    cpp = "\n".join(p.read_text(encoding="utf-8") for p in cpp_files)
    # The emitter splits comb evaluation into `eval_comb_<n>()` plus
    # `eval_comb_<n>_part_<k>()` helpers (per-module comb + partitioning), and
    # `eval_comb_pass()` only dispatches to them. Collect the bodies of every
    # comb method so the driver count is measured where the writes land.
    comb_sigs = list(re.finditer(r"void\s+\w+::(eval_comb[\w]*)\(\)", cpp))
    assert comb_sigs, "expected eval_comb* methods in generated C++"

    def _body_after(pos: int) -> str:
        # Brace-match: a lazy `.*?` up to the next `}` line would stop at the
        # first indented closing brace (\s* spans newlines) and truncate.
        open_brace = cpp.index("{", pos)
        depth, end = 0, open_brace
        for i in range(open_brace, len(cpp)):
            if cpp[i] == "{":
                depth += 1
            elif cpp[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        assert depth == 0, "unbalanced braces after a comb method"
        return cpp[open_brace + 1 : end]

    comb_body = "\n".join(_body_after(m.end()) for m in comb_sigs)

    # C++ variables are named by the emitter, not by `pyc.name`: since
    # --observe-named=demand, undemanded names (compiler-inserted `*_next`
    # temporaries among them) fall back to op-based identifiers. Resolve the
    # probe-path -> C++ variable mapping from the registration calls instead.
    probe_vars = dict(re.findall(r'reg_path\("([^"]+)"\),\s*&(\w+)\)', cpp))
    next_probes = {p: v for p, v in probe_vars.items() if p.endswith("__next")}
    assert next_probes, f"no next probes registered: {sorted(probe_vars)[:5]}..."
    obs_var = next_probes.get(obs_v)
    assert obs_var, f"{obs_v} not registered as a probe: {sorted(next_probes)[:5]}..."

    c_counts = _next_assign_counts(comb_body, pattern=rf"\b({re.escape(obs_var)})\s*=")
    assert c_counts.get(obs_var) == 1, f"C++ eval_comb_pass writes to {obs_v} ({obs_var}): {c_counts}"
    for probe, var in sorted(next_probes.items()):
        n = len(re.findall(rf"\b{re.escape(var)}\s*=", comb_body))
        assert n == 1, f"C++ {n} drivers for {probe} ({var}) in eval_comb_pass"


def test_multi_assign_cpp_and_verilog_match_spec(tmp_path: Path) -> None:
    """Both backends must commit 274 to fifo[2] when only port2 is valid."""
    _require_tools()
    cpp_run, rtl_run = _run_both(tmp_path)
    cpp_val = _parse_obs(_combined_output(cpp_run))
    rtl_val = _parse_obs(_combined_output(rtl_run))
    if cpp_val == PORT2_TAG and rtl_val == PORT2_TAG:
        return
    raise AssertionError(
        f"C++ and Verilator must both retire fifo_{OBS_SLOT}={PORT2_TAG} when "
        f"only valid2=1 (fifo[{OBS_SLOT}] reset was {RESET_TAG}).\n"
        f"C++ exit={cpp_run.returncode} fifo_{OBS_SLOT}={cpp_val}\n{_combined_output(cpp_run)}\n"
        f"Verilator exit={rtl_run.returncode} fifo_{OBS_SLOT}={rtl_val}\n{_combined_output(rtl_run)}"
    )


COMBO_SOURCE = textwrap.dedent(
    """
    from pycircuit import (
        CycleAwareCircuit,
        CycleAwareDomain,
        CycleAwareTb,
        Tb,
        cas,
        compile_cycle_aware,
        testbench,
        u,
        wire_of,
    )


    def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
        hold = domain.signal(width=8, reset_value=7, name="hold")
        hold.assign(cas(domain, u(8, 99), cycle=0), when=cas(domain, u(1, 0), cycle=0))
        keep = domain.signal(width=9, reset_value=1, name="keep")
        keep.assign(cas(domain, u(9, 274), cycle=0), when=cas(domain, u(1, 1), cycle=0))
        keep.assign(cas(domain, u(9, 0), cycle=0), when=cas(domain, u(1, 0), cycle=0))
        last = domain.signal(width=8, reset_value=1, name="last")
        last.assign(cas(domain, u(8, 100), cycle=0), when=cas(domain, u(1, 1), cycle=0))
        last.assign(cas(domain, u(8, 200), cycle=0), when=cas(domain, u(1, 1), cycle=0))
        m.output("hold", wire_of(hold))
        m.output("keep", wire_of(keep))
        m.output("last", wire_of(last))


    build.__pycircuit_name__ = "when_combo"


    @testbench
    def tb(t: Tb) -> None:
        bench = CycleAwareTb(t)
        bench.clock("clk")
        bench.reset("rst", cycles_asserted=1, cycles_deasserted=1)
        bench.timeout(8)
        bench.print("PYC_OBS", ports=["hold", "keep", "last"])
        bench.finish()


    if __name__ == "__main__":
        print(compile_cycle_aware(build, name="when_combo", eager=True).emit_mlir())
    """
).lstrip()

COMBO_LINE = re.compile(r"(hold|keep|last)=([0-9a-fA-Fx]+)")


def test_stacked_when_combo_cpp_and_verilog(tmp_path: Path) -> None:
    """when=0 holds Q or prior next; two when=1 writes keep the later value."""
    _require_tools()
    source = tmp_path / "when_combo.py"
    source.write_text(COMBO_SOURCE, encoding="utf-8")
    cpp_dir = tmp_path / "combo_cpp"
    rtl_dir = tmp_path / "combo_rtl"
    cpp_build = _build(source, cpp_dir, target="cpp", run_verilator=False)
    assert cpp_build.returncode == 0, _combined_output(cpp_build)
    cpp_run = _run_cpp_tb(cpp_dir)
    rtl_run = _build(source, rtl_dir, target="verilator", run_verilator=True)
    assert rtl_run.returncode == 0, _combined_output(rtl_run)

    def parsed(text: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, raw in COMBO_LINE.findall(text):
            out[name] = int(raw, 0 if raw.lower().startswith("0x") else 16)
        return out

    expected = {"hold": 7, "keep": 274, "last": 200}
    cpp_vals = parsed(_combined_output(cpp_run))
    rtl_vals = parsed(_combined_output(rtl_run))
    if cpp_vals == expected and rtl_vals == expected:
        return
    raise AssertionError(
        f"expected {expected}\n"
        f"C++ {cpp_vals}\n{_combined_output(cpp_run)}\n"
        f"Verilator {rtl_vals}\n{_combined_output(rtl_run)}"
    )


def test_stacked_assign_mlir_has_one_next_assign() -> None:
    """Frontend seal emits one pyc.assign per stacked when= chain."""
    _frontend_on_path()
    from pycircuit import cas, compile_cycle_aware, u, wire_of

    def build(m, domain) -> None:
        slot = domain.signal(width=9, reset_value=1, name="slot")
        slot.assign(cas(domain, u(9, 100), cycle=0), when=cas(domain, u(1, 1), cycle=0))
        slot.assign(cas(domain, u(9, 200), cycle=0), when=cas(domain, u(1, 0), cycle=0))
        slot.assign(cas(domain, u(9, 300), cycle=0), when=cas(domain, u(1, 1), cycle=0))
        m.output("slot", wire_of(slot))

    mlir = compile_cycle_aware(build, name="slot_acc", eager=True).emit_mlir()
    next_decl = re.search(r"(%\w+)\s*=\s*pyc\.wire\s*\{pyc\.name\s*=\s*\"slot__next\"\}", mlir)
    assert next_decl, f"missing slot__next wire in:\n{mlir}"
    next_ref = next_decl.group(1)
    assigns = re.findall(rf"pyc\.assign\s+{re.escape(next_ref)}\s*,", mlir)
    assert len(assigns) == 1, f"expected one assign to {next_ref}, got {len(assigns)}:\n{mlir}"


def test_wire_assign_mlir_last_write_wins() -> None:
    """Two Circuit.assign calls on one wire emit a single pyc.assign."""
    _frontend_on_path()
    from pycircuit import compile_cycle_aware

    def build(m, domain) -> None:
        w = m.named_wire("w", width=8)
        m.assign(w, 1)
        m.assign(w, 2)
        m.output("o", w)

    mlir = compile_cycle_aware(build, name="wire_last", eager=True).emit_mlir()
    wire_decl = re.search(r"(%\w+)\s*=\s*pyc\.wire\s*\{pyc\.name\s*=\s*\"w\"\}", mlir)
    assert wire_decl, f"missing w in:\n{mlir}"
    ref = wire_decl.group(1)
    assigns = re.findall(rf"pyc\.assign\s+{re.escape(ref)}\s*,\s*(%\w+)", mlir)
    assert len(assigns) == 1, f"expected one assign to {ref}, got {assigns}:\n{mlir}"


DOUBLE_ASSIGN_SOURCE = textwrap.dedent(
    """
    from pycircuit import (
        CycleAwareCircuit,
        CycleAwareDomain,
        CycleAwareTb,
        Tb,
        compile_cycle_aware,
        testbench,
    )


    def build(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
        w = m.named_wire("w", width=8)
        m.assign(w, 1)
        m.assign(w, 2)
        m.output("o", w)


    build.__pycircuit_name__ = "double_assign"


    @testbench
    def tb(t: Tb) -> None:
        bench = CycleAwareTb(t)
        bench.clock("clk")
        bench.reset("rst", cycles_asserted=1, cycles_deasserted=1)
        bench.timeout(8)
        bench.print("PYC_OBS", ports=["o"])
        bench.finish()


    if __name__ == "__main__":
        print(compile_cycle_aware(build, name="double_assign", eager=True).emit_mlir())
    """
).lstrip()

WIRE_OBS = re.compile(r"o=([0-9a-fA-Fx]+)")


def test_raw_double_assign_last_write_wins(tmp_path: Path) -> None:
    """Two m.assign calls on one wire keep the later value on both backends."""
    _require_tools()
    source = tmp_path / "double_assign.py"
    source.write_text(DOUBLE_ASSIGN_SOURCE, encoding="utf-8")
    cpp_dir = tmp_path / "wire_cpp"
    rtl_dir = tmp_path / "wire_rtl"
    cpp_build = _build(source, cpp_dir, target="cpp", run_verilator=False)
    assert cpp_build.returncode == 0, _combined_output(cpp_build)
    cpp_run = _run_cpp_tb(cpp_dir)
    rtl_run = _build(source, rtl_dir, target="verilator", run_verilator=True)
    assert rtl_run.returncode == 0, _combined_output(rtl_run)

    def parsed(text: str) -> int | None:
        match = WIRE_OBS.search(text)
        if match is None:
            return None
        raw = match.group(1)
        return int(raw, 0 if raw.lower().startswith("0x") else 16)

    cpp_val = parsed(_combined_output(cpp_run))
    rtl_val = parsed(_combined_output(rtl_run))
    if cpp_val == 2 and rtl_val == 2:
        return
    raise AssertionError(
        f"expected o=2 after assign(1) then assign(2)\n"
        f"C++ {cpp_val}\n{_combined_output(cpp_run)}\n"
        f"Verilator {rtl_val}\n{_combined_output(rtl_run)}"
    )
