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
from typing import Any, Callable, Iterable, Iterator, Mapping, TypeVar, Union

from .dsl import Signal
from .hw import Circuit, ClockDomain, Reg, Vec, Wire
from .literals import LiteralValue, infer_literal_width
from .tb import Tb as _Tb

F = TypeVar("F", bound=Callable[..., Any])

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

    def const_signal(self, value: int, width: int, domain: "CycleAwareDomain") -> Wire:
        return domain.create_const(int(value), width=int(width))

    def input_signal(self, name: str, width: int, domain: "CycleAwareDomain") -> Wire:
        return domain.create_signal(str(name), width=int(width))


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

    def create_signal(self, port_name: str, *, width: int) -> Wire:
        return self._m.input(str(port_name), width=int(width))

    def create_const(self, value: int, *, width: int, name: str = "") -> Wire:
        _ = name
        return self._m.const(int(value), width=int(width))

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
        reset_value: int | None = None,
        name: str = "",
    ) -> Wire:
        """Single-stage register (DFF); output is one logical cycle after the input value."""
        w = _as_wire(self._m, sig)
        width = w.width
        init = 0 if reset_value is None else int(reset_value)
        reg_name = str(name).strip() or f"_v5_reg_{self._reg_serial}"
        self._reg_serial += 1
        full = self._m.scoped_name(reg_name)
        r = self._m.out(full, domain=self._cd, width=width, init=init)
        r.set(w)
        return r.q

    def _state(
        self,
        *,
        width: int,
        reset_value: int = 0,
        name: str = "",
    ) -> "StateSignal":
        """Internal: create a feedback register. Use ``domain.signal()`` instead."""
        reg_name = str(name).strip() or f"_v5_reg_{self._reg_serial}"
        self._reg_serial += 1
        full = self._m.scoped_name(reg_name)
        reg = self._m.out(full, domain=self._cd, width=int(width), init=int(reset_value))
        return StateSignal(self, reg, self._occurrence)

    def signal(
        self,
        *,
        width: int,
        reset_value: int = 0,
        name: str = "",
    ) -> "ForwardSignal":
        """Declare a forward-declared register with ``<<=`` / ``.assign()`` syntax.

        Returns a :class:`ForwardSignal` whose Q output is immediately usable
        in expressions.  The D input is connected later via::

            sig <<= next_val          # unconditional
            sig.assign(next_val, when=cond)  # conditional

        This is sugar over :meth:`state` with a more ergonomic write syntax.
        """
        st = self._state(width=width, reset_value=reset_value, name=name)
        return ForwardSignal(st)

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
                if isinstance(actual, Vec):
                    info = actual._as_vector_signal()
                    if info is None:
                        raise TypeError(f"input {port_name!r} Vec cannot be converted to a vector signal")
                    _actual_m, actual_sig, _actual_signs = info
                else:
                    actual_sig = _to_wire(actual).sig

                if actual_sig.ty != port_sig.ty and actual_sig.ty.startswith("i") and port_sig.ty.startswith("i"):
                    actual_w = int(actual_sig.ty[1:])
                    expect_w = int(port_sig.ty[1:])
                    w = Wire(self._m, actual_sig)
                    if actual_w < expect_w:
                        w = w._zext(width=expect_w)
                    else:
                        w = w._trunc(width=expect_w)
                    actual_sig = w.sig
                if actual_sig.ty != port_sig.ty:
                    raise TypeError(f"input {port_name!r} type mismatch: actual {actual_sig.ty} != expected {port_sig.ty}")
                input_sigs.append(actual_sig)
            else:
                if port_sig.ty.startswith("vector<"):
                    shape, elem_ty = Vec._vector_shape_elem_type(port_sig.ty)
                    width = int(elem_ty[1:])
                elif port_sig.ty.startswith("i"):
                    shape = None
                    width = int(port_sig.ty[1:])
                else:
                    shape = None
                    width = 1
                if port_name.startswith(canonical_prefix + "_"):
                    suffix = port_name[len(canonical_prefix) + 1:]
                    parent_port_name = f"{prefix}_{suffix}"
                else:
                    parent_port_name = f"{prefix}_{port_name}"
                parent_value = self._m.input(parent_port_name, width=width, shape=shape)
                if isinstance(parent_value, Vec):
                    info = parent_value._as_vector_signal()
                    if info is None:
                        raise TypeError(f"input {port_name!r} parent Vec cannot be converted to a vector signal")
                    _parent_m, parent_sig, _parent_signs = info
                    input_sigs.append(parent_sig)
                else:
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
            if sig.ty.startswith("vector<"):
                shape, _elem_ty = Vec._vector_shape_elem_type(sig.ty)
                out_values.append(Vec._from_vector_signal(self._m, sig, signs=[False for _ in range(shape[0])]))
            else:
                out_values.append(Wire(self._m, sig))
        return _reconstruct_output_dict(out_entries, out_values, self)

    def delay_to(self, w: Wire, *, from_cycle: int, to_cycle: int, width: int) -> Wire:
        """Insert (to_cycle - from_cycle) register stages for automatic cycle balancing."""
        if to_cycle <= from_cycle:
            return w
        d = to_cycle - from_cycle
        cur: Wire = w
        for _ in range(d):
            self._delay_serial += 1
            nm = f"_v5_bal_{self._delay_serial}"
            r = self._m.out(self._m.scoped_name(nm), domain=self._cd, width=width, init=0)
            r.set(cur)
            cur = r.q
        return cur


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
        if isinstance(val, Vec):
            info = val._as_vector_signal()
            if info is None:
                raise TypeError(f"output {key!r} Vec cannot be converted to a vector signal")
            _m, sig, _signs = info
            idx = ref_to_idx.get(sig.ref, -1) if ref_to_idx else -1
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


class StateSignal:
    """Internal feedback register. Created by ``domain._state()`` (private).

    Users should use ``domain.signal()`` which returns a ``ForwardSignal`` instead.
    """

    __slots__ = ("_domain", "_reg", "_cas")

    def __init__(self, domain: "CycleAwareDomain", reg: Reg, cycle: int) -> None:
        self._domain = domain
        self._reg = reg
        self._cas = CycleAwareSignal(domain, reg.out(), cycle)

    def set(
        self,
        next_val: "Wire | Reg | CycleAwareSignal | StateSignal",
        *,
        when: "Wire | Reg | CycleAwareSignal | StateSignal | None" = None,
    ) -> None:
        """Connect the D input of the register (close the feedback loop)."""
        w = _to_wire(next_val)
        wh = _to_wire(when) if when is not None else None
        if wh is not None:
            self._reg.set(w, when=wh)
        else:
            self._reg.set(w)

    @property
    def cycle(self) -> int:
        return self._cas.cycle

    @property
    def domain(self) -> "CycleAwareDomain":
        return self._domain

    def __getattr__(self, name: str) -> object:
        return getattr(self._cas, name)

    def __add__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__add__(other)

    def __radd__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__radd__(other)

    def __sub__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__sub__(other)

    def __mul__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__mul__(other)

    def __and__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__and__(other)

    def __or__(self, other: object) -> "CycleAwareSignal":
        if isinstance(other, str):
            return self._cas
        return self._cas.__or__(other)

    def __xor__(self, other: object) -> "CycleAwareSignal":
        return self._cas.__xor__(other)

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

    def __getitem__(self, idx: int | slice) -> "CycleAwareSignal":
        return self._cas.__getitem__(idx)

    def __repr__(self) -> str:
        return f"StateSignal({self._cas._w}, cycle={self._cas.cycle})"


class ForwardSignal:
    """Forward-declared register signal with ``<<=`` and ``.assign()`` syntax.

    Created by ``domain.signal()``.  The underlying hardware is identical to
    :class:`StateSignal` — a D flip-flop whose Q output is available at the
    declaration cycle and whose D input is connected later.

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

    def __init__(self, state: "StateSignal") -> None:
        self._state = state

    # ── assignment operators ──────────────────────────────────────────

    def __ilshift__(self, next_val: object) -> "ForwardSignal":
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

    # ── arithmetic / logic operators (forward to inner CAS) ──────────

    def __add__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__add__(other)

    def __radd__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__radd__(other)

    def __sub__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__sub__(other)

    def __mul__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__mul__(other)

    def __and__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__and__(other)

    def __or__(self, other: object) -> "CycleAwareSignal":
        if isinstance(other, str):
            return self._state._cas
        return self._state._cas.__or__(other)

    def __xor__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__xor__(other)

    def __invert__(self) -> "CycleAwareSignal":
        return self._state._cas.__invert__()

    def __eq__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        return self._state._cas.__eq__(other)

    def __ne__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        return self._state._cas.__ne__(other)

    def __lt__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__lt__(other)

    def __gt__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__gt__(other)

    def __le__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__le__(other)

    def __ge__(self, other: object) -> "CycleAwareSignal":
        return self._state._cas.__ge__(other)

    def __getitem__(self, idx: int | slice) -> "CycleAwareSignal":
        return self._state._cas.__getitem__(idx)

    def __getattr__(self, name: str) -> object:
        return getattr(self._state._cas, name)

    def __repr__(self) -> str:
        return f"ForwardSignal({self._state._cas._w}, cycle={self._state.cycle})"


def _to_wire(v: "Wire | Reg | CycleAwareSignal | StateSignal | ForwardSignal") -> Wire:
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


class CycleAwareSignal:
    """Value with logical cycle tag; operators align by delaying earlier operands."""

    __slots__ = ("_domain", "_w", "_cycle")

    def __init__(self, domain: CycleAwareDomain, wire: Wire, cycle: int) -> None:
        if wire.m is not domain._m:
            raise ValueError("Wire must belong to the same circuit as the domain")
        self._domain = domain
        self._w = wire
        self._cycle = int(cycle)

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

    def __mul__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a * b, c)

    def __and__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a & b, c)

    def __or__(self, other: object) -> "CycleAwareSignal":  # type: ignore[override]
        if isinstance(other, str):
            _ = other
            return self
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a | b, c)

    def __xor__(self, other: object) -> "CycleAwareSignal":
        a, b, c = self._align(other)  # type: ignore[arg-type]
        return CycleAwareSignal(self._domain, a ^ b, c)

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

    def trunc(self, width: int) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, self._w.trunc(width=int(width)), self._cycle)

    def zext(self, width: int) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, self._w.zext(width=int(width)), self._cycle)

    def sext(self, width: int) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, self._w.sext(width=int(width)), self._cycle)

    def slice(self, high: int, low: int) -> "CycleAwareSignal":
        lo = int(low)
        hi = int(high)
        return CycleAwareSignal(self._domain, self._w[lo : hi + 1], self._cycle)

    def select(self, true_val: object, false_val: object) -> "CycleAwareSignal":
        return mux(self, true_val, false_val)

    def as_signed(self) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, Wire(self._domain._m, self._w.sig, signed=True), self._cycle)

    def as_unsigned(self) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, Wire(self._domain._m, self._w.sig, signed=False), self._cycle)

    def __getitem__(self, idx: int | slice) -> "CycleAwareSignal":
        return CycleAwareSignal(self._domain, self._w[idx], self._cycle)


def _promote_pair(m: Circuit, a: Wire, b: Wire) -> tuple[Wire, Wire]:
    if a.width == b.width:
        return a, b
    out_w = max(a.width, b.width)
    if a.width < out_w:
        a = a._sext(width=out_w) if a.signed else a._zext(width=out_w)
    if b.width < out_w:
        b = b._sext(width=out_w) if b.signed else b._zext(width=out_w)
    return a, b


def _is_cas(v: object) -> bool:
    return isinstance(v, (CycleAwareSignal, StateSignal, ForwardSignal))


def mux(
    cond: Union[Wire, Reg, CycleAwareSignal, StateSignal, ForwardSignal],
    a: Union[Wire, Reg, CycleAwareSignal, StateSignal, ForwardSignal, int, LiteralValue],
    b: Union[Wire, Reg, CycleAwareSignal, StateSignal, ForwardSignal, int, LiteralValue],
) -> Union[Wire, CycleAwareSignal]:
    if _is_cas(cond) or _is_cas(a) or _is_cas(b):
        def _unwrap(v: object) -> object:
            if isinstance(v, ForwardSignal):
                return v._state._cas
            if isinstance(v, StateSignal):
                return v._cas
            return v
        return _mux_cycle_aware(_unwrap(cond), _unwrap(a), _unwrap(b))
    return _mux_wire(cond, a, b)


def _mux_wire(
    cond: Union[Wire, Reg],
    a: Union[Wire, Reg, int, LiteralValue],
    b: Union[Wire, Reg, int, LiteralValue],
) -> Wire:
    c = cond.q if isinstance(cond, Reg) else cond
    m = c.m
    if not isinstance(m, Circuit):
        raise TypeError("mux(cond, ...) requires wires from a Circuit")

    def as_wire(v: Union[Wire, Reg, int, LiteralValue], *, ctx_w: int | None) -> Wire:
        if isinstance(v, Reg):
            return v.q
        if isinstance(v, Wire):
            return v
        if isinstance(v, LiteralValue):
            if v.width is not None:
                lit_w = int(v.width)
            else:
                lit_w = infer_literal_width(
                    int(v.value),
                    signed=(bool(v.signed) if v.signed is not None else int(v.value) < 0),
                )
            return m.const(int(v.value), width=int(lit_w))
        if isinstance(v, int):
            w = ctx_w if ctx_w is not None else max(1, infer_literal_width(int(v), signed=(int(v) < 0)))
            return m.const(int(v), width=int(w))
        raise TypeError(f"mux: unsupported branch type {type(v).__name__}")

    aw = as_wire(a, ctx_w=c.width)
    bw = as_wire(b, ctx_w=c.width)
    aw, bw = _promote_pair(m, aw, bw)
    if c.ty != "i1":
        raise TypeError("mux condition must be i1")
    return c._select_internal(aw, bw)


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
    if cw2.ty != "i1":
        raise TypeError("mux condition must be i1")
    out_w = cw2._select_internal(aw, bw)
    return CycleAwareSignal(dom, out_w, mx)


def cas(domain: CycleAwareDomain, w: Wire, *, cycle: int | None = None) -> CycleAwareSignal:
    c = domain.cycle_index if cycle is None else int(cycle)
    return CycleAwareSignal(domain, w, c)


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

    __slots__ = ("_t", "_cycle")

    def __init__(self, t: _Tb) -> None:
        if not isinstance(t, _Tb):
            raise TypeError(
                f"CycleAwareTb requires a Tb instance, got {type(t).__name__}"
            )
        self._t = t
        self._cycle = 0

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

    def drive(self, port: str, value: int | bool) -> None:
        """Drive *port* at the current cycle."""
        self._t.drive(port, value, at=self._cycle)

    def expect(
        self,
        port: str,
        value: int | bool,
        *,
        phase: str = "post",
        msg: str | None = None,
    ) -> None:
        """Check *port* at the current cycle."""
        self._t.expect(port, value, at=self._cycle, phase=phase, msg=msg)

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


