#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/CombDepGraph.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/SymbolTable.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"

#include <algorithm>
#include <limits>

using namespace mlir;

namespace pyc {
namespace {

static bool isSequentialOp(Operation *op) {
  return isa<pyc::RegOp,
             pyc::DelayLineOp,
             pyc::FifoOp,
             pyc::ByteMemOp,
             pyc::SyncMemOp,
             pyc::SyncMemDPOp,
             pyc::AsyncFifoOp,
             pyc::CdcSyncOp>(op);
}

static int64_t ceilLog2(int64_t n) {
  if (n <= 1)
    return 0;
  int64_t depth = 0;
  int64_t v = 1;
  while (v < n) {
    v <<= 1;
    ++depth;
  }
  return depth;
}

template <typename ReduceOp>
static int64_t vectorReduceCost(ReduceOp op) {
  auto vecTy = dyn_cast<VectorType>(op.getVec().getType());
  if (!vecTy)
    return 1;
  int64_t lanes = 1;
  if (auto dim = op.getDim()) {
    if (*dim < 0 || *dim >= vecTy.getRank())
      return 1;
    lanes = vecTy.getDimSize(*dim);
  } else {
    for (int64_t extent : vecTy.getShape())
      lanes *= extent;
  }
  if (auto mode = op->template getAttrOfType<StringAttr>("mode")) {
    if (mode.getValue() == "tree")
      return std::max<int64_t>(1, ceilLog2(lanes));
  }
  return std::max<int64_t>(1, lanes - 1);
}

static int64_t opCost(Operation *op) {
  if (!op)
    return 0;
  if (isSequentialOp(op))
    return 0;
  if (isa<pyc::WireOp, pyc::AliasOp, pyc::ResetActiveOp, pyc::ConstantOp, pyc::CombOp, pyc::YieldOp,
          pyc::DelayTapOp, arith::ConstantOp>(op))
    return 0;
  if (isa<pyc::VGetOp, pyc::VCreateOp, pyc::VBroadcastOp>(op))
    return 0;
  if (auto vr = dyn_cast<pyc::VOrReduceOp>(op))
    return vectorReduceCost(vr);
  if (auto vr = dyn_cast<pyc::VAndReduceOp>(op))
    return vectorReduceCost(vr);
  if (auto vr = dyn_cast<pyc::VAddReduceOp>(op))
    return vectorReduceCost(vr);
  return 1;
}

class CheckLogicDepthPass : public PassWrapper<CheckLogicDepthPass, OperationPass<ModuleOp>> {
public:
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CheckLogicDepthPass)

  explicit CheckLogicDepthPass(unsigned depth = 32) : maxDepthLimit(depth) {}

  StringRef getArgument() const override { return "pyc-check-logic-depth"; }
  StringRef getDescription() const override {
    return "Check strict combinational depth and compute WNS/TNS against LOGIC_DEPTH";
  }

  void runOnOperation() override {
    ModuleOp module = getOperation();
    CombDepGraphCache combCache(module);

    bool failedAny = false;

    for (func::FuncOp f : module.getOps<func::FuncOp>()) {
      const int64_t limit = static_cast<int64_t>(maxDepthLimit);

      llvm::DenseMap<Value, llvm::SmallVector<Value>> wireDrivers;
      f.walk([&](pyc::AssignOp a) {
        Value dst = a.getDst();
        if (!dst || !dst.getDefiningOp<pyc::WireOp>())
          return;
        wireDrivers[dst].push_back(a.getSrc());
      });

      llvm::DenseMap<Value, int64_t> memo;
      llvm::DenseSet<Value> visiting;
      bool failedThisFunc = false;

      auto depthOf = [&](auto &&self, Value v) -> int64_t {
        if (!v)
          return 0;
        auto it = memo.find(v);
        if (it != memo.end())
          return it->second;
        if (!visiting.insert(v).second)
          return limit + 1;

        int64_t d = 0;

        if (auto barg = dyn_cast<BlockArgument>(v)) {
          Operation *parentOp = barg.getOwner() ? barg.getOwner()->getParentOp() : nullptr;
          if (isa_and_nonnull<func::FuncOp>(parentOp)) {
            d = 0;
          } else if (auto comb = dyn_cast_or_null<pyc::CombOp>(parentOp)) {
            unsigned idx = static_cast<unsigned>(barg.getArgNumber());
            auto inputs = comb.getInputs();
            if (idx < inputs.size())
              d = self(self, inputs[idx]);
            else
              d = 0;
          } else {
            d = 0;
          }

          visiting.erase(v);
          memo.try_emplace(v, d);
          return d;
        }

        Operation *def = v.getDefiningOp();
        if (!def || isSequentialOp(def)) {
          d = 0;
        } else if (isa<arith::ConstantOp, pyc::ConstantOp>(def)) {
          d = 0;
        } else if (isa<pyc::WireOp>(def)) {
          int64_t inMax = 0;
          if (auto itD = wireDrivers.find(v); itD != wireDrivers.end()) {
            for (Value src : itD->second)
              inMax = std::max(inMax, self(self, src));
          }
          d = inMax;
        } else if (auto a = dyn_cast<pyc::AliasOp>(def)) {
          d = self(self, a.getIn());
        } else if (auto comb = dyn_cast<pyc::CombOp>(def)) {
          auto r = dyn_cast<OpResult>(v);
          unsigned resIdx = r ? r.getResultNumber() : 0u;
          auto yield = comb.getBody().empty() ? pyc::YieldOp() : dyn_cast_or_null<pyc::YieldOp>(comb.getBody().front().getTerminator());
          if (!yield || resIdx >= yield.getValues().size()) {
            d = 0;
          } else {
            d = self(self, yield.getValues()[resIdx]);
          }
        } else if (auto inst = dyn_cast<pyc::InstanceOp>(def)) {
          auto r = dyn_cast<OpResult>(v);
          unsigned resIdx = r ? r.getResultNumber() : 0u;

          auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
          if (!calleeAttr) {
            inst.emitError("pyc.instance missing required `callee` attr");
            failedThisFunc = true;
            d = limit + 1;
          } else {
            auto sym = SymbolTable::lookupSymbolIn(module, calleeAttr.getValue());
            auto callee = dyn_cast_or_null<func::FuncOp>(sym);
            if (!callee) {
              inst.emitError("pyc.instance callee is not a func.func symbol: ") << calleeAttr.getValue();
              failedThisFunc = true;
              d = limit + 1;
            } else {
              const FuncCombSummary *sum = combCache.getFuncSummary(callee);
              if (!sum) {
                // Multi-.pyc build mode emits declaration-only dependency stubs.
                // Without a callee body (or an explicit summary) we cannot
                // propagate depth precisely, so treat the instance result as a
                // local cut point here. Full-design gates should validate
                // cross-instance depth when the full IR is available.
                if (callee.isDeclaration()) {
                  d = 0;
                } else {
                  failedThisFunc = true;
                  d = limit + 1;
                }
              } else if (resIdx >= sum->results.size()) {
                d = 0;
              } else {
                const CombResultSummary &rs = sum->results[resIdx];
                int64_t best = (rs.baseDepth >= 0) ? rs.baseDepth : 0;

                auto inputs = inst.getInputs();
                unsigned n = std::min<unsigned>(static_cast<unsigned>(inputs.size()), static_cast<unsigned>(rs.argDepth.size()));
                for (unsigned i = 0; i < n; ++i) {
                  int64_t delta = rs.argDepth[i];
                  if (delta < 0)
                    continue;
                  best = std::max(best, self(self, inputs[i]) + delta);
                }
                d = best;
              }
            }
          }
        } else {
          int64_t inMax = 0;
          for (Value in : def->getOperands())
            inMax = std::max(inMax, self(self, in));
          d = inMax + opCost(def);
        }

        visiting.erase(v);
        memo.try_emplace(v, d);
        return d;
      };

      int64_t maxDepth = 0;
      int64_t wns = std::numeric_limits<int64_t>::max();
      int64_t tns = 0;

      auto observeEndpoint = [&](Operation *op, Value v) {
        int64_t d = depthOf(depthOf, v);
        maxDepth = std::max(maxDepth, d);
        int64_t slack = limit - d;
        wns = std::min(wns, slack);
        if (slack < 0)
          tns += slack;
        if (d > limit) {
          op->emitError("logic depth exceeds limit: depth=") << d << " limit=" << limit;
          failedThisFunc = true;
        }
      };

      f.walk([&](Operation *op) {
        if (auto ret = dyn_cast<func::ReturnOp>(op)) {
          for (Value v : ret.getOperands())
            observeEndpoint(op, v);
          return;
        }
        if (auto a = dyn_cast<pyc::AssertOp>(op)) {
          observeEndpoint(op, a.getCond());
          return;
        }
        if (isSequentialOp(op)) {
          for (Value v : op->getOperands())
            observeEndpoint(op, v);
        }
      });

      if (wns == std::numeric_limits<int64_t>::max())
        wns = limit;

      auto i64Ty = IntegerType::get(f.getContext(), 64);
      f->setAttr("pyc.logic_depth.max", IntegerAttr::get(i64Ty, maxDepth));
      f->setAttr("pyc.logic_depth.wns", IntegerAttr::get(i64Ty, wns));
      f->setAttr("pyc.logic_depth.tns", IntegerAttr::get(i64Ty, tns));

      if (failedThisFunc)
        failedAny = true;
    }

    if (failedAny)
      signalPassFailure();
  }

private:
  unsigned maxDepthLimit = 32;
};

} // namespace

std::unique_ptr<::mlir::Pass> createCheckLogicDepthPass(unsigned logicDepth) {
  return std::make_unique<CheckLogicDepthPass>(logicDepth);
}

static PassRegistration<CheckLogicDepthPass> pass;

} // namespace pyc
