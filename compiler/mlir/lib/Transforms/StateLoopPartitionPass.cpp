// Partition pyc.reg and scalar pyc.delay_line ops into per-clock state loops.
// Analysis-only: no IR semantics change. Each op gets `pyc.loop_id = <i>`
// (loops are numbered densely per function, one per equivalent-clock domain);
// the function gets `pyc.stats.loop_groups` / `pyc.stats.loop_lanes`. The C++
// emitter lowers each loop into one pyc::cpp::RegLoop<W> / DelayLoop<W> member
// and one generated update loop instead of per-object expansions.
//
// Equivalence of clock SSA values resolves clk aliases, so a loop is exactly
// the set of state ops that could share one edge check.

#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/StateOptimization.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SmallVector.h"

#include <algorithm>

using namespace mlir;

namespace pyc {
namespace {

constexpr llvm::StringLiteral kLoopId = "pyc.loop_id";
constexpr llvm::StringLiteral kLoopGroups = "pyc.stats.loop_groups";
constexpr llvm::StringLiteral kLoopLanes = "pyc.stats.loop_lanes";

struct StateLoopPartitionPass
    : public PassWrapper<StateLoopPartitionPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(StateLoopPartitionPass)

  StateLoopPartitionPass() = default;
  StateLoopPartitionPass(const StateLoopPartitionPass &other)
      : PassWrapper(other) {}

  StringRef getArgument() const override { return "pyc-state-loop-partition"; }
  StringRef getDescription() const override {
    return "Group registers per clock domain into state-loop ids (analysis only)";
  }

  void runOnOperation() override {
    func::FuncOp function = getOperation();

    // Regs and scalar delay_lines whose clk resolves to equivalent values
    // share a loop. Reset/enable/depth stay per-lane (read per lane in the
    // loop); the emitter further splits by (id, width[, depth]) because
    // RegLoop<W>/DelayLoop<W> are single-width templates and delay lanes
    // carry per-lane depth.
    llvm::SmallVector<pyc::RegOp> regs;
    llvm::SmallVector<pyc::DelayLineOp> delays;
    function.walk([&](Operation *op) {
      if (auto reg = dyn_cast<pyc::RegOp>(op))
        regs.push_back(reg);
      else if (auto delay = dyn_cast<pyc::DelayLineOp>(op))
        if (!isa<VectorType>(delay.getQ().getType()))
          delays.push_back(delay);
    });
    if (regs.empty() && delays.empty()) {
      setI64Attr(function, kLoopGroups, 0);
      setI64Attr(function, kLoopLanes, 0);
      return;
    }

    // Deterministic order: block layout.
    std::sort(regs.begin(), regs.end(), [](pyc::RegOp a, pyc::RegOp b) {
      return a->isBeforeInBlock(b);
    });
    std::sort(delays.begin(), delays.end(), [](pyc::DelayLineOp a, pyc::DelayLineOp b) {
      return a->isBeforeInBlock(b);
    });

    llvm::DenseMap<Value, int64_t> clkToLoop;
    int64_t nextLoop = 0;
    auto resolveLoop = [&](Value clk) -> int64_t {
      auto it = clkToLoop.find(clk);
      if (it != clkToLoop.end())
        return it->second;
      Value canonical = clk;
      if (auto *def = clk.getDefiningOp())
        if (isa<pyc::AliasOp>(def))
          canonical = def->getOperand(0);
      auto cit = clkToLoop.find(canonical);
      if (cit != clkToLoop.end()) {
        const int64_t id = cit->second;  // insertion below may rehash
        clkToLoop[clk] = id;
        return id;
      }
      const int64_t id = nextLoop++;
      clkToLoop[canonical] = id;
      clkToLoop[clk] = id;
      return id;
    };

    int64_t lanes = 0;
    for (pyc::RegOp reg : regs) {
      const int64_t loopId = resolveLoop(reg.getClk());
      reg->setAttr(kLoopId,
                   IntegerAttr::get(IntegerType::get(reg->getContext(), 64),
                                    loopId));
      ++lanes;
    }
    for (pyc::DelayLineOp delay : delays) {
      const int64_t loopId = resolveLoop(delay.getClk());
      delay->setAttr(kLoopId,
                     IntegerAttr::get(IntegerType::get(delay->getContext(), 64),
                                      loopId));
      ++lanes;
    }

    setI64Attr(function, kLoopGroups, nextLoop);
    setI64Attr(function, kLoopLanes, lanes);
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createStateLoopPartitionPass() {
  return std::make_unique<StateLoopPartitionPass>();
}

namespace {
static PassRegistration<StateLoopPartitionPass> pass;
} // namespace

} // namespace pyc
