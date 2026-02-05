from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dsl import Signal
from .hw import Bundle, Circuit, Reg, Vec, Wire


class JitError(RuntimeError):
    pass


def jit_inline(fn: Any) -> Any:
    """Mark a Python helper to be inlined by the AST/SCF JIT compiler.

    When a `@jit_inline` function is called from inside a `jit_compile`'d
    function, the callee is parsed via `ast` and its body is compiled into the
    *current* Circuit, instead of being executed as Python at JIT time.

    This enables:
    - modular designs split across files (stages/modules),
    - consistent name-mangling with file/line provenance,
    - future inter-procedural transformations.
    """

    setattr(fn, "__pycircuit_inline__", True)
    return fn


@dataclass(frozen=True)
class _IndexValue:
    """Placeholder for an SCF induction variable (index-typed SSA value)."""

    ref: str

    def __str__(self) -> str:
        return self.ref


def _find_function_def(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise JitError(f"failed to find function definition for {name!r}")


def _assigned_names(stmts: list[ast.stmt]) -> set[str]:
    out: set[str] = set()

    class V(ast.NodeVisitor):
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

    V().visit(ast.Module(body=stmts, type_ignores=[]))
    return out


def _expect_wire(v: Any, *, ctx: str) -> Wire:
    if isinstance(v, Wire):
        return v
    if isinstance(v, Reg):
        return v.q
    raise JitError(f"{ctx}: expected a Wire/Reg, got {type(v).__name__}")


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
        source_stem: str | None = None,
        line_offset: int = 0,
    ) -> None:
        self.m = m
        self.env: dict[str, Any] = dict(params)
        self.globals = globals_
        self.source_stem = source_stem
        self.line_offset = int(line_offset)
        self._inline_stack: list[Any] = []

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
        if isinstance(v, Reg):
            v = v.q
        if isinstance(v, Wire):
            w = v
        elif isinstance(v, Signal):
            w = Wire(self.m, v)
        elif isinstance(v, bool):
            w = self.m.const_wire(int(v), width=self._ty_width(expected_ty))
        elif isinstance(v, int):
            w = self.m.const_wire(int(v), width=self._ty_width(expected_ty))
        else:
            raise JitError(f"{ctx}: expected Wire/Reg/Signal/int, got {type(v).__name__}")

        if w.ty == expected_ty:
            return w

        if w.ty.startswith("i") and expected_ty.startswith("i"):
            ew = self._ty_width(expected_ty)
            if w.width < ew:
                return w.sext(width=ew) if w.signed else w.zext(width=ew)
            if w.width > ew:
                return w.trunc(width=ew)
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

    def _name_with_loc(self, name: str, node: ast.AST) -> str:
        line = self._abs_lineno(node)
        if line is None:
            return name
        if self.source_stem:
            return f"{name}__{self.source_stem}__L{line}"
        return f"{name}__L{line}"

    def _alias_if_wire(self, v: Any, *, base_name: str, node: ast.AST) -> Any:
        n = self._scoped_name(self._name_with_loc(base_name, node))
        if isinstance(v, Wire):
            return Wire(self.m, self.m.alias(v.sig, name=n), signed=v.signed)
        if isinstance(v, Reg):
            q_named = Wire(self.m, self.m.alias(v.q.sig, name=n), signed=v.q.signed)
            return Reg(q=q_named, clk=v.clk, rst=v.rst, en=v.en, next=v.next, init=v.init)
        return v

    # ---- constant evaluation (for range bounds, widths, etc) ----
    def eval_const(self, node: ast.AST) -> int:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return int(node.value)
            if isinstance(node.value, int):
                return int(node.value)
            raise JitError(f"unsupported constant in const-eval: {node.value!r}")
        if isinstance(node, ast.Name):
            v = self.env.get(node.id, self.globals.get(node.id))
            if isinstance(v, bool):
                return int(v)
            if isinstance(v, int):
                return int(v)
            raise JitError(f"const-eval name {node.id!r} is not an int/bool")
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
        raise JitError(f"unsupported const-eval expression: {ast.dump(node, include_attributes=False)}")

    # ---- expression evaluation (hardware + params) ----
    def eval_expr(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.List):
            elts = [self.eval_expr(e) for e in node.elts]
            if elts and all(isinstance(e, (Wire, Reg)) for e in elts):
                return Vec(tuple(elts))
            return elts
        if isinstance(node, ast.Tuple):
            elts = [self.eval_expr(e) for e in node.elts]
            if elts and all(isinstance(e, (Wire, Reg)) for e in elts):
                return Vec(tuple(elts))
            return tuple(elts)
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
                if isinstance(sl, ast.Constant) and isinstance(sl.value, int):
                    return base[int(sl.value)]
                if isinstance(sl, ast.Slice):
                    if sl.step is not None:
                        raise JitError("Vec slicing does not support step (prototype)")
                    lo = None
                    if sl.lower is not None:
                        if not isinstance(sl.lower, ast.Constant) or not isinstance(sl.lower.value, int):
                            raise JitError("Vec slice lower bound must be a constant integer")
                        lo = int(sl.lower.value)
                    hi = None
                    if sl.upper is not None:
                        if not isinstance(sl.upper, ast.Constant) or not isinstance(sl.upper.value, int):
                            raise JitError("Vec slice upper bound must be a constant integer")
                        hi = int(sl.upper.value)
                    return base[slice(lo, hi, None)]
                raise JitError("Vec subscript must be a constant integer or slice")
            if isinstance(base, Bundle):
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    return base[str(sl.value)]
                raise JitError("Bundle subscript must be a constant string key")
            if isinstance(base, (Wire, Reg)):
                if isinstance(sl, ast.Slice):
                    if sl.step is not None:
                        raise JitError("wire slicing does not support step")
                    lo = None if sl.lower is None else self.eval_const(sl.lower)
                    hi = None if sl.upper is None else self.eval_const(sl.upper)
                    return _expect_wire(base, ctx="wire slice")[slice(lo, hi, None)]
                bit = int(self.eval_const(sl))
                return _expect_wire(base, ctx="wire subscript")[bit]
            if isinstance(base, (list, tuple)):
                if isinstance(sl, ast.Constant) and isinstance(sl.value, int):
                    return base[int(sl.value)]
                raise JitError("list/tuple subscript must be a constant integer")
            raise JitError(f"unsupported subscript base type: {type(base).__name__}")
        if isinstance(node, ast.BinOp):
            lhs = self.eval_expr(node.left)
            rhs = self.eval_expr(node.right)
            if isinstance(node.op, ast.Add):
                if isinstance(lhs, (Wire, Reg)):
                    return lhs + rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs + lhs
                return int(lhs) + int(rhs)
            if isinstance(node.op, ast.Sub):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="-") - rhs
                if isinstance(rhs, (Wire, Reg)):
                    return int(lhs) - _expect_wire(rhs, ctx="-")
                return int(lhs) - int(rhs)
            if isinstance(node.op, ast.Mult):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="*") * rhs
                if isinstance(rhs, (Wire, Reg)):
                    return _expect_wire(rhs, ctx="*") * lhs
                return int(lhs) * int(rhs)
            if isinstance(node.op, ast.FloorDiv) or isinstance(node.op, ast.Div):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="/") // rhs
                if isinstance(rhs, (Wire, Reg)):
                    w = _expect_wire(rhs, ctx="/")
                    lhs_w = w._as_wire(int(lhs), width=w.width)
                    return lhs_w // w
                return int(lhs) // int(rhs)
            if isinstance(node.op, ast.Mod):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="%") % rhs
                if isinstance(rhs, (Wire, Reg)):
                    w = _expect_wire(rhs, ctx="%")
                    lhs_w = w._as_wire(int(lhs), width=w.width)
                    return lhs_w % w
                return int(lhs) % int(rhs)
            if isinstance(node.op, ast.BitAnd):
                if isinstance(lhs, (Wire, Reg)):
                    return lhs & rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs & lhs
                return int(lhs) & int(rhs)
            if isinstance(node.op, ast.BitOr):
                if isinstance(lhs, (Wire, Reg)):
                    return lhs | rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs | lhs
                return int(lhs) | int(rhs)
            if isinstance(node.op, ast.BitXor):
                if isinstance(lhs, (Wire, Reg)):
                    return lhs ^ rhs
                if isinstance(rhs, (Wire, Reg)):
                    return rhs ^ lhs
                return int(lhs) ^ int(rhs)
            if isinstance(node.op, ast.LShift):
                w = _expect_wire(lhs, ctx="<<")
                amt = rhs
                if not isinstance(amt, int):
                    raise JitError("<< only supports constant shift amounts")
                return w.shl(amount=int(amt))
            if isinstance(node.op, ast.RShift):
                w = _expect_wire(lhs, ctx=">>")
                amt = rhs
                if not isinstance(amt, int):
                    raise JitError(">> only supports constant shift amounts")
                return w >> int(amt)
        if isinstance(node, ast.UnaryOp):
            v = self.eval_expr(node.operand)
            if isinstance(node.op, ast.Invert):
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
                for nxt in node.values[1:]:
                    b = self.eval_expr(nxt)
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
                for nxt in node.values[1:]:
                    b = self.eval_expr(nxt)
                    if isinstance(out, (Wire, Reg)) or isinstance(b, (Wire, Reg)):
                        if isinstance(out, (Wire, Reg)):
                            out = _expect_wire(out, ctx="or") | b
                        else:
                            out = _expect_wire(b, ctx="or") | out
                    else:
                        out = bool(out) or bool(b)
                return out
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise JitError("only single comparisons are supported")
            lhs = self.eval_expr(node.left)
            rhs = self.eval_expr(node.comparators[0])
            op = node.ops[0]
            if isinstance(op, ast.Eq):
                w = _expect_wire(lhs, ctx="==") if isinstance(lhs, (Wire, Reg)) else _expect_wire(rhs, ctx="==")
                return w.eq(rhs if isinstance(lhs, (Wire, Reg)) else lhs)
            if isinstance(op, ast.NotEq):
                w = _expect_wire(lhs, ctx="!=") if isinstance(lhs, (Wire, Reg)) else _expect_wire(rhs, ctx="!=")
                eq = w.eq(rhs if isinstance(lhs, (Wire, Reg)) else lhs)
                return ~eq
            if isinstance(op, ast.Lt):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="<").lt(rhs)
                if isinstance(rhs, (Wire, Reg)):
                    # a < b  ==>  b > a
                    return _expect_wire(rhs, ctx="<").gt(lhs)
                return int(lhs) < int(rhs)
            if isinstance(op, ast.LtE):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx="<=").le(rhs)
                if isinstance(rhs, (Wire, Reg)):
                    return _expect_wire(rhs, ctx="<=").ge(lhs)
                return int(lhs) <= int(rhs)
            if isinstance(op, ast.Gt):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx=">").gt(rhs)
                if isinstance(rhs, (Wire, Reg)):
                    # a > b  ==>  b < a
                    return _expect_wire(rhs, ctx=">").lt(lhs)
                return int(lhs) > int(rhs)
            if isinstance(op, ast.GtE):
                if isinstance(lhs, (Wire, Reg)):
                    return _expect_wire(lhs, ctx=">=").ge(rhs)
                if isinstance(rhs, (Wire, Reg)):
                    return _expect_wire(rhs, ctx=">=").le(lhs)
                return int(lhs) >= int(rhs)
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
        if isinstance(node.func, ast.Name) and node.func.id == "range":
            raise JitError("range() is only supported in for-loops")

        if isinstance(node.func, ast.Attribute):
            recv = self.eval_expr(node.func.value)
            fn = getattr(recv, node.func.attr)
        elif isinstance(node.func, ast.Name):
            fn = self.env.get(node.func.id, self.globals.get(node.func.id))
            if fn is None:
                raise JitError(f"unknown function {node.func.id!r}")
        else:
            raise JitError("unsupported call target")

        args = [self.eval_expr(a) for a in node.args]
        kwargs = {kw.arg: self.eval_expr(kw.value) for kw in node.keywords if kw.arg is not None}
        try:
            if getattr(fn, "__pycircuit_inline__", False):
                return self._eval_inline_call(fn, args=args, kwargs=kwargs)
            return fn(*args, **kwargs)
        except TypeError as e:
            raise JitError(f"call failed: {e}") from e

    def _eval_inline_call(self, fn: Any, *, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if fn in self._inline_stack:
            raise JitError(f"recursive @jit_inline call is not supported: {getattr(fn, '__name__', fn)!r}")

        try:
            lines, start_line = inspect.getsourcelines(fn)
        except OSError as e:
            raise JitError(f"cannot inline {getattr(fn, '__name__', fn)!r}: failed to read source ({e})") from e

        src = textwrap.dedent("".join(lines))
        tree = ast.parse(src)
        fdef = _find_function_def(tree, fn.__name__)

        if not fdef.args.args:
            raise JitError("@jit_inline function must take at least one argument (the Circuit builder)")
        builder_arg = fdef.args.args[0].arg

        # Use Python's own binding semantics for args/kwargs/defaults.
        try:
            bound = inspect.signature(fn).bind(*args, **kwargs)
        except TypeError as e:
            raise JitError(f"inline call failed: {e}") from e
        bound.apply_defaults()

        if builder_arg not in bound.arguments:
            raise JitError("internal: failed to bind builder argument for @jit_inline call")
        if bound.arguments[builder_arg] is not self.m:
            raise JitError("@jit_inline function must be called with the current Circuit builder")

        src_file = inspect.getsourcefile(fn) or inspect.getfile(fn)
        src_stem = None
        try:
            if src_file:
                src_stem = Path(src_file).stem
        except Exception:
            src_stem = None

        child = _Compiler(
            self.m,
            params=dict(bound.arguments),
            globals_=dict(getattr(fn, "__globals__", {})),
            source_stem=src_stem,
            line_offset=int(start_line - 1),
        )
        child._inline_stack = [*self._inline_stack, fn]

        for stmt in fdef.body:
            if isinstance(stmt, ast.Return):
                if stmt.value is None:
                    return None
                return child.eval_expr(stmt.value)
            child.compile_stmt(stmt)

        # No explicit return => None.
        return None

    # ---- statement compilation ----
    def compile_block(self, stmts: list[ast.stmt]) -> None:
        for s in stmts:
            self.compile_stmt(s)

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
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise JitError("only simple assignments to a single name are supported")
            name = node.targets[0].id
            v = self.eval_expr(node.value)
            if isinstance(v, _IndexValue):
                raise JitError("cannot assign index values into hardware variables")
            self.env[name] = self._alias_if_wire(v, base_name=name, node=node)
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
        if isinstance(node, ast.If):
            self.compile_if(node)
            return
        if isinstance(node, ast.For):
            self.compile_for(node)
            return
        if isinstance(node, ast.With):
            self.compile_with(node)
            return
        if isinstance(node, ast.Return):
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
            start = len(self.m._lines)  # noqa: SLF001
            fn()
            lines = self.m._lines[start:]  # noqa: SLF001
            del self.m._lines[start:]  # noqa: SLF001
            return lines

        def value_ty(v: Any) -> str | None:
            if isinstance(v, Reg):
                v = v.q
            if isinstance(v, Wire):
                return v.ty
            if isinstance(v, Signal):
                return v.ty
            return None

        def int_width(v: int) -> int:
            if v < 0:
                raise JitError("cannot infer width for negative integer constant in dynamic if; use m.const_wire(..., width=...)")
            return max(1, int(v).bit_length())

        # Compile branches first (captured), then infer phi types from their final values.
        then_comp = _Compiler(
            self.m,
            {},
            globals_=self.globals,
            source_stem=self.source_stem,
            line_offset=self.line_offset,
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
            source_stem=self.source_stem,
            line_offset=self.line_offset,
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
            tv = then_v if isinstance(then_v, (int, bool)) else 0
            ev = else_v if isinstance(else_v, (int, bool)) else 0
            w = max(int_width(int(tv)), int_width(int(ev)))
            expected_types.append(f"i{w}")

        results: list[str] = []
        if phi_vars:
            results = [self.m._tmp() for _ in phi_vars]  # noqa: SLF001

        _emit_scf_if_header(self.m, results, cond, expected_types)

        # then (captured body + captured yield epilogue)
        then_lines = list(then_body_lines)

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

        then_lines.extend(capture(emit_then_yield))
        self.m._lines.extend(then_lines)  # noqa: SLF001

        # else
        self.m.emit_line("} else {")
        else_lines = list(else_body_lines)

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

        else_lines.extend(capture(emit_else_yield))
        self.m._lines.extend(else_lines)  # noqa: SLF001
        self.m.emit_line("}")

        # Merge results back into env (including newly introduced names).
        for name, res_ref, ty in zip(phi_vars, results, expected_types):
            self.env[name] = self._alias_if_wire(Wire(self.m, Signal(ref=res_ref, ty=ty)), base_name=name, node=node)

    def compile_for(self, node: ast.For) -> None:
        if not isinstance(node.target, ast.Name):
            raise JitError("only `for name in range(...)` loops are supported")
        loop_var = node.target.id

        if not isinstance(node.iter, ast.Call) or not isinstance(node.iter.func, ast.Name) or node.iter.func.id != "range":
            raise JitError("only `for ... in range(...)` is supported")
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
            raise JitError("range() step must be > 0 (static hardware unrolling)")

        pre_env = dict(self.env)
        assigned = sorted(_assigned_names(node.body))
        assigned = [n for n in assigned if n != loop_var and n in pre_env]
        for name in assigned:
            if not isinstance(pre_env[name], Wire):
                raise JitError(f"loop assigns {name!r} under SCF, but it is not a Wire")

        if not assigned:
            raise JitError("for-loop must update at least one pre-existing Wire variable (iter_args)")

        # index constants for bounds/step
        lb = self.m.index_const(lb_i)
        ub = self.m.index_const(ub_i)
        step = self.m.index_const(step_i)

        iv_ref = self.m._tmp()  # noqa: SLF001
        iter_arg_refs = [self.m._tmp() for _ in assigned]  # noqa: SLF001
        iter_inits = [_expect_wire(pre_env[n], ctx="for init") for n in assigned]
        iter_args = list(zip(iter_arg_refs, iter_inits))
        result_types = [w.ty for w in iter_inits]
        result_refs = [self.m._tmp() for _ in assigned]  # noqa: SLF001

        _emit_scf_for_header(self.m, result_refs, iv_ref, lb.ref, ub.ref, step.ref, iter_args, result_types)

        self.m.push_indent()
        body_comp = _Compiler(
            self.m,
            {},
            globals_=self.globals,
            source_stem=self.source_stem,
            line_offset=self.line_offset,
        )
        body_comp.env = dict(pre_env)
        body_comp.env[loop_var] = _IndexValue(ref=iv_ref)
        for name, arg_ref, w in zip(assigned, iter_arg_refs, iter_inits):
            body_comp.env[name] = Wire(self.m, Signal(ref=arg_ref, ty=w.ty))
        body_comp.compile_block(node.body)

        yield_vals = [body_comp._coerce_to_type(body_comp.env[n], expected_ty=ty, ctx="for yield") for n, ty in zip(assigned, result_types)]
        _emit_scf_yield(self.m, yield_vals)
        self.m.pop_indent()
        self.m.emit_line("}")

        for name, res_ref, ty in zip(assigned, result_refs, result_types):
            self.env[name] = self._alias_if_wire(Wire(self.m, Signal(ref=res_ref, ty=ty)), base_name=name, node=node)


def compile(fn: Any, *, name: str | None = None, **params: Any) -> Circuit:
    """Compile a restricted Python function into a static pyCircuit Module.

    The function is *not executed*; it is parsed via `ast` and lowered into
    MLIR SCF + PYC ops, then `pyc-compile` will lower SCF into static muxes and
    unrolled logic.

    Restrictions (prototype):
    - `if` conditions: python bool or `i1` Wire
    - `for` loops: `for ... in range(const)` only, step must be > 0
    - Loop induction variable is currently not usable in expressions.
    """

    lines, start_line = inspect.getsourcelines(fn)
    src = textwrap.dedent("".join(lines))
    tree = ast.parse(src)
    fdef = _find_function_def(tree, fn.__name__)

    if not fdef.args.args:
        raise JitError("function must take at least one argument (the Circuit builder)")
    builder_arg = fdef.args.args[0].arg

    # Bind JIT-time params to remaining arguments by name.
    for a in fdef.args.args[1:]:
        if a.arg not in params:
            raise JitError(f"missing JIT param {a.arg!r}")

    m = Circuit(name or fn.__name__)
    src_file = inspect.getsourcefile(fn) or inspect.getfile(fn)
    src_stem = None
    try:
        if src_file:
            src_stem = Path(src_file).stem
    except Exception:
        src_stem = None
    c = _Compiler(
        m,
        params=dict(params),
        globals_=dict(fn.__globals__),
        source_stem=src_stem,
        line_offset=int(start_line - 1),
    )
    c.env[builder_arg] = m

    returned: list[Any] = []
    for stmt in fdef.body:
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                break
            v = c.eval_expr(stmt.value)
            if isinstance(v, tuple):
                returned.extend(v)
            else:
                returned.append(v)
            break
        c.compile_stmt(stmt)

    if returned and getattr(m, "_results", []):  # noqa: SLF001
        raise JitError("cannot mix `return` and explicit `m.output(...)`")
    if returned:
        if len(returned) == 1:
            m.output("out", returned[0])
        else:
            for i, v in enumerate(returned):
                m.output(f"out{i}", v)

    return m
