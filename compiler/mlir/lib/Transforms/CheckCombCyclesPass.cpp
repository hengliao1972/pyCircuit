#include "pyc/Transforms/CombDepGraph.h"
#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Support/raw_ostream.h"

using namespace mlir;

namespace pyc {
namespace {

static std::string valueLabel(Value value) {
  if (Operation *producer = value.getDefiningOp()) {
    if (auto n = producer->getAttrOfType<StringAttr>("pyc.name"))
      return n.getValue().str();
  }
  std::string s;
  llvm::raw_string_ostream os(s);
  value.print(os);
  os.flush();
  return s;
}

struct CheckCombCyclesPass
    : public PassWrapper<CheckCombCyclesPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CheckCombCyclesPass)

  StringRef getArgument() const override { return "pyc-check-comb-cycles"; }
  StringRef getDescription() const override {
    return "Detect same-tick cycles in the canonical combinational dependency "
           "graph";
  }

  void runOnOperation() override {
    ModuleOp module = getOperation();
    CombDepGraphCache combCache(module);

    for (func::FuncOp f : module.getOps<func::FuncOp>()) {
      if (f.isDeclaration() || f.getBody().empty())
        continue;

      auto graph = FunctionCombDepGraph::build(f, combCache);
      if (failed(graph)) {
        signalPassFailure();
        return;
      }

      llvm::SmallVector<unsigned> cycle;
      if (succeeded((*graph)->stableTopologicalOrder(&cycle)))
        continue;

      std::string message;
      llvm::raw_string_ostream os(message);
      os << "combinational cycle detected";
      if (!cycle.empty()) {
        os << ": ";
        auto nodes = (*graph)->getNodes();
        for (auto [index, nodeId] : llvm::enumerate(cycle)) {
          if (index)
            os << " -> ";
          os << valueLabel(nodes[nodeId].value);
        }
      }
      os.flush();
      f.emitError(message);
      signalPassFailure();
      return;
    }
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCheckCombCyclesPass() {
  return std::make_unique<CheckCombCyclesPass>();
}

static PassRegistration<CheckCombCyclesPass> pass;

} // namespace pyc
