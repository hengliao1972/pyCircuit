//===----------------------------------------------------------------------===//
// WorklistDriver.h -- single-seed stack worklist driver for rewrite patterns.
//
// Processing semantics mirror the upstream greedy driver (fold, then match
// patterns, LIFO over a postorder population, constant materialization
// included), with one crucial difference: the users of every replaced value
// are re-enqueued immediately. The upstream greedy driver restarts from a
// full region walk each iteration and never re-enqueues users of replaced
// values, so a folding chain in which each level only becomes foldable after
// the previous one collapsed advances exactly one level per iteration and
// aborts once it outgrows the iteration budget. With users re-enqueued, such
// chains collapse within a single worklist drain and no iteration budget is
// needed.
//
// Include this header after "using namespace mlir;" (the class body relies on
// unqualified MLIR names resolved at the point of inclusion).
//===----------------------------------------------------------------------===//

#ifndef PYC_TRANSFORMS_WORKLISTDRIVER_H
#define PYC_TRANSFORMS_WORKLISTDRIVER_H

#include "mlir/IR/Matchers.h"
#include "mlir/IR/PatternMatch.h"
#include "mlir/Interfaces/SideEffectInterfaces.h"
#include "mlir/Rewrite/PatternApplicator.h"
#include "mlir/Transforms/FoldUtils.h"

#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/STLExtras.h"

#include <utility>
#include <vector>

namespace pyc {

/// Worklist driver with user re-enqueue on replacement. A generous rewrite
/// cap guards against a hypothetical non-confluent pattern interaction;
/// exceeding it fails the driver instead of silently looping.
class ChainCollapseDriver : public mlir::RewriterBase::Listener {
public:
  ChainCollapseDriver(mlir::MLIRContext *ctx,
                      const mlir::FrozenRewritePatternSet &patterns)
      : context(ctx), rewriter(ctx), matcher(patterns) {
    matcher.applyDefaultCostModel();
    rewriter.setListener(this);
  }

  ~ChainCollapseDriver() override { rewriter.setListener(nullptr); }

  mlir::LogicalResult collapse(mlir::Region &region, int64_t iterationCap = 100) {
    bool changed = false;
    int64_t iteration = 0;
    do {
      // Safety cap: one drain converges all use-side chains; iterations only
      // guard against a hypothetical non-confluent pattern interaction.
      if (++iteration > iterationCap)
        return mlir::failure();
      worklist.clear();
      worklistMap.clear();
      mlir::OperationFolder folder(context, this);
      auto insertKnownConstant = [&](mlir::Operation *op) {
        mlir::Attribute constValue;
        if (mlir::matchPattern(op, mlir::m_Constant(&constValue)))
          if (!folder.insertKnownConstant(op, constValue))
            return true;
        return false;
      };
      region.walk([&](mlir::Operation *op) {
        if (!insertKnownConstant(op))
          addToWorklist(op);
      });
      changed = processWorklist();
    } while (changed);
    return mlir::success();
  }

private:
  void addToWorklist(mlir::Operation *op) {
    // Mirror the upstream scope handling: enqueue the op and its ancestors.
    mlir::SmallVector<mlir::Operation *, 8> ancestors;
    mlir::Region *region = nullptr;
    do {
      ancestors.push_back(op);
      region = op->getParentRegion();
      if (region == nullptr)
        break;
      op = region->getParentOp();
    } while (op);
    for (mlir::Operation *op : ancestors)
      push(op);
  }

  void push(mlir::Operation *op) {
    if (!worklistMap.insert({op, worklist.size()}).second)
      return;
    worklist.push_back(op);
  }

  mlir::Operation *pop() {
    while (!worklist.empty()) {
      mlir::Operation *op = worklist.back();
      worklist.pop_back();
      if (!op)
        continue;
      worklistMap.erase(op);
      return op;
    }
    return nullptr;
  }

  bool empty() const {
    return !llvm::any_of(worklist,
                         [](mlir::Operation *op) { return op != nullptr; });
  }

  void removeFromList(mlir::Operation *op) {
    auto it = worklistMap.find(op);
    if (it != worklistMap.end()) {
      worklist[it->second] = nullptr;
      worklistMap.erase(it);
    }
  }

  bool processWorklist() {
    bool changed = false;
    while (!empty()) {
      mlir::Operation *op = pop();

      // If the operation is trivially dead - remove it.
      if (mlir::isOpTriviallyDead(op)) {
        rewriter.eraseOp(op);
        changed = true;
        continue;
      }

      // Try to fold this op. Do not fold constant ops; that would loop with
      // the materialized constants.
      if (!op->hasTrait<mlir::OpTrait::ConstantLike>()) {
        mlir::SmallVector<mlir::OpFoldResult> foldResults;
        if (mlir::succeeded(op->fold(foldResults))) {
          if (foldResults.empty()) {
            // In-place update; revisit the op itself.
            push(op);
            changed = true;
            continue;
          }
          mlir::OpBuilder::InsertionGuard g(rewriter);
          rewriter.setInsertionPoint(op);
          mlir::SmallVector<mlir::Value> replacements;
          bool materialized = true;
          for (auto [ofr, resultType] :
               llvm::zip_equal(foldResults, op->getResultTypes())) {
            if (auto value = ofr.dyn_cast<mlir::Value>()) {
              replacements.push_back(value);
              continue;
            }
            mlir::Operation *constOp = op->getDialect()->materializeConstant(
                rewriter, ofr.get<mlir::Attribute>(), resultType, op->getLoc());
            if (!constOp) {
              for (mlir::Value replacement : replacements)
                if (mlir::Operation *def = replacement.getDefiningOp())
                  rewriter.eraseOp(def);
              materialized = false;
              break;
            }
            replacements.push_back(constOp->getResult(0));
          }
          if (materialized) {
            rewriter.replaceOp(op, replacements);
            changed = true;
            continue;
          }
        }
      }

      // Try to match one of the patterns.
      if (mlir::succeeded(matcher.matchAndRewrite(op, rewriter)))
        changed = true;
    }
    return changed;
  }

  void notifyOperationInserted(mlir::Operation *op,
                               mlir::OpBuilder::InsertPoint previous) override {
    push(op);
  }

  void notifyOperationModified(mlir::Operation *op) override { push(op); }

  void notifyOperationReplaced(mlir::Operation *op,
                               mlir::ValueRange replacement) override {
    // The upstream greedy driver never re-enqueues the users of a replaced
    // value; enqueuing them here is what lets serial folding chains collapse
    // within a single worklist drain.
    for (mlir::Value v : replacement)
      for (mlir::Operation *user : v.getUsers())
        push(user);
  }

  void notifyOperationErased(mlir::Operation *op) override {
    removeFromList(op);
    for (mlir::Value operand : op->getOperands())
      if (mlir::Operation *def = operand.getDefiningOp())
        push(def);
  }

  mlir::MLIRContext *context;
  mlir::PatternRewriter rewriter;
  mlir::PatternApplicator matcher;
  std::vector<mlir::Operation *> worklist;
  llvm::DenseMap<mlir::Operation *, unsigned> worklistMap;
};

} // namespace pyc

#endif // PYC_TRANSFORMS_WORKLISTDRIVER_H
