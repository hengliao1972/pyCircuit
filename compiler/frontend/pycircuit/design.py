from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Callable, Iterable, Mapping, ParamSpec, TYPE_CHECKING, TypeVar, overload

from .api_contract import FRONTEND_CONTRACT
from .data import Data
from .dsl import Module
from .jit_cache import get_structural_metrics
from .vector_lower import port_mlir_type

if TYPE_CHECKING:
    from .hw import Circuit


class DesignError(RuntimeError):
    pass


P = ParamSpec("P")
R = TypeVar("R")


_CANON_PATH_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _canon_path_key(path: str, key: str) -> str:
    if not path:
        return str(key)
    if _CANON_PATH_IDENT_RE.match(str(key)):
        return f"{path}.{key}"
    return f"{path}[{key!r}]"


def _canon_path_index(path: str, idx: int) -> str:
    if not path:
        return f"[{int(idx)}]"
    return f"{path}[{int(idx)}]"


def _normalize_value_param_ty(ty: str) -> str:
    raw = str(ty).strip()
    if raw == "clock":
        return "!pyc.clock"
    if raw == "reset":
        return "!pyc.reset"
    if raw in {"!pyc.clock", "!pyc.reset"}:
        return raw
    if raw.startswith("i"):
        try:
            w = int(raw[1:])
        except ValueError as e:
            raise DesignError(f"invalid value-param type {ty!r}: expected iN/clock/reset") from e
        if w <= 0:
            raise DesignError(f"invalid value-param type {ty!r}: iN width must be > 0")
        return f"i{w}"
    raise DesignError(f"invalid value-param type {ty!r}: expected iN/clock/reset")


def _normalize_value_params_decl(value_params: Mapping[str, str] | None) -> dict[str, str]:
    if not value_params:
        return {}
    out: dict[str, str] = {}
    for raw_k in sorted(value_params.keys(), key=lambda x: str(x)):
        k = str(raw_k).strip()
        if not k:
            raise DesignError("value_params keys must be non-empty strings")
        out[k] = _normalize_value_param_ty(value_params[raw_k])
    return out


def value_params_of(fn: Any) -> dict[str, str]:
    raw = getattr(fn, "__pycircuit_value_params__", None)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise DesignError(
            f"invalid __pycircuit_value_params__ on {getattr(fn, '__name__', fn)!r}: expected mapping"
        )
    return _normalize_value_params_decl(raw)


def _ordered_value_params(fn: Any) -> tuple[tuple[str, str], ...]:
    vp = value_params_of(fn)
    if not vp:
        return ()

    try:
        sig = inspect.signature(fn)
        ps = list(sig.parameters.values())
    except (TypeError, ValueError):
        return tuple((k, vp[k]) for k in sorted(vp.keys()))

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in ps[1:]:
        if p.name in vp:
            ordered.append((p.name, vp[p.name]))
            seen.add(p.name)
    for name in sorted(set(vp.keys()) - seen):
        ordered.append((name, vp[name]))
    return tuple(ordered)


def module(
    _fn: Any | None = None,
    *,
    name: str | None = None,
    structural: bool = False,
    value_params: Mapping[str, str] | None = None,
) -> Callable[[Any], Any] | Any:
    """Mark a function as a hierarchy-preserving module boundary.

    Module callsites are materialized as `pyc.instance` and are not inlined by
    the frontend. `structural=True` tags the symbol for structural emission.
    """

    def deco(fn: Any) -> Any:
        module_name = str(name).strip() if isinstance(name, str) and name.strip() else getattr(fn, "__name__", "Module")
        vp = _normalize_value_params_decl(value_params)
        setattr(fn, "__pycircuit_module_name__", str(module_name))
        setattr(fn, "__pycircuit_kind__", "module")
        setattr(fn, "__pycircuit_inline__", False)
        setattr(fn, "__pycircuit_emit_structural__", bool(structural))
        setattr(fn, "__pycircuit_value_params__", dict(vp))
        return fn

    if _fn is None:
        return deco
    return deco(_fn)


@overload
def function(_fn: Callable[P, R], *, name: str | None = None) -> Callable[P, R]: ...


@overload
def function(
    _fn: None = None, *, name: str | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def function(
    _fn: Callable[P, R] | None = None, *, name: str | None = None
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Mark a function as an inline hardware helper.

    Function callsites are lowered inline into the caller.
    """

    def deco(fn: Callable[P, R]) -> Callable[P, R]:
        if isinstance(name, str) and name.strip():
            setattr(fn, "__pycircuit_module_name__", str(name).strip())
        setattr(fn, "__pycircuit_kind__", "function")
        setattr(fn, "__pycircuit_inline__", True)
        setattr(fn, "__pycircuit_emit_structural__", False)
        setattr(fn, "__pycircuit_value_params__", {})
        return fn

    if _fn is None:
        return deco
    return deco(_fn)


@overload
def const(_fn: Callable[P, R], *, name: str | None = None) -> Callable[P, R]: ...


@overload
def const(
    _fn: None = None, *, name: str | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def const(
    _fn: Callable[P, R] | None = None, *, name: str | None = None
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Mark a function as compile-time metaprogramming logic.

    `@const` calls execute in Python during JIT and must be pure: they may not
    emit IR or mutate module interfaces.
    """

    def deco(fn: Callable[P, R]) -> Callable[P, R]:
        if isinstance(name, str) and name.strip():
            setattr(fn, "__pycircuit_module_name__", str(name).strip())
        setattr(fn, "__pycircuit_kind__", "const")
        setattr(fn, "__pycircuit_inline__", True)
        setattr(fn, "__pycircuit_emit_structural__", False)
        setattr(fn, "__pycircuit_value_params__", {})
        return fn

    if _fn is None:
        return deco
    return deco(_fn)


def testbench(_fn: Any | None = None, *, name: str | None = None) -> Callable[[Any], Any] | Any:
    """Mark a Python function as a host-side testbench entrypoint.

    Testbench functions are not lowered as hardware modules; they are consumed by
    `pycircuit.cli build` to emit TB `.pyc` payloads for backend lowering.
    """

    def deco(fn: Any) -> Any:
        if isinstance(name, str) and name.strip():
            setattr(fn, "__pycircuit_module_name__", str(name).strip())
        setattr(fn, "__pycircuit_testbench__", True)
        setattr(fn, "__pycircuit_kind__", "testbench")
        setattr(fn, "__pycircuit_value_params__", {})
        return fn

    if _fn is None:
        return deco
    return deco(_fn)


def probe(
    _fn: Any | None = None,
    *,
    target: Any,
    name: str | None = None,
) -> Callable[[Any], Any] | Any:
    """Mark a Python function as a frontend-only probe definition.

    `@probe` is not lowered as hardware. It is resolved after the target
    `@module` hierarchy has been compiled into a final probe catalog.
    """

    if getattr(target, "__pycircuit_kind__", None) != "module":
        raise DesignError("@probe target must be a function decorated with @module")

    def deco(fn: Any) -> Any:
        probe_name = str(name).strip() if isinstance(name, str) and name.strip() else getattr(fn, "__name__", "probe")
        setattr(fn, "__pycircuit_probe_name__", str(probe_name))
        setattr(fn, "__pycircuit_probe_target__", target)
        setattr(fn, "__pycircuit_kind__", "probe")
        setattr(fn, "__pycircuit_inline__", False)
        setattr(fn, "__pycircuit_emit_structural__", False)
        setattr(fn, "__pycircuit_value_params__", {})
        return fn

    if _fn is None:
        return deco
    return deco(_fn)


def _canon_param(v: Any, *, path: str) -> Any:
    # Deterministic, JSON-compatible subset (Decision 0139/0144).
    if v is None:
        return None
    if isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, (tuple, list)):
        return [_canon_param(x, path=_canon_path_index(path, i)) for i, x in enumerate(v)]
    if isinstance(v, (set, frozenset)):
        # Sets are compile-time specialization data (e.g. cut_after in pipeline
        # cut-point search); canonicalize to a deterministically sorted list so
        # each distinct value yields a distinct, stable cache key.
        items = [_canon_param(x, path=_canon_path_index(path, i)) for i, x in enumerate(sorted(v, key=repr))]
        return {"kind": "set", "items": items}
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k in sorted(v.keys(), key=lambda x: str(x)):
            if not isinstance(k, str):
                raise DesignError(
                    f"{path}: dict keys must be str for caching/canonicalization, got {type(k).__name__} "
                    "(hint: use string keys or map to a list/tuple of items)"
                )
            out[str(k)] = _canon_param(v[k], path=_canon_path_key(path, str(k)))
        return out
    fn = getattr(v, "__pyc_template_value__", None)
    if callable(fn):
        try:
            rep = fn()
        except Exception as e:  # noqa: BLE001
            raise DesignError(f"{path}.__pyc_template_value__(): template value hook failed for {type(v).__name__}: {e}") from e
        return _canon_param(rep, path=f"{path}.__pyc_template_value__()")
    if is_dataclass(v):
        params = getattr(v, "__dataclass_params__", None)
        if params is not None and hasattr(params, "frozen") and not bool(params.frozen):
            raise DesignError(
                f"{path}: dataclass params must be frozen for caching/canonicalization: {type(v).__name__} "
                "(hint: use @dataclass(frozen=True) or implement __pyc_template_value__())"
            )
        out_fields: dict[str, Any] = {}
        for f in fields(v):
            fname = str(f.name)
            out_fields[fname] = _canon_param(getattr(v, f.name), path=_canon_path_key(path, fname))
        return {
            "kind": "dataclass",
            "type": f"{type(v).__module__}.{type(v).__qualname__}",
            "fields": out_fields,
        }
    raise DesignError(
        f"{path}: unsupported param type for specialization/caching: {type(v).__name__} "
        "(allowed: bool/int/str/None, list/tuple, set/frozenset, dict[str,...], frozen dataclass, __pyc_template_value__())"
    )


def canonical_params_json(params: Mapping[str, Any], *, path: str = "params") -> str:
    canon = _canon_param(dict(params), path=str(path))
    return json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _params_json(params: Mapping[str, Any]) -> str:
    return canonical_params_json(params, path="params")


def _port_specs_json(port_specs: Mapping[str, Any] | None) -> str:
    if not port_specs:
        return "{}"
    return canonical_params_json(port_specs, path="port_specs")


def _params_hash8(params_json: str) -> str:
    h = hashlib.sha256(params_json.encode("utf-8")).hexdigest()
    return h[:8]


def _base_name(fn: Any) -> str:
    override = getattr(fn, "__pycircuit_module_name__", None)
    if isinstance(override, str) and override.strip():
        return override.strip()
    return getattr(fn, "__name__", "Module")


def _kind_of(fn: Any) -> str:
    k = getattr(fn, "__pycircuit_kind__", None)
    if isinstance(k, str):
        kk = k.strip().lower()
        if kk in {"module", "function", "const"}:
            if kk == "const":
                return "template"
            return kk
    return "module"


def _inline_of(fn: Any) -> bool:
    if _kind_of(fn) == "function":
        return True
    return bool(getattr(fn, "__pycircuit_inline__", False))


def _emit_structural_of(fn: Any) -> bool:
    return bool(getattr(fn, "__pycircuit_emit_structural__", False))


@dataclass(frozen=True)
class CompiledModule:
    fn: Any
    params_json: str
    sym_name: str
    mod: Module
    arg_names: tuple[str, ...]
    arg_types: tuple[Data, ...]
    result_names: tuple[str, ...]
    result_types: tuple[Data, ...]
    value_param_names: tuple[str, ...]
    value_param_types: tuple[str, ...]
    struct_metrics_json: str
    struct_collections_json: str


class Design:
    """A multi-module compilation unit (MLIR `module`) produced by the Python frontend."""

    def __init__(self, *, top: str) -> None:
        self.top = str(top)
        self._mods: dict[str, CompiledModule] = {}

    def add(self, cm: CompiledModule) -> None:
        if cm.sym_name in self._mods:
            raise DesignError(f"duplicate module symbol: {cm.sym_name!r}")
        self._mods[cm.sym_name] = cm

    def modules(self) -> Iterable[CompiledModule]:
        return self._mods.values()

    def lookup(self, sym_name: str) -> CompiledModule | None:
        return self._mods.get(str(sym_name))

    def emit_mlir(self) -> str:
        # Emit a single MLIR `module` containing all compiled `func.func`s.
        #
        # `pyc.top` is a FlatSymbolRefAttr for tools to find the top module.
        parts: list[str] = []
        parts.append(
            f'module attributes {{pyc.top = @{self.top}, pyc.frontend.contract = "{FRONTEND_CONTRACT}"}} {{\n'
        )
        for cm in self._mods.values():
            parts.append(cm.mod.emit_func_mlir())
            parts.append("\n")
        parts.append("}\n")
        return "".join(parts)

    def emit_module_mlir_map(self) -> dict[str, str]:
        """Emit deterministic single-module `.pyc` MLIR text per compiled symbol."""
        out: dict[str, str] = {}
        for sym in sorted(self._mods.keys()):
            cm = self._mods[sym]
            body = cm.mod.emit_func_mlir()
            deps = [d for d in self._deps_for_module_mlir(body) if d != sym and d in self._mods]
            dep_decls: list[str] = []
            for dep_sym in deps:
                dep_decls.append(self._emit_dep_decl_mlir(self._mods[dep_sym]))
            out[sym] = (
                f'module attributes {{pyc.top = @{sym}, pyc.frontend.contract = "{FRONTEND_CONTRACT}"}} {{\n'
                + "".join(dep_decls)
                + body
                + "\n}\n"
            )
        return out

    @staticmethod
    def _deps_for_module_mlir(mlir_text: str) -> list[str]:
        deps = sorted(set(re.findall(r"@([A-Za-z_][A-Za-z0-9_\\$]*)", mlir_text)))
        # Remove self references created from function headers by caller filtering.
        return deps

    @staticmethod
    def _emit_dep_decl_mlir(cm: CompiledModule) -> str:
        args_sig = ", ".join(str(port_mlir_type(t)) for t in cm.arg_types)
        sig = f"({args_sig})"
        if cm.result_types:
            sig += f" -> ({', '.join(str(port_mlir_type(t)) for t in cm.result_types)})"
        kind = _kind_of(cm.fn)
        inline = "true" if _inline_of(cm.fn) else "false"
        base = _base_name(cm.fn)
        params_esc = json.dumps(cm.params_json, ensure_ascii=False)
        base_esc = json.dumps(base, ensure_ascii=False)
        arg_names_esc = json.dumps(list(cm.arg_names), ensure_ascii=False)
        result_names_esc = json.dumps(list(cm.result_names), ensure_ascii=False)
        value_param_names_esc = json.dumps(list(cm.value_param_names), ensure_ascii=False)
        value_param_types_esc = json.dumps(list(cm.value_param_types), ensure_ascii=False)
        struct_metrics_esc = json.dumps(cm.struct_metrics_json, ensure_ascii=False)
        struct_collections_esc = json.dumps(cm.struct_collections_json, ensure_ascii=False)
        attrs = (
            f'attributes {{arg_names = {arg_names_esc}, result_names = {result_names_esc}, '
            f"pyc.value_params = {value_param_names_esc}, pyc.value_param_types = {value_param_types_esc}, "
            f'pyc.kind = "{kind}", pyc.inline = "{inline}", pyc.params = {params_esc}, '
            f"pyc.base = {base_esc}, pyc.struct.metrics = {struct_metrics_esc}, "
            f"pyc.struct.collections = {struct_collections_esc}"
        )
        if _emit_structural_of(cm.fn):
            attrs += ', pyc.emit.structural = "true"'
        attrs += "}"
        return f"  func.func private @{cm.sym_name}{sig} {attrs}\n"

    def emit_project_manifest(self, *, module_dir_rel: str = "device/modules") -> dict[str, Any]:
        """Create deterministic project manifest for multi-`.pyc` flow."""
        modules_out: list[dict[str, Any]] = []
        for sym in sorted(self._mods.keys()):
            cm = self._mods[sym]
            func_mlir = cm.mod.emit_func_mlir()
            deps = [d for d in self._deps_for_module_mlir(func_mlir) if d != sym and d in self._mods]
            params_hash = hashlib.sha256(cm.params_json.encode("utf-8")).hexdigest()[:16]
            modules_out.append(
                {
                    "name": sym,
                    "pyc": f"{module_dir_rel}/{sym}.pyc",
                    "params_hash": params_hash,
                    "params_json": cm.params_json,
                    "base": _base_name(cm.fn),
                    "deps": deps,
                    "arg_names": list(cm.arg_names),
                    "arg_types": [str(t) for t in cm.arg_types],
                    "result_names": list(cm.result_names),
                    "result_types": [str(t) for t in cm.result_types],
                    "value_param_names": list(cm.value_param_names),
                    "value_param_types": list(cm.value_param_types),
                }
            )

        return {
            "version": 1,
            "frontend_contract": FRONTEND_CONTRACT,
            "top": self.top,
            "modules": modules_out,
        }


class DesignContext:
    """Specialization cache + registry for a Design's compiled modules."""

    def __init__(self, design: Design) -> None:
        self.design = design
        self._cache: dict[tuple[int, str, str, str, str | None], CompiledModule] = {}
        self._used_sym_names: set[str] = set()

    def _unique_sym(self, base: str, *, cache_sig_json: str, module_name: str | None) -> str:
        if module_name is not None:
            sym = str(module_name)
        else:
            sym = f"{base}__p{_params_hash8(cache_sig_json)}"
        if sym in self._used_sym_names:
            # Same fn+params should map to the same symbol; collisions here mean
            # a user-provided module_name conflict.
            in_design = sym in self.design._mods
            raise DesignError(f"duplicate specialized module name: {sym!r} (already_in_design={in_design})")
        self._used_sym_names.add(sym)
        return sym

    def _bind_params(
        self,
        fn: Any,
        params: Mapping[str, Any],
        *,
        port_names: set[str] | None = None,
        value_param_names: set[str] | None = None,
    ) -> dict[str, Any]:
        sig = inspect.signature(fn)
        ps = list(sig.parameters.values())
        if not ps:
            raise DesignError("module function must accept at least one argument (Circuit builder)")
        ports = set(port_names or ()) | set(value_param_names or ())
        # The first argument is the builder; bind remaining by name.
        bound: dict[str, Any] = {}
        for p in ps[1:]:
            if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                raise DesignError("varargs are not supported for module specialization")
            if p.name in ports:
                continue
            if p.name not in params:
                if p.default is inspect._empty:
                    raise DesignError(f"missing module param {p.name!r} for {getattr(fn, '__name__', fn)!r}")
                bound[p.name] = p.default
            else:
                bound[p.name] = params[p.name]
        # Reject unknown keys early to avoid silent mismatches.
        extra = set(params.keys()) - {p.name for p in ps[1:] if p.name not in ports}
        if extra:
            raise DesignError(f"unknown module param(s) for {getattr(fn, '__name__', fn)!r}: {', '.join(sorted(extra))}")
        return bound

    def register_top(self, fn: Any, *, sym_name: str, params: Mapping[str, Any], mod: Module) -> CompiledModule:
        params_json = _params_json(params)
        base = _base_name(fn)
        # Top symbol is explicit (no hash suffix); still mark as used.
        if sym_name in self._used_sym_names:
            raise DesignError(f"duplicate top module symbol: {sym_name!r}")
        self._used_sym_names.add(sym_name)

        cm = self._finalize_compiled(fn, sym_name=sym_name, params_json=params_json, base=base, mod=mod)
        self.design.add(cm)
        return cm

    def specialize(
        self,
        fn: Any,
        *,
        params: Mapping[str, Any],
        module_name: str | None = None,
        port_specs: Mapping[str, Any] | None = None,
    ) -> CompiledModule:
        port_specs_dict = dict(port_specs or {})
        value_params_map = value_params_of(fn)
        value_param_names = set(value_params_map.keys())
        overlap_params = sorted(value_param_names & set(params.keys()))
        if overlap_params:
            raise DesignError(
                f"value-param(s) must be connected as instance ports, not specialization params: {', '.join(overlap_params)}"
            )
        overlap_ports = sorted(value_param_names & set(port_specs_dict.keys()))
        if overlap_ports:
            raise DesignError(
                f"value-param(s) must not appear in signature-bound port_specs: {', '.join(overlap_ports)}"
            )

        params_bound = self._bind_params(
            fn,
            params,
            port_names=set(port_specs_dict.keys()),
            value_param_names=value_param_names,
        )
        params_json = _params_json(params_bound)
        port_specs_json = _port_specs_json(port_specs_dict)
        value_params_json = json.dumps(value_params_map, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        key = (id(fn), params_json, port_specs_json, value_params_json, module_name)
        if key in self._cache:
            return self._cache[key]

        base = _base_name(fn)
        cache_sig_json = json.dumps(
            {
                "params": json.loads(params_json),
                "ports": json.loads(port_specs_json),
                "value_params": json.loads(value_params_json),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        sym_guess = str(module_name) if module_name is not None else f"{base}__p{_params_hash8(cache_sig_json)}"
        if sym_guess in self._used_sym_names:
            existing = self.design.lookup(sym_guess)
            if existing is not None:
                self._cache[key] = existing
                return existing
        sym_name = self._unique_sym(base, cache_sig_json=cache_sig_json, module_name=module_name)

        mod = self._compile_module(
            fn,
            sym_name=sym_name,
            params=params_bound,
            port_specs=port_specs_dict,
            value_params=value_params_map,
        )
        cm = self._finalize_compiled(fn, sym_name=sym_name, params_json=params_json, base=base, mod=mod)
        self.design.add(cm)
        self._cache[key] = cm
        return cm

    def _compile_module(
        self,
        fn: Any,
        *,
        sym_name: str,
        params: Mapping[str, Any],
        port_specs: Mapping[str, Any] | None = None,
        value_params: Mapping[str, str] | None = None,
    ) -> Module:
        from .jit import compile_module as jit_compile

        return jit_compile(
            fn,
            module_name=sym_name,
            design_ctx=self,
            port_specs=port_specs,
            value_params=value_params,
            **params,
        )

    def _finalize_compiled(self, fn: Any, *, sym_name: str, params_json: str, base: str, mod: Module) -> CompiledModule:
        value_param_pairs = _ordered_value_params(fn)
        value_param_names = tuple(name for name, _ in value_param_pairs)
        value_param_types = tuple(ty for _, ty in value_param_pairs)
        static_metrics = get_structural_metrics(fn).to_dict()
        runtime_metrics_fn = getattr(mod, "structural_runtime_metadata", None)
        runtime_metrics = runtime_metrics_fn() if callable(runtime_metrics_fn) else {}
        struct_metrics = dict(static_metrics)
        for key in (
            "instance_count",
            "state_alloc_count",
            "collection_count",
            "collection_instance_count",
            "module_family_collection_count",
        ):
            struct_metrics[key] = int(runtime_metrics.get(key, 0))
        struct_metrics_json = json.dumps(struct_metrics, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        struct_collections_json = json.dumps(
            runtime_metrics.get("collections", []),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        # Attach debug attributes (emitted in func.func header).
        try:
            mod.set_func_attr("pyc.base", base)
            mod.set_func_attr("pyc.params", params_json)
            mod.set_func_attr("pyc.kind", _kind_of(fn))
            mod.set_func_attr("pyc.inline", "true" if _inline_of(fn) else "false")
            mod.set_func_attr_json("pyc.value_params", list(value_param_names))
            mod.set_func_attr_json("pyc.value_param_types", list(value_param_types))
            mod.set_func_attr("pyc.struct.metrics", struct_metrics_json)
            mod.set_func_attr("pyc.struct.collections", struct_collections_json)
            if _emit_structural_of(fn):
                mod.set_func_attr("pyc.emit.structural", "true")
        except Exception as e:
            raise DesignError(f"failed to set module attrs for {sym_name!r}: {e}") from e

        arg_names = tuple(n for n, _ in getattr(mod, "_args", []))  # noqa: SLF001
        arg_types = tuple(sig.ty for _, sig in getattr(mod, "_args", []))  # noqa: SLF001
        res_names = tuple(n for n, _ in getattr(mod, "_results", []))  # noqa: SLF001
        res_types = tuple(sig.ty for _, sig in getattr(mod, "_results", []))  # noqa: SLF001

        return CompiledModule(
            fn=fn,
            params_json=params_json,
            sym_name=str(sym_name),
            mod=mod,
            arg_names=arg_names,
            arg_types=arg_types,
            result_names=res_names,
            result_types=res_types,
            value_param_names=value_param_names,
            value_param_types=value_param_types,
            struct_metrics_json=struct_metrics_json,
            struct_collections_json=struct_collections_json,
        )
