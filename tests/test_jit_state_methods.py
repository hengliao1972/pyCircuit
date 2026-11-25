"""Gate for the JIT's `_STATE_METHODS` table.

`jit_cache._STATE_METHODS` is a set of *DSL method names*: the front end uses it
to decide whether a function allocates state, which gates both "hardware-carrying
call to an undecorated function" and "@function must not allocate state". Two
kinds of drift are silent there: a listed name that is not a DSL method (dead
entry, can never match) and a memory-allocating DSL method that is missing
(the gate lets it through). Both are pinned here.
"""

from __future__ import annotations

from pycircuit.dsl import Module
from pycircuit.hw import Circuit
from pycircuit.jit_cache import _STATE_METHODS, get_structural_metrics


def test_listed_state_methods_exist() -> None:
    for name in sorted(_STATE_METHODS):
        assert hasattr(Module, name) or hasattr(Circuit, name), (
            f"_STATE_METHODS lists {name!r}, which is neither a Module nor a "
            "Circuit method; the table holds DSL method names, not dialect op names"
        )


def test_memory_methods_count_as_state() -> None:
    def via_sync_mem(m, clk):
        return m.sync_mem(
            clk, ren=1, raddr=2, wvalid=3, waddr=4, wdata=5, wstrb=6, depth=4, name="a"
        )

    def via_sync_mem_dp(m, clk):
        return m.sync_mem_dp(
            clk,
            ren0=1, raddr0=2, ren1=3, raddr1=4,
            wvalid=5, waddr=6, wdata=7, wstrb=8, depth=4, name="b",
        )

    def via_sync_mem_nm(m, clk):
        return m.sync_mem_nm(clk, [(1, 2)], [(3, 4, 5, 6)], depth=4, name="c")

    for fn in (via_sync_mem, via_sync_mem_dp, via_sync_mem_nm):
        assert get_structural_metrics(fn).state_call_count == 1, fn.__name__
