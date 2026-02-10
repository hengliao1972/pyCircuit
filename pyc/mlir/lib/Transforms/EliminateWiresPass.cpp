#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Dominance.h"
#include "mlir/IR/Operation.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"

using namespace mlir;

namespace pyc {
namespace {

static bool dependsOn(Value root, Value target) {
  if (!root || !target)
    return false;

  llvm::SmallVector<Value> worklist;
  worklist.push_back(root);

  llvm::DenseSet<Value> seen;
  seen.reserve(64);

  while (!worklist.empty()) {
    Value v = worklist.pop_back_val();
    if (v == target)
      return true;
    if (!seen.insert(v).second)
      continue;
    Operation *def = v.getDefiningOp();
    if (!def)
      continue;
    for (Value opnd : def->getOperands())
      worklist.push_back(opnd);
  }

  return false;
}

struct EliminateWiresPass : public PassWrapper<EliminateWiresPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(EliminateWiresPass)

  StringRef getArgument() const override { return "pyc-eliminate-wires"; }
  StringRef getDescription() const override {
    return "Eliminate trivial pyc.wire + pyc.assign pairs (netlist cleanup)";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();
    DominanceInfo dom(f);

    OpBuilder builder(f.getContext());
    bool changed = true;
    while (changed) {
      changed = false;
      // Collect wires up-front since we may erase ops during the walk.
      llvm::SmallVector<pyc::WireOp> wires;
      f.walk([&](pyc::WireOp w) { wires.push_back(w); });
      for (pyc::WireOp w : wires)
        changed |= tryEliminateWire(w, dom, builder);
    }
  }

  bool tryEliminateWire(pyc::WireOp w, DominanceInfo &dom, OpBuilder &builder) {
    Value wireVal = w.getResult();

    llvm::SmallVector<pyc::AssignOp> assigns;
    llvm::SmallVector<OpOperand *> reads;

    for (OpOperand &use : wireVal.getUses()) {
      if (auto a = dyn_cast<pyc::AssignOp>(use.getOwner())) {
        if (use.getOperandNumber() == 0) {
          assigns.push_back(a);
          continue;
        }
      }
      reads.push_back(&use);
    }

    // If the wire is never read, drop it and all of its drivers.
    if (reads.empty()) {
      for (pyc::AssignOp a : assigns)
        a.erase();
      w.erase();
      return true;
    }

    // Only handle the single-driver case for now.
    if (assigns.size() != 1)
      return false;

    pyc::AssignOp driver = assigns.front();
    Value src = driver.getSrc();

    // Avoid creating self-referential definitions.
    if (src == wireVal)
      return false;
    if (dependsOn(src, wireVal))
      return false;

    for (OpOperand *use : reads) {
      if (!dom.dominates(src, use->getOwner()))
        return false;
    }

    Value replacement = src;
    if (auto nameAttr = w->getAttrOfType<StringAttr>("pyc.name")) {
      if (Operation *def = src.getDefiningOp())
        builder.setInsertionPointAfter(def);
      else
        builder.setInsertionPointToStart(src.getParentBlock());
      auto alias = builder.create<pyc::AliasOp>(w.getLoc(), src.getType(), src);
      alias->setAttr("pyc.name", nameAttr);
      replacement = alias.getResult();
    }

    for (OpOperand *use : reads)
      use->set(replacement);

    driver.erase();
    w.erase();
    return true;
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createEliminateWiresPass() { return std::make_unique<EliminateWiresPass>(); }

static PassRegistration<EliminateWiresPass> pass;

} // namespace pyc
