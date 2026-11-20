// Form compact delay-line state from proven serial register chains. Every
// register is a candidate once the sequential proof holds: untagged chains are
// admitted just like cycle-balance ones. External observation identities are
// remapped onto delay-line / delay-tap views instead of blocking the rewrite.

#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/StateOptimization.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"

#include <cstddef>

using namespace mlir;

namespace pyc {
namespace {

constexpr llvm::StringLiteral kOptimizedByAttr = "pyc.optimized_by";
constexpr llvm::StringLiteral kStructuralCombineDelayChains =
    "combine_delay_chains_structural";

static void combineStateChains(func::FuncOp function) {
  llvm::SmallVector<pyc::RegOp> regs;
  function.walk([&](pyc::RegOp reg) { regs.push_back(reg); });

  llvm::DenseSet<Operation *> erased;
  for (pyc::RegOp tail : llvm::reverse(regs)) {
    if (erased.contains(tail.getOperation()))
      continue;

    llvm::SmallVector<pyc::RegOp> chainFromTail{tail};
    llvm::SmallVector<StateChainLink> linksFromTail;
    pyc::RegOp cursor = tail;
    while (auto link = matchStateChainPredecessor(cursor, tail)) {
      if (erased.contains(link->predecessor.getOperation()))
        break;
      linksFromTail.push_back(*link);
      cursor = link->predecessor;
      chainFromTail.push_back(cursor);
    }
    if (chainFromTail.size() < 2)
      continue;

    // Preflight all intermediate fanout before creating the replacement. A
    // second stateful consumer cannot be represented by a read-only tap.
    bool hasIllegalFanout = false;
    for (unsigned i = 1; i < chainFromTail.size() && !hasIllegalFanout; ++i) {
      pyc::RegOp reg = chainFromTail[i];
      const StateChainLink &link = linksFromTail[i - 1];
      Operation *requiredUser = nullptr;
      if (link.aliasesFromConsumerToProducer.empty()) {
        requiredUser = chainFromTail[i - 1].getOperation();
      } else {
        pyc::AliasOp requiredAlias =
            link.aliasesFromConsumerToProducer.back();
        requiredUser = requiredAlias.getOperation();
      }
      for (Operation *user : reg.getQ().getUsers()) {
        if (user != requiredUser && isStatefulConsumer(user)) {
          hasIllegalFanout = true;
          break;
        }
      }
      for (auto aliasIt = link.aliasesFromConsumerToProducer.rbegin();
           aliasIt != link.aliasesFromConsumerToProducer.rend() &&
           !hasIllegalFanout;
           ++aliasIt) {
        pyc::AliasOp alias = *aliasIt;
        auto nextAlias = std::next(aliasIt);
        if (nextAlias == link.aliasesFromConsumerToProducer.rend()) {
          requiredUser = chainFromTail[i - 1].getOperation();
        } else {
          pyc::AliasOp nextAliasOp = *nextAlias;
          requiredUser = nextAliasOp.getOperation();
        }
        for (Operation *user : alias.getResult().getUsers()) {
          if (user != requiredUser && isStatefulConsumer(user)) {
            hasIllegalFanout = true;
            break;
          }
        }
      }
    }
    if (hasIllegalFanout)
      continue;

    const int64_t depth = static_cast<int64_t>(chainFromTail.size());
    pyc::RegOp head = chainFromTail.back();
    const bool allGenerated = llvm::all_of(
        chainFromTail,
        [](pyc::RegOp reg) { return isCycleBalanceGenerated(reg); });

    // Insert at the original head so the delay and its taps dominate every use
    // that was previously dominated by any state in the chain.
    OpBuilder builder(head);
    auto delay = builder.create<pyc::DelayLineOp>(
        tail.getLoc(), tail.getQ().getType(), head.getClk(), head.getRst(),
        head.getEn(),
        stripStateAliases(head.getNext()),
        head.getInit());
    delay->setAttr("depth", builder.getI64IntegerAttr(depth));
    if (allGenerated)
      delay->setAttr("pyc.generated",
                     builder.getStringAttr("cycle_balance"));
    delay->setAttr(kOptimizedByAttr,
                   builder.getStringAttr(kStructuralCombineDelayChains));

    // A non-tail state may have read-only fanout. Replace each such direct
    // read with a fixed-depth view of the new history, while preserving the
    // unique state-to-state edge that defines the chain. Observation
    // identities on erased stages are remapped onto the same views.
    remapStateOpIdentity(builder, tail.getOperation(), delay.getQ());
    for (unsigned i = 1; i < chainFromTail.size(); ++i) {
      pyc::RegOp reg = chainFromTail[i];
      llvm::SmallVector<OpOperand *, 8> sideUses;
      const StateChainLink &link = linksFromTail[i - 1];
      pyc::AliasOp requiredAlias;
      Operation *requiredUser = nullptr;
      if (link.aliasesFromConsumerToProducer.empty()) {
        requiredUser = chainFromTail[i - 1].getOperation();
      } else {
        requiredAlias = link.aliasesFromConsumerToProducer.back();
        requiredUser = requiredAlias.getOperation();
      }
      for (OpOperand &use : reg.getQ().getUses())
        if (use.getOwner() != requiredUser)
          sideUses.push_back(&use);
      for (auto aliasIt = link.aliasesFromConsumerToProducer.rbegin();
           aliasIt != link.aliasesFromConsumerToProducer.rend(); ++aliasIt) {
        pyc::AliasOp alias = *aliasIt;
        auto nextAlias = std::next(aliasIt);
        if (nextAlias == link.aliasesFromConsumerToProducer.rend()) {
          requiredUser = chainFromTail[i - 1].getOperation();
        } else {
          pyc::AliasOp nextAliasOp = *nextAlias;
          requiredUser = nextAliasOp.getOperation();
        }
        for (OpOperand &use : alias.getResult().getUses())
          if (use.getOwner() != requiredUser)
            sideUses.push_back(&use);
      }
      const bool needsObservationView =
          hasExternalObservationIdentity(reg.getOperation()) ||
          llvm::any_of(link.aliasesFromConsumerToProducer, [](pyc::AliasOp a) {
            return hasExternalObservationIdentity(a);
          });
      if (sideUses.empty() && !needsObservationView)
        continue;
      const int64_t tapDepth = static_cast<int64_t>(chainFromTail.size() - i);
      auto tap = builder.create<pyc::DelayTapOp>(
          tail.getLoc(), delay.getQ().getType(), delay.getQ());
      tap->setAttr("depth", builder.getI64IntegerAttr(tapDepth));
      tap->setAttr("pyc.optimized_by",
                   builder.getStringAttr("combine_delay_chain_tap"));
      remapStateOpIdentity(builder, reg.getOperation(), tap.getTap());
      for (pyc::AliasOp alias : link.aliasesFromConsumerToProducer) {
        if (hasExternalObservationIdentity(alias))
          materializeObservationAlias(builder, alias.getLoc(), tap.getTap(),
                                      alias);
      }
      for (OpOperand *use : sideUses) {
        use->set(tap.getTap());
      }
    }

    tail.getQ().replaceAllUsesWith(delay.getQ());

    for (unsigned i = 0; i < linksFromTail.size(); ++i) {
      pyc::RegOp reg = chainFromTail[i];
      erased.insert(reg.getOperation());
      reg.erase();
      for (pyc::AliasOp alias :
           linksFromTail[i].aliasesFromConsumerToProducer)
        alias.erase();
    }
    erased.insert(head.getOperation());
    head.erase();
  }
}

static void shareEquivalentDelayLines(func::FuncOp function) {
  llvm::SmallVector<pyc::DelayLineOp> delays;
  function.walk([&](pyc::DelayLineOp delay) { delays.push_back(delay); });

  llvm::DenseMap<std::size_t, llvm::SmallVector<pyc::DelayLineOp>> buckets;
  for (pyc::DelayLineOp delay : delays) {
    auto &bucket = buckets[delayLineStateHash(delay)];
    pyc::DelayLineOp survivor;
    for (pyc::DelayLineOp existing : bucket) {
      if (equivalentDelayLineState(existing, delay)) {
        survivor = existing;
        break;
      }
    }
    if (!survivor) {
      bucket.push_back(delay);
      continue;
    }

    OpBuilder builder(survivor);
    builder.setInsertionPointAfter(survivor);
    remapStateOpIdentity(builder, delay.getOperation(), survivor.getQ());
    delay.getQ().replaceAllUsesWith(survivor.getQ());
    delay.erase();
  }
}

struct CombineDelayChainsPass
    : public PassWrapper<CombineDelayChainsPass,
                         OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CombineDelayChainsPass)

  StringRef getArgument() const override { return "pyc-combine-delay-chains"; }
  StringRef getDescription() const override {
    return "Form proven fixed-depth delay lines from serial register chains";
  }

  void runOnOperation() override { runDelayChainFormAndShare(getOperation()); }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCombineDelayChainsPass() {
  return std::make_unique<CombineDelayChainsPass>();
}

void runDelayChainFormAndShare(func::FuncOp function) {
  combineStateChains(function);
  shareEquivalentDelayLines(function);
}

static PassRegistration<CombineDelayChainsPass> pass;

} // namespace pyc
