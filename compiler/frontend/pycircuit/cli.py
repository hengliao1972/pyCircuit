from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .api_contract import collect_local_python_graph, nearest_project_root, scan_file
from .diagnostics import render_diagnostic
from .dsl import Module
from .design import FRONTEND_CONTRACT, Design, DesignError, value_params_of
from .jit import JitError, compile
from .packaged_toolchain import bundled_toolchain_root, tool_executable
from .probe import (
    ProbeError,
    TbProbes,
    build_resolved_probe_manifest,
    collect_probe_functions,
    load_probe_catalog,
    resolve_probe_function,
)
from .sidecar_sections import (
    inspect_sidecar_file,
    render_sidecar_inspect_text,
)
from .tb import Tb, TbError, _sanitize_id
from .testbench import emit_testbench_pyc, testbench_payload_from_tb
from .trace_dsl import (
    TraceConfigError,
    TracePlan,
    compute_trace_plan,
    compute_trace_plan_from_artifacts,
    load_trace_config,
)


def _default_top_name(src: Path) -> str:
    parts = [p for p in src.stem.replace("-", "_").split("_") if p]
    if not parts:
        return "Top"
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _tool_script(name: str) -> Path:
    candidates = [
        Path(__file__).resolve().parent / "_tools" / name,
        Path(__file__).resolve().parents[3] / "flows" / "tools" / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(f"required pyCircuit helper script not found: {name}")


def _load_py_file(path: Path) -> object:
    path = path.resolve()
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to import {path}")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _resolve_emit_source(src_arg: str) -> tuple[Path | None, object]:
    if "." in src_arg and not Path(src_arg).exists():
        spec = importlib.util.find_spec(src_arg)
        src: Path | None = None
        if spec is not None and isinstance(spec.origin, str) and spec.origin.endswith(".py"):
            src = Path(spec.origin).resolve()
        mod = importlib.import_module(src_arg)
        return src, mod
    src = Path(src_arg).resolve()
    return src, _load_py_file(src)


def _scan_api_contract(entry: Path, *, project_root_override: str | None = None) -> None:
    if not entry.is_file():
        return
    root = Path(project_root_override).resolve() if project_root_override else nearest_project_root(entry)
    files = collect_local_python_graph(entry.resolve(), project_root=root)
    diags = []
    for f in files:
        diags.extend(scan_file(f, stage="api-contract"))
    if not diags:
        return
    for d in diags:
        print(render_diagnostic(d), file=sys.stderr)
    raise SystemExit(f"api contract check failed: {len(diags)} violation(s)")


def _project_root(entry: Path, *, project_root_override: str | None = None) -> Path:
    if project_root_override:
        return Path(project_root_override).resolve()
    return nearest_project_root(entry)


def _is_cycle_aware_entrypoint(build: Any) -> bool:
    """True for V5 ``def build(m, domain, ...)`` cycle-aware designs."""
    try:
        params = list(inspect.signature(build).parameters.values())
    except (TypeError, ValueError):
        return False
    if len(params) < 2:
        return False
    p1 = params[1]
    if p1.name == "domain":
        return True
    ann = p1.annotation
    if ann is inspect._empty:
        return False
    return "CycleAwareDomain" in str(ann)


def _collect_jit_params(build: Any, *, overrides: list[str]) -> dict[str, object]:
    if not callable(build):
        raise SystemExit("build must be a callable @module entrypoint: `def build(m: Circuit, ...)`")

    sig = inspect.signature(build)
    params = list(sig.parameters.values())
    if not params:
        raise SystemExit("build must use JIT entry semantics: `@module def build(m: Circuit, ...)`")
    value_param_names = set(value_params_of(build).keys())
    cycle_aware = _is_cycle_aware_entrypoint(build)

    # Cycle-aware entrypoints use ``build(m, domain, *, ...)``.  The domain is
    # supplied by ``compile_cycle_aware`` rather than being a JIT parameter.
    param_start = 2 if len(params) >= 2 and params[1].name == "domain" else 1

    # Collect JIT-time parameters from defaults.
    jit_params: dict[str, object] = {}
    missing: list[str] = []
    for p in params[param_start:]:
        if p.name in value_param_names:
            continue
        # Injected by compile_cycle_aware; not a CLI/JIT value parameter.
        if cycle_aware and p.name == "domain":
            continue
        if p.default is inspect._empty:
            missing.append(p.name)
        else:
            jit_params[p.name] = p.default
    if missing:
        raise SystemExit(
            f"build() is treated as a JIT design function but missing default values for: {', '.join(missing)}"
        )

    # Apply CLI overrides.
    for spec in overrides:
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

    return jit_params


def _compile_to_design(build: Any, *, top_name: str, jit_params: dict[str, object]) -> Design:
    """Compile ``build`` to a :class:`Design` (JIT ``@module`` or eager V5 cycle-aware)."""
    if _is_cycle_aware_entrypoint(build):
        from .v5 import _make_compiled_module, compile_cycle_aware

        circuit = compile_cycle_aware(build, name=top_name, eager=True, hierarchical=True, **jit_params)
        existing = getattr(circuit, "_v5_design", None)
        if isinstance(existing, Design):
            return existing
        cm = _make_compiled_module(build, circuit, top_name)
        design = Design(top=top_name)
        design.add(cm)
        return design
    try:
        design_obj = compile(build, name=top_name, **jit_params)
    except (DesignError, JitError) as e:
        raise SystemExit(f"design compile failed: {e}") from e
    if not isinstance(design_obj, Design):
        raise SystemExit("internal error: expected Design from compile(...)")
    return design_obj


def _top_name_for_build(src: Path, build: Any) -> str:
    top_name = _default_top_name(src)
    override = getattr(build, "__pycircuit_name__", None)
    if isinstance(override, str) and override.strip():
        top_name = override.strip()
    return top_name


def _cmd_emit(args: argparse.Namespace) -> int:
    src_arg = args.python_file
    out = Path(args.output)
    src, mod = _resolve_emit_source(src_arg)
    if src is not None:
        _scan_api_contract(src, project_root_override=args.project_root)
    if not hasattr(mod, "build"):
        raise SystemExit(f"{src_arg} must define a pyCircuit entrypoint: `@module def build(m: Circuit, ...)`")
    build = getattr(mod, "build")

    jit_params = _collect_jit_params(build, overrides=list(args.param or []))
    top_name = _top_name_for_build(src if src is not None else Path(src_arg.replace(".", "/") + ".py"), build)
    design = _compile_to_design(build, top_name=top_name, jit_params=jit_params)

    if isinstance(design, Design):
        out.write_text(design.emit_mlir(), encoding="utf-8")
        if getattr(args, "module_graph_out", None):
            tool = _tool_script("pyc_module_graph.py")
            cmd = [
                sys.executable,
                str(tool),
                "--pyc",
                str(out),
                "--out",
                str(args.module_graph_out),
                "--edge-label-mode",
                str(getattr(args, "module_graph_edge_label_mode", "ports")),
                "--edge-label-limit",
                str(int(getattr(args, "module_graph_edge_label_limit", 4))),
                "--max-nodes",
                str(int(getattr(args, "module_graph_max_nodes", 500))),
                "--max-edges",
                str(int(getattr(args, "module_graph_max_edges", 2000))),
            ]
            if getattr(args, "module_graph_module", ""):
                cmd += ["--module", str(args.module_graph_module)]
            if bool(getattr(args, "module_graph_recursive", False)):
                # "Recursive nest" = expand the full instance hierarchy (bounded by tool guardrails).
                cmd += ["--hierarchical", "--expand-all", "--expand-depth", "64"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(
                    "module-graph generation failed.\n"
                    f"cmd: {' '.join(cmd)}\n"
                    f"stdout:\n{r.stdout}\n"
                    f"stderr:\n{r.stderr}\n"
                )
        return 0

    raise SystemExit("internal error: compile did not return a Design")
    return 0


def _detect_pycc() -> Path:
    env = os.environ.get("PYCC")
    if env:
        p = Path(env)
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise SystemExit(f"PYCC is set but not executable: {p}")

    root = Path(__file__).resolve().parents[3]
    toolchain_root_env = os.environ.get("PYC_TOOLCHAIN_ROOT")
    candidates = [
        tool_executable("pycc"),
        Path(toolchain_root_env) / "bin" / "pycc" if toolchain_root_env else None,
        root / ".pycircuit_out" / "toolchain" / "install" / "bin" / "pycc",
        root / "dist" / "pycircuit" / "bin" / "pycc",
        root / "build-top" / "bin" / "pycc",
        root / "build" / "bin" / "pycc",
        root / "compiler" / "mlir" / "build2" / "bin" / "pycc",
        root / "compiler" / "mlir" / "build" / "bin" / "pycc",
    ]
    for c in candidates:
        if c is None:
            continue
        if c.is_file() and os.access(c, os.X_OK):
            return c

    found = shutil.which("pycc")
    if found:
        return Path(found)

    raise SystemExit("missing pycc (set PYCC=... or build it with: flows/scripts/pyc build)")


def _toolchain_roots(pycc: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        try:
            rp = path.resolve()
        except OSError:
            return
        if rp in seen:
            return
        seen.add(rp)
        roots.append(rp)

    env = os.environ.get("PYC_TOOLCHAIN_ROOT")
    if env:
        add(Path(env))

    add(bundled_toolchain_root())

    if pycc is not None:
        try:
            resolved_pycc = pycc.resolve()
        except OSError:
            resolved_pycc = pycc
        if resolved_pycc.parent.name == "bin":
            add(resolved_pycc.parent.parent)

    repo_root = Path(__file__).resolve().parents[3]
    add(repo_root / ".pycircuit_out" / "toolchain" / "install")
    add(repo_root / "dist" / "pycircuit")
    return roots


def _runtime_lib_filename() -> str:
    return "pyc4_runtime.lib" if os.name == "nt" else "libpyc4_runtime.a"


def _detect_toolchain_root(pycc: Path | None = None) -> Path | None:
    for root in _toolchain_roots(pycc):
        cmake_cfg = root / "share" / "pycircuit" / "cmake" / "pycircuitConfig.cmake"
        runtime_lib = root / "lib" / _runtime_lib_filename()
        if cmake_cfg.is_file() or runtime_lib.is_file():
            return root
    return None


def _runtime_manifest_for_toolchain(toolchain_root: Path | None) -> dict[str, object]:
    if toolchain_root is None:
        raise SystemExit(
            "missing pyc toolchain root (set PYC_TOOLCHAIN_ROOT or use flows/scripts/pyc build to stage an install tree)"
        )

    include_dir = (toolchain_root / "include").resolve()
    # Prefer lib/, fall back to lib64/ (common on RHEL/Fedora 64-bit multilib).
    lib_dir = (toolchain_root / "lib")
    if not (lib_dir / _runtime_lib_filename()).is_file():
        lib_dir = toolchain_root / "lib64"
    lib_dir = lib_dir.resolve()
    cmake_config_dir = (toolchain_root / "share" / "pycircuit" / "cmake").resolve()
    runtime_lib = (lib_dir / _runtime_lib_filename()).resolve()

    if not include_dir.is_dir():
        raise SystemExit(f"invalid toolchain root: missing include dir: {include_dir}")
    if not runtime_lib.is_file():
        raise SystemExit(f"invalid toolchain root: missing runtime library: {runtime_lib}")

    return {
        "mode": "prebuilt",
        "cmake_package": "pycircuit",
        "cmake_target": "pycircuit::pyc4_runtime",
        "toolchain_root_hint": str(toolchain_root.resolve()),
        "cmake_config_dir": str(cmake_config_dir),
        "include_dirs": [str(include_dir)],
        "lib_dirs": [str(lib_dir)],
        "libs": ["pyc4_runtime"],
        "library_files": [str(runtime_lib)],
    }


@dataclass(frozen=True)
class _PortInfo:
    ty: str
    shape: tuple[int, ...]
    leaf_width: int

    @property
    def is_vector(self) -> bool:
        return bool(self.shape)

    @property
    def total_width(self) -> int:
        n = int(self.leaf_width)
        for d in self.shape:
            n *= int(d)
        return n


def _int_width_from_ty(ty: str) -> int:
    raw = str(ty).strip()
    if not raw.startswith("i"):
        raise SystemExit(f"unsupported port type for TB generation: {ty!r}")
    try:
        width = int(raw[1:])
    except ValueError as e:
        raise SystemExit(f"unsupported port type for TB generation: {ty!r}") from e
    if width <= 0:
        raise SystemExit(f"unsupported port type for TB generation: {ty!r}")
    return width


def _split_vector_type_parts(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(body):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "x" and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return [p.strip() for p in parts]


def _parse_vector_type_for_tb(ty: str) -> tuple[list[int], str]:
    raw = str(ty).strip()
    if not (raw.startswith("vector<") and raw.endswith(">")):
        raise SystemExit(f"unsupported port type for TB generation: {ty!r}")
    body = raw[len("vector<") : -1]
    parts = _split_vector_type_parts(body)
    if len(parts) < 2:
        raise SystemExit(f"unsupported port type for TB generation: {ty!r}")
    dims: list[int] = []
    try:
        for p in parts[:-1]:
            lanes = int(p)
            if lanes <= 0:
                raise ValueError
            dims.append(lanes)
    except ValueError as e:
        raise SystemExit(f"unsupported port type for TB generation: {ty!r}") from e
    return dims, parts[-1]


def _port_info(ty: str) -> _PortInfo:
    raw = str(ty).strip()
    if raw == "!pyc.clock" or raw == "!pyc.reset":
        return _PortInfo(raw, (), 1)
    if raw.startswith("i"):
        return _PortInfo(raw, (), _int_width_from_ty(raw))
    if raw.startswith("vector<"):
        shape, elem_ty = _parse_vector_type_for_tb(raw)
        dims = list(shape)
        while elem_ty.startswith("vector<"):
            sub_shape, elem_ty = _parse_vector_type_for_tb(elem_ty)
            dims.extend(sub_shape)
        if not dims or any(int(d) <= 0 for d in dims):
            raise SystemExit(f"unsupported port type for TB generation: {ty!r}")
        return _PortInfo(raw, tuple(int(d) for d in dims), _int_width_from_ty(elem_ty))
    raise SystemExit(f"unsupported port type for TB generation: {ty!r}")


def _as_int_width(ty: str) -> int:
    if ty == "!pyc.clock" or ty == "!pyc.reset":
        return 1
    return _port_info(ty).total_width


def _collect_build(mod: object, src: Path, args: argparse.Namespace) -> Module | Design:
    if not hasattr(mod, "build"):
        raise SystemExit(f"{src} must define a pyCircuit entrypoint: `@module def build(m: Circuit, ...)`")
    build = getattr(mod, "build")

    jit_params = _collect_jit_params(build, overrides=list(getattr(args, "param", []) or []))
    top_name = _top_name_for_build(src, build)
    return _compile_to_design(build, top_name=top_name, jit_params=jit_params)


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


def _top_iface_from_manifest(manifest: Mapping[str, Any]) -> _TopIface:
    top = str(manifest.get("top", "")).strip()
    modules = manifest.get("modules", None)
    if not top or not isinstance(modules, list):
        raise SystemExit("invalid project_manifest.json: missing `top` or `modules`")
    for m in modules:
        if not isinstance(m, Mapping):
            continue
        if str(m.get("name", "")).strip() != top:
            continue
        in_raw = [str(x) for x in (m.get("arg_names") or [])]
        in_tys = [str(x) for x in (m.get("arg_types") or [])]
        out_raw = [str(x) for x in (m.get("result_names") or [])]
        out_tys = [str(x) for x in (m.get("result_types") or [])]
        return _TopIface(sym=top, in_raw=in_raw, in_tys=in_tys, out_raw=out_raw, out_tys=out_tys)
    raise SystemExit(f"invalid project_manifest.json: top module {top!r} not found in modules list")


def _module_paths_from_manifest(manifest: Mapping[str, Any], *, out_dir: Path) -> dict[str, Path]:
    modules = manifest.get("modules", None)
    if not isinstance(modules, list) or not modules:
        raise SystemExit("invalid project_manifest.json: missing `modules` list")
    out: dict[str, Path] = {}
    for m in modules:
        if not isinstance(m, Mapping):
            continue
        name = str(m.get("name", "")).strip()
        pyc_rel = str(m.get("pyc", "")).strip()
        if not name or not pyc_rel:
            continue
        out[name] = (out_dir / pyc_rel).resolve()
    if not out:
        raise SystemExit("invalid project_manifest.json: module list is empty")
    return out


def _render_tb_cpp_sidecar(
    iface: _TopIface,
    t: Tb,
    *,
    trace_plan: TracePlan | None = None,
    schedule_path: Path | None = None,
) -> str:
    has_clocks = bool(t.clocks)
    has_reset = t.reset_spec is not None
    if has_reset and not has_clocks:
        raise SystemExit("tb() with reset requires at least one clock via t.clock(...)")
    if trace_plan and trace_plan.enabled_signals:
        raise SystemExit("sidecar C++ TB currently does not support trace-config binary traces; use --tb-schedule-mode=inline")
    if schedule_path is None:
        raise SystemExit("sidecar C++ TB requires an external schedule path")
    from .schedule_ir import (
        build_sidecar_schedule_ir,
        infer_port_protocol,
        infer_port_role,
        schedule_ir_to_sidecar_bytes,
    )

    top = _sanitize_id(iface.sym)
    hdr = f"{iface.sym}.hpp"

    def mask_value(v: int | bool, width: int) -> int:
        if isinstance(v, bool):
            vv = 1 if v else 0
        else:
            vv = int(v)
        if width <= 0:
            raise SystemExit("internal: invalid width")
        return vv & ((1 << width) - 1)

    def nwords(width: int) -> int:
        return (int(width) + 63) // 64

    def value_words(v: int | bool, width: int) -> list[int]:
        vv = mask_value(v, width)
        return [((vv >> (64 * i)) & ((1 << 64) - 1)) for i in range(nwords(width))]

    def words_array_literal(words: list[int], max_words: int) -> str:
        padded = list(words) + [0] * max(0, max_words - len(words))
        return "{{" + ", ".join(f"0x{int(w) & ((1 << 64) - 1):x}ull" for w in padded[:max_words]) + "}}"

    def wire_from_event_expr(width: int) -> str:
        return f"pyc::cpp::Wire<{int(width)}>({{{', '.join(f'ev.words[{i}]' for i in range(nwords(width)))}}})"

    def wire_from_frame_expr(width: int, slot: int) -> str:
        return f"pyc::cpp::Wire<{int(width)}>({{{', '.join(f'frame.words[{int(slot)}][{i}]' for i in range(nwords(width)))}}})"

    port_ids: dict[str, int] = {}
    port_meta_by_sn: dict[str, dict[str, Any]] = {}

    def get_port_id(sn: str) -> int:
        key = str(sn)
        if key not in port_ids:
            port_ids[key] = len(port_ids)
        return port_ids[key]

    def register_port(raw: str, direction: str, ty: str) -> None:
        _dir, sn, resolved_ty = iface.resolve(raw)
        w = 1 if resolved_ty in {"!pyc.clock", "!pyc.reset"} else _as_int_width(resolved_ty)
        pid = get_port_id(sn)
        meta: dict[str, Any] = {
            "id": int(pid),
            "name": str(sn),
            "direction": direction,
            "bit_width": int(w),
            "word_count": int(nwords(w)),
            "role": infer_port_role(sn, resolved_ty),
        }
        protocol = infer_port_protocol(sn)
        if protocol is not None:
            meta["protocol"] = protocol
        port_meta_by_sn[sn] = meta

    for raw, ty in zip(iface.in_raw, iface.in_tys):
        register_port(str(raw), "input", str(ty))
    for raw, ty in zip(iface.out_raw, iface.out_tys):
        register_port(str(raw), "output", str(ty))

    drive_events: list[tuple[int, int, str, int, list[int]]] = []
    pre_expect_events: list[tuple[int, int, str, int, list[int], str]] = []
    post_expect_events: list[tuple[int, int, str, int, list[int], str]] = []

    for d in t.drives:
        dir_, sn, ty = iface.resolve(d.port)
        if dir_ != "in":
            raise SystemExit(f"drive() requires input port, got output: {d.port!r}")
        w = _as_int_width(ty)
        drive_events.append((int(d.at), get_port_id(sn), sn, w, value_words(d.value, w)))

    for e in t.expects:
        _dir, sn, ty = iface.resolve(e.port)
        w = _as_int_width(ty)
        msg = e.msg if e.msg is not None else f"{sn} mismatch"
        row = (int(e.at), get_port_id(sn), sn, w, value_words(e.value, w), str(msg))
        ph = str(getattr(e, "phase", "post")).strip().lower()
        if ph == "pre":
            pre_expect_events.append(row)
        else:
            post_expect_events.append(row)

    prints_at: dict[int, list[tuple[str, list[tuple[str, str, int]]]]] = {}
    prints_every: list[tuple[str, int, int, list[tuple[str, str, int]]]] = []
    for p in getattr(t, "prints", []):
        fmt = str(p.fmt)
        port_specs: list[tuple[str, str, int]] = []
        for raw in p.ports:
            _dir, sn, ty = iface.resolve(raw)
            w = _as_int_width(ty)
            if w > 64:
                raise SystemExit(f"print() for i{w} not supported in sidecar C++ TB generator")
            port_specs.append((str(raw), sn, w))
        if p.at is not None:
            prints_at.setdefault(int(p.at), []).append((fmt, port_specs))
        else:
            st = 0 if p.start is None else int(p.start)
            ev = 1 if p.every is None else int(p.every)
            prints_every.append((fmt, st, ev, port_specs))

    drive_events.sort(key=lambda x: (x[0], x[1]))
    pre_expect_events.sort(key=lambda x: (x[0], x[1]))
    post_expect_events.sort(key=lambda x: (x[0], x[1]))

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
                raise SystemExit(f"random() for i{w} not supported in sidecar C++ TB generator")
            rand_specs.append((sn, w, int(r.seed), int(r.start), int(r.every)))

    clk_sn = ""
    rst_sn = ""
    ca = 0
    cd = 0
    if has_clocks:
        clk = t.clocks[0].port
        _, clk_sn, _clk_ty = iface.resolve(clk)
    if has_reset:
        rst = t.reset_spec.port
        _, rst_sn, _rst_ty = iface.resolve(rst)
        ca = int(t.reset_spec.cycles_asserted)
        cd = int(t.reset_spec.cycles_deasserted)

    max_words = 1
    for _cyc, _pid, _sn, _w, words in drive_events:
        max_words = max(max_words, len(words))
    for _cyc, _pid, _sn, _w, words, _msg in pre_expect_events:
        max_words = max(max_words, len(words))
    for _cyc, _pid, _sn, _w, words, _msg in post_expect_events:
        max_words = max(max_words, len(words))

    drive_ports = sorted({(pid, sn, w) for _cyc, pid, sn, w, _words in drive_events}, key=lambda x: x[0])
    drive_slot_by_pid = {int(pid): slot for slot, (pid, _sn, _w) in enumerate(drive_ports)}
    drive_frame_rows: list[tuple[int, list[int], list[list[int]]]] = []
    if drive_events:
        by_cycle: dict[int, list[tuple[int, list[int]]]] = {}
        for cyc, pid, _sn, _w, words in drive_events:
            by_cycle.setdefault(int(cyc), []).append((int(pid), list(words)))
        mask_words = (len(drive_ports) + 63) // 64
        for cyc in sorted(by_cycle.keys()):
            masks = [0] * mask_words
            values = [[0] * max_words for _ in drive_ports]
            for pid, words in by_cycle[cyc]:
                slot = drive_slot_by_pid[pid]
                masks[slot // 64] |= 1 << (slot % 64)
                for word_idx, word in enumerate(words[:max_words]):
                    values[slot][word_idx] = int(word) & ((1 << 64) - 1)
            drive_frame_rows.append((int(cyc), masks, values))
    expect_ports = sorted(
        {(pid, sn, w) for _cyc, pid, sn, w, _words, _msg in [*pre_expect_events, *post_expect_events]},
        key=lambda x: x[0],
    )

    def emit_event_array(name: str, rows: list[tuple[int, int, str, int, list[int]]] | list[tuple[int, int, str, int, list[int], str]]) -> list[str]:
        out: list[str] = []
        if not rows:
            out.append(f"static constexpr std::array<TbEvent, 0> {name} = {{}};\n\n")
            return out
        out.append(f"static constexpr std::array<TbEvent, {len(rows)}> {name} = {{\n")
        out.append("  {\n")
        for row in rows:
            cyc = int(row[0])
            pid = int(row[1])
            words = list(row[4])
            msg_lit = "nullptr"
            if len(row) >= 6:
                msg_lit = json.dumps(str(row[5]))
            out.append(
                f"    TbEvent{{{cyc}ull, {pid}u, {len(words)}u, {words_array_literal(words, max_words)}, {msg_lit}}},\n"
            )
        out.append("  }\n")
        out.append("};\n\n")
        return out

    schedule_t0 = time.perf_counter()
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_generate_s = 0.0
    schedule_ir = build_sidecar_schedule_ir(
        top_symbol=iface.sym,
        schedule_path=schedule_path,
        ports=port_meta_by_sn.values(),
        timeout_cycles=int(t.timeout_cycles),
        reset_cycles=int(ca + cd) if has_reset else 0,
        clocking="single_clock" if has_clocks else "none",
        schedule_bytes=0,
        max_event_words=int(max_words),
        drive_events=drive_events,
        drive_ports=drive_ports,
        drive_frame_rows=drive_frame_rows,
        pre_expect_events=pre_expect_events,
        post_expect_events=post_expect_events,
        generate_s=schedule_generate_s,
    )
    schedule_sidecar_blob = schedule_ir_to_sidecar_bytes(schedule_ir)
    schedule_path.write_bytes(schedule_sidecar_blob)
    schedule_generate_s = time.perf_counter() - schedule_t0
    drive_port_ids_literal = "{" + ", ".join(f"{int(pid)}u" for pid, _sn, _w in drive_ports) + "}"

    lines: list[str] = []
    lines.append("// Generated by pycircuit (sidecar schedule TB)\n")
    lines.append("#include <array>\n")
    lines.append("#include <cstdint>\n")
    lines.append("#include <cstdlib>\n")
    lines.append("#include <filesystem>\n")
    lines.append("#include <iostream>\n")
    lines.append("#include <optional>\n")
    lines.append("#include <string>\n\n")
    lines.append("#include <cpp/pyc_tb.hpp>\n\n")
    lines.append("#include <cpp/pyc_tb_sidecar_runtime.hpp>\n\n")
    lines.append("#include <cpp/pyc_tb_sidecar.hpp>\n\n")
    lines.append(f"#include \"{hdr}\"\n\n")
    lines.append("using pyc::cpp::Testbench;\n\n")
    lines.append("namespace {\n\n")
    lines.append(f"static constexpr std::uint32_t kMaxEventWords = {int(max_words)}u;\n\n")
    lines.append(f"static constexpr std::uint32_t kDrivePortCount = {len(drive_ports)}u;\n")
    lines.append(f"static constexpr std::array<std::uint32_t, kDrivePortCount> kDrivePortIds = {drive_port_ids_literal};\n")
    lines.append("using SidecarEvent = pyc::cpp::SidecarEvent<kMaxEventWords>;\n")
    lines.append("using SidecarDriveFrame = pyc::cpp::SidecarDriveFrame<kMaxEventWords, kDrivePortCount>;\n\n")

    lines.append("template <typename Dut>\n")
    lines.append("void applyDriveFrame(Dut &dut, const SidecarDriveFrame &frame) {\n")
    lines.append("  auto hasDrive = [&](std::uint32_t slot) -> bool {\n")
    lines.append("    return ((frame.port_mask[slot / 64u] >> (slot % 64u)) & 1ull) != 0ull;\n")
    lines.append("  };\n")
    for slot, (_pid, sn, w) in enumerate(drive_ports):
        lines.append(f"  if (hasDrive({int(slot)}u)) dut.{sn} = {wire_from_frame_expr(w, slot)};\n")
    lines.append("}\n\n")

    lines.append("template <typename Dut>\n")
    lines.append("void applyPeriodicDrive(Dut &dut, const pyc::cpp::SidecarPeriodicDrive &pattern, std::uint64_t cyc) {\n")
    lines.append("  const auto &words = pattern.activeAt(cyc) ? pattern.active_words : pattern.default_words;\n")
    lines.append("  switch (pattern.port_id) {\n")
    for pid, sn, w in drive_ports:
        lines.append(f"  case {int(pid)}u:\n")
        lines.append(f"    dut.{sn} = pyc::cpp::Wire<{int(w)}>({{{', '.join(f'words[{i}]' for i in range(nwords(w)))}}});\n")
        lines.append("    return;\n")
    lines.append("  default:\n")
    lines.append("    std::cerr << \"ERROR: invalid periodic drive port_id=\" << pattern.port_id << \" at cycle=\" << cyc << \"\\n\";\n")
    lines.append("    std::exit(1);\n")
    lines.append("  }\n")
    lines.append("}\n\n")

    lines.append("template <typename WireT>\n")
    lines.append("void printWireHex(const WireT &v) {\n")
    lines.append("  for (int i = static_cast<int>(WireT::kWords) - 1; i >= 0; --i) {\n")
    lines.append("    std::cerr << v.word(static_cast<unsigned>(i));\n")
    lines.append("  }\n")
    lines.append("}\n\n")

    lines.append("template <typename Dut>\n")
    lines.append("bool checkExpect(Dut &dut, const SidecarEvent &ev, const char *phase) {\n")
    lines.append("  switch (ev.port_id) {\n")
    for pid, sn, w in expect_ports:
        exp_expr = wire_from_event_expr(w)
        lines.append(f"  case {int(pid)}u: {{\n")
        lines.append(f"    const auto expected = {exp_expr};\n")
        lines.append(f"    if (!(dut.{sn} == expected)) {{\n")
        lines.append("      std::cerr << \"ERROR(\" << phase << \"): cycle=\" << ev.cycle")
        lines.append(f" << \" port={sn}\";\n")
        lines.append("      if (!ev.msg.empty()) std::cerr << \" msg=\" << ev.msg;\n")
        lines.append("      std::cerr << \" got=0x\" << std::hex;\n")
        lines.append(f"      printWireHex(dut.{sn});\n")
        lines.append("      std::cerr << \" exp=0x\";\n")
        lines.append("      printWireHex(expected);\n")
        lines.append("      std::cerr << std::dec << \"\\n\";\n")
        lines.append("      return false;\n")
        lines.append("    }\n")
        lines.append("    return true;\n")
        lines.append("  }\n")
    lines.append("  default:\n")
    lines.append("    std::cerr << \"ERROR(\" << phase << \"): invalid expect port_id=\" << ev.port_id << \" at cycle=\" << ev.cycle << \"\\n\";\n")
    lines.append("    return false;\n")
    lines.append("  }\n")
    lines.append("}\n\n")
    lines.append("} // namespace\n\n")

    lines.append("int main() {\n")
    lines.append(f"  pyc::gen::{top} dut;\n")
    lines.append(f"  Testbench<pyc::gen::{top}> tb(dut);\n\n")
    lines.append("  const char *schedule_env = std::getenv(\"PYC_TB_SCHEDULE\");\n")
    lines.append(
        f"  const std::filesystem::path schedule_path = schedule_env != nullptr && schedule_env[0] != '\\0' ? std::filesystem::path(schedule_env) : std::filesystem::path({json.dumps(str(schedule_path))});\n"
    )
    lines.append("  pyc::cpp::SidecarRunnerSchedule<kMaxEventWords, kDrivePortCount> schedule;\n")
    lines.append("  pyc::cpp::SidecarSchedule sidecar_schedule;\n")
    lines.append("  std::string sidecar_error;\n")
    lines.append("  if (!pyc::cpp::loadSidecarSchedule(schedule_path, &sidecar_schedule, &sidecar_error)) {\n")
    lines.append("    std::cerr << \"ERROR: failed to load sidecar schedule: \" << sidecar_error << \"\\n\";\n")
    lines.append("    return 1;\n")
    lines.append("  }\n")
    lines.append("  if (!pyc::cpp::convertSidecarToRunnerSchedule(sidecar_schedule, kDrivePortIds, &schedule, &sidecar_error)) {\n")
    lines.append("    std::cerr << \"ERROR: failed to convert sidecar schedule: \" << sidecar_error << \"\\n\";\n")
    lines.append("    return 1;\n")
    lines.append("  }\n\n")
    if rand_specs:
        lines.append("  // Random streams (deterministic).\n")
        for sn, _w, seed, _st, _ev in rand_specs:
            seed64 = int(seed) & ((1 << 64) - 1)
            lines.append(f"  std::uint64_t rng_{sn} = 0x{seed64:x}ull;\n")
        lines.append("\n")

    lines.append("  const char *trace_dir_env = std::getenv(\"PYC_TRACE_DIR\");\n")
    lines.append("  const bool trace_env_enabled = (trace_dir_env != nullptr) && (std::string(trace_dir_env).size() != 0);\n")
    lines.append("  if (trace_env_enabled) {\n")
    lines.append("    std::filesystem::path out_dir = std::filesystem::path(trace_dir_env);\n")
    lines.append(f"    out_dir /= \"tb_{iface.sym}\";\n")
    lines.append("    std::filesystem::create_directories(out_dir);\n")
    lines.append(f"    tb.enableVcd((out_dir / \"tb_{iface.sym}.vcd\").string(), /*top=*/\"tb_{iface.sym}\");\n")
    for sn in [*iface.in_names, *iface.out_names]:
        lines.append(f"    tb.vcdTrace(dut.{sn}, \"{sn}\");\n")
    lines.append("  }\n\n")

    if has_clocks:
        for c in t.clocks:
            dir_, sn, _ = iface.resolve(c.port)
            if dir_ != "in":
                raise SystemExit(f"clock must be an input port, got output: {c.port!r}")
            lines.append(
                f"  tb.addClock(dut.{sn}, /*halfPeriodSteps=*/{int(c.half_period_steps)}, /*phaseSteps=*/{int(c.phase_steps)}, /*startHigh=*/{str(bool(c.start_high)).lower()});\n"
            )
    if has_reset:
        lines.append(f"  tb.reset(dut.{rst_sn}, /*cyclesAsserted=*/{int(ca)}, /*cyclesDeasserted=*/{int(cd)});\n\n")

    lines.append(f"  const std::uint64_t timeout_cycles = {int(t.timeout_cycles)}ull;\n")
    lines.append(f"  bool ok = {str(t.finish_cycle is None).lower()};\n")
    lines.append("  std::size_t drive_frame_idx = 0;\n")
    lines.append("  std::size_t pre_expect_idx = 0;\n")
    lines.append("  std::size_t post_expect_idx = 0;\n")
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

    lines.append("    for (const auto &pattern : sidecar_schedule.periodic_drives) {\n")
    lines.append("      if (cyc >= pattern.start_cycle && cyc < pattern.end_cycle) applyPeriodicDrive(dut, pattern, cyc);\n")
    lines.append("    }\n")
    lines.append("    while (drive_frame_idx < schedule.drive_frames.size() && schedule.drive_frames[drive_frame_idx].cycle == cyc) {\n")
    lines.append("      applyDriveFrame(dut, schedule.drive_frames[drive_frame_idx]);\n")
    lines.append("      ++drive_frame_idx;\n")
    lines.append("    }\n")
    lines.append("    if (pre_expect_idx < schedule.pre_expect_events.size() && schedule.pre_expect_events[pre_expect_idx].cycle == cyc) {\n")
    lines.append("      pyc::cpp::detail::maybe_comb(dut);\n")
    lines.append("    }\n")
    lines.append("    while (pre_expect_idx < schedule.pre_expect_events.size() && schedule.pre_expect_events[pre_expect_idx].cycle == cyc) {\n")
    lines.append("      if (!checkExpect(dut, schedule.pre_expect_events[pre_expect_idx], \"pre\")) return 1;\n")
    lines.append("      ++pre_expect_idx;\n")
    lines.append("    }\n")
    if has_clocks:
        lines.append("    tb.runCycleAutoTrace(cyc, nullptr);\n")
    else:
        lines.append("    tb.runSteps(1);\n")
    lines.append("    while (post_expect_idx < schedule.post_expect_events.size() && schedule.post_expect_events[post_expect_idx].cycle == cyc) {\n")
    lines.append("      if (!checkExpect(dut, schedule.post_expect_events[post_expect_idx], \"post\")) return 1;\n")
    lines.append("      ++post_expect_idx;\n")
    lines.append("    }\n")
    if prints_at or prints_every:
        if prints_at:
            lines.append("    // Per-cycle prints.\n")
            lines.append("    switch (cyc) {\n")
            for cyc in sorted(prints_at.keys()):
                lines.append(f"    case {cyc}: {{\n")
                for fmt, ports in prints_at[cyc]:
                    msg_lit = json.dumps(f" {fmt}")
                    lines.append(f"      std::cerr << \"[tb] cyc=\" << cyc << {msg_lit}")
                    for raw, sn, w in ports:
                        raw_lit = json.dumps(f" {raw}=")
                        if w == 1:
                            lines.append(f" << {raw_lit} << dut.{sn}.value()")
                        else:
                            lines.append(f" << {raw_lit} << \"0x\" << std::hex << dut.{sn}.value() << std::dec")
                    lines.append(" << \"\\n\";\n")
                lines.append("      break; }\n")
            lines.append("    default: break;\n")
            lines.append("    }\n")
        if prints_every:
            lines.append("    // Periodic prints.\n")
            for fmt, st, ev, ports in prints_every:
                msg_lit = json.dumps(f" {fmt}")
                lines.append(f"    if (cyc >= {st}ull && ((cyc - {st}ull) % {ev}ull) == 0ull) {{\n")
                lines.append(f"      std::cerr << \"[tb] cyc=\" << cyc << {msg_lit}")
                for raw, sn, w in ports:
                    raw_lit = json.dumps(f" {raw}=")
                    if w == 1:
                        lines.append(f" << {raw_lit} << dut.{sn}.value()")
                    else:
                        lines.append(f" << {raw_lit} << \"0x\" << std::hex << dut.{sn}.value() << std::dec")
                lines.append(" << \"\\n\";\n")
                lines.append("    }\n")
    if t.finish_cycle is not None:
        lines.append(f"    if (cyc >= {int(t.finish_cycle)}ull) {{ ok = true; break; }}\n")
    lines.append("  }\n\n")
    lines.append("  if (!ok) {\n")
    lines.append("    std::cerr << \"TIMEOUT: finish cycle not reached within \" << timeout_cycles << \" cycles\\n\";\n")
    lines.append("    return 1;\n")
    lines.append("  }\n")
    lines.append("  return 0;\n")
    lines.append("}\n")
    return "".join(lines)


def _render_tb_cpp(
    iface: _TopIface,
    t: Tb,
    *,
    trace_plan: TracePlan | None = None,
    schedule_mode: str = "inline",
    schedule_path: Path | None = None,
) -> str:
    mode = str(schedule_mode).strip().lower()
    if mode == "sidecar":
        return _render_tb_cpp_sidecar(
            iface,
            t,
            trace_plan=trace_plan,
            schedule_path=schedule_path,
        )
    if mode != "inline":
        raise SystemExit(f"unsupported C++ TB schedule mode: {schedule_mode!r}")

    has_clocks = bool(t.clocks)
    has_reset = t.reset_spec is not None
    if has_reset and not has_clocks:
        raise SystemExit("tb() with reset requires at least one clock via t.clock(...)")

    top = _sanitize_id(iface.sym)
    hdr = f"{iface.sym}.hpp"

    def mask_value(v: int | bool, width: int) -> int:
        if isinstance(v, bool):
            vv = 1 if v else 0
        else:
            vv = int(v)
        if width <= 0:
            raise SystemExit("internal: invalid width")
        return vv & ((1 << width) - 1)

    def wire_literal(v: int | bool, width: int) -> str:
        vv = mask_value(v, width)
        words = (width + 63) // 64
        raw_words = []
        for i in range(words):
            raw_words.append(f"0x{((vv >> (64 * i)) & ((1 << 64) - 1)):x}ull")
        return f"pyc::cpp::Wire<{width}>({{{', '.join(raw_words)}}})"

    def flat_indices(shape: tuple[int, ...]) -> list[tuple[int, ...]]:
        if not shape:
            return [()]
        out: list[tuple[int, ...]] = []

        def walk(prefix: tuple[int, ...], rest: tuple[int, ...]) -> None:
            if not rest:
                out.append(prefix)
                return
            for i in range(int(rest[0])):
                walk((*prefix, i), rest[1:])

        walk((), tuple(shape))
        return out

    def cpp_access(sn: str, idx: tuple[int, ...]) -> str:
        return "dut." + str(sn) + "".join(f"[{i}]" for i in idx)

    def lane_value(v: int | bool, info: _PortInfo, lane: int) -> int:
        vv = mask_value(v, info.total_width)
        return (vv >> (lane * info.leaf_width)) & ((1 << info.leaf_width) - 1)

    def drive_const_lines(sn: str, value: int | bool, ty: str, *, indent: str) -> list[str]:
        info = _port_info(ty)
        if not info.is_vector:
            return [f"{indent}dut.{sn} = {wire_literal(value, info.leaf_width)};\n"]
        out: list[str] = []
        for lane, idx in enumerate(flat_indices(info.shape)):
            out.append(f"{indent}{cpp_access(sn, idx)} = {wire_literal(lane_value(value, info, lane), info.leaf_width)};\n")
        return out

    def drive_expr_lines(sn: str, expr: str, ty: str, *, indent: str) -> list[str]:
        info = _port_info(ty)
        if info.total_width > 64:
            raise SystemExit(f"random() for i{info.total_width} not supported in C++ TB generator (prototype limitation)")
        if not info.is_vector:
            return [f"{indent}dut.{sn} = pyc::cpp::Wire<{info.leaf_width}>({expr});\n"]
        out: list[str] = []
        for lane, idx in enumerate(flat_indices(info.shape)):
            mask = (1 << info.leaf_width) - 1
            shift = lane * info.leaf_width
            out.append(
                f"{indent}{cpp_access(sn, idx)} = pyc::cpp::Wire<{info.leaf_width}>((({expr}) >> {shift}) & 0x{mask:x}ull);\n"
            )
        return out

    def packed_value_expr(sn: str, ty: str) -> str:
        info = _port_info(ty)
        if info.total_width > 64:
            raise SystemExit(f"print() for i{info.total_width} not supported in C++ TB generator (prototype limitation)")
        if not info.is_vector:
            return f"dut.{sn}.value()"
        parts: list[str] = []
        for lane, idx in enumerate(flat_indices(info.shape)):
            mask = (1 << info.leaf_width) - 1
            shift = lane * info.leaf_width
            access = cpp_access(sn, idx)
            term = f"(({access}.value() & 0x{mask:x}ull) << {shift})"
            parts.append(term)
        return "(" + " | ".join(parts or ["0ull"]) + ")"

    def expect_lines(sn: str, value: int | bool, msg: str, ty: str, *, indent: str, phase: str) -> list[str]:
        info = _port_info(ty)
        if not info.is_vector:
            vv = mask_value(value, info.leaf_width)
            exp = wire_literal(value, info.leaf_width)
            if info.leaf_width == 1:
                return [
                    f"{indent}if (dut.{sn}.value() != {vv}u) {{ std::cerr << \"ERROR{phase}: {msg}: got=\" << dut.{sn}.value() << \" exp={vv}\\n\"; return 1; }}\n"
                ]
            if info.leaf_width <= 64:
                return [
                    f"{indent}if (dut.{sn}.value() != {vv}u) {{ std::cerr << \"ERROR{phase}: {msg}: got=0x\" << std::hex << dut.{sn}.value() << \" exp=0x{vv:x}\" << std::dec << \"\\n\"; return 1; }}\n"
                ]
            return [f"{indent}if (!(dut.{sn} == {exp})) {{ std::cerr << \"ERROR{phase}: {msg}\\n\"; return 1; }}\n"]

        out: list[str] = []
        for lane, idx in enumerate(flat_indices(info.shape)):
            access = cpp_access(sn, idx)
            vv = lane_value(value, info, lane)
            lane_msg = f"{msg}[{']['.join(str(i) for i in idx)}]"
            if info.leaf_width == 1:
                out.append(
                    f"{indent}if ({access}.value() != {vv}u) {{ std::cerr << \"ERROR{phase}: {lane_msg}: got=\" << {access}.value() << \" exp={vv}\\n\"; return 1; }}\n"
                )
            elif info.leaf_width <= 64:
                out.append(
                    f"{indent}if ({access}.value() != {vv}u) {{ std::cerr << \"ERROR{phase}: {lane_msg}: got=0x\" << std::hex << {access}.value() << \" exp=0x{vv:x}\" << std::dec << \"\\n\"; return 1; }}\n"
                )
            else:
                out.append(
                    f"{indent}if (!({access} == {wire_literal(vv, info.leaf_width)})) {{ std::cerr << \"ERROR{phase}: {lane_msg}\\n\"; return 1; }}\n"
                )
        return out

    # Group actions by cycle for compact emission.
    drives_by: dict[int, list[tuple[str, int | bool, str]]] = {}
    expects_pre_by: dict[int, list[tuple[str, int | bool, str | None, str]]] = {}
    expects_post_by: dict[int, list[tuple[str, int | bool, str | None, str]]] = {}
    prints_at: dict[int, list[tuple[str, list[tuple[str, str, int]]]]] = {}
    prints_every: list[tuple[str, int, int, list[tuple[str, str, int]]]] = []
    for d in t.drives:
        dir_, sn, ty = iface.resolve(d.port)
        if dir_ != "in":
            raise SystemExit(f"drive() requires input port, got output: {d.port!r}")
        drives_by.setdefault(int(d.at), []).append((sn, d.value, ty))
    for e in t.expects:
        _dir, sn, ty = iface.resolve(e.port)
        ph = str(getattr(e, "phase", "post")).strip().lower()
        if ph == "pre":
            expects_pre_by.setdefault(int(e.at), []).append((sn, e.value, e.msg, ty))
        else:
            expects_post_by.setdefault(int(e.at), []).append((sn, e.value, e.msg, ty))

    for p in getattr(t, "prints", []):
        fmt = str(p.fmt)
        port_specs: list[tuple[str, str, int]] = []
        for raw in p.ports:
            _dir, sn, ty = iface.resolve(raw)
            w = _as_int_width(ty)
            if w > 64:
                raise SystemExit(f"print() for i{w} not supported in C++ TB generator (prototype limitation)")
            port_specs.append((str(raw), sn, w))
        if p.at is not None:
            prints_at.setdefault(int(p.at), []).append((fmt, port_specs))
        else:
            st = 0 if p.start is None else int(p.start)
            ev = 1 if p.every is None else int(p.every)
            prints_every.append((fmt, st, ev, port_specs))

    rand_specs: list[tuple[str, int, str, int, int, int]] = []
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
            rand_specs.append((sn, w, ty, int(r.seed), int(r.start), int(r.every)))

    clk_sn = ""
    rst_sn = ""
    ca = 0
    cd = 0
    if has_clocks:
        clk = t.clocks[0].port
        _, clk_sn, _clk_ty = iface.resolve(clk)
    if has_reset:
        rst = t.reset_spec.port
        _, rst_sn, _rst_ty = iface.resolve(rst)
        ca = int(t.reset_spec.cycles_asserted)
        cd = int(t.reset_spec.cycles_deasserted)

    lines: list[str] = []
    lines.append("// Generated by pycircuit (prototype)\n")
    lines.append("#include <algorithm>\n")
    lines.append("#include <array>\n")
    lines.append("#include <cstdint>\n")
    lines.append("#include <cstdlib>\n")
    lines.append("#include <filesystem>\n")
    lines.append("#include <iostream>\n\n")
    lines.append("#include <iterator>\n")
    lines.append("#include <string>\n")
    lines.append("#include <string_view>\n\n")
    lines.append("#include <cpp/pyc_tb.hpp>\n\n")
    lines.append("#include <cpp/pyc_trace_bin.hpp>\n\n")
    lines.append(f"#include \"{hdr}\"\n\n")
    lines.append("using pyc::cpp::Testbench;\n\n")
    lines.append("int main() {\n")
    lines.append(f"  pyc::gen::{top} dut;\n")
    lines.append(f"  Testbench<pyc::gen::{top}> tb(dut);\n\n")
    lines.append("  std::optional<pyc::cpp::PycTraceBinWriter> bin_trace;\n\n")
    if rand_specs:
        lines.append("  // Random streams (deterministic).\n")
        for sn, _w, _ty, seed, _st, _ev in rand_specs:
            seed64 = int(seed) & ((1 << 64) - 1)
            lines.append(f"  std::uint64_t rng_{sn} = 0x{seed64:x}ull;\n")
        lines.append("\n")
    lines.append("  // Optional traces (Decision 0145).\n")
    lines.append("  const char *trace_dir_env = std::getenv(\"PYC_TRACE_DIR\");\n")
    lines.append(
        "  const bool trace_env_enabled = (trace_dir_env != nullptr) && (std::string(trace_dir_env).size() != 0);\n"
    )
    lines.append(f"  const bool trace_cfg_enabled = {str(bool(trace_plan and trace_plan.enabled_signals)).lower()};\n")
    lines.append("  if (trace_env_enabled || trace_cfg_enabled) {\n")
    lines.append(
        "    std::filesystem::path out_dir = trace_env_enabled ? std::filesystem::path(trace_dir_env) : std::filesystem::path(\".\");\n"
    )
    lines.append(f"    out_dir /= \"tb_{iface.sym}\";\n")
    lines.append("    std::filesystem::create_directories(out_dir);\n")
    lines.append(f"    tb.enableVcd((out_dir / \"tb_{iface.sym}.vcd\").string(), /*top=*/\"tb_{iface.sym}\");\n")
    if trace_plan and trace_plan.enabled_signals:
        sigs = list(trace_plan.enabled_signals)
        insts = list(trace_plan.enabled_instances)
        sig_obs = dict(getattr(trace_plan, "signal_obs", {}) or {})
        # Ensure stable ordering for reproducible generated TB text.
        sigs = sorted(set(str(s) for s in sigs))
        insts = sorted(set(str(s) for s in insts))
        sig_obs = {str(k): str(v).strip().lower() for k, v in sig_obs.items() if str(k) in set(sigs)}
        tick_sigs = sorted([k for k, v in sig_obs.items() if v == "tick"])
        xfer_sigs = sorted([k for k, v in sig_obs.items() if v == "xfer"])
        lines.append("    // Trace config selected signals (generated from trace DSL).\n")
        lines.append("    static constexpr std::string_view kEnabledInstances[] = {\n")
        for s in insts:
            lines.append(f"      {json.dumps(s)},\n")
        lines.append("    };\n")
        lines.append("    static constexpr std::string_view kEnabledSignals[] = {\n")
        for s in sigs:
            lines.append(f"      {json.dumps(s)},\n")
        lines.append("    };\n")
        lines.append("    // Per-signal observation points (Decision 0113 / 0140).\n")
        lines.append(
            f"    static constexpr std::array<std::string_view, {len(tick_sigs)}> kTickObsSignals = {{\n"
        )
        for s in tick_sigs:
            lines.append(f"      {json.dumps(s)},\n")
        lines.append("    };\n")
        lines.append(
            f"    static constexpr std::array<std::string_view, {len(xfer_sigs)}> kXferObsSignals = {{\n"
        )
        for s in xfer_sigs:
            lines.append(f"      {json.dumps(s)},\n")
        lines.append("    };\n")
        lines.append(
            "    auto enabledInstance = [&](std::string_view p) -> bool {\n"
            "      return std::binary_search(std::begin(kEnabledInstances), std::end(kEnabledInstances), p);\n"
            "    };\n"
        )
        lines.append(
            "    auto enabledSignal = [&](std::string_view p) -> bool {\n"
            "      return std::binary_search(std::begin(kEnabledSignals), std::end(kEnabledSignals), p);\n"
            "    };\n"
        )
        lines.append(
            "    auto sampleAtForSignal = [&](std::string_view p) -> pyc::cpp::PycTraceBinWriter::SampleAt {\n"
            "      if (std::binary_search(kTickObsSignals.begin(), kTickObsSignals.end(), p))\n"
            "        return pyc::cpp::PycTraceBinWriter::SampleAt::Tick;\n"
            "      if (std::binary_search(kXferObsSignals.begin(), kXferObsSignals.end(), p))\n"
            "        return pyc::cpp::PycTraceBinWriter::SampleAt::Commit;\n"
            "      return pyc::cpp::PycTraceBinWriter::SampleAt::Auto;\n"
            "    };\n"
        )
        lines.append("    dut.pyc_trace_vcd(tb, /*prefix=*/\"dut\", enabledInstance, enabledSignal);\n")
        lines.append("    // Binary trace event stream (Decision 0016).\n")
        lines.append("    pyc::cpp::ProbeRegistry reg;\n")
        lines.append("    dut.pyc_register_probes(reg, /*prefix=*/\"dut\");\n")
        lines.append("    std::vector<const pyc::cpp::ProbeRegistry::Entry *> trace_probes;\n")
        lines.append("    std::vector<pyc::cpp::PycTraceBinWriter::SampleAt> trace_sample_at;\n")
        lines.append("    trace_probes.reserve(std::size(kEnabledSignals));\n")
        lines.append("    trace_sample_at.reserve(std::size(kEnabledSignals));\n")
        lines.append("    for (auto p : kEnabledSignals) {\n")
        lines.append(
            "      if (const auto *e = reg.findByPath(p)) { trace_probes.push_back(e); trace_sample_at.push_back(sampleAtForSignal(p)); }\n"
        )
        lines.append("    }\n")
        lines.append("    bin_trace.emplace();\n")
        lines.append(
            f"    if (!bin_trace->open(out_dir / \"tb_{iface.sym}.pyctrace\", std::move(trace_probes), /*external_manifest=*/true, std::move(trace_sample_at))) {{\n"
        )
        lines.append("      std::cerr << \"WARN: failed to open pyc binary trace output\\n\";\n")
        lines.append("      bin_trace.reset();\n")
        lines.append("    }\n")
    else:
        for sn in [*iface.in_names, *iface.out_names]:
            lines.append(f"    tb.vcdTrace(dut.{sn}, \"{sn}\");\n")
    lines.append("  }\n\n")

    if has_clocks:
        for c in t.clocks:
            dir_, sn, _ = iface.resolve(c.port)
            if dir_ != "in":
                raise SystemExit(f"clock must be an input port, got output: {c.port!r}")
            lines.append(
                f"  tb.addClock(dut.{sn}, /*halfPeriodSteps=*/{int(c.half_period_steps)}, /*phaseSteps=*/{int(c.phase_steps)}, /*startHigh=*/{str(bool(c.start_high)).lower()});\n"
            )
    if has_reset:
        lines.append("  if (bin_trace) {\n")
        lines.append("    const std::uint64_t __pyc_reset_assert_cycle = 0ull;\n")
        lines.append(
            f"    const std::uint64_t __pyc_reset_deassert_cycle = ({int(ca)}ull == 0ull) ? 0ull : ({int(ca)}ull - 1ull);\n"
        )
        lines.append(
            "    bin_trace->writeInvalidate(__pyc_reset_assert_cycle, pyc::cpp::PycTraceBinWriter::Phase::Tick, "
            "\"global\", pyc::cpp::PycTraceBinWriter::InvalidateReason::WarmReset, \"global\", \"tb.reset\");\n"
        )
        lines.append(
            "    bin_trace->writeResetAssert(__pyc_reset_assert_cycle, pyc::cpp::PycTraceBinWriter::Phase::Tick, "
            "\"global\", pyc::cpp::PycTraceBinWriter::ResetKind::Warm);\n"
        )
        lines.append(
            "    bin_trace->writeResetDeassert(__pyc_reset_deassert_cycle, pyc::cpp::PycTraceBinWriter::Phase::Tick, "
            "\"global\", pyc::cpp::PycTraceBinWriter::ResetKind::Warm);\n"
        )
        lines.append("  }\n")
        lines.append(f"  tb.reset(dut.{rst_sn}, /*cyclesAsserted=*/{int(ca)}, /*cyclesDeasserted=*/{int(cd)});\n\n")

    if trace_plan and trace_plan.enabled_signals and trace_plan.window:
        begin = trace_plan.window.begin_cycle
        end = trace_plan.window.end_cycle
        if begin is not None and end is not None:
            hp = int(t.clocks[0].half_period_steps) if has_clocks else 0
            steps_per_cycle = 1 if not has_clocks else max(1, 2 * hp)
            lines.append("  // Bounded trace window (cycles are relative to post-reset cycle 0).\n")
            lines.append("  if (trace_cfg_enabled) {\n")
            lines.append("    const std::uint64_t trace_base_steps = tb.timeSteps();\n")
            lines.append(f"    const std::uint64_t steps_per_cycle = {int(steps_per_cycle)}ull;\n")
            lines.append(
                f"    tb.setVcdWindow(trace_base_steps + ({int(begin)}ull * steps_per_cycle), "
                f"trace_base_steps + (({int(end)}ull + 1ull) * steps_per_cycle) - 1ull);\n"
            )
            lines.append("  }\n\n")

    lines.append(f"  const std::uint64_t timeout_cycles = {int(t.timeout_cycles)}ull;\n")
    lines.append("  bool ok = false;\n")
    lines.append("  for (std::uint64_t cyc = 0; cyc < timeout_cycles; ++cyc) {\n")

    if rand_specs:
        lines.append("    // Random drives for this cycle (applied before explicit drives).\n")
        for sn, w, ty, _seed, st, ev in rand_specs:
            mask = (1 << w) - 1 if w < 64 else (1 << 64) - 1
            lines.append(
                f"    if (cyc >= {int(st)}ull && ((cyc - {int(st)}ull) % {int(ev)}ull) == 0ull) {{\n"
                f"      rng_{sn} = rng_{sn} * 6364136223846793005ull + 1ull;\n"
            )
            lines.extend(drive_expr_lines(sn, f"(0x{mask:x}ull & rng_{sn})", ty, indent="      "))
            lines.append("    }\n")
        lines.append("\n")

    if drives_by:
        lines.append("    switch (cyc) {\n")
        for cyc in sorted(drives_by.keys()):
            lines.append(f"    case {cyc}:\n")
            for sn, val, ty in drives_by[cyc]:
                lines.extend(drive_const_lines(sn, val, ty, indent="      "))
            lines.append("      break;\n")
        lines.append("    default: break;\n")
        lines.append("    }\n")

    if expects_pre_by:
        # In the generated C++ TB, combinational logic only updates when we call
        # `dut.comb()`. For pre-step (TICK-OBS) sampling, ensure values reflect
        # the drives applied for this cycle before checking expectations.
        lines.append("    dut.comb();\n")
        lines.append("    // Pre-step expects for this cycle.\n")
        lines.append("    switch (cyc) {\n")
        for cyc in sorted(expects_pre_by.keys()):
            lines.append(f"    case {cyc}: {{\n")
            for sn, val, msg, ty in expects_pre_by[cyc]:
                m = msg if msg is not None else f"{sn} mismatch"
                lines.extend(expect_lines(sn, val, m, ty, indent="      ", phase="(pre)"))
            lines.append("      break; }\n")
        lines.append("    default: break;\n")
        lines.append("    }\n")

    if has_clocks:
        if trace_plan and trace_plan.enabled_signals and trace_plan.window:
            begin = trace_plan.window.begin_cycle
            end = trace_plan.window.end_cycle
            if begin is not None and end is not None:
                lines.append(
                    f"    tb.runCycleAutoTrace(cyc, (bin_trace && cyc >= {int(begin)}ull && cyc <= {int(end)}ull) ? &*bin_trace : nullptr);\n"
                )
            else:
                lines.append("    tb.runCycleAutoTrace(cyc, bin_trace ? &*bin_trace : nullptr);\n")
        else:
            lines.append("    tb.runCycleAutoTrace(cyc, bin_trace ? &*bin_trace : nullptr);\n")
    else:
        lines.append("    tb.runSteps(1);\n")

    # Binary trace sampling is performed inside Testbench stepping (Decision 0113).

    if expects_post_by:
        lines.append("    // Post-step expects for this cycle.\n")
        lines.append("    switch (cyc) {\n")
        for cyc in sorted(expects_post_by.keys()):
            lines.append(f"    case {cyc}: {{\n")
            for sn, val, msg, ty in expects_post_by[cyc]:
                m = msg if msg is not None else f"{sn} mismatch"
                lines.extend(expect_lines(sn, val, m, ty, indent="      ", phase=""))
            lines.append("      break; }\n")
        lines.append("    default: break;\n")
        lines.append("    }\n")

    if prints_at or prints_every:
        if prints_at:
            lines.append("    // Per-cycle prints.\n")
            lines.append("    switch (cyc) {\n")
            for cyc in sorted(prints_at.keys()):
                lines.append(f"    case {cyc}: {{\n")
                for fmt, ports in prints_at[cyc]:
                    msg_lit = json.dumps(f" {fmt}")
                    lines.append(f"      std::cerr << \"[tb] cyc=\" << cyc << {msg_lit}")
                    for raw, sn, w in ports:
                        raw_lit = json.dumps(f" {raw}=")
                        expr = packed_value_expr(sn, iface.resolve(raw)[2])
                        if w == 1:
                            lines.append(f" << {raw_lit} << {expr}")
                        else:
                            lines.append(f" << {raw_lit} << \"0x\" << std::hex << {expr} << std::dec")
                    lines.append(" << \"\\n\";\n")
                lines.append("      break; }\n")
            lines.append("    default: break;\n")
            lines.append("    }\n")
        if prints_every:
            lines.append("    // Periodic prints.\n")
            for fmt, st, ev, ports in prints_every:
                msg_lit = json.dumps(f" {fmt}")
                lines.append(f"    if (cyc >= {st}ull && ((cyc - {st}ull) % {ev}ull) == 0ull) {{\n")
                lines.append(f"      std::cerr << \"[tb] cyc=\" << cyc << {msg_lit}")
                for raw, sn, w in ports:
                    raw_lit = json.dumps(f" {raw}=")
                    expr = packed_value_expr(sn, iface.resolve(raw)[2])
                    if w == 1:
                        lines.append(f" << {raw_lit} << {expr}")
                    else:
                        lines.append(f" << {raw_lit} << \"0x\" << std::hex << {expr} << std::dec")
                lines.append(" << \"\\n\";\n")
                lines.append("    }\n")

    if t.finish_cycle is not None:
        lines.append(f"    if (cyc == {int(t.finish_cycle)}ull) {{ ok = true; break; }}\n")

    lines.append("  }\n")
    lines.append("  if (!ok) { std::cerr << \"TIMEOUT\\n\"; return 1; }\n")
    lines.append("  std::cerr << \"OK\\n\";\n")
    lines.append("  return 0;\n")
    lines.append("}\n")
    return "".join(lines)


def _render_tb_sv(iface: _TopIface, t: Tb, *, trace_plan: TracePlan | None = None) -> str:
    has_clocks = bool(t.clocks)
    has_reset = t.reset_spec is not None
    if has_reset and not has_clocks:
        raise SystemExit("tb() with reset requires at least one clock via t.clock(...)")

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
        info = _port_info(ty)
        # Match VerilogEmitter packed vector ports (flat bus), not unpacked arrays.
        w = info.total_width
        if w == 1:
            return f"  logic {name};\n"
        return f"  logic [{w - 1}:0] {name};\n"

    def flat_indices(shape: tuple[int, ...]) -> list[tuple[int, ...]]:
        if not shape:
            return [()]
        out: list[tuple[int, ...]] = []

        def walk(prefix: tuple[int, ...], rest: tuple[int, ...]) -> None:
            if not rest:
                out.append(prefix)
                return
            for i in range(int(rest[0])):
                walk((*prefix, i), rest[1:])

        walk((), tuple(shape))
        return out


    def sv_packed_expr(name: str, ty: str) -> str:
        # TB signals are declared as packed buses matching DUT ports.
        _ = ty
        return str(name)

    def sv_drive_const_lines(name: str, value: int | bool, ty: str, *, indent: str) -> list[str]:
        info = _port_info(ty)
        return [f"{indent}{name} = {sv_lit(info.total_width, value)};\n"]

    def sv_drive_expr_lines(name: str, expr: str, ty: str, *, indent: str) -> list[str]:
        info = _port_info(ty)
        if info.total_width > 64:
            raise SystemExit(f"random() for i{info.total_width} not supported in SV TB generator (prototype limitation)")
        hi = 63 if info.total_width >= 64 else (info.total_width - 1)
        return [f"{indent}{name} = {expr}[{hi}:0];\n"]

    def sv_expect_lines(name: str, value: int | bool, msg: str, ty: str, *, indent: str, phase: str) -> list[str]:
        info = _port_info(ty)
        prefix = "PRE: " if phase == "pre" else ""
        return [f"{indent}if ({name} !== {sv_lit(info.total_width, value)}) $fatal(1, \"{prefix}{msg}\");\n"]

    drives_by: dict[int, list[tuple[str, int | bool, str]]] = {}
    expects_pre_by: dict[int, list[tuple[str, int | bool, str | None, str]]] = {}
    expects_post_by: dict[int, list[tuple[str, int | bool, str | None, str]]] = {}
    prints_at: dict[int, list[tuple[str, list[tuple[str, str, str]]]]] = {}
    prints_every: list[tuple[str, int, int, list[tuple[str, str, str]]]] = []
    for d in t.drives:
        dir_, sn, ty = iface.resolve(d.port)
        if dir_ != "in":
            raise SystemExit(f"drive() requires input port, got output: {d.port!r}")
        drives_by.setdefault(int(d.at), []).append((sn, d.value, ty))
    for e in t.expects:
        _dir, sn, ty = iface.resolve(e.port)
        ph = str(getattr(e, "phase", "post")).strip().lower()
        if ph == "pre":
            expects_pre_by.setdefault(int(e.at), []).append((sn, e.value, e.msg, ty))
        else:
            expects_post_by.setdefault(int(e.at), []).append((sn, e.value, e.msg, ty))
    for p in getattr(t, "prints", []):
        fmt = str(p.fmt)
        ports = []
        for raw in p.ports:
            _dir, sn, ty = iface.resolve(raw)
            ports.append((str(raw), sn, ty))
        if p.at is not None:
            prints_at.setdefault(int(p.at), []).append((fmt, ports))
        else:
            st = 0 if p.start is None else int(p.start)
            ev = 1 if p.every is None else int(p.every)
            prints_every.append((fmt, st, ev, ports))

    rand_specs: list[tuple[str, int, str, int, int, int]] = []
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
            rand_specs.append((sn, w, ty, int(r.seed), int(r.start), int(r.every)))

    clk_sn = ""
    rst_sn = ""
    ca = 0
    cd = 0
    if has_clocks:
        clk = t.clocks[0].port
        _, clk_sn, _clk_ty = iface.resolve(clk)
    if has_reset:
        rst = t.reset_spec.port
        _, rst_sn, _rst_ty = iface.resolve(rst)
        ca = int(t.reset_spec.cycles_asserted)
        cd = int(t.reset_spec.cycles_deasserted)

    lines: list[str] = []
    lines.append("// Generated by pycircuit (prototype)\n")
    lines.append("`timescale 1ns/1ps\n\n")
    lines.append(f"module tb_{top};\n")
    lines.append("  /* verilator lint_off UNUSEDSIGNAL */\n")

    for n, ty in zip(iface.in_names, iface.in_tys):
        lines.append(decl(n, ty))
    for n, ty in zip(iface.out_names, iface.out_tys):
        lines.append(decl(n, ty))
    if rand_specs:
        lines.append("\n")
        lines.append("  // Random stream state.\n")
        for sn, _w, _ty, _seed, _st, _ev in rand_specs:
            lines.append(f"  longint unsigned rng_{sn};\n")
    lines.append("  integer timeout_cycles;\n")
    lines.append("  integer cyc;\n")
    lines.append("  logic __pyc_tb_active;\n")
    lines.append("  initial __pyc_tb_active = 1'b0;\n")
    lines.append("  logic __pyc_tb_done;\n")
    lines.append("  initial __pyc_tb_done = 1'b0;\n")
    lines.append("\n")

    lines.append(f"  {mod_name} dut (\n")
    conns = [f"    .{sn}({sn})" for sn in [*iface.in_names, *iface.out_names]]
    lines.append(",\n".join(conns))
    lines.append("\n  );\n\n")

    # Optional VCD tracing via `$dumpvars` (Decision 0145).
    if trace_plan and trace_plan.enabled_signals:
        # Decision 0023: enabled_signals are canonical `<instance_path>:<field_path>` strings.
        # SystemVerilog `$dumpvars` expects hierarchical references, so map ":" -> ".".
        sigs = sorted(set(str(s) for s in trace_plan.enabled_signals))

        def canonical_to_sv_ref(p: str) -> str:
            inst, sep, field = str(p).partition(":")
            if not sep:
                return str(p)
            if not inst:
                return _sanitize_id(field)
            if not field:
                return inst
            # Verilog/SV identifiers cannot contain `.` or `[]` separators used
            # by canonical field paths (Decisions 0009/0024). The Verilog
            # backend applies `_sanitize_id` on port names, so do the same here.
            return f"{inst}.{_sanitize_id(field)}"

        sv_sigs = sorted(set(canonical_to_sv_ref(s) for s in sigs))
        lines.append("  // Optional traces (generated from trace DSL).\n")
        lines.append("  initial begin : __pyc_tb_trace\n")
        lines.append(f"    $dumpfile(\"tb_{top}.vcd\");\n")
        # Chunk long `$dumpvars` arg lists to keep tool limits reasonable.
        chunk = 64
        for i in range(0, len(sv_sigs), chunk):
            args = ", ".join(sv_sigs[i : i + chunk])
            lines.append(f"    $dumpvars(0, {args});\n")
        if trace_plan.window and trace_plan.window.begin_cycle is not None and trace_plan.window.end_cycle is not None:
            if int(trace_plan.window.begin_cycle) > 0:
                lines.append("    $dumpoff;\n")
        lines.append("  end\n\n")

    # Clock generation: currently only supports the first clock.
    if has_clocks:
        hp = int(t.clocks[0].half_period_steps)
        if hp != 1:
            lines.append("  // NOTE: half_period_steps != 1 is approximated by scaling delay.\n")
        lines.append("  initial begin\n")
        lines.append(f"    {clk_sn} = {1 if (t.clocks and t.clocks[0].start_high) else 0};\n")
        lines.append("  end\n")
        lines.append(f"  always #{hp} {clk_sn} = ~{clk_sn};\n\n")

    # Main stimulus loop.
    lines.append("  initial begin : __pyc_tb_main\n")
    # Initialize all driven inputs to 0.
    for sn, ty in zip(iface.in_names, iface.in_tys):
        if sn == clk_sn:
            continue
        lines.extend(sv_drive_const_lines(sn, 0, ty, indent="    "))
    lines.append("    __pyc_tb_active = 1'b0;\n")
    lines.append("    __pyc_tb_done = 1'b0;\n")
    if rand_specs:
        lines.append("\n")
        lines.append("    // Random stream seeds.\n")
        for sn, _w, _ty, seed, _st, _ev in rand_specs:
            seed64 = int(seed) & ((1 << 64) - 1)
            lines.append(f"    rng_{sn} = 64'h{seed64:016x};\n")
    lines.append("\n")
    if has_reset:
        lines.append(f"    {rst_sn} = 1'b1;\n")
        lines.append(f"    repeat ({int(ca)}) @(posedge {clk_sn});\n")
        # Deassert reset away from a posedge to avoid races with posedge-triggered state.
        lines.append(f"    @(negedge {clk_sn});\n")
        lines.append(f"    {rst_sn} = 1'b0;\n")
        lines.append(f"    repeat ({int(cd)}) @(posedge {clk_sn});\n")
        # Ensure cycle 0 starts on a negedge after any post-reset settle cycles.
        lines.append(f"    if ({int(cd)} != 0) @(negedge {clk_sn});\n\n")
    elif has_clocks:
        # Align stimulus so cycle 0 drives are applied on a negedge, avoiding races
        # with posedge-triggered sequential logic in the DUT.
        lines.append(f"    @(negedge {clk_sn});\n\n")

    lines.append(f"    timeout_cycles = {int(t.timeout_cycles)};\n")
    lines.append("    for (cyc = 0; cyc < timeout_cycles; cyc = cyc + 1) begin\n")

    if trace_plan and trace_plan.enabled_signals and trace_plan.window:
        b = trace_plan.window.begin_cycle
        e = trace_plan.window.end_cycle
        if b is not None and e is not None:
            lines.append("      // Trace window toggles.\n")
            lines.append(f"      if (cyc == {int(b)}) $dumpon;\n")
            lines.append(f"      if (cyc == {int(e) + 1}) $dumpoff;\n\n")

    if rand_specs:
        lines.append("      // Random drives for this cycle (applied before explicit drives).\n")
        for sn, _w, ty, _seed, st, ev in rand_specs:
            lines.append(f"      if (cyc >= {int(st)} && (((cyc - {int(st)}) % {int(ev)}) == 0)) begin\n")
            lines.append("        // LCG: state = state * 6364136223846793005 + 1.\n")
            lines.append(f"        rng_{sn} = (rng_{sn} * 64'd6364136223846793005) + 64'd1;\n")
            lines.extend(sv_drive_expr_lines(sn, f"rng_{sn}", ty, indent="        "))
            lines.append("      end\n")
        lines.append("\n")

    if drives_by:
        lines.append("      // Drives for this cycle (applied before posedge).\n")
        lines.append("      unique case (cyc)\n")
        for cyc in sorted(drives_by.keys()):
            lines.append(f"        {cyc}: begin\n")
            for sn, val, ty in drives_by[cyc]:
                lines.extend(sv_drive_const_lines(sn, val, ty, indent="          "))
            lines.append("        end\n")
        lines.append("        default: begin end\n")
        lines.append("      endcase\n")

    if expects_pre_by:
        # Allow a delta-cycle for combinational logic to settle after procedural
        # drives in this TB process. This keeps pre-step sampling stable and
        # avoids racey reads of DUT outputs.
        lines.append("      #0;\n")
        lines.append("      // Pre-step expects for this cycle (checked before posedge).\n")
        lines.append("      unique case (cyc)\n")
        for cyc in sorted(expects_pre_by.keys()):
            lines.append(f"        {cyc}: begin\n")
            for sn, val, msg, ty in expects_pre_by[cyc]:
                m = msg if msg is not None else f"{sn} mismatch"
                lines.extend(sv_expect_lines(sn, val, m, ty, indent="          ", phase="pre"))
            lines.append("        end\n")
        lines.append("        default: begin end\n")
        lines.append("      endcase\n")

    if has_clocks:
        lines.append(f"      @(posedge {clk_sn});\n")
        lines.append(f"      @(negedge {clk_sn});\n")
    else:
        lines.append("      #1;\n")
    lines.append("      __pyc_tb_active = 1'b1;\n")

    if expects_post_by:
        lines.append("      // Expects for this cycle (checked after posedge updates).\n")
        lines.append("      unique case (cyc)\n")
        for cyc in sorted(expects_post_by.keys()):
            lines.append(f"        {cyc}: begin\n")
            for sn, val, msg, ty in expects_post_by[cyc]:
                m = msg if msg is not None else f"{sn} mismatch"
                lines.extend(sv_expect_lines(sn, val, m, ty, indent="          ", phase="post"))
            lines.append("        end\n")
        lines.append("        default: begin end\n")
        lines.append("      endcase\n")

    if prints_at:
        lines.append("      // Per-cycle prints.\n")
        lines.append("      unique case (cyc)\n")
        for cyc in sorted(prints_at.keys()):
            lines.append(f"        {cyc}: begin\n")
            for fmt, ports in prints_at[cyc]:
                esc = str(fmt).replace("\\", "\\\\").replace("\"", "\\\"")
                if ports:
                    suffix = "".join(f" {raw}=%0h" for raw, _sn, _ty in ports)
                    args = ", ".join(["cyc", *(sv_packed_expr(sn, ty) for _raw, sn, ty in ports)])
                    lines.append(f"          $display(\"[tb] cyc=%0d {esc}{suffix}\", {args});\n")
                else:
                    lines.append(f"          $display(\"[tb] cyc=%0d {esc}\", cyc);\n")
            lines.append("        end\n")
        lines.append("        default: begin end\n")
        lines.append("      endcase\n")

    if prints_every:
        lines.append("      // Periodic prints.\n")
        for fmt, st, ev, ports in prints_every:
            esc = str(fmt).replace("\\", "\\\\").replace("\"", "\\\"")
            lines.append(f"      if (cyc >= {st} && (((cyc - {st}) % {ev}) == 0)) begin\n")
            if ports:
                suffix = "".join(f" {raw}=%0h" for raw, _sn, _ty in ports)
                args = ", ".join(["cyc", *(sv_packed_expr(sn, ty) for _raw, sn, ty in ports)])
                lines.append(f"        $display(\"[tb] cyc=%0d {esc}{suffix}\", {args});\n")
            else:
                lines.append(f"        $display(\"[tb] cyc=%0d {esc}\", cyc);\n")
            lines.append("      end\n")

    if t.finish_cycle is not None:
        lines.append(f"      if (cyc == {int(t.finish_cycle)}) begin\n")
        lines.append("        __pyc_tb_done = 1'b1;\n")
        lines.append("        $display(\"OK\");\n")
        lines.append("        $finish;\n")
        lines.append("        disable __pyc_tb_main;\n")
        lines.append("      end\n")

    lines.append("    end\n")
    if t.finish_cycle is None:
        lines.append("    if (!__pyc_tb_done) $fatal(1, \"TIMEOUT\");\n")
    lines.append("  end\n\n")

    # SVA assertions.
    if t.sva_asserts:
        if not has_clocks:
            raise SystemExit("sva_assert requires t.clock(...) in testbench")
        lines.append("  // SVA assertions.\n")
        for i, a in enumerate(t.sva_asserts):
            nm = a.name or f"sva_{i}"
            clk_dir, clk_port, _ = iface.resolve(a.clock)
            if clk_dir != "in":
                raise SystemExit(f"sva_assert clock must be an input port, got output: {a.clock!r}")
            pv = f"__pyc_sva_past_valid_{i}"
            # Guard against `$past` being undefined in the first sampled cycle by
            # generating a per-assertion past-valid bit.
            lines.append(f"  logic {pv};\n")
            lines.append(f"  initial {pv} = 1'b0;\n")
            disable_terms = ["!__pyc_tb_active"]
            if a.reset:
                rst_dir, rst_port, _ = iface.resolve(a.reset)
                if rst_dir != "in":
                    raise SystemExit(f"sva_assert reset must be an input port, got output: {a.reset!r}")
                disable_terms.insert(0, rst_port)
                lines.append(f"  always_ff @(posedge {clk_port}) begin\n")
                lines.append(f"    if ({rst_port}) {pv} <= 1'b0; else {pv} <= 1'b1;\n")
                lines.append("  end\n")
            else:
                lines.append(f"  always_ff @(posedge {clk_port}) begin\n")
                lines.append(f"    {pv} <= 1'b1;\n")
                lines.append("  end\n")
            rst_expr = f" disable iff ({' || '.join(disable_terms)})"
            msg = a.msg or f"SVA {nm} failed"
            expr = f"(!{pv}) || ({a.expr})"
            # Sample on negedge so assertions observe values after posedge-triggered
            # sequential updates in common designs.
            lines.append(
                f"  assert property (@(negedge {clk_port}){rst_expr} {expr}) else $fatal(1, \"{msg}\");\n"
            )
        lines.append("\n")

    lines.append("  /* verilator lint_on UNUSEDSIGNAL */\n")
    lines.append("endmodule\n")
    return "".join(lines)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.is_file():
        try:
            if path.read_bytes() == data:
                return
        except OSError:
            # Fall back to overwrite if we can't read for comparison.
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _run_backend_job(job: tuple[str, list[str]]) -> tuple[str, str]:
    name, cmd = job
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.strip()
        out = proc.stdout.strip()
        raise RuntimeError(f"backend job {name!r} failed ({proc.returncode})\ncmd: {' '.join(cmd)}\n{err}\n{out}")
    return (name, proc.stdout.strip())


def _emit_multi_pyc_artifacts(design: Design, *, out_dir: Path) -> tuple[Path, dict[str, Any], dict[str, Path], Path]:
    module_map = design.emit_module_mlir_map()
    module_dir = out_dir / "device" / "modules"
    module_dir.mkdir(parents=True, exist_ok=True)

    module_paths: dict[str, Path] = {}
    for sym in sorted(module_map.keys()):
        p = module_dir / f"{sym}.pyc"
        _write_text_atomic(p, module_map[sym])
        module_paths[sym] = p

    design_pyc_path = out_dir / "device" / "design.pyc"
    _write_text_atomic(design_pyc_path, design.emit_mlir())

    manifest = design.emit_project_manifest(module_dir_rel="device/modules")
    manifest["design_pyc"] = str(design_pyc_path.relative_to(out_dir))
    manifest_path = out_dir / "project_manifest.json"
    _write_text_atomic(manifest_path, json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return (manifest_path, manifest, module_paths, design_pyc_path)


def _collect_testbench_payload(
    mod: object,
    iface: _TopIface,
    *,
    trace_plan: TracePlan | None = None,
    tb_probes: TbProbes | None = None,
    tb_schedule_mode: str = "inline",
    tb_schedule_dir: Path | None = None,
) -> tuple[str, str]:
    if not hasattr(mod, "tb") or not callable(getattr(mod, "tb")):
        raise SystemExit("build requires `@testbench def tb(t: Tb): ...`")
    tb_fn = getattr(mod, "tb")
    if not bool(getattr(tb_fn, "__pycircuit_testbench__", False)):
        raise SystemExit("build requires tb(...) to be decorated with `@testbench`")
    t = Tb()
    try:
        tb_sig = inspect.signature(tb_fn)
        if len(tb_sig.parameters) >= 2:
            tb_fn(t, TbProbes([]) if tb_probes is None else tb_probes)
        else:
            tb_fn(t)
    except TbError as e:
        raise SystemExit(f"tb() failed: {e}") from e
    except ProbeError as e:
        raise SystemExit(f"tb() probe access failed: {e}") from e
    payload_obj = testbench_payload_from_tb(
        top_symbol=iface.sym,
        in_raw=list(iface.in_raw),
        in_tys=list(iface.in_tys),
        out_raw=list(iface.out_raw),
        out_tys=list(iface.out_tys),
        tb=t,
        probes=tb_probes,
    )
    tb_name = getattr(tb_fn, "__pycircuit_module_name__", None)
    if not isinstance(tb_name, str) or not tb_name.strip():
        tb_name = f"tb_{iface.sym}"
    tb_name = _sanitize_id(str(tb_name))
    payload = payload_obj.as_dict()
    payload["tb_name"] = str(tb_name)
    payload["tb_schedule_mode"] = str(tb_schedule_mode)
    tb_runtime_schedule_path: Path | None = None
    if str(tb_schedule_mode).strip().lower() == "sidecar":
        if tb_schedule_dir is None:
            raise SystemExit("sidecar TB requires a schedule output directory")
        tb_runtime_schedule_path = tb_schedule_dir / f"{tb_name}.schedule.bin"
        payload["tb_schedule"] = str(tb_runtime_schedule_path)
    if trace_plan is not None:
        payload["trace_plan"] = trace_plan.as_dict()
    payload["cpp_text"] = _render_tb_cpp(
        iface,
        t,
        trace_plan=trace_plan,
        schedule_mode=tb_schedule_mode,
        schedule_path=tb_runtime_schedule_path,
    )
    payload["sv_text"] = _render_tb_sv(iface, t, trace_plan=trace_plan)
    return (str(tb_name), json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _emit_testbench_pyc_file(
    *,
    out_dir: Path,
    tb_name: str,
    payload_json: str,
) -> Path:
    tb_dir = out_dir / "tb"
    tb_dir.mkdir(parents=True, exist_ok=True)
    tb_pyc_path = tb_dir / f"{tb_name}.pyc"
    payload = json.loads(payload_json)
    _write_text_atomic(
        tb_pyc_path,
        emit_testbench_pyc(payload=payload, tb_name=tb_name, frontend_contract=FRONTEND_CONTRACT),
    )
    return tb_pyc_path


def _gather_cpp_sources(cpp_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(cpp_root.rglob("*.cpp")):
        if p.is_file():
            out.append(p)
    return out


def _gather_cpp_headers(cpp_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(cpp_root.rglob("*.hpp")):
        if p.is_file():
            out.append(p)
    return out


def _module_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deps_hash(entry: Path, *, project_root: Path) -> str:
    root = project_root.resolve()
    files = collect_local_python_graph(entry.resolve(), project_root=root)
    h = hashlib.sha256()
    for p in files:
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).digest())
        h.update(b"\0")
    return h.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(data, sort_keys=True, indent=2) + "\n")


def _base_name_of(fn: Any) -> str:
    override = getattr(fn, "__pycircuit_module_name__", None)
    if isinstance(override, str) and override.strip():
        return override.strip()
    return getattr(fn, "__name__", "Module")


def _module_params_from_manifest(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    modules = manifest.get("modules", [])
    if not isinstance(modules, list):
        return out
    for raw in modules:
        if not isinstance(raw, Mapping):
            continue
        sym = str(raw.get("name", "")).strip()
        params_json = str(raw.get("params_json", "{}"))
        if not sym:
            continue
        try:
            params = json.loads(params_json)
        except Exception:
            params = {}
        if isinstance(params, Mapping):
            out[sym] = dict(params)
        else:
            out[sym] = {}
    return out


def _module_bases_from_manifest(manifest: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    modules = manifest.get("modules", [])
    if not isinstance(modules, list):
        return out
    for raw in modules:
        if not isinstance(raw, Mapping):
            continue
        sym = str(raw.get("name", "")).strip()
        base = str(raw.get("base", "")).strip()
        if not sym or not base:
            continue
        out.setdefault(base, []).append(sym)
    for key in list(out.keys()):
        out[key] = sorted(set(out[key]))
    return out


def _resolve_probe_outputs(
    *,
    mod: object,
    manifest: Mapping[str, Any],
    probe_catalog_path: Path,
    out_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    catalog = load_probe_catalog(probe_catalog_path)
    params_by_symbol = _module_params_from_manifest(manifest)
    bases = _module_bases_from_manifest(manifest)
    explicit_plans = []
    probe_entries: list[dict[str, Any]] = []
    probe_dir = out_dir / "device" / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)

    probe_modules: list[object] = []
    seen_module_ids: set[int] = set()

    def add_probe_module(candidate: object | None) -> None:
        if candidate is None:
            return
        mod_id = id(candidate)
        if mod_id in seen_module_ids:
            return
        seen_module_ids.add(mod_id)
        probe_modules.append(candidate)

    add_probe_module(mod)
    for value in vars(mod).values():
        owner = inspect.getmodule(value) if callable(value) else None
        if owner is not None:
            add_probe_module(owner)

    seen_probe_fns: set[int] = set()
    probe_fns: list[Any] = []
    for probe_mod in probe_modules:
        for probe_fn in collect_probe_functions(probe_mod):
            probe_id = id(probe_fn)
            if probe_id in seen_probe_fns:
                continue
            seen_probe_fns.add(probe_id)
            probe_fns.append(probe_fn)

    for probe_fn in probe_fns:
        target_fn = getattr(probe_fn, "__pycircuit_probe_target__", None)
        if target_fn is None:
            raise SystemExit(f"invalid @probe without target: {getattr(probe_fn, '__name__', probe_fn)!r}")
        target_base = _base_name_of(target_fn)
        target_symbols = bases.get(target_base, [])
        plan = resolve_probe_function(
            probe_fn,
            catalog=catalog,
            target_base=target_base,
            target_symbols=target_symbols,
            params_by_symbol=params_by_symbol,
        )
        explicit_plans.append(plan)
        rel = Path("device") / "probes" / f"{plan.name}.json"
        _save_json(out_dir / rel, plan.as_dict())
        probe_entries.append(
            {
                "name": plan.name,
                "target_base": target_base,
                "target_symbols": list(plan.target_symbols),
                "json": str(rel),
                "leaf_count": len(plan.leaves),
            }
        )

    probe_manifest = build_resolved_probe_manifest(
        top=str(manifest.get("top", "")),
        root_instance="dut",
        explicit_plans=explicit_plans,
        catalog=catalog,
    )
    probe_plan = {
        "version": 1,
        "top_symbol": str(manifest.get("top", "")),
        "aliases": [
            {"canonical_path": leaf.canonical_path, "source_path": leaf.source_path}
            for plan in explicit_plans
            for leaf in plan.leaves
        ],
    }
    probe_plan_path = out_dir / "probe_plan.json"
    _save_json(probe_plan_path, probe_plan)
    return (probe_manifest, {"version": 1, "probes": probe_entries}, probe_plan_path)


def _cmd_build(args: argparse.Namespace) -> int:
    src = Path(args.python_file).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_path = out_dir / ".build_cache.json"
    cache = _load_json(cache_path) if cache_path.is_file() else {"module_hashes": {}}

    project_root = _project_root(src, project_root_override=args.project_root)
    _scan_api_contract(src, project_root_override=str(project_root))
    mod = _load_py_file(src)
    if not hasattr(mod, "build") or not callable(getattr(mod, "build")):
        raise SystemExit(f"{src} must define a pyCircuit entrypoint: `@module def build(m: Circuit, ...)`")
    build = getattr(mod, "build")
    jit_params = _collect_jit_params(build, overrides=list(getattr(args, "param", []) or []))
    top_name = _top_name_for_build(src, build)

    from .design import canonical_params_json

    try:
        jit_params_json = canonical_params_json(jit_params, path="jit_params")
    except DesignError as e:
        raise SystemExit(f"JIT param canonicalization failed: {e}") from e
    jit_inputs = {
        "version": 1,
        "entry_hash": _module_hash(src),
        "deps_hash": _deps_hash(src, project_root=project_root),
        "jit_params_json": jit_params_json,
        "top_name": top_name,
        "frontend_contract": FRONTEND_CONTRACT,
    }
    jit_key = _canonical_hash(jit_inputs)

    manifest_path = out_dir / "project_manifest.json"
    design: Design | None = None
    manifest: dict[str, Any]
    module_paths: dict[str, Path]
    design_pyc_path: Path
    iface: _TopIface

    cached_key = str(cache.get("jit_cache_key", "")).strip()
    cache_hit = cached_key == jit_key and manifest_path.is_file()
    if cache_hit:
        try:
            manifest = _load_json(manifest_path)
            module_paths = _module_paths_from_manifest(manifest, out_dir=out_dir)
            if not all(p.is_file() for p in module_paths.values()):
                raise FileNotFoundError("missing cached .pyc modules")
            design_pyc_rel = str(manifest.get("design_pyc", "")).strip()
            design_pyc_path = (out_dir / design_pyc_rel) if design_pyc_rel else (out_dir / "device" / "design.pyc")
            if not design_pyc_path.is_file():
                raise FileNotFoundError("missing cached design.pyc")
            iface = _top_iface_from_manifest(manifest)
            print("jit-cache: hit")
        except Exception:
            cache_hit = False

    if not cache_hit:
        design = _compile_to_design(build, top_name=top_name, jit_params=jit_params)
        iface = _top_iface(design)
        manifest_path, manifest, module_paths, design_pyc_path = _emit_multi_pyc_artifacts(design, out_dir=out_dir)
        print("jit-cache: miss")

    pycc = _detect_pycc()
    jobs = max(1, int(args.jobs))
    if int(args.logic_depth) <= 0:
        raise SystemExit("--logic-depth must be > 0")
    logic_depth = int(args.logic_depth)

    device_cpp_root = out_dir / "device" / "cpp"
    device_v_root = out_dir / "device" / "verilog"
    device_cpp_root.mkdir(parents=True, exist_ok=True)
    device_v_root.mkdir(parents=True, exist_ok=True)

    target = str(args.target)
    do_cpp = target in {"cpp", "both"}
    do_v = target in {"verilator", "both"}
    pycc_build_profile = "dev-fast" if str(args.profile) == "dev" else "release"
    pycc_hard_hierarchy_flags = [
        f"--build-profile={pycc_build_profile}",
        "--inline-policy=off",
        "--hierarchy-policy=strict",
    ]
    if args.cpp_pch:
        pycc_hard_hierarchy_flags.append("--cpp-pch")

    build_flags = {
        "pycc": str(pycc.resolve()),
        "logic_depth": logic_depth,
        "profile": str(args.profile),
        "pycc_build_profile": pycc_build_profile,
        "inline_policy": "off",
        "hierarchy_policy": "strict",
        "cpp_pch": bool(args.cpp_pch),
        "target": target,
        "tb_schedule_mode": str(args.tb_schedule_mode),
        "frontend_contract": FRONTEND_CONTRACT,
    }
    build_flags_hash = _canonical_hash(build_flags)
    same_flags = str(cache.get("build_flags_hash", "")) == build_flags_hash

    design_key = "__design_pyc"
    old_hashes = dict(cache.get("module_hashes", {}))
    module_hashes: dict[str, str] = {}
    design_hash = _module_hash(design_pyc_path)
    module_hashes[design_key] = design_hash
    probe_catalog_path = out_dir / "device" / "probe_catalog.json"
    probe_catalog_ready = probe_catalog_path.is_file()
    probe_unchanged = same_flags and old_hashes.get(design_key) == design_hash
    pycc_jobs: list[tuple[str, list[str]]] = []
    if not (probe_unchanged and probe_catalog_ready):
        pycc_jobs.append(
            (
                "probe-catalog",
                [
                    str(pycc),
                    str(design_pyc_path),
                    "--emit=none",
                    *pycc_hard_hierarchy_flags,
                    "--probe-manifest",
                    str(probe_catalog_path),
                    f"--logic-depth={logic_depth}",
                ],
            )
        )
    if pycc_jobs:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(_run_backend_job, j): j[0] for j in pycc_jobs}
            for fut in as_completed(futs):
                _ = fut.result()
        pycc_jobs = []

    try:
        probe_manifest_obj, probe_section, probe_plan_path = _resolve_probe_outputs(
            mod=mod,
            manifest=manifest,
            probe_catalog_path=probe_catalog_path,
            out_dir=out_dir,
        )
    except ProbeError as e:
        raise SystemExit(f"probe resolution failed: {e}") from e
    probe_manifest_path = out_dir / "probe_manifest.json"
    _save_json(probe_manifest_path, probe_manifest_obj)
    manifest["probe_manifest"] = str(probe_manifest_path.relative_to(out_dir))
    manifest["probes"] = list(probe_section.get("probes", []))

    trace_plan: TracePlan | None = None
    trace_cfg_path = getattr(args, "trace_config", None)
    if trace_cfg_path is not None:
        raw = str(trace_cfg_path).strip()
        if raw:
            try:
                cfg = load_trace_config(Path(raw))
                trace_plan = compute_trace_plan_from_artifacts(
                    manifest=manifest,
                    module_paths=module_paths,
                    config=cfg,
                    probe_manifest=probe_manifest_obj,
                )
            except TraceConfigError as e:
                raise SystemExit(f"trace config error: {e}") from e

    tb_probes = TbProbes.from_probe_manifest(probe_manifest_obj)
    tb_name, tb_payload_json = _collect_testbench_payload(
        mod,
        iface,
        trace_plan=trace_plan,
        tb_probes=tb_probes,
        tb_schedule_mode=str(args.tb_schedule_mode),
        tb_schedule_dir=out_dir / "tb",
    )
    tb_pyc_path = _emit_testbench_pyc_file(out_dir=out_dir, tb_name=tb_name, payload_json=tb_payload_json)
    manifest["testbench"] = {"name": tb_name, "pyc": str(tb_pyc_path.relative_to(out_dir))}
    if trace_plan is not None:
        trace_path = out_dir / "trace_plan.json"
        _save_json(trace_path, trace_plan.as_dict())
        manifest["trace_plan"] = str(trace_path.relative_to(out_dir))

    tb_cpp_out = out_dir / "tb" / f"{tb_name}.cpp"
    tb_sv_out = out_dir / "tb" / f"{tb_name}.sv"
    for sym in sorted(module_paths.keys()):
        mp = module_paths[sym]
        h = _module_hash(mp)
        module_hashes[sym] = h
        unchanged = same_flags and old_hashes.get(sym) == h

        cpp_out_dir = device_cpp_root / sym
        cpp_ready = cpp_out_dir.is_dir() and any(cpp_out_dir.glob("*.cpp")) and any(cpp_out_dir.glob("*.hpp"))
        if do_cpp and not (unchanged and cpp_ready):
            cpp_out_dir.mkdir(parents=True, exist_ok=True)
            pycc_jobs.append(
                (
                    f"cpp:{sym}",
                    [
                        str(pycc),
                        str(mp),
                        "--emit=cpp",
                        *pycc_hard_hierarchy_flags,
                        "--out-dir",
                        str(cpp_out_dir),
                        "--cpp-split=module",
                        "--probe-plan",
                        str(probe_plan_path),
                        f"--logic-depth={logic_depth}",
                    ],
                )
            )

        verilog_out_dir = device_v_root / sym
        verilog_ready = verilog_out_dir.is_dir() and any(verilog_out_dir.glob("*.v"))
        if do_v and not (unchanged and verilog_ready):
            verilog_out_dir.mkdir(parents=True, exist_ok=True)
            pycc_jobs.append(
                (
                    f"verilog:{sym}",
                    [
                        str(pycc),
                        str(mp),
                        "--emit=verilog",
                        *pycc_hard_hierarchy_flags,
                        "--out-dir",
                        str(verilog_out_dir),
                        f"--logic-depth={logic_depth}",
                    ],
                )
            )

    if do_cpp:
        tb_key = f"tb:{tb_name}"
        tb_hash = _module_hash(tb_pyc_path)
        module_hashes[tb_key] = tb_hash
        tb_unchanged = same_flags and old_hashes.get(tb_key) == tb_hash
        if not (tb_unchanged and tb_cpp_out.is_file()):
            pycc_jobs.append(
                (
                    f"tb-cpp:{tb_name}",
                    [str(pycc), str(tb_pyc_path), *pycc_hard_hierarchy_flags, "-cpp", str(tb_cpp_out)],
                )
            )
    if do_v:
        tb_key = f"tb:{tb_name}"
        tb_hash = module_hashes.get(tb_key) or _module_hash(tb_pyc_path)
        module_hashes[tb_key] = tb_hash
        tb_unchanged = same_flags and old_hashes.get(tb_key) == tb_hash
        if not (tb_unchanged and tb_sv_out.is_file()):
            pycc_jobs.append(
                (
                    f"tb-sv:{tb_name}",
                    [str(pycc), str(tb_pyc_path), *pycc_hard_hierarchy_flags, "-verilog", str(tb_sv_out)],
                )
            )

    if pycc_jobs:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(_run_backend_job, j): j[0] for j in pycc_jobs}
            for fut in as_completed(futs):
                _ = fut.result()

    if do_cpp:
        cpp_sources = _gather_cpp_sources(device_cpp_root)
        if not cpp_sources:
            raise SystemExit("build(cpp): no generated C++ sources found")
        if not tb_cpp_out.is_file():
            raise SystemExit(f"build(cpp): missing generated TB C++ source: {tb_cpp_out}")
        cpp_headers = _gather_cpp_headers(device_cpp_root)
        include_dirs: list[str] = []
        include_dirs.append(str(device_cpp_root))
        runtime_source_include = Path(__file__).resolve().parents[3] / "runtime"
        if runtime_source_include.is_dir():
            include_dirs.append(str(runtime_source_include))
        for p in [*cpp_sources, *cpp_headers]:
            parent = str(p.parent)
            if parent not in include_dirs:
                include_dirs.append(parent)

        runtime = _runtime_manifest_for_toolchain(_detect_toolchain_root(pycc))

        build_manifest = {
            "version": 3,
            "target_name": iface.sym,
            "tb_cpp": str(tb_cpp_out),
            "sources": [str(p) for p in cpp_sources],
            "headers": [str(p) for p in cpp_headers],
            "include_dirs": include_dirs,
            "runtime": runtime,
            "cxx_standard": "c++17",
            "profile": str(args.profile),
        }
        if args.cpp_pch:
            # Collect device hpp headers flagged for PCH across all module manifests.
            seen: set[str] = set()
            pch_headers: list[str] = []
            for manifest_path in sorted(device_cpp_root.rglob("cpp_compile_manifest.json")):
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                profile = data.get("profile_summary") or {}
                if not profile.get("cpp_pch"):
                    continue
                for header in data.get("precompile_headers") or []:
                    if header not in seen:
                        seen.add(header)
                        pch_headers.append(header)
            pch_headers.sort()
            if not pch_headers:
                pch_script = _tool_script("cpp_pch_headers.py")
                pch_mod = _load_py_file(pch_script)
                pch_headers = pch_mod.select_device_hpp_headers(build_manifest["headers"])
            if pch_headers:
                build_manifest["precompile_headers"] = pch_headers
                build_manifest["precompile_headers_mode"] = "device_hpp"
        cpp_manifest = out_dir / "cpp_project_manifest.json"
        _save_json(cpp_manifest, build_manifest)

        gen_script = _tool_script("gen_cmake_from_manifest.py")
        cmake_src = out_dir / "cpp_build" / "src"
        cmake_build = out_dir / "cpp_build" / "build"
        cmake_src.mkdir(parents=True, exist_ok=True)
        cmake_build.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            [sys.executable, str(gen_script), "--manifest", str(cpp_manifest), "--out-dir", str(cmake_src)],
            check=True,
        )
        build_type = "Release" if str(args.profile) == "release" else "RelWithDebInfo"

        # Windows/MSYS2: `ninja.exe --version` can intermittently fail with
        # STATUS_DLL_INIT_FAILED in subprocesses. Prefer Makefiles here for
        # robustness.
        cmake_cmd = [
            "cmake",
            "-G",
            "Ninja",
            "-S",
            str(cmake_src),
            "-B",
            str(cmake_build),
            f"-DCMAKE_BUILD_TYPE={build_type}",
        ]
        if os.name == "nt":
            cmake_cmd = [
                "cmake",
                "-G",
                "MinGW Makefiles",
                "-S",
                str(cmake_src),
                "-B",
                str(cmake_build),
                f"-DCMAKE_BUILD_TYPE={build_type}",
                "-DCMAKE_MAKE_PROGRAM=mingw32-make",
            ]

        subprocess.run(cmake_cmd, check=True)
        subprocess.run(["cmake", "--build", str(cmake_build), "-j", str(jobs)], check=True)
        manifest["cpp_executable"] = str(cmake_build / "pyc_tb")

    if do_v:
        if not tb_sv_out.is_file():
            raise SystemExit(f"build(verilator): missing generated TB SV source: {tb_sv_out}")
        prim_file: Path | None = None
        verilog_module_sources: list[str] = []
        for p in sorted(device_v_root.rglob("*.v")):
            if not p.is_file():
                continue
            if p.name == "pyc_primitives.v":
                if prim_file is None:
                    prim_file = p
                continue
            verilog_module_sources.append(str(p))
        if not verilog_module_sources:
            raise SystemExit("build(verilator): no generated Verilog sources found")
        verilog_sources = ([str(prim_file)] if prim_file is not None else []) + verilog_module_sources
        verilog_manifest = {
            "version": 1,
            "top": tb_name,
            "tb_sv": str(tb_sv_out),
            "sources": verilog_sources,
            "include_dirs": [str(device_v_root)],
        }
        sim_manifest = out_dir / "verilator_manifest.json"
        _save_json(sim_manifest, verilog_manifest)
        manifest["verilator_manifest"] = str(sim_manifest.relative_to(out_dir))
        if bool(args.run_verilator):
            vbuild = out_dir / "verilator_build"

            # On Windows, MSYS2's `verilator` is typically a script (shebang) and
            # cannot be launched via CreateProcess directly. Prefer the real exe.
            verilator_exe = "verilator"
            if os.name == "nt":
                verilator_exe = (
                    shutil.which("verilator_bin.exe")
                    or shutil.which("verilator_bin")
                    or "verilator_bin.exe"
                )

            # Verilator needs a valid VERILATOR_ROOT on Windows; otherwise it may
            # form mixed /path\\include\\... strings and fail to locate std SV.
            run_env = None
            if os.name == "nt":
                run_env = os.environ.copy()
                vb = shutil.which(str(verilator_exe))
                if vb:
                    prefix = Path(vb).resolve().parents[1]
                    run_env["VERILATOR_ROOT"] = str(prefix / "share" / "verilator")

            cmd = [
                verilator_exe,
                "--binary",
                "-Wall",
                "-Wno-fatal",
                "-Wno-DECLFILENAME",
                "-Wno-UNUSEDSIGNAL",
                "-Wno-WIDTHEXPAND",
                "--quiet",
            ]
            cmd.extend(
                [
                    "--timing",
                    "--trace",
                    "--top-module",
                    tb_name,
                    "--Mdir",
                    str(vbuild),
                    str(tb_sv_out),
                    *verilog_sources,
                ]
            )
            subprocess.run(cmd, check=True, env=run_env)
            vbin = vbuild / f"V{tb_name}"
            if os.name == "nt" and not vbin.is_file():
                vbin_exe = vbin.with_suffix(".exe")
                if vbin_exe.is_file():
                    vbin = vbin_exe
            manifest["verilator_binary"] = str(vbin)
            if not vbin.is_file():
                raise SystemExit(f"build(verilator): expected binary not found: {vbin}")
            run_args = list(getattr(args, "run_arg", []) or [])
            subprocess.run([str(vbin), *run_args], cwd=str(out_dir), check=True)

    cache_out = dict(cache)
    cache_out.update(
        {
            "module_hashes": module_hashes,
            "pycc": str(pycc),
            "build_flags": build_flags,
            "build_flags_hash": build_flags_hash,
            "jit_cache_key": jit_key,
            "jit_cache_inputs": jit_inputs,
            "last_pycc_jobs": int(len(pycc_jobs)),
        }
    )
    _save_json(cache_path, cache_out)
    _save_json(manifest_path, manifest)
    print(str(manifest_path))
    return 0


def _cmd_sidecar_inspect(args: argparse.Namespace) -> int:
    report = inspect_sidecar_file(Path(args.file))
    sys.stdout.write(render_sidecar_inspect_text(report))
    return 1 if bool(report.get("errors")) and bool(getattr(args, "strict", False)) else 0


def _cmd_sidecar_verify(args: argparse.Namespace) -> int:
    report = inspect_sidecar_file(Path(args.file))
    if report.get("errors"):
        for item in report["errors"]:
            print(f"ERROR: {item}", file=sys.stderr)
    if report.get("warnings"):
        for item in report["warnings"]:
            print(f"WARNING: {item}", file=sys.stderr)
    if report.get("valid"):
        print("sidecar verify: ok")
        return 0
    print("sidecar verify: failed", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pycircuit")
    sub = p.add_subparsers(dest="cmd", required=True)

    emit = sub.add_parser("emit", help="Emit PYC MLIR (*.pyc) from a Python design file.")
    emit.add_argument("python_file", help="Python source defining `@module def build(m: Circuit, ...)`")
    emit.add_argument("-o", "--output", required=True, help="Output .pyc path")
    emit.add_argument(
        "--param",
        action="append",
        default=[],
        help="Override a JIT parameter (repeatable): name=value (parsed as a Python literal when possible)",
    )
    emit.add_argument(
        "--project-root",
        default=None,
        help="Optional project root for strict API contract scan (defaults to nearest .git/pyproject.toml).",
    )
    emit.add_argument(
        "--module-graph-out",
        dest="module_graph_out",
        default=None,
        help="Optional: emit a module-instance connectivity graph from the emitted .pyc (DOT/SVG via Graphviz).",
    )
    emit.add_argument(
        "--module-graph-module",
        dest="module_graph_module",
        default="",
        help="Target module symbol for the graph (default: module attribute pyc.top).",
    )
    emit.add_argument(
        "--module-graph-recursive",
        dest="module_graph_recursive",
        action="store_true",
        help="Recursively expand module instances (module nest) in the graph.",
    )
    emit.add_argument(
        "--module-graph-edge-label-mode",
        dest="module_graph_edge_label_mode",
        choices=["ports", "count", "none"],
        default="ports",
        help="Edge label mode for module graph.",
    )
    emit.add_argument(
        "--module-graph-edge-label-limit",
        dest="module_graph_edge_label_limit",
        type=int,
        default=4,
        help="Max port mappings per edge label (module graph).",
    )
    emit.add_argument(
        "--module-graph-max-nodes",
        dest="module_graph_max_nodes",
        type=int,
        default=500,
        help="Max instance nodes before aborting (module graph).",
    )
    emit.add_argument(
        "--module-graph-max-edges",
        dest="module_graph_max_edges",
        type=int,
        default=2000,
        help="Max instance edges before aborting (module graph).",
    )
    emit.set_defaults(fn=_cmd_emit)

    build = sub.add_parser("build", help="Canonical flow: multi-.pyc emit + parallel pycc + CMake/Verilator.")
    build.add_argument("python_file", help="Python source defining `@module build(...)` and `@testbench tb(...)`")
    build.add_argument("--out-dir", required=True, help="Output directory for project artifacts")
    build.add_argument(
        "--param",
        action="append",
        default=[],
        help="Override a JIT parameter (repeatable): name=value (parsed as a Python literal when possible)",
    )
    build.add_argument(
        "--project-root",
        default=None,
        help="Optional project root for strict API contract scan (defaults to nearest .git/pyproject.toml).",
    )
    build.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 1), help="Parallel backend jobs")
    build.add_argument("--profile", choices=["dev", "release"], default="release", help="C++ build profile")
    build.add_argument(
        "--target",
        choices=["cpp", "verilator", "both"],
        default="both",
        help="Backend targets to generate/build",
    )
    build.add_argument("--logic-depth", type=int, default=32, help="Max combinational logic depth for pycc")
    build.add_argument(
        "--cpp-pch",
        action="store_true",
        help="Precompile device module hpp headers in generated CMake build",
    )
    build.add_argument(
        "--trace-config",
        default=None,
        help="Optional trace configuration JSON (instance globs + probe tags + windows) for VCD generation.",
    )
    build.add_argument(
        "--tb-schedule-mode",
        choices=["inline", "sidecar"],
        default="inline",
        help="C++ testbench schedule rendering mode: inline preserves existing per-cycle emission; sidecar emits a stable runner plus external schedule sidecar data.",
    )
    build.add_argument(
        "--run-verilator",
        action="store_true",
        help="Also run generated Verilator binary after build",
    )
    build.add_argument(
        "--run-arg",
        action="append",
        default=[],
        help="Argument passed to the Verilator binary when --run-verilator is set (repeatable).",
    )
    build.set_defaults(fn=_cmd_build)

    sidecar = sub.add_parser("sidecar", help="Inspect and verify sidecar schedule files.")
    sidecar_sub = sidecar.add_subparsers(dest="sidecar_cmd", required=True)

    sidecar_inspect = sidecar_sub.add_parser("inspect", help="Print a human-readable sidecar section summary.")
    sidecar_inspect.add_argument("file", help="sidecar file path")
    sidecar_inspect.add_argument("--strict", action="store_true", help="Return non-zero if framework-level errors exist.")
    sidecar_inspect.set_defaults(fn=_cmd_sidecar_inspect)

    sidecar_verify = sidecar_sub.add_parser("verify", help="Verify sidecar container and section-directory consistency.")
    sidecar_verify.add_argument("file", help="sidecar file path")
    sidecar_verify.set_defaults(fn=_cmd_sidecar_verify)

    ns = p.parse_args(argv)
    return int(ns.fn(ns))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
