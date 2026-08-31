"""PyCircuit V5 cycle-aware frontend (tutorial + Cycle-Aware API).

Maps documented grammar onto the existing Circuit/Wire MLIR builder. Library and
top-level designs should use CycleAwareCircuit / CycleAwareDomain and
compile_cycle_aware() instead of @module + compile().
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass, field
import inspect
import textwrap
import threading
from typing import Any, Callable, Generic, Iterable, Iterator, Mapping, TypeVar, Union, cast, overload

from .data import DT, Bits, Data, Vector
from .dsl import Signal
from .hw import Circuit, ClockDomain, Reg, Wire
from .literals import LiteralValue, infer_literal_width
from .tb import Tb as _Tb, TbError

F = TypeVar("F", bound=Callable[..., Any])
VT = TypeVar("VT", bound=Data)

# Union of every signal flavor accepted by the V5 coercion helpers
# (CycleAwareSignal.as_cas / is_cas, mux, cat, priority_mux).  Forward
# references are fine because they only appear inside string annotations.
CycleAwareLike = Union[
    "CycleAwareSignal",
    "StateSignal",
    "ForwardSignal",
    Wire,
    Reg,
    int,
    LiteralValue,
]

_tls = threading.local()


def _current_domain() -> "CycleAwareDomain | None":
    return getattr(_tls, "domain", None)


def _set_current_domain(d: "CycleAwareDomain | None") -> None:
    _tls.domain = d


@dataclass
class _ModuleCtx:
    owner: "pyc_CircuitModule"
    inputs: list[Any]
    description: str
    outputs: list[Any] = field(default_factory=list)


class CycleAwareCircuit(Circuit):
    """V5 top-level builder; extends Circuit so m.out / m.cat / emit_mlir work unchanged."""

    def __init__(self, name: str, design_ctx: Any | None = None) -> None:
        super().__init__(name, design_ctx=design_ctx)
        self._v5_design: Any | None = None

    def emit_mlir(self) -> str:
        if self._v5_design is not None:
            return self._v5_design.emit_mlir()
        return super().emit_mlir()

    def create_domain(self, name: str, *, frequency_desc: str = "", reset_active_high: bool = False) -> "CycleAwareDomain":
        _ = (frequency_desc, reset_active_high)
        return CycleAwareDomain(self, str(name))

    def const_signal(
        self,
        value: int | list,
        width: int,
        domain: "CycleAwareDomain",
        *,
        signed: bool = False,
    ) -> Wire:
        """Create a scalar or shaped constant in a V5 clock domain."""
        return domain.create_const(value, width=int(width), signed=signed)

    def input_signal(
        self,
        name: str,
        width: int,
        domain: "CycleAwareDomain",
        *,
        shape: list[int] | None = None,
        signed: bool = False,
    ) -> Wire:
        """Create a scalar or Vector input port in a V5 clock domain."""
        return domain.create_signal(str(name), width=int(width), shape=shape, signed=signed)


class CycleAwareDomain:
    """Clock domain with logical occurrence index (tutorial: next/prev/push/pop/cycle)."""

    def __init__(self, circuit: Circuit, domain_name: str) -> None:
        self._m = circuit
        self._name = str(domain_name)
        self._cd = _clock_domain_ports(circuit, self._name)
        self._occurrence = 0
        self._stack: list[int] = []
        self._delay_serial = 0
        self._reg_serial = 0
        # Hierarchical compilation state (set by compile_cycle_aware)
        self._hierarchical: bool = False
        self._design: Any | None = None
        self._sub_cache: dict[tuple[Any, ...], Any] = {}

    @property
    def clock_domain(self) -> ClockDomain:
        """Underlying clk/rst pair for m.out(..., domain=...)."""
        return self._cd

    @property
    def circuit(self) -> Circuit:
        return self._m

    def create_reset(self) -> Wire:
        """Active-high reset as **i1** for mux / boolean logic (via ``pyc.reset_active``)."""
        ra = self._m.reset_active(self._cd.rst)
        return Wire(self._m, ra)

    def create_signal(
        self,
        port_name: str,
        *,
        width: int,
        shape: list[int] | None = None,
        signed: bool = False,
    ) -> Wire:
        """Declare a scalar or statically shaped Vector input port."""
        return self._m.input(str(port_name), width=int(width), shape=list(shape or []), signed=signed)

    def create_const(
        self,
        value: int | list,
        *,
        width: int,
        name: str = "",
        signed: bool = False,
    ) -> Wire:
        """Create a scalar or nested-list Vector constant."""
        _ = name
        return self._m.const(value, width=int(width), signed=signed)

    def next(self) -> None:
        self._occurrence += 1

    def prev(self) -> None:
        self._occurrence -= 1

    def push(self) -> None:
        self._stack.append(self._occurrence)

    def pop(self) -> None:
        if not self._stack:
            raise RuntimeError("clock_domain.pop() without matching push()")
        self._occurrence = self._stack.pop()

    @property
    def cycle_index(self) -> int:
        return self._occurrence

    def cycle(
        self,
        sig: Union[Wire, Reg, "CycleAwareSignal"],
        reset_value: int | list[Any] | None = None,
        name: str = "",
    ) -> Wire:
        """Single-stage register (DFF); output is one logical cycle after the input value."""
        w = _as_wire(self._m, sig)
        width = w.width
        init = 0 if reset_value is None else reset_value
        reg_name = str(name).strip() or f"_v5_reg_{self._reg_serial}"
        self._reg_serial += 1
        full = self._m.scoped_name(reg_name)
        shape = w.ty.shape() if isinstance(w.ty, Vector) else []
        r = self._m.out(full, domain=self._cd, width=width, shape=shape, init=init)
        r.set(w)
        return r.q

    def _state(
        self,
        *,
        width: int,
        reset_value: int | list[Any] = 0,
        name: str = "",
        shape: list[int] | None = None,
    ) -> "StateSignal":
        """Internal: create a feedback register. Use ``domain.signal()`` instead."""
        reg_name = str(name).strip() or f"_v5_reg_{self._reg_serial}"
        self._reg_serial += 1
        full = self._m.scoped_name(reg_name)
        reg = self._m.out(
            full,
            domain=self._cd,
            width=int(width),
            shape=list(shape or []),
            init=reset_value,
        )
        return StateSignal(self, reg, self._occurrence)

    def signal(
        self,
        *,
        width: int | None = None,
        reset_value: int | list[Any] = 0,
        name: str = "",
        shape: list[int] | None = None,
        fields: Any | None = None,
        enum: Any | None = None,
    ) -> "ForwardSignal | Any":
        """Declare a forward-declared register with ``<<=`` / ``.assign()`` syntax.

        Returns a :class:`ForwardSignal` whose Q output is immediately usable
        in expressions.  The D input is connected later via::

            sig <<= next_val          # unconditional
            sig.assign(next_val, when=cond)  # conditional

        This is sugar over :meth:`state` with a more ergonomic write syntax.

        Passing ``fields=`` (a ``BitfieldSpec`` or a plain ``{name: (msb, lsb)}``
        mapping) binds the layout and returns a ``BitfieldSignal`` supporting
        ``x["field"]`` / ``x.field`` access plus ``x <<= ...``; when ``width`` is
        omitted it is taken from the spec (required for a plain mapping). Passing
        ``enum=`` (a ``PycEnum`` subclass) sizes the register to the enum width
        and returns an ``EnumSignal`` supporting ``x.is_(E.MEMBER)`` and
        ``x <<= E.MEMBER``.
        """
        if enum is not None:
            if fields is not None or shape is not None:
                raise TypeError("signal(enum=...) cannot be combined with fields=/shape=")
            from .enums import EnumSignal, coerce_enum_cls, enum_width

            enum = coerce_enum_cls(enum)
            ew = enum_width(enum)
            if width is None:
                width = ew
            elif int(width) != ew:
                raise ValueError(
                    f"signal width {width} does not match enum {enum.__name__} width {ew}"
                )
            st = self._state(width=width, reset_value=reset_value, name=name)
            return EnumSignal(enum, ForwardSignal(st))
        if fields is not None:
            if shape is not None:
                raise TypeError("signal(fields=...) cannot be combined with shape=")
            from .bitfield import coerce_bitfield_spec

            fields = coerce_bitfield_spec(fields, width=width)
            if width is None:
                width = int(fields.width)
            elif int(width) != int(fields.width):
                raise ValueError(
                    f"signal width {width} does not match BitfieldSpec width {fields.width}"
                )
        if width is None:
            raise TypeError("signal() requires width= (or fields=)")
        st = self._state(width=width, reset_value=reset_value, name=name, shape=shape)
        fwd = ForwardSignal(st)
        return fields.bind(fwd) if fields is not None else fwd

    def call(
        self,
        fn: Callable[..., Any],
        *,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Call a sub-module function with cycle isolation.

        In normal (flat) mode: saves/restores the cycle counter and inlines
        the sub-module logic into the parent circuit.

        In hierarchical mode (``self._hierarchical is True``): compiles the
        sub-module as a separate MLIR ``func.func``, emits a ``pyc.instance``
        op in the parent, and returns output signals wired from the instance.

        The returned dict preserves each signal's ``cycle`` attribute.
        """
        if self._hierarchical:
            return self._call_hierarchical(fn, inputs=inputs, **kwargs)
        self.push()
        try:
            result = fn(self._m, self, inputs=inputs, **kwargs)
        finally:
            self.pop()
        return result

    def _call_hierarchical(
        self,
        fn: Callable[..., Any],
        *,
        inputs: dict[str, Any] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Compile *fn* as a standalone sub-module, then instantiate it."""
        sub_name = getattr(fn, "__pycircuit_name__", getattr(fn, "__name__", "sub"))
        prefix = kwargs.get("prefix", sub_name)

        cache_key = _hierarchical_cache_key(fn, kwargs)
        if cache_key not in self._sub_cache:
            canonical_kwargs = dict(kwargs)
            canonical_kwargs["prefix"] = sub_name

            sub_m = CycleAwareCircuit(sub_name)
            sub_dom = sub_m.create_domain(self._name)
            sub_dom._hierarchical = True
            sub_dom._design = self._design
            sub_dom._sub_cache = self._sub_cache

            outs_dict = fn(sub_m, sub_dom, inputs=None, **canonical_kwargs)

            out_entries = _record_output_structure(outs_dict, circuit=sub_m)

            cm = _make_compiled_module(fn, sub_m, sub_name)
            self._design.add(cm)
            self._sub_cache[cache_key] = (sub_m, out_entries)

        sub_m, out_entries = self._sub_cache[cache_key]

        canonical_prefix = sub_name
        input_map: dict[str, Any] = {}
        if inputs:
            for k, v in inputs.items():
                input_map[f"{canonical_prefix}_{k}"] = v

        input_sigs: list[Signal] = []
        for port_name, port_sig in sub_m._args:
            if port_sig.ty == "!pyc.clock":
                input_sigs.append(self._cd.clk)
            elif port_sig.ty == "!pyc.reset":
                input_sigs.append(self._cd.rst)
            elif port_name in input_map:
                actual = input_map[port_name]
                actual_sig = _to_wire(actual).sig

                if actual_sig.ty != port_sig.ty and isinstance(actual_sig.ty, Bits) and isinstance(port_sig.ty, Bits):
                    actual_w = actual_sig.ty.width
                    expect_w = port_sig.ty.width
                    w = Wire(self._m, actual_sig)
                    if actual_w < expect_w:
                        w = w.zext(width=expect_w)
                    else:
                        w = w.trunc(width=expect_w)
                    actual_sig = w.sig
                if actual_sig.ty != port_sig.ty:
                    raise TypeError(f"input {port_name!r} type mismatch: actual {actual_sig.ty} != expected {port_sig.ty}")
                input_sigs.append(actual_sig)
            else:
                if isinstance(port_sig.ty, Vector):
                    shape = port_sig.ty.shape()
                    elem_ty = port_sig.ty.datatype()
                    width = elem_ty.width if isinstance(elem_ty, Bits) else 1
                elif isinstance(port_sig.ty, Bits):
                    shape = []
                    width = port_sig.ty.width
                else:
                    shape = []
                    width = 1
                if port_name.startswith(canonical_prefix + "_"):
                    suffix = port_name[len(canonical_prefix) + 1:]
                    parent_port_name = f"{prefix}_{suffix}"
                else:
                    parent_port_name = f"{prefix}_{port_name}"
                parent_value = self._m.input(parent_port_name, width=width, shape=shape)
                input_sigs.append(parent_value.sig)

        result_types = [sig.ty for _, sig in sub_m._results]
        out_sigs = self._m.instance_op(
            sub_name,
            *input_sigs,
            result_types=result_types,
            name=prefix,
        )

        out_values: list[Any] = []
        for sig in out_sigs:
            out_values.append(Wire(self._m, sig))
        return _reconstruct_output_dict(out_entries, out_values, self)

    def delay_to(
        self,
        w: Wire,
        *,
        from_cycle: int,
        to_cycle: int,
        width: int | None = None,
    ) -> Wire:
        """Insert (to_cycle - from_cycle) register stages for automatic cycle balancing."""
        if to_cycle <= from_cycle:
            return w
        d = to_cycle - from_cycle
        cur: Wire = w
        for _ in range(d):
            self._delay_serial += 1
            nm = f"_v5_bal_{self._delay_serial}"
            shape = w.ty.shape() if isinstance(w.ty, Vector) else []
            r = self._m.out(
                self._m.scoped_name(nm),
                domain=self._cd,
                width=w.width if width is None else int(width),
                shape=shape,
                init=0,
            )
            r.set(cur)
            cur = Wire(self._m, r.q.sig, signed=cur.signed)
        return cur

    def vec(
        self,
        *values: CycleAwareSignal[VT] | StateSignal[VT] | ForwardSignal[VT] | Wire[VT] | Reg[VT] | int | LiteralValue | list[CycleAwareSignal[VT] | StateSignal[VT] | ForwardSignal[VT] | Wire[VT] | Reg[VT] | int | LiteralValue],
    ) -> CycleAwareSignal[Vector[VT]]:
        """Build a Vector and align every lane to the latest logical cycle.

        Lanes may be any cycle-aware signal flavor, a plain Wire/Reg, or a
        hardware literal (``LiteralValue`` / ``int``). Width resolution matches
        ``Circuit.vec``: explicit-width literals and Wires are authoritative;
        width-less literals (``U(1)``) and bare ints adopt the widest context
        width. Every lane is then delayed to the latest cycle so the resulting
        vector is cycle-consistent.
        """
        if len(values) == 1 and isinstance(values[0], list):
            values = tuple(values[0])
        if not values:
            raise ValueError("CycleAwareDomain.vec requires at least one value")

        # Pass 1: collect (raw, cycle) and resolve the target lane width/signed.
        raw_lanes: list[tuple[object, int]] = []
        wire_width: int | None = None
        wire_signed: bool | None = None
        lit_width: int | None = None
        lit_signed: bool | None = None
        for value in values:
            if isinstance(value, (CycleAwareSignal, StateSignal, ForwardSignal)):
                if value.domain is not self:
                    raise ValueError("CycleAwareDomain.vec values must share this domain")
                w = _to_wire(value)
                cyc = value.cycle
                if wire_width is None:
                    wire_width = w.width
                    wire_signed = w.signed
                raw_lanes.append((value, cyc))
            elif isinstance(value, Reg):
                if wire_width is None:
                    wire_width = value.q.width
                    wire_signed = value.q.signed
                raw_lanes.append((value, self.cycle_index))
            elif isinstance(value, Wire):
                if value.m is not self._m:
                    raise ValueError("CycleAwareDomain.vec values must share this circuit")
                if wire_width is None:
                    wire_width = value.width
                    wire_signed = value.signed
                raw_lanes.append((value, self.cycle_index))
            elif isinstance(value, LiteralValue):
                lw = value.width if value.width is not None else infer_literal_width(int(value.value), signed=bool(value.signed))
                if lit_width is None or lw > lit_width:
                    lit_width = lw
                if lit_signed is None:
                    lit_signed = bool(value.signed)
                raw_lanes.append((value, self.cycle_index))
            elif isinstance(value, int):
                lw = infer_literal_width(int(value), signed=value < 0)
                if lit_width is None or lw > lit_width:
                    lit_width = lw
                if lit_signed is None:
                    lit_signed = value < 0
                raw_lanes.append((value, self.cycle_index))
            else:
                raise TypeError(f"CycleAwareDomain.vec expects signal lanes or literals, got {type(value).__name__}")

        if wire_width is not None:
            resolved_width, resolved_signed = wire_width, wire_signed
        else:
            resolved_width, resolved_signed = lit_width, lit_signed

        # Pass 2: materialize each lane into a Wire at the resolved width,
        # then delay to the common cycle.
        cycle = max(cyc for _, cyc in raw_lanes)
        aligned: list[Wire] = []
        for value, cyc in raw_lanes:
            if isinstance(value, (CycleAwareSignal, StateSignal, ForwardSignal, Reg, Wire)):
                w = _to_wire(value) if not isinstance(value, Wire) else value
                if isinstance(value, Reg):
                    w = value.q
            elif isinstance(value, LiteralValue):
                w = Wire.as_wire(value, width=resolved_width, signed=resolved_signed, m=self._m)
            else:  # int
                w = Wire.as_wire(int(value), width=resolved_width, signed=resolved_signed, m=self._m)
            aligned.append(self.delay_to(w, from_cycle=cyc, to_cycle=cycle))
        return CycleAwareSignal(self, self._m.vec(aligned), cycle)

    def cat(
        self,
        *elems: CycleAwareSignal | StateSignal | ForwardSignal | Wire | Reg | int,
    ) -> CycleAwareSignal:
        """Concatenate values MSB-first with cycle alignment (see :func:`cat`)."""
        return cat(*elems)  # type: ignore[arg-type]

    def priority_mux(
        self,
        sels: CycleAwareSignal | StateSignal | ForwardSignal | Wire,
        vals: CycleAwareSignal | StateSignal | ForwardSignal | Wire,
        *,
        mode: str = "chain",
        default: CycleAwareSignal | StateSignal | ForwardSignal | Wire | None = None,
    ) -> CycleAwareSignal:
        """Cycle-aware wrapper for ``pyc.priority_mux`` (see :func:`priority_mux`)."""
        return priority_mux(sels, vals, mode=mode, default=default)  # type: ignore[arg-type]


# ── Hierarchical compilation helpers ──────────────────────────────────────

def _hierarchical_cache_key(fn: Callable[..., Any], kwargs: dict[str, Any]) -> tuple[Any, ...]:
    """Build a cache key from function identity + compile-time kwargs.

    ``prefix`` is excluded because it only affects port naming, not the
    module's structural identity."""
    import json as _json
    kw_str = _json.dumps(
        {k: repr(v) for k, v in sorted(kwargs.items()) if k != "prefix"},
        sort_keys=True, separators=(",", ":"),
    )
    return (id(fn), kw_str)


def _record_output_structure(
    outs_dict: dict[str, Any] | Any,
    circuit: "CycleAwareCircuit | None" = None,
) -> list[tuple[str, str, int, list[int], list[int]]]:
    """Walk *outs_dict* and record ``(key, kind, count, cycles, result_indices)``
    for each entry.

    When *circuit* is provided, each signal's Wire is matched against
    ``circuit._results`` to record the actual result port indices instead of
    assuming positional matching (which fails when ``m.output()`` calls are
    interleaved across dict entries).

    Returns a list consumed by :func:`_reconstruct_output_dict`.
    """
    if not isinstance(outs_dict, dict):
        return []

    ref_to_idx: dict[str, int] = {}
    if circuit is not None:
        for i, (_name, sig) in enumerate(circuit._results):
            ref_to_idx[sig.ref] = i

    entries: list[tuple[str, str, int, list[int], list[int]]] = []
    for key, val in outs_dict.items():
        if isinstance(val, Wire) and isinstance(val.sig.ty, Vector):
            idx = ref_to_idx.get(val.sig.ref, -1) if ref_to_idx else -1
            entries.append((key, "vec", len(val), [0], [idx]))
        elif isinstance(val, list):
            cycles: list[int] = []
            indices: list[int] = []
            for v in val:
                if isinstance(v, (CycleAwareSignal, ForwardSignal, StateSignal)):
                    cycles.append(v.cycle)
                    w = wire_of(v)
                    indices.append(ref_to_idx.get(w.sig.ref, -1) if ref_to_idx else -1)
                elif isinstance(v, (Wire, Reg)):
                    cycles.append(0)
                    w = wire_of(v)
                    indices.append(ref_to_idx.get(w.sig.ref, -1) if ref_to_idx else -1)
                else:
                    cycles.append(0)
                    indices.append(-1)
            entries.append((key, "list", len(val), cycles, indices))
        elif isinstance(val, (CycleAwareSignal, ForwardSignal, StateSignal)):
            w = wire_of(val)
            idx = ref_to_idx.get(w.sig.ref, -1) if ref_to_idx else -1
            entries.append((key, "scalar", 1, [val.cycle], [idx]))
    return entries


def _reconstruct_output_dict(
    entries: list[tuple[str, str, int, list[int], list[int]]],
    out_wires: list[Any],
    domain: CycleAwareDomain,
) -> dict[str, Any]:
    """Rebuild an output dict from ``pyc.instance`` result wires.

    Uses recorded result indices when available (>= 0); falls back to
    sequential positional matching otherwise.
    """
    outs: dict[str, Any] = {}
    seq_idx = 0
    for key, kind, count, cycles, indices in entries:
        if kind == "scalar":
            ri = indices[0] if indices and indices[0] >= 0 else seq_idx
            outs[key] = CycleAwareSignal(domain, out_wires[ri], cycles[0])
            seq_idx += 1
        elif kind == "vec":
            ri = indices[0] if indices and indices[0] >= 0 else seq_idx
            outs[key] = out_wires[ri]
            seq_idx += 1
        else:
            items: list[CycleAwareSignal] = []
            for i in range(count):
                ri = indices[i] if indices and indices[i] >= 0 else seq_idx
                items.append(CycleAwareSignal(domain, out_wires[ri], cycles[i]))
                seq_idx += 1
            outs[key] = items
    return outs


def _make_compiled_module(fn: Any, circuit: CycleAwareCircuit, sym_name: str) -> Any:
    """Create a :class:`~pycircuit.design.CompiledModule` from an eagerly-compiled circuit."""
    from .design import CompiledModule, _kind_of, _inline_of, _base_name
    import json as _json

    arg_names = tuple(n for n, _ in circuit._args)
    arg_types = tuple(sig.ty for _, sig in circuit._args)
    res_names = tuple(n for n, _ in circuit._results)
    res_types = tuple(sig.ty for _, sig in circuit._results)

    kind = _kind_of(fn)
    inline = "true" if _inline_of(fn) else "false"
    base = _base_name(fn)
    struct_metrics = _json.dumps({
        "source_loc": 0, "ast_node_count": 0, "hardware_call_count": 0,
        "loop_count": 0, "module_call_count": 0, "state_call_count": 0,
        "estimated_inline_cost": 0, "instance_count": 0,
        "state_alloc_count": 0, "collection_count": 0,
        "collection_instance_count": 0, "module_family_collection_count": 0,
        "repeat_pressure": 0, "repeated_body_clusters": [],
    }, sort_keys=True, separators=(",", ":"))
    struct_collections = "[]"

    circuit.set_func_attr("pyc.kind", kind)
    circuit.set_func_attr("pyc.inline", inline)
    circuit.set_func_attr("pyc.params", "{}")
    circuit.set_func_attr("pyc.base", base)
    circuit.set_func_attr("pyc.struct.metrics", struct_metrics)
    circuit.set_func_attr("pyc.struct.collections", struct_collections)
    circuit.set_func_attr_json("pyc.value_params", [])
    circuit.set_func_attr_json("pyc.value_param_types", [])

    return CompiledModule(
        fn=fn,
        params_json="{}",
        sym_name=str(sym_name),
        mod=circuit,
        arg_names=arg_names,
        arg_types=arg_types,
        result_names=res_names,
        result_types=res_types,
        value_param_names=(),
        value_param_types=(),
        struct_metrics_json=struct_metrics,
        struct_collections_json=struct_collections,
    )


def _clock_domain_ports(m: Circuit, name: str) -> ClockDomain:
    if name == "clk":
        return ClockDomain(clk=m.clock("clk"), rst=m.reset("rst"))
    return m.domain(name)


def _as_wire(m: Circuit, sig: Union[Wire, Reg, "CycleAwareSignal", "ForwardSignal", Signal]) -> Wire:
    if isinstance(sig, ForwardSignal):
        return sig._state._cas._w
    if isinstance(sig, CycleAwareSignal):
        return sig._w
    if isinstance(sig, Reg):
        return sig.q
    if isinstance(sig, Wire):
        return sig
    if isinstance(sig, Signal):
        return Wire(m, sig)
    raise TypeError(f"expected Wire/Reg/CycleAwareSignal/ForwardSignal/Signal, got {type(sig).__name__}")


class StateSignal(Generic[DT]):
    """Internal feedback register. Created by ``domain._state()`` (private).

    Users should use ``domain.signal()`` which returns a ``ForwardSignal`` instead.
    Exposes the same ``.ty`` / ``.width`` / ``.signed`` / ``.wire`` surface as :class:`Wire`.
    """

    __slots__ = ("_domain", "_reg", "_cas")

    def __init__(self, domain: "CycleAwareDomain", reg: Reg, cycle: int) -> None:
        self._domain = domain
        self._reg = reg
        self._cas = CycleAwareSignal(domain, reg.out(), cycle)

    def set(
        self,
        next_val: "Wire | Reg | CycleAwareSignal | StateSignal | int | LiteralValue",
        *,
        when: "Wire | Reg | CycleAwareSignal | StateSignal | None" = None,
    ) -> None:
        """Connect the D input of the register (close the feedback loop).

        A plain Python ``int`` / ``LiteralValue`` is loaded as a constant of the
        register's declared width (``reg <<= 0b1010``).
        """
        next_val = self._coerce_next(next_val)
        w = _to_wire(next_val)
        wh = _to_wire(when) if when is not None else None
        if wh is not None:
            self._reg.set(w, when=wh)
        else:
            self._reg.set(w)

    def _coerce_next(self, next_val: object) -> object:
        """Turn a plain ``int``/``LiteralValue`` into a const of the reg width."""
        width = self._cas._w.width
        m = self._domain._m
        if isinstance(next_val, bool):
            return m.const(int(next_val), width=width)
        if isinstance(next_val, int):
            return m.const(int(next_val), width=width)
        if isinstance(next_val, LiteralValue):
            lit_w = int(next_val.width) if next_val.width is not None else width
            return m.const(int(next_val.value), width=lit_w)
        return next_val

    @property
    def cycle(self) -> int:
        return self._cas.cycle

    @property
    def domain(self) -> "CycleAwareDomain":
        return self._domain

    @property
    def ty(self) -> DT:
        """Underlying scalar or Vector data type (matches :class:`Wire.ty`)."""
        return self._cas.ty

    @property
    def width(self) -> int:
        """Leaf element width (matches :class:`Wire.width`)."""
        return self._cas.width

    @property
    def signed(self) -> bool:
        return self._cas.signed

    @property
    def wire(self) -> Wire[DT]:
        """Read-side Q wire for APIs that require the regular frontend value."""
        return self._cas.wire

    def __getattr__(self, name: str) -> object:
        return getattr(self._cas, name)

    def __add__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__add__(other)

    def __radd__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__radd__(other)

    def __sub__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__sub__(other)

    def __rsub__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__rsub__(other)

    def __mul__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__mul__(other)

    def __rmul__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__rmul__(other)

    def __floordiv__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__floordiv__(other)

    def __rfloordiv__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__rfloordiv__(other)

    def __mod__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__mod__(other)

    def __rmod__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__rmod__(other)

    def __and__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__and__(other)

    def __rand__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__rand__(other)

    def __or__(self, other: object) -> "CycleAwareSignal":
        if isinstance(other, str):
            return self._cas
        return self._cas.__or__(other)

    def __ror__(self, other: object) -> "CycleAwareSignal":
        if isinstance(other, str):
            return self._cas
        return self._cas.__ror__(other)

    def __xor__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__xor__(other)

    def __rxor__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__rxor__(other)

    def __invert__(self) -> "CycleAwareSignal":
        return self._cas.__invert__()

    def __eq__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        return self._cas.__eq__(other)

    def __ne__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        return self._cas.__ne__(other)

    def __lt__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__lt__(other)

    def __gt__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__gt__(other)

    def __le__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__le__(other)

    def __ge__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__ge__(other)

    def __lshift__(self, amount: object) -> "CycleAwareSignal":
        return self._cas.__lshift__(amount)

    def __rshift__(self, amount: object) -> "CycleAwareSignal":
        return self._cas.__rshift__(amount)

    def __len__(self) -> int:
        return len(self._cas)

    def __iter__(self) -> Iterator["CycleAwareSignal"]:
        return iter(self._cas)

    def __getitem__(self, idx: int | slice) -> "CycleAwareSignal":
        return self._cas.__getitem__(idx)

    def __repr__(self) -> str:
        return f"StateSignal({self._cas._w}, cycle={self._cas.cycle})"


class ForwardSignal(Generic[DT]):
    """Forward-declared register signal with ``<<=`` and ``.assign()`` syntax.

    Created by ``domain.signal()``.  The underlying hardware is identical to
    :class:`StateSignal` — a D flip-flop whose Q output is available at the
    declaration cycle and whose D input is connected later.

    Exposes the same ``.ty`` / ``.width`` / ``.signed`` / ``.wire`` surface as
    :class:`Wire` so callers can inspect the represented Bits/Vector type.

    **Usage**::

        # Cycle 0: declare and read
        counter = domain.signal(width=8, reset_value=0, name="cnt")
        m.output("cnt_out", counter)

        domain.next()  # → Cycle 1

        # Unconditional update
        counter <<= counter + 1

        # — or conditional —
        counter.assign(counter + 1, when=enable)

    ``ForwardSignal`` delegates all arithmetic / comparison / slicing operators
    to the inner ``CycleAwareSignal`` so it can be used directly in expressions
    without wrapping in ``cas()``.
    """

    __slots__ = ("_state",)

    def __init__(self, state: StateSignal[DT]) -> None:
        self._state = state

    # ── assignment operators ──────────────────────────────────────────

    def __ilshift__(self, next_val: Union[Wire, CycleAwareSignal, StateSignal]) -> "ForwardSignal":
        """``signal <<= expr`` → unconditional register drive."""
        self._state.set(next_val)
        return self

    def assign(
        self,
        next_val: "Wire | Reg | CycleAwareSignal | StateSignal | ForwardSignal",
        *,
        when: "Wire | Reg | CycleAwareSignal | StateSignal | ForwardSignal | None" = None,
    ) -> None:
        """Conditional register drive: ``signal.assign(expr, when=cond)``."""
        self._state.set(next_val, when=when)

    # ── read-side properties (delegate to inner CAS) ─────────────────

    @property
    def cycle(self) -> int:
        return self._state.cycle

    @property
    def domain(self) -> "CycleAwareDomain":
        return self._state.domain

    @property
    def name(self) -> str:
        return str(self._state._cas._w)

    @property
    def ty(self) -> DT:
        """Underlying scalar or Vector data type (matches :class:`Wire.ty`)."""
        return self._state.ty

    @property
    def width(self) -> int:
        """Leaf element width (matches :class:`Wire.width`)."""
        return self._state.width

    @property
    def signed(self) -> bool:
        return self._state.signed

    @property
    def wire(self) -> Wire[DT]:
        """Read-side Q wire for APIs that require the regular frontend value."""
        return self._state.wire

    # ── arithmetic / logic operators (forward to inner CAS) ──────────
    def as_cas(self) -> "CycleAwareSignal":
        """Read the register at the domain's current logical cycle."""
        return CycleAwareSignal(
            self._state.domain,
            self._state._cas._w,
            self._state.domain.cycle_index,
        )

    def __add__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__add__(other)

    def __radd__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__radd__(other)

    def __sub__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__sub__(other)

    def __rsub__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__rsub__(other)

    def __mul__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__mul__(other)

    def __rmul__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__rmul__(other)

    def __floordiv__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__floordiv__(other)

    def __rfloordiv__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__rfloordiv__(other)

    def __mod__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__mod__(other)

    def __rmod__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__rmod__(other)

    def __and__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__and__(other)

    def __rand__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__rand__(other)

    def __or__(self, other: object) -> "CycleAwareSignal":
        if isinstance(other, str):
            return self.as_cas()
        return self.as_cas().__or__(other)

    def __ror__(self, other: object) -> "CycleAwareSignal":
        if isinstance(other, str):
            return self.as_cas()
        return self.as_cas().__ror__(other)

    def __xor__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__xor__(other)

    def __rxor__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__rxor__(other)

    def __invert__(self) -> "CycleAwareSignal":
        return self.as_cas().__invert__()

    def __eq__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        return self.as_cas().__eq__(other)

    def __ne__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        return self.as_cas().__ne__(other)

    def __lt__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__lt__(other)

    def __gt__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__gt__(other)

    def __le__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__le__(other)

    def __ge__(self, other: object) -> "CycleAwareSignal":
        return self.as_cas().__ge__(other)

    def __lshift__(self, amount: object) -> "CycleAwareSignal":
        return self.as_cas().__lshift__(amount)

    def __rshift__(self, amount: object) -> "CycleAwareSignal":
        return self.as_cas().__rshift__(amount)

    def __len__(self) -> int:
        return len(self.as_cas())

    def __iter__(self) -> Iterator["CycleAwareSignal"]:
        return iter(self.as_cas())

    def __getitem__(self, idx: int | slice) -> "CycleAwareSignal":
        return self.as_cas().__getitem__(idx)

    def __getattr__(self, name: str) -> object:
        return getattr(self.as_cas(), name)

    def __repr__(self) -> str:
        return f"ForwardSignal({self._state._cas._w}, cycle={self._state.cycle})"


def _to_wire(v: "Wire | Reg | CycleAwareSignal | StateSignal | ForwardSignal") -> Wire:
    _unwrap = getattr(v, "__pyc_unwrap__", None)
    if callable(_unwrap):
        v = _unwrap()
    if isinstance(v, ForwardSignal):
        return v._state._cas._w
    if isinstance(v, StateSignal):
        return v._cas._w
    if isinstance(v, CycleAwareSignal):
        return v._w
    if isinstance(v, Reg):
        return v.q
    if isinstance(v, Wire):
        return v
    raise TypeError(f"expected Wire/Reg/CycleAwareSignal/StateSignal/ForwardSignal, got {type(v).__name__}")


# ── Hierarchical-composition helpers ──────────────────────────────────────

def submodule_input(
    io: dict[str, Any] | None,
    key: str,
    m: Circuit,
    domain: CycleAwareDomain,
    *,
    prefix: str,
    width: int,
    cycle: int = 0,
) -> "CycleAwareSignal":
    """Resolve an input signal in dual-mode: composed or standalone.

    When *io* is provided and contains *key*, the caller's
    ``CycleAwareSignal`` is returned unchanged (preserving its cycle
    provenance).  Otherwise a fresh top-level ``m.input()`` is created so the
    module can still compile independently::

        pc = submodule_input(inputs, "pc", m, domain, prefix="fe", width=32)

    Parameters
    ----------
    io : dict or None
        The ``inputs`` dict forwarded by the parent.  ``None`` means
        standalone compilation.
    key : str
        Signal name inside the dict / port suffix.
    m : Circuit
        The circuit object.
    domain : CycleAwareDomain
        Active clock domain.
    prefix : str
        Port-name prefix for standalone mode (creates ``{prefix}_{key}``).
    width : int
        Bit width of the signal.
    cycle : int
        Cycle tag used only when creating a standalone ``m.input()`` port.

    Returns
    -------
    CycleAwareSignal
    """
    if io is not None and key in io:
        sig = io[key]
        if isinstance(sig, (CycleAwareSignal, ForwardSignal, StateSignal)):
            return sig  # type: ignore[return-value]
        if isinstance(sig, Wire):
            return CycleAwareSignal(domain, sig, cycle)
        raise TypeError(f"submodule_input: unexpected type for key '{key}': {type(sig).__name__}")
    return CycleAwareSignal(domain, m.input(f"{prefix}_{key}", width=width), cycle)


def wire_of(
    sig: "CycleAwareSignal | ForwardSignal | StateSignal | Wire | Reg",
) -> Wire:
    """Extract the raw ``Wire`` from any signal wrapper (for ``m.output()``)::

        m.output("result", wire_of(outs["result"]))

    This is the **only** sanctioned way to obtain a bare ``Wire`` from a
    ``CycleAwareSignal`` / ``ForwardSignal`` / ``StateSignal``.
    Direct ``.wire`` access is removed from the public API.
    """
    _unwrap = getattr(sig, "__pyc_unwrap__", None)
    if callable(_unwrap):
        sig = _unwrap()
    if isinstance(sig, ForwardSignal):
        return sig._state._cas._w
    if isinstance(sig, StateSignal):
        return sig._cas._w
    if isinstance(sig, CycleAwareSignal):
        return sig._w
    if isinstance(sig, Reg):
        return sig.q
    if isinstance(sig, Wire):
        return sig
    raise TypeError(f"wire_of: unsupported type {type(sig).__name__}")


class CycleAwareSignal(Generic[DT]):
    """Value with logical cycle tag; operators align by delaying earlier operands."""

    __slots__ = ("_domain", "_w", "_cycle")

    def __init__(self, domain: CycleAwareDomain, wire: Wire[DT], cycle: int) -> None:
        if wire.m is not domain._m:
            raise ValueError("Wire must belong to the same circuit as the domain")
        self._domain = domain
        self._w = wire
        self._cycle = int(cycle)

    # ── unified coercion (mirrors Wire.as_wire) ──────────────────────────

    @staticmethod
    def is_cas(v: object) -> bool:
        """Return True if *v* is one of the cycle-aware signal wrappers.

        Accepts :class:`CycleAwareSignal`, :class:`StateSignal`, and
        :class:`ForwardSignal`.  Plain :class:`Wire` / :class:`Reg` / ``int`` /
        :class:`LiteralValue` return False (they need a domain to be promoted).
        """
        return isinstance(v, (CycleAwareSignal, StateSignal, ForwardSignal))

    @classmethod
    def as_cas(
        cls,
        v: "CycleAwareLike",
        *,
        domain: "CycleAwareDomain | None" = None,
        cycle: int | None = None,
    ) -> "CycleAwareSignal":
        """Coerce a heterogeneous signal value into a :class:`CycleAwareSignal`.

        Unified coercion mirroring :meth:`Wire.as_wire`.  Handles every signal
        flavor used in V5 code:

        - :class:`CycleAwareSignal` / :class:`StateSignal` / :class:`ForwardSignal`
          are returned (unwrapped to a bare CAS) unchanged, preserving their
          original cycle tag.  *domain* / *cycle* are ignored.
        - :class:`Reg` is read at ``domain.cycle_index`` (Q output).
        - :class:`Wire` is tagged at ``domain.cycle_index``.
        - ``int`` / :class:`LiteralValue` are materialized as a constant on
          *domain*'s circuit and tagged at ``domain.cycle_index``.

        Parameters
        ----------
        v
            The value to coerce.
        domain
            Required when *v* is **not** already cycle-aware (i.e. it is a
            ``Wire`` / ``Reg`` / ``int`` / ``LiteralValue``).  Ignored otherwise.
        cycle
            Optional cycle tag override for the promoted case.  Defaults to
            ``domain.cycle_index`` when *v* needs promotion.

        Raises
        ------
        TypeError
            If *v* has an unsupported type, or if *domain* is ``None`` when
            needed for promotion.
        """
        # Already cycle-aware: unwrap StateSignal / ForwardSignal to the inner
        # CAS and return as-is (cycle provenance preserved).
        if isinstance(v, ForwardSignal):
            return v._state._cas
        if isinstance(v, StateSignal):
            return v._cas
        if isinstance(v, CycleAwareSignal):
            return v

        # Scalar promotion path below — needs a domain.
        if domain is None:
            raise TypeError(
                f"as_cas: domain is required to promote {type(v).__name__} to a CycleAwareSignal"
            )
        m = domain._m
        tag = domain.cycle_index if cycle is None else int(cycle)
        if isinstance(v, Reg):
            return CycleAwareSignal(domain, v.q, tag)
        if isinstance(v, Wire):
            return CycleAwareSignal(domain, v, tag)
        if isinstance(v, int):
            w = m.const(v, width=max(1, infer_literal_width(v, signed=v < 0)), signed=v < 0)
            return CycleAwareSignal(domain, w, tag)
        if isinstance(v, LiteralValue):
            lw = v.width if v.width is not None else infer_literal_width(int(v.value), signed=bool(v.signed))
            w = m.const(int(v.value), width=int(lw))
            return CycleAwareSignal(domain, w, tag)
        raise TypeError(f"as_cas: unsupported operand type {type(v).__name__}")

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def domain(self) -> CycleAwareDomain:
        return self._domain

    @property
    def name(self) -> str:
        return str(self._w)

    @property
    def signed(self) -> bool:
        return bool(self._w.signed)

    @property
    def wire(self) -> Wire[DT]:
        """Underlying Wire for APIs that require the regular frontend value."""
        return self._w

    @property
    def ty(self) -> DT:
        """Underlying scalar or Vector data type."""
        return self._w.ty

    @property
    def width(self) -> int:
        """Leaf element width, matching :class:`Wire`."""
        return self._w.width

    def __len__(self) -> int:
        return len(self._w)

    def __iter__(self) -> Iterator["CycleAwareSignal"]:
        for lane in self._w:
            yield CycleAwareSignal(self._domain, lane, self._cycle)

    def named(self, name: str) -> "CycleAwareSignal":
        nw = self._domain._m.named(self._w, str(name))
        return CycleAwareSignal(self._domain, nw, self._cycle)

    def _align(self, other: "CycleAwareSignal | StateSignal | ForwardSignal | Wire | Reg | int | LiteralValue") -> tuple[Wire, Wire, int]:
        if isinstance(other, ForwardSignal):
            return self._align(other._state._cas)
        if isinstance(other, StateSignal):
            return self._align(other._cas)
        if isinstance(other, CycleAwareSignal):
            if other._domain is not self._domain:
                raise ValueError("CycleAwareSignal operands must share the same domain")
            oc = other._cycle
            ow = other._w
        elif isinstance(other, (Wire, Reg)):
            ow = other.q if isinstance(other, Reg) else other
            oc = self._domain.cycle_index
        elif isinstance(other, int):
            ow = self._domain._m.const(other, width=max(1, infer_literal_width(other, signed=other < 0)))
            oc = self._domain.cycle_index
        elif isinstance(other, LiteralValue):
            lit_w = other.width if other.width is not None else infer_literal_width(int(other.value), signed=bool(other.signed))
            ow = self._domain._m.const(int(other.value), width=int(lit_w))
            oc = self._domain.cycle_index
        else:
            raise TypeError(f"unsupported operand: {type(other).__name__}")
        mx = max(self._cycle, oc)
        aw = self._domain.delay_to(self._w, from_cycle=self._cycle, to_cycle=mx, width=self._w.width)
        bw = self._domain.delay_to(ow, from_cycle=oc, to_cycle=mx, width=ow.width)
        a2, b2 = _promote_pair(self._domain._m, aw, bw)
        return a2, b2, mx

    def __add__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a + b, c)

    def __radd__(self, other: object) -> "CycleAwareSignal":
        return self.__add__(other)

    def __sub__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a - b, c)

    def __rsub__(self, other: object) -> "CycleAwareSignal":
        # other - self: align first, then subtract with swapped operand order.
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, b - a, c)

    def __mul__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a * b, c)

    def __rmul__(self, other: object) -> "CycleAwareSignal":
        return self.__mul__(other)

    def __floordiv__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a // b, c)

    def __rfloordiv__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, b // a, c)

    def __mod__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a % b, c)

    def __rmod__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, b % a, c)

    def __truediv__(self, other: object) -> "CycleAwareSignal":
        _ = other
        raise TypeError("hardware `/` division is not supported; use `//` for integer division")

    def __rtruediv__(self, other: object) -> "CycleAwareSignal":
        _ = other
        raise TypeError("hardware `/` division is not supported; use `//` for integer division")

    def __and__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a & b, c)

    def __rand__(self, other: object) -> "CycleAwareSignal":
        return self.__and__(other)

    def __or__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        if isinstance(other, str):
            _ = other
            return self
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a | b, c)

    def __ror__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        if isinstance(other, str):
            _ = other
            return self
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, b | a, c)

    def __xor__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a ^ b, c)

    def __rxor__(self, other: object) -> "CycleAwareSignal":
        return self.__xor__(other)

    def __invert__(self) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, ~self._w, self._cycle)

    def __eq__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a == b, c)

    def __ne__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a != b, c)

    def __lt__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a < b, c)

    def __gt__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a > b, c)

    def __le__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a <= b, c)

    def __ge__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a >= b, c)

    def eq(self, other: object) -> "CycleAwareSignal":
        return self.__eq__(other)

    def lt(self, other: object) -> "CycleAwareSignal":
        return self.__lt__(other)

    def gt(self, other: object) -> "CycleAwareSignal":
        return self.__gt__(other)

    def le(self, other: object) -> "CycleAwareSignal":
        return self.__le__(other)

    def ge(self, other: object) -> "CycleAwareSignal":
        return self.__ge__(other)

    def ult(self, other: object) -> "CycleAwareSignal":
        """Unsigned less-than (explicit signedness; result is i1, same cycle)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.ult(b), c)

    def ugt(self, other: object) -> "CycleAwareSignal":
        """Unsigned greater-than (explicit signedness; result is i1)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.ugt(b), c)

    def ule(self, other: object) -> "CycleAwareSignal":
        """Unsigned less-than-or-equal (explicit signedness; result is i1)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.ule(b), c)

    def uge(self, other: object) -> "CycleAwareSignal":
        """Unsigned greater-than-or-equal (explicit signedness; result is i1)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.uge(b), c)

    def slt(self, other: object) -> "CycleAwareSignal":
        """Signed less-than (explicit signedness; result is i1)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.slt(b), c)

    def sgt(self, other: object) -> "CycleAwareSignal":
        """Signed greater-than (explicit signedness; result is i1)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.sgt(b), c)

    def sle(self, other: object) -> "CycleAwareSignal":
        """Signed less-than-or-equal (explicit signedness; result is i1)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.sle(b), c)

    def sge(self, other: object) -> "CycleAwareSignal":
        """Signed greater-than-or-equal (explicit signedness; result is i1)."""
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.sge(b), c)

    def __lshift__(self, amount: object) -> "CycleAwareSignal":
        if isinstance(amount, int):
            return CycleAwareSignal(self._domain, self._w << amount, self._cycle)
        a, b, c = self._align(amount)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a << b, c)

    def __rshift__(self, amount: object) -> "CycleAwareSignal":
        if isinstance(amount, int):
            return CycleAwareSignal(self._domain, self._w >> amount, self._cycle)
        a, b, c = self._align(amount)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a >> b, c)

    def shl(self, *, amount: object) -> "CycleAwareSignal":
        return self << amount

    def lshr(self, *, amount: object) -> "CycleAwareSignal":
        if isinstance(amount, int):
            return CycleAwareSignal(self._domain, self._w.lshr(amount=amount), self._cycle)
        a, b, c = self._align(amount)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.lshr(amount=b), c)

    def ashr(self, *, amount: object) -> "CycleAwareSignal":
        if isinstance(amount, int):
            return CycleAwareSignal(self._domain, self._w.ashr(amount=amount), self._cycle)
        a, b, c = self._align(amount)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a.ashr(amount=b), c)

    def trunc(self, width: int) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, self._w.trunc(width=int(width)), self._cycle)

    def zext(self, width: int) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, self._w.zext(width=int(width)), self._cycle)

    def sext(self, width: int) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, self._w.sext(width=int(width)), self._cycle)

    def slice(
        self,
        high: int | None = None,
        low: int | None = None,
        *,
        lsb: int | None = None,
        width: int | None = None,
    ) -> CycleAwareSignal:
        """Extract a range using legacy ``high, low`` or Wire-style ``lsb, width``."""
        if lsb is not None or width is not None:
            if high is not None or low is not None or lsb is None or width is None:
                raise TypeError("slice() requires either (high, low) or keyword lsb=..., width=...")
            return CycleAwareSignal(
                self._domain,
                self._w.slice(lsb=int(lsb), width=int(width)),
                self._cycle,
            )
        if high is None or low is None:
            raise TypeError("slice() requires either (high, low) or keyword lsb=..., width=...")
        return CycleAwareSignal(self._domain, self._w[int(low) : int(high) + 1], self._cycle)

    def extract(self, *, lsb: int, width: int) -> "CycleAwareSignal":
        """Extract a scalar bit range or the corresponding range per Vector lane."""
        return self.slice(lsb=lsb, width=width)

    def lane(self, idx: int, *, width: int) -> "CycleAwareSignal":
        """ASL scaled slice ``x[idx *: width]`` — element-granular (keeps cycle)."""
        return CycleAwareSignal(self._domain, self._w.lane(int(idx), width=int(width)), self._cycle)

    def select(self, true_val: object, false_val: object) -> "CycleAwareSignal":
        return mux(self, true_val, false_val)

    def broadcast(self: "CycleAwareSignal[Vector[VT]]", *, size: int, dim: int) -> "CycleAwareSignal[Vector[Data]]":
        return CycleAwareSignal(
            self.domain,
            self._w.broadcast(size=int(size), dim=int(dim)),
            self.cycle,
        )

    def reduce_or(
        self: "CycleAwareSignal[Vector[Data]]",
        *,
        dim: int | None = None,
        mode: str = "chain",
    ) -> "CycleAwareSignal":
        return CycleAwareSignal(
            self.domain,
            self._w.reduce_or(dim=dim, mode=mode),
            self.cycle,
        )

    def reduce_and(
        self: "CycleAwareSignal[Vector[Data]]",
        *,
        dim: int | None = None,
        mode: str = "chain",
    ) -> "CycleAwareSignal":
        return CycleAwareSignal(
            self.domain,
            self._w.reduce_and(dim=dim, mode=mode),
            self.cycle,
        )

    def reduce_sum(
        self: "CycleAwareSignal[Vector[Data]]",
        *,
        dim: int | None = None,
        mode: str = "chain",
    ) -> "CycleAwareSignal":
        return CycleAwareSignal(
            self.domain,
            self._w.reduce_sum(dim=dim, mode=mode),
            self.cycle,
        )

    def priority_mux(
        self: "CycleAwareSignal[Vector[Data]]",
        vals: "Wire | CycleAwareSignal | StateSignal | ForwardSignal",
        *,
        mode: str = "chain",
        default: "Wire | CycleAwareSignal | StateSignal | ForwardSignal | None" = None,
    ) -> "CycleAwareSignal":
        """Select a vector lane after aligning selector, values, and default.

        ``vals`` and ``default`` may be any cycle-aware signal flavor
        (:class:`CycleAwareSignal`, :class:`StateSignal`, :class:`ForwardSignal`)
        or a plain :class:`Wire`; non-cycle-aware scalars (``Reg`` / ``int`` /
        :class:`LiteralValue`) are rejected to stay consistent with the
        ``as_cas``-anchored contract documented in
        ``docs/v5_drop_to_cas_or_none_plan.md``.
        """
        if isinstance(vals, (CycleAwareSignal, StateSignal, ForwardSignal)):
            vals_cas = CycleAwareSignal.as_cas(vals)
            if vals_cas._domain is not self._domain:
                raise ValueError("priority_mux values must share the selector domain")
            vals_w = vals_cas._w
            vals_cycle = vals_cas._cycle
        elif isinstance(vals, Wire):
            vals_w = vals
            vals_cycle = self.domain.cycle_index
        else:
            raise TypeError("priority_mux vals must be Wire or CycleAwareSignal")

        if default is None:
            default_w = None
            default_cycle = self.cycle
        elif isinstance(default, (CycleAwareSignal, StateSignal, ForwardSignal)):
            default_cas = CycleAwareSignal.as_cas(default)
            if default_cas._domain is not self._domain:
                raise ValueError("priority_mux default must share the selector domain")
            default_w = default_cas._w
            default_cycle = default_cas._cycle
        elif isinstance(default, Wire):
            default_w = default
            default_cycle = self.domain.cycle_index
        else:
            raise TypeError("priority_mux default must be Wire, CycleAwareSignal, or None")

        target_cycle = max(self.cycle, vals_cycle, default_cycle)
        sels_w = self.domain.delay_to(
            self._w,
            from_cycle=self.cycle,
            to_cycle=target_cycle,
            width=self._w.width,
        )
        vals_w = self.domain.delay_to(
            vals_w,
            from_cycle=vals_cycle,
            to_cycle=target_cycle,
            width=vals_w.width,
        )
        if default_w is not None:
            default_w = self.domain.delay_to(
                default_w,
                from_cycle=default_cycle,
                to_cycle=target_cycle,
                width=default_w.width,
            )
        return CycleAwareSignal(
            self.domain,
            self._domain._m.priority_mux(sels_w, vals_w, mode=mode, default=default_w),
            target_cycle,
        )

    def as_signed(self) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, Wire(self._domain._m, self._w.sig, signed=True), self._cycle)

    def as_unsigned(self) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, Wire(self._domain._m, self._w.sig, signed=False), self._cycle)

    def matches(self, pattern: str) -> "CycleAwareSignal":
        """ASL-style bit-mask match: ``(self & mask) == value`` (i1, same cycle)."""
        from .bitmask import parse_bitmask_checked

        mask, value = parse_bitmask_checked(pattern, width=self._w.width)
        return (self & mask) == value

    def in_(self, *patterns: "str | Iterable[str]") -> "CycleAwareSignal":
        """True iff any pattern matches (OR-reduction of :meth:`matches`).

        Accepts varargs (``in_("1010", "1100")``) or a single iterable
        (``in_(["1010", "1100"])``).
        """
        from .bitmask import normalize_patterns

        pats = normalize_patterns(patterns)
        result = self.matches(pats[0])
        for p in pats[1:]:
            result = result | self.matches(p)
        return result

    def not_in_(self, *patterns: "str | Iterable[str]") -> "CycleAwareSignal":
        """True iff no pattern matches (ASL ``IN !{...}``)."""
        return ~self.in_(*patterns)

    def as_(
        self,
        *args: "int | Iterable[int]",
        width: int | None = None,
        range: tuple[int, int] | None = None,
        values: "Iterable[int] | None" = None,
        msg: str | None = None,
    ) -> "CycleAwareSignal":
        """ASL ``expression as ty`` — checked cast (preserves cycle). Exactly one
        of positional value(s) / ``values=[...]`` (value assertion, returns self),
        ``width=`` (narrowing + trunc), or ``range=(lo, hi)``. See :meth:`Wire.as_`.
        """
        from .hw import _normalize_as_values

        val_set = _normalize_as_values(args, values)
        given = [n for n, v in (("width", width), ("range", range), ("values", val_set)) if v is not None]
        if len(given) != 1:
            raise TypeError(
                "as_ requires exactly one of: positional value(s)/values=, width=, or range="
            )
        if val_set is not None:
            return self.assert_in(val_set, msg=msg)
        if width is not None:
            w = int(width)
            if w <= 0:
                raise ValueError("as_(width=) must be > 0")
            if w > self._w.width:
                raise ValueError(
                    f"as_(width={w}) cannot widen a {self._w.width}-bit value; use zext/sext"
                )
            if w == self._w.width:
                return self
            high = self._w[w:self._w.width]
            cond = high == 0
            self._domain._m.assert_(cond, msg=msg or f"as_: value does not fit in {w} bits")
            return CycleAwareSignal(self._domain, self._w.trunc(width=w), self._cycle)
        lo, hi = range  # type: ignore[misc]
        return self.assert_range(lo, hi, msg=msg)

    def assert_fits(self, *, width: int, msg: str | None = None) -> "CycleAwareSignal":
        """Alias for ``as_(width=..)``."""
        return self.as_(width=width, msg=msg)

    def assert_range(self, lo: int, hi: int, *, msg: str | None = None) -> "CycleAwareSignal":
        """Assert (unsigned) ``lo <= self <= hi``; returns self (cycle kept)."""
        self._w.assert_range(int(lo), int(hi), msg=msg)
        return self

    def assert_in(self, values: "Iterable[int]", *, msg: str | None = None) -> "CycleAwareSignal":
        """Assert ``self`` equals one of ``values``; returns self (cycle kept)."""
        self._w.assert_in(values, msg=msg)
        return self

    def __getitem__(self, idx: int | slice | tuple) -> "CycleAwareSignal":
        # tuple ``x[i, w]`` is ASL scaled-slice sugar for ``x.lane(i, width=w)``;
        # delegated to the underlying Wire, keeping the cycle tag.
        return CycleAwareSignal(self._domain, self._w[idx], self._cycle)


def _promote_pair(m: Circuit, a: Wire, b: Wire) -> tuple[Wire, Wire]:
    if a.width == b.width:
        return a, b
    out_w = max(a.width, b.width)
    if a.width < out_w:
        a = a.sext(width=out_w) if a.signed else a.zext(width=out_w)
    if b.width < out_w:
        b = b.sext(width=out_w) if b.signed else b.zext(width=out_w)
    return a, b


@overload
def mux(cond: Wire, a: Union[Wire, int], b: Union[Wire, int]) -> Wire: ...


@overload
def mux(cond: Union[CycleAwareSignal, StateSignal, ForwardSignal], a: Union[Wire, int, CycleAwareSignal, StateSignal, ForwardSignal], b: Union[Wire, int, CycleAwareSignal, StateSignal, ForwardSignal]) -> CycleAwareSignal: ...


@overload
def mux(cond: Wire, a: Union[CycleAwareSignal, StateSignal, ForwardSignal], b: Union[Wire, int, CycleAwareSignal, StateSignal, ForwardSignal]) -> CycleAwareSignal: ...


@overload
def mux(cond: Wire, a: Union[Wire, int, CycleAwareSignal, StateSignal, ForwardSignal], b: Union[CycleAwareSignal, StateSignal, ForwardSignal]) -> CycleAwareSignal: ...


def mux(
    cond: Union[Wire, CycleAwareSignal, StateSignal, ForwardSignal],
    a: Union[Wire, CycleAwareSignal, StateSignal, ForwardSignal, int],
    b: Union[Wire, CycleAwareSignal, StateSignal, ForwardSignal, int],
) -> Wire | CycleAwareSignal:
    def _unwrap(v: Union[Wire, CycleAwareSignal, StateSignal, ForwardSignal]) -> Union[Wire, CycleAwareSignal]:
        if isinstance(v, ForwardSignal):
            return v._state._cas
        if isinstance(v, StateSignal):
            return v._cas
        return v
    raw_cond = _unwrap(cond)
    raw_a = a if isinstance(a, int) else _unwrap(a)
    raw_b = b if isinstance(b, int) else _unwrap(b)
    if not any(isinstance(value, CycleAwareSignal) for value in (raw_cond, raw_a, raw_b)):
        raw_cond = cast(Wire, raw_cond)
        if raw_cond.width != 1:
            raise TypeError(f"mux() condition must be i1, got {raw_cond.ty}")
        return raw_cond._select_internal(
            cast(Wire | int, raw_a),
            cast(Wire | int, raw_b),
        )
    return _mux_cycle_aware(raw_cond, raw_a, raw_b)


def _mux_cycle_aware(
    cond: Union[Wire, Reg, CycleAwareSignal],
    a: Union[Wire, Reg, CycleAwareSignal, int, LiteralValue],
    b: Union[Wire, Reg, CycleAwareSignal, int, LiteralValue],
) -> CycleAwareSignal:
    def pick_dom() -> CycleAwareDomain:
        for x in (cond, a, b):
            if isinstance(x, CycleAwareSignal):
                return x._domain
        raise RuntimeError("internal: mux cycle-aware without CycleAwareSignal")

    dom = pick_dom()
    m = dom._m

    def to_cas(x: Union[Wire, Reg, CycleAwareSignal, int, LiteralValue]) -> CycleAwareSignal:
        if isinstance(x, CycleAwareSignal):
            return x
        if isinstance(x, Reg):
            return CycleAwareSignal(dom, x.q, dom.cycle_index)
        if isinstance(x, Wire):
            return CycleAwareSignal(dom, x, dom.cycle_index)
        if isinstance(x, int):
            w = m.const(x, width=max(1, infer_literal_width(x, signed=x < 0)))
            return CycleAwareSignal(dom, w, dom.cycle_index)
        if isinstance(x, LiteralValue):
            lw = x.width if x.width is not None else infer_literal_width(int(x.value), signed=bool(x.signed))
            w = m.const(int(x.value), width=int(lw))
            return CycleAwareSignal(dom, w, dom.cycle_index)
        raise TypeError(f"mux: unsupported value {type(x).__name__}")

    c_cas = to_cas(cond) if not isinstance(cond, CycleAwareSignal) else cond
    ca = to_cas(a)
    cb = to_cas(b)
    cc = c_cas._cycle
    cw = c_cas._w
    mx = max(cc, ca._cycle, cb._cycle)
    cw2 = dom.delay_to(cw, from_cycle=cc, to_cycle=mx, width=cw.width)
    aw = dom.delay_to(ca._w, from_cycle=ca._cycle, to_cycle=mx, width=ca._w.width)
    bw = dom.delay_to(cb._w, from_cycle=cb._cycle, to_cycle=mx, width=cb._w.width)
    aw, bw = _promote_pair(m, aw, bw)
    if cw2.width != 1:
        raise TypeError("mux condition must be i1")
    out_w = cw2._select_internal(aw, bw)
    return CycleAwareSignal(dom, out_w, mx)


def priority_mux(
    sels: Union[Wire, CycleAwareSignal, StateSignal, ForwardSignal],
    vals: Union[Wire, CycleAwareSignal, StateSignal, ForwardSignal],
    *,
    mode: str = "chain",
    default: Union[Wire, CycleAwareSignal, StateSignal, ForwardSignal, None] = None,
) -> CycleAwareSignal:
    """Cycle-aware wrapper for :meth:`Circuit.priority_mux`.

    Aligns *sels*, *vals*, and *default* to the latest logical cycle before
    invoking the underlying ``pyc.priority_mux`` op.  Each operand is coerced via
    :meth:`CycleAwareSignal.as_cas`; at least one operand must be cycle-aware
    (``CycleAwareSignal`` / ``StateSignal`` / ``ForwardSignal``) to anchor the
    domain.  Scalar operands (``Wire`` / ``Reg`` / ``int``) without a cycle-aware
    anchor cause :meth:`as_cas` itself to raise ``TypeError``.
    """
    dom = None
    for _v in (sels, vals, default):
        if _v is not None and CycleAwareSignal.is_cas(_v):
            dom = CycleAwareSignal.as_cas(_v).domain
            break
    if dom is None:
        raise TypeError(
            "cat/priority_mux: at least one operand must be cycle-aware "
            "(CycleAwareSignal / StateSignal / ForwardSignal) to anchor the domain"
        )
    sels_cas = CycleAwareSignal.as_cas(sels, domain=dom)
    vals_cas = CycleAwareSignal.as_cas(vals, domain=dom)
    default_cas = (
        CycleAwareSignal.as_cas(default, domain=dom) if default is not None else None
    )
    return sels_cas.priority_mux(vals_cas, mode=mode, default=default_cas)


def cat(*elems: Union[Wire, Reg, CycleAwareSignal, StateSignal, ForwardSignal, int]) -> CycleAwareSignal:
    """Concatenate values into a packed bus (MSB-first).

    Cycle-aware variant of :func:`pycircuit.cat`: every element is coerced via
    :meth:`CycleAwareSignal.as_cas`, aligned to the latest logical cycle, and
    the result keeps the cycle tag.  At least one element must be cycle-aware
    (``CycleAwareSignal`` / ``StateSignal`` / ``ForwardSignal``) to anchor the
    domain; otherwise :meth:`as_cas` raises ``TypeError`` when trying to promote
    a scalar (``Wire`` / ``Reg`` / ``int``) without a domain.
    """
    if not elems:
        raise ValueError("cat() requires at least one element")
    dom = None
    for _v in elems:
        if CycleAwareSignal.is_cas(_v):
            dom = CycleAwareSignal.as_cas(_v).domain
            break
    if dom is None:
        raise TypeError(
            "cat/priority_mux: at least one operand must be cycle-aware "
            "(CycleAwareSignal / StateSignal / ForwardSignal) to anchor the domain"
        )
    cas_elems = [CycleAwareSignal.as_cas(e, domain=dom) for e in elems]
    target_cycle = 0
    for c in cas_elems:
        target_cycle = max(target_cycle, c.cycle)
    aligned: list[Wire] = []
    for c in cas_elems:
        w = _to_wire(c)
        aligned.append(dom.delay_to(w, from_cycle=c.cycle, to_cycle=target_cycle, width=w.width))
    packed = dom._m.cat(*aligned)
    return CycleAwareSignal(dom, packed, target_cycle)


def cas(
    domain: CycleAwareDomain,
    w: Wire | Reg | int | LiteralValue,
    *,
    cycle: int | None = None,
) -> CycleAwareSignal:
    """Wrap a scalar value as a :class:`CycleAwareSignal` at *domain*'s cycle.

    Intentionally accepts only scalar inputs (``Wire`` / ``Reg`` / ``int`` /
    :class:`LiteralValue`); cycle-aware values are returned unchanged by
    :meth:`CycleAwareSignal.as_cas` instead.
    """
    if isinstance(w, (CycleAwareSignal, StateSignal, ForwardSignal)):
        raise TypeError(f"cas() expects Wire, Reg, int, or LiteralValue; got {type(w).__name__} (use CycleAwareSignal.as_cas)")
    return CycleAwareSignal.as_cas(w, domain=domain, cycle=cycle)


def _strip_domain_for_jit(fn: Callable[..., Any], *, domain_name: str) -> Callable[..., Any]:
    """Drop the ``domain`` parameter for JIT and prepend ``domain = m.create_domain(...)``."""
    try:
        source = textwrap.dedent(inspect.getsource(fn))
    except OSError as e:
        raise TypeError(
            "compile_cycle_aware(fn): need inspectable source for JIT; use eager=True or define fn in a .py file"
        ) from e
    tree = ast.parse(source)
    name = getattr(fn, "__name__", None)
    if not isinstance(name, str) or not name:
        raise TypeError("compile_cycle_aware(fn): function must have a __name__")
    fdef: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            fdef = node
            break
    if fdef is None:
        raise TypeError(f"compile_cycle_aware: could not find def {name!r} in source of {fn!r}")
    pos = fdef.args.args
    if len(pos) < 2:
        raise TypeError("compile_cycle_aware(fn): source must declare at least (m, domain, ...)")
    m_arg = pos[0].arg
    if pos[1].arg != "domain":
        raise TypeError(
            "compile_cycle_aware(fn): second parameter must be named 'domain' for JIT (or use eager=True)"
        )
    fdef.args.args.pop(1)
    prelude = ast.Assign(
        targets=[ast.Name(id="domain", ctx=ast.Store())],
        value=ast.Call(
            func=ast.Attribute(
                value=ast.Name(id=m_arg, ctx=ast.Load()),
                attr="create_domain",
                ctx=ast.Load(),
            ),
            args=[ast.Constant(value=str(domain_name))],
            keywords=[],
        ),
    )
    fdef.body.insert(0, prelude)
    ast.fix_missing_locations(fdef)
    new_src = ast.unparse(fdef) + "\n"
    globs = dict(fn.__globals__)
    exec(compile(ast.parse(new_src), "<pycircuit_v5_strip_domain>", "exec"), globs)
    out: Callable[..., Any] = globs[name]
    out.__pycircuit_jit_source__ = new_src
    out.__pycircuit_jit_start_line__ = 1
    out.__pycircuit_jit_source_file__ = "<pycircuit_v5_strip_domain>"
    setattr(out, "__pycircuit_kind__", "module")
    setattr(out, "__pycircuit_inline__", False)
    for attr in ("__pycircuit_name__", "__pycircuit_module_name__"):
        if hasattr(fn, attr):
            setattr(out, attr, getattr(fn, attr))
    return out


def compile_cycle_aware(
    fn: F,
    *,
    name: str | None = None,
    domain_name: str = "clk",
    eager: bool = False,
    hierarchical: bool = False,
    structural: bool | None = None,
    value_params: Mapping[str, str] | dict[str, str] | None = None,
    design_ctx: Any | None = None,
    **jit_params: Any,
) -> Any:
    """Compile or execute ``fn(m, domain, **kwargs)``.

    By default this lowers through :func:`pycircuit.jit.compile`: a tiny ``@module``-style
    wrapper instantiates :class:`CycleAwareDomain` from ``domain_name`` and calls ``fn``.
    Pass ``eager=True`` to run ``fn`` directly in Python and get a
    :class:`CycleAwareCircuit` (no JIT; no ``if Wire`` / JIT control flow).

    When ``hierarchical=True`` (requires ``eager=True``), each ``domain.call()``
    boundary is preserved: sub-modules are compiled as separate ``func.func``
    MLIR ops and instantiated via ``pyc.instance``.  The returned circuit's
    ``emit_mlir()`` emits a multi-module ``Design``.
    """
    if eager:
        circuit_name = name if isinstance(name, str) and name.strip() else getattr(fn, "__name__", "design") or "design"
        m = CycleAwareCircuit(str(circuit_name), design_ctx=design_ctx)
        dom = m.create_domain(str(domain_name))

        if hierarchical:
            from .design import Design
            design = Design(top=str(circuit_name))
            dom._hierarchical = True
            dom._design = design
            dom._sub_cache = {}

        out = fn(m, dom, **jit_params)
        if out is not None:
            _register_implicit_outputs(m, out)

        if hierarchical:
            cm = _make_compiled_module(fn, m, str(circuit_name))
            design.add(cm)
            m._v5_design = design

        return m

    from .jit import compile as jit_compile

    if name is None or not str(name).strip():
        override = getattr(fn, "__pycircuit_name__", None)
        if isinstance(override, str) and override.strip():
            sym = override.strip()
        else:
            sym = getattr(fn, "__name__", "Top")
    else:
        sym = str(name).strip()

    struc = bool(getattr(fn, "__pycircuit_emit_structural__", False)) if structural is None else bool(structural)

    if value_params is None:
        vp_raw = getattr(fn, "__pycircuit_value_params__", None)
        vp: dict[str, str] = dict(vp_raw) if isinstance(vp_raw, dict) else {}
    else:
        vp = dict(value_params)

    domain_n = str(domain_name)

    _jit_fn = _strip_domain_for_jit(fn, domain_name=domain_n)
    setattr(_jit_fn, "__pycircuit_module_name__", sym)
    setattr(_jit_fn, "__pycircuit_kind__", "module")
    setattr(_jit_fn, "__pycircuit_inline__", False)
    setattr(_jit_fn, "__pycircuit_emit_structural__", struc)
    setattr(_jit_fn, "__pycircuit_value_params__", vp)
    pn = getattr(fn, "__pycircuit_name__", None)
    if isinstance(pn, str) and pn.strip():
        setattr(_jit_fn, "__pycircuit_name__", pn.strip())
    else:
        setattr(_jit_fn, "__pycircuit_name__", sym)

    return jit_compile(_jit_fn, name=name, **jit_params)


def _register_implicit_outputs(m: Circuit, out: Any) -> None:
    if isinstance(out, CycleAwareSignal):
        m.output("result", out._w)
        return
    if isinstance(out, Wire):
        m.output("result", out)
        return
    if isinstance(out, Reg):
        m.output("result", out.q)
        return
    if isinstance(out, tuple):
        for i, x in enumerate(out):
            _register_implicit_outputs_single(m, f"result{i}", x)
        return
    _register_implicit_outputs_single(m, "result", out)


def _register_implicit_outputs_single(m: Circuit, port: str, x: Any) -> None:
    if isinstance(x, CycleAwareSignal):
        m.output(port, x._w)
    elif isinstance(x, Wire):
        m.output(port, x)
    elif isinstance(x, Reg):
        m.output(port, x.q)


class pyc_CircuitModule:
    """Tutorial-style module base (hierarchy + with self.module(...))."""

    def __init__(self, name: str, clock_domain: CycleAwareDomain) -> None:
        self.name = str(name)
        self.clock_domain = clock_domain
        self._m = clock_domain.circuit

    @property
    def circuit(self) -> CycleAwareCircuit:
        return self._m

    @contextmanager
    def module(
        self,
        *,
        inputs: list[Any] | None = None,
        description: str = "",
    ) -> Iterator[_ModuleCtx]:
        _ = description
        ctx = _ModuleCtx(self, list(inputs or []), description)
        prev = _current_domain()
        _set_current_domain(self.clock_domain)
        try:
            with self._m.scope(self.name):
                yield ctx
        finally:
            _set_current_domain(prev)
        for out in ctx.outputs:
            _ = out


# Tutorial aliases
pyc_ClockDomain = CycleAwareDomain
pyc_Signal = CycleAwareSignal


class pyc_CircuitLogger:
    """Minimal hierarchical text logger (tutorial compatibility)."""

    def __init__(self, filename: str, is_flatten: bool = False) -> None:
        self.filename = str(filename)
        self.is_flatten = bool(is_flatten)
        self._lines: list[str] = []

    def reset(self) -> None:
        self._lines.clear()

    def write_to_file(self) -> None:
        with open(self.filename, "w", encoding="utf-8") as f:
            f.write("\n".join(self._lines))


def log(value: Any) -> Any:
    return value


class _SignalSlice:
    def __init__(self, high: int, low: int) -> None:
        self.high = int(high)
        self.low = int(low)
        self.width = self.high - self.low + 1

    def __call__(self, *, value: Any = 0, name: str = "") -> CycleAwareSignal:
        dom = _current_domain()
        if dom is None:
            raise RuntimeError("signal[...](...) requires an active pyc_CircuitModule.module() context")
        w = _materialize_signal_value(dom, value, self.width, str(name))
        return CycleAwareSignal(dom, w, dom.cycle_index)


class _SignalMeta(type):
    def __getitem__(cls, item: Any) -> _SignalSlice:
        if isinstance(item, slice):
            if item.step not in (None, 1):
                raise ValueError("signal slice step must be 1")
            hi, lo = item.start, item.stop
            if hi is None or lo is None:
                raise ValueError("signal[h:l] requires both high and low")
            return _SignalSlice(int(hi), int(lo))
        if isinstance(item, str):
            part = item.split(":", 1)
            if len(part) != 2:
                raise ValueError('signal["h:l"] expects one ":"')
            return _SignalSlice(int(part[0].strip()), int(part[1].strip()))
        raise TypeError("signal[...] expects slice like [7:0] or string '7:0'")

    def __call__(cls, *, value: Any = 0, name: str = "") -> CycleAwareSignal:
        if cls is signal:
            return _signal_plain(value=value, name=name)
        return type.__call__(cls)


class signal(metaclass=_SignalMeta):
    """Tutorial: ``signal[7:0](value=0) | \"desc\"`` and ``signal(value=...)``."""


def _signal_plain(*, value: Any = 0, name: str = "") -> CycleAwareSignal:
    dom = _current_domain()
    if dom is None:
        raise RuntimeError("signal(value=...) requires an active pyc_CircuitModule.module() context")
    w = _materialize_signal_value(dom, value, None, str(name))
    return CycleAwareSignal(dom, w, dom.cycle_index)


def _materialize_signal_value(dom: CycleAwareDomain, value: Any, width: int | None, name: str) -> Wire:
    m = dom._m
    if isinstance(value, int):
        w = infer_literal_width(int(value), signed=(int(value) < 0)) if width is None else int(width)
        return m.const(int(value), width=w)
    if isinstance(value, str):
        base = str(value).strip()
        if base.isidentifier():
            guess = 8 if width is None else int(width)
            return m.input(base, width=guess)
        return m.named_wire(dom._m.scoped_name(name or "sig"), width=int(width or 8))
    if isinstance(value, Wire):
        return value
    raise TypeError(f"unsupported signal value: {type(value).__name__}")


# ---------------------------------------------------------------------------
# V5 Cycle-Aware Testbench wrapper
# ---------------------------------------------------------------------------

class CycleAwareTb:
    """V5 cycle-aware testbench wrapper.

    Wraps :class:`Tb` so that ``drive`` / ``expect`` / ``finish`` calls use the
    current cycle tracked by :meth:`next` instead of an explicit ``at=``
    parameter, mirroring ``domain.next()`` in design code.

    When *circuit* is provided at construction (or via :meth:`bind`),
    vector ports can be driven / expected as plain Python lists::

        tb = CycleAwareTb(t, circuit=m)
        tb.drive("dp_in_pdest", [1, 4])          # auto-packed to leaf_w from port def
        tb.expect("dp_iq_int_valid", [1, 0])

    Lane 0 maps to the LSB of the packed integer; leaf width and lane count
    are looked up from the compiled circuit's port table. Plain ``int`` /
    ``bool`` values are passed through unchanged (scalar ports, or pre-packed
    integers for backward compatibility).

    Usage inside a ``@testbench`` function::

        @testbench
        def tb(t: Tb) -> None:
            tb = CycleAwareTb(t)
            tb.clock("clk")
            tb.reset("rst", cycles_asserted=2, cycles_deasserted=1)
            tb.timeout(64)

            # --- cycle 0 ---
            tb.drive("enable", 1)
            tb.expect("count", 1)

            tb.next()  # --- cycle 1 ---
            tb.expect("count", 2)

            tb.finish()
    """

    __slots__ = ("_t", "_cycle", "_leaf_widths")

    def __init__(self, t: _Tb, *, circuit: Any = None) -> None:
        if not isinstance(t, _Tb):
            raise TypeError(
                f"CycleAwareTb requires a Tb instance, got {type(t).__name__}"
            )
        self._t = t
        self._cycle = 0
        self._leaf_widths: dict[str, int] = {}
        if circuit is not None:
            self.bind(circuit)

    # -- circuit binding (for list-form drive/expect) -----------------------

    def bind(self, circuit: Any) -> None:
        """Attach a compiled circuit so vector ports accept list values.

        Builds an internal ``{port_name: leaf_width}`` map from the circuit's
        input (``_args``) and output (``_results``) port tables.  Only ports
        whose type is :class:`Vector` are recorded; scalar ports stay on the
        plain int/bool path.
        """
        widths: dict[str, int] = {}
        for source in (getattr(circuit, "_args", []), getattr(circuit, "_results", [])):
            for entry in source or []:
                try:
                    name, sig = entry
                except (ValueError, TypeError):
                    continue
                ty = getattr(sig, "ty", None)
                # Vector has .datatype(); Bits has .width directly.
                if ty is None:
                    continue
                leaf = ty.datatype() if hasattr(ty, "datatype") else ty
                w = getattr(leaf, "width", None)
                if isinstance(w, int) and w > 0:
                    widths[str(name)] = int(w)
        self._leaf_widths = widths

    def _leaf_width_of(self, port: str) -> int | None:
        """Return the leaf width of *port* if it is a known vector port."""
        return self._leaf_widths.get(str(port).strip())

    # -- cycle management ---------------------------------------------------

    def next(self) -> None:
        """Advance to the next clock cycle (like ``domain.next()``)."""
        self._cycle += 1

    @property
    def cycle(self) -> int:
        """Current cycle index."""
        return self._cycle

    # -- setup (cycle-independent) ------------------------------------------

    def clock(self, port: str, **kw: Any) -> None:
        self._t.clock(port, **kw)

    def reset(self, port: str, **kw: Any) -> None:
        self._t.reset(port, **kw)

    def timeout(self, cycles: int) -> None:
        self._t.timeout(cycles)

    # -- stimulus / check (cycle-relative) ----------------------------------

    def drive(self, port: str, value: "int | bool | list[int] | tuple[int, ...]") -> None:
        """Drive *port* at the current cycle.

        - ``int`` / ``bool``: passed straight through (scalar port, or a
          pre-packed integer for a vector port).
        - ``list[int]`` / ``tuple[int, ...]``: requires :meth:`bind` to have
          been called; the lane values are packed into a single integer with
          lane 0 at the LSB using the port's leaf width.
        """
        packed = self._coerce_value(port, value)
        self._t.drive(port, packed, at=self._cycle)

    def expect(
        self,
        port: str,
        value: "int | bool | list[int] | tuple[int, ...]",
        *,
        phase: str = "post",
        msg: str | None = None,
    ) -> None:
        """Check *port* at the current cycle.  See :meth:`drive` for value forms."""
        packed = self._coerce_value(port, value)
        self._t.expect(port, packed, at=self._cycle, phase=phase, msg=msg)

    def _coerce_value(self, port: str, value: "int | bool | list[int] | tuple[int, ...]") -> int:
        """Translate a drive/expect value into the integer the backend expects."""
        if isinstance(value, (bool, int)):
            return int(value)
        if isinstance(value, (list, tuple)):
            lanes = [int(x) for x in value]
            leaf_w = self._leaf_width_of(port)
            if leaf_w is None:
                raise TbError(
                    f"port {port!r}: list value requires CycleAwareTb(circuit=...) to be set "
                    f"(unknown port); pass an int, or bind the compiled circuit"
                )
            return _pack_lanes(leaf_w, lanes)
        raise TypeError(
            f"port {port!r}: drive/expect value must be int/bool/list[int], got {type(value).__name__}"
        )

    def finish(self, *, at: int | None = None) -> None:
        """End the simulation at the current cycle (or at an explicit cycle)."""
        self._t.finish(at=self._cycle if at is None else int(at))

    # -- print helpers ------------------------------------------------------

    def print(self, fmt: str, *, ports: Iterable[str] = ()) -> None:
        """Print at the current cycle."""
        self._t.print(fmt, at=self._cycle, ports=ports)

    def print_every(self, fmt: str, **kw: Any) -> None:
        self._t.print_every(fmt, **kw)

    # -- pass-through -------------------------------------------------------

    def sva_assert(self, expr: Any, **kw: Any) -> None:
        self._t.sva_assert(expr, **kw)

    def random(self, port: str, **kw: Any) -> None:
        self._t.random(port, **kw)


def _pack_lanes(leaf_w: int, lanes: list[int]) -> int:
    """Pack lane values into a single integer (lane 0 at LSB).

    Matches the backend's packed-bus convention for ``vector<NxiW>`` ports:
    lane ``i`` occupies bits ``[i*W : (i+1)*W-1]``.
    """
    if leaf_w <= 0:
        raise ValueError(f"_pack_lanes: leaf_w must be > 0, got {leaf_w}")
    v = 0
    mask = (1 << leaf_w) - 1
    for i, x in enumerate(lanes):
        v |= (int(x) & mask) << (i * leaf_w)
    return v


