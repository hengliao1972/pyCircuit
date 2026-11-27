#pragma once

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Transforms/Passes.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/BuiltinOps.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/ADT/StringSet.h"

#include <cstddef>
#include <optional>

namespace pyc {

// Shared stat-attribute helpers for the state-optimization passes.
inline void setI64Attr(mlir::Operation *op, llvm::StringRef name,
                       std::int64_t value) {
  op->setAttr(name, mlir::IntegerAttr::get(
                        mlir::IntegerType::get(op->getContext(), 64), value));
}

inline std::int64_t getI64Attr(mlir::Operation *op, llvm::StringRef name,
                               std::int64_t fallback = 0) {
  if (auto attr = op->getAttrOfType<mlir::IntegerAttr>(name))
    return attr.getInt();
  return fallback;
}

/// Stateful ops terminate combinational scheduling: registers, delay lines,
/// memories, FIFOs, CDC syncs, and instance boundaries.
bool isStatefulConsumer(mlir::Operation *op);

/// Semantic hash of a value (resolved through aliases; constants by literal).
std::size_t semanticValueHash(mlir::Value value);

bool isCycleBalanceGenerated(mlir::Operation *op);
bool shouldKeepStateOptimization(mlir::Operation *op);
/// True when `pyc.observe_lazy` marks an undeclared name as on-read lookup.
bool isObserveLazy(mlir::Operation *op);
/// True for a non-lazy, non-cycle-balance `pyc.name` that must stay an eager probe.
bool hasStableStateName(mlir::Operation *op);

/// True when `op` carries an identity that probe/VCD/`dut.read` may consume.
/// `cycle_balance` auto-names are excluded; other observation attrs still count.
bool hasExternalObservationIdentity(mlir::Operation *op);

/// True when the state op or any alias of `q` has an external observation
/// identity that must remain readable after a rewrite.
bool stateHasExternalObservation(mlir::Operation *state, mlir::Value q);

/// Copy name/debug/probe/trace/observable attrs, skipping cycle-balance names.
void copyExternalObservationAttrs(mlir::Operation *from, mlir::Operation *to);

/// Attach a read-only alias of `source` that carries `attrSource`'s external
/// observation identity. Used when a rewrite deletes or replaces the physical
/// state but must leave a value for external reads.
pyc::AliasOp materializeObservationAlias(mlir::OpBuilder &builder,
                                         mlir::Location loc, mlir::Value source,
                                         mlir::Operation *attrSource);

/// Materialize observation aliases for identities that live on `oldState`
/// itself. Aliases of `oldQ` are left to RAUW or explicit remap of erased
/// aliases.
void remapStateOpIdentity(mlir::OpBuilder &builder, mlir::Operation *oldState,
                          mlir::Value newSource);

mlir::Value stripStateAliases(mlir::Value value);
bool equivalentStateValue(mlir::Value lhs, mlir::Value rhs);

struct StateChainLink {
  pyc::RegOp predecessor;
  llvm::SmallVector<pyc::AliasOp> aliasesFromConsumerToProducer;
};

/// Match a proven serial state chain: `consumer.next` reaches `keyReg`'s q
/// only through transparent aliases, with equivalent clock/reset/enable/init.
/// Read-only fanout off intermediate stages is allowed; the rewrite turns it
/// into fixed-depth delay taps.
std::optional<StateChainLink>
matchStateChainPredecessor(pyc::RegOp consumer, pyc::RegOp keyReg);

bool equivalentDelayLineState(pyc::DelayLineOp lhs, pyc::DelayLineOp rhs);
std::size_t delayLineStateHash(pyc::DelayLineOp delay);

/// Attribute names used to carry lazy probe metadata on IR.
llvm::StringRef lazyProbeSlicesAttrName();
llvm::StringRef lazyProbeWiresAttrName();

/// Where an observation name is served from: packed state storage (a slice of
/// a reg/delay_line at `lsb`, optionally a delay tap). `storage` is the owning
/// reg/delay_line op; `q` is its result value; `tap` is the delay_tap result
/// when the slice reads through a tap (null otherwise).
struct StateStorageSource {
  mlir::Operation *storage = nullptr;
  mlir::Value q{};
  unsigned lsb = 0;
  int64_t tapDepth = -1;
  mlir::Value tap{};
};

/// Resolve `value` through aliases, packed extracts, and comb yields down to
/// the reg/delay_line storage that produces it. Nullopt when the value is not
/// backed by state storage.
std::optional<StateStorageSource>
findStateStorageSource(mlir::Value value);

/// Resolve `value` to the host op whose emitted C++ member a named lookup
/// should point at (a comb result or a plain op result). Nullopt for state
/// ops, which are served via `findStateStorageSource` instead.
struct WireProbeHostSource {
  mlir::Operation *op = nullptr;
  unsigned resultIndex = 0;
};

std::optional<WireProbeHostSource> findWireProbeHost(mlir::Value value);

/// Append a `pyc.lazy_probe_slices` entry for `name` on `storage` (idempotent).
mlir::LogicalResult
appendLazyProbeSlice(mlir::Operation *storage, llvm::StringRef name,
                     unsigned lsb, unsigned width, int64_t tapDepth);

/// Append a `pyc.lazy_probe_wires` entry for `name` on `host` (idempotent).
mlir::LogicalResult appendLazyProbeWire(mlir::Operation *host,
                                        llvm::StringRef name, unsigned width,
                                        unsigned resultIndex);

/// One probe-plan alias: canonical path -> source path.
struct ProbePlanAlias {
  std::string canonicalPath;
  std::string sourcePath;
};

/// Loaded observation plans shared by demand/placement/emit consumers.
struct ObservationPlans {
  /// probe_plan.json `top_symbol` (empty when no plan was loaded).
  std::string probeTopSymbol;
  /// probe_plan.json aliases (all modules).
  llvm::SmallVector<ProbePlanAlias> probeAliases;
  /// trace_codegen_plan.json: module -> selected internal fields.
  llvm::StringMap<llvm::StringSet<>> traceFieldsByModule;
};

/// Load and validate both plan files. Empty paths are skipped. Emits errors
/// on `module` and returns failure for malformed input.
mlir::LogicalResult loadObservationPlans(mlir::ModuleOp module,
                                         llvm::StringRef probePlanPath,
                                         llvm::StringRef traceCodegenPlanPath,
                                         ObservationPlans &out);

/// Probe-plan aliases targeting `topSymbol`, sorted by canonical path.
llvm::SmallVector<ProbePlanAlias>
probeAliasesForTop(const ObservationPlans &plans, llvm::StringRef topSymbol);

} // namespace pyc
