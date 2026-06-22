from __future__ import annotations

import ast
import copy
import inspect
from dataclasses import dataclass
from typing import Any, Hashable, Mapping, get_args, get_origin

from .api_contract import removed_call_diagnostic
from .connectors import Connector, ConnectorBundle, is_connector, is_connector_bundle
from .diagnostics import Diagnostic, make_diagnostic, render_diagnostic, snippet_from_text
from .dsl import Signal
from .hw import (
    Bundle,
    Circuit,
    Reg,
    Vec,
    Wire,
)
from .jit_cache import assigned_names_for, get_function_meta, get_signature, get_structural_metrics
from .literals import LiteralValue


class JitError(RuntimeError):
    def __init__(self, message: str, *, diagnostic: Diagnostic | None = None) -> None:
        self.diagnostic = diagnostic
        text = render_diagnostic(diagnostic) if diagnostic is not None else str(message)
        super().__init__(text)

    @classmethod
    def from_diagnostic(cls, diagnostic: Diagnostic) -> "JitError":
        return cls(diagnostic.message, diagnostic=diagnostic)


class _InlineReturn(RuntimeError):
    def __init__(self, value: Any) -> None:
        super().__init__("inline return")
        self.value = value


_HAS_AST_MATCH = hasattr(ast, "Match")


def _check_removed_api_call(node: ast.Call, *, compiler: "_Compiler") -> None:
    if not isinstance(node.func, ast.Attribute):
        return
    attr = str(node.func.attr)
    line = compiler._abs_lineno(node)
    col = getattr(node, "col_offset", None)
    col_out = (int(col) + 1) if isinstance(col, int) else None
    diag = removed_call_diagnostic(
        attr=attr,
        path=compiler.source_file,
        line=line,
        col=col_out,
        source_text=compiler.source_text,
        stage="jit",
    )
    if diag is not None:
        raise JitError.from_diagnostic(diag)


def _call_kind(fn: Any) -> str | None:
    k = getattr(fn, "__pycircuit_kind__", None)
    if isinstance(k, str):
        kk = k.strip().lower()
        if kk in {"module", "function", "const"}:
            if kk == "const":
                return "template"
            return kk
    if bool(getattr(fn, "__pycircuit_inline__", False)):
        return "function"
    if isinstance(getattr(fn, "__pycircuit_module_name__", None), str):
        return "module"
    return None


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
            raise JitError(f"invalid value-param type {ty!r}: expected iN/clock/reset") from e
        if w <= 0:
            raise JitError(f"invalid value-param type {ty!r}: iN width must be > 0")
        return f"i{w}"
    raise JitError(f"invalid value-param type {ty!r}: expected iN/clock/reset")


def _value_params_of(fn: Any) -> dict[str, str]:
    raw = getattr(fn, "__pycircuit_value_params__", None)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise JitError(f"invalid value_params metadata on {getattr(fn, '__name__', fn)!r}: expected mapping")
    out: dict[str, str] = {}
    for k in sorted(raw.keys(), key=lambda x: str(x)):
        name = str(k).strip()
        if not name:
            raise JitError("value_params contains an empty key")
        out[name] = _normalize_value_param_ty(str(raw[k]))
    return out


@dataclass(frozen=True)
class _IndexValue:
    """Placeholder for an SCF induction variable (index-typed SSA value)."""

    ref: str

    def __str__(self) -> str:
        return self.ref


def _assigned_names(stmts: list[ast.stmt]) -> frozenset[str]:
    return assigned_names_for(stmts)


def _expect_wire(v: Any, *, ctx: str) -> Wire:
    if isinstance(v, Connector):
        v = v.read()
    if isinstance(v, Wire):
        return v
    if isinstance(v, Reg):
        return v.q
    raise JitError(f"{ctx}: expected a Wire/Reg, got {type(v).__name__}")


def _wire_ifexpr(cond: Wire, true_v: Any, false_v: Any) -> Wire:
    if cond.ty != "i1":
        raise JitError("if-expression condition must be an i1 wire")
    if isinstance(true_v, Connector):
        true_v = true_v.read()
    if isinstance(false_v, Connector):
        false_v = false_v.read()
    if not isinstance(true_v, (Wire, Reg, Signal, int, LiteralValue)):
        raise JitError(f"if-expression true branch must be Wire/Reg/Signal/int/literal, got {type(true_v).__name__}")
    if not isinstance(false_v, (Wire, Reg, Signal, int, LiteralValue)):
        raise JitError(f"if-expression false branch must be Wire/Reg/Signal/int/literal, got {type(false_v).__name__}")
    return cond._select_internal(true_v, false_v)

_TemplateKey = tuple[int, Hashable, Hashable]


def _resolve_call_target(node: ast.Call, *, eval_expr: Any, env: dict[str, Any], globals_: dict[str, Any]) -> Any:
    if isinstance(node.func, ast.Attribute):
        recv = eval_expr(node.func.value)
        try:
            return getattr(recv, node.func.attr)
        except AttributeError as e:
            raise JitError(str(e)) from e
    if isinstance(node.func, ast.Name):
        fn = env.get(node.func.id, globals_.get(node.func.id))
        if fn is None:
            builtins_obj = globals_.get("__builtins__")
            if isinstance(builtins_obj, dict):
                fn = builtins_obj.get(node.func.id)
            elif builtins_obj is not None:
                fn = getattr(builtins_obj, node.func.id, None)
        if fn is None:
            raise JitError(f"unknown function {node.func.id!r}")
        return fn
    raise JitError("unsupported call target")


def _eval_call_args(node: ast.Call, *, eval_expr: Any) -> tuple[list[Any], dict[str, Any]]:
    args = [eval_expr(a) for a in node.args]
    kwargs = {kw.arg: eval_expr(kw.value) for kw in node.keywords if kw.arg is not None}
    return args, kwargs


def _template_meta_value(v: Any) -> Any | None:
    fn = getattr(v, "__pyc_template_value__", None)
    if not callable(fn):
        return None
    try:
        rep = fn()
    except Exception as e:  # noqa: BLE001
        raise JitError(f"template meta value provider failed for {type(v).__name__}: {e}") from e
    return rep


def _template_identity_snapshot(v: Any) -> Hashable:
    rep = _template_meta_value(v)
    if rep is not None:
        return ("meta", type(v).__name__, _template_identity_snapshot(rep))
    if isinstance(v, LiteralValue):
        w = int(v.width) if v.width is not None else None
        s = bool(v.signed) if v.signed is not None else None
        return ("literal", int(v.value), w, s)
    if isinstance(v, bool):
        return ("bool", bool(v))
    if isinstance(v, int):
        return ("int", int(v))
    if isinstance(v, str):
        return ("str", v)
    if v is None:
        return ("none",)
    if isinstance(v, Circuit):
        return ("circuit", id(v), str(getattr(v, "name", "")))
    if isinstance(v, Wire):
        return ("wire", id(v), v.sig.ref, v.ty, bool(v.signed))
    if isinstance(v, Reg):
        q = v.q
        return ("reg", id(v), q.sig.ref, q.ty, bool(q.signed))
    if isinstance(v, Signal):
        return ("signal", id(v), v.ref, v.ty)
    if isinstance(v, Connector):
        rd = v.read()
        return ("connector", type(v).__name__, id(v), str(getattr(v, "name", "")), _template_identity_snapshot(rd))
    if isinstance(v, ConnectorBundle):
        items = tuple((str(k), _template_identity_snapshot(vv)) for k, vv in sorted(v.items(), key=lambda kv: str(kv[0])))
        return ("connector_bundle", items)
    if isinstance(v, (list, tuple)):
        return (type(v).__name__, tuple(_template_identity_snapshot(x) for x in v))
    if isinstance(v, dict):
        items = tuple(
            (str(k), _template_identity_snapshot(vv))
            for k, vv in sorted(v.items(), key=lambda kv: str(kv[0]))
        )
        return ("dict", items)
    raise JitError(
        "template arguments must be canonicalizable primitives/containers or hardware references; "
        + f"unsupported value type: {type(v).__name__}"
    )


def _validate_template_return(v: Any, *, where: str = "return") -> None:
    rep = _template_meta_value(v)
    if rep is not None:
        _validate_template_return(rep, where=f"{where}.__pyc_template_value__()")
        return
    if v is None or isinstance(v, (bool, int, str, LiteralValue)):
        return
    if isinstance(v, (Wire, Reg, Signal, Connector, ConnectorBundle, Bundle, Vec)):
        raise JitError(f"@const {where} cannot be a hardware value ({type(v).__name__})")
    if isinstance(v, (list, tuple)):
        for i, elem in enumerate(v):
            _validate_template_return(elem, where=f"{where}[{i}]")
        return
    if isinstance(v, dict):
        for k, elem in v.items():
            if not isinstance(k, (str, int, bool)):
                raise JitError(
                    f"@const {where} dict keys must be str/int/bool, got {type(k).__name__}"
                )
            _validate_template_return(elem, where=f"{where}[{k!r}]")
        return
    raise JitError(f"@const {where} has unsupported value type: {type(v).__name__}")


def _emit_scf_yield(m: Circuit, values: list[Wire]) -> None:
    if not values:
        m.emit_line("scf.yield")
        return
    refs = ", ".join(v.ref for v in values)
    tys = ", ".join(v.ty for v in values)
    m.emit_line(f"scf.yield {refs} : {tys}")


def _emit_scf_if_header(m: Circuit, results: list[str], cond: Wire, result_types: list[str]) -> None:
    if not results:
        m.emit_line(f"scf.if {cond.ref} {{")
        return
    res_lhs = ", ".join(results)
    if len(result_types) == 1:
        ty_sig = result_types[0]
    else:
        ty_sig = f"({', '.join(result_types)})"
    m.emit_line(f"{res_lhs} = scf.if {cond.ref} -> {ty_sig} {{")


def _emit_scf_for_header(
    m: Circuit,
    results: list[str],
    iv_ref: str,
    lb_ref: str,
    ub_ref: str,
    step_ref: str,
    iter_args: list[tuple[str, Wire]],
    result_types: list[str],
) -> None:
    if not iter_args:
        m.emit_line(f"scf.for {iv_ref} = {lb_ref} to {ub_ref} step {step_ref} {{")
        return

    if not results:
        raise JitError("internal: scf.for with iter_args requires results")
    res_lhs = ", ".join(results)
    it_sig = ", ".join(f"{name} = {init.ref}" for name, init in iter_args)
    if len(result_types) == 1:
        ty_sig = result_types[0]
    else:
        ty_sig = f"({', '.join(result_types)})"
    m.emit_line(
        f"{res_lhs} = scf.for {iv_ref} = {lb_ref} to {ub_ref} step {step_ref} "
        + f"iter_args({it_sig}) -> {ty_sig} {{"
    )


class _Compiler:
    def __init__(
        self,
        m: Circuit,
        params: dict[str, Any],
        *,
        globals_: dict[str, Any],
        source_file: str | None = None,
        source_text: str | None = None,
        source_stem: str | None = None,
        line_offset: int = 0,
        value_param_names: set[str] | None = None,
        value_param_types: Mapping[str, str] | None = None,
    ) -> None:
        self.m = m
        self.env: dict[str, Any] = dict(params)
        self.globals = globals_
        self.source_file = source_file
        self.source_text = source_text
        self.source_stem = source_stem
        self.line_offset = int(line_offset)
        self._inline_stack: list[Any] = []
        self._callsite_counts: dict[tuple[str, int | None], int] = {}
        self._allow_auto_instance = True
        self._template_cache: dict[_TemplateKey, Any] = {}
        self._value_param_names: set[str] = set(value_param_names or ())
        self._value_param_types: dict[str, str] = dict(value_param_types or {})

    @staticmethod
    def _ty_width(ty: str) -> int:
        if not ty.startswith("i"):
            raise JitError(f"expected integer type iN, got {ty!r}")
        try:
            w = int(ty[1:])
        except ValueError as e:
            raise JitError(f"invalid integer type: {ty!r}") from e
        if w <= 0:
            raise JitError(f"invalid integer width in type: {ty!r}")
        return w

    def _coerce_to_type(self, v: Any, *, expected_ty: str, ctx: str) -> Wire:
        """Coerce a value into a Wire of `expected_ty` (ints become constants)."""
        if isinstance(v, Connector):
            v = v.read()
        if isinstance(v, Reg):
            v = v.q
        if isinstance(v, Wire):
            w = v
        elif isinstance(v, Signal):
            w = Wire(self.m, v)
        elif isinstance(v, LiteralValue):
            lit_w = int(v.width) if v.width is not None else self._ty_width(expected_ty)
            w = self.m.const(int(v.value), width=lit_w)
            if v.signed is not None:
                w = w.as_signed() if bool(v.signed) else w.as_unsigned()
        elif isinstance(v, bool):
            w = self.m.const(int(v), width=self._ty_width(expected_ty))
        elif isinstance(v, int):
            w = self.m.const(int(v), width=self._ty_width(expected_ty))
        else:
            raise JitError(f"{ctx}: expected Wire/Reg/Signal/int/literal, got {type(v).__name__}")

        if w.ty == expected_ty:
            return w

        if w.ty.startswith("i") and expected_ty.startswith("i"):
            ew = self._ty_width(expected_ty)
            if w.width < ew:
                return w._sext(width=ew) if w.signed else w._zext(width=ew)
            if w.width > ew:
                return w._trunc(width=ew)
            return w

        raise JitError(f"{ctx}: type mismatch, got {w.ty} expected {expected_ty}")

    def _scoped_name(self, base: str) -> str:
        scoped = base
        if hasattr(self.m, "scoped_name"):
            scoped = self.m.scoped_name(base)  # type: ignore[no-any-return]
        return scoped

    def _abs_lineno(self, node: ast.AST) -> int | None:
        line = getattr(node, "lineno", None)
        if not isinstance(line, int) or line <= 0:
            return None
        return self.line_offset + line

    @staticmethod
    def _node_col(node: ast.AST) -> int | None:
        col = getattr(node, "col_offset", None)
        if not isinstance(col, int) or col < 0:
            return None
        return int(col) + 1

    def _error_with_node(self, node: ast.AST, err: Exception, *, code: str = "PYC500", hint: str | None = None) -> JitError:
        if isinstance(err, JitError) and err.diagnostic is not None:
            return err
        message = str(err) if str(err).strip() else err.__class__.__name__
        line = self._abs_lineno(node)
        snippet = snippet_from_text(self.source_text, line) if (self.source_text is not None and line is not None) else None
        diag = make_diagnostic(
            code=code,
            stage="jit",
            path=self.source_file,
            line=line,
            col=self._node_col(node),
            message=message,
            hint=hint,
            snippet=snippet,
        )
        return JitError.from_diagnostic(diag)

    def _name_with_loc(self, name: str, node: ast.AST) -> str:
        line = self._abs_lineno(node)
        if line is None:
            return name
        if self.source_stem:
            return f"{name}__{self.source_stem}__L{line}"
        return f"{name}__L{line}"

    @staticmethod
    def _is_hw_value(v: Any) -> bool:
        if isinstance(v, (Wire, Reg, Signal, Vec, Bundle, Connector, LiteralValue)):
            return True
        if is_connector_bundle(v):
            return True
        if isinstance(v, (list, tuple)):
            return any(_Compiler._is_hw_value(x) for x in v)
        if isinstance(v, dict):
            return any(_Compiler._is_hw_value(x) for x in v.values())
        return False

    @staticmethod
    def _is_specialization_literal(v: Any) -> bool:
        if v is None or isinstance(v, (bool, int, str, LiteralValue)):
            return True
        if isinstance(v, (list, tuple)):
            return all(_Compiler._is_specialization_literal(x) for x in v)
        if isinstance(v, dict):
            return all(isinstance(k, str) and _Compiler._is_specialization_literal(vv) for k, vv in v.items())
        return False

    @staticmethod
    def _is_frontend_intrinsic_helper(fn: Any) -> bool:
        mod = getattr(fn, "__module__", None)
        if not isinstance(mod, str):
            return False
        return (
            mod.startswith("pycircuit.hw")
            or mod.startswith("pycircuit.spec")
            or mod.startswith("pycircuit.wiring")
            or mod.startswith("pycircuit.logic")
            or mod.startswith("pycircuit.lib")
        )

    @staticmethod
    def _return_annotation_blocks_instance(ret_ann: Any) -> bool:
        if ret_ann is inspect._empty:
            return False
        if ret_ann in (None, type(None), Wire, Reg, Signal, Bundle, Any, object):
            return False
        if isinstance(ret_ann, str):
            low = ret_ann.replace(" ", "").lower()
            return ("list[" in low) or ("dict[" in low) or ("set[" in low)

        origin = get_origin(ret_ann)
        if origin in (list, dict, set):
            return True
        if origin is tuple:
            return any(_Compiler._return_annotation_blocks_instance(a) for a in get_args(ret_ann))
        return False

    def _next_instance_name(self, fn: Any, node: ast.Call) -> str:
        base = getattr(fn, "__name__", "inst")
        line = self._abs_lineno(node)
        key = (str(base), line)
        idx = self._callsite_counts.get(key, 0) + 1
        self._callsite_counts[key] = idx
        loc = f"L{line}" if line is not None else "L0"
        return self._scoped_name(f"{base}__{loc}__N{idx}")

    def _maybe_instance_call(self, fn: Any, *, args: list[Any], kwargs: dict[str, Any], node: ast.Call) -> Any | None:
        # Auto-instance only applies to plain function calls in a multi-module design context.
        if self.m._design_ctx is None:  # noqa: SLF001
            return None
        if not inspect.isfunction(fn):
            return None
        if _call_kind(fn) != "module":
            return None
        # Auto-instance applies only to explicit module-style calls: fn(m, ...).
        if not args or args[0] is not self.m:
            return None

        callee_value_params = _value_params_of(fn)

        has_hw = any(self._is_hw_value(a) for a in args) or any(self._is_hw_value(v) for v in kwargs.values())
        # Value-params are always runtime boundary values, even when callsites
        # pass Python literals. Such calls must still materialize as instances.
        has_value_param_bindings = False
        if callee_value_params:
            try:
                sig_for_probe = get_signature(fn)
                ps_probe = list(sig_for_probe.parameters.values())
                pnames_probe = [
                    p.name
                    for p in ps_probe[1:]
                    if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                ]
                for i, _ in enumerate(args[1:]):
                    if i < len(pnames_probe) and pnames_probe[i] in callee_value_params:
                        has_value_param_bindings = True
                        break
                if not has_value_param_bindings:
                    for k in kwargs:
                        if str(k) in callee_value_params:
                            has_value_param_bindings = True
                            break
            except Exception:  # noqa: BLE001
                has_value_param_bindings = False

        if not has_hw and not callee_value_params and not has_value_param_bindings:
            return None

        sig = get_signature(fn)
        if self._return_annotation_blocks_instance(sig.return_annotation):
            return None

        # Keep auto-instance conservative for helper-style calls that pass
        # complex Python containers or non-canonical specialization objects.
        all_call_vals = [*args, *kwargs.values()]
        for v in all_call_vals:
            if v is self.m:
                continue
            if is_connector(v):
                continue
            if is_connector_bundle(v):
                raise JitError(
                    "@module call does not accept ConnectorBundle as a single port value; "
                    "bind each callee port explicitly"
                )
            if isinstance(v, (list, tuple, dict, set)):
                return None
            if self._is_hw_value(v):
                # Implicit port coercion: Wire/Reg/Signal values are accepted
                # and wrapped as connectors in Circuit.instance_handle.
                continue
            if not self._is_specialization_literal(v):
                return None

        params = list(sig.parameters.values())
        if not params:
            raise JitError("instance callee must take at least one argument (the Circuit builder)")

        # Allow callsites that pass the current builder explicitly:
        #   submod(m, in_a=..., ...)
        args_for_params = list(args)
        if args_for_params and args_for_params[0] is self.m:
            args_for_params = args_for_params[1:]

        # Build specialization params from call-site values that are not hardware values.
        param_names = [p.name for p in params[1:]]
        spec_params: dict[str, Any] = {}
        ports: dict[str, Any] = {}

        # Positional args map by function signature; hardware values become ports.
        if len(args_for_params) > len(param_names):
            raise JitError(f"too many positional arguments for auto-instance call to {getattr(fn, '__name__', fn)!r}")
        for i, v in enumerate(args_for_params):
            pname = param_names[i]
            if pname in callee_value_params:
                if is_connector_bundle(v):
                    raise JitError(
                        f"@module value-param {pname!r} cannot be a ConnectorBundle; "
                        "bind a scalar value/connector"
                    )
                ports[pname] = v
            elif is_connector(v):
                ports[pname] = v
            elif is_connector_bundle(v):
                raise JitError(
                    f"@module call positional arg {pname!r} cannot be a ConnectorBundle; "
                    "bind each formal port explicitly"
                )
            elif self._is_hw_value(v):
                ports[pname] = v
            else:
                spec_params[pname] = v

        # Keyword split: hardware values are ports, others are specialization params.
        for k, v in kwargs.items():
            key = str(k)
            if key in callee_value_params:
                if is_connector_bundle(v):
                    raise JitError(
                        f"@module value-param {key!r} cannot be a ConnectorBundle; "
                        "bind a scalar value/connector"
                    )
                ports[key] = v
            elif is_connector(v):
                ports[str(k)] = v
            elif is_connector_bundle(v):
                raise JitError(
                    f"@module call kwarg {k!r} cannot be a ConnectorBundle; "
                    "bind each formal port explicitly"
                )
            elif self._is_hw_value(v):
                ports[str(k)] = v
            else:
                spec_params[str(k)] = v

        inst_name = self._next_instance_name(fn, node)
        try:
            return self.m.instance(fn, name=inst_name, params=spec_params, **ports)
        except Exception as e:
            raise JitError(f"auto-instance call failed for {getattr(fn, '__name__', fn)!r}: {e}") from e

    def _alias_if_wire(self, v: Any, *, base_name: str, node: ast.AST) -> Any:
        if isinstance(v, Wire):
            # `pyc.assign` destinations must be defined by `pyc.wire`. The JIT
            # compiler normally wraps assigned values in `pyc.alias` for stable
            # naming, but that would break assignable/backedge wires.
            if getattr(v, "assignable", False):
                return v
            n = self._scoped_name(self._name_with_loc(base_name, node))
            return Wire(self.m, self.m.alias(v.sig, name=n), signed=v.signed)
        if isinstance(v, Reg):
            n = self._scoped_name(self._name_with_loc(base_name, node))
            q_named = Wire(self.m, self.m.alias(v.q.sig, name=n), signed=v.q.signed)
            return Reg(q=q_named, clk=v.clk, rst=v.rst, en=v.en, next=v.next, init=v.init)
        return v

    def _assign_wire_target(self, target: ast.AST, value: Any, *, node: ast.AST) -> None:
        if isinstance(target, ast.Name):
            if isinstance(value, _IndexValue):
                raise JitError("cannot assign index values into hardware variables")
            self.env[target.id] = self._alias_if_wire(value, base_name=target.id, node=node)
            return
        if isinstance(target, ast.Subscript):
            if isinstance(target.slice, ast.Slice):
                raise JitError("slice assignment is not supported")
            container = self.eval_expr(target.value)
            idx_v = self.eval_expr(target.slice)
            if isinstance(idx_v, LiteralValue):
                idx_v = int(idx_v.value)
            if isinstance(container, list):
                if not isinstance(idx_v, int):
                    raise JitError("list assignment index must be an int")
                try:
                    container[idx_v] = value
                except IndexError as e:
                    raise JitError(f"list assignment index out of range: {idx_v}") from e
                return
            if isinstance(container, dict):
                container[idx_v] = value
                return
            raise JitError("subscript assignment target must be a list or dict variable")
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, Vec):
                value = list(value.elems)
            if not isinstance(value, (tuple, list)):
                raise JitError("tuple/list assignment requires tuple/list values")
            if len(value) != len(target.elts):
                raise JitError("tuple/list assignment arity mismatch")
            for sub_t, sub_v in zip(target.elts, value):
                self._assign_wire_target(sub_t, sub_v, node=node)
            return
        raise JitError("assignment target must be a name, subscript, or tuple/list of targets")

    # ---- constant evaluation (for range bounds, widths, etc) ----
    def eval_const(self, node: ast.AST) -> int:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return int(node.value)
            if isinstance(node.value, int):
                return int(node.value)
            raise JitError(f"unsupported constant in const-eval: {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id in self._value_param_names:
                raise JitError(
                    f"value parameter {node.id!r} cannot be used in a compile-time context "
                    "(range bounds/widths/indices must be static)"
                )
            v = self.env.get(node.id, self.globals.get(node.id))
            if isinstance(v, bool):
                return int(v)
            if isinstance(v, int):
                return int(v)
            if isinstance(v, LiteralValue):
                return int(v.value)
            raise JitError(f"const-eval name {node.id!r} is not an int/bool/literal")
        if isinstance(node, ast.Attribute):
            v = self.eval_expr(node)
            if isinstance(v, bool):
                return int(v)
            if isinstance(v, int):
                return int(v)
            if isinstance(v, LiteralValue):
                return int(v.value)
            raise JitError(f"const-eval attribute {ast.unparse(node)!r} is not an int/bool/literal")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self.eval_const(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            return +self.eval_const(node.operand)
        if isinstance(node, ast.BinOp):
            a = self.eval_const(node.left)
            b = self.eval_const(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.FloorDiv):
                return a // b
            if isinstance(node.op, ast.Div):
                return a // b
            if isinstance(node.op, ast.Mod):
                return a % b
            if isinstance(node.op, ast.LShift):
                return a << b
            if isinstance(node.op, ast.RShift):
                return a >> b
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "int" and len(node.args) == 1 and not node.keywords:
                return int(self.eval_const(node.args[0]))
            if isinstance(node.func, ast.Name) and node.func.id == "len" and len(node.args) == 1 and not node.keywords:
                v = self.eval_expr(node.args[0])
                if isinstance(v, (list, tuple, Vec, Bundle)):
                    return int(len(v))
                raise JitError(f"len() const-eval expects list/tuple/Vec/Bundle, got {type(v).__name__}")
        raise JitError(f"unsupported const-eval expression: {ast.dump(node, include_attributes=False)}")

    # ---- expression evaluation (hardware + params) ----
    def eval_expr(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                    continue
                if isinstance(v, ast.FormattedValue):
                    inner = self.eval_expr(v.value)
                    if isinstance(inner, (str, int, bool, LiteralValue)):
                        if isinstance(inner, LiteralValue):
                            parts.append(str(int(inner.value)))
                            continue
                        parts.append(str(inner))
                        continue
                raise JitError("f-strings in JIT expressions must resolve to compile-time str/int/bool values")
            return "".join(parts)
        if isinstance(node, ast.List):
            elts = [self.eval_expr(e) for e in node.elts]
            if elts and all(isinstance(e, (Wire, Reg)) for e in elts):
                return Vec(elts)
            return elts
        if isinstance(node, ast.ListComp):
            if len(node.generators) != 1:
                raise JitError("only single-generator list comprehensions are supported")
            gen = node.generators[0]
            if gen.is_async:
                raise JitError("async list comprehensions are not supported")
            if gen.ifs:
                raise JitError("list-comprehension if-filters are not supported")
            if not isinstance(gen.target, ast.Name):
                raise JitError("list-comprehension target must be a simple name")
            iter_vals: list[Any]
            if isinstance(gen.iter, ast.Call) and isinstance(gen.iter.func, ast.Name) and gen.iter.func.id == "range":
                args = gen.iter.args
                if not (1 <= len(args) <= 3):
                    raise JitError("range() in list comprehensions must have 1..3 arguments")
                if len(args) == 1:
                    lb_i = 0
                    ub_i = self.eval_const(args[0])
                    step_i = 1
                elif len(args) == 2:
                    lb_i = self.eval_const(args[0])
                    ub_i = self.eval_const(args[1])
                    step_i = 1
                else:
                    lb_i = self.eval_const(args[0])
                    ub_i = self.eval_const(args[1])
                    step_i = self.eval_const(args[2])
                if step_i <= 0:
                    raise JitError("range() step in list comprehensions must be > 0")
                iter_vals = [int(i) for i in range(lb_i, ub_i, step_i)]
            else:
                raw_iter = self.eval_expr(gen.iter)
                if isinstance(raw_iter, range):
                    iter_vals = [int(i) for i in raw_iter]
                elif isinstance(raw_iter, (list, tuple, Vec)):
                    iter_vals = list(raw_iter)
                else:
                    raise JitError(
                        "list comprehensions only support static range/list/tuple iterators, got "
                        + f"{type(raw_iter).__name__} from `{ast.unparse(gen.iter)}`"
                    )

            name = gen.target.id
            had_prev = name in self.env
            prev = self.env.get(name)
            out: list[Any] = []
            for i in iter_vals:
                self.env[name] = i
                out.append(self.eval_expr(node.elt))
            if had_prev:
                self.env[name] = prev
            else:
                self.env.pop(name, None)
            if out and all(isinstance(e, (Wire, Reg)) for e in out):
                return Vec(out)
            return out
        if isinstance(node, ast.Tuple):
            elts = [self.eval_expr(e) for e in node.elts]
            if elts and all(isinstance(e, (Wire, Reg)) for e in elts):
                return Vec(elts)
            return tuple(elts)
        if isinstance(node, ast.Dict):
            out: dict[Any, Any] = {}
            for k_node, v_node in zip(node.keys, node.values):
                if k_node is None:
                    raise JitError("dict unpacking is not supported in JIT expressions")
                k = self.eval_expr(k_node)
                try:
                    hash(k)
                except Exception as e:  # noqa: BLE001
                    raise JitError(f"dict key must be hashable, got {type(k).__name__}") from e
                out[k] = self.eval_expr(v_node)
            return out
        if isinstance(node, ast.Name):
            if node.id in self.env:
                v = self.env[node.id]
                if isinstance(v, _IndexValue):
                    raise JitError("loop induction variables are not usable in expressions (prototype limitation)")
                return v
            if node.id in self.globals:
                return self.globals[node.id]
            raise JitError(f"unknown name {node.id!r}")
        if isinstance(node, ast.Subscript):
            base = self.eval_expr(node.value)
            sl = node.slice
            if isinstance(base, Vec):
                if isinstance(sl, ast.Tuple):
                    idx_parts: list[int | slice] = []
                    for part in sl.elts:
                        if isinstance(part, ast.Slice):
                            if part.step is not None:
                                raise JitError("Vec slicing does not support step (prototype)")
                            lo = None if part.lower is None else self.eval_const(part.lower)
                            hi = None if part.upper is None else self.eval_const(part.upper)
                            idx_parts.append(slice(lo, hi, None))
                        else:
                            idx_parts.append(int(self.eval_const(part)))
                    return base[tuple(idx_parts)]
                if isinstance(sl, ast.Slice):
                    if sl.step is not None:
                        raise JitError("Vec slicing does not support step (prototype)")
                    lo = None
                    if sl.lower is not None:
                        lo = self.eval_const(sl.lower)
                    hi = None
                    if sl.upper is not None:
                        hi = self.eval_const(sl.upper)
                    return base[slice(lo, hi, None)]
                idx_i = self.eval_const(sl)
                return base[int(idx_i)]
            if isinstance(base, Bundle):
                if isinstance(sl, ast.Name) and sl.id in self._value_param_names:
                    raise JitError(
                        f"value parameter {sl.id!r} cannot be used as a bundle key; "
                        "bundle keys must be compile-time constant strings"
                    )
                key_v = self.eval_expr(sl)
                if isinstance(key_v, str):
                    return base[key_v]
                raise JitError("Bundle subscript must resolve to a compile-time constant string key")
            if isinstance(base, ConnectorBundle):
                if isinstance(sl, ast.Name) and sl.id in self._value_param_names:
                    raise JitError(
                        f"value parameter {sl.id!r} cannot be used as a bundle key; "
                        "bundle keys must be compile-time constant strings"
                    )
                key_v = self.eval_expr(sl)
                if isinstance(key_v, str):
                    return base[key_v]
                raise JitError("ConnectorBundle subscript must resolve to a compile-time constant string key")
            if isinstance(base, (Wire, Reg)):
                if isinstance(sl, ast.Slice):
                    if sl.step is not None:
                        raise JitError("wire slicing does not support step")
                    lo = None if sl.lower is None else self.eval_const(sl.lower)
                    hi = None if sl.upper is None else self.eval_const(sl.upper)
                    return _expect_wire(base, ctx="wire slice")[slice(lo, hi, None)]
                bit = int(self.eval_const(sl))
                return _expect_wire(base, ctx="wire subscript")[bit]
            if isinstance(base, dict):
                key = self.eval_expr(sl)
                if isinstance(key, LiteralValue):
                    key = int(key.value)
                try:
                    return base[key]
                except Exception as e:  # noqa: BLE001
                    raise JitError(f"dict subscript failed: {e}") from e
            if isinstance(base, (list, tuple)):
                return base[int(self.eval_const(sl))]
            if hasattr(base, "__getitem__"):
                if isinstance(sl, ast.Slice):
                    if sl.step is not None:
                        raise JitError("slice step is not supported for generic subscript bases")
                    lo = None if sl.lower is None else self.eval_const(sl.lower)
                    hi = None if sl.upper is None else self.eval_const(sl.upper)
                    idx = slice(lo, hi, None)
                else:
                    idx = self.eval_expr(sl)
                    if isinstance(idx, LiteralValue):
                        idx = int(idx.value)
                try:
                    return base[idx]
                except Exception as e:  # noqa: BLE001
                    raise JitError(f"subscript failed for {type(base).__name__}: {e}") from e
            raise JitError(f"unsupported subscript base type: {type(base).__name__}")
        if isinstance(node, ast.BinOp):
            lhs = self.eval_expr(node.left)
            rhs = self.eval_expr(node.right)
            if isinstance(lhs, Connector):
                lhs = lhs.read()
            if isinstance(rhs, Connector):
                rhs = rhs.read()

            def _as_py_int(v: Any) -> int:
                if isinstance(v, LiteralValue):
                    return int(v.value)
                return int(v)

            if isinstance(node.op, ast.Add):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs + rhs
                if isinstance(lhs, (Wire, Reg)):
                    return lhs + rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs + lhs
                if isinstance(lhs, list) and isinstance(rhs, list):
                    return lhs + rhs
                if isinstance(lhs, tuple) and isinstance(rhs, tuple):
                    return lhs + rhs
                return _as_py_int(lhs) + _as_py_int(rhs)
            if isinstance(node.op, ast.Sub):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs - rhs
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="-") - rhs
                if isinstance(rhs, (Wire, Reg)):
                    return _as_py_int(lhs) - _expect_wire(rhs, ctx="-")
                return _as_py_int(lhs) - _as_py_int(rhs)
            if isinstance(node.op, ast.Mult):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs * rhs
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="*") * rhs
                if isinstance(rhs, (Wire, Reg)):
                    return _expect_wire(rhs, ctx="*") * lhs
                return _as_py_int(lhs) * _as_py_int(rhs)
            if isinstance(node.op, ast.Div):
                raise JitError("hardware `/` division is not supported; use `//` for integer division")
            if isinstance(node.op, ast.FloorDiv):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs // rhs
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="/") // rhs
                if isinstance(rhs, (Wire, Reg)):
                    w = _expect_wire(rhs, ctx="/")
                    lhs_w = w._as_wire(_as_py_int(lhs), width=w.width)
                    return lhs_w // w
                return _as_py_int(lhs) // _as_py_int(rhs)
            if isinstance(node.op, ast.Mod):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs % rhs
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="%") % rhs
                if isinstance(rhs, (Wire, Reg)):
                    w = _expect_wire(rhs, ctx="%")
                    lhs_w = w._as_wire(_as_py_int(lhs), width=w.width)
                    return lhs_w % w
                return _as_py_int(lhs) % _as_py_int(rhs)
            if isinstance(node.op, ast.BitAnd):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs & rhs
                if isinstance(lhs, (Wire, Reg)):
                    return lhs & rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs & lhs
                return _as_py_int(lhs) & _as_py_int(rhs)
            if isinstance(node.op, ast.BitOr):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs | rhs
                if isinstance(lhs, (Wire, Reg)):
                    return lhs | rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs | lhs
                return _as_py_int(lhs) | _as_py_int(rhs)
            if isinstance(node.op, ast.BitXor):
                if isinstance(lhs, Vec) or isinstance(rhs, Vec):
                    return lhs ^ rhs
                if isinstance(lhs, (Wire, Reg)):
                    return lhs ^ rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs ^ lhs
                return _as_py_int(lhs) ^ _as_py_int(rhs)
            if isinstance(node.op, ast.LShift):
                if isinstance(lhs, Vec):
                    amt = rhs.value if isinstance(rhs, LiteralValue) else rhs
                    if not isinstance(amt, int):
                        raise JitError("Vec << only supports constant shift amounts")
                    return lhs << int(amt)
                if isinstance(lhs, (Wire, Reg)):
                    w = _expect_wire(lhs, ctx="<<")
                    amt = rhs.value if isinstance(rhs, LiteralValue) else rhs
                    if not isinstance(amt, int):
                        raise JitError("<< only supports constant shift amounts")
                    return w.shl(amount=int(amt))
                if isinstance(rhs, (Wire, Reg)):
                    raise JitError("<< requires a wire on the left side when using hardware values")
                return _as_py_int(lhs) << _as_py_int(rhs)
            if isinstance(node.op, ast.RShift):
                if isinstance(lhs, Vec):
                    amt = rhs.value if isinstance(rhs, LiteralValue) else rhs
                    if not isinstance(amt, int):
                        raise JitError("Vec >> only supports constant shift amounts")
                    return lhs >> int(amt)
                if isinstance(lhs, (Wire, Reg)):
                    w = _expect_wire(lhs, ctx=">>")
                    amt = rhs.value if isinstance(rhs, LiteralValue) else rhs
                    if not isinstance(amt, int):
                        raise JitError(">> only supports constant shift amounts")
                    return w >> int(amt)
                if isinstance(rhs, (Wire, Reg)):
                    raise JitError(">> requires a wire on the left side when using hardware values")
                return _as_py_int(lhs) >> _as_py_int(rhs)
        if isinstance(node, ast.UnaryOp):
            v = self.eval_expr(node.operand)
            if isinstance(v, Connector):
                v = v.read()
            if isinstance(node.op, ast.Invert):
                if isinstance(v, Vec):
                    return ~v
                if isinstance(v, LiteralValue):
                    return ~int(v.value)
                if isinstance(v, (int, bool)):
                    return ~int(v)
                w = _expect_wire(v, ctx="~")
                return ~w
            if isinstance(node.op, ast.Not):
                if isinstance(v, (Wire, Reg)):
                    w = _expect_wire(v, ctx="not")
                    if w.ty != "i1":
                        raise JitError("not only supports i1 wires")
                    return ~w
                return not bool(v)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                out = self.eval_expr(node.values[0])
                if isinstance(out, Connector):
                    out = out.read()
                for nxt in node.values[1:]:
                    b = self.eval_expr(nxt)
                    if isinstance(b, Connector):
                        b = b.read()
                    if isinstance(out, (Wire, Reg)) or isinstance(b, (Wire, Reg)):
                        if isinstance(out, (Wire, Reg)):
                            out = _expect_wire(out, ctx="and") & b
                        else:
                            out = _expect_wire(b, ctx="and") & out
                    else:
                        out = bool(out) and bool(b)
                return out
            if isinstance(node.op, ast.Or):
                out = self.eval_expr(node.values[0])
                if isinstance(out, Connector):
                    out = out.read()
                for nxt in node.values[1:]:
                    b = self.eval_expr(nxt)
                    if isinstance(b, Connector):
                        b = b.read()
                    if isinstance(out, (Wire, Reg)) or isinstance(b, (Wire, Reg)):
                        if isinstance(out, (Wire, Reg)):
                            out = _expect_wire(out, ctx="or") | b
                        else:
                            out = _expect_wire(b, ctx="or") | out
                    else:
                        out = bool(out) or bool(b)
                return out
        if isinstance(node, ast.IfExp):
            cond_v = self.eval_expr(node.test)
            if isinstance(cond_v, LiteralValue):
                return self.eval_expr(node.body if bool(int(cond_v.value)) else node.orelse)
            if not isinstance(cond_v, (Wire, Reg)) and isinstance(cond_v, (bool, int)):
                return self.eval_expr(node.body if bool(cond_v) else node.orelse)

            cond = _expect_wire(cond_v, ctx="if-expression condition")
            true_v = self.eval_expr(node.body)
            false_v = self.eval_expr(node.orelse)
            return _wire_ifexpr(cond, true_v, false_v)
        if isinstance(node, ast.Compare):
            def _py_cmp_value(v: Any) -> Any:
                if isinstance(v, LiteralValue):
                    return int(v.value)
                return v
            def _eval_single_compare(op: ast.cmpop, lhs: Any, rhs: Any) -> Any:
                if isinstance(op, ast.Is):
                    return lhs is rhs
                if isinstance(op, ast.IsNot):
                    return lhs is not rhs
                if isinstance(op, ast.Eq):
                    if isinstance(lhs, Vec):
                        return lhs == rhs
                    if isinstance(rhs, Vec):
                        return rhs == lhs
                    if not isinstance(lhs, (Wire, Reg)) and not isinstance(rhs, (Wire, Reg)):
                        return _py_cmp_value(lhs) == _py_cmp_value(rhs)
                    w = _expect_wire(lhs, ctx="==") if isinstance(lhs, (Wire, Reg)) else _expect_wire(rhs, ctx="==")
                    return w == (rhs if isinstance(lhs, (Wire, Reg)) else lhs)
                if isinstance(op, ast.NotEq):
                    if isinstance(lhs, Vec):
                        return lhs != rhs
                    if isinstance(rhs, Vec):
                        return rhs != lhs
                    if not isinstance(lhs, (Wire, Reg)) and not isinstance(rhs, (Wire, Reg)):
                        return _py_cmp_value(lhs) != _py_cmp_value(rhs)
                    w = _expect_wire(lhs, ctx="!=") if isinstance(lhs, (Wire, Reg)) else _expect_wire(rhs, ctx="!=")
                    eq = w == (rhs if isinstance(lhs, (Wire, Reg)) else lhs)
                    return ~eq
                if isinstance(op, ast.Lt):
                    if isinstance(lhs, Vec):
                        return lhs < rhs
                    if isinstance(rhs, Vec):
                        return rhs > lhs
                    if isinstance(lhs, (Wire, Reg)):
                        return _expect_wire(lhs, ctx="<") < rhs
                    if isinstance(rhs, (Wire, Reg)):
                        # a < b  ==>  b > a
                        return _expect_wire(rhs, ctx="<") > lhs
                    lhs_i = int(lhs.value) if isinstance(lhs, LiteralValue) else int(lhs)
                    rhs_i = int(rhs.value) if isinstance(rhs, LiteralValue) else int(rhs)
                    return lhs_i < rhs_i
                if isinstance(op, ast.LtE):
                    if isinstance(lhs, Vec):
                        return lhs <= rhs
                    if isinstance(rhs, Vec):
                        return rhs >= lhs
                    if isinstance(lhs, (Wire, Reg)):
                        return _expect_wire(lhs, ctx="<=") <= rhs
                    if isinstance(rhs, (Wire, Reg)):
                        return _expect_wire(rhs, ctx="<=") >= lhs
                    lhs_i = int(lhs.value) if isinstance(lhs, LiteralValue) else int(lhs)
                    rhs_i = int(rhs.value) if isinstance(rhs, LiteralValue) else int(rhs)
                    return lhs_i <= rhs_i
                if isinstance(op, ast.Gt):
                    if isinstance(lhs, Vec):
                        return lhs > rhs
                    if isinstance(rhs, Vec):
                        return rhs < lhs
                    if isinstance(lhs, (Wire, Reg)):
                        return _expect_wire(lhs, ctx=">") > rhs
                    if isinstance(rhs, (Wire, Reg)):
                        # a > b  ==>  b < a
                        return _expect_wire(rhs, ctx=">") < lhs
                    lhs_i = int(lhs.value) if isinstance(lhs, LiteralValue) else int(lhs)
                    rhs_i = int(rhs.value) if isinstance(rhs, LiteralValue) else int(rhs)
                    return lhs_i > rhs_i
                if isinstance(op, ast.GtE):
                    if isinstance(lhs, Vec):
                        return lhs >= rhs
                    if isinstance(rhs, Vec):
                        return rhs <= lhs
                    if isinstance(lhs, (Wire, Reg)):
                        return _expect_wire(lhs, ctx=">=") >= rhs
                    if isinstance(rhs, (Wire, Reg)):
                        return _expect_wire(rhs, ctx=">=") <= lhs
                    lhs_i = int(lhs.value) if isinstance(lhs, LiteralValue) else int(lhs)
                    rhs_i = int(rhs.value) if isinstance(rhs, LiteralValue) else int(rhs)
                    return lhs_i >= rhs_i
                raise JitError(f"unsupported comparison operator: {op.__class__.__name__}")

            lhs = self.eval_expr(node.left)
            if isinstance(lhs, Connector):
                lhs = lhs.read()
            chain_out: Any | None = None
            for op, rhs_node in zip(node.ops, node.comparators):
                rhs = self.eval_expr(rhs_node)
                if isinstance(rhs, Connector):
                    rhs = rhs.read()
                cmp_out = _eval_single_compare(op, lhs, rhs)
                if chain_out is None:
                    chain_out = cmp_out
                elif isinstance(chain_out, (Wire, Reg, Vec)) or isinstance(cmp_out, (Wire, Reg, Vec)):
                    if isinstance(chain_out, Vec):
                        chain_out = chain_out & cmp_out
                    elif isinstance(cmp_out, Vec):
                        chain_out = cmp_out & chain_out
                    elif isinstance(chain_out, (Wire, Reg)):
                        chain_out = _expect_wire(chain_out, ctx="comparison chain") & cmp_out
                    else:
                        chain_out = _expect_wire(cmp_out, ctx="comparison chain") & chain_out
                else:
                    chain_out = bool(chain_out) and bool(cmp_out)
                lhs = rhs
            if chain_out is None:
                raise JitError("comparison expression is empty")
            return chain_out
        if isinstance(node, ast.Call):
            return self.eval_call(node)
        if isinstance(node, ast.Attribute):
            base = self.eval_expr(node.value)
            try:
                return getattr(base, node.attr)
            except AttributeError as e:
                raise JitError(str(e)) from e

        raise JitError(f"unsupported expression: {ast.dump(node, include_attributes=False)}")

    def eval_call(self, node: ast.Call) -> Any:
        _check_removed_api_call(node, compiler=self)
        if isinstance(node.func, ast.Name) and node.func.id == "range":
            raise JitError("range() is only supported in for-loops")

        fn = _resolve_call_target(node, eval_expr=self.eval_expr, env=self.env, globals_=self.globals)
        args, kwargs = _eval_call_args(node, eval_expr=self.eval_expr)
        kind = _call_kind(fn)
        has_hw = any(self._is_hw_value(a) for a in args) or any(self._is_hw_value(v) for v in kwargs.values())
        try:
            if has_hw and inspect.isfunction(fn) and kind is None and not self._is_frontend_intrinsic_helper(fn):
                struct_metrics = get_structural_metrics(fn)
                if struct_metrics.module_call_count > 0 or struct_metrics.state_call_count > 0:
                    raise JitError(
                        f"hardware-carrying call to undecorated function {getattr(fn, '__name__', fn)!r}; "
                        "use @module for hierarchy boundaries, @function for inline helpers, or @const for compile-time metaprogramming"
                    )

            if kind == "module":
                # Module calls with hardware values are hierarchy boundaries and
                # must materialize as `pyc.instance` through `Circuit.instance`.
                if isinstance(node.func, ast.Name) and self._allow_auto_instance:
                    inst = self._maybe_instance_call(fn, args=args, kwargs=kwargs, node=node)
                    if inst is not None:
                        return inst
                if has_hw:
                    raise JitError(
                        f"@module call {getattr(fn, '__name__', fn)!r} must pass the current Circuit as first argument "
                        "and bind inter-module ports explicitly (Connector/Wire/Reg/Signal)"
                    )

            if kind == "function":
                return self._eval_inline_call(fn, args=args, kwargs=kwargs)
            if kind == "template":
                return self._eval_template_call(fn, args=args, kwargs=kwargs)
            return fn(*args, **kwargs)
        except TypeError as e:
            raise JitError(f"call failed: {e}") from e

    def _eval_inline_call(
        self,
        fn: Any,
        *,
        args: list[Any],
        kwargs: dict[str, Any],
        require_builder: bool = True,
    ) -> Any:
        if fn in self._inline_stack:
            raise JitError(f"recursive @function call is not supported: {getattr(fn, '__name__', fn)!r}")

        try:
            fn_name = getattr(fn, "__name__", None)
            if not isinstance(fn_name, str) or not fn_name:
                raise JitError(f"cannot inline non-function target: {fn!r}")
            meta = get_function_meta(fn, fn_name=fn_name)
        except OSError as e:
            raise JitError(f"cannot inline {getattr(fn, '__name__', fn)!r}: failed to read source ({e})") from e
        except RuntimeError as e:
            raise JitError(f"cannot inline {getattr(fn, '__name__', fn)!r}: {e}") from e

        fdef = meta.fdef
        struct_metrics = get_structural_metrics(fn)
        if struct_metrics.module_call_count > 0:
            raise JitError(
                f"@function {getattr(fn, '__name__', fn)!r} must not instantiate modules/collections "
                f"(module_call_count={struct_metrics.module_call_count}); "
                "promote the repeated hierarchy to `@module` and instantiate via `array(...)`"
            )
        if struct_metrics.state_call_count > 0:
            raise JitError(
                f"@function {getattr(fn, '__name__', fn)!r} must not allocate state "
                f"(state_call_count={struct_metrics.state_call_count}); "
                "move the stateful logic behind a `@module` boundary"
            )
        if struct_metrics.estimated_inline_cost > 1400:
            raise JitError(
                f"@function {getattr(fn, '__name__', fn)!r} exceeds inline complexity cap "
                f"(estimated_inline_cost={struct_metrics.estimated_inline_cost} > 1400); "
                "split the helper or promote it to `@module`"
            )
        if struct_metrics.repeat_pressure() > 96:
            raise JitError(
                f"@function {getattr(fn, '__name__', fn)!r} has repeated inline hardware pressure "
                f"({struct_metrics.repeat_pressure()} > 96); "
                "use `spec.module_family(...)` + `array(...)` for repeated same-shape structure"
            )

        if require_builder:
            if not fdef.args.args:
                raise JitError("@function must take at least one argument (the Circuit builder)")
            builder_arg = fdef.args.args[0].arg
        else:
            builder_arg = None

        # Use Python's own binding semantics for args/kwargs/defaults.
        try:
            bound = meta.signature.bind(*args, **kwargs)
        except TypeError as e:
            raise JitError(f"inline call failed: {e}") from e
        bound.apply_defaults()

        if require_builder:
            if builder_arg not in bound.arguments:
                raise JitError("internal: failed to bind builder argument for @function call")
            if bound.arguments[builder_arg] is not self.m:
                raise JitError("@function must be called with the current Circuit builder")

        child = _Compiler(
            self.m,
            params=dict(bound.arguments),
            globals_=getattr(fn, "__globals__", {}),
            source_file=meta.source_file,
            source_text=meta.source,
            source_stem=meta.source_stem,
            line_offset=int(meta.start_line - 1),
            value_param_names=set(self._value_param_names),
            value_param_types=dict(self._value_param_types),
        )
        if not require_builder:
            child._allow_auto_instance = False
        child._template_cache = self._template_cache
        child._inline_stack = [*self._inline_stack, fn]

        for stmt in fdef.body:
            if isinstance(stmt, ast.Return):
                if stmt.value is None:
                    return None
                return child.eval_expr(stmt.value)
            try:
                child.compile_stmt(stmt)
            except _InlineReturn as ret:
                return ret.value
            except Exception as e:  # noqa: BLE001
                raise child._error_with_node(stmt, e, code="PYC510", hint=f"inline call target: {fn_name!r}") from e

        # No explicit return => None.
        return None

    def _snapshot_template_purity_state(self) -> dict[str, Any]:
        snap: dict[str, Any] = {
            "lines": list(self.m._lines),  # noqa: SLF001
            "next_tmp": int(self.m._next_tmp),  # noqa: SLF001
            "args": list(self.m._args),  # noqa: SLF001
            "results": list(self.m._results),  # noqa: SLF001
            "finalizers": list(getattr(self.m, "_finalizers", [])),  # noqa: SLF001
            "indent_level": int(getattr(self.m, "_indent_level", 0)),  # noqa: SLF001
            "func_attrs": dict(getattr(self.m, "_func_attrs", {})),  # noqa: SLF001
        }
        if hasattr(self.m, "_scope_stack"):
            snap["scope_stack"] = list(getattr(self.m, "_scope_stack"))  # noqa: SLF001
        if hasattr(self.m, "_debug_exports"):
            snap["debug_exports"] = dict(getattr(self.m, "_debug_exports"))  # noqa: SLF001
        return snap

    def _restore_template_purity_state(self, snap: Mapping[str, Any]) -> None:
        self.m._lines = list(snap["lines"])  # noqa: SLF001
        self.m._next_tmp = int(snap["next_tmp"])  # noqa: SLF001
        self.m._args = list(snap["args"])  # noqa: SLF001
        self.m._results = list(snap["results"])  # noqa: SLF001
        if hasattr(self.m, "_finalizers"):
            self.m._finalizers = list(snap.get("finalizers", []))  # noqa: SLF001
        if hasattr(self.m, "_indent_level"):
            self.m._indent_level = int(snap.get("indent_level", 0))  # noqa: SLF001
        if hasattr(self.m, "_func_attrs"):
            self.m._func_attrs = dict(snap.get("func_attrs", {}))  # noqa: SLF001
        if hasattr(self.m, "_scope_stack"):
            self.m._scope_stack = list(snap.get("scope_stack", []))  # noqa: SLF001
        if hasattr(self.m, "_debug_exports"):
            self.m._debug_exports = dict(snap.get("debug_exports", {}))  # noqa: SLF001

    def _template_purity_mutations(self, snap: Mapping[str, Any]) -> list[str]:
        changed: list[str] = []
        if list(self.m._lines) != list(snap["lines"]):  # noqa: SLF001
            changed.append("_lines")
        if int(self.m._next_tmp) != int(snap["next_tmp"]):  # noqa: SLF001
            changed.append("_next_tmp")
        if list(self.m._args) != list(snap["args"]):  # noqa: SLF001
            changed.append("_args")
        if list(self.m._results) != list(snap["results"]):  # noqa: SLF001
            changed.append("_results")
        if list(getattr(self.m, "_finalizers", [])) != list(snap.get("finalizers", [])):  # noqa: SLF001
            changed.append("_finalizers")
        if int(getattr(self.m, "_indent_level", 0)) != int(snap.get("indent_level", 0)):  # noqa: SLF001
            changed.append("_indent_level")
        if dict(getattr(self.m, "_func_attrs", {})) != dict(snap.get("func_attrs", {})):  # noqa: SLF001
            changed.append("_func_attrs")
        if hasattr(self.m, "_scope_stack") and list(getattr(self.m, "_scope_stack")) != list(snap.get("scope_stack", [])):  # noqa: SLF001
            changed.append("_scope_stack")
        if hasattr(self.m, "_debug_exports") and dict(getattr(self.m, "_debug_exports")) != dict(snap.get("debug_exports", {})):  # noqa: SLF001
            changed.append("_debug_exports")
        return changed

    def _eval_template_call(self, fn: Any, *, args: list[Any], kwargs: dict[str, Any]) -> Any:
        fn_name = getattr(fn, "__name__", repr(fn))
        try:
            sig = inspect.signature(fn)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
        except TypeError as e:
            raise JitError(f"@const call failed for {fn_name!r}: {e}") from e

        ps = list(sig.parameters.values())
        if not ps:
            raise JitError(f"@const {fn_name!r} must take at least one argument (the current Circuit builder)")

        builder_arg = ps[0].name
        if bound.arguments.get(builder_arg) is not self.m:
            raise JitError(f"@const {fn_name!r} must be called with the current Circuit as first argument")

        args_key = tuple(_template_identity_snapshot(a) for a in args)
        kwargs_key = tuple((str(k), _template_identity_snapshot(v)) for k, v in sorted(kwargs.items()))
        cache_key: _TemplateKey = (id(fn), ("args", args_key), ("kwargs", kwargs_key))
        if cache_key in self._template_cache:
            return copy.deepcopy(self._template_cache[cache_key])

        snap = self._snapshot_template_purity_state()
        call_err: Exception | None = None
        result: Any = None
        try:
            result = fn(*bound.args, **bound.kwargs)
        except Exception as e:  # noqa: BLE001
            call_err = e

        muts = self._template_purity_mutations(snap)
        if muts:
            self._restore_template_purity_state(snap)
            details = ", ".join(muts)
            raise JitError(
                f"@const {fn_name!r} must be compile-time pure and emit no IR; mutated module state: {details}"
            )
        if call_err is not None:
            raise JitError(f"@const call failed for {fn_name!r}: {call_err}") from call_err

        _validate_template_return(result)
        self._template_cache[cache_key] = copy.deepcopy(result)
        return result

    # ---- statement compilation ----
    def compile_block(self, stmts: list[ast.stmt]) -> None:
        for s in stmts:
            try:
                self.compile_stmt(s)
            except _InlineReturn:
                raise
            except Exception as e:  # noqa: BLE001
                raise self._error_with_node(s, e) from e

    def compile_stmt(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Pass):
            return
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # Ignore docstrings.
            return
        if isinstance(node, ast.Expr):
            _ = self.eval_expr(node.value)
            return
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                raise JitError("multiple assignment targets are not supported")
            v = self.eval_expr(node.value)
            self._assign_wire_target(node.targets[0], v, node=node)
            return
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or node.value is None:
                raise JitError("only simple annotated assignments are supported")
            name = node.target.id
            v = self.eval_expr(node.value)
            self.env[name] = self._alias_if_wire(v, base_name=name, node=node)
            return
        if isinstance(node, ast.AugAssign):
            if not isinstance(node.target, ast.Name):
                raise JitError("only simple augmented assignments are supported")
            name = node.target.id
            cur = self.env.get(name)
            if cur is None:
                raise JitError(f"augassign to unknown name {name!r}")
            rhs = self.eval_expr(node.value)
            if isinstance(node.op, ast.Add):
                self.env[name] = self._alias_if_wire(_expect_wire(cur, ctx="+=") + rhs, base_name=name, node=node)
                return
            if isinstance(node.op, ast.BitAnd):
                self.env[name] = self._alias_if_wire(_expect_wire(cur, ctx="&=") & rhs, base_name=name, node=node)
                return
            if isinstance(node.op, ast.BitOr):
                self.env[name] = self._alias_if_wire(_expect_wire(cur, ctx="|=") | rhs, base_name=name, node=node)
                return
            if isinstance(node.op, ast.BitXor):
                self.env[name] = self._alias_if_wire(_expect_wire(cur, ctx="^=") ^ rhs, base_name=name, node=node)
                return
            if isinstance(node.op, ast.LShift):
                if not isinstance(cur, Reg):
                    raise JitError("<<= is only supported for Reg variables (use x = x.shl(amount=...) for wires)")
                cur <<= rhs
                self.env[name] = cur
                return
            raise JitError("unsupported augmented assignment operator")
        if isinstance(node, ast.Assert):
            test_v = self.eval_expr(node.test)
            msg: str | None = None
            if node.msg is not None:
                mv = self.eval_expr(node.msg)
                if not isinstance(mv, str):
                    raise JitError("assert message must be a constant string")
                msg = mv

            if isinstance(test_v, (bool, int)):
                if not bool(test_v):
                    raise JitError(f"compile-time assert failed{': ' + msg if msg else ''}")
                return

            w = _expect_wire(test_v, ctx="assert")
            if w.ty != "i1":
                raise JitError("assert condition must be an i1 Wire")
            self.m.assert_(w, msg=msg)
            return
        if isinstance(node, ast.If):
            self.compile_if(node)
            return
        if _HAS_AST_MATCH and isinstance(node, ast.Match):
            self.compile_match(node)
            return
        if isinstance(node, ast.For):
            self.compile_for(node)
            return
        if isinstance(node, ast.With):
            self.compile_with(node)
            return
        if isinstance(node, ast.Return):
            # Inline helpers may return from nested control-flow blocks.
            if self._inline_stack:
                v = None if node.value is None else self.eval_expr(node.value)
                raise _InlineReturn(v)
            # Return is handled by the top-level driver; disallow in nested blocks.
            raise JitError("return is only supported at top-level (use m.output instead inside control flow)")

        raise JitError(f"unsupported statement: {ast.dump(node, include_attributes=False)}")

    def compile_with(self, node: ast.With) -> None:
        # Prototype support for naming scopes / context managers.
        if len(node.items) != 1:
            raise JitError("with supports exactly one context manager (prototype)")
        item = node.items[0]
        if item.optional_vars is not None:
            raise JitError("with-as is not supported (prototype)")

        cm = self.eval_expr(item.context_expr)
        enter = getattr(cm, "__enter__", None)
        exit_ = getattr(cm, "__exit__", None)
        if enter is None or exit_ is None:
            raise JitError("with context is not a context manager")

        enter()
        try:
            self.compile_block(node.body)
        finally:
            exit_(None, None, None)

    def compile_if(self, node: ast.If) -> None:
        cond_v = self.eval_expr(node.test)
        if not isinstance(cond_v, Wire) and isinstance(cond_v, (bool, int)):
            if bool(cond_v):
                self.compile_block(node.body)
            else:
                self.compile_block(node.orelse)
            return

        cond = _expect_wire(cond_v, ctx="if condition")
        if cond.ty != "i1":
            raise JitError("if condition must be an i1 wire or a python bool")

        pre_env = dict(self.env)
        assigned = sorted(_assigned_names(node.body) | _assigned_names(node.orelse))
        if not assigned:
            raise JitError("if does not assign any variables under a dynamic condition (use a compile-time condition instead)")

        def capture(fn: Any) -> list[str]:
            # Avoid slicing/copying the shared line buffer in hot dynamic-if paths.
            saved_lines = self.m._lines  # noqa: SLF001
            local_lines: list[str] = []
            self.m._lines = local_lines  # noqa: SLF001
            try:
                fn()
            finally:
                self.m._lines = saved_lines  # noqa: SLF001
            return local_lines

        def value_ty(v: Any) -> str | None:
            if isinstance(v, Reg):
                v = v.q
            if isinstance(v, Wire):
                return v.ty
            if isinstance(v, Signal):
                return v.ty
            if isinstance(v, LiteralValue) and v.width is not None:
                return f"i{int(v.width)}"
            return None

        def int_width(v: int) -> int:
            if v < 0:
                raise JitError(
                    "cannot infer width for negative integer constant in dynamic if; use s(width, value)"
                )
            return max(1, int(v).bit_length())

        # Compile branches first (captured), then infer phi types from their final values.
        then_comp = _Compiler(
            self.m,
            {},
            globals_=self.globals,
            source_file=self.source_file,
            source_text=self.source_text,
            source_stem=self.source_stem,
            line_offset=self.line_offset,
            value_param_names=set(self._value_param_names),
            value_param_types=dict(self._value_param_types),
        )
        then_comp._inline_stack = list(self._inline_stack)
        then_comp.env = dict(pre_env)

        def compile_then_body() -> None:
            self.m.push_indent()
            try:
                then_comp.compile_block(node.body)
            finally:
                self.m.pop_indent()

        then_body_lines = capture(compile_then_body)

        else_comp = _Compiler(
            self.m,
            {},
            globals_=self.globals,
            source_file=self.source_file,
            source_text=self.source_text,
            source_stem=self.source_stem,
            line_offset=self.line_offset,
            value_param_names=set(self._value_param_names),
            value_param_types=dict(self._value_param_types),
        )
        else_comp._inline_stack = list(self._inline_stack)
        else_comp.env = dict(pre_env)

        def compile_else_body() -> None:
            self.m.push_indent()
            try:
                else_comp.compile_block(node.orelse)
            finally:
                self.m.pop_indent()

        else_body_lines = capture(compile_else_body)

        phi_vars = list(assigned)

        expected_types: list[str] = []
        for name in phi_vars:
            pre_v = pre_env.get(name)
            if isinstance(pre_v, Wire):
                expected_types.append(pre_v.ty)
                continue
            if isinstance(pre_v, Signal):
                expected_types.append(pre_v.ty)
                continue
            if pre_v is not None and not isinstance(pre_v, (int, bool)):
                raise JitError(
                    f"if assigns {name!r} under a dynamic condition, but it is not a Wire/int/bool "
                    "(define it as a Wire before the if, or make the condition compile-time)"
                )

            then_v = then_comp.env.get(name)
            else_v = else_comp.env.get(name)
            then_ty = value_ty(then_v)
            else_ty = value_ty(else_v)

            if then_ty is not None and else_ty is not None:
                if then_ty == else_ty:
                    expected_types.append(then_ty)
                    continue
                if then_ty.startswith("i") and else_ty.startswith("i"):
                    expected_types.append(f"i{max(self._ty_width(then_ty), self._ty_width(else_ty))}")
                    continue
                raise JitError(f"if assigns {name!r} with incompatible types: {then_ty} vs {else_ty}")

            if then_ty is not None:
                expected_types.append(then_ty)
                continue
            if else_ty is not None:
                expected_types.append(else_ty)
                continue

            # Fall back to constant inference (missing => 0).
            tv = then_v if isinstance(then_v, (int, bool, LiteralValue)) else 0
            ev = else_v if isinstance(else_v, (int, bool, LiteralValue)) else 0
            tv_i = int(tv.value) if isinstance(tv, LiteralValue) else int(tv)
            ev_i = int(ev.value) if isinstance(ev, LiteralValue) else int(ev)
            w = max(int_width(tv_i), int_width(ev_i))
            expected_types.append(f"i{w}")

        results: list[str] = []
        if phi_vars:
            results = [self.m._tmp() for _ in phi_vars]  # noqa: SLF001

        _emit_scf_if_header(self.m, results, cond, expected_types)

        # then (captured body + captured yield epilogue)
        def emit_then_yield() -> None:
            self.m.push_indent()
            try:
                then_vals: list[Wire] = []
                for name, ty in zip(phi_vars, expected_types):
                    v = then_comp.env.get(name, 0)
                    then_vals.append(then_comp._coerce_to_type(v, expected_ty=ty, ctx="if then"))
                _emit_scf_yield(self.m, then_vals)
            finally:
                self.m.pop_indent()

        self.m._lines.extend(then_body_lines)  # noqa: SLF001
        self.m._lines.extend(capture(emit_then_yield))  # noqa: SLF001

        # else
        self.m.emit_line("} else {")

        def emit_else_yield() -> None:
            self.m.push_indent()
            try:
                else_vals: list[Wire] = []
                for name, ty in zip(phi_vars, expected_types):
                    v = else_comp.env.get(name, 0)
                    else_vals.append(else_comp._coerce_to_type(v, expected_ty=ty, ctx="if else"))
                _emit_scf_yield(self.m, else_vals)
            finally:
                self.m.pop_indent()

        self.m._lines.extend(else_body_lines)  # noqa: SLF001
        self.m._lines.extend(capture(emit_else_yield))  # noqa: SLF001
        self.m.emit_line("}")

        # Merge results back into env (including newly introduced names).
        for name, res_ref, ty in zip(phi_vars, results, expected_types):
            self.env[name] = self._alias_if_wire(Wire(self.m, Signal(ref=res_ref, ty=ty)), base_name=name, node=node)

    def compile_match(self, node: ast.Match) -> None:
        subject_v = self.eval_expr(node.subject)
        subj_name = f"__match_subject_{len(self._inline_stack)}_{len(self._callsite_counts)}"
        while subj_name in self.env:
            subj_name = f"{subj_name}_n"
        self.env[subj_name] = subject_v

        branches: list[tuple[ast.expr, list[ast.stmt]]] = []
        default_body: list[ast.stmt] | None = None
        for case in node.cases:
            if case.guard is not None:
                raise JitError("match guards are not supported yet")

            pat = case.pattern
            if isinstance(pat, ast.MatchAs) and pat.pattern is None and pat.name is None:
                default_body = list(case.body)
                break

            if isinstance(pat, ast.MatchValue):
                cmp = ast.Compare(
                    left=ast.Name(id=subj_name, ctx=ast.Load()),
                    ops=[ast.Eq()],
                    comparators=[copy.deepcopy(pat.value)],
                )
                branches.append((cmp, list(case.body)))
                continue

            if isinstance(pat, ast.MatchSingleton):
                cmp = ast.Compare(
                    left=ast.Name(id=subj_name, ctx=ast.Load()),
                    ops=[ast.Eq()],
                    comparators=[ast.Constant(value=pat.value)],
                )
                branches.append((cmp, list(case.body)))
                continue

            raise JitError("unsupported match pattern (only literal values and wildcard `_` are supported)")

        if default_body is None:
            default_body = []

        if not branches:
            self.compile_block(default_body)
            self.env.pop(subj_name, None)
            return

        orelse = default_body
        for test, body in reversed(branches):
            orelse = [ast.If(test=test, body=body, orelse=orelse)]

        self.compile_block(orelse)
        self.env.pop(subj_name, None)

    def compile_for(self, node: ast.For) -> None:
        def target_names(t: ast.AST) -> list[str]:
            if isinstance(t, ast.Name):
                return [t.id]
            if isinstance(t, (ast.Tuple, ast.List)):
                out: list[str] = []
                for e in t.elts:
                    out.extend(target_names(e))
                return out
            raise JitError("for-loop target must be a name or tuple/list of names")

        def bind_target(t: ast.AST, v: Any) -> None:
            if isinstance(t, ast.Name):
                self.env[t.id] = v
                return
            if isinstance(t, (ast.Tuple, ast.List)):
                if not isinstance(v, (tuple, list)):
                    raise JitError("tuple/list for-loop target requires tuple/list values")
                if len(v) != len(t.elts):
                    raise JitError("for-loop unpacking arity mismatch")
                for sub_t, sub_v in zip(t.elts, v):
                    bind_target(sub_t, sub_v)
                return
            raise JitError("for-loop target must be a name or tuple/list of names")

        if isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name) and node.iter.func.id == "range":
            args = node.iter.args
            if not (1 <= len(args) <= 3):
                raise JitError("range() must have 1..3 arguments")

            if len(args) == 1:
                lb_i = 0
                ub_i = self.eval_const(args[0])
                step_i = 1
            elif len(args) == 2:
                lb_i = self.eval_const(args[0])
                ub_i = self.eval_const(args[1])
                step_i = 1
            else:
                lb_i = self.eval_const(args[0])
                ub_i = self.eval_const(args[1])
                step_i = self.eval_const(args[2])
            if step_i <= 0:
                raise JitError("range() step must be > 0")
            iter_vals = list(range(lb_i, ub_i, step_i))
        else:
            iter_raw = self.eval_expr(node.iter)
            try:
                iter_vals = list(iter_raw)
            except TypeError as e:
                raise JitError(f"for-loop iterator is not statically iterable: {type(iter_raw).__name__}") from e

        names = target_names(node.target)
        saved = {n: self.env[n] for n in names if n in self.env}
        had = set(saved.keys())

        for v in iter_vals:
            bind_target(node.target, v)
            self.compile_block(node.body)

        for n in names:
            if n in had:
                self.env[n] = saved[n]
            else:
                self.env.pop(n, None)


def compile_module(
    fn: Any,
    *,
    module_name: str | None = None,
    name: str | None = None,
    design_ctx: Any | None = None,
    port_specs: Mapping[str, Any] | None = None,
    value_params: Mapping[str, str] | None = None,
    **params: Any,
) -> Circuit:
    """Compile one hardware function into a static pyCircuit Module.

    The function is *not executed*; it is parsed via `ast` and lowered into
    MLIR SCF + PYC ops, then `pycc` will lower SCF into static muxes and
    unrolled logic.

    Restrictions (prototype):
    - `if` conditions: python bool or `i1` Wire
    - `for` loops: `for ... in range(const)` only, step must be > 0
    - Loop induction variable is currently not usable in expressions.
    """

    try:
        fn_name = getattr(fn, "__name__", None)
        if not isinstance(fn_name, str) or not fn_name:
            raise JitError(f"invalid JIT function target: {fn!r}")
        meta = get_function_meta(fn, fn_name=fn_name)
    except OSError as e:
        raise JitError(f"cannot compile {getattr(fn, '__name__', fn)!r}: failed to read source ({e})") from e
    except RuntimeError as e:
        raise JitError(f"cannot compile {getattr(fn, '__name__', fn)!r}: {e}") from e

    fdef = meta.fdef
    if _call_kind(fn) == "template":
        raise JitError(f"cannot compile @const function {getattr(fn, '__name__', fn)!r} as a hardware module")

    sig = meta.signature
    ps = list(sig.parameters.values())
    if not ps:
        raise JitError("function must take at least one argument (the Circuit builder)")
    for p in ps[1:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise JitError("varargs are not supported in JIT-compiled module functions")

    builder_arg = ps[0].name
    port_specs_dict = dict(port_specs or {})
    declared_value_params = _value_params_of(fn)
    if value_params is not None:
        explicit_vp = {str(k): _normalize_value_param_ty(str(v)) for k, v in value_params.items()}
        if declared_value_params and explicit_vp != declared_value_params:
            raise JitError(
                f"value_params mismatch for {getattr(fn, '__name__', fn)!r}: "
                "decorator declaration differs from compile-time declaration"
            )
        declared_value_params = explicit_vp
    value_param_names = set(declared_value_params.keys())

    param_names = {p.name for p in ps[1:]}
    unknown_ports = set(port_specs_dict.keys()) - param_names
    if unknown_ports:
        raise JitError(
            f"unknown signature-bound port(s) for {getattr(fn, '__name__', fn)!r}: {', '.join(sorted(unknown_ports))}"
        )
    unknown_value_params = value_param_names - param_names
    if unknown_value_params:
        raise JitError(
            f"unknown value-param declaration(s) for {getattr(fn, '__name__', fn)!r}: "
            + ", ".join(sorted(unknown_value_params))
        )
    overlap_port_value = set(port_specs_dict.keys()) & value_param_names
    if overlap_port_value:
        raise JitError(
            "value-param(s) must not be provided as signature-bound port specs: "
            + ", ".join(sorted(overlap_port_value))
        )
    overlap_param_value = set(params.keys()) & value_param_names
    if overlap_param_value:
        raise JitError(
            "value-param(s) must be connected as module input ports, not specialization params: "
            + ", ".join(sorted(overlap_param_value))
        )

    extra_params = set(params.keys()) - {
        p.name for p in ps[1:] if p.name not in port_specs_dict and p.name not in value_param_names
    }
    if extra_params:
        raise JitError(f"unknown JIT param(s) for {getattr(fn, '__name__', fn)!r}: {', '.join(sorted(extra_params))}")

    bound_params: dict[str, Any] = {}
    for p in ps[1:]:
        if p.name in port_specs_dict or p.name in value_param_names:
            continue
        if p.name in params:
            bound_params[p.name] = params[p.name]
        elif p.default is not inspect._empty:
            bound_params[p.name] = p.default
        else:
            raise JitError(f"missing JIT param {p.name!r}")

    if module_name is None:
        module_name = name
    m = Circuit(module_name or fn.__name__, design_ctx=design_ctx)
    c = _Compiler(
        m,
        params=dict(bound_params),
        globals_=fn.__globals__,
        source_file=meta.source_file,
        source_text=meta.source,
        source_stem=meta.source_stem,
        line_offset=int(meta.start_line - 1),
        value_param_names=set(value_param_names),
        value_param_types=dict(declared_value_params),
    )
    c.env[builder_arg] = m
    for p in ps[1:]:
        if p.name in declared_value_params:
            decl_ty = declared_value_params[p.name]
            if decl_ty == "!pyc.clock":
                c.env[p.name] = m.clock(p.name)
                continue
            if decl_ty == "!pyc.reset":
                c.env[p.name] = m.reset(p.name)
                continue
            if decl_ty.startswith("i"):
                width = int(decl_ty[1:])
                c.env[p.name] = m.input(p.name, width=width, signed=False)
                continue
            raise JitError(f"unsupported value-param type for {p.name!r}: {decl_ty!r}")

        if p.name not in port_specs_dict:
            continue
        spec = port_specs_dict[p.name]
        if not isinstance(spec, dict):
            raise JitError(f"invalid signature-bound port spec for {p.name!r}: expected dict")
        kind = str(spec.get("kind", "")).strip()
        if kind == "clock":
            c.env[p.name] = m.clock(p.name)
            continue
        if kind == "reset":
            c.env[p.name] = m.reset(p.name)
            continue
        if kind == "wire":
            ty = str(spec.get("ty", "")).strip()
            if not ty.startswith("i"):
                raise JitError(f"invalid integer type in signature-bound port {p.name!r}: {ty!r}")
            try:
                width = int(ty[1:])
            except ValueError as e:
                raise JitError(f"invalid integer width in signature-bound port {p.name!r}: {ty!r}") from e
            if width <= 0:
                raise JitError(f"invalid integer width in signature-bound port {p.name!r}: {ty!r}")
            signed = bool(spec.get("signed", False))
            c.env[p.name] = m.input(p.name, width=width, signed=signed)
            continue
        raise JitError(f"unsupported signature-bound port kind for {p.name!r}: {kind!r}")

    returned: list[Any] = []
    for stmt in fdef.body:
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                break
            try:
                v = c.eval_expr(stmt.value)
            except Exception as e:  # noqa: BLE001
                raise c._error_with_node(stmt, e, code="PYC520") from e
            if isinstance(v, tuple):
                returned.extend(v)
            else:
                returned.append(v)
            break
        try:
            c.compile_stmt(stmt)
        except Exception as e:  # noqa: BLE001
            raise c._error_with_node(stmt, e, code="PYC520") from e

    if returned and getattr(m, "_results", []):  # noqa: SLF001
        raise JitError("cannot mix `return` and explicit `m.output(...)`")
    if returned:
        def as_out(v: Any) -> Any:
            if isinstance(v, Reg):
                return v.q
            return v

        if len(returned) == 1:
            v0 = as_out(returned[0])
            if is_connector_bundle(v0):
                for out_name, out_v in v0.items():
                    m.output(str(out_name), out_v)
            else:
                m.output("out", v0)
        else:
            for i, v in enumerate(returned):
                m.output(f"out{i}", as_out(v))

    return m




def compile(top_fn: Any, *, name: str | None = None, **top_params: Any):
    """Compile a multi-module Design rooted at `top_fn`.

    The returned Design contains multiple `func.func`s and preserves hierarchy
    via `pyc.instance` ops emitted by `Circuit.instance(...)`.
    """

    from .design import Design, DesignContext

    top_kind = _call_kind(top_fn)
    if top_kind != "module":
        raise JitError(
            f"top entrypoint {getattr(top_fn, '__name__', top_fn)!r} must be decorated with @module"
        )

    sym = name
    if sym is None:
        override = getattr(top_fn, "__pycircuit_name__", None)
        if isinstance(override, str) and override.strip():
            sym = override.strip()
        else:
            sym = getattr(top_fn, "__name__", "Top")

    design = Design(top=str(sym))
    ctx = DesignContext(design)
    # Compile the top as an explicit symbol (no hash suffix).
    ctx.specialize(top_fn, params=dict(top_params), module_name=str(sym))
    return design
