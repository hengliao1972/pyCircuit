// Collapse proven single-source register pipelines into one history and
// reconstruct observable stage values from fixed-depth taps.  The rewrite is
// intentionally local: every inter-stage cone must be pure, depend only on the
// preceding state plus constants, and preserve reset/init exactly.

#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/StateOptimization.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/IRMapping.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"

#include <algorithm>
#include <cstdint>
#include <optional>

using namespace mlir;

namespace pyc {
namespace {

constexpr llvm::StringLiteral kRetimedBy = "retime_pipeline_history";
constexpr llvm::StringLiteral kCommonDelaySink = "retime_common_delay_sink";

struct StateSource {
  Operation *op = nullptr;
  Value q;
  Value next;
  Value init;
  Value clk;
  Value rst;
  Value en;
  int64_t depth = 1;
};

struct CommonDelayRegion {
  Operation *root = nullptr;
  llvm::SmallVector<StateSource> sources;
  llvm::SmallVector<Operation *> coneOps;
  llvm::DenseSet<Operation *> coneSet;
  llvm::APInt transformedInit{1, 0};
  int64_t stateBitsRemoved = 0;
};

struct PipelineLink {
  pyc::RegOp predecessor;
  pyc::RegOp consumer;
  Value root;
  llvm::SmallVector<Operation *> coneOps;
};

struct PipelineRegion {
  llvm::SmallVector<pyc::RegOp> regs;
  llvm::SmallVector<PipelineLink> links;
  llvm::DenseSet<Operation *> coneSet;
  int64_t uniqueCombOps = 0;
  int64_t clonedCombOps = 0;
  int64_t extraCombOps = 0;
  int64_t stateBitsRemoved = 0;
};

struct RetimeStats {
  int64_t regionsRetimed = 0;
  int64_t regsRetimed = 0;
  int64_t statePrimitivesRemoved = 0;
  int64_t tapsCreated = 0;
  int64_t combOpsCloned = 0;
  int64_t commonDelaySinks = 0;
  int64_t commonDelaySourceStates = 0;
  int64_t combOpsMoved = 0;
  int64_t stateBitsRemoved = 0;
  int64_t blockedInit = 0;
  int64_t blockedCost = 0;
};

static bool isAllowedConeOp(Operation *op) {
  return isa<pyc::AliasOp, pyc::AddOp, pyc::SubOp, pyc::MulOp, pyc::AndOp,
             pyc::OrOp, pyc::XorOp, pyc::NotOp, pyc::MuxOp, pyc::EqOp,
             pyc::UltOp, pyc::SltOp, pyc::TruncOp, pyc::ZextOp, pyc::SextOp,
             pyc::ExtractOp, pyc::ShliOp, pyc::LshriOp, pyc::AshriOp,
             pyc::ShlOp, pyc::LshrOp, pyc::AshrOp, pyc::ConcatOp>(op);
}

static std::optional<llvm::APInt> constantValue(Value value);
static std::optional<StateSource> stateSource(Value value);

static std::optional<llvm::APInt> constantValue(Value value) {
  llvm::SmallPtrSet<Operation *, 8> aliasSeen;
  while (auto alias = value.getDefiningOp<pyc::AliasOp>()) {
    if (!aliasSeen.insert(alias.getOperation()).second)
      return std::nullopt;
    value = alias.getIn();
  }
  if (auto constant = value.getDefiningOp<pyc::ConstantOp>())
    return constant.getValueAttr().getValue();
  if (auto constant = value.getDefiningOp<arith::ConstantOp>()) {
    if (auto integer = dyn_cast<IntegerAttr>(constant.getValue()))
      return integer.getValue();
  }
  return std::nullopt;
}

static std::optional<unsigned> integerWidth(Type type) {
  if (auto integer = dyn_cast<IntegerType>(type))
    return integer.getWidth();
  return std::nullopt;
}

static std::optional<StateSource> stateSource(Value value) {
  if (auto reg = value.getDefiningOp<pyc::RegOp>())
    return StateSource{
        reg.getOperation(), reg.getQ(),   reg.getNext(), reg.getInit(),
        reg.getClk(),       reg.getRst(), reg.getEn(),   1};
  if (auto delay = value.getDefiningOp<pyc::DelayLineOp>()) {
    auto depth = delay->getAttrOfType<IntegerAttr>("depth");
    if (!depth || depth.getInt() <= 0)
      return std::nullopt;
    return StateSource{delay.getOperation(), delay.getQ(),   delay.getNext(),
                       delay.getInit(),      delay.getClk(), delay.getRst(),
                       delay.getEn(),        depth.getInt()};
  }
  return std::nullopt;
}

struct ConeMatch {
  std::optional<PipelineLink> link;
};

static ConeMatch matchPipelineLink(pyc::RegOp consumer, pyc::RegOp keyReg) {
  PipelineLink link;
  link.consumer = consumer;
  link.root = consumer.getNext();

  llvm::DenseSet<Operation *> seenOps;
  llvm::DenseSet<Value> visiting;
  pyc::RegOp predecessor;
  bool failed = false;

  auto visit = [&](auto &&self, Value value) -> void {
    if (failed || !value)
      return;
    if (!visiting.insert(value).second) {
      failed = true;
      return;
    }

    if (auto reg = value.getDefiningOp<pyc::RegOp>()) {
      if (!predecessor)
        predecessor = reg;
      else if (predecessor != reg)
        failed = true;
      visiting.erase(value);
      return;
    }
    if (constantValue(value)) {
      visiting.erase(value);
      return;
    }

    Operation *def = value.getDefiningOp();
    if (!def || def->getBlock() != consumer->getBlock() ||
        !isAllowedConeOp(def)) {
      failed = true;
      visiting.erase(value);
      return;
    }
    // Mark the op before descending. The cone is a DAG; recording it only
    // after the subtree returns re-expands every shared mux.
    if (!seenOps.insert(def).second) {
      visiting.erase(value);
      return;
    }
    for (Value operand : def->getOperands())
      self(self, operand);
    if (!failed)
      link.coneOps.push_back(def);
    visiting.erase(value);
  };
  visit(visit, link.root);

  if (failed || !predecessor || predecessor == consumer ||
      predecessor->getBlock() != consumer->getBlock())
    return {};
  if (!equivalentStateValue(predecessor.getClk(), keyReg.getClk()) ||
      !equivalentStateValue(predecessor.getRst(), keyReg.getRst()) ||
      !equivalentStateValue(predecessor.getEn(), keyReg.getEn()))
    return {};
  llvm::DenseSet<Operation *> coneSet(link.coneOps.begin(), link.coneOps.end());
  for (Operation *op : link.coneOps) {
    for (Value result : op->getResults()) {
      for (Operation *user : result.getUsers()) {
        if (!coneSet.contains(user) && user != consumer.getOperation())
          return {};
      }
    }
  }

  link.predecessor = predecessor;
  return {std::move(link)};
}

static std::optional<llvm::APInt>
lookupEvalValue(Value value, const llvm::DenseMap<Value, llvm::APInt> &values) {
  if (auto it = values.find(value); it != values.end())
    return it->second;
  return constantValue(value);
}

static std::optional<llvm::APInt>
evaluateConeOp(Operation *op,
               const llvm::DenseMap<Value, llvm::APInt> &values) {
  if (op->getNumResults() != 1)
    return std::nullopt;
  auto width = integerWidth(op->getResult(0).getType());
  if (!width)
    return std::nullopt;

  llvm::SmallVector<llvm::APInt> args;
  for (Value operand : op->getOperands()) {
    auto value = lookupEvalValue(operand, values);
    if (!value)
      return std::nullopt;
    args.push_back(*value);
  }
  auto fit = [&](llvm::APInt value) { return value.zextOrTrunc(*width); };

  if (isa<pyc::AliasOp>(op))
    return fit(args[0]);
  if (isa<pyc::AddOp>(op))
    return fit(args[0] + args[1]);
  if (isa<pyc::SubOp>(op))
    return fit(args[0] - args[1]);
  if (isa<pyc::MulOp>(op))
    return fit(args[0] * args[1]);
  if (isa<pyc::AndOp>(op))
    return fit(args[0] & args[1]);
  if (isa<pyc::OrOp>(op))
    return fit(args[0] | args[1]);
  if (isa<pyc::XorOp>(op))
    return fit(args[0] ^ args[1]);
  if (isa<pyc::NotOp>(op))
    return fit(~args[0]);
  if (isa<pyc::MuxOp>(op))
    return fit(args[0].isZero() ? args[2] : args[1]);
  if (isa<pyc::EqOp>(op))
    return llvm::APInt(1, args[0] == args[1]);
  if (isa<pyc::UltOp>(op))
    return llvm::APInt(1, args[0].ult(args[1]));
  if (isa<pyc::SltOp>(op))
    return llvm::APInt(1, args[0].slt(args[1]));
  if (isa<pyc::TruncOp>(op))
    return args[0].trunc(*width);
  if (isa<pyc::ZextOp>(op))
    return args[0].zext(*width);
  if (isa<pyc::SextOp>(op))
    return args[0].sext(*width);
  if (auto extract = dyn_cast<pyc::ExtractOp>(op))
    return args[0]
        .lshr(static_cast<unsigned>(extract.getLsbAttr().getInt()))
        .trunc(*width);
  auto logicalShift = [&](bool left, uint64_t amount) {
    if (amount >= args[0].getBitWidth())
      return llvm::APInt(*width, 0);
    return fit(left ? args[0] << static_cast<unsigned>(amount)
                    : args[0].lshr(static_cast<unsigned>(amount)));
  };
  auto arithmeticShift = [&](uint64_t amount) {
    amount = std::min<uint64_t>(amount, args[0].getBitWidth() - 1);
    return fit(args[0].ashr(static_cast<unsigned>(amount)));
  };
  if (auto shift = dyn_cast<pyc::ShliOp>(op))
    return logicalShift(true, shift.getAmountAttr().getInt());
  if (auto shift = dyn_cast<pyc::LshriOp>(op))
    return logicalShift(false, shift.getAmountAttr().getInt());
  if (auto shift = dyn_cast<pyc::AshriOp>(op))
    return arithmeticShift(shift.getAmountAttr().getInt());
  if (isa<pyc::ShlOp>(op))
    return logicalShift(true, args[1].getLimitedValue());
  if (isa<pyc::LshrOp>(op))
    return logicalShift(false, args[1].getLimitedValue());
  if (isa<pyc::AshrOp>(op))
    return arithmeticShift(args[1].getLimitedValue());
  if (isa<pyc::ConcatOp>(op)) {
    llvm::APInt result(*width, 0);
    for (llvm::APInt arg : args) {
      result = result.shl(arg.getBitWidth());
      result |= arg.zextOrTrunc(*width);
    }
    return result;
  }
  return std::nullopt;
}

static std::optional<llvm::APInt> evaluateLink(PipelineLink &link,
                                               llvm::APInt input) {
  llvm::DenseMap<Value, llvm::APInt> values;
  values.try_emplace(link.predecessor.getQ(), std::move(input));
  for (Operation *op : link.coneOps) {
    auto result = evaluateConeOp(op, values);
    if (!result)
      return std::nullopt;
    values.try_emplace(op->getResult(0), std::move(*result));
  }
  return lookupEvalValue(link.root, values);
}

static bool dependsOnCone(Value value, const llvm::DenseSet<Operation *> &cone,
                          llvm::DenseSet<Value> &seen) {
  if (!value || !seen.insert(value).second)
    return false;
  Operation *def = value.getDefiningOp();
  if (!def)
    return false;
  if (cone.contains(def))
    return true;
  return llvm::any_of(def->getOperands(), [&](Value operand) {
    return dependsOnCone(operand, cone, seen);
  });
}

static bool dependsOnValues(Value value, const llvm::DenseSet<Value> &targets,
                            llvm::DenseSet<Value> &seen) {
  if (!value || !seen.insert(value).second)
    return false;
  if (targets.contains(value))
    return true;
  Operation *def = value.getDefiningOp();
  if (!def)
    return false;
  return llvm::any_of(def->getOperands(), [&](Value operand) {
    return dependsOnValues(operand, targets, seen);
  });
}

static bool hasOpaqueNextDependency(Value value, llvm::DenseSet<Value> &seen) {
  if (!value || !seen.insert(value).second)
    return false;
  Operation *def = value.getDefiningOp();
  if (!def)
    return false;
  if (isa<pyc::WireOp>(def))
    return true;
  return llvm::any_of(def->getOperands(), [&](Value operand) {
    return hasOpaqueNextDependency(operand, seen);
  });
}

static std::optional<CommonDelayRegion>
matchCommonDelayRegion(Operation *root, RetimeStats &stats) {
  if (!root || root->getNumResults() != 1 || root->getResult(0).use_empty() ||
      !isAllowedConeOp(root))
    return std::nullopt;
  if (root->getResult(0).hasOneUse() &&
      isAllowedConeOp(*root->getResult(0).getUsers().begin()))
    return std::nullopt;

  CommonDelayRegion region;
  region.root = root;
  llvm::DenseMap<Operation *, unsigned> sourceIndices;
  llvm::DenseSet<Value> visiting;
  bool failed = false;

  auto visit = [&](auto &&self, Value value) -> void {
    if (failed || !value)
      return;
    if (!visiting.insert(value).second) {
      failed = true;
      return;
    }
    if (auto source = stateSource(value)) {
      if (source->op->getBlock() != root->getBlock()) {
        failed = true;
      } else if (!sourceIndices.count(source->op)) {
        sourceIndices.try_emplace(source->op, region.sources.size());
        region.sources.push_back(*source);
      }
      visiting.erase(value);
      return;
    }
    if (constantValue(value)) {
      visiting.erase(value);
      return;
    }

    Operation *def = value.getDefiningOp();
    if (!def || def->getBlock() != root->getBlock() || !isAllowedConeOp(def)) {
      failed = true;
      visiting.erase(value);
      return;
    }
    // Same DAG pruning as the pipeline matcher.
    if (!region.coneSet.insert(def).second) {
      visiting.erase(value);
      return;
    }
    for (Value operand : def->getOperands())
      self(self, operand);
    if (!failed)
      region.coneOps.push_back(def);
    visiting.erase(value);
  };
  visit(visit, root->getResult(0));

  if (failed || region.sources.size() < 2 || !region.coneSet.contains(root))
    return std::nullopt;

  const StateSource &key = region.sources.front();
  llvm::DenseSet<Value> sourceValues;
  for (const StateSource &source : region.sources)
    sourceValues.insert(source.q);
  for (const StateSource &source : region.sources) {
    if (source.depth != key.depth ||
        !equivalentStateValue(source.clk, key.clk) ||
        !equivalentStateValue(source.rst, key.rst) ||
        !equivalentStateValue(source.en, key.en))
      return std::nullopt;
    llvm::DenseSet<Value> nextDeps;
    llvm::DenseSet<Value> opaqueDeps;
    llvm::DenseSet<Value> stateDeps;
    if (dependsOnCone(source.next, region.coneSet, nextDeps) ||
        hasOpaqueNextDependency(source.next, opaqueDeps) ||
        dependsOnValues(source.next, sourceValues, stateDeps))
      return std::nullopt;
    for (Operation *user : source.q.getUsers())
      if (!region.coneSet.contains(user))
        return std::nullopt;
  }

  for (Operation *op : region.coneOps) {
    if (op == root)
      continue;
    for (Value result : op->getResults())
      for (Operation *user : result.getUsers())
        if (!region.coneSet.contains(user))
          return std::nullopt;
  }

  llvm::DenseMap<Value, llvm::APInt> initValues;
  int64_t oldStateBits = 0;
  for (const StateSource &source : region.sources) {
    auto init = constantValue(source.init);
    auto width = integerWidth(source.q.getType());
    if (!init || !width) {
      ++stats.blockedInit;
      return std::nullopt;
    }
    initValues.try_emplace(source.q, *init);
    oldStateBits += static_cast<int64_t>(*width) * source.depth;
  }
  for (Operation *op : region.coneOps) {
    auto value = evaluateConeOp(op, initValues);
    if (!value) {
      ++stats.blockedInit;
      return std::nullopt;
    }
    initValues.try_emplace(op->getResult(0), std::move(*value));
  }
  auto transformedInit = lookupEvalValue(root->getResult(0), initValues);
  auto resultWidth = integerWidth(root->getResult(0).getType());
  if (!transformedInit || !resultWidth) {
    ++stats.blockedInit;
    return std::nullopt;
  }
  const int64_t newStateBits = static_cast<int64_t>(*resultWidth) * key.depth;
  if (newStateBits > oldStateBits ||
      (newStateBits == oldStateBits && region.sources.size() == 1)) {
    ++stats.blockedCost;
    return std::nullopt;
  }
  region.transformedInit = std::move(*transformedInit);
  region.stateBitsRemoved = oldStateBits - newStateBits;
  return region;
}

// Common-delay sinking: move a consumer cone across the state boundary so it
// reads source.next instead of source.q. The cone then lives in the same
// combinational cone as the producers of next, where downstream CSE and
// fuse-comb merge them (shared subexpressions deduplicate, one scheduled comb
// region instead of a producer/consumer pair split across cycles). The
// sources themselves become deletable when nothing else observes them. This
// fusion — not the bit delta — is the primary benefit; newStateBits <=
// oldStateBits is only the guard that the state axis never regresses.
static void rewriteCommonDelayRegion(CommonDelayRegion &region,
                                     RetimeStats &stats) {
  const StateSource &key = region.sources.front();
  OpBuilder builder(region.root);
  IRMapping mapping;
  for (const StateSource &source : region.sources)
    mapping.map(source.q, source.next);
  for (Operation *op : region.coneOps)
    builder.clone(*op, mapping);
  Value movedNext = mapping.lookupOrDefault(region.root->getResult(0));
  auto resultType = cast<IntegerType>(region.root->getResult(0).getType());
  auto init = builder.create<pyc::ConstantOp>(
      region.root->getLoc(), resultType,
      IntegerAttr::get(resultType, region.transformedInit));

  Value replacement;
  if (key.depth == 1) {
    auto reg = builder.create<pyc::RegOp>(region.root->getLoc(), resultType,
                                          key.clk, key.rst, key.en, movedNext,
                                          init.getResult());
    reg->setAttr("pyc.optimized_by", builder.getStringAttr(kCommonDelaySink));
    replacement = reg.getQ();
  } else {
    auto delay = builder.create<pyc::DelayLineOp>(
        region.root->getLoc(), resultType, key.clk, key.rst, key.en, movedNext,
        init.getResult());
    delay->setAttr("depth", builder.getI64IntegerAttr(key.depth));
    delay->setAttr("pyc.optimized_by", builder.getStringAttr(kCommonDelaySink));
    replacement = delay.getQ();
  }
  region.root->getResult(0).replaceAllUsesWith(replacement);

  // Frontend names (`pyc.name`, etc.) may live on `pyc.alias` ops of a
  // source's `q` that also appear in `coneOps`. Preserving them keeps
  // `dut.read` / probe / VCD lookups working on the retained history.
  for (Operation *op : llvm::reverse(region.coneOps)) {
    bool unused = llvm::all_of(op->getResults(),
                               [](Value value) { return value.use_empty(); });
    if (!unused)
      continue;
    if (auto alias = dyn_cast<pyc::AliasOp>(op))
      if (hasExternalObservationIdentity(alias))
        continue;
    op->erase();
  }
  for (const StateSource &source : region.sources) {
    // Named/probed sources stay as read-only histories so dut.read still
    // sees the pre-sink values. Unobserved unused sources are deleted.
    if (!source.q.use_empty() ||
        stateHasExternalObservation(source.op, source.q))
      continue;
    source.op->erase();
  }

  ++stats.commonDelaySinks;
  stats.commonDelaySourceStates += region.sources.size();
  stats.combOpsMoved += region.coneOps.size();
  stats.stateBitsRemoved += region.stateBitsRemoved;
  ++stats.regionsRetimed;
  stats.regsRetimed += region.sources.size();
  stats.statePrimitivesRemoved += region.sources.size() - 1;
}

static void runCommonDelaySinking(func::FuncOp function, RetimeStats &stats) {
  llvm::SmallVector<Operation *> roots;
  function.walk([&](Operation *op) {
    if (isAllowedConeOp(op))
      roots.push_back(op);
  });

  llvm::SmallVector<CommonDelayRegion> candidates;
  for (Operation *root : llvm::reverse(roots)) {
    auto region = matchCommonDelayRegion(root, stats);
    if (region)
      candidates.push_back(std::move(*region));
  }
  llvm::stable_sort(candidates, [](const CommonDelayRegion &lhs,
                                   const CommonDelayRegion &rhs) {
    if (lhs.stateBitsRemoved != rhs.stateBitsRemoved)
      return lhs.stateBitsRemoved > rhs.stateBitsRemoved;
    if (lhs.sources.size() != rhs.sources.size())
      return lhs.sources.size() > rhs.sources.size();
    return lhs.coneOps.size() > rhs.coneOps.size();
  });

  llvm::DenseSet<Operation *> claimedStates;
  llvm::DenseSet<Operation *> claimedCone;
  llvm::DenseSet<Operation *> claimedNextCone;
  for (CommonDelayRegion &region : candidates) {
    if (llvm::any_of(region.sources,
                     [&](const StateSource &source) {
                       return claimedStates.contains(source.op);
                     }) ||
        llvm::any_of(region.coneOps,
                     [&](Operation *op) {
                       return claimedCone.contains(op) ||
                              claimedNextCone.contains(op);
                     }) ||
        llvm::any_of(region.sources, [&](const StateSource &source) {
          llvm::DenseSet<Value> seen;
          return dependsOnCone(source.next, claimedCone, seen);
        }))
      continue;
    for (const StateSource &source : region.sources)
      claimedStates.insert(source.op);
    for (Operation *op : region.coneOps)
      claimedCone.insert(op);
    for (const StateSource &source : region.sources) {
      llvm::DenseSet<Value> seen;
      auto collectNextCone = [&](auto &&self, Value value) -> void {
        if (!value || !seen.insert(value).second)
          return;
        Operation *def = value.getDefiningOp();
        if (!def || isStatefulConsumer(def))
          return;
        claimedNextCone.insert(def);
        for (Value operand : def->getOperands())
          self(self, operand);
      };
      collectNextCone(collectNextCone, source.next);
    }
    rewriteCommonDelayRegion(region, stats);
  }
}

static bool needsRebuiltView(PipelineRegion &region, unsigned index) {
  pyc::RegOp reg = region.regs[index];
  if (stateHasExternalObservation(reg.getOperation(), reg.getQ()))
    return true;
  Operation *regOp = reg.getOperation();
  for (OpOperand &use : reg.getQ().getUses()) {
    Operation *user = use.getOwner();
    if (region.coneSet.contains(user))
      continue;
    if (index + 1 < region.regs.size() &&
        user == region.regs[index + 1].getOperation())
      continue;
    if (user != regOp)
      return true;
  }
  return false;
}

static bool finalizeRegion(PipelineRegion &region, RetimeStats &stats) {
  llvm::DenseSet<Value> stateValues;
  for (pyc::RegOp reg : region.regs)
    stateValues.insert(reg.getQ());
  llvm::DenseSet<Value> stateDeps;
  llvm::DenseSet<Value> opaqueDeps;
  if (dependsOnValues(region.regs.front().getNext(), stateValues, stateDeps) ||
      hasOpaqueNextDependency(region.regs.front().getNext(), opaqueDeps)) {
    ++stats.blockedCost;
    return false;
  }

  auto init = constantValue(region.regs.front().getInit());
  if (!init) {
    ++stats.blockedInit;
    return false;
  }
  llvm::APInt current = *init;
  for (PipelineLink &link : region.links) {
    auto next = evaluateLink(link, current);
    auto expected = constantValue(link.consumer.getInit());
    if (!next || !expected || next->getBitWidth() != expected->getBitWidth() ||
        *next != *expected) {
      ++stats.blockedInit;
      return false;
    }
    current = std::move(*next);
  }

  for (PipelineLink &link : region.links)
    for (Operation *op : link.coneOps)
      region.coneSet.insert(op);
  region.uniqueCombOps = static_cast<int64_t>(region.coneSet.size());

  int64_t oldStateBits = 0;
  for (pyc::RegOp reg : region.regs) {
    auto width = integerWidth(reg.getQ().getType());
    if (!width) {
      ++stats.blockedCost;
      return false;
    }
    oldStateBits += *width;
  }
  auto headWidth = integerWidth(region.regs.front().getQ().getType());
  const int64_t newStateBits =
      static_cast<int64_t>(*headWidth) * region.regs.size();
  if (newStateBits > oldStateBits) {
    ++stats.blockedCost;
    return false;
  }
  region.stateBitsRemoved = oldStateBits - newStateBits;

  int64_t prefixOps = 0;
  for (unsigned i = 0; i < region.regs.size(); ++i) {
    if (i > 0)
      prefixOps += static_cast<int64_t>(region.links[i - 1].coneOps.size());
    if (!needsRebuiltView(region, i))
      continue;
    region.clonedCombOps += prefixOps;
  }
  region.extraCombOps =
      std::max<int64_t>(0, region.clonedCombOps - region.uniqueCombOps);
  return true;
}

static std::optional<PipelineRegion>
findRegionFromTail(pyc::RegOp tail, const llvm::DenseSet<Operation *> &claimed,
                   RetimeStats &stats) {
  PipelineRegion region;
  llvm::SmallVector<pyc::RegOp> regsFromTail{tail};
  llvm::SmallVector<PipelineLink> linksFromTail;
  llvm::DenseSet<Operation *> localRegs{tail.getOperation()};
  pyc::RegOp cursor = tail;
  bool hasComb = false;

  while (true) {
    ConeMatch match = matchPipelineLink(cursor, tail);
    if (!match.link)
      break;
    auto predecessorInit = constantValue(match.link->predecessor.getInit());
    auto consumerInit = constantValue(match.link->consumer.getInit());
    auto transformedInit =
        predecessorInit ? evaluateLink(*match.link, std::move(*predecessorInit))
                        : std::nullopt;
    if (!transformedInit || !consumerInit ||
        transformedInit->getBitWidth() != consumerInit->getBitWidth() ||
        *transformedInit != *consumerInit) {
      ++stats.blockedInit;
      break;
    }
    pyc::RegOp predecessor = match.link->predecessor;
    if (claimed.contains(predecessor.getOperation()) ||
        !localRegs.insert(predecessor.getOperation()).second)
      break;
    hasComb |= !match.link->coneOps.empty();
    linksFromTail.push_back(std::move(*match.link));
    regsFromTail.push_back(predecessor);
    cursor = predecessor;
  }
  if (regsFromTail.size() < 2 || !hasComb)
    return std::nullopt;

  region.regs.assign(regsFromTail.rbegin(), regsFromTail.rend());
  region.links.assign(linksFromTail.rbegin(), linksFromTail.rend());
  if (!finalizeRegion(region, stats))
    return std::nullopt;
  return region;
}

static Value clonePrefix(PipelineRegion &region, unsigned stage, Value source,
                         OpBuilder &builder) {
  Value current = source;
  for (unsigned i = 0; i < stage; ++i) {
    PipelineLink &link = region.links[i];
    IRMapping mapping;
    mapping.map(link.predecessor.getQ(), current);
    for (Operation *op : link.coneOps)
      builder.clone(*op, mapping);
    current = mapping.lookupOrDefault(link.root);
  }
  return current;
}

static void rewriteRegion(PipelineRegion &region, RetimeStats &stats) {
  pyc::RegOp head = region.regs.front();
  OpBuilder lineBuilder(head);
  const int64_t depth = static_cast<int64_t>(region.regs.size());
  auto line = lineBuilder.create<pyc::DelayLineOp>(
      head.getLoc(), head.getQ().getType(), head.getClk(), head.getRst(),
      head.getEn(), head.getNext(), head.getInit());
  line->setAttr("depth", lineBuilder.getI64IntegerAttr(depth));
  line->setAttr("pyc.optimized_by", lineBuilder.getStringAttr(kRetimedBy));

  llvm::SmallVector<llvm::SmallVector<OpOperand *>> externalUses(
      region.regs.size());
  llvm::DenseSet<Operation *> rewriteAliased;
  for (unsigned i = 0; i < region.regs.size(); ++i) {
    for (OpOperand &use : region.regs[i].getQ().getUses()) {
      Operation *user = use.getOwner();
      // Observation-identity aliases must have their operand rewritten to the
      // rebuilt view even when they sit inside the cone, or cone cleanup
      // drops them and name-based lookups stop working.
      if (auto alias = dyn_cast<pyc::AliasOp>(user))
        if (hasExternalObservationIdentity(alias))
          rewriteAliased.insert(alias.getOperation());
      if (region.coneSet.contains(user) && !rewriteAliased.contains(user))
        continue;
      if (i + 1 < region.regs.size() &&
          user == region.regs[i + 1].getOperation())
        continue;
      externalUses[i].push_back(&use);
    }
  }

  for (unsigned i = 0; i < region.regs.size(); ++i) {
    const bool needsObservation =
        stateHasExternalObservation(region.regs[i].getOperation(),
                                    region.regs[i].getQ());
    if (externalUses[i].empty() && !needsObservation)
      continue;
    OpBuilder builder(region.regs[i]);
    const int64_t tapDepth = static_cast<int64_t>(i + 1);
    Value source = line.getQ();
    if (tapDepth != depth) {
      auto tap = builder.create<pyc::DelayTapOp>(
          region.regs[i].getLoc(), head.getQ().getType(), line.getQ());
      tap->setAttr("depth", builder.getI64IntegerAttr(tapDepth));
      tap->setAttr("pyc.optimized_by", builder.getStringAttr(kRetimedBy));
      source = tap.getTap();
      ++stats.tapsCreated;
    }
    Value rebuilt = clonePrefix(region, i, source, builder);
    remapStateOpIdentity(builder, region.regs[i].getOperation(), rebuilt);
    for (OpOperand *use : externalUses[i])
      use->set(rebuilt);
  }

  for (unsigned i = static_cast<unsigned>(region.regs.size() - 1); i > 0; --i) {
    region.regs[i].erase();
    for (Operation *op : llvm::reverse(region.links[i - 1].coneOps)) {
      bool unused = llvm::all_of(op->getResults(),
                                 [](Value value) { return value.use_empty(); });
      if (!unused)
        continue;
      // Observation-identity aliases were rewritten onto the rebuilt view
      // above and must survive to keep name-based lookups working.
      if (auto alias = dyn_cast<pyc::AliasOp>(op))
        if (hasExternalObservationIdentity(alias))
          continue;
      op->erase();
    }
  }
  head.erase();

  ++stats.regionsRetimed;
  stats.regsRetimed += depth;
  stats.statePrimitivesRemoved += depth - 1;
  stats.combOpsCloned += region.clonedCombOps;
  stats.stateBitsRemoved += region.stateBitsRemoved;
}

static RetimeStats runRetiming(func::FuncOp function) {
  RetimeStats stats;
  runCommonDelaySinking(function, stats);
  llvm::SmallVector<pyc::RegOp> regs;
  function.walk([&](pyc::RegOp reg) { regs.push_back(reg); });

  llvm::DenseSet<Operation *> noClaims;
  llvm::SmallVector<PipelineRegion> candidates;
  for (pyc::RegOp tail : llvm::reverse(regs)) {
    auto region = findRegionFromTail(tail, noClaims, stats);
    if (region)
      candidates.push_back(std::move(*region));
  }
  llvm::stable_sort(candidates,
                    [](const PipelineRegion &lhs, const PipelineRegion &rhs) {
                      if (lhs.regs.size() != rhs.regs.size())
                        return lhs.regs.size() > rhs.regs.size();
                      if (lhs.stateBitsRemoved != rhs.stateBitsRemoved)
                        return lhs.stateBitsRemoved > rhs.stateBitsRemoved;
                      return lhs.extraCombOps < rhs.extraCombOps;
                    });

  llvm::DenseSet<Operation *> claimed;
  for (PipelineRegion &region : candidates) {
    if (llvm::any_of(region.regs, [&](pyc::RegOp reg) {
          return claimed.contains(reg.getOperation());
        }))
      continue;
    for (pyc::RegOp reg : region.regs)
      claimed.insert(reg.getOperation());
  rewriteRegion(region, stats);
  }
  return stats;
}

static void writeStats(func::FuncOp function, const RetimeStats &stats) {
  setI64Attr(function, "pyc.stats.retime_regions_rewritten",
             stats.regionsRetimed);
  setI64Attr(function, "pyc.stats.retime_regs_rewritten", stats.regsRetimed);
  setI64Attr(function, "pyc.stats.retime_state_primitives_removed",
             stats.statePrimitivesRemoved);
  setI64Attr(function, "pyc.stats.retime_taps_created", stats.tapsCreated);
  setI64Attr(function, "pyc.stats.retime_comb_ops_cloned", stats.combOpsCloned);
  setI64Attr(function, "pyc.stats.retime_common_delay_sinks",
             stats.commonDelaySinks);
  setI64Attr(function, "pyc.stats.retime_common_delay_source_states",
             stats.commonDelaySourceStates);
  setI64Attr(function, "pyc.stats.retime_comb_ops_moved", stats.combOpsMoved);
  setI64Attr(function, "pyc.stats.retime_state_bits_removed",
             stats.stateBitsRemoved);
  setI64Attr(function, "pyc.stats.retime_blocked_init", stats.blockedInit);
  setI64Attr(function, "pyc.stats.retime_blocked_cost", stats.blockedCost);
}

struct RetimePipelinesPass
    : public PassWrapper<RetimePipelinesPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(RetimePipelinesPass)

  RetimePipelinesPass() = default;
  RetimePipelinesPass(const RetimePipelinesPass &other) : PassWrapper(other) {}

  StringRef getArgument() const override { return "pyc-retime-pipelines"; }
  StringRef getDescription() const override {
    return "Collapse proven pure-combinational register pipelines into delay "
           "histories and sink consumer cones onto source.next so they fuse "
           "with the producing combinational logic";
  }

  void runOnOperation() override {
    RetimeStats stats = runRetiming(getOperation());
    writeStats(getOperation(), stats);
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createRetimePipelinesPass() {
  return std::make_unique<RetimePipelinesPass>();
}

void runRetimePipelines(func::FuncOp function) {
  RetimeStats stats = runRetiming(function);
  writeStats(function, stats);
}

static PassRegistration<RetimePipelinesPass> retimePass;

} // namespace pyc
