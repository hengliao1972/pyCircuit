# C++ runtime layout

Header-only runtime for generated cycle-accurate models. One file owns one
concept; generated code only ever includes the umbrella `pyc_sim.hpp`.

The vector containers (`pyc_vec.hpp`, `Vec<T, N>`, `pycVecReg`, `pycVecDelayLine`)
are gone together with the IR `vector<>` type; fronts expand vectors into scalar
lanes, so every remaining component is scalar-valued. The value-type sharing
bases (`RegBase`, `DelayBase`, `CdcBase`) went with them: each family now has
exactly one leaf, so its semantics live directly in the generated class.

## Type layer

| File | Owns |
|---|---|
| `pyc_bits.hpp` | `Bits<W>` / `Wire<W>`: storage, operators, SIMD helpers, width helpers |
| `pyc_clock.hpp` | `ClockDomain` |
| `pyc_connector.hpp` | `Connector<W>` late-bound wire reference |

## Component protocol

| File | Owns |
|---|---|
| `pyc_component.hpp` | `BaseElement`, `ClockedElement<Derived>`, `ComponentFlags` |

## Components (one file per family)

| File | Owns |
|---|---|
| `pyc_reg.hpp` | `pycReg<W>` |
| `pyc_delay_line.hpp` | `pycDelayLine<W, Depth>` |
| `pyc_fifo.hpp` | `pycFifo<W, Depth>` |
| `pyc_cdc_sync.hpp` | `pycCdcSync<W, Stages>` |
| `pyc_sync_mem.hpp` | `pycSyncMemNM` (N read ports, M write ports) |
| `pyc_byte_mem.hpp` | `pycByteMem` (byte-addressed) |
| `pyc_async_fifo.hpp` | `pycAsyncFifo` (dual clock) |
| `pyc_state_loop.hpp` | `RegLoop` / `DelayLoop` batched same-clock updates |

## Aggregators

| File | Role |
|---|---|
| `pyc_primitives.hpp` | combinational `pyc_add` / `pyc_mux` / `pyc_and` / `pyc_or` / `pyc_xor` / `pyc_not`, and includes the three leaf-family headers above for backward compatibility |
| `pyc_sim.hpp` | umbrella included by generated models |
| `pyc_ops.hpp` | legacy functional wrappers (`Add` / `Mux` / ...); no in-tree users, kept for installed-header compatibility |

## Adding a component

1. Add `pyc_<family>.hpp` under `runtime/cpp/`; derive from `ClockedElement`
   (or `BaseElement` when stateless) instead of re-declaring the protocol.
2. Include it from `pyc_sim.hpp`. Add it to `pyc_primitives.hpp` only when the
   existing tests/designs include that aggregator directly.
3. Add the two compatibility symlinks:
   `include/cpp/pyc_<family>.hpp -> ../../runtime/cpp/pyc_<family>.hpp` and
   `include/pyc/cpp/pyc_<family>.hpp -> ../../../runtime/cpp/pyc_<family>.hpp`.
4. Re-run `tests/runtime/run_*.sh` (they compile against all three include roots).
