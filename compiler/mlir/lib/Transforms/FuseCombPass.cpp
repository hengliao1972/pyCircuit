#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/StateOptimization.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/IRMapping.h"
#include "mlir/Pass/Pass.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/STLExtras.h"

using namespace mlir;

namespace pyc {
namespace {

static bool isFusableCombOp(Operation *op) {
  if (shouldKeepStateOptimization(op) || hasStableStateName(op))
    return false;
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
             pyc::ResetActiveOp,
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
             pyc::ShlOp,
             pyc::LshrOp,
             pyc::AshrOp,
             pyc::VGetOp,
             pyc::VCreateOp,
             pyc::VBroadcastOp,
             pyc::VOrReduceOp,
             pyc::VAndReduceOp,
             pyc::VAddReduceOp,
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
    if (auto structural = f->getAttrOfType<StringAttr>("pyc.emit.structural")) {
      llvm::StringRef v = structural.getValue();
      if (v.equals_insensitive("true") || v == "1")
        return;
    }
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
    llvm::DenseSet<Value> outputSet;
    for (Operation *op : run) {
      for (Value r : op->getResults()) {
        bool usedOutside = false;
        for (OpOperand &use : r.getUses()) {
          if (!runSet.contains(use.getOwner())) {
            usedOutside = true;
            break;
          }
        }
        if (usedOutside && outputSet.insert(r).second)
          outputs.push_back(r);
      }
    }

    // Promote any pyc.lazy_probe_wires host in the run to a live-out so
    // its wired result survives as a comb output. Observation demand
    // hangs the attribute on the alias-stripped producer (see
    // findWireHost in ApplyObservationDemandPass), so a named alias that
    // is the live-out, or a mid-pipeline named op consumed only by other
    // fused ops, would otherwise be cloned into the pyc.comb body and
    // become invisible to the emitter (which skips ops inside pyc.comb).
    for (Operation *op : run) {
      auto wires = op->getAttrOfType<ArrayAttr>("pyc.lazy_probe_wires");
      if (!wires)
        continue;
      for (Attribute item : wires) {
        auto dict = dyn_cast<DictionaryAttr>(item);
        if (!dict)
          continue;
        auto resultAttr = dict.getAs<IntegerAttr>("result");
        const unsigned srcResult =
            resultAttr ? static_cast<unsigned>(resultAttr.getInt()) : 0;
        if (srcResult >= op->getNumResults())
          continue;
        Value r = op->getResult(srcResult);
        if (outputSet.insert(r).second)
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

    // Keep undeclared combinational names lookupable after fusion: they
    // register as addWire of this comb result, not a second named copy.
    SmallVector<Attribute> lazyWires;
    auto copyLazyWires = [&](Value out, int64_t combResult) {
      Operation *def = out.getDefiningOp();
      if (!def)
        return;
      auto wires = def->getAttrOfType<ArrayAttr>("pyc.lazy_probe_wires");
      if (!wires)
        return;
      unsigned outResult = 0;
      if (auto res = dyn_cast<OpResult>(out))
        outResult = res.getResultNumber();
      for (Attribute item : wires) {
        auto dict = dyn_cast<DictionaryAttr>(item);
        if (!dict)
          continue;
        auto resultAttr = dict.getAs<IntegerAttr>("result");
        const unsigned srcResult =
            resultAttr ? static_cast<unsigned>(resultAttr.getInt()) : 0;
        if (srcResult != outResult)
          continue;
        NamedAttrList fields;
        if (auto name = dict.getAs<StringAttr>("name"))
          fields.set("name", name);
        if (auto width = dict.getAs<IntegerAttr>("width"))
          fields.set("width", width);
        fields.set("result", builder.getI64IntegerAttr(combResult));
        lazyWires.push_back(DictionaryAttr::get(def->getContext(), fields));
      }
    };
    for (auto [i, out] : llvm::enumerate(outputs))
      copyLazyWires(out, static_cast<int64_t>(i));
    if (!lazyWires.empty())
      comb->setAttr("pyc.lazy_probe_wires",
                    ArrayAttr::get(comb.getContext(), lazyWires));

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
