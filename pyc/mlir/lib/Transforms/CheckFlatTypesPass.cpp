#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/Pass/Pass.h"

using namespace mlir;

namespace pyc {
namespace {

static bool isAllowedHardwareType(Type t) {
  return isa<IntegerType, pyc::ClockType, pyc::ResetType>(t);
}

struct CheckFlatTypesPass : public PassWrapper<CheckFlatTypesPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CheckFlatTypesPass)

  StringRef getArgument() const override { return "pyc-check-flat-types"; }
  StringRef getDescription() const override {
    return "Verify the PYC IR is fully lowered to flat integer wires (no aggregate/unsupported types remain)";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();

    for (Type t : f.getFunctionType().getInputs()) {
      if (!isAllowedHardwareType(t)) {
        f.emitError() << "unsupported func input type for emission: " << t;
        signalPassFailure();
        return;
      }
    }
    for (Type t : f.getFunctionType().getResults()) {
      if (!isAllowedHardwareType(t)) {
        f.emitError() << "unsupported func result type for emission: " << t;
        signalPassFailure();
        return;
      }
    }

    bool ok = true;
    f.walk([&](Operation *op) {
      for (Type t : op->getOperandTypes()) {
        if (!isAllowedHardwareType(t)) {
          op->emitError() << "unsupported operand type for emission: " << t;
          ok = false;
          return;
        }
      }
      for (Type t : op->getResultTypes()) {
        if (!isAllowedHardwareType(t)) {
          op->emitError() << "unsupported result type for emission: " << t;
          ok = false;
          return;
        }
      }
    });

    if (!ok)
      signalPassFailure();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCheckFlatTypesPass() { return std::make_unique<CheckFlatTypesPass>(); }

static PassRegistration<CheckFlatTypesPass> pass;

} // namespace pyc

