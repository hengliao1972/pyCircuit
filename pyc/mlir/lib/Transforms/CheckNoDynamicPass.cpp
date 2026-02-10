#include "pyc/Transforms/Passes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/Pass/Pass.h"

using namespace mlir;

namespace pyc {
namespace {

struct CheckNoDynamicPass : public PassWrapper<CheckNoDynamicPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CheckNoDynamicPass)

  StringRef getArgument() const override { return "pyc-check-no-dynamic"; }
  StringRef getDescription() const override {
    return "Validate that no dynamic control-flow (scf) or index-typed values remain (prototype)";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();

    auto failOnType = [&](Type ty, Location loc, StringRef what) -> bool {
      if (!ty || !ty.isIndex())
        return false;
      emitError(loc) << what << " has forbidden type `index` (expected only !pyc.clock/!pyc.reset/iN)";
      return true;
    };

    bool failedAny = false;
    for (BlockArgument arg : f.getArguments())
      failedAny |= failOnType(arg.getType(), f.getLoc(), "function argument");
    for (Type ty : f.getResultTypes())
      failedAny |= failOnType(ty, f.getLoc(), "function result");

    f.walk([&](Operation *op) {
      if (isa<scf::SCFDialect>(op->getDialect())) {
        op->emitError("dynamic SCF op remains after lowering: ") << op->getName();
        failedAny = true;
        return WalkResult::interrupt();
      }
      if (isa<arith::ConstantOp>(op)) {
        for (Value r : op->getResults())
          failedAny |= failOnType(r.getType(), op->getLoc(), "arith.constant result");
      }
      for (Value v : op->getOperands())
        failedAny |= failOnType(v.getType(), op->getLoc(), "operand");
      for (Value v : op->getResults())
        failedAny |= failOnType(v.getType(), op->getLoc(), "result");
      return WalkResult::advance();
    });

    if (failedAny)
      signalPassFailure();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCheckNoDynamicPass() { return std::make_unique<CheckNoDynamicPass>(); }

static PassRegistration<CheckNoDynamicPass> pass;

} // namespace pyc

