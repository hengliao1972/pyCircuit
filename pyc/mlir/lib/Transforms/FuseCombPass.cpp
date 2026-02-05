#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/IRMapping.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/STLExtras.h"

using namespace mlir;

namespace pyc {
namespace {

static bool isFusableCombOp(Operation *op) {
  return isa<pyc::ConstantOp,
             pyc::AddOp,
             pyc::SubOp,
             pyc::MulOp,
             pyc::UdivOp,
             pyc::UremOp,
             pyc::SdivOp,
             pyc::SremOp,
             pyc::MuxOp,
             pyc::AndOp,
             pyc::OrOp,
             pyc::XorOp,
             pyc::NotOp,
             pyc::ConcatOp,
             pyc::AliasOp,
             pyc::EqOp,
             pyc::UltOp,
             pyc::SltOp,
             pyc::TruncOp,
             pyc::ZextOp,
             pyc::SextOp,
             pyc::ExtractOp,
             pyc::ShliOp,
             pyc::LshriOp,
             pyc::AshriOp,
             arith::SelectOp>(op);
}

struct FuseCombPass : public PassWrapper<FuseCombPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(FuseCombPass)

  StringRef getArgument() const override { return "pyc-fuse-comb"; }
  StringRef getDescription() const override {
    return "Fuse consecutive pyc combinational ops into codegen-friendly pyc.comb regions";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();
    for (Block &b : f.getBody())
      fuseBlock(b);
  }

  void fuseBlock(Block &block) {
    llvm::SmallVector<Operation *> run;

    auto flushRun = [&]() {
      if (run.size() < 2) {
        run.clear();
        return;
      }
      fuseRun(run);
      run.clear();
    };

    for (Operation &op : llvm::make_early_inc_range(block)) {
      if (isFusableCombOp(&op)) {
        run.push_back(&op);
        continue;
      }
      flushRun();
    }
    flushRun();
  }

  void fuseRun(ArrayRef<Operation *> run) {
    llvm::DenseSet<Operation *> runSet;
    runSet.reserve(run.size());
    for (Operation *op : run)
      runSet.insert(op);

    // Live-outs: results that are used by an op outside the run.
    llvm::SmallVector<Value> outputs;
    for (Operation *op : run) {
      for (Value r : op->getResults()) {
        bool usedOutside = false;
        for (OpOperand &use : r.getUses()) {
          if (!runSet.contains(use.getOwner())) {
            usedOutside = true;
            break;
          }
        }
        if (usedOutside)
          outputs.push_back(r);
      }
    }
    if (outputs.empty())
      return;

    // External inputs: operands that are not defined by an op in the run.
    llvm::SmallVector<Value> inputs;
    llvm::DenseSet<Value> seenInputs;
    for (Operation *op : run) {
      for (Value v : op->getOperands()) {
        if (Operation *def = v.getDefiningOp()) {
          if (runSet.contains(def))
            continue;
        }
        if (seenInputs.insert(v).second)
          inputs.push_back(v);
      }
    }

    llvm::SmallVector<Type> outTypes;
    outTypes.reserve(outputs.size());
    for (Value v : outputs)
      outTypes.push_back(v.getType());

    OpBuilder builder(run.front());
    auto comb = builder.create<pyc::CombOp>(run.front()->getLoc(), outTypes, inputs);

    // Build a single-block region and clone ops into it.
    Region &region = comb.getBody();
    Block *body = new Block();
    region.push_back(body);
    for (Value in : inputs)
      body->addArgument(in.getType(), comb.getLoc());

    IRMapping mapping;
    for (auto [i, in] : llvm::enumerate(inputs))
      mapping.map(in, body->getArgument(i));

    builder.setInsertionPointToStart(body);
    for (Operation *op : run) {
      Operation *cloned = builder.clone(*op, mapping);
      for (auto [oldRes, newRes] : llvm::zip(op->getResults(), cloned->getResults()))
        mapping.map(oldRes, newRes);
    }

    llvm::SmallVector<Value> yieldVals;
    yieldVals.reserve(outputs.size());
    for (Value out : outputs)
      yieldVals.push_back(mapping.lookup(out));
    builder.create<pyc::YieldOp>(comb.getLoc(), yieldVals);

    // Replace uses outside the run with comb results.
    for (auto [out, res] : llvm::zip(outputs, comb.getResults())) {
      out.replaceUsesWithIf(res, [&](OpOperand &use) { return !runSet.contains(use.getOwner()); });
    }

    for (Operation *op : llvm::reverse(run))
      op->erase();
  }
};

} // namespace

std::unique_ptr<mlir::Pass> createFuseCombPass() { return std::make_unique<FuseCombPass>(); }

static PassRegistration<FuseCombPass> pass;

} // namespace pyc
