#!/usr/bin/env python3
"""Build Verilog RTL for all XiangShan-pyc modules.

Usage:
    python build_verilog.py                    # full-size (XLEN=64) build
    python build_verilog.py --small            # reduced-width build for quick iteration
    python build_verilog.py --module alu mul   # build only specific modules
    python build_verilog.py --list             # list all available modules
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

XS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(XS_ROOT))

PYCC_SEARCH_PATHS = [
    Path(__file__).resolve().parents[2] / "build" / "bin" / "pycc",
    Path(__file__).resolve().parents[2] / "compiler" / "mlir" / "build" / "bin" / "pycc",
    Path(__file__).resolve().parents[2] / "compiler" / "mlir" / "build2" / "bin" / "pycc",
    Path(__file__).resolve().parents[2] / ".pycircuit_out" / "toolchain" / "install" / "bin" / "pycc",
    Path(__file__).resolve().parents[2] / "dist" / "pycircuit" / "bin" / "pycc",
    Path(__file__).resolve().parents[2] / "compiler" / "mlir" / "build-agent-pr37" / "bin" / "pycc",
]

PYTHONPATH = str(Path(__file__).resolve().parents[2] / "compiler" / "frontend")


def find_pycc() -> Path:
    env = os.environ.get("PYCC")
    if env:
        p = Path(env)
        if p.is_file() and os.access(p, os.X_OK):
            return p
    for p in PYCC_SEARCH_PATHS:
        if p.is_file() and os.access(p, os.X_OK):
            return p
    raise SystemExit("pycc not found. Set PYCC=<path> or build the toolchain first.")


# ---------------------------------------------------------------------------
# Module registry: (name, import_path, build_fn_name, full_kwargs, small_kwargs)
# ---------------------------------------------------------------------------

def _modules() -> list[dict[str, Any]]:
    """Return a list of all module build specifications."""
    from top.parameters import (
        XLEN, CACHE_LINE_SIZE, PADDR_BITS_MAX, VADDR_BITS_SV39,
        DECODE_WIDTH, COMMIT_WIDTH,
        ICACHE_SETS, ICACHE_WAYS, ICACHE_BLOCK_BYTES,
        IBUFFER_SIZE,
        DCACHE_SETS, DCACHE_WAYS, DCACHE_BLOCK_BYTES,
        ITLB_WAYS, ASID_LENGTH,
        L2_SETS, L2_WAYS,
        INT_PHYS_REGS,
        ISSUE_QUEUE_SIZE, ROB_SIZE,
        STORE_QUEUE_SIZE, VIRTUAL_LOAD_QUEUE_SIZE,
        STORE_BUFFER_SIZE, STORE_BUFFER_THRESHOLD,
        NUM_LDU, NUM_STA,
        PTAG_WIDTH_INT, ROB_IDX_WIDTH,
    )

    PC_WIDTH = VADDR_BITS_SV39  # 39

    return [
        # ── Frontend ──
        {
            "name": "alu",
            "file": "backend/exu/alu.py",
            "fn": "build_alu",
            "full": {"data_width": XLEN},
            "small": {"data_width": 16},
        },
        {
            "name": "bru",
            "file": "backend/exu/bru.py",
            "fn": "build_bru",
            "full": {"data_width": XLEN, "pc_width": PC_WIDTH},
            "small": {"data_width": 16, "pc_width": 16},
        },
        {
            "name": "mul",
            "file": "backend/exu/mul.py",
            "fn": "build_mul",
            "full": {"data_width": XLEN},
            "small": {"data_width": 16},
        },
        {
            "name": "div",
            "file": "backend/exu/div.py",
            "fn": "build_div",
            "full": {"data_width": XLEN},
            "small": {"data_width": 16},
        },
        {
            "name": "fpu",
            "file": "backend/fu/fpu.py",
            "fn": "build_fpu",
            "full": {"data_width": XLEN, "pipe_latency": 3, "fdiv_latency": 12},
            "small": {"data_width": 16, "pipe_latency": 2, "fdiv_latency": 4},
        },
        {
            "name": "regfile",
            "file": "backend/regfile/regfile.py",
            "fn": "build_regfile",
            "full": {"num_entries": INT_PHYS_REGS, "num_read": 14, "num_write": 8, "data_width": XLEN, "addr_width": PTAG_WIDTH_INT},
            "small": {"num_entries": 16, "num_read": 4, "num_write": 2, "data_width": 16, "addr_width": 4},
        },
        {
            "name": "rename",
            "file": "backend/rename/rename.py",
            "fn": "build_rename",
            "full": {"rename_width": DECODE_WIDTH, "int_phys_regs": INT_PHYS_REGS, "int_logic_regs": 32, "commit_width": COMMIT_WIDTH, "snapshot_num": 4},
            "small": {"rename_width": 2, "int_phys_regs": 16, "int_logic_regs": 8, "commit_width": 2, "snapshot_num": 2},
        },
        {
            "name": "dispatch",
            "file": "backend/dispatch/dispatch.py",
            "fn": "dispatch",
            "full": {"dispatch_width": DECODE_WIDTH, "fu_type_width": 3, "ptag_w": PTAG_WIDTH_INT, "pc_width": PC_WIDTH, "rob_idx_w": ROB_IDX_WIDTH},
            "small": {"dispatch_width": 2, "fu_type_width": 3, "ptag_w": 4, "pc_width": 16, "rob_idx_w": 4},
        },
        {
            "name": "issue_queue",
            "file": "backend/issue/issue_queue.py",
            "fn": "issue_queue",
            "full": {"entries": ISSUE_QUEUE_SIZE, "enq_ports": DECODE_WIDTH, "issue_ports": 2, "wb_ports": 4, "ptag_w": PTAG_WIDTH_INT, "rob_idx_w": ROB_IDX_WIDTH, "fu_type_width": 3},
            "small": {"entries": 4, "enq_ports": 2, "issue_ports": 1, "wb_ports": 2, "ptag_w": 4, "rob_idx_w": 4, "fu_type_width": 3},
        },
        {
            "name": "rob",
            "file": "backend/rob/rob.py",
            "fn": "build_rob",
            "full": {"rob_size": ROB_SIZE, "rename_width": DECODE_WIDTH, "commit_width": COMMIT_WIDTH, "wb_ports": 4, "ptag_w": PTAG_WIDTH_INT, "lreg_w": 5, "pc_width": PC_WIDTH},
            "small": {"rob_size": 16, "rename_width": 2, "commit_width": 2, "wb_ports": 2, "ptag_w": 4, "lreg_w": 3, "pc_width": 16},
        },
        {
            "name": "ctrlblock",
            "file": "backend/ctrlblock/ctrlblock.py",
            "fn": "build_ctrlblock",
            "full": {"decode_width": DECODE_WIDTH, "commit_width": COMMIT_WIDTH, "ptag_w": PTAG_WIDTH_INT, "pc_width": PC_WIDTH, "rob_idx_w": ROB_IDX_WIDTH},
            "small": {"decode_width": 2, "commit_width": 2, "ptag_w": 4, "pc_width": 16, "rob_idx_w": 4},
        },
        {
            "name": "backend",
            "file": "backend/backend.py",
            "fn": "build_backend",
            "full": {"decode_width": DECODE_WIDTH, "commit_width": COMMIT_WIDTH, "num_wb": 4, "num_int_exu": 4, "num_fp_exu": 2, "ptag_w": PTAG_WIDTH_INT, "data_width": XLEN, "pc_width": PC_WIDTH, "rob_idx_w": ROB_IDX_WIDTH, "fu_type_w": 3},
            "small": {"decode_width": 2, "commit_width": 2, "num_wb": 2, "num_int_exu": 1, "num_fp_exu": 1, "ptag_w": 4, "data_width": 16, "pc_width": 16, "rob_idx_w": 4, "fu_type_w": 3},
        },
        # ── Frontend subsystem ──
        {
            "name": "decode",
            "file": "frontend/decode/decode.py",
            "fn": "build_decode",
            "full": {"decode_width": DECODE_WIDTH, "pc_width": PC_WIDTH},
            "small": {"decode_width": 2, "pc_width": 16},
        },
        {
            "name": "ibuffer",
            "file": "frontend/ibuffer/ibuffer.py",
            "fn": "build_ibuffer",
            "full": {"size": IBUFFER_SIZE, "enq_width": 4, "deq_width": DECODE_WIDTH},
            "small": {"size": 8, "enq_width": 2, "deq_width": 2},
        },
        {
            "name": "icache",
            "file": "frontend/icache/icache.py",
            "fn": "build_icache",
            "full": {"n_sets": ICACHE_SETS, "n_ways": ICACHE_WAYS, "block_bytes": ICACHE_BLOCK_BYTES, "pc_width": PC_WIDTH},
            "small": {"n_sets": 16, "n_ways": 2, "block_bytes": 8, "pc_width": 16},
        },
        {
            "name": "ifu",
            "file": "frontend/ifu/ifu.py",
            "fn": "build_ifu",
            "full": {"fetch_width": 32, "pc_width": PC_WIDTH, "cache_data_width": CACHE_LINE_SIZE},
            "small": {"fetch_width": 4, "pc_width": 16, "cache_data_width": 128},
        },
        {
            "name": "ubtb",
            "file": "frontend/bpu/ubtb.py",
            "fn": "build_ubtb",
            "full": {"entries": 32, "tag_width": 22, "target_width": 22, "pc_width": PC_WIDTH},
            "small": {"entries": 4, "tag_width": 8, "target_width": 8, "pc_width": 16},
        },
        {
            "name": "tage",
            "file": "frontend/bpu/tage.py",
            "fn": "build_tage",
            "full": {"pc_width": PC_WIDTH},
            "small": {"pc_width": 16},
        },
        {
            "name": "sc",
            "file": "frontend/bpu/sc.py",
            "fn": "build_sc",
            "full": {"pc_width": PC_WIDTH},
            "small": {"pc_width": 16},
        },
        {
            "name": "ittage",
            "file": "frontend/bpu/ittage.py",
            "fn": "build_ittage",
            "full": {"pc_width": PC_WIDTH},
            "small": {"pc_width": 16},
        },
        {
            "name": "ras",
            "file": "frontend/bpu/ras.py",
            "fn": "build_ras",
            "full": {"pc_width": PC_WIDTH},
            "small": {"pc_width": 16},
        },
        {
            "name": "bpu",
            "file": "frontend/bpu/bpu.py",
            "fn": "build_bpu",
            "full": {"pc_width": PC_WIDTH},
            "small": {"pc_width": 16},
        },
        {
            "name": "ftq",
            "file": "frontend/ftq/ftq.py",
            "fn": "build_ftq",
            "full": {"size": 64, "pc_width": PC_WIDTH},
            "small": {"size": 8, "pc_width": 16},
        },
        {
            "name": "frontend",
            "file": "frontend/frontend.py",
            "fn": "build_frontend",
            "full": {"decode_width": DECODE_WIDTH, "pc_width": PC_WIDTH, "fetch_width": DECODE_WIDTH, "inst_width": 32, "block_bits": CACHE_LINE_SIZE},
            "small": {"decode_width": 2, "pc_width": 16, "fetch_width": 2, "inst_width": 32, "block_bits": 128},
        },
        # ── Cache ──
        {
            "name": "dcache",
            "file": "cache/dcache/dcache.py",
            "fn": "build_dcache",
            "full": {"n_sets": DCACHE_SETS, "n_ways": DCACHE_WAYS, "block_bytes": DCACHE_BLOCK_BYTES, "paddr_width": PADDR_BITS_MAX},
            "small": {"n_sets": 16, "n_ways": 2, "block_bytes": 8, "paddr_width": 16},
        },
        {
            "name": "tlb",
            "file": "cache/mmu/tlb.py",
            "fn": "build_tlb",
            "full": {"n_ways": ITLB_WAYS, "vpn_width": 27, "ppn_width": 24, "asid_width": ASID_LENGTH},
            "small": {"n_ways": 4, "vpn_width": 8, "ppn_width": 8, "asid_width": 4},
        },
        # ── Memory subsystem ──
        {
            "name": "load_unit",
            "file": "mem/pipeline/load_unit.py",
            "fn": "build_load_unit",
            "full": {},
            "small": {},
        },
        {
            "name": "store_unit",
            "file": "mem/pipeline/store_unit.py",
            "fn": "build_store_unit",
            "full": {},
            "small": {},
        },
        {
            "name": "load_queue",
            "file": "mem/lsqueue/load_queue.py",
            "fn": "load_queue",
            "full": {"size": VIRTUAL_LOAD_QUEUE_SIZE, "addr_width": PADDR_BITS_MAX},
            "small": {"size": 8, "addr_width": 16},
        },
        {
            "name": "store_queue",
            "file": "mem/lsqueue/store_queue.py",
            "fn": "store_queue",
            "full": {"size": STORE_QUEUE_SIZE, "addr_width": PADDR_BITS_MAX},
            "small": {"size": 8, "addr_width": 16},
        },
        {
            "name": "sbuffer",
            "file": "mem/sbuffer/sbuffer.py",
            "fn": "build_sbuffer",
            "full": {"size": STORE_BUFFER_SIZE, "threshold": STORE_BUFFER_THRESHOLD, "addr_width": PADDR_BITS_MAX},
            "small": {"size": 4, "threshold": 2, "addr_width": 16},
        },
        {
            "name": "prefetcher",
            "file": "mem/prefetch/prefetcher.py",
            "fn": "build_prefetcher",
            "full": {"table_size": 16, "addr_width": PADDR_BITS_MAX},
            "small": {"table_size": 4, "addr_width": 16},
        },
        {
            "name": "memblock",
            "file": "mem/memblock.py",
            "fn": "build_memblock",
            "full": {"num_load": NUM_LDU, "num_store": NUM_STA},
            "small": {"num_load": 1, "num_store": 1},
        },
        # ── L2 cache ──
        {
            "name": "l2_top",
            "file": "l2/l2_top.py",
            "fn": "build_l2_top",
            "full": {"addr_width": PADDR_BITS_MAX, "data_width": XLEN, "block_bits": CACHE_LINE_SIZE, "queue_size": 8, "tag_w": 20, "idx_w": 10, "num_ways": L2_WAYS},
            "small": {"addr_width": 16, "data_width": 16, "block_bits": 128, "queue_size": 4, "tag_w": 8, "idx_w": 4, "num_ways": 2},
        },
        {
            "name": "coupled_l2",
            "file": "l2/coupled_l2.py",
            "fn": "build_coupled_l2",
            "full": {"sets": L2_SETS, "ways": L2_WAYS, "addr_width": PADDR_BITS_MAX, "data_width": CACHE_LINE_SIZE},
            "small": {"sets": 16, "ways": 2, "addr_width": 16, "data_width": 128},
        },
        # ── Top-level ──
        {
            "name": "plic",
            "file": "top/peripherals.py",
            "fn": "build_plic",
            "full": {"num_sources": 64, "num_targets": 4, "prio_width": 3},
            "small": {"num_sources": 8, "num_targets": 2, "prio_width": 3},
        },
        {
            "name": "clint",
            "file": "top/peripherals.py",
            "fn": "build_clint",
            "full": {"timer_width": XLEN},
            "small": {"timer_width": 16},
        },
        {
            "name": "xs_core",
            "file": "top/xs_core.py",
            "fn": "build_xs_core",
            "full": {"decode_width": DECODE_WIDTH, "commit_width": COMMIT_WIDTH, "num_wb": 4, "num_load": NUM_LDU, "num_store": NUM_STA, "data_width": XLEN, "pc_width": PC_WIDTH, "ptag_w": PTAG_WIDTH_INT, "rob_idx_w": ROB_IDX_WIDTH, "fu_type_w": 3, "block_bits": CACHE_LINE_SIZE},
            "small": {"decode_width": 2, "commit_width": 2, "num_wb": 2, "num_load": 1, "num_store": 1, "data_width": 16, "pc_width": 16, "ptag_w": 4, "rob_idx_w": 4, "fu_type_w": 3, "block_bits": 128},
        },
        {
            "name": "xs_tile",
            "file": "top/xs_tile.py",
            "fn": "build_xs_tile",
            "full": {"decode_width": DECODE_WIDTH, "commit_width": COMMIT_WIDTH, "num_wb": 4, "num_load": NUM_LDU, "num_store": NUM_STA, "data_width": XLEN, "pc_width": PC_WIDTH, "ptag_w": PTAG_WIDTH_INT, "rob_idx_w": ROB_IDX_WIDTH, "fu_type_w": 3, "block_bits": CACHE_LINE_SIZE, "hart_id_w": 4},
            "small": {"decode_width": 2, "commit_width": 2, "num_wb": 2, "num_load": 1, "num_store": 1, "data_width": 16, "pc_width": 16, "ptag_w": 4, "rob_idx_w": 4, "fu_type_w": 3, "block_bits": 128, "hart_id_w": 4},
        },
        {
            "name": "xs_top",
            "file": "top/xs_top.py",
            "fn": "build_xs_top",
            "full": {"num_cores": 2, "data_width": XLEN, "addr_width": PC_WIDTH, "block_bits": CACHE_LINE_SIZE, "hart_id_w": 4, "axi_id_w": 8},
            "small": {"num_cores": 2, "data_width": 16, "addr_width": 16, "block_bits": 128, "hart_id_w": 4, "axi_id_w": 4},
        },
    ]


import re

# ---------------------------------------------------------------------------
# Module hierarchy tree: parent → [children].
# Defines the XiangShan module instantiation hierarchy.  Used by
# --hierarchical to generate a merged MLIR and Verilog hierarchy wrappers.
# ---------------------------------------------------------------------------

HIERARCHY_TREE: dict[str, list[str]] = {
    "xs_top": ["xs_tile", "plic", "clint"],
    "xs_tile": ["xs_core", "coupled_l2", "l2_top"],
    "xs_core": ["frontend", "backend", "memblock"],
    "frontend": ["bpu", "ftq", "icache", "ifu", "ibuffer", "decode"],
    "backend": ["ctrlblock", "rename", "dispatch", "issue_queue", "rob",
                "regfile", "alu", "bru", "mul", "div", "fpu"],
    "bpu": ["ubtb", "tage", "sc", "ittage", "ras"],
    "memblock": ["load_unit", "store_unit", "load_queue", "store_queue",
                 "sbuffer", "prefetcher", "dcache", "tlb"],
}


def _stamp_pycc_metadata(m: Any, name: str, params_json: str = "{}") -> None:
    """Stamp the func-level metadata attributes that pycc requires."""
    import json
    m.set_func_attr("pyc.kind", "module")
    m.set_func_attr("pyc.inline", "false")
    m.set_func_attr("pyc.params", params_json)
    m.set_func_attr("pyc.base", name)
    metrics = json.dumps({
        "ast_node_count": 0,
        "collection_count": 0,
        "collection_instance_count": 0,
        "estimated_inline_cost": 0,
        "hardware_call_count": 0,
        "instance_count": 0,
        "loop_count": 0,
        "module_call_count": 0,
        "module_family_collection_count": 0,
        "repeat_pressure": 0,
        "repeated_body_clusters": [],
        "source_loc": 0,
        "state_alloc_count": 0,
        "state_call_count": 0,
    }, sort_keys=True, separators=(",", ":"))
    m.set_func_attr("pyc.struct.metrics", metrics)
    m.set_func_attr("pyc.struct.collections", "[]")
    m.set_func_attr_json("pyc.value_params", [])
    m.set_func_attr_json("pyc.value_param_types", [])


def _wrap_module_attrs(mlir: str, top_name: str) -> str:
    """Replace bare `module {` with pycc-compatible module attributes."""
    return mlir.replace(
        "module {\n",
        f'module attributes {{pyc.top = @{top_name}, pyc.frontend.contract = "pycircuit"}} {{\n',
        1,
    )


def compile_module(spec: dict[str, Any], *, small: bool, out_dir: Path) -> Path:
    """Compile a single module to MLIR .pyc via eager mode with stamped metadata."""
    import importlib.util
    import json

    name = spec["name"]
    file_path = XS_ROOT / spec["file"]
    fn_name = spec["fn"]
    kwargs = spec["small"] if small else spec["full"]

    mod_key = f"_pyc_build_{name}"
    spec_mod = importlib.util.spec_from_file_location(mod_key, file_path)
    if spec_mod is None or spec_mod.loader is None:
        raise RuntimeError(f"Cannot load {file_path}")
    mod = importlib.util.module_from_spec(spec_mod)
    sys.modules[mod_key] = mod
    if str(XS_ROOT) not in sys.path:
        sys.path.insert(0, str(XS_ROOT))
    spec_mod.loader.exec_module(mod)

    # XiangShan design sources expose their cycle-aware builders using their
    # module names (for example, ``alu``), while the registry historically
    # recorded the obsolete ``build_alu`` spelling. Prefer an explicitly
    # exported registry name, but retain the module-name fallback so the
    # registry can compile both naming conventions during the migration.
    build_fn = getattr(mod, fn_name, None)
    if build_fn is None and fn_name.startswith("build_"):
        build_fn = getattr(mod, fn_name.removeprefix("build_"), None)
    if build_fn is None or not callable(build_fn):
        raise RuntimeError(f"{file_path} does not export a callable {fn_name!r}")
    from pycircuit import compile_cycle_aware

    params_json = json.dumps(kwargs, sort_keys=True, separators=(",", ":"))
    circuit = compile_cycle_aware(build_fn, name=name, eager=True, **kwargs)
    _stamp_pycc_metadata(circuit, name, params_json)

    mlir_text = _wrap_module_attrs(circuit.emit_mlir(), name)
    pyc_path = out_dir / "mlir" / f"{name}.pyc"
    pyc_path.parent.mkdir(parents=True, exist_ok=True)
    pyc_path.write_text(mlir_text, encoding="utf-8")
    return pyc_path


# ---------------------------------------------------------------------------
# Hierarchical build helpers
# ---------------------------------------------------------------------------

def extract_module_body(mlir_text: str) -> str:
    """Extract the body of the MLIR ``module { ... }`` wrapper.

    Returns everything between the module-body opening ``{`` and its
    matching closing ``}``, which typically contains one ``func.func``.
    """
    # Track braces to skip the `module attributes {...}` dict and locate the
    # body braces.
    mod_idx = mlir_text.find("module")
    if mod_idx < 0:
        raise ValueError("No `module` keyword in MLIR text")

    depth = 0
    attrs_done = False
    body_start = -1
    for i in range(mod_idx, len(mlir_text)):
        ch = mlir_text[i]
        if ch == "{":
            depth += 1
            if depth == 1 and attrs_done:
                body_start = i + 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and not attrs_done:
                attrs_done = True
            elif depth == 0 and attrs_done:
                return mlir_text[body_start:i].strip()
    raise ValueError("Failed to find module body")


def merge_mlir_modules(pyc_paths: dict[str, Path], top_name: str) -> str:
    """Merge multiple .pyc MLIR files into a single MLIR module.

    Each .pyc file contributes its ``func.func`` definition.  Non-top
    functions are marked ``private`` so MLIR allows them in the same module.
    The resulting module carries ``pyc.top = @<top_name>``.
    """
    funcs: list[str] = []
    for name, path in pyc_paths.items():
        text = path.read_text(encoding="utf-8")
        body = extract_module_body(text)
        if name != top_name:
            body = body.replace("func.func @", "func.func private @", 1)
        funcs.append(f"// ── {name} ──\n{body}")
    header = (
        f'module attributes {{pyc.top = @{top_name}, '
        f'pyc.frontend.contract = "pycircuit"}} {{'
    )
    return header + "\n\n" + "\n\n".join(funcs) + "\n\n}\n"


def parse_verilog_ports(v_text: str) -> dict[str, Any] | None:
    """Parse a Verilog module declaration and return name + port list."""
    hdr = re.search(r"module\s+(\w+)\s*\(", v_text)
    if not hdr:
        return None
    mod_name = hdr.group(1)

    paren_start = hdr.end() - 1
    depth, end = 0, paren_start
    for i in range(paren_start, len(v_text)):
        if v_text[i] == "(":
            depth += 1
        elif v_text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    port_block = v_text[paren_start + 1 : end]

    ports: list[dict[str, Any]] = []
    for line in port_block.splitlines():
        line = line.strip().rstrip(",")
        m = re.match(
            r"(input|output)\s+(?:reg\s+)?(\[[\d:]+\]\s*)?(\w+)", line
        )
        if m:
            direction = m.group(1)
            width_str = (m.group(2) or "").strip()
            pname = m.group(3)
            if width_str:
                wm = re.match(r"\[(\d+):(\d+)\]", width_str)
                width = int(wm.group(1)) - int(wm.group(2)) + 1 if wm else 1
            else:
                width = 1
            ports.append({"dir": direction, "name": pname, "width": width})
    return {"name": mod_name, "ports": ports}


def generate_hierarchy_wrapper(
    parent_name: str,
    children: list[str],
    verilog_dir: Path,
    out_path: Path,
) -> None:
    """Generate a Verilog wrapper that instantiates every child module.

    Each child port is exposed at the wrapper boundary prefixed with
    ``<child>_``, except ``clk`` and ``rst`` which are shared.
    """
    child_infos: dict[str, dict[str, Any]] = {}
    for child in children:
        for candidate in (
            verilog_dir / f"{child}.v",
            verilog_dir / child / f"{child}.v",
        ):
            if candidate.exists():
                info = parse_verilog_ports(candidate.read_text(encoding="utf-8"))
                if info:
                    child_infos[child] = info
                break

    if not child_infos:
        return

    shared_clocks = {"clk", "rst"}

    wrapper_ports: list[str] = []
    wrapper_ports.append("  input clk")
    wrapper_ports.append("  input rst")
    for child, info in child_infos.items():
        for p in info["ports"]:
            if p["name"] in shared_clocks:
                continue
            w = f"[{p['width'] - 1}:0] " if p["width"] > 1 else ""
            wrapper_ports.append(f"  {p['dir']} {w}{child}_{p['name']}")

    inst_blocks: list[str] = []
    for child, info in child_infos.items():
        lines = [f"{info['name']} u_{child} ("]
        conns: list[str] = []
        for p in info["ports"]:
            if p["name"] in shared_clocks:
                conns.append(f"  .{p['name']}({p['name']})")
            else:
                conns.append(f"  .{p['name']}({child}_{p['name']})")
        lines.append(",\n".join(conns))
        lines.append(");")
        inst_blocks.append("\n".join(lines))

    body = (
        f"// Hierarchy wrapper for {parent_name}\n"
        f"// Generated by build_verilog.py --hierarchical\n"
        f"// Children: {', '.join(children)}\n\n"
        f"module {parent_name}_hier (\n"
        + ",\n".join(wrapper_ports)
        + "\n);\n\n"
        + "\n\n".join(inst_blocks)
        + "\n\nendmodule\n"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")


def run_pycc_hierarchical(
    pycc: Path,
    merged_path: Path,
    out_dir: Path,
    *,
    top: str,
    logic_depth: int = 512,
) -> Path:
    """Run pycc on a merged multi-module .pyc file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(pycc),
        str(merged_path),
        "--emit=verilog",
        f"--out-dir={out_dir}",
        "--hierarchy-policy=instantiate",
        "--inline-policy=off",
        f"--logic-depth={logic_depth}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"pycc (hierarchical) failed:\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  stdout: {result.stdout.strip()}"
        )
    return out_dir


def run_pycc(pycc: Path, pyc_path: Path, verilog_dir: Path, *, name: str, logic_depth: int = 128) -> Path:
    """Run pycc to convert .pyc MLIR to Verilog."""
    out_dir = verilog_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(pycc),
        str(pyc_path),
        "--emit=verilog",
        f"--out-dir={out_dir}",
        "--hierarchy-policy=strict",
        "--inline-policy=off",
        f"--logic-depth={logic_depth}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"pycc failed for {name}:\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  stdout: {result.stdout.strip()}"
        )
    return out_dir


def _build_flat(modules: list[dict[str, Any]], *, small: bool, out_dir: Path,
                pycc_bin: Path | None, mlir_only: bool, logic_depth: int) -> int:
    """Original flat build: each module compiled and lowered independently."""
    verilog_dir = out_dir / "verilog"
    verilog_dir.mkdir(parents=True, exist_ok=True)

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []
    total_start = time.time()

    for i, spec in enumerate(modules, 1):
        name = spec["name"]
        print(f"[{i:2d}/{len(modules)}] {name} ... ", end="", flush=True)
        t0 = time.time()

        try:
            pyc_path = compile_module(spec, small=small, out_dir=out_dir)
            mlir_size = pyc_path.stat().st_size

            if mlir_only or pycc_bin is None:
                dt = time.time() - t0
                print(f"MLIR OK ({mlir_size:,} bytes, {dt:.1f}s)")
                succeeded.append(name)
                continue

            v_dir = run_pycc(pycc_bin, pyc_path, verilog_dir, name=name, logic_depth=logic_depth)
            v_files = list(v_dir.glob("*.v"))
            v_total = sum(f.stat().st_size for f in v_files)
            dt = time.time() - t0
            print(f"OK ({len(v_files)} files, {v_total:,} bytes, {dt:.1f}s)")
            succeeded.append(name)

        except Exception as e:
            dt = time.time() - t0
            print(f"FAIL ({dt:.1f}s)")
            print(f"       {e}", file=sys.stderr)
            failed.append((name, str(e)))

    total_time = time.time() - total_start
    print()
    print(f"{'=' * 60}")
    print(f"Results: {len(succeeded)} succeeded, {len(failed)} failed ({total_time:.1f}s total)")

    if not mlir_only:
        v_files = list(verilog_dir.rglob("*.v"))
        total_bytes = sum(f.stat().st_size for f in v_files)
        total_lines = sum(1 for f in v_files for _ in f.open())
        print(f"Verilog: {len(v_files)} files, {total_lines:,} lines, {total_bytes:,} bytes")
        print(f"Output:  {verilog_dir}")

    if failed:
        print(f"\nFailed modules:")
        for name, err in failed:
            print(f"  {name}: {err[:120]}")
        return 1
    return 0


def _build_hierarchical(modules: list[dict[str, Any]], *, small: bool,
                        out_dir: Path, pycc_bin: Path, logic_depth: int,
                        top: str) -> int:
    """Hierarchical build: flat compile + merged MLIR + Verilog wrappers.

    Steps:
      1) Compile each module independently to MLIR + Verilog (like flat mode).
      2) Collect all Verilog into a unified ``verilog_hier/`` directory.
      3) Merge all MLIR ``func.func`` into a single ``.pyc`` file (for future
         ``pyc.instance`` support and analysis).
      4) Generate Verilog hierarchy wrappers for each node in HIERARCHY_TREE.
    """
    flat_verilog = out_dir / "verilog"
    flat_verilog.mkdir(parents=True, exist_ok=True)
    hier_dir = out_dir / "verilog_hier"
    hier_dir.mkdir(parents=True, exist_ok=True)
    mlir_dir = out_dir / "mlir"
    mlir_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: compile each module independently ──
    print("Phase 1: Compiling modules to MLIR + Verilog ...")
    pyc_paths: dict[str, Path] = {}
    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []
    t0_phase = time.time()

    for i, spec in enumerate(modules, 1):
        name = spec["name"]
        print(f"  [{i:2d}/{len(modules)}] {name} ... ", end="", flush=True)
        t0 = time.time()
        try:
            pyc_path = compile_module(spec, small=small, out_dir=out_dir)
            pyc_paths[name] = pyc_path
            v_dir = run_pycc(pycc_bin, pyc_path, flat_verilog, name=name,
                             logic_depth=logic_depth)
            v_files = list(v_dir.glob("*.v"))
            v_total = sum(f.stat().st_size for f in v_files)
            dt = time.time() - t0
            print(f"OK ({len(v_files)} files, {v_total:,} bytes, {dt:.1f}s)")
            succeeded.append(name)
        except Exception as e:
            dt = time.time() - t0
            print(f"FAIL ({dt:.1f}s)")
            print(f"       {e}", file=sys.stderr)
            failed.append((name, str(e)))

    dt_phase1 = time.time() - t0_phase
    print(f"  Phase 1 done: {len(succeeded)} succeeded, {len(failed)} failed "
          f"({dt_phase1:.1f}s)\n")

    if failed:
        print(f"Phase 1 failures:")
        for name, err in failed:
            print(f"  {name}: {err[:120]}")
        return 1

    # ── Phase 2: collect all module .v into unified directory ──
    print("Phase 2: Collecting Verilog into unified directory ...")
    import shutil
    collected = 0
    for name in succeeded:
        src = flat_verilog / name / f"{name}.v"
        if src.exists():
            shutil.copy2(src, hier_dir / f"{name}.v")
            collected += 1
    prims_src = flat_verilog / succeeded[0] / "pyc_primitives.v" if succeeded else None
    if prims_src and prims_src.exists():
        shutil.copy2(prims_src, hier_dir / "pyc_primitives.v")
    print(f"  Collected {collected} module Verilog files\n")

    # ── Phase 3: merge MLIR (reference artifact) ──
    print("Phase 3: Merging MLIR (reference artifact) ...")
    merged_path = mlir_dir / "merged_hierarchy.pyc"
    try:
        merged_text = merge_mlir_modules(pyc_paths, top)
        merged_path.write_text(merged_text, encoding="utf-8")
        print(f"  Merged {len(pyc_paths)} modules → {merged_path.stat().st_size:,} bytes")
        print(f"  Top module: @{top}\n")
    except Exception as e:
        print(f"  FAIL (non-fatal): {e}", file=sys.stderr)

    # ── Phase 4: generate Verilog hierarchy wrappers ──
    print("Phase 4: Generating hierarchy wrappers ...")
    wrapper_count = 0
    names_set = set(succeeded)
    for parent, children in HIERARCHY_TREE.items():
        present = [c for c in children if c in names_set]
        if not present:
            continue
        wrapper_path = hier_dir / f"{parent}_hier.v"
        generate_hierarchy_wrapper(parent, present, hier_dir, wrapper_path)
        if wrapper_path.exists():
            wrapper_count += 1
            print(f"  {parent}_hier.v ({len(present)} children)")

    # ── Summary ──
    all_v = list(hier_dir.glob("*.v"))
    total_bytes = sum(f.stat().st_size for f in all_v)
    total_lines = sum(1 for f in all_v for _ in f.open())
    print(f"\n{'=' * 60}")
    print(f"Hierarchical build complete:")
    print(f"  Modules:  {len(succeeded)}")
    print(f"  Wrappers: {wrapper_count}")
    print(f"  Verilog:  {len(all_v)} files, {total_lines:,} lines, {total_bytes:,} bytes")
    print(f"  Output:   {hier_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Verilog RTL for XiangShan-pyc modules")
    parser.add_argument("--small", action="store_true",
                        help="Use reduced-width parameters for quick iteration (16-bit data paths)")
    parser.add_argument("--module", nargs="*", default=None,
                        help="Build only specific modules (by name)")
    parser.add_argument("--list", action="store_true",
                        help="List all available modules and exit")
    parser.add_argument("--out-dir", default=None,
                        help="Output directory (default: build_out/ or build_out_small/)")
    parser.add_argument("--mlir-only", action="store_true",
                        help="Only generate MLIR, skip pycc Verilog backend")
    parser.add_argument("--logic-depth", type=int, default=512,
                        help="Max combinational logic depth for pycc (default: 512)")
    parser.add_argument("--jobs", type=int, default=1,
                        help="Number of parallel pycc jobs (default: 1)")
    parser.add_argument("--hierarchical", action="store_true",
                        help="Merge all modules into one MLIR and emit hierarchical Verilog")
    parser.add_argument("--top", default="xs_top",
                        help="Top module for hierarchical build (default: xs_top)")
    args = parser.parse_args()

    modules = _modules()

    if args.list:
        print(f"Available modules ({len(modules)}):\n")
        for m in modules:
            print(f"  {m['name']:20s}  {m['file']}")
        print(f"\nHierarchy tree:")
        for parent, children in HIERARCHY_TREE.items():
            print(f"  {parent:20s} → {', '.join(children)}")
        return 0

    if args.module:
        names = set(args.module)
        modules = [m for m in modules if m["name"] in names]
        not_found = names - {m["name"] for m in modules}
        if not_found:
            print(f"ERROR: Unknown modules: {', '.join(sorted(not_found))}", file=sys.stderr)
            return 1

    suffix = "_small" if args.small else ""
    out_dir = Path(args.out_dir) if args.out_dir else (XS_ROOT / f"build_out{suffix}")
    out_dir.mkdir(parents=True, exist_ok=True)

    pycc_bin: Path | None = None
    if not args.mlir_only:
        pycc_bin = find_pycc()
        print(f"pycc: {pycc_bin}")

    mode = "small (16-bit)" if args.small else "full (64-bit XLEN)"
    build_style = "hierarchical" if args.hierarchical else "flat"
    print(f"Build mode: {mode} ({build_style})")
    print(f"Output dir: {out_dir}")
    print(f"Modules: {len(modules)}")
    print()

    if args.hierarchical:
        if pycc_bin is None:
            print("ERROR: --hierarchical requires pycc (cannot use --mlir-only)", file=sys.stderr)
            return 1
        return _build_hierarchical(
            modules, small=args.small, out_dir=out_dir,
            pycc_bin=pycc_bin, logic_depth=args.logic_depth, top=args.top,
        )

    return _build_flat(
        modules, small=args.small, out_dir=out_dir,
        pycc_bin=pycc_bin, mlir_only=args.mlir_only, logic_depth=args.logic_depth,
    )


if __name__ == "__main__":
    raise SystemExit(main())
