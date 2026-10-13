#pragma once

#include <cstdint>

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Operation.h"
#include "mlir/Support/LogicalResult.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"

namespace pyc {

inline constexpr llvm::StringLiteral kChangeScheduleSchema =
    "pyc.change_schedule.v1";
inline constexpr llvm::StringLiteral kChangeScheduleSummaryAttr =
    "pyc.change_schedule.summary";
inline constexpr llvm::StringLiteral kChangeScheduleNodeAttr =
    "pyc.change_schedule.node";
inline constexpr llvm::StringLiteral kChangeScheduleRankAttr =
    "pyc.change_schedule.rank";
inline constexpr llvm::StringLiteral kChangeScheduleSlotAttr =
    "pyc.change_schedule.slot";
inline constexpr llvm::StringLiteral kChangeScheduleFanoutAttr =
    "pyc.change_schedule.fanout";

struct ChangeScheduleNode {
  mlir::Operation *operation = nullptr;
  unsigned resultIndex = 0;
  uint64_t id = 0;
  uint64_t rank = 0;
  uint64_t slot = 0;
  llvm::SmallVector<uint64_t> fanout;
};

struct ChangeSchedulePlan {
  llvm::SmallVector<ChangeScheduleNode> nodes;
  uint64_t edgeCount = 0;
  uint64_t rankCount = 0;
};

/// Builds the deterministic function-local schedule attached to the function.
mlir::FailureOr<ChangeSchedulePlan>
buildChangeDrivenSchedule(mlir::func::FuncOp func);

/// Returns true for the current phase's executable scheduling units:
/// fused comb regions, module instances, and runtime-evaluated primitives.
bool isChangeScheduleNode(mlir::Operation *op);

} // namespace pyc
