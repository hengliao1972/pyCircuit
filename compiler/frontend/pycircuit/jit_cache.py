from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
import weakref
from dataclasses import dataclass
from typing import Any
from pathlib import Path


_INSTANCE_METHODS = frozenset({"instance", "instance_auto", "instance_handle", "new", "array"})
_STATE_METHODS = frozenset(
    {
        "out",
        "reg_wire",
        "reg_domain",
        "backedge_reg",
        "byte_mem",
        "sync_mem_nm",
        "sync_mem_2w",
        "async_fifo",
        "cdc_sync",
        "fifo",
        "fifo_domain",
    }
)


@dataclass(frozen=True)
class FunctionMeta:
    fn: Any
    signature: inspect.Signature
    source: str
    start_line: int
    tree: ast.AST
    fdef: ast.FunctionDef
    source_file: str | None
    source_stem: str | None


@dataclass(frozen=True)
class RepeatedBodyCluster:
    fingerprint: str
    count: int
    node_count: int
    hardware_calls: int
    module_calls: int
    state_calls: int
    loop_extent_hint: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "fingerprint": self.fingerprint,
            "count": int(self.count),
            "node_count": int(self.node_count),
            "hardware_calls": int(self.hardware_calls),
            "module_calls": int(self.module_calls),
            "state_calls": int(self.state_calls),
            "loop_extent_hint": int(self.loop_extent_hint),
        }


@dataclass(frozen=True)
class StructuralMetrics:
    source_loc: int
    ast_node_count: int
    hardware_call_count: int
    loop_count: int
    module_call_count: int
    state_call_count: int
    estimated_inline_cost: int
    repeated_body_clusters: tuple[RepeatedBodyCluster, ...]

    def repeat_pressure(self) -> int:
        return sum(
            max(cluster.loop_extent_hint, 1)
            * max(cluster.hardware_calls + (cluster.state_calls * 4) + (cluster.module_calls * 6), 1)
            * max(cluster.count, 1)
            for cluster in self.repeated_body_clusters
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_loc": int(self.source_loc),
            "ast_node_count": int(self.ast_node_count),
            "hardware_call_count": int(self.hardware_call_count),
            "loop_count": int(self.loop_count),
            "module_call_count": int(self.module_call_count),
            "state_call_count": int(self.state_call_count),
            "estimated_inline_cost": int(self.estimated_inline_cost),
            "repeat_pressure": int(self.repeat_pressure()),
            "repeated_body_clusters": [cluster.to_dict() for cluster in self.repeated_body_clusters],
        }


_META_CACHE: weakref.WeakKeyDictionary[Any, FunctionMeta] = weakref.WeakKeyDictionary()
_SIG_CACHE: weakref.WeakKeyDictionary[Any, inspect.Signature] = weakref.WeakKeyDictionary()
_ASSIGNED_NAMES_CACHE: dict[tuple[int, ...], frozenset[str]] = {}
_STRUCT_METRICS_CACHE: weakref.WeakKeyDictionary[Any, StructuralMetrics] = weakref.WeakKeyDictionary()


def _nonempty_source_loc(source: str) -> int:
    return sum(1 for line in source.splitlines() if line.strip())


def _range_extent_hint(node: ast.AST) -> int:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "range":
        return 0
    args = node.args
    if not (1 <= len(args) <= 3):
        return 0
    values: list[int] = []
    for arg in args:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, int):
            return 0
        values.append(int(arg.value))
    if len(values) == 1:
        start, stop, step = 0, values[0], 1
    elif len(values) == 2:
        start, stop = values
        step = 1
    else:
        start, stop, step = values
    if step <= 0:
        return 0
    if stop <= start:
        return 0
    return max(0, (stop - start + (step - 1)) // step)


def _call_attr_name(node: ast.Call) -> tuple[str, str] | None:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    base = func.value
    if not isinstance(base, ast.Name):
        return None
    return (base.id, func.attr)


def _loop_cluster_for(node: ast.For | ast.While) -> RepeatedBodyCluster:
    body_mod = ast.Module(body=list(node.body), type_ignores=[])
    body_dump = ast.dump(body_mod, include_attributes=False)
    fingerprint = hashlib.sha256(body_dump.encode("utf-8")).hexdigest()[:16]

    hardware_calls = 0
    module_calls = 0
    state_calls = 0
    for sub in ast.walk(body_mod):
        if not isinstance(sub, ast.Call):
            continue
        attr = _call_attr_name(sub)
        if attr is None:
            continue
        base, method = attr
        if base != "m":
            continue
        hardware_calls += 1
        if method in _INSTANCE_METHODS:
            module_calls += 1
        if method in _STATE_METHODS:
            state_calls += 1

    return RepeatedBodyCluster(
        fingerprint=fingerprint,
        count=1,
        node_count=sum(1 for _ in ast.walk(body_mod)),
        hardware_calls=hardware_calls,
        module_calls=module_calls,
        state_calls=state_calls,
        loop_extent_hint=_range_extent_hint(node.iter) if isinstance(node, ast.For) else 0,
    )


def get_structural_metrics(fn: Any) -> StructuralMetrics:
    cached = _STRUCT_METRICS_CACHE.get(fn)
    if cached is not None:
        return cached

    meta = get_function_meta(fn)
    hardware_call_count = 0
    loop_count = 0
    module_call_count = 0
    state_call_count = 0
    cluster_accum: dict[str, RepeatedBodyCluster] = {}

    for node in ast.walk(meta.fdef):
        if isinstance(node, ast.Call):
            attr = _call_attr_name(node)
            if attr is None:
                continue
            base, method = attr
            if base != "m":
                continue
            hardware_call_count += 1
            if method in _INSTANCE_METHODS:
                module_call_count += 1
            if method in _STATE_METHODS:
                state_call_count += 1
            continue

        if isinstance(node, (ast.For, ast.While)):
            loop_count += 1
            cluster = _loop_cluster_for(node)
            prev = cluster_accum.get(cluster.fingerprint)
            if prev is None:
                cluster_accum[cluster.fingerprint] = cluster
            else:
                cluster_accum[cluster.fingerprint] = RepeatedBodyCluster(
                    fingerprint=cluster.fingerprint,
                    count=prev.count + 1,
                    node_count=max(prev.node_count, cluster.node_count),
                    hardware_calls=max(prev.hardware_calls, cluster.hardware_calls),
                    module_calls=max(prev.module_calls, cluster.module_calls),
                    state_calls=max(prev.state_calls, cluster.state_calls),
                    loop_extent_hint=max(prev.loop_extent_hint, cluster.loop_extent_hint),
                )

    repeated_body_clusters = tuple(
        sorted(cluster_accum.values(), key=lambda c: (-c.count, -c.node_count, c.fingerprint))
    )
    ast_node_count = sum(1 for _ in ast.walk(meta.fdef))
    repeated_pressure = sum(
        max(cluster.loop_extent_hint, 1)
        * max(cluster.hardware_calls + (cluster.state_calls * 4) + (cluster.module_calls * 6), 1)
        * max(cluster.count, 1)
        for cluster in repeated_body_clusters
    )
    estimated_inline_cost = (
        _nonempty_source_loc(meta.source)
        + (ast_node_count // 8)
        + (hardware_call_count * 4)
        + (loop_count * 12)
        + (module_call_count * 64)
        + (state_call_count * 40)
        + repeated_pressure
    )
    metrics = StructuralMetrics(
        source_loc=_nonempty_source_loc(meta.source),
        ast_node_count=ast_node_count,
        hardware_call_count=hardware_call_count,
        loop_count=loop_count,
        module_call_count=module_call_count,
        state_call_count=state_call_count,
        estimated_inline_cost=estimated_inline_cost,
        repeated_body_clusters=repeated_body_clusters,
    )
    _STRUCT_METRICS_CACHE[fn] = metrics
    return metrics


def _find_function_def(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise RuntimeError(f"failed to find function definition for {name!r}")


def get_signature(fn: Any) -> inspect.Signature:
    cached = _SIG_CACHE.get(fn)
    if cached is not None:
        return cached
    sig = inspect.signature(fn)
    _SIG_CACHE[fn] = sig
    return sig


def get_function_meta(fn: Any, *, fn_name: str | None = None) -> FunctionMeta:
    cached = _META_CACHE.get(fn)
    if cached is not None and (fn_name is None or cached.fdef.name == fn_name):
        return cached

    synthetic = getattr(fn, "__pycircuit_jit_source__", None)
    if isinstance(synthetic, str) and synthetic.strip():
        source = textwrap.dedent(synthetic).strip() + "\n"
        start_line = int(getattr(fn, "__pycircuit_jit_start_line__", 1) or 1)
        tree = ast.parse(source)
    else:
        lines, start_line = inspect.getsourcelines(fn)
        source = textwrap.dedent("".join(lines))
        tree = ast.parse(source)
    name = fn_name if fn_name is not None else getattr(fn, "__name__", None)
    if not isinstance(name, str) or not name:
        raise RuntimeError(f"failed to infer function name for {fn!r}")
    fdef = _find_function_def(tree, name)

    if isinstance(synthetic, str) and synthetic.strip():
        source_file = getattr(fn, "__pycircuit_jit_source_file__", None) or "<pycircuit_v5>"
    else:
        source_file = inspect.getsourcefile(fn) or inspect.getfile(fn)
    source_stem = None
    try:
        if source_file:
            source_stem = Path(source_file).stem
    except Exception:
        source_stem = None

    meta = FunctionMeta(
        fn=fn,
        signature=get_signature(fn),
        source=source,
        start_line=int(start_line),
        tree=tree,
        fdef=fdef,
        source_file=source_file,
        source_stem=source_stem,
    )
    _META_CACHE[fn] = meta
    return meta


def assigned_names_for(stmts: list[ast.stmt]) -> frozenset[str]:
    key = tuple(id(s) for s in stmts)
    cached = _ASSIGNED_NAMES_CACHE.get(key)
    if cached is not None:
        return cached

    out: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
            self.generic_visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            if isinstance(node.target, ast.Name):
                out.add(node.target.id)
            if node.value is not None:
                self.generic_visit(node.value)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
            if isinstance(node.target, ast.Name):
                out.add(node.target.id)
            self.generic_visit(node.value)

    _Visitor().visit(ast.Module(body=stmts, type_ignores=[]))
    frozen = frozenset(out)
    _ASSIGNED_NAMES_CACHE[key] = frozen
    return frozen


def clear_metadata_caches() -> None:
    _META_CACHE.clear()
    _SIG_CACHE.clear()
    _ASSIGNED_NAMES_CACHE.clear()
    _STRUCT_METRICS_CACHE.clear()
