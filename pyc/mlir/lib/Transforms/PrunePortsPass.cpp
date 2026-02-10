#include "pyc/Transforms/Passes.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/SmallVector.h"

using namespace mlir;

namespace pyc {
namespace {

struct PrunePortsPass : public PassWrapper<PrunePortsPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(PrunePortsPass)

  StringRef getArgument() const override { return "pyc-prune-ports"; }
  StringRef getDescription() const override {
    return "Prune unused func arguments (ports) and update call sites (interface-changing; off by default in pyc-compile)";
  }

  void runOnOperation() override {
    ModuleOp m = getOperation();

    llvm::SmallVector<func::FuncOp> funcs;
    m.walk([&](func::FuncOp f) {
      if (!f.isDeclaration())
        funcs.push_back(f);
    });

    for (func::FuncOp f : funcs)
      (void)pruneFuncArgs(m, f);
  }

  bool pruneFuncArgs(ModuleOp module, func::FuncOp f) {
    // Identify unused arguments.
    llvm::SmallVector<unsigned> deadArgs;
    for (unsigned i = 0; i < f.getNumArguments(); ++i) {
      if (f.getArgument(i).use_empty())
        deadArgs.push_back(i);
    }
    if (deadArgs.empty())
      return false;

    // Build a keep-mask for arguments.
    llvm::SmallVector<bool> keep;
    keep.assign(f.getNumArguments(), true);
    for (unsigned i : deadArgs)
      keep[i] = false;

    // Update call sites first (so arg indices refer to the old signature).
    llvm::SmallVector<func::CallOp> calls;
    module.walk([&](func::CallOp call) {
      if (call.getCallee() == f.getSymName())
        calls.push_back(call);
    });

    for (func::CallOp call : calls) {
      llvm::SmallVector<Value> newOperands;
      newOperands.reserve(call.getNumOperands());
      for (auto [i, v] : llvm::enumerate(call.getOperands())) {
        if (i < keep.size() && keep[i])
          newOperands.push_back(v);
      }
      OpBuilder b(call);
      auto newCall = b.create<func::CallOp>(call.getLoc(), call.getCalleeAttr(), call.getResultTypes(), newOperands);
      call.replaceAllUsesWith(newCall);
      call.erase();
    }

    // Update arg_names attribute if present.
    if (auto namesAttr = f->getAttrOfType<ArrayAttr>("arg_names")) {
      llvm::SmallVector<Attribute> newNames;
      newNames.reserve(namesAttr.size());
      for (auto [i, a] : llvm::enumerate(namesAttr)) {
        if (i < keep.size() && keep[i])
          newNames.push_back(a);
      }
      f->setAttr("arg_names", ArrayAttr::get(module.getContext(), newNames));
    }

    // Update function type.
    llvm::SmallVector<Type> newInputs;
    newInputs.reserve(f.getNumArguments());
    for (auto [i, t] : llvm::enumerate(f.getFunctionType().getInputs())) {
      if (i < keep.size() && keep[i])
        newInputs.push_back(t);
    }
    auto newType = FunctionType::get(module.getContext(), newInputs, f.getFunctionType().getResults());
    f.setType(newType);

    // Erase entry block arguments (descending indices).
    Block &entry = f.getBody().front();
    llvm::sort(deadArgs, std::greater<unsigned>());
    for (unsigned i : deadArgs)
      entry.eraseArgument(i);

    return true;
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createPrunePortsPass() { return std::make_unique<PrunePortsPass>(); }

static PassRegistration<PrunePortsPass> pass;

} // namespace pyc

