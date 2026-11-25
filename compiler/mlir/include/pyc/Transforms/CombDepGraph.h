#pragma once

#include <cstdint>
#include <memory>
#include <optional>

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Value.h"
#include "mlir/Support/LogicalResult.h"
#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/BitVector.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"

namespace pyc {

// Summary of how a callee's output depends on its inputs within the tick/comb
// phase (i.e. across combinational logic only; sequential/stateful ops are
// cut points).
//
// Depth is expressed in "logic levels" using the same cost model as
// pyc-check-logic-depth:
// - 0 for wiring/aliases/constants
// - 1 for other combinational ops (approximate proxy)
// - instance boundaries are *not* cut points
struct CombResultSummary {
  llvm::BitVector argDeps;
  int64_t baseDepth = -1;                 // max depth from internal (non-arg) sources, -1 if none
  llvm::SmallVector<int64_t> argDepth{};  // max depth from each arg, -1 if unreachable
};

struct FuncCombSummary {
  unsigned numArgs = 0;
  unsigned numResults = 0;
  llvm::SmallVector<CombResultSummary> results;
};

// Cache for per-function combinational dependency summaries used by
// instance-aware verifiers (comb-cycle and logic-depth).
class CombDepGraphCache {
public:
  explicit CombDepGraphCache(mlir::ModuleOp module);

  // Returns nullptr on failure (and emits an error on the relevant op).
  const FuncCombSummary *getFuncSummary(mlir::func::FuncOp func);

private:
  mlir::ModuleOp module_;
  llvm::DenseMap<mlir::Operation *, std::unique_ptr<FuncCombSummary>> cache_;
  llvm::DenseSet<mlir::Operation *> inProgress_;
};

/// The kind of same-TICK dependence represented by a canonical graph edge.
///
/// The graph deliberately distinguishes semantic dependence from physical
/// materialization.  In particular, InstancePort and PrimitiveComb edges are
/// real combinational edges even though an instance/primitive remains a
/// top-level barrier and is never cloned into a pyc.comb region.
enum class CombDepEdgeKind : uint8_t {
  SSA,
  WireDriver,
  CombBoundary,
  InstancePort,
  PrimitiveComb,
};

struct CombDepEdge {
  unsigned source = 0;
  unsigned target = 0;
  CombDepEdgeKind kind = CombDepEdgeKind::SSA;
  mlir::Operation *owner = nullptr;
  unsigned operandIndex = 0;
};

/// A canonical Decision-0127 value node.  Nodes represent values observable
/// during the same tick; edges encode value dependence.  Sequential results
/// are graph sources, while asynchronous primitive results retain their real
/// operand dependencies.
struct CombDepValueNode {
  mlir::Value value;
  mlir::Operation *producer = nullptr;
  unsigned resultIndex = 0;
  unsigned stableOrdinal = 0;
  llvm::SmallVector<unsigned> incomingEdges;
  llvm::SmallVector<unsigned> outgoingEdges;
};

/// Function-local projection of the canonical CombDepGraph.
///
/// Instance results are connected to the caller operands selected by the
/// callee summary, so an instance is not a semantic cut.  Materialization is
/// intentionally module-local: users of this graph may treat InstanceOp as a
/// placement barrier without dropping its graph edges.
class FunctionCombDepGraph {
public:
  static mlir::FailureOr<std::unique_ptr<FunctionCombDepGraph>>
  build(mlir::func::FuncOp func, CombDepGraphCache &cache);

  llvm::ArrayRef<CombDepValueNode> getNodes() const { return nodes_; }
  llvm::ArrayRef<CombDepEdge> getEdges() const { return edges_; }

  std::optional<unsigned> lookupNodeId(mlir::Value value) const;

  /// Deterministic value-node topological order.  Returns failure if the
  /// function contains a same-TICK combinational cycle.  cycleWitness, when
  /// supplied, receives a closed node-id cycle (first == last).
  mlir::FailureOr<llvm::SmallVector<unsigned>>
  stableTopologicalOrder(llvm::SmallVectorImpl<unsigned> *cycleWitness =
                             nullptr) const;

private:
  explicit FunctionCombDepGraph(mlir::func::FuncOp func) : func_(func) {}

  unsigned ensureNode(mlir::Value value);
  void addEdge(mlir::Value source, mlir::Value target, CombDepEdgeKind kind,
               mlir::Operation *owner, unsigned operandIndex);
  mlir::LogicalResult construct(CombDepGraphCache &cache);

  mlir::func::FuncOp func_;
  llvm::SmallVector<CombDepValueNode> nodes_;
  llvm::SmallVector<CombDepEdge> edges_;
  llvm::DenseMap<mlir::Value, unsigned> valueToNode_;
  llvm::DenseSet<uint64_t> edgeKeys_;
};

} // namespace pyc
