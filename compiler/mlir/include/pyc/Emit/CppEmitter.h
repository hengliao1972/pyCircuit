#pragma once

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Value.h"
#include "mlir/Support/LogicalResult.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/raw_ostream.h"

#include <cstdint>
#include <optional>
#include <string>

namespace pyc {

// ---------------------------------------------------------------------------
// Placement contract between pyc-cpp-placement (writer) and the emitter (reader)
// ---------------------------------------------------------------------------
// These constants name IR attributes stamped by the C++ placement pass and
// consumed by emit + the profile JSON pipeline.

inline constexpr llvm::StringLiteral kCppStorageAttr = "pyc.cpp.storage";
inline constexpr llvm::StringLiteral kCppOwnerAttr = "pyc.cpp.owner";
inline constexpr llvm::StringLiteral kCppMethodAttr = "pyc.cpp.method";
inline constexpr llvm::StringLiteral kCppPlacementSummaryAttr = "pyc.cpp.placement_summary";
inline constexpr llvm::StringLiteral kCppCombChunkNodesAttr = "pyc.cpp.comb_chunk_nodes";

enum class CppStorageKind { Struct, Local };

struct CppPlacementSummary {
  unsigned structMembers = 0;
  /// Comb wires localized as function-local Wire<> purely by comb boundary:
  /// defined inside a comb, not a comb result, not a block arg, no escaping use.
  unsigned localInMethod = 0;
  /// Comb-region values without a defining op (e.g. block args) kept on the struct.
  unsigned probePinnedStruct = 0;
  /// Comb wires that would be local by boundary, but are used across part methods
  /// and therefore promoted to struct members.
  unsigned crossPartPromoted = 0;
  /// Cross-part localizable wires under the chosen schedule/cut (or fallback).
  unsigned scheduledCrossMethod = 0;
  /// Weighted cut cost for the chosen scheduled partition.
  uint64_t scheduledCutWeight = 0;
};

/// Chunk size chosen by `pyc-cpp-placement` (emit + localization read this).
std::optional<unsigned> getModuleCombChunkNodes(::mlir::ModuleOp module);

/// Read per-function summary written by `pyc-cpp-placement` (used by emit as a
/// "did the pass run?" guard and by the profile pipeline).
std::optional<CppPlacementSummary> getFuncPlacementSummary(::mlir::func::FuncOp f);

/// Aggregate per-function placement summaries across the module into totals.
/// Implementation lives in the placement pass TU; used by pycc profile JSON.
CppPlacementSummary accumulateModulePlacementSummary(::mlir::ModuleOp module);

// ---------------------------------------------------------------------------
// CppEmitterOptions + public emit entry points
// ---------------------------------------------------------------------------

struct CppEmitterOptions {
  enum class SplitMode {
    None,
    Module,
  };

  /// Default comb/eval chunk size (pycc placement pass and emitter both use this).
  static constexpr unsigned kDefaultCombChunkNodes = 256;

  SplitMode splitMode = SplitMode::None;
  unsigned shardThresholdLines = 120000;
  unsigned shardThresholdBytes = 4 * 1024 * 1024;
  // Chunk full-topology eval bodies to avoid mega-functions that are expensive
  // for downstream C++ compilers.
  unsigned evalTopoChunkNodes = kDefaultCombChunkNodes;
  // Chunk fused comb helpers to avoid single mega-functions that dominate
  // downstream C++ TU cost even after file sharding.
  unsigned combChunkNodes = kDefaultCombChunkNodes;
  std::string probePlanPath{};
};

::mlir::LogicalResult emitCpp(::mlir::ModuleOp module, ::llvm::raw_ostream &os,
                              const CppEmitterOptions &opts = {});

::mlir::LogicalResult emitCppFunc(::mlir::ModuleOp module, ::mlir::func::FuncOp f, ::llvm::raw_ostream &os,
                                  const CppEmitterOptions &opts = {});

// ---------------------------------------------------------------------------
// Placement readers used by the emitter
// ---------------------------------------------------------------------------

/// Read storage decision for a value (default struct).
CppStorageKind getValueCppStorage(::mlir::Value v);

/// Owner method name for local values (empty if struct or unknown).
::llvm::StringRef getValueCppOwner(::mlir::Value v);

/// Per-emission state for lazy local declarations inside a method body.
struct CppEmitterPlacementState {
  ::llvm::StringRef currentMethod;
  ::llvm::DenseSet<::mlir::Value> declaredLocals;

  void beginMethod(::llvm::StringRef methodName) {
    currentMethod = methodName;
    declaredLocals.clear();
  }

  bool emitLocalDeclIfNeeded(::mlir::Value v, ::mlir::Type ty, ::llvm::StringRef name,
                             ::llvm::raw_ostream &os, unsigned indentSpaces = 4);

  /// Emit `name = expr` or `Wire<w> name = expr` for method-local SSA results.
  void emitValueAssign(::mlir::Value result, ::mlir::Type ty, ::llvm::StringRef name,
                       ::llvm::StringRef expr, ::llvm::raw_ostream &os,
                       unsigned indentSpaces = 4);
};

} // namespace pyc
