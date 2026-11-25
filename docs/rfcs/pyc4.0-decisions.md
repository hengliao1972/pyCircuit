# pyc4.0 Decisions (WIP)

This file is an incremental decision log. Each decision is recorded as we align
on the pyc4.0 direction before refactoring.

## Decision 0001: C++ sim object model and module boundary

**Status:** Accepted

**Context / Goal**
pyc4.0 targets ultra-large designs with scalable C++ functional simulation.
We need a first-class simulation object model that maps to module instances,
with strong DFX/probe support and explicit state ownership.

**Decision**
- **SimObject is 1:1 with a pyCircuit `@module` instance** (option 1).
- Each SimObject must provide:
  - `tick()`
  - `transfer()`
- Each SimObject owns internal state generated from:
  - `reg`-like state
  - `mem`-like state
- **Combinational logic inside a module may be flattened** (no requirement to preserve sub-expression structure), but module-instance boundaries are preserved as SimObjects.
- Frontend/IR contract: **pyCircuit MLIR must be able to emit module SimObjects** (i.e., the backend has the information required to generate C++ objects per module instance).

**Implications**
- Backend C++ emission must generate a class/struct per module type plus an instantiation graph that creates per-instance objects.
- DFX/probe pathing should naturally align to module-instance hierarchy.
- `tick/transfer` split implies a 2-phase cycle model (update vs commit), enabling deterministic scheduling and scalable tracing.

**Source**
- User direction in #linx-core (2026-03-01): "选项1 ... tick和transfer ... state由reg和mem产生 ... comb flatten ... mlir emit module sim obj".

## Decision 0002: DFX/probe naming paths are hierarchical but must stay short

**Status:** Accepted

**Context / Goal**
Ultra-large designs need stable, human-readable hierarchical paths for DFX,
trace, breakpointing, and interactive inspection. At the same time, deeply
nested hierarchies can produce overly long strings that harm usability and
runtime overhead.

**Decision**
- Use **hierarchical naming paths** for SimObjects and probes (option A).
- The naming scheme must be **simple** and explicitly designed to **avoid overly long hierarchy strings**.

**Notes / Open follow-ups**
- We still need to decide the concrete shortening mechanism (examples):
  - allow per-module `short_name` / alias
  - path segment hashing beyond a depth threshold
  - optional elision of intermediate hierarchy with stable anchors

**Source**
- User direction in #linx-core (2026-03-01): "a 并且命名规则要简单，避免层级字符串过长".

## Decision 0003: Probe is a unified concept with typed inference (wire/reg/mem/statevar)

**Status:** Accepted

**Context / Goal**
We want strong DFX for ultra-large designs and explicitly need non-wire probes
(e.g. registers, memories, and structured state). At the API surface, we want a
single concept called a "probe" rather than forcing users to manually choose
probe subclasses.

**Decision**
- Use a unified concept: **everything is a `probe`** at the user-facing API.
- Internally, probes have inferred kinds/types, at minimum:
  - wire-like
  - reg-like
  - mem-like
  - statevar-like
- The system performs **type/kind inference** to decide probe behavior and tooling.

**Implications**
- Frontend must preserve enough metadata for probe-kind inference.
- C++ runtime must expose a uniform probe handle/descriptor while still allowing
  kind-specific operations (e.g. mem watch/dump).

**Source**
- User direction in #linx-core (2026-03-01): "a 但是都需要统称为probe，我们有类型推导".

## Decision 0004: Central ProbeRegistry for scalable lookup

**Status:** Accepted

**Context / Goal**
Ultra-large designs require probe lookup to be efficient and tooling-friendly.
A naive tree-walk on every lookup does not scale.

**Decision**
- Use a **centralized `ProbeRegistry`** (option A).
- Registry must support efficient lookup by:
  - hierarchical path (exact match)
  - wildcard/glob match
  - inferred probe kind/type (wire/reg/mem/statevar)

**Implications**
- SimObjects must register their probes deterministically during construction/elaboration.
- Registry must be able to operate with short-path naming rules (Decision 0002).

**Source**
- User direction in #linx-core (2026-03-01): "a" for centralized registry.

## Decision 0005: Two-phase probe sampling (pre/post transfer)

**Status:** Accepted

**Context / Goal**
With a `tick()` / `transfer()` simulation split, debugging needs visibility into
both the computed next-state and the committed state.

**Decision**
- Support two sampling points (option A):
  - **pre-transfer** sampling
  - **post-transfer** sampling

**Implications**
- Trace/DFX tooling can choose pre, post, or both.
- Probe evaluation must be well-defined at each phase.

**Source**
- User direction in #linx-core (2026-03-01): "a" for dual-phase sampling.

## Decision 0006: Memory observability supports hash/watch/dump modes

**Status:** Accepted

**Context / Goal**
Memory state is large; DFX must scale without dumping full contents every cycle.
We need modes that support fast regression checking, targeted debugging, and
on-demand snapshots.

**Decision**
- MemProbe observability supports three modes (option A):
  1) **hash**: `mem_hash()` per-cycle or periodic hashing
  2) **watch**: `mem_watch(range)` event stream for reads/writes in a range
  3) **dump**: `mem_dump(trigger)` snapshot on trigger

**Implications**
- Runtime needs a stable hashing scheme and event encoding.
- Trigger and range filter semantics must be standardized.

**Source**
- User direction in #linx-core (2026-03-01): "a" for mem hash/watch/dump.

## Decision 0007: spec supports runtime reflection

**Status:** Accepted

**Context / Goal**
pyc4.0 needs scalable DFX, probe inference, and large-module integration.
That requires being able to introspect structured types at runtime (field list,
widths, nesting, and paths) rather than losing all type info after elaboration.

**Decision**
- `spec` must support **runtime reflection** (option A).

**Implications**
- The frontend must preserve spec metadata into the emitted artifacts.
- The C++ runtime/templates must be able to represent/specify reflective schemas.

**Source**
- User direction in #linx-core (2026-03-01): "a" for spec runtime reflection.

## Decision 0008: spec uses a layered type system (Bits/Array/Struct/Union/Signature) with parameterization

**Status:** Accepted

**Context / Goal**
Ultra-large module interfaces need expressive, structured types that can be used
consistently across Python frontend authoring, MLIR emission, and C++ simulation
code generation. DFX and probe inference also benefit from explicit type kinds.

**Decision**
- `spec` will provide a layered type system (option A):
  - `Bits(width, signed)`
  - `Array(n, elem)`
  - `Struct(fields...)`
  - `Union(variants...)`
  - `Signature(directed ports...)`
- The type system must support **parameterization** (e.g. via valueclass/config objects).

**Implications**
- Frontend API needs stable constructors/builders for these types.
- MLIR and emitted artifacts must preserve these type kinds.
- C++ templates should map these type kinds into generated storage/ports.

**Source**
- User direction in #linx-core (2026-03-01): "a" for layered spec type system.

## Decision 0009: spec fields have canonical path strings

**Status:** Accepted

**Context / Goal**
We need stable, tool-friendly naming for DFX, probe registration, trace, and
cross-language mapping (Python ↔ MLIR ↔ C++). Structured types must still have a
canonical string path form.

**Decision**
- Each spec field must have a **canonical path string** representation (option A),
  e.g. `foo.bar[3].baz`.

**Implications**
- Reflection APIs must expose canonical paths.
- Path strings should remain simple/short in line with Decision 0002.

**Source**
- User direction in #linx-core (2026-03-01): "a" for canonical spec field paths.

## Decision 0010: Remove global cycle-aware; allow optional module-local cycle-aware sub-DSL

**Status:** Accepted

**Context / Goal**
pyc4.0 focuses on scalable C++ functional simulation with explicit state and
module SimObjects. The legacy global cycle-aware signal model adds complexity
and conflicts with the desired object/state semantics.

**Decision**
- **No global cycle-aware signal system** in pyc4.0.
- Default timing/state semantics are expressed via explicit `reg`/`mem` state and
  `tick()`/`transfer()`.
- Optionally, allow a **module-local cycle-aware sub-DSL** contained within a
  single module boundary (clear isolation).

**Implications**
- Frontend APIs and docs should no longer center cycle-aware programming.
- If the module-local sub-DSL exists, it must compile down to ordinary reg/mem +
  comb logic within the module, without leaking cycle annotations across module
  boundaries.

**Source**
- User direction in #linx-core (2026-03-01): "a" for global removal + module-local option.

## Decision 0011: C++ emits one SimObject class per module type

**Status:** Accepted

**Context / Goal**
To scale functional simulation and keep DFX/probe mapping clean, we want strong
module boundaries (SimObjects) with explicit state ownership, while keeping
internal combinational logic flattenable.

**Decision**
- For each `@module` **type**, emit a dedicated C++ SimObject class (option A),
  e.g. `<ModuleName>_SimObject`.
- Each `@module` **instance** becomes an instance of that class in the
  instantiated object graph.

**Implications**
- The emitter must generate a module-type class definition and a top-level
  builder/instantiation graph.
- DFX paths align naturally with instance hierarchy.

**Source**
- User direction in #linx-core (2026-03-01): "a" for per-module-type class emission.

## Decision 0012: Parent SimObjects own children via unique_ptr (collections via vector)

**Status:** Accepted

**Context / Goal**
We need a clear ownership model for a large SimObject hierarchy without
reference-count overhead or ambiguous lifetimes.

**Decision**
- Parent SimObjects own child SimObjects via `std::unique_ptr` (option A).
- Child collections are represented with `std::vector<std::unique_ptr<...>>` (or
  equivalent) when multiplicity is dynamic.

**Implications**
- Object graph lifetime is tree-owned from the top.
- Avoids `shared_ptr` overhead and simplifies teardown.

**Source**
- User direction in #linx-core (2026-03-01): "a" for unique_ptr ownership.

## Decision 0013: C++ runtime is header + precompiled library

**Status:** Accepted

**Context / Goal**
Header-only template-heavy runtimes can cause compile-time blow-ups for ultra-
large generated designs. We want fast incremental builds and stable runtime
behavior.

**Decision**
- The pyc4.0 C++ runtime will be delivered as **headers + a precompiled library**
  (static and/or shared) (option B).

**Implications**
- Build system must produce and link `libpyc4_runtime` (name TBD).
- Public headers must keep ABI boundaries clean and minimize template bloat.

**Source**
- User direction in #linx-core (2026-03-01): "b" for runtime delivery form.

## Decision 0014: C++ runtime depends only on STL by default

**Status:** Accepted

**Context / Goal**
Minimize dependency footprint and maximize portability across build
environments. Keep the runtime easy to integrate and avoid dependency/version
conflicts.

**Decision**
- The pyc4.0 C++ runtime will depend on **STL only** by default (option A).
- Non-STL dependencies (fmt/spdlog/etc.) may exist only as optional features.

**Implications**
- Logging and formatting must have a minimal default implementation.
- Optional dependency features must be cleanly gated.

**Source**
- User direction in #linx-core (2026-03-01): "a" for STL-only default.

## Decision 0015: Execution is single-thread deterministic first; parallel hooks later

**Status:** Accepted

**Context / Goal**
We need a correct, deterministic baseline simulator for bring-up and debugging.
Ultra-large performance can be improved later once semantics, DFX, and runtime
stability are proven.

**Decision**
- Start with **single-thread deterministic** scheduling/execution (option A).
- Design APIs to allow adding parallelism later (hooks / partitions / task graph),
  without changing architectural semantics.

**Implications**
- Trace/probe behavior is deterministic and reproducible.
- Parallel implementation can be staged once the baseline is stable.

**Source**
- User direction in #linx-core (2026-03-01): "a" for single-thread first.

## Decision 0016: Trace output is primarily a binary event stream

**Status:** Accepted

**Context / Goal**
Ultra-large designs produce enormous trace volumes. Text-first formats are too
slow and too large. We need a scalable representation that supports efficient
writing and offline post-processing.

**Decision**
- Trace output is primarily a **binary event stream** (option A), written in
  chunks/streams.
- Offline tools can convert to human-readable views.

**Implications**
- Define a stable on-disk event schema and versioning.
- Provide minimal tooling for decoding/inspection.

**Source**
- User direction in #linx-core (2026-03-01): "a" for binary-first trace.

## Decision 0017: Path shortening uses short_name first, then stable hashing if still too long

**Status:** Accepted

**Context / Goal**
We require hierarchical paths for DFX/probes (Decision 0002) but must avoid
unwieldy long strings in deep hierarchies.

**Decision**
- Use combined strategy (option C):
  1) Prefer per-module/per-instance `short_name` (alias) when provided.
  2) If the path still exceeds configured depth/length thresholds, apply stable
     hashing to elide middle segments while preserving head/tail readability.

**Implications**
- Define canonical hash algorithm and threshold policy.
- Ensure the shortened path remains stable across builds given identical
  hierarchy + naming.

**Source**
- User direction in #linx-core (2026-03-01): "c" for short_name + hash.

## Decision 0018: ProbeRegistry uses both string paths and numeric ids

**Status:** Accepted

**Context / Goal**
We need human-friendly probe addressing (paths) and high-performance runtime
lookup (ids). Tooling integration also benefits from stable identifiers.

**Decision**
- ProbeRegistry maintains dual indexing (option A):
  - canonical `path` string (for user/tool interaction)
  - numeric `probe_id` (e.g. `u64`) for fast runtime access

**Implications**
- Define `probe_id` stability rules (within a run vs across builds).
- Ensure path shortening (Decision 0017) is reflected in registry path keys.

**Source**
- User direction in #linx-core (2026-03-01): "a" for path+id dual indexing.

## Decision 0019: probe_id is stable across builds for the same canonical path

**Status:** Accepted

**Context / Goal**
Tooling (trace correlation, external dashboards, regression comparison) benefits
from stable identifiers. If ids change across builds, cached views and
annotations become invalid.

**Decision**
- `probe_id` must be **stable across builds** (option B) for the same canonical
  probe path.

**Implications**
- Define deterministic id assignment (e.g. hash(canonical_path) with versioned
  algorithm/salt).
- Renames/path shortening changes will change ids; provide migration guidance.

**Source**
- User direction in #linx-core (2026-03-01): "b" for cross-build stable probe_id.

## Decision 0020: probe_id is hash64(canonical_path)

**Status:** Accepted

**Context / Goal**
To ensure cross-build stable identifiers (Decision 0019) with minimal
implementation complexity, ids should be derived deterministically from the
canonical probe path.

**Decision**
- `probe_id = hash64(canonical_path)` (option A), using a versioned, specified
  hash algorithm.

**Implications**
- Specify the exact algorithm (e.g. xxHash64 / SipHash / HighwayHash) and
  endianness.
- Provide collision detection/handling strategy (log + secondary disambiguator).

**Source**
- User direction in #linx-core (2026-03-01): "a" for hash64(path).

## Decision 0021: hash64 algorithm is xxHash64

**Status:** Accepted

**Context / Goal**
We need a fast, deterministic 64-bit hash for large numbers of probes/paths.

**Decision**
- `hash64` uses **xxHash64** (option A), with a specified seed (default 0 unless
  otherwise required).

**Implications**
- Vendor a small xxHash implementation or add it as an optional runtime feature
  (but keep Decision 0014: STL-only default; thus prefer vendoring).

**Source**
- User direction in #linx-core (2026-03-01): "a" for xxHash64.

## Decision 0022: Detect hash collisions; resolve by rehashing with a numeric suffix

**Status:** Accepted

**Context / Goal**
Even with 64-bit hashes, collisions are possible. We need a deterministic,
visible strategy that preserves usability and avoids silent misbinding.

**Decision**
- At registry construction, detect collisions.
- If collision occurs, resolve deterministically by rehashing:
  - try `hash64(path + "#1")`, then `#2`, ... until a free id is found (option B).

**Implications**
- Log collisions with both paths and final assigned ids.
- Stability across builds is preserved as long as the set/order of colliding
  paths is stable; specify tie-breaking order (e.g. sort by path string).

**Source**
- User direction in #linx-core (2026-03-01): "b" for collision rehashing.

## Decision 0023: canonical_path format is <instance_path>:<field_path>

**Status:** Accepted

**Context / Goal**
`canonical_path` is the user-facing identity string and the input to `probe_id`
(Decisions 0019–0022). It must be unambiguous and easy to parse.

**Decision**
- `canonical_path = <canonical_instance_path> ":" <canonical_field_path>` (option A).

**Implications**
- Instance paths use the shortening rules (Decision 0017).
- Field paths follow the canonical field path rules (Decision 0009).

**Source**
- User direction in #linx-core (2026-03-01): "a" for instance:field separator.

## Decision 0024: Array indexing in paths uses square brackets

**Status:** Accepted

**Context / Goal**
Canonical path strings must encode array indexing unambiguously and in a
familiar style.

**Decision**
- Array indices use `name[index]` (option A), e.g. `foo[3]`.

**Implications**
- Specify escaping rules if field names may contain `[`/`]` (ideally forbid in
  identifiers).

**Source**
- User direction in #linx-core (2026-03-01): "a" for `foo[3]` indexing.

## Decision 0025: Path identifiers use strict C-like character set; no escaping

**Status:** Accepted

**Context / Goal**
Canonical paths should be trivial to parse and stable across tooling. Escaping
rules add ambiguity and implementation burden.

**Decision**
- Identifiers in paths are restricted to a strict C-like set (option A):
  - `[A-Za-z_][A-Za-z0-9_]*`
- No escaping/encoding is supported in canonical paths.

**Implications**
- Frontend must validate and reject identifiers outside this set (or map them to
  `short_name`/aliases that obey the restriction).

**Source**
- User direction in #linx-core (2026-03-01): "a" for strict identifiers.

## Decision 0026: Simulation cycle uses 2-phase execution (comb then tick/commit)

**Status:** Accepted

**Context / Goal**
We need clear, deterministic clocked semantics with explicit separation between
combinational evaluation and state updates, to match hardware intuition and
avoid accidental read-after-write within a cycle.

**Decision**
- Use 2-phase cycle semantics (option A):
  - `comb()` computes purely combinational outputs from current state/inputs.
  - `tick()` computes next-state from current state and comb results.
  - `commit()` applies next-state (may be explicit or an internal step).

**Implications**
- Generated code must maintain `state` vs `next_state` separation.
- Tracing can cleanly attribute events to comb vs tick.

**Source**
- User direction in #linx-core (2026-03-01): "a" for 2-phase cycle.

## Decision 0027: Provide both step() and explicit comb/tick/commit APIs

**Status:** Accepted

**Context / Goal**
Most users want a simple `step()` API, but advanced testing/DFX may require
manual phase control.

**Decision**
- Provide both (option C):
  - Default high-level API: `step()` performs comb + tick + commit.
  - Advanced APIs: `comb()`, `tick()`, `commit()` are also exposed.

**Implications**
- Document legal call sequences and invariants.
- Ensure trace/probe semantics are well-defined for both modes.

**Source**
- User direction in #linx-core (2026-03-01): "c" for step + explicit phase APIs.

## Decision 0028: Reset is modeled as an input affecting tick; provide reset() helper

**Status:** Accepted

**Context / Goal**
Keep reset semantics consistent with clocked state update rules and avoid hidden
state mutations outside the comb/tick/commit pipeline.

**Decision**
- Model reset as an input that affects `tick()` next-state computation (option A).
- Provide a `reset()` helper that asserts reset and steps as required.

**Implications**
- Reset behavior is traceable and deterministic.
- Avoids bypassing commit invariants.

**Source**
- User direction in #linx-core (2026-03-01): "a" for modeled reset + helper.

## Decision 0029: Simulation timebase is cycle count (u64)

**Status:** Accepted

**Context / Goal**
We need a simple, deterministic time representation aligned with the step-based
execution model.

**Decision**
- Use cycle count as the timebase: `time = u64 cycles` (option A).

**Implications**
- Trace timestamps and events are indexed by cycle.
- Physical time (ns/ps) can be derived externally if a clock period is known.

**Source**
- User direction in #linx-core (2026-03-01): "a" for cycle-based time.

## Decision 0030: Trace timestamps include phase (cycle, phase)

**Status:** Accepted

**Context / Goal**
With multi-phase simulation (Decision 0026) and optional explicit phase APIs
(Decision 0027), trace needs to preserve intra-cycle ordering.

**Decision**
- Trace timestamps include phase information (option A):
  - `timestamp = (cycle, phase)` where `phase ∈ {comb, tick, commit}`.

**Implications**
- Event schema must encode phase.
- When using `step()`, phases are emitted in the standard order.

**Source**
- User direction in #linx-core (2026-03-01): "a" for (cycle, phase) timestamps.

## Decision 0031: Trace event minimal set includes cycle boundaries, value changes, and log/assert

**Status:** Accepted

**Context / Goal**
Binary trace needs a minimal closed set of events to support basic waveform-like
inspection and debugging without overengineering.

**Decision**
- Use minimal event set (option A):
  - `CycleBegin` / `CycleEnd` (with timestamp)
  - `ValueChange(probe_id, value)`
  - `Log` / `Assert` events

**Implications**
- Registry/probe declarations are separate from the event stream initially.
- Additional events can be added in future versions with schema versioning.

**Source**
- User direction in #linx-core (2026-03-01): "a" for minimal trace event set.

## Decision 0032: ValueChange value encoding is unified bitvector (bits + width)

**Status:** Accepted

**Context / Goal**
Trace values must represent both scalars and wide vectors with a single
consistent encoding.

**Decision**
- Use unified encoding (option A): `value = (width, bits)` where scalars are
  represented as bitvectors with `width <= 64`.

**Implications**
- Define bit ordering (LSB0) and byte order for serialization.
- Optionally add future fast-path scalar tags as an extension without changing
  semantic meaning.

**Source**
- User direction in #linx-core (2026-03-01): "a" for unified bitvector encoding.

## Decision 0033: Bitvector serialization uses LSB0 and little-endian byte order

**Status:** Accepted

**Context / Goal**
To ensure cross-language, cross-platform consistency for trace value decoding,
we must define bit ordering and byte order.

**Decision**
- Bit ordering is **LSB0**: bit 0 is the least-significant bit.
- Byte order is **little-endian** in the serialized byte stream (option A).

**Implications**
- Readers must reconstruct integers/vectors accordingly.
- For widths not multiple of 8, high unused bits in the final byte are ignored
  (must be zeroed on write).

**Source**
- User direction in #linx-core (2026-03-01): "a" for LSB0 + little-endian.

## Decision 0034: ValueChange emission is delta-by-default with optional full/periodic dumps

**Status:** Accepted

**Context / Goal**
Emitting all probe values every cycle does not scale. We need a default encoding
that compresses well while still allowing periodic resynchronization.

**Decision**
- Default: emit `ValueChange` events only when the value changes (option A).
- Support optional modes:
  - full dump (emit all watched probes at a chosen point)
  - periodic dump (emit full dump every N cycles)

**Implications**
- Trace readers must maintain last-value state per probe.
- Periodic/full dumps provide recovery points for seeking.

**Source**
- User direction in #linx-core (2026-03-01): "a" for delta encoding default.

## Decision 0035: Log/Assert event schema uses simple levels and messages

**Status:** Accepted

**Context / Goal**
We need debuggable, filterable textual diagnostics in the trace stream without
overcomplicating the event schema.

**Decision**
- Use simple event forms (option A):
  - `Log(level, message)`
  - `Assert(message, fatal)`

**Implications**
- Define `level` enum (e.g. debug/info/warn/error).
- `Assert(fatal=true)` may terminate the simulation or mark the trace as
  aborted.

**Source**
- User direction in #linx-core (2026-03-01): "a" for Log(level)/Assert(fatal).

## Decision 0036: Log level enum is trace/debug/info/warn/error/fatal

**Status:** Accepted

**Context / Goal**
Provide enough granularity for filtering diagnostics without making the logging
system heavyweight.

**Decision**
- Log levels are (option B):
  - `trace`, `debug`, `info`, `warn`, `error`, `fatal`

**Implications**
- `fatal` is distinct from `Assert(fatal=true)`; `fatal` logs may still be used
  for non-assert termination paths.

**Source**
- User direction in #linx-core (2026-03-01): "b" for 6-level enum.

## Decision 0037: Support both self-describing and external-manifest trace modes

**Status:** Accepted

**Context / Goal**
Single-file traces are easier to share and analyze, but separating the probe
manifest can reduce duplication and file size.

**Decision**
- Support both (option C):
  - Self-describing mode: include `ProbeDeclare` records in the trace.
  - External-manifest mode: keep probe registry/manifest as a separate artifact.

**Implications**
- Define a canonical manifest schema shared by both modes.
- Readers must handle either embedded declarations or an external manifest.

**Source**
- User direction in #linx-core (2026-03-01): "c" for dual trace modes.

## Decision 0038: ProbeDeclare includes id/path/kind/type plus optional human alias

**Status:** Accepted

**Context / Goal**
We want self-describing traces that are usable by humans without requiring a
separate UI-side name mapping.

**Decision**
- `ProbeDeclare` includes (option B):
  - `probe_id`
  - `canonical_path`
  - `kind`
  - `type_sig`
  - optional `human_name` / alias

**Implications**
- Define `type_sig` encoding to cover Bits/Array/Struct/Union.
- Alias is non-unique and for display only; `canonical_path` remains the stable
  identity.

**Source**
- User direction in #linx-core (2026-03-01): "b" for adding alias.

## Decision 0039: type_sig reuses spec type structure with compact binary encoding

**Status:** Accepted

**Context / Goal**
Trace readers must be able to reconstruct structured types for display and
navigation. A compact, versionable binary encoding avoids heavy text parsing.

**Decision**
- Encode `type_sig` by reusing the spec type structure (option A):
  - `Bits / Array / Struct / Union / Signature`
- Use a compact binary variant/TLV encoding with schema versioning.

**Implications**
- Define a stable type-tag enum and recursive encoding rules.
- Readers can decode without needing the original Python source.

**Source**
- User direction in #linx-core (2026-03-01): "a" for structured binary type_sig.

## Decision 0040: Trace has a global schema_version; type_sig does not carry per-record version

**Status:** Accepted

**Context / Goal**
Avoid per-record overhead while keeping decoding rules unambiguous.

**Decision**
- Store a global `schema_version` in the trace header (option A).
- `ProbeDeclare.type_sig` is interpreted under that global version.

**Implications**
- Any breaking changes require bumping the trace schema version.
- Readers can reject unsupported versions early.

**Source**
- User direction in #linx-core (2026-03-01): "a" for global schema_version.

## Decision 0041: Trace framing is chunked records with length/type/payload and optional CRC

**Status:** Accepted

**Context / Goal**
We need forward compatibility (skip unknown record types), robustness, and the
ability to seek/partition traces.

**Decision**
- Use chunked framing (option A):
  - `[chunk_len][chunk_type][payload][crc?]...`

**Implications**
- Define chunk_type namespace and rules for unknown chunk skipping.
- CRC can be optional per chunk or globally configured.

**Source**
- User direction in #linx-core (2026-03-01): "a" for chunked framing.

## Decision 0042: chunk_len is fixed u32 little-endian

**Status:** Accepted

**Context / Goal**
Keep decoding fast and simple; 4GB max chunk size is more than sufficient.

**Decision**
- Encode `chunk_len` as fixed-width `u32` little-endian (option A).

**Implications**
- Writer must split very large payloads into multiple chunks.

**Source**
- User direction in #linx-core (2026-03-01): "a" for u32 LE chunk_len.

## Decision 0043: No per-chunk CRC in trace framing

**Status:** Accepted

**Context / Goal**
Avoid overhead and keep the format minimal; rely on transport/storage integrity
or higher-level validation.

**Decision**
- Do not include per-chunk CRC (option C).

**Implications**
- Corruption may manifest as decode errors later; readers should still validate
  lengths and handle malformed input robustly.

**Source**
- User direction in #linx-core (2026-03-01): "c" for no CRC.

## Decision 0044: Optional chunk-level compression (zstd) for trace payloads

**Status:** Accepted

**Context / Goal**
Large traces benefit greatly from compression. Chunk-level compression preserves
seeking and partial decoding while keeping the core framing stable.

**Decision**
- Support optional chunk-level compression (option B):
  - Introduce a `Compressed` chunk type whose payload contains:
    - compression algorithm (default: zstd)
    - uncompressed length
    - compressed bytes

**Implications**
- Readers must be able to skip compressed chunks if unsupported.
- Writers choose chunk sizes to balance compression ratio and random access.

**Source**
- User direction in #linx-core (2026-03-01): "b" for chunk-level compression.

## Decision 0045: Default compressed chunk size is 1MB with zstd level 3

**Status:** Accepted

**Context / Goal**
Pick sensible defaults that balance compression ratio, CPU cost, and random
access granularity.

**Decision**
- Defaults (option A):
  - uncompressed chunk target size: **1MB**
  - zstd compression level: **3**

**Implications**
- Expose overrides for power users and CI.

**Source**
- User direction in #linx-core (2026-03-01): "a" for 1MB + level 3.

## Decision 0046: Only CycleBegin/End carry timestamps; other events inherit current time

**Status:** Accepted

**Context / Goal**
Reduce trace size while keeping event ordering well-defined.

**Decision**
- Only `CycleBegin` / `CycleEnd` carry `timestamp=(cycle,phase)` (option A).
- All other events are interpreted at the current active timestamp.

**Implications**
- Readers maintain a "current timestamp" state.
- Writers must emit CycleBegin/End in a consistent order to avoid ambiguity.

**Source**
- User direction in #linx-core (2026-03-01): "a" for implicit timestamps.

## Decision 0047: CycleBegin/End are emitted per phase (comb/tick/commit)

**Status:** Accepted

**Context / Goal**
Make phase boundaries explicit in the trace stream so tools can segment events
and present phase-scoped views.

**Decision**
- Emit `CycleBegin`/`CycleEnd` per phase (option A):
  - `(cycle, comb)` begin/end
  - `(cycle, tick)` begin/end
  - `(cycle, commit)` begin/end

**Implications**
- Writers must follow a consistent ordering of phases.
- Readers can treat begin/end as the scope for inherited timestamps.

**Source**
- User direction in #linx-core (2026-03-01): "a" for per-phase boundaries.

## Decision 0048: Default ValueChange sampling/emission occurs in comb phase (end-of-comb)

**Status:** Accepted

**Context / Goal**
Provide a predictable, intuitive default for waveform-like visualization of
combinational results each cycle.

**Decision**
- Default: emit/sample `ValueChange` in the **comb** phase (option A), scoped
  within the `(cycle, comb)` begin/end interval (typically near end-of-comb).

**Implications**
- Other views (tick/commit state) can be obtained by probing state elements and
  emitting in corresponding phases if desired.

**Source**
- User direction in #linx-core (2026-03-01): "a" for comb-phase default.

## Decision 0049: Reg module write semantics: enable-gated write, otherwise hold

**Status:** Accepted

**Context / Goal**
Define unambiguous state element behavior for tracing and commit semantics.

**Decision**
- For `reg`-like stateful modules with an `enable`:
  - if `enable==1`: write/update to the new value
  - if `enable==0`: **no write**; output/state **holds** its previous value

**Implications**
- With delta-only `ValueChange` (Decision 0034), cycles where `enable==0` will
  typically emit no value change for that reg (because the value is held).
- Trace tools can treat `enable` as the authoritative indicator of whether a
  state update occurred.

**Source**
- User direction in #linx-core (2026-03-01): reg.enable==0 inhibits write and holds value.

## Decision 0050: Commit phase emits ValueChange for stateful probes when updates occur

**Status:** Accepted

**Context / Goal**
Make architectural/state updates explicit at commit time while keeping combinational
signals in comb. Works naturally with enable-gated regs and delta-only encoding.

**Decision**
- Default (option B): in **commit** phase, emit `ValueChange` for **stateful probes**
  (e.g., regs/CSRs/state elements) when an update occurs.
  - If a reg has `enable==0`, it is a hold; no update and typically no emitted
    value change.

**Implications**
- Tools can show "what became architecturally visible" by focusing on commit.
- Writers should classify probes as stateful vs combinational (or infer from module type).

**Source**
- User direction in #linx-core (2026-03-01): "b" for commit emitting stateful updates.

## Decision 0051: Probe kind explicitly encodes stateful vs combinational classification

**Status:** Accepted

**Context / Goal**
Trace writers and tools need a reliable way to decide whether a probe represents
state (commit-visible) or combinational signals, without brittle inference.

**Decision**
- Encode the classification explicitly in `ProbeDeclare.kind` (option A):
  - e.g. `comb` vs `state` (naming TBD)

**Implications**
- Readers can filter and present commit vs comb views deterministically.
- Avoids coupling trace semantics to module-type inference.

**Source**
- User direction in #linx-core (2026-03-01): "a" for explicit kind.

## Decision 0052: Probe kind is comb/state with optional subkind (reg/mem/csr/etc.)

**Status:** Accepted

**Context / Goal**
Keep the core classification simple while allowing richer filtering/grouping in
tools when needed.

**Decision**
- Use a two-level scheme (option C):
  - `kind`: `comb` | `state`
  - optional `subkind`: e.g. `reg` | `mem` | `csr` | ...

**Implications**
- Subkind is advisory; semantics derive primarily from kind.
- Trace writers can omit subkind when unknown.

**Source**
- User direction in #linx-core (2026-03-01): "c" for kind+subkind.

## Decision 0053: Record explicit Write events; keep ValueChange delta-based

**Status:** Accepted

**Context / Goal**
We need a clear notion of "a write happened" independent of whether the data
actually changed, while preserving compact delta-only value encoding.

**Decision**
- Support both signals (option C):
  - Emit explicit state update intent as `Write`-like events (e.g., reg-write,
    mem-write, csr-write) when a write command/enable is asserted.
  - Continue to emit `ValueChange` only when the observable value changes (delta).

**Implications**
- Tools can count/inspect writes even when the value is unchanged.
- Writers need access to write intents (valid/enable/mask) for stateful modules.

**Source**
- User direction in #linx-core (2026-03-01): "c" for write events + delta ValueChange.

## Decision 0054: Use generic Write event with required subkind; fields interpreted by subkind

**Status:** Accepted

**Context / Goal**
Avoid an explosion of event types while still capturing the differences between
reg/mem/csr writes.

**Decision**
- Use a generic `Write` event (option C) with a required `subkind`:
  - `subkind`: `reg` | `mem` | `csr` | ...
- Interpret optional fields based on `subkind`:
  - `probe_id` (always)
  - `addr` (mem/csr if applicable)
  - `mask` (mem if applicable)
  - `data` (written data)
  - `meta` (optional)

**Implications**
- Decoders need a per-subkind schema table.
- Writers can omit irrelevant fields.

**Source**
- User direction in #linx-core (2026-03-01): "c" for generic Write + subkind.

## Decision 0055: Write.data and Write.mask use the same ValueBlob bytes encoding as ValueChange

**Status:** Accepted

**Context / Goal**
Keep encodings consistent across events and avoid per-event special cases.

**Decision**
- Encode `Write.data` as `ValueBlob` bytes (same rules as `ValueChange.value`).
- Encode `Write.mask` also as bytes (`ValueBlob`), where the interpretation is
  subkind-defined (e.g., mem byte-enable mask).

**Implications**
- Readers can reuse the same value decoding pipeline.
- Mask semantics must be specified per subkind (width/endianness).

**Source**
- User direction in #linx-core (2026-03-01): "a" for ValueBlob encoding.

## Decision 0056: Define X-state (unknown) via an explicit known-mask alongside value bytes

**Status:** Accepted

**Context / Goal**
We need a first-class way to represent "unknown / uninitialized / unresolved"
values (X) in traces and intermediate representations, without overloading
normal numeric encodings.

**Decision**
- Support **bit-level** X (option A).
- Extend `ValueBlob` conceptually to carry two equal-length byte arrays:
  - `value_bytes`: the 0/1 payload bits
  - `known_mask_bytes`: 1 means the corresponding bit is known; 0 means **X**
- A bit is interpreted as:
  - if `known_mask=1`: bit value is `value_bytes` (0 or 1)
  - if `known_mask=0`: bit value is **X** (unknown)

**Notes**
- This is a 2-state value + 1-bit validity mask representation (common in RTL sims).
- For scalar bool: `known_mask=0` represents X.

**Implications**
- Trace consumers can render X cleanly and propagate unknowns when doing derived views.
- Writers that do not model X can set `known_mask_bytes` to all-ones.

**Source**
- User direction in #linx-core (2026-03-01): need an X-state definition; chose "A" for bit-level X.

## Decision 0057: Write.mask default is all-ones (full write)

**Status:** Accepted

**Context / Goal**
Choose an unsurprising default for write masks.

**Decision**
- If `Write.mask` is omitted, it is interpreted as **all-ones** (option A), i.e.
  full write over the addressed width.

**Implications**
- Writers only need to include mask for partial writes.

**Source**
- User direction in #linx-core (2026-03-01): "A" for default full-write mask.

## Decision 0058: Delta ValueChange triggers on changes to either value bytes or known-mask bytes

**Status:** Accepted

**Context / Goal**
Ensure X->known and known->X transitions are observable in delta-only traces.

**Decision**
- For delta emission, treat the pair `(value_bytes, known_mask_bytes)` as the
  logical value (option A).
- Emit `ValueChange` if either `value_bytes` **or** `known_mask_bytes` differs
  from the last emitted state.

**Implications**
- Trace size may increase slightly when unknowns resolve, but semantics are correct.

**Source**
- User direction in #linx-core (2026-03-01): "A" for including known-mask changes.

## Decision 0059: Partial writes preserve old value/known-mask for untouched bits/bytes

**Status:** Accepted

**Context / Goal**
Define deterministic merging behavior for masked writes in the presence of X-state.

**Decision**
- For masked/partial writes (option A):
  - written lanes update `value_bytes` and `known_mask_bytes` per the write data
  - **unwritten** lanes keep their previous `value_bytes` and `known_mask_bytes`

**Implications**
- Unknowns are not introduced unless explicitly written as unknown.

**Source**
- User direction in #linx-core (2026-03-01): "A" for preserving untouched lanes.

## Decision 0060: Write.data supports X via the same (value, known-mask) representation

**Status:** Accepted

**Context / Goal**
Allow a write to explicitly introduce unknown bits (X), and keep encoding uniform
across ValueChange and Write.

**Decision**
- Option A: `Write.data` uses the same `(value_bytes, known_mask_bytes)`
  representation as `ValueChange`.
  - `known_mask=0` bits mean the write sets those bits to X.

**Implications**
- Masked writes combine with Decision 0059: only written lanes apply the new
  known-mask; untouched lanes retain their old known-mask.

**Source**
- User direction in #linx-core (2026-03-01): "A" for Write supporting X.

## Decision 0061: Uninitialized state defaults to X until reset/init/write defines it

**Status:** Accepted

**Context / Goal**
Model realistic power-on/unknown state to avoid masking bugs and to make X
resolution visible in traces.

**Decision**
- Option A: for stateful elements, the default power-on/uninitialized value is
  **all X** (`known_mask_bytes` all-zeros) until:
  - a reset/init sequence defines it, or
  - a write defines it (possibly partially, per mask)

**Implications**
- Consumers should expect early-cycle X noise unless reset is modeled.
- Reset events (if present) should drive known_mask to 1 for reset-defined bits.

**Source**
- User direction in #linx-core (2026-03-01): "A" for default uninitialized = X.

## Decision 0062: Include explicit Reset events in the trace

**Status:** Accepted

**Context / Goal**
Make reset sequences explicitly visible and distinguishable from ordinary writes.

**Decision**
- Option A: include a `Reset` event, with fields along the lines of:
  - `domain` (optional)
  - `kind` (optional: e.g., POR, warm, SW)
  - `cycle` and `phase`
- Reset-driven state updates should appear as commit-phase stateful `ValueChange`
  (per Decision 0050).

**Implications**
- Tools can segment timelines and suppress/annotate early X-resolution noise.

**Source**
- User direction in #linx-core (2026-03-01): "A" for explicit Reset event.

## Decision 0063: Reset is represented as edge events (assert/deassert)

**Status:** Accepted

**Context / Goal**
Represent reset in a streaming-friendly way with unambiguous duration.

**Decision**
- Option A: represent reset as two edge events:
  - `ResetAssert{domain?, kind?, cycle, phase}`
  - `ResetDeassert{domain?, kind?, cycle, phase}`

**Implications**
- Writers can emit events online without knowing the future.
- Readers can reconstruct reset intervals precisely.

**Source**
- User direction in #linx-core (2026-03-01): "A" for assert/deassert edges.

## Decision 0064: Reset domain is required (module/domain scoped resets)

**Status:** Accepted

**Context / Goal**
Flush/reset may be applied to a specific module (or reset domain), not
necessarily the whole design.

**Decision**
- Option A: `domain` is **required** on reset edge events.
  - `ResetAssert{domain, kind?, cycle, phase}`
  - `ResetDeassert{domain, kind?, cycle, phase}`

**Implications**
- Tools can distinguish global resets from per-module flushes (modeled as reset).
- Writers must define a stable domain naming/ID scheme.

**Source**
- User direction in #linx-core (2026-03-01): "A" because flushing a module uses reset semantics.

## Decision 0065: Reset.kind is standardized as an enum including flush

**Status:** Accepted

**Context / Goal**
Provide a uniform way for tools to distinguish POR/warm reset vs module flushes.

**Decision**
- Option A: standardize `kind` as a small enum (extensible), at least:
  - `por`
  - `warm`
  - `flush`
  - `sw`

**Implications**
- Tools can consistently color/segment timelines by reset kind.
- Writers should map internal reset causes into this enum.

**Source**
- User direction in #linx-core (2026-03-01): "A" for kind enum.

## Decision 0066: Reset effects are represented via commit-phase ValueChange events

**Status:** Accepted

**Context / Goal**
Avoid a special bulk snapshot format and keep all state updates flowing through
one mechanism.

**Decision**
- Option A: reset results are expressed via commit-phase `ValueChange` events
  (including `known_mask` transitions), rather than embedding a bulk snapshot in
  the reset event.

**Implications**
- Tools that already understand ValueChange automatically display reset effects.
- Reset event remains a timeline marker, not a state container.

**Source**
- User direction in #linx-core (2026-03-01): "A" for reset effects via ValueChange.

## Decision 0067: Flush/reset includes explicit invalidate events for in-flight work

**Status:** Accepted

**Context / Goal**
When a module is flushed (modeled as reset), it often cancels in-flight
transactions/queue entries. We want that cancellation to be explicit in the
trace so tools do not need heuristics.

**Decision**
- Option A: introduce an explicit invalidation/cancel event, e.g.:
  - `Invalidate{domain, reason?, cycle, phase, scope?}`
- Emit this around the flush/reset boundary to indicate which in-flight work is
  being dropped.

**Implications**
- Trace viewers can visually strike-through or terminate flows at invalidate.
- Writers must define `scope`/`reason` conventions per domain.

**Source**
- User direction in #linx-core (2026-03-01): "A" for explicit invalidate events.

## Decision 0068: Invalidate supports both domain-wide and fine-grained scope (scope optional)

**Status:** Accepted

**Context / Goal**
We want a streaming-friendly invalidate event that is useful immediately (coarse
flush) but can scale to precise cancellation when IDs exist.

**Decision**
- Choose option C: `Invalidate.scope` is **optional**.
  - If `scope` is omitted: invalidate applies to the entire `domain`.
  - If `scope` is present: it precisely identifies the object(s) to cancel
    (queue/entry id, txn id, ROB id, etc.; domain-defined).

**Implications**
- Minimal writers can emit coarse invalidates without schema gymnastics.
- Advanced writers can emit targeted invalidates for better visualization.

**Source**
- Assistant decision in #linx-core (2026-03-01) after user: "you decide encoding".

## Decision 0069: Invalidate is emitted in pre phase (before reset and commit state updates)

**Status:** Accepted

**Context / Goal**
Provide a deterministic ordering: first cancel in-flight work, then perform
reset/flush markers, then commit the resulting architectural state changes.

**Decision**
- Option A: emit `Invalidate` in `pre` phase.
  - Typical ordering within a cycle:
    1) `Invalidate` (pre)
    2) `ResetAssert` / `ResetDeassert` (pre)
    3) commit-phase stateful `ValueChange`

**Implications**
- Viewers can terminate flows before showing reset-driven state changes.
- Writers have a clear phase to attach cancellation semantics.

**Source**
- User direction in #linx-core (2026-03-01): "A" for pre-phase invalidate.

## Decision 0070: Invalidate.reason is required

**Status:** Accepted

**Context / Goal**
Provide a stable classification key so tools can group/visualize different
cancellation causes.

**Decision**
- Option A: `Invalidate.reason` is **required**.
  - Examples: `flush_mispredict`, `flush_exception`, `replay`, `squash`, etc.

**Implications**
- Writers must map internal cancel causes to a stable reason string/enum.
- Viewers can color/aggregate invalidations by reason.

**Source**
- User direction in #linx-core (2026-03-01): "A" for required reason.

## Decision 0071: Invalidate.reason uses a standardized enum (with extensible other)

**Status:** Accepted

**Context / Goal**
Avoid taxonomy drift across writers; enable consistent viewer behavior.

**Decision**
- Option A: `Invalidate.reason` is standardized as an enum.
  - Any out-of-tree reason should use an escape hatch like `other:<string>`
    (or an `OTHER` + `detail` scheme in the future).

**Implications**
- Viewers can reliably color/aggregate by reason.
- Writers have to update the enum (or use other:*) for new reason classes.

**Source**
- User direction in #linx-core (2026-03-01): "A" for reason enum.

## Decision 0072: Event cycle is explicit and required

**Status:** Accepted

**Context / Goal**
Keep parsing and random access simple and deterministic across tooling.

**Decision**
- Option A: every event carries an explicit `cycle` (required).

**Implications**
- Slightly larger traces, but much simpler parsing/indexing and slicing.

**Source**
- User direction in #linx-core (2026-03-01): "A" for explicit required cycle.

## Decision 0073: phase is optional; default phase is commit

**Status:** Accepted

**Context / Goal**
Reduce verbosity for the common case where most events are commit-phase.

**Decision**
- Option B: `phase` is optional.
  - If `phase` is omitted, it defaults to `commit`.

**Implications**
- Writers must explicitly set `phase=pre` for pre-phase events.
- Readers must apply the defaulting rule consistently.

**Source**
- User direction in #linx-core (2026-03-01): "B" for phase default=commit.

## Decision 0074: Emit explicit CycleEnd boundary events

**Status:** Accepted

**Context / Goal**
Make streaming viewers simpler by providing an unambiguous “end of cycle” marker
so UIs can flush/render without buffering heuristics.

**Decision**
- Option A: emit a boundary event at the end of each cycle, e.g.:
  - `CycleEnd{cycle}`

**Implications**
- Slight trace size overhead (one small event per cycle).
- Viewers can incrementally render per-cycle without guessing.

**Source**
- User direction in #linx-core (2026-03-01): "A" for explicit cycle boundary.

## Decision 0075: CycleEnd is global (no domain)

**Status:** Accepted

**Context / Goal**
Keep the cycle boundary semantics single and unambiguous; domain-specific
semantics are expressed via per-event `domain` fields instead.

**Decision**
- Option A: `CycleEnd` does **not** carry `domain`; it is a global timeline
  boundary.

**Implications**
- Multi-domain systems must share a common cycle axis for the trace.

**Source**
- User direction in #linx-core (2026-03-01): "A" for global CycleEnd.

## Decision 0076: Enforce within-cycle ordering: pre events, then commit, then CycleEnd

**Status:** Accepted

**Context / Goal**
Make traces deterministic for streaming viewers without requiring a secondary
stable sort key.

**Decision**
- Option A: within a given `cycle`, event order is constrained as:
  1) all `phase=pre` events (e.g. `Invalidate`, `ResetAssert`, `ResetDeassert`)
  2) all `phase=commit` events (including defaulted commit events)
  3) `CycleEnd{cycle}` as the final event for that cycle

**Implications**
- Writers must buffer/reorder within a cycle to satisfy ordering if needed.
- Readers can process events in file order.

**Source**
- User direction in #linx-core (2026-03-01): "A" for strict within-cycle ordering.

## Decision 0077: Pre-phase ordering is fixed: Invalidate → ResetAssert → ResetDeassert

**Status:** Accepted

**Context / Goal**
Provide deterministic semantics when multiple pre events occur in the same
cycle.

**Decision**
- Option A: within `phase=pre` for a cycle, enforce this order:
  1) `Invalidate`
  2) `ResetAssert`
  3) `ResetDeassert`

**Implications**
- Writers may need to buffer pre events within a cycle.
- Readers/viewers can rely on a consistent causal timeline.

**Source**
- User direction in #linx-core (2026-03-01): "A" for fixed pre ordering.

## Decision 0078: Allow same-cycle reset pulses (assert+deassert in one cycle)

**Status:** Accepted

**Context / Goal**
Support 1-cycle reset/flush pulses without introducing a dedicated pulse event.

**Decision**
- Option A: allow `ResetAssert` and `ResetDeassert` to both appear in the same
  `cycle` (in pre phase, respecting the pre-ordering rules).

**Implications**
- Readers should treat this as a single-cycle reset interval.

**Source**
- User direction in #linx-core (2026-03-01): "A" for same-cycle pulse.

## Decision 0079: Reset is non-reentrant per domain (strict pairing)

**Status:** Accepted

**Context / Goal**
Keep reset semantics simple and avoid having to model nesting depth.

**Decision**
- Option A: reset is non-reentrant for a given `domain`.
  - You must not emit a second `ResetAssert` for a domain that is already in the
    asserted state.

**Implications**
- Writers must coalesce overlapping causes into a single reset interval.
- Readers can treat reset as a boolean state per domain.

**Source**
- User direction in #linx-core (2026-03-01): "A" for non-reentrant reset.

## Decision 0080: Reset.kind is immutable while asserted; kind changes require deassert+assert

**Status:** Accepted

**Context / Goal**
Avoid introducing additional state-machine events and keep reset a simple boolean
interval with a stable classification.

**Decision**
- Choose option A: while a domain is in reset asserted state, `Reset.kind` does
  not change.
  - If a different reset cause/kind arises, writers must either:
    - coalesce it into the existing kind (policy-defined), or
    - end the current reset (`ResetDeassert`) and begin a new one (`ResetAssert`)
      with the new kind.

**Implications**
- No `ResetKindChange` event type needed.
- Viewers can treat kind as a stable tag for the reset interval.

**Source**
- Assistant recommendation in #linx-core (2026-03-01): pick A for kind immutability.

## Decision 0081: Remove Reset.kind=sw

**Status:** Accepted

**Context / Goal**
The `sw` label is ambiguous (software-triggered reset vs software-requested
flush) and overlaps conceptually with more explicit kinds.

**Decision**
- Option C: remove `Reset.kind=sw`.
  - Prefer using explicit kinds such as `flush` (pipeline flush semantics) or
    architectural reset kinds (e.g., por/warm) where applicable.

**Implications**
- Fewer ambiguous categories; tools/writers converge on shared semantics.

**Source**
- User direction in #linx-core (2026-03-01): "C" to drop sw.

## Decision 0082: Keep both Reset.kind=flush and Reset.kind=warm

**Status:** Accepted

**Context / Goal**
Distinguish pipeline/in-flight clearing from a broader but non-POR reset.

**Decision**
- Option A: keep both kinds.
  - `flush`: pipeline/in-flight work invalidation semantics.
  - `warm`: module/subsystem reset semantics that are stronger than flush but
    not a full power-on reset.

**Implications**
- Writers can choose the appropriate semantic level.
- Viewers can present flush vs warm differently.

**Source**
- User direction in #linx-core (2026-03-01): "A" to keep both.

## Decision 0083: warm reset should be accompanied by Invalidate (recommended, not required)

**Status:** Accepted

**Context / Goal**
Make in-flight cancellation explicit for viewers when warm resets occur, while
not forcing all writers to emit additional events.

**Decision**
- Option C: recommend emitting an `Invalidate` alongside `ResetAssert(kind=warm)`
  (same cycle or adjacent), but do not require it.
  - Linters/tools may warn if warm reset occurs without a corresponding
    invalidate.

**Implications**
- Minimal traces remain valid.
- High-fidelity traces can keep causal cancellation explicit.

**Source**
- User direction in #linx-core (2026-03-01): "C" for recommend-but-not-require.

## Decision 0084: flush reset should be accompanied by Invalidate (recommended, not required)

**Status:** Accepted

**Context / Goal**
Encourage explicit cancellation semantics for flush resets while preserving a
low-friction writer experience.

**Decision**
- Option C: recommend emitting an `Invalidate` alongside `ResetAssert(kind=flush)`
  (same cycle or adjacent), but do not require it.
  - Linters/tools may warn if flush reset occurs without a corresponding
    invalidate.

**Implications**
- Writers can start with reset-only traces.
- Viewers get best results when invalidate is present.

**Source**
- User direction in #linx-core (2026-03-01): "C" for recommend-but-not-require.

## Decision 0085: Coalescing events is a viewer presentation policy (not trace semantics)

**Status:** Accepted

**Context / Goal**
Keep the trace contract focused on deterministic semantics and ordering, while
allowing viewers to choose richer or simpler presentations without constraining
writers.

**Decision**
- Option C: the spec does not mandate whether viewers must show `Invalidate` and
  `ResetAssert(kind=flush)` separately or as a single “Flush” concept.
  - Viewers **may** coalesce related same-cycle events into a single UI concept
    (presentation layer), but the underlying events remain distinct.

**Implications**
- Trace format remains stable and minimal.
- Different viewers can optimize for clarity vs fidelity.

**Source**
- User direction in #linx-core (2026-03-01): "C" for viewer policy.

## Decision 0086: Define a normative Viewer Contract layer

**Status:** Accepted

**Context / Goal**
Ensure consistent user experience and interpretation across multiple viewers by
standardizing key presentation/interaction behaviors on top of the same trace
semantics.

**Decision**
- Option A: the spec is explicitly two-layered:
  - **Trace Semantics**: event meaning + ordering + decoding.
  - **Viewer Contract**: required/standard viewer behaviors (e.g., default
    aggregation rules, reset/flush visualization conventions, ordering within UI
    lanes).

**Implications**
- Viewer implementations become more interoperable.
- Some flexibility is traded for consistency across tools.

**Source**
- User direction in #linx-core (2026-03-01): "A" to define a viewer contract.

## Decision 0087: Viewer default is object-level timelines

**Status:** Accepted

**Context / Goal**
Optimize for signal-centric debugging (regs/mems/ops) where users track an
object’s evolution over time; cycle markers remain available as a global ruler.

**Decision**
- Option B: viewers default to **object-level** timelines; `cycle` is primarily a
  shared time axis, not the primary grouping unit.

**Implications**
- Viewers should provide optional cycle-level grouping/filters as secondary
  affordances.

**Source**
- User direction in #linx-core (2026-03-01): "B" for object-level default.

## Decision 0088: Boundary events render as global overlays by default

**Status:** Accepted

**Context / Goal**
Avoid cluttering per-object lanes while still making major boundaries obvious in
an object-centric timeline.

**Decision**
- Option A: `Reset*`, `Invalidate`, and similar boundary events should render as
  a **global overlay band** by default (spanning all lanes), rather than being
  injected into each object lane.

**Implications**
- Viewers must provide interaction affordances (hover/click) on the overlay to
  inspect affected domains/scopes.

**Source**
- User direction in #linx-core (2026-03-01): "A" for global overlay.

## Decision 0089: Use a single overlay lane; distinguish domains via styling

**Status:** Accepted

**Context / Goal**
Minimize vertical space usage while still supporting multi-domain boundaries.

**Decision**
- Option A: viewers use **one** overlay lane by default; different `domain`s are
  distinguished via color/labels/tooltips.

**Implications**
- Viewers should provide filtering/highlighting by domain for clarity when many
  domains are active.

**Source**
- User direction in #linx-core (2026-03-01): "A" for single overlay lane.

## Decision 0090: Viewer Contract focuses on large-design debugging, not pixel-perfect UI consistency

**Status:** Accepted

**Context / Goal**
pyc4.0’s primary goal is to make debugging **ultra-large** designs practical.
We want consistency where it improves interpretability, but we do not want to
freeze UI/interaction details that would slow iteration.

**Decision**
- Option A: Viewer Contract does **not** aim for pixel-level identical UI across
  viewers.
- Viewer Contract should standardize only what is necessary to make large-design
  debugging convenient and consistent at the semantic/information-architecture
  level (e.g., default layouts, required affordances, required interpretations).

**Implications**
- Different viewers may differ in exact visuals and interactions.
- Spec effort stays focused on scalability and debugging utility.

**Source**
- User direction in #linx-core (2026-03-01): "a 只要能够方便我们调试超超超大大大的design就好".

## Decision 0091: Debugging assumes selective instrumentation (not "probe everything")

**Status:** Accepted

**Context / Goal**
For ultra-large designs, we typically do not probe or view everything. The common
workflow is to add probes only at suspected/problematic areas, iterate, and keep
the system responsive.

**Decision**
- Option C: primary scalability concern is **hierarchy depth / pathing** and
  general navigability, assuming **selective probe insertion** rather than
  blanket probing/viewing of all signals.

**Implications**
- Probe/path naming must remain usable at extreme hierarchy depth.
- Tooling should optimize for rapid add/remove of targeted probes and quick
  navigation to those probes.

**Source**
- User direction in #linx-core (2026-03-01): "c 我们不会全部都probe和view。只是在有问题的地方插入probe".

## Decision 0092: Spec standardizes mechanisms; workflow remains non-normative

**Status:** Accepted

**Context / Goal**
Avoid over-specifying debugging workflows. Keep the spec focused on providing
robust mechanisms (probe/trace/path/id/semantics) that enable many workflows.
At the same time, provide guidance that helps humans debug ultra-large designs
quickly.

**Decision**
- The spec standardizes **mechanisms** (probe/trace/paths/ids and their
  semantics).
- Debugging workflow guidance and viewer UX flows are **not normative
  requirements**.

**Implications**
- Viewer implementations may offer different workflows as long as the core
  semantics remain interoperable.

**Source**
- User direction in #linx-core (2026-03-01): "a".

## Decision 0093: Include a recommended (non-normative) debugging workflow

**Status:** Accepted

**Context / Goal**
Even if workflow is not normative, documenting a recommended workflow improves
team alignment and makes it easier to debug ultra-large designs consistently.

**Decision**
- Option B: the spec includes a **recommended** (non-normative) debugging
  workflow section. It should describe how to use the standardized mechanisms
  effectively, without imposing hard requirements on viewer UX.

**Implications**
- The RFC/spec should maintain a clear separation: normative semantics vs
  non-normative workflow guidance.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0094: Trace is an on-demand, filtered debug artifact (offline file)

**Status:** Accepted

**Context / Goal**
Tracing everything by default is not practical for ultra-large designs. Trace
should be something you turn on only when debugging, and typically only for a
subset of suspicious modules/signals.

**Decision**
- Option A: linxtrace is primarily an **offline trace file** artifact.
- Trace emission is **off by default**; it is enabled on-demand for debugging.
- Trace must support **filtering** so users can limit emission to selected
  modules and signals.

**Implications**
- Writer/runtime must provide efficient filter configuration (at elaboration
  time and/or runtime) to avoid overhead when trace is off.
- Viewer workflow assumes partial traces are common.

**Source**
- User direction in #linx-core (2026-03-01): "a 默认不产生 只有debug时才会用… 也只是对部分怀疑的模块和信号开，要有过滤功能".

## Decision 0095: Filtering is primarily a writer/runtime responsibility

**Status:** Accepted

**Context / Goal**
To keep ultra-large design debugging practical, overhead must be avoided when
trace is off and when only a small subset of probes is of interest.

**Decision**
- Option A: filtering is primarily implemented in the **trace writer/runtime**.
  If a probe/scope is not enabled, it should **not emit** trace records.

**Implications**
- Filtering must be cheap to check at emission sites.
- Viewer-side filtering remains useful but is secondary.

**Source**
- User direction in #linx-core (2026-03-01): "a" + clarification: "probe在框架中默认不emit…".

## Decision 0096: Probe is a pyc primitive (an IR construct) lowered/emitted to C++

**Status:** Accepted

**Context / Goal**
We need a scalable way to instrument ultra-large designs without assuming all
signals are probed or that probes are purely a C++ runtime concept.

**Decision**
- A `probe` is a **pyc primitive** and thus a first-class construct in the **pyc
  IR**.
- Probes are selected/configured at the pyc/Python level and are **lowered /
  emitted into C++** as part of JIT/codegen.

**Implications**
- The IR must carry enough metadata (path/type/kind) for consistent trace
  emission.
- Filtering/enablement should be realized primarily during lowering/codegen so
  disabled probes do not impose runtime overhead.

**Source**
- User direction in #linx-core (2026-03-01): "probe是pyc的primitive，也是pyc的ir一种，然后emit到c++".

## Decision 0097: probe_id is assigned in IR/lowering (not in Python, not in C++ emit)

**Status:** Accepted

**Context / Goal**
Keep probe identity stable and consistent with the final canonical paths/types
that will actually be emitted, while avoiding coupling to C++ backend
implementation details.

**Decision**
- Option B: `probe_id` is assigned/generated during **IR lowering / codegen
  preparation** (after canonical paths are finalized), and is then emitted into
  C++.

**Implications**
- Python/front-end constructs do not need to guess final paths.
- C++ emission remains a mechanical lowering step.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0098: Probe registry/manifest is owned by IR/lowering; C++ keeps only lightweight mappings

**Status:** Accepted

**Context / Goal**
In a JIT architecture where probes are IR constructs, we want a single source of
truth for probe metadata (id/path/type/kind) that matches the final lowered
program and can be used to generate trace declarations/manifests.

**Decision**
- Option B: the primary probe registry (and any generated manifest) is produced
  and owned by **IR lowering / codegen preparation**.
- The C++ runtime/emitter may keep only the lightweight mappings needed for
  emission and trace writing.

**Implications**
- Aligns probe identity with Decision 0097 (id assigned in lowering).
- Keeps C++ runtime simpler and avoids duplicating control-plane logic.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0099: Support both self-describing traces and external manifests; prefer external manifests

**Status:** Accepted

**Context / Goal**
Traces are often partial (selective probes) and used for ultra-large designs.
Viewers benefit from fast access to probe metadata (id/path/type/kind) without
having to scan the entire trace payload.

**Decision**
- Option B: support both:
  - **Self-describing** traces (probe declarations inside the trace), and
  - **External manifest** (sidecar file or referenced metadata)
- Prefer external manifests as the primary workflow.

**Implications**
- The toolchain should define a stable manifest format and linking mechanism
  (e.g., trace header references manifest hash/path).

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0100: External manifest is generated during IR/lowering

**Status:** Accepted

**Context / Goal**
The manifest should reflect the final canonical probe metadata (id/path/type/kind)
used by emission, and should be available without running the full simulation.

**Decision**
- Option B: generate the external manifest during **IR lowering / codegen
  preparation**.

**Implications**
- Keeps manifest consistent with Decision 0097/0098.
- Enables tooling to prepare trace+manifest before execution.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0101: Filtering selection language uses hierarchical path patterns (glob/regex) + optional grouping

**Status:** Accepted

**Context / Goal**
Debugging ultra-large designs usually focuses on suspicious regions. Users need
an ergonomic way to select scopes/signals without enumerating all probe ids.

**Decision**
- Option B: filtering configuration supports hierarchical **path pattern**
  selection (glob/regex) and may optionally support tags/groups for convenience.

**Implications**
- Canonical path syntax must be stable enough for pattern matching.
- Tooling should provide helpers (autocomplete/search) to build these filters.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0102: Filtering is primarily compile-time (JIT-time) fixed

**Status:** Accepted

**Context / Goal**
Avoid runtime overhead in hot paths. Keep trace-off and trace-minimal modes as
cheap as possible.

**Decision**
- Option A: filtering selection is primarily applied at **JIT compile time**.
  Only enabled probes are lowered/emitted; others do not generate runtime emit
  code.

**Implications**
- Changing filters requires re-JIT/recompile (acceptable in debug workflow).
- Minimizes per-probe runtime checks.

**Source**
- User direction in #linx-core (2026-03-01): "a".

## Decision 0103: Event-driven simulation with memoized tick; port-level change detection

**Status:** Accepted

**Context / Goal**
Current LinxCore simulation scales poorly when the whole design is effectively
flattened and evaluated every cycle. We want module-instance SimObjects (Decision
0001) and an event-driven execution model so that only impacted modules re-run.

**Decision**
- Simulation is **event-driven**: a module instance re-runs `tick()` only when
  at least one of its **input ports changes**.
- Change detection granularity is **port-level** (not field/bit-level).
- Skipping `tick()` is defined as **pure memoization** (A2 semantics): if inputs
  are unchanged, outputs are guaranteed unchanged, so reusing previous outputs is
  semantically equivalent to recomputation (no implicit latching semantics).

**Implications**
- Port values should support a **version/epoch** concept to enable fast
  change-detection and fanout-based wakeups.
- Works naturally with SimObject-per-instance and hierarchical DFX/probe pathing.

**Source**
- User direction in #linx-core (2026-03-01): "a" for port-level change detection;
  follow-up: "a2" for memoization semantics.

## Decision 0104: Versioned value storage + fanout wakeup for event-driven scheduling

**Status:** Accepted

**Context / Goal**
To make event-driven simulation scalable, we need O(1) change detection and a
way to efficiently find downstream modules impacted by an output change.

**Decision**
- Port values are stored in versioned slots (e.g. a `ValuePool` where each value
  has an associated **version/epoch**).
- Each module instance caches last-seen input versions; it is scheduled when any
  input version differs.
- The runtime maintains a **fanout/dependency mapping** from an output port/value
  to the set of downstream module input ports that depend on it; when an output
  version increments, only affected modules are woken.

**Implications**
- Encourages a handle/index-based storage model compatible with large designs.
- Enables efficient dirty-queue scheduling without full-graph scans.

**Source**
- User direction in #linx-core (2026-03-01): "好" (agree) to record this as the
  next decision after 0103.

## Decision 0105: Value version increments only on semantic change (new != old)

**Status:** Accepted

**Context / Goal**
In an event-driven simulator, unnecessary wakeups kill the benefit. If a module
writes an output that is equal to the previous value, downstream modules should
not be rescheduled.

**Decision**
- Option B: bump a value/port's **version/epoch only when the new value differs
  from the old value**.

**Implications**
- Requires an equality/compare operation for port-level values.
- Avoids spurious fanout wakeups and improves steady-state performance.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0106: Two-level equality check for large port values (fast precheck + fallback)

**Status:** Accepted

**Context / Goal**
Port-level values may be large (struct/array/wide bits). Always doing a full
byte/word compare can dominate runtime and erase event-driven wins.

**Decision**
- Option B: use a **two-level** change check:
  1) a fast precheck (e.g. cached hash/signature, dirty flag, chunk summary)
  2) if needed, a full compare fallback to preserve exact semantics

**Implications**
- Keeps strict correctness while reducing average-case compare cost.
- Requires defining what metadata is stored alongside values (e.g. per-value
  signature).

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0107: Event-driven tick; batch transfer

**Status:** Accepted

**Context / Goal**
We want event-driven speedups without complicating the state-commit semantics.
The `tick()` phase benefits from selective evaluation; the `transfer()` phase is
more naturally expressed as a simple commit step.

**Decision**
- Option B: the dirty-queue/event-driven scheduler drives **`tick()`**.
- **`transfer()` remains batch-style** (e.g. run for all modules with state, or
  via a simple precomputed list), rather than being event-driven.

**Implications**
- Simplifies correctness reasoning: commit semantics do not depend on scheduler
  details.
- Keeps the door open for later optimization (e.g. limiting transfer to stateful
  modules) without changing the conceptual model.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0108: Initial dirty set via fanout from external inputs

**Status:** Accepted

**Context / Goal**
At simulation start/reset, we need an initial set of modules to evaluate.
Marking the whole design dirty negates the event-driven benefit.

**Decision**
- Option B: compute the initial dirty set by starting from **external/top-level
  input ports** and doing a single **fanout propagation** to mark downstream
  module instances dirty.

**Implications**
- Requires a representation of external inputs as versioned values/handles.
- Avoids full-graph evaluation at time 0 while still producing correct outputs.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0109: Within-cycle tick runs until the dirty queue is empty (converge)

**Status:** Accepted

**Context / Goal**
An event-driven scheduler can cascade changes through the graph. To preserve
cycle semantics equivalent to recomputing combinational effects from changed
inputs, we need a clear within-cycle convergence policy.

**Decision**
- Option B: during a cycle's `tick()` phase, repeatedly process the dirty queue
  **until it is empty** (i.e. run to a fixed point within the cycle).

**Implications**
- Requires a strategy for combinational cycles (e.g. detection, iteration cap,
  or explicit modeling rules) to avoid infinite oscillation.

**Source**
- User direction in #linx-core (2026-03-01): "b".

## Decision 0110: Combinational cycle handling: MLIR verification + runtime iteration cap (error)

**Status:** Accepted

**Context / Goal**
With Decision 0109 (run-to-empty within a cycle), combinational loops/oscillation
could cause non-termination or unstable behavior. We need a clear policy that is
scalable and fits the MLIR-based toolchain.

**Decision**
- Option B: treat combinational cycles as **invalid by default**.
- Add an **MLIR-level verification/check** to detect illegal combinational
  cycles in the relevant IR graph(s) and fail fast with a clear diagnostic.
- Add a **runtime safety net**: an iteration cap / oscillation detection during
  within-cycle convergence; on hit, stop and report an error (with enough path
  context to debug).

**Implications**
- Most cases should be caught statically in MLIR, keeping runtime overhead low.
- The runtime cap prevents hangs if a bad graph slips through or is constructed
  dynamically.

**Source**
- User direction in #linx-core (2026-03-01): "b" and reminder: "我们是mlir需要检查".

## Decision 0111: TemplateSpec hardening contract (Python @const/spec → MLIR + C++ metadata)

**Status:** Accepted

**Context / Goal**
pyCircuit's template/spec system intentionally lives at JIT elaboration time:
Python `pycircuit.spec` objects and `@const` metaprogramming are powerful for
constructing immutable compile-time structures (signatures, structs, collections
of module instances). However, after JIT, Python objects are gone and the C++
runtime/event-driven simulator must not depend on them.

**Decision**
- Treat all Python template/spec objects (`pycircuit.spec.*`, `@spec.valueclass`
  instances, and `@const`-returned containers) as **elaboration-time only**.
- Any information required by runtime semantics (event-driven scheduling,
  port/value layout, instance pathing/DFX/probes, fanout dependencies, etc.) MUST
  be **hardened at JIT-time** into one or both of:
  - **MLIR IR/attributes** (for verification + codegen inputs)
  - **C++ runtime metadata tables/blobs** (for simulation/DFX at runtime)
- The C++ runtime must rely only on hardened MLIR/codegen outputs + runtime state
  (ValuePool/StatePool/etc.), never on live Python objects.

**Implications**
- Strong separation of concerns:
  - Python: authoring + metaprogramming + deterministic elaboration
  - MLIR: structural truth + verification (incl. combinational cycle checks)
  - C++: fast event-driven execution using versioned values + fanout wakeups
- Requires defining stable serialized forms for key spec artifacts (e.g.
  signature/struct layouts, instance paths, connection graphs).

**Source**
- User request in #linx-core (2026-03-01): "请再读一下pyc的template spec设计。它可以做Python的数据结构，但是jit之后就没有了" and follow-up "好你先把契约写了".

## Decision 0112: MLIR dialect is the single semantic source; C++ sim and Verilog emission must be logically equivalent

**Status:** Accepted

**Context / Goal**
In the pyCircuit flow, MLIR serves as the "semantic truth" of the design. We
must support both:
- a fast C++ event-driven simulator backend, and
- an MLIR→Verilog emission backend.

To avoid divergence, the two backends must implement the *same* semantics.
After the C++ simulator passes, the Verilog emission should require no special
"fixups" and should also pass the same workloads.

**Decision (strong constraint)**
- Treat the pyc IR dialect (and its verified invariants) as the **single source
  of truth** for design semantics.
- The MLIR→C++ simulator backend and the MLIR→Verilog backend MUST be
  **logically equivalent** implementations of that same MLIR semantics.
- Backend-specific patches that change semantics are disallowed; semantic fixes
  must be made at the MLIR dialect semantics / verification / lowering rules so
  that both backends inherit the fix.

**Implications**
- MLIR verification is a gatekeeper: if it passes, both backends operate under
  the same validated assumptions (types, connections, illegal comb cycles, etc.).
- Regression should include **dual-backend equivalence tests**: identical
  stimulus, sampled at defined observation points (e.g. pre/post transfer), with
  C++ sim traces compared against Verilog-sim traces.

**Source**
- User direction in #linx-core (2026-03-01):
  - "verilog和cpp要等价" / "cpp仿真通了以后，verilog也不要改，也应该跑通"
  - "这就是pyc ir方言设计理念。要写成强约束"

## Decision 0113: Equivalence observation points are part of the semantics (pre/post transfer + defined sampling)

**Status:** Accepted

**Context / Goal**
Dual-backend equivalence testing (Decision 0112) only works if we precisely
define *when* values are observed within a cycle. Otherwise, trace comparisons
will produce false mismatches caused by sampling at different points rather than
real semantic divergence.

**Decision (strong constraint)**
- The dialect/runtime defines two canonical observation points per cycle:
  - **TICK-OBS (pre-transfer):** after within-cycle `tick()` convergence
    (Decision 0109) completes, but before `transfer()` commits state.
  - **XFER-OBS (post-transfer):** after `transfer()` batch commit
    (Decision 0107) completes.
- Probes/trace points MUST declare which observation point they sample at.
- C++ sim and Verilog sim equivalence comparisons MUST compare values sampled at
  the same named observation point.

**Implications**
- Clarifies semantics for reg/state visibility: state updates become visible at
  XFER-OBS; combinational effects of current inputs are visible at TICK-OBS.
- Makes it possible to generate consistent trace/probe code in both backends.

**Source**
- User direction in #linx-core (2026-03-01): "好继续" (continue; write the
  observation-point constraint as a decision).

## Decision 0114: Memory (mem/array) semantics are explicit and backend-stable (read timing + write commit + RDW rule)

**Status:** Accepted

**Context / Goal**
Memories are the most common source of "CPP sim passes, Verilog sim differs" if
read/write timing is implicit. To satisfy Decision 0112 (dual-backend logical
 equivalence) and Decision 0113 (observation points), memory behavior must be
part of the dialect semantics, not an implementation detail.

**Decision (strong constraint)**
- The dialect defines cycle semantics for memories in terms of the same
  observation points:
  - **Reads during `tick()` observe the pre-transfer memory state** (the state
    committed at the end of the previous cycle).
  - **Writes are committed during `transfer()`** and become visible at
    **XFER-OBS** of the current cycle (and subsequently at TICK-OBS of the next
    cycle).
- Read-during-write (same address in same cycle) behavior MUST be defined by the
  dialect. Default rule:
  - **old-data** (read returns the pre-transfer value; write takes effect at
    XFER-OBS).
  - If alternative behavior is needed (write-first/no-change), it must be an
    explicit, typed memory op/attribute so both backends match.

**Implications**
- C++ event-driven simulation can implement mem as versioned state committed in
  batch transfer, matching Verilog's clocked-update model.
- Verilog emission must lower the same rule (e.g. sequential write + defined RDW
  semantics), and testbenches must sample at TICK-OBS/XFER-OBS consistently.

**Source**
- User direction in #linx-core (2026-03-01): "好" (continue to lock down mem
  semantics as a strong constraint).

## Decision 0115: Reset/initialization semantics are explicit and identical across backends

**Status:** Accepted

**Context / Goal**
Initialization is another common source of divergence between C++ simulators and
Verilog (e.g. Verilog `initial` blocks, X-propagation, tool-specific init). To
satisfy Decision 0112, reset/initial behavior must be defined by the dialect and
lowered identically.

**Decision (strong constraint)**
- The dialect must make reset/init explicit:
  - State-bearing elements (regs, memories, stateful modules) have a defined
    **initial value** and/or an explicit **reset** behavior.
  - No backend may rely on implicit Verilog initialization defaults or simulator
    quirks.
- If a design uses reset, its sampling/visibility is defined relative to the
  same observation points (Decision 0113):
  - Reset effects that update state are applied at **transfer/commit** and are
    visible at **XFER-OBS** (and thereafter).
  - Pure combinational reset gating affects TICK-OBS like any other comb logic.

**Implications**
- C++ backend: initialize StatePool/MemPool from hardened init metadata; apply
  reset via the same transfer path used for normal state updates.
- Verilog backend: emit explicit reset logic (and/or explicit init values if
  synthesizable) consistent with the dialect; avoid `initial` unless the dialect
  explicitly models it.

**Source**
- User direction in #linx-core (2026-03-01): "好继续" (continue; lock down
  reset/initial equivalence as a strong constraint).

## Decision 0116: Dialect supports 4-valued logic (X) to match Verilog; X is preserved across both backends

**Status:** Accepted

**Context / Goal**
Verilog simulation is inherently 4-valued (0/1/X/Z). If the pyc dialect is to be
Verilog-friendly and satisfy Decision 0112 (C++ sim ↔ Verilog equivalence), the
core value model must be able to represent and propagate unknowns (X) rather
than silently collapsing to 2-valued logic.

**Decision (strong constraint)**
- The pyc dialect value model MUST support **X (unknown)** for signals/ports and
  state elements.
- The C++ event-driven simulator backend MUST implement X-aware operations and
  comparisons, preserving X semantics (do not coerce X to 0/1).
- The Verilog emission backend MUST preserve the same X semantics as represented
  in the dialect; equivalence testing compares X consistently.

**Implications**
- Value representation likely needs (value_bits, known_mask) or an equivalent
  encoding; equality and "new!=old" checks (Decision 0105/0106) must be defined
  for X-aware values (e.g. version bump when either value_bits differs under
  known bits or known_mask changes).
- Memory/reg reset/init rules (Decision 0115) must specify whether init produces
  known values or X, and how X propagates.

**Source**
- User direction in #linx-core (2026-03-01): "方言要适配好verilog，所以需要支持x".

## Decision 0117: X-aware equality/versioning contract (value_bits + known_mask; change detection + fast signature)

**Status:** Accepted

**Context / Goal**
Decision 0105/0106 require "bump version only on semantic change" and a
performance-friendly two-level compare. With Decision 0116 (support X), we must
make equality/change-detection precise and backend-stable; otherwise the C++
event-driven scheduler and Verilog simulation will diverge.

**Decision (strong constraint)**
- Represent each logic value as two bitvectors of equal width:
  - `value_bits`: the 0/1 payload
  - `known_mask`: 1 = known, 0 = unknown (X)
- Define semantic equality (`eq`) and change detection (`changed`) as:
  - `eq(a,b)` iff `a.value_bits == b.value_bits` AND `a.known_mask == b.known_mask`
  - `changed(a,b)` is the negation of `eq(a,b)`
- Version bump rule (refines Decision 0105): bump version iff `changed(old,new)`.
- Two-level compare (refines Decision 0106):
  - Maintain a per-value **signature** computed from both `value_bits` and
    `known_mask` (e.g. hash of the pair).
  - Fast precheck compares signatures; on mismatch do full compare on both
    bitvectors to avoid hash-collision false negatives.

**Implications**
- C++ backend: `ValuePool` slots must store both bitvectors (or an equivalent
  packed form), and all primitive ops must propagate `known_mask`.
- Verilog backend: lowering must preserve the same semantics; for example, a
  4-valued vector in Verilog corresponds to the same `(value_bits, known_mask)`
  model for equivalence testing.
- Probe/trace serialization must include X information (known_mask) to make
  dual-backend traces comparable.

**Source**
- User direction in #linx-core (2026-03-01): "好" (continue; lock down X-aware
  change detection and fast compare semantics).

## Decision 0118: Dialect supports Z (high-impedance) explicitly; tri-state/resolve semantics are defined

**Status:** Accepted

**Context / Goal**
To be Verilog-compatible, X support alone is not sufficient: Verilog also models
**Z (high-impedance)** and resolution on nets with multiple drivers. If the
pyc dialect is to emit Verilog that is logically equivalent to the C++ simulator
(Decision 0112), Z and multi-driver resolution must be explicit dialect
semantics, not left to backend interpretation.

**Decision (strong constraint)**
- The pyc dialect value model MUST support **Z** in addition to 0/1/X.
- The dialect MUST define where Z is legal:
  - Z may appear on *nets/ports/wires* that represent tri-state connectivity.
  - Z is illegal for *state elements* (regs/mems) unless explicitly modeled.
- The dialect MUST define multi-driver **net resolution** rules that both
  backends implement identically. Default rule set (Verilog-like):
  - If all active (non-Z) drivers agree on a known 0/1 → resolved = that value.
  - If no active drivers (all Z) → resolved = Z.
  - If conflicting active drivers or any unknown participation → resolved = X.
- The C++ simulator MUST implement resolution using the dialect rule, not by
  ad-hoc last-writer-wins.

**Implications**
- Requires distinguishing *net* vs *variable/state* in the dialect lowering and
  metadata (single-driver variables can bypass resolution for performance).
- Equivalence tests must sample resolved net values (post-resolution) at the
  defined observation points (Decision 0113).

**Source**
- User direction in #linx-core (2026-03-01): "需要" (need; add Z support).

## Decision 0119: Compile-time WNS/TNS-equivalent checks are logic-depth based (not timing); thresholds are compiler options

**Status:** Accepted

**Context / Goal**
We want early (compile-time) feedback on "too deep" combinational logic without
requiring a full timing model/library. In MLIR, we can estimate *logic depth*
(number of logic levels / longest-path depth) and use this as a WNS/TNS-like
proxy to gate designs during compilation.

**Decision (strong constraint)**
- The pyc dialect/toolchain MUST provide a compile-time check that computes
  **depth-based slack** for combinational paths, analogous to WNS/TNS:
  - Define per-endpoint **depth_arrival** as the maximum combinational depth
    from valid sources (e.g. regs/Q, primary inputs, memory read outputs) to the
    endpoint (e.g. regs/D, primary outputs, explicit timing endpoints).
  - Define a user-specified **depth_budget** (integer levels) provided as a
    compiler option/flag.
  - Define **depth_slack = depth_budget − depth_arrival**.
  - **WNS-equivalent** = minimum depth_slack across endpoints.
  - **TNS-equivalent** = sum of negative depth_slack across endpoints.
- The check MUST run in the MLIR pipeline and emit source-located diagnostics
  when thresholds are violated.
- The check MUST be backend-stable: C++ sim and Verilog emission consume the
  same verified IR and do not redefine depth semantics.

**Implications**
- Provides deterministic, library-free compile-time gating.
- Establishes a clear upgrade path: a future STA/timing-based analysis can reuse
  the same endpoint graph and report format, swapping "depth" for "delay".

**Source**
- User direction in #linx-core (2026-03-01): "mlir可以估计逻辑级数而不是timing…threshold是编译选项".

## Decision 0120: Value model upgrade plan: first-class 4-valued logic + explicit net/var split

**Status:** Accepted

**Context / Goal**
Current prototype uses plain MLIR integers (`iN`) as data values, which is
2-valued and cannot faithfully model Verilog's 4-valued semantics (Decision 0116
X + Decision 0118 Z). Z also implies multi-driver net resolution, which requires
an explicit notion of nets vs variables/state.

**Decision (required changes)**
- Introduce a first-class PYC value model that can represent **0/1/X/Z**.
- Make **net vs variable/state** explicit in IR and verifiable:
  - *net*: may have multiple drivers and uses the dialect-defined resolution rule
    (Decision 0118).
  - *var/state*: must be single-driven; no resolution; illegal to produce Z.
- Update all core comb ops to be defined over the 4-valued model (X/Z-aware),
  not implicitly over 2-valued integers.

**Implications**
- Enables backend-stable semantics for both C++ sim and Verilog emission.

**Source**
- Gap analysis request in #linx-core (2026-03-01): "请分析一下现有的pyc ir，还有哪些欠缺的" and follow-up: "把上述欠缺都写入decisions中".

## Decision 0121: Observation points are IR-visible and required for probes/trace and equivalence

**Status:** Accepted

**Context / Goal**
Decisions 0112/0113 define canonical observation points (TICK-OBS/XFER-OBS).
The current IR spec does not make these visible or enforceable, which risks
trace/equivalence drift across backends.

**Decision (required changes)**
- Extend IR/metadata so probes/trace sites can explicitly declare sampling at
  **TICK-OBS** or **XFER-OBS**.
- Define and document which ops take effect in tick vs transfer, so sampling is
  well-defined.

**Implications**
- Eliminates "false mismatches" in dual-backend comparisons.

**Source**
- Gap analysis request in #linx-core (2026-03-01).

## Decision 0122: Memory semantics must be normalized at the dialect level (tick-read vs transfer-write + RDW)

**Status:** Accepted

**Context / Goal**
The prototype primitive set includes both combinational-read and synchronous-read
memories, but the dialect-level semantics are not unified, risking divergence
between C++ sim and Verilog.

**Decision (required changes)**
- Lift memory behavior into explicit dialect semantics:
  - define read timing relative to TICK-OBS/XFER-OBS (Decision 0114)
  - define write commit at transfer
  - define RDW behavior (default old-data) as part of the op/attr contract
- Require MLIR verification to reject ambiguous/underspecified memory behavior.

**Source**
- Gap analysis request in #linx-core (2026-03-01).

## Decision 0123: Combinational cycle legality must be enforced by an MLIR verifier pass with instance-aware diagnostics

**Status:** Accepted

**Context / Goal**
Decision 0110 requires MLIR-level comb-cycle detection. The current IR spec
includes constructs that can express feedback (e.g. `pyc.wire/pyc.assign` and
cross-instance connections) but does not define the verification pass or graph
construction.

**Decision (required changes)**
- Implement an MLIR pass/verifier that builds the combinational dependency graph
  (including across `pyc.instance` boundaries) and rejects illegal cycles.
- Diagnostics must include enough hierarchical/instance-path context to debug.

**Source**
- Gap analysis request in #linx-core (2026-03-01).

## Decision 0124: Depth (WNS/TNS proxy) requires a specified counting model and endpoint rules

**Status:** Accepted

**Context / Goal**
Decision 0119 defines depth-based WNS/TNS equivalents, but the prototype does not
specify the counting model (which ops contribute depth) or the exact endpoint
set, which would make results unstable.

**Decision (required changes)**
- Specify depth counting rules in the dialect/toolchain:
  - which ops count as +1 logic level (e.g. add/mux/compare)
  - which ops are depth-neutral (e.g. alias/bitcast/wire plumbing)
  - how `pyc.comb` regions contribute (flattened by contained ops)
  - how instance boundaries contribute (depth propagates through instance I/O)
- Specify valid sources/endpoints for depth analysis.

**Source**
- Gap analysis request in #linx-core (2026-03-01).

## Decision 0125: TemplateSpec hardening must materialize required runtime/DFX metadata in MLIR (no Python dependency)

**Status:** Accepted

**Context / Goal**
Decision 0111 states Python TemplateSpec is elaboration-time only. The current
IR spec does not fully enumerate the hardened metadata required for runtime/DFX
(instance paths, port naming/layouts, probe maps, etc.).

**Decision (required changes)**
- Define a stable hardened metadata surface in MLIR for:
  - hierarchical instance paths/names
  - port/result names + signature/layout identity
  - probe/trace mapping to value slots
  - (if needed) fanout/connectivity metadata for schedulers/DFX

**Source**
- Gap analysis request in #linx-core (2026-03-01).

## Decision 0126: Multi-clock/CDC legality rules must be verified at MLIR level (no combinational cross-domain paths)

**Status:** Accepted

**Context / Goal**
The IR spec claims multi-clock modeling and strict ready/valid semantics, and the
primitive layer includes async FIFO / CDC synchronizers. However, cross-domain
legality constraints are not yet a first-class, verified dialect contract.

**Decision (required changes)**
- Make clock domains explicit where relevant (ops/values annotated or typed).
- Add MLIR verification rules/passes:
  - forbid combinational paths crossing clock domains
  - require CDC to occur only via explicit CDC primitives/ops
  - validate async FIFO and CDC primitive parameter constraints

**Source**
- Gap analysis request in #linx-core (2026-03-01).

## Decision 0127: Define the combinational dependency graph precisely (what is a node/edge; instance crossing; cut points)

**Status:** Accepted

**Context / Goal**
Comb-cycle legality (Decision 0110 / 0123) and depth/WNS proxy (Decision 0119 /
0124) require a single, deterministic definition of the "combinational
dependency graph". Without a precise graph model, different passes/backends will
compute different answers, and dual-backend equivalence will drift.

**Decision (strong constraint / required changes)**
- Define a single canonical **CombDepGraph** for the dialect/toolchain.
- Graph nodes represent *values at observation within tick* (i.e. combinational
  signals/nets after resolution where applicable).
- Graph edges represent *combinational dependence* of a node's value on another
  node's value within the same TICK phase.
- Instance crossing is part of the graph:
  - `pyc.instance` creates edges from caller operands → callee inputs, and from
    callee outputs → caller results.
  - Graph construction must be instance-aware and preserve hierarchical context
    for diagnostics.
- Define **cut points** (break combinational dependence) explicitly; at minimum:
  - `pyc.reg` state boundary (Q is a source; D is a sink)
  - memory state boundaries per the memory op semantics (Decision 0114/0122)
  - CDC/async FIFO boundaries (Decision 0126)

**Implications**
- All comb-cycle checks, depth checks, and scheduler fanout derivation operate on
  the same CombDepGraph definition.

**Source**
- Architecture re-evaluation request in #linx-core (2026-03-01): "先重新看一下4.0的架构变动…逻辑环还有问题的" and follow-up: "把这些都写在decision中".

## Decision 0128: Strong policy: combinational loops are illegal, including those involving net resolution (Z)

**Status:** Accepted

**Context / Goal**
Event-driven tick-to-fixpoint simulation (Decision 0109) plus Z/net resolution
(Decision 0118) can create subtle feedback loops. To keep semantics simple,
backend-stable, and Verilog-equivalent, we must forbid combinational loops in
all forms.

**Decision (strong constraint)**
- Any cycle in the CombDepGraph (Decision 0127) is **illegal by default**.
- This includes cycles that arise through:
  - explicit SSA/wire backedges (`pyc.wire/pyc.assign`)
  - cross-instance combinational paths
  - multi-driver net resolution / tri-state networks (Z)
- Designs requiring feedback must express it using explicit stateful elements
  (regs/mems/explicit sequential primitives) so the loop is cut by a transfer
  boundary.

**Implications**
- MLIR verifier must report cycles with a hierarchical path to the responsible
  ops/ports.
- Runtime iteration cap remains as a safety net, but well-formed IR must not
  rely on it.

**Source**
- Architecture re-evaluation request in #linx-core (2026-03-01).

## Decision 0129: 4-valued encoding is (value_bits, known_mask, z_mask); equality/version/signature cover all three

**Status:** Accepted

**Context / Goal**
Decision 0116/0118 require X and Z. Decision 0117 currently specifies an X-aware
2-vector model, but Z requires an explicit third component to keep C++ sim and
Verilog emission logically equivalent and to make event-driven change detection
sound.

**Decision (strong constraint / required changes)**
- Represent each logic value as three equal-width bitvectors:
  - `value_bits`: payload for known 0/1 bits
  - `known_mask`: 1 = known, 0 = unknown (X or Z)
  - `z_mask`: 1 = Z (high-impedance), 0 = not-Z
- Invariants:
  - `z_mask` implies unknown: for any bit, if `z_mask=1` then `known_mask=0`.
  - state variables/regs/mems must have `z_mask==0` unless explicitly modeled.
- Semantic equality/change/version/signature must be defined over the full
  triple:
  - `eq(a,b)` iff all three bitvectors match exactly
  - `changed(a,b)` = !eq(a,b)
  - version bumps iff changed
  - signatures/hashes include all three bitvectors, with full-compare fallback

**Implications**
- Makes event-driven wakeup decisions sound under Z/X.
- Provides a stable serialization for probe/trace and dual-backend comparison.

**Source**
- Architecture re-evaluation request in #linx-core (2026-03-01).

## Decision 0130: Net/var split is enforced: single-driver vars; resolved nets; resolve occurs during tick before TICK-OBS

**Status:** Accepted

**Context / Goal**
Supporting Z and multi-driver resolution requires distinguishing resolved nets
from single-driver variables/state. Without an explicit, verified split, C++ and
Verilog will diverge (e.g. last-writer-wins vs Verilog resolution).

**Decision (strong constraint / required changes)**
- The dialect defines two connectivity classes:
  - **var/state**: exactly one driver; no resolution; may not take Z.
  - **net**: may have multiple drivers; resolution is applied using Decision 0118.
- Add an MLIR verifier:
  - reject var/state values with 0 or >1 drivers
  - reject illegal Z production on var/state
- Define a canonical simulation order for nets:
  - net resolution is conceptually performed during `tick()` as part of the
    within-cycle convergence, producing resolved net values.
  - **TICK-OBS samples resolved nets** (resolution has happened).

**Implications**
- Makes both backends generate/interpret the same resolved value at sampling.

**Source**
- Architecture re-evaluation request in #linx-core (2026-03-01).

## Decision 0131: Depth/WNS proxy counting rules must account for net resolution and instance I/O

**Status:** Accepted

**Context / Goal**
Depth (Decision 0119/0124) must remain meaningful once nets and resolution are
introduced (Decision 0118/0130). Otherwise tri-state/resolution-heavy designs
will be mischaracterized.

**Decision (required changes)**
- Depth counting rules must explicitly include:
  - instance input→output propagation (depth flows through instance boundaries)
  - net resolution cost (resolution contributes a defined depth increment)
  - memory read semantics per memory kind

**Source**
- Architecture re-evaluation request in #linx-core (2026-03-01).

## Decision 0132: Hardened metadata must include the information needed to build CombDepGraph and diagnostics (paths, ports, probe maps)

**Status:** Accepted

**Context / Goal**
CombDepGraph-based verification/analysis and strong DFX require stable metadata
(Decision 0111/0125). The metadata must be sufficient not just for codegen, but
also for graph construction and hierarchical diagnostics.

**Decision (required changes)**
- Harden MLIR metadata for at least:
  - stable hierarchical instance identifiers/paths (with shortening rules from
    Decision 0002)
  - stable port/result naming and signature/layout identities
  - probe/trace maps that identify value/state slots and observation points
  - enough connectivity information to attribute graph edges back to IR/paths

**Source**
- Architecture re-evaluation request in #linx-core (2026-03-01).

## Decision 0133: Python control-flow is allowed, but must lower to static hardware; dynamic SCF is forbidden post-lowering

**Status:** Accepted

**Context / Goal**
pyc4.0 wants a Pythonic, serial-programming frontend with `if`/`for` and helper
functions, while keeping the backend IR as fully static hardware for scalable
verification, scheduling, and emission.

The current toolchain already follows this direction:
- `pyc-lower-scf-static` unrolls constant-bounded loops and lowers `scf.if` to
  mux networks.
- `pyc-check-no-dynamic` rejects residual `scf.*` and `index` values.

We make this a stable contract for pyc4.0.

**Decision (strong constraint / required changes)**
- The Python frontend MAY expose structured control flow (`if`/`for`) and helper
  functions as ergonomic authoring features.
- The backend MLIR pipeline MUST fully lower these constructs into static PYC
  hardware ops:
  - `scf.for` must be statically unrolled (bounds/step are compile-time
    constants; induction variable must not be used in hardware computations).
  - `scf.if` must lower to a mux network by speculating both branches and muxing
    yielded values (side-effect-free within branches), or be constant-folded.
- After lowering, dynamic control flow MUST NOT remain:
  - Any remaining `scf.*` ops or `index`-typed SSA values are compilation errors
    (`pyc-check-no-dynamic` is a required gate).

**Implications**
- "Loop" in Python source is an *authoring convenience* only; it does not imply
  a runtime loop in the generated hardware IR.
- This contract enables deterministic comb/depth analysis and backend-stable
  emission.

**Source**
- User question in #linx-core (2026-03-01): "但是我们有jit，展开成ssa pyc ir后还有loop吗？"

## Decision 0134: Comb-cycle legality must be instance-aware; InstanceOp is NOT a combinational cut point

**Status:** Accepted

**Context / Goal**
The existing prototype comb-cycle checker (`pyc-check-comb-cycles`) builds a
wire/assign dependency graph and currently treats `pyc.instance` as a
"sequential" definition, which cuts dependencies and can hide cross-instance
combinational cycles.

In pyc4.0, module boundaries are preserved as SimObjects (Decision 0001), but
combinational semantics must remain combinational across module boundaries.

**Decision (strong constraint / required changes)**
- `pyc.instance` MUST NOT be treated as a combinational cut point.
- CombDepGraph construction (Decision 0127) MUST traverse instance boundaries:
  - caller operand → callee input edges
  - callee output → caller result edges
- Only explicitly stateful primitives (reg/mem/fifo/async_fifo/cdc_sync/etc.)
  may act as combinational cut points.
- The comb-cycle verifier MUST report cycles with hierarchical context:
  - instance path segments (shortened per Decision 0002)
  - port/result names where the dependence crosses an instance boundary

**Implications**
- Fixes "logic loop still exists" cases that occur across module boundaries.
- Ensures the IR-level legality contract matches both C++ sim and Verilog
  semantics.

**Source**
- Architecture re-evaluation in #linx-core (2026-03-01): "逻辑环还有问题的" + inspection of current pass behavior.

## Decision 0135: Logic-depth (WNS/TNS proxy) analysis must propagate through instance boundaries

**Status:** Accepted

**Context / Goal**
The current `pyc-check-logic-depth` prototype treats `pyc.instance` as a
sequential op (depth cut), which underestimates depth in hierarchical designs.
This breaks the intent of depth-based compile-time gating (Decision 0119).

**Decision (strong constraint / required changes)**
- Depth analysis is defined over the same instance-aware CombDepGraph (Decision
  0127/0134).
- `pyc.instance` does not reset depth; depth contributions of a callee propagate
  to the caller outputs.
- The endpoint set for depth/WNS/TNS must include:
  - module return values (primary outputs)
  - `pyc.assert` conditions
  - sequential-op inputs that form next-state/commit boundaries (reg D, mem
    write inputs, fifo control/data inputs)

**Implications**
- Depth gates become stable and meaningful on large hierarchical designs.

**Source**
- User direction in #linx-core (2026-03-01): compile-time depth thresholds + current pass inspection.

## Decision 0136: Type inference belongs in the frontend; MLIR backend enforces a flat emission contract via gates

**Status:** Accepted

**Context / Goal**
The existing flow relies on frontend inference (literal widths, implicit
zero/sign-extension for convenience) and a backend gate (`pyc-check-flat-types`)
that enforces that only emission-safe types remain.

In pyc4.0 we will likely introduce richer *authoring* types (bundles/structs and
4-valued logic), but the emission backends still benefit from a flat, uniform IR
surface.

**Decision (strong constraint / required changes)**
- Frontend responsibilities:
  - infer widths/sign where ergonomic (e.g. literal width defaults)
  - track structured interfaces (bundle/struct) and lower them deterministically
    to flat wires before emission
  - attach hardened metadata needed for layout/field mapping (Decision 0125/0132)
- Backend responsibilities:
  - run required gates that reject unsupported residual types or dynamics:
    - `pyc-check-no-dynamic`: no `scf.*`/`index`
    - `pyc-check-flat-types`: only allowed lowered emission types
- Any new "authoring types" introduced for pyc4.0 MUST have a lowering plan to
  the flat emission surface.

**Implications**
- Keeps `pycc` emitters simple and stable, while allowing a Pythonic frontend.

**Source**
- User note in #linx-core (2026-03-01): "很多类型是可以推导出来的".

## Decision 0137: Frontend must provide explicit constructs for nets/multi-driver intent; do not rely on wire/assign hacks

**Status:** Accepted

**Context / Goal**
The prototype uses `pyc.wire/pyc.assign` both for SSA backedges and as an
implicit mechanism that could be (ab)used to encode multi-driver connectivity.
With pyc4.0 adding Z/resolution and strict comb-cycle legality, we need explicit
frontend constructs so intent is unambiguous and verifiable.

**Decision (required changes)**
- Provide explicit frontend APIs/IR forms to express:
  - single-driver variables/state (var)
  - multi-driver resolved nets (net)
  - tri-state drive (drive with enable producing Z when disabled)
- `pyc.wire/pyc.assign` should remain a backedge/SSA plumbing mechanism, but
  MUST NOT be the primary user-facing way to express multi-driver resolution.

**Implications**
- Makes net resolution semantics verifiable (Decision 0130) and avoids accidental
  comb loops due to ambiguous modeling.

**Source**
- User direction in #linx-core (2026-03-01): "好，把改进意见写成decision，尽可能详细".

## Decision 0138: Interface-first authoring: first-class Bundle/Struct types with deterministic flattening + one-shot probe expansion

**Status:** Accepted

**Context / Goal**
Large designs (e.g. LinxCore) are dominated by structured stage interfaces.
Authoring must be Pythonic and low-boilerplate: users should define interfaces
once and reuse them across modules, while the backend still emits flat wires.
DFX should be easy: probing a bundle should automatically expand into stable,
field-named probes.

**Decision (strong constraint / required changes)**
- Provide first-class authoring-time aggregate types (Bundle/Struct) with:
  - stable field order (schema-defined)
  - per-field metadata (width, signed intent, logic-kind)
  - deterministic flattening to the flat emission surface (Decision 0136)
- Add a frontend API to "probe a bundle" in one call:
  - bundle probe expands into per-field probes with stable names
  - probe expansion supports hierarchical prefixes and stage/lane naming helpers

**Implications**
- Reduces code volume drastically for large pipelines.
- Makes DFX naming stable and script-friendly.

**Source**
- User goal in #linx-core (2026-03-01): "pyc需要变得更加pythonic…probe，trace各种dfx能力在前端的扩展".

## Decision 0139: Const/template system must support canonicalizable const data structures + deterministic caching keys

**Status:** Accepted

**Context / Goal**
pyc4.0 needs a compile-time metaprogramming layer similar to C++ templates:
const data structures drive specialization and generate large hardware graphs.
All const computation must finish during JIT, and results must be usable as
stable cache keys for incremental builds.

**Decision (strong constraint / required changes)**
- Define a canonical const value domain that is fully serializable and stable:
  - primitives: bool/int/str/None
  - containers: tuple/list/dict with deterministic key ordering
  - frozen dataclasses / user objects via an explicit "template value" hook
- All `@const` evaluation MUST be complete during JIT and MUST NOT emit hardware
  IR or mutate module interfaces.
- Provide deterministic canonicalization of const values used in specialization:
  - identical semantic const values must produce identical cache keys
  - diagnostics must pinpoint the non-canonical/non-serializable value site

**Implications**
- Enables fast incremental rebuilds for huge designs.

**Source**
- User goal in #linx-core (2026-03-01): "它需要有const数据结构，就像c++ template一样。const也可以计算，但是都是在jit时间内算完".

## Decision 0140: DFX is a first-class frontend extension point: probes include observation point + tags; trace is configurable by instance globs

**Status:** Accepted

**Context / Goal**
Ultra-large designs require scalable DFX: probes and traces must be easy to add,
selectively enable, and correlate with hierarchy. pyc4.0 also requires
observation-point stability (Decision 0121) to keep C++/Verilog traces
comparable.

**Decision (strong constraint / required changes)**
- Frontend probe API MUST support:
  - explicit sampling point: `at = tick|xfer` (mapped to TICK-OBS/XFER-OBS)
  - optional tags (stage/lane/family) to drive downstream tools (pipeview, occ)
  - bundle expansion (Decision 0138)
- Trace configuration MUST support:
  - enable/disable by hierarchical instance glob
  - enable/disable by probe tags/families
  - bounded sampling windows and trigger conditions (for cosim/debug loops)
- Harden probe/trace maps into MLIR metadata (Decision 0132).

**Implications**
- Large designs can be debugged without hand-wiring `dbg__*` ports everywhere.

**Source**
- User goal in #linx-core (2026-03-01): "trace各种dfx能力在前端的扩展…debug也容易".

## Decision 0141: Incremental build is a core requirement: multi-layer caches + stable naming/layout must minimize rebuild scope

**Status:** Accepted

**Context / Goal**
To ramp up quickly on large designs, we need incremental builds: changes should
recompile only affected modules/passes and reuse cached artifacts whenever
possible.

**Decision (strong constraint / required changes)**
- Define a project build graph with deterministic cache keys:
  - Python/JIT stage: (source hash, const params key, dependency hashes)
  - MLIR stage: (input MLIR hash, pass-pipeline hash, compile options)
  - C++ stage: object cache keyed by (generated source hash, toolchain flags)
- Enforce stable naming and stable layout flattening so non-semantic changes do
  not invalidate caches (Decision 0125/0132/0138).
- The build system MUST support "compile only the touched modules" and avoid
  relinking/regenerating the world unless needed.

**Implications**
- Makes iteration on LinxCore-scale designs practical.

**Source**
- User goal in #linx-core (2026-03-01): "He needs incremental build…快速ramp up起来".

## Decision 0142: Cosim integration is a first-class workflow: standardize DUT commit/retire bundle + protocol schema versioning + DFX dump on mismatch

**Status:** Accepted

**Context / Goal**
LinxCore already uses a QEMU lockstep cosim protocol (M1) based on commit traces
and sparse memory snapshots. pyc4.0 should generalize this into a standard
workflow so other designs can reuse it, and so mismatch diagnosis becomes faster.

**Decision (strong constraint / required changes)**
- Standardize a DUT-facing "commit/retire bundle" schema in the pyc ecosystem:
  - minimum fields: pc/insn/len/next_pc + wb + mem + trap groups (as in M1)
  - allow extension fields (uop_uid/block_bid/etc.) without breaking tools
- Cosim protocol MUST include schema identification/versioning:
  - `start` message carries `commit_schema_id` (or equivalent)
  - runner validates schema compatibility and fails early with a clear message
- On mismatch, tooling MUST support automatic DFX dump:
  - dump a configured set of probes (by tags/globs) around the mismatch window
  - include instance-path context and observation points

**Implications**
- Reduces time-to-root-cause by turning mismatches into rich, actionable
  diagnostics.

**Source**
- User goal in #linx-core (2026-03-01): "能和qemu进行cosim…probe，trace各种dfx能力…debug也容易" + existing LinxCore lockstep protocol.

## Decision 0143: Bundle/Struct flattening rules are deterministic and tool-visible (layout ID, field paths, packing)

**Status:** Accepted

**Context / Goal**
Decision 0138 introduces Bundle/Struct authoring types, but incremental build,
DFX, and backend emission require deterministic, standardized flattening:
- two identical schemas must flatten identically across runs
- tooling must be able to map (bundle_field_path) ↔ (flat wire slice)

**Decision (strong constraint / required changes)**
- Define canonical flattening rules:
  - field order is schema order (no hash-map iteration)
  - packing is MSB-first within each field; bundle concatenation order is
    schema order (documented)
  - no implicit padding unless explicitly requested by schema
- Every flattened aggregate MUST carry:
  - `layout_id`: stable hash of the schema (field names + widths + logic-kind)
  - a field map: (field_path → [lsb,width]) suitable for probes and emitters
- Emitters MUST preserve field-path names for DFX (Decision 0140) and must not
  reorder fields.

**Implications**
- Enables stable cache keys, stable probe names, and deterministic codegen.

**Source**
- Follow-up in #linx-core (2026-03-01): "好继续补" after Bundle/probe decisions.

## Decision 0144: Const canonicalization algorithm is standardized; failures produce pinpoint diagnostics

**Status:** Accepted

**Context / Goal**
Const/template data drives specialization and incremental caching (Decision
0139/0141). Without a standardized canonicalization algorithm, semantically
identical const values may miss caches, and debugging const failures becomes
painful.

**Decision (strong constraint / required changes)**
- Define a single canonicalization algorithm for const values:
  - dict keys are sorted by their canonical string form
  - tuples/lists are canonicalized element-wise
  - frozen dataclasses/user objects canonicalize via an explicit template hook
  - forbid non-deterministic values (e.g. object ids, file handles, lambdas)
- Canonicalization failures MUST produce diagnostics that include:
  - python source location (file:line)
  - const path (e.g. `params.cfg.table[3].opcode`)
  - the rejected value type and guidance to fix

**Implications**
- Makes const metaprogramming scale to very large generators.

**Source**
- Follow-up in #linx-core (2026-03-01): "好继续补" after const/template decisions.

## Decision 0145: Trace configuration DSL: instance globs + tags + triggers + sampling windows

**Status:** Accepted

**Context / Goal**
Decision 0140 requires configurable tracing. Large designs need a concise DSL to
enable trace/probes without editing design code, and to capture short windows
around triggers (especially for cosim mismatches).

**Decision (strong constraint / required changes)**
- Define a trace configuration DSL (file or CLI flag) that supports:
  - hierarchical instance glob patterns (with the same shortening rules as
    Decision 0002)
  - probe tags/families selection (pv/occ/custom)
  - triggers (first commit at PC==X, mismatch event, user predicate)
  - sampling windows (N cycles before/after trigger, max bytes/events)
- Trace config MUST be consumable by both:
  - C++ runtime tracing
  - Verilog/Verilator tracing

**Implications**
- Enables "turn on just enough trace" workflows for fast bug isolation.

**Source**
- Follow-up in #linx-core (2026-03-01): "好继续补" after DFX decisions.

## Decision 0146: Cosim commit bundle compatibility rules: unknown fields are ignored; groups obey validity gating; schema evolution is non-breaking

**Status:** Accepted

**Context / Goal**
Cosim commit/retire bundles (Decision 0142) will evolve. Tools must remain
compatible across versions while still comparing the architectural essentials.

**Decision (strong constraint / required changes)**
- Commit bundle fields are grouped with explicit validity gating:
  - `wb_*` fields only compared if `wb_valid==1`
  - `mem_*` fields only compared if `mem_valid==1`
  - `trap_*` fields only compared if `trap_valid==1`
- Unknown/extra fields in a commit record MUST be ignored by older runners
  (forward-compatible parsing).
- Schema evolution rules:
  - adding optional fields is non-breaking
  - changing meaning/units of an existing field requires a new schema id

**Implications**
- Prevents toolchain lockstep breakage when adding DFX-friendly fields.

**Source**
- LinxCore M1 protocol normalization rules + follow-up in #linx-core (2026-03-01).

## Decision 0147: Generated C++/MLIR artifacts must be stable under non-semantic edits to maximize incremental build hit rate

**Status:** Accepted

**Context / Goal**
Incremental build (Decision 0141) depends on stable artifacts. Non-semantic
source edits (formatting, variable renames) should not cause widespread rebuilds
or invalidate probe paths.

**Decision (strong constraint / required changes)**
- Enforce stable naming policies where possible:
  - module symbol names derive from (base_name + params_hash) deterministically
  - temporary SSA names are not part of cache keys
  - debug/probe names come from explicit user names and schema paths, not from
    transient compiler-generated identifiers
- Layout IDs (Decision 0143) and schema IDs (Decision 0146) are the stable
  anchors for caching and tool compatibility.

**Implications**
- Keeps iteration speed acceptable on LinxCore-scale projects.

**Source**
- Follow-up in #linx-core (2026-03-01): "好继续补" after incremental build decisions.

## Decision 0148: State write-intent probes read the storage itself at the post-transfer observation point

**Status:** Accepted

**Context / Goal**
Decision 0053 requires an explicit "a write happened" signal independent of whether
the value changed, which needs both a validity flag and the written value. Two-phase
state only publishes `q` at `transfer()`, so probes were bound to a sampled-next-value
copy (`qNext`) held next to every register. For the single-phase (tick-fused) lanes
that copy exists purely for observation: nobody reads it unless a trace writer is
attached, yet it is stored every edge.

**Decision (strong constraint / required changes)**
- Write-intent probes for state in a `RegLoop` bind the lane's own `q` as write data
  and declare `write_post_transfer`; the trace writer emits those `Write` events in
  the transfer (`XFER-OBS`) phase block.
- Write data that is captured at the edge (memory write ports, delay heads, registers
  still on the per-object two-phase path) stays in the tick (`TICK-OBS`) block.
- The write-intent validity flag MUST remain observable at `XFER-OBS`: `commit()` does
  not clear it per lane; the next edge's compute overwrites it. A per-edge
  "commit already applied" guard keeps the falling-edge `commit()` O(1).
- A probe MUST NOT require an extra copy of authoritative state. New observation
  surfaces read the storage, they do not shadow it.

**Implications**
- The observation-only copy of state disappears for single-phase lanes, along with the
  per-lane write-intent mirror bookkeeping.
- Within a cycle the `Write` chunks now appear after the commit `CycleBegin` boundary.
  The stream stays self-describing (the phase boundary carries the observation point),
  so the schema id does not change.
- Tick-fused lanes keep their validity flag from the in-compute commit until the next
  edge; it is therefore only defined at an edge's post-transfer point. Writers must
  sample at an observation point, never mid-cycle.

**Source**
- Single-phase commit simplification: make the fused update path the mainline and let
  the two-phase path be the fallback for feedback, memories, delay lines and CDC.
