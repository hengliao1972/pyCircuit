#pragma once

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Value.h"
#include "mlir/IR/Visitors.h"
#include "llvm/ADT/DenseMap.h"

namespace pyc {

/// Decision 0137: wire/assign is a single driver. Successive Reg.set updates
/// must already have been folded in the frontend, so any leftover multi-drive
/// `pyc.assign` on the same destination is rejected before emission.
inline mlir::LogicalResult rejectMultipleWireDrivers(mlir::func::FuncOp f) {
  llvm::DenseMap<mlir::Value, pyc::AssignOp> firstAssign;
  mlir::LogicalResult result = mlir::success();
  f.walk([&](pyc::AssignOp assign) {
    auto [it, inserted] = firstAssign.try_emplace(assign.getDst(), assign);
    if (inserted)
      return mlir::WalkResult::advance();
    result = assign.emitOpError(
        "has multiple drivers for the same wire; fold successive updates "
        "into one assign (Reg.set / assign(when=)) or use an explicit net");
    return mlir::WalkResult::interrupt();
  });
  return result;
}

} // namespace pyc
