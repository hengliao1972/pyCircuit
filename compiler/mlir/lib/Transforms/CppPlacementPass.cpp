#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Emit/CppEmitter.h"
#include "pyc/Transforms/Passes.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Attributes.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Operation.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/StringRef.h"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <queue>
#include <vector>

using namespace mlir;

namespace pyc {

namespace {

// ---------------------------------------------------------------------------
// placement-writer helpers (used only inside this TU)
// ---------------------------------------------------------------------------

// IR attribute names shared with the emitter (kCppStorageAttr / kCppOwnerAttr /
// kCppMethodAttr / kCppPlacementSummaryAttr / kCppCombChunkNodesAttr) come from
// Emit/CppEmitter.h via the placement contract. No pass-only attribute names
// are needed.

static std::string methodForCombPart(unsigned combIdx, unsigned partIdx, bool hasParts) {
  if (hasParts)
    return "eval_comb_" + std::to_string(combIdx) + "_part_" + std::to_string(partIdx);
  return "eval_comb_" + std::to_string(combIdx);
}

// Approximate register / transfer cost used when scoring live wires and cuts.
// Wider integers cost more (ceil(width/64) limbs) so cuts prefer thinner values.
// Vectors cost proportionally to element count × element width, since a
// crossing vector value copies the whole nested Vec across the cut.
static uint64_t placementWeight(Value v) {
  Type ty = v.getType();
  if (auto intTy = dyn_cast<IntegerType>(ty))
    return 1 + (intTy.getWidth() + 63) / 64;
  if (auto vecTy = dyn_cast<VectorType>(ty)) {
    uint64_t elems = 1;
    for (unsigned dim : vecTy.getShape())
      elems *= dim;
    uint64_t elemWeight = 1;
    if (auto elemIntTy = dyn_cast<IntegerType>(vecTy.getElementType()))
      elemWeight = 1 + (elemIntTy.getWidth() + 63) / 64;
    return elems * elemWeight;
  }
  return 1;
}

// True when `v` is defined inside `comb` and every use stays in that Comb body
// (not Yield, not escaped). Only these values can become method-local and pay
// into crossing / live-range costs.
static bool isLocalizableCombValue(Value v, pyc::CombOp comb) {
  if (!v.getDefiningOp() || v.getDefiningOp()->getParentOfType<pyc::CombOp>() != comb)
    return false;
  for (OpOperand &use : v.getUses()) {
    Operation *user = use.getOwner();
    if (isa<pyc::YieldOp>(user) || user->getParentOfType<pyc::CombOp>() != comb)
      return false;
  }
  return true;
}

// Reorder Comb body ops while preserving SSA edges to shrink live wires before
// chunking. Ready nodes are ranked by remaining work/depth first, then by
// live-weight delta (prefer closing heavy wires), then by stable original index.
// Falls back to `original` if the dependency graph cannot produce a total order.
static llvm::SmallVector<Operation *> localityAwareTopologicalOrder(
    pyc::CombOp comb, ArrayRef<Operation *> original) {
  const unsigned n = original.size();
  llvm::DenseMap<Operation *, unsigned> nodeIndex;
  for (auto [i, op] : llvm::enumerate(original))
    nodeIndex.try_emplace(op, static_cast<unsigned>(i));

  // Build intra-comb SSA dependence graph.
  llvm::SmallVector<llvm::SmallVector<unsigned>> successors(n);
  llvm::SmallVector<unsigned> indegree(n, 0);
  for (auto [i, op] : llvm::enumerate(original)) {
    llvm::DenseSet<unsigned> dependencies;
    for (Value operand : op->getOperands()) {
      auto it = nodeIndex.find(operand.getDefiningOp());
      if (it != nodeIndex.end())
        dependencies.insert(it->second);
    }
    indegree[i] = dependencies.size();
    for (unsigned dependency : dependencies)
      successors[dependency].push_back(i);
  }

  // Static shortest-completion estimate. The saturated sum distinguishes a
  // short narrow chain from a shallow but wide fanout; depth breaks saturated
  // ties. The original body is already in SSA dominance order.
  constexpr uint64_t kWorkLimit = std::numeric_limits<uint64_t>::max() / 4;
  llvm::SmallVector<uint64_t> completionWork(n, 1);
  llvm::SmallVector<unsigned> remainingDepth(n, 1);
  for (unsigned i = n; i-- > 0;) {
    uint64_t work = 1;
    unsigned depth = 1;
    for (unsigned successor : successors[i]) {
      work = std::min(kWorkLimit, work + completionWork[successor]);
      depth = std::max(depth, 1 + remainingDepth[successor]);
    }
    completionWork[i] = work;
    remainingDepth[i] = depth;
  }

  llvm::DenseMap<Value, unsigned> remainingUses;
  for (Operation *op : original)
    for (Value operand : op->getOperands())
      if (nodeIndex.count(operand.getDefiningOp()))
        ++remainingUses[operand];

  // Priority-queue key: smaller work/depth first; then smaller liveDelta
  // (close more weight than open); then larger closedWeight; then earlier index.
  struct ReadyCandidate {
    uint64_t work;
    unsigned depth;
    int64_t liveDelta;
    uint64_t closedWeight;
    unsigned index;
  };
  auto worse = [](const ReadyCandidate &a, const ReadyCandidate &b) {
    if (a.work != b.work)
      return a.work > b.work;
    if (a.depth != b.depth)
      return a.depth > b.depth;
    if (a.liveDelta != b.liveDelta)
      return a.liveDelta > b.liveDelta;
    if (a.closedWeight != b.closedWeight)
      return a.closedWeight < b.closedWeight;
    return a.index > b.index;
  };
  std::priority_queue<ReadyCandidate, std::vector<ReadyCandidate>, decltype(worse)> ready(worse);

  auto makeCandidate = [&](unsigned index) {
    Operation *op = original[index];
    uint64_t opened = 0;
    uint64_t closed = 0;
    // Values this op newly makes live.
    for (Value result : op->getResults())
      if (isLocalizableCombValue(result, comb) &&
          remainingUses.lookup(result) != 0)
        opened += placementWeight(result);

    // Values whose last remaining use is consumed by this op.
    llvm::DenseMap<Value, unsigned> usesHere;
    for (Value operand : op->getOperands())
      if (nodeIndex.count(operand.getDefiningOp()))
        ++usesHere[operand];
    for (auto &entry : usesHere)
      if (remainingUses.lookup(entry.first) == entry.second)
        closed += placementWeight(entry.first);

    return ReadyCandidate{completionWork[index], remainingDepth[index],
                          static_cast<int64_t>(opened) -
                              static_cast<int64_t>(closed),
                          closed, index};
  };

  for (unsigned i = 0; i < n; ++i)
    if (indegree[i] == 0)
      ready.push(makeCandidate(i));

  // Kahn-style scheduling over the ready set.
  llvm::SmallVector<Operation *> ordered;
  ordered.reserve(n);
  while (!ready.empty()) {
    unsigned selected = ready.top().index;
    ready.pop();
    Operation *selectedOp = original[selected];
    ordered.push_back(selectedOp);
    for (Value operand : selectedOp->getOperands()) {
      auto it = remainingUses.find(operand);
      if (it != remainingUses.end() && it->second != 0)
        --it->second;
    }
    for (unsigned successor : successors[selected])
      if (--indegree[successor] == 0)
        ready.push(makeCandidate(successor));
  }

  if (ordered.size() != n)
    return llvm::SmallVector<Operation *>(original.begin(), original.end());
  return ordered;
}

// Choose exclusive part end indices for `order` that minimize total weighted
// cut cost. `maxChunkNodes` is a per-part size cap (TU budget), not a fill
// target: parts may be shorter. Uses the fewest parts K = ceil(n / M) so that
// definition regions stay as large as the cap allows, which favors fewer
// crosses under the localizable-cut metric. Returns ends like [e0, ..., n].
// Falls back to fixed-size ends if the DP cannot reach a valid cover.
static llvm::SmallVector<unsigned>
optimalPartEnds(pyc::CombOp comb, ArrayRef<Operation *> order, unsigned maxChunkNodes) {
  const unsigned n = order.size();
  if (n == 0)
    return {};
  const unsigned parts = (n + maxChunkNodes - 1) / maxChunkNodes;
  if (parts == 1)
    return {n};

  llvm::DenseMap<Operation *, unsigned> position;
  for (auto [i, op] : llvm::enumerate(order))
    position.try_emplace(op, static_cast<unsigned>(i));

  // lastUse[v] = last consumer index in `order` (inclusive).
  llvm::DenseMap<Value, unsigned> lastUse;
  llvm::DenseMap<Value, uint64_t> weights;
  for (auto [i, op] : llvm::enumerate(order)) {
    for (Value result : op->getResults()) {
      if (!isLocalizableCombValue(result, comb))
        continue;
      unsigned last = i;
      for (OpOperand &use : result.getUses()) {
        auto it = position.find(use.getOwner());
        if (it != position.end())
          last = std::max(last, it->second);
      }
      lastUse[result] = last;
      weights[result] = placementWeight(result);
    }
  }

  // Represent each block as size = M - deficit. With minimum part count,
  // slack = K*M - n satisfies 0 <= slack < M. Enumerate every deficit so the
  // search over legal part lengths is exact (minimize weighted cross).
  const unsigned slack = parts * maxChunkNodes - n;
  std::vector<unsigned> deficits;
  deficits.reserve(slack + 1);
  for (unsigned deficit = 0; deficit <= slack; ++deficit)
    deficits.push_back(deficit);
  const unsigned zeroState = 0;
  const unsigned finalState = slack;
  const uint64_t inf = std::numeric_limits<uint64_t>::max() / 4;
  std::vector<uint64_t> previous(deficits.size(), inf), current(deficits.size(), inf);
  std::vector<std::vector<unsigned>> predecessor(
      parts + 1,
      std::vector<unsigned>(deficits.size(), std::numeric_limits<unsigned>::max()));
  previous[zeroState] = 0;

  // DP: minimize total cut weight over `parts` blocks whose deficits sum to
  // slack (each part size <= maxChunkNodes).
  for (unsigned part = 1; part <= parts; ++part) {
    std::fill(current.begin(), current.end(), inf);
    for (auto [deficitState, deficit] : llvm::enumerate(deficits)) {
      unsigned end = part * maxChunkNodes - deficit;
      if (end == 0 || end > n)
        continue;

      // crossingByLength[len] is the exact weight of values defined in the
      // candidate block [end-len,end) and used at or after end.
      unsigned maxLength = std::min(maxChunkNodes, end);
      std::vector<uint64_t> crossingByLength(maxLength + 1, 0);
      uint64_t crossing = 0;
      for (unsigned length = 1; length <= maxLength; ++length) {
        Operation *definition = order[end - length];
        for (Value result : definition->getResults()) {
          auto useIt = lastUse.find(result);
          if (useIt != lastUse.end() && useIt->second >= end)
            crossing += weights.lookup(result);
        }
        crossingByLength[length] = crossing;
      }

      for (auto [previousState, previousDeficit] : llvm::enumerate(deficits)) {
        if (previousDeficit > deficit || previous[previousState] == inf)
          continue;
        unsigned begin = (part - 1) * maxChunkNodes - previousDeficit;
        unsigned length = end - begin;
        if (length == 0 || length > maxLength)
          continue;
        uint64_t candidate = previous[previousState] + crossingByLength[length];
        if (candidate < current[deficitState]) {
          current[deficitState] = candidate;
          predecessor[part][deficitState] = previousState;
        }
      }
    }
    previous.swap(current);
  }

  // Unreachable final deficit: fall back to uniform maxChunkNodes cuts.
  if (previous[finalState] == inf) {
    llvm::SmallVector<unsigned> fixed;
    for (unsigned end = maxChunkNodes; end < n; end += maxChunkNodes)
      fixed.push_back(end);
    fixed.push_back(n);
    return fixed;
  }

  // Reconstruct exclusive ends from the chosen deficit per part.
  llvm::SmallVector<unsigned> reversed;
  unsigned state = finalState;
  for (unsigned part = parts; part > 0; --part) {
    unsigned deficit = deficits[state];
    reversed.push_back(part * maxChunkNodes - deficit);
    state = predecessor[part][state];
  }
  std::reverse(reversed.begin(), reversed.end());
  return reversed;
}

// Count localizable values whose uses leave the defining part, plus the sum of
// their placementWeight. Returned as {crossingValueCount, weightedCutCost}.
static std::pair<unsigned, uint64_t>
crossingStats(pyc::CombOp comb, ArrayRef<Operation *> order,
              ArrayRef<unsigned> partEnds) {
  llvm::DenseMap<Operation *, unsigned> partForOp;
  unsigned begin = 0;
  for (auto [part, end] : llvm::enumerate(partEnds)) {
    for (unsigned i = begin; i < end; ++i)
      partForOp[order[i]] = part;
    begin = end;
  }

  unsigned values = 0;
  uint64_t weight = 0;
  for (Operation *op : order) {
    unsigned definitionPart = partForOp.lookup(op);
    for (Value result : op->getResults()) {
      if (!isLocalizableCombValue(result, comb))
        continue;
      bool crosses = false;
      for (OpOperand &use : result.getUses()) {
        auto it = partForOp.find(use.getOwner());
        if (it != partForOp.end() && it->second != definitionPart) {
          crosses = true;
          break;
        }
      }
      if (crosses) {
        ++values;
        weight += placementWeight(result);
      }
    }
  }
  return {values, weight};
}

// Assign each Comb body op to an eval_comb_* / eval_comb_*_part_* method.
// When chunking, compare locality-aware schedule + DP cuts against fixed-size
// chunks, reorder the body to the winner, and stamp pyc.cpp.method.
//
// `crossPartValues` collects localizable comb values whose uses leave their
// defining part (i.e. they cross method boundaries). The placement main loop
// uses this set to promote those values to struct members: a local Wire<> is
// only valid when every use shares the same part method. Cut cost stats are
// accumulated into `summary` (no intermediate IR attrs).
static void assignCombOpMethods(pyc::CombOp comb, unsigned combIdx, unsigned combChunkNodes,
                                llvm::DenseMap<Operation *, std::string> &opToMethod,
                                llvm::DenseSet<mlir::Value> &crossPartValues,
                                CppPlacementSummary &summary) {
  Block &b = comb.getBody().front();
  llvm::SmallVector<Operation *> combOps;
  for (Operation &op : b) {
    if (isa<pyc::YieldOp>(op))
      break;
    combOps.push_back(&op);
  }

  // Small combs stay in one method: eval_comb_N.
  const bool chunk = combOps.size() > combChunkNodes;
  if (!chunk) {
    std::string m = methodForCombPart(combIdx, 0, false);
    for (Operation *op : combOps) {
      opToMethod[op] = m;
      op->setAttr(kCppMethodAttr, StringAttr::get(op->getContext(), m));
    }
    return;
  }

  // Large combs are split into eval_comb_N_part_K shards.
  // Prefer a locality-aware schedule that keeps cut wires small; if it does not
  // beat fixed-size chunking, fall back to the original body order.
  llvm::SmallVector<Operation *> ordered = localityAwareTopologicalOrder(comb, combOps);
  llvm::SmallVector<unsigned> partEnds = optimalPartEnds(comb, ordered, combChunkNodes);

  // Baseline: preserve original SSA order and cut every combChunkNodes ops.
  llvm::SmallVector<unsigned> fixedPartEnds;
  for (unsigned end = combChunkNodes; end < combOps.size(); end += combChunkNodes)
    fixedPartEnds.push_back(end);
  fixedPartEnds.push_back(combOps.size());

  // Stats are (crossing value count, weighted cut cost). Prefer lower weight,
  // then fewer crossing values when weights tie.
  auto fixedStats = crossingStats(comb, combOps, fixedPartEnds);
  auto scheduledStats = crossingStats(comb, ordered, partEnds);
  if (scheduledStats.second > fixedStats.second ||
      (scheduledStats.second == fixedStats.second &&
       scheduledStats.first > fixedStats.first)) {
    ordered.assign(combOps.begin(), combOps.end());
    partEnds.assign(fixedPartEnds.begin(), fixedPartEnds.end());
    scheduledStats = fixedStats;
  }

  summary.scheduledCrossMethod += scheduledStats.first;
  summary.scheduledCutWeight += scheduledStats.second;

  // Materialize the chosen order in the Comb body.
  Operation *terminator = b.getTerminator();
  for (Operation *op : ordered)
    op->moveBefore(terminator);

  // Stamp each op with its part method so the C++ emitter can split the TU.
  // Also record which localizable values cross part boundaries: their defining
  // op is in one part but at least one use is in another.
  llvm::DenseMap<Operation *, unsigned> partIndexOfOp;
  unsigned begin = 0;
  for (auto [partIdx, end] : llvm::enumerate(partEnds)) {
    std::string m = methodForCombPart(combIdx, partIdx, true);
    for (unsigned i = begin; i < end; ++i) {
      opToMethod[ordered[i]] = m;
      ordered[i]->setAttr(kCppMethodAttr, StringAttr::get(ordered[i]->getContext(), m));
      partIndexOfOp[ordered[i]] = partIdx;
    }
    begin = end;
  }

  for (Operation *op : ordered) {
    unsigned defPart = partIndexOfOp.lookup(op);
    for (mlir::Value result : op->getResults()) {
      if (!isLocalizableCombValue(result, comb))
        continue;
      for (OpOperand &use : result.getUses()) {
        auto it = partIndexOfOp.find(use.getOwner());
        if (it != partIndexOfOp.end() && it->second != defPart) {
          crossPartValues.insert(result);
          break;
        }
      }
    }
  }

  // The comb terminator (pyc.yield) lives in the main eval_comb_N method, not
  // in any _part_K shard (combOps breaks at yield, so yield is absent from
  // partIndexOfOp). Each yield operand is read by the main method after every
  // part has run. If such an operand is defined inside a part shard, it
  // crosses the part->main boundary and must be promoted to a struct member —
  // otherwise the emitter references an undeclared method-local Wire<> and the
  // generated C++ fails to compile
  //
  // NOTE: isLocalizableCombValue() returns false for any value consumed by
  // yield (it treats yield as an escaping use), so we cannot use it here.
  // The test we need is narrower: the value is defined by an op that was
  // assigned to a part shard (i.e. present in partIndexOfOp) and lives inside
  // this comb.
  if (auto yield = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator())) {
    for (mlir::Value v : yield.getOperands()) {
      Operation *def = v.getDefiningOp();
      if (!def)
        continue;
      if (def->getParentOfType<pyc::CombOp>() != comb)
        continue;
      if (partIndexOfOp.count(def))
        crossPartValues.insert(v);
    }
  }
}

// Top-level Combs in body order; indices match emitFunc's comb enumeration.
static llvm::SmallVector<pyc::CombOp> collectTopLevelCombs(func::FuncOp f) {
  llvm::SmallVector<pyc::CombOp> combs;
  if (f.getBody().empty())
    return combs;
  for (Operation &op : f.getBody().front())
    if (auto comb = dyn_cast<pyc::CombOp>(op))
      combs.push_back(comb);
  return combs;
}

/// Returns true for values that must remain struct members (cannot be localized).
static bool pinToStruct(Value v) {
  Operation *def = v.getDefiningOp();
  if (!def)
    return true;

  // Top-level comb results and state-holding ops always live on the struct.
  if (isa<pyc::CombOp>(def))
    return true;
  if (isa<pyc::RegOp, pyc::InstanceOp, pyc::FifoOp, pyc::ByteMemOp, pyc::SyncMemOp, pyc::SyncMemDPOp,
          pyc::AsyncFifoOp, pyc::CdcSyncOp>(def))
    return true;

  // Values defined inside a comb but used outside it must be struct members.
  if (def->getParentOfType<pyc::CombOp>()) {
    for (OpOperand &use : v.getUses()) {
      Operation *user = use.getOwner();
      if (!user->getParentOfType<pyc::CombOp>())
        return true;
    }
  }
  return false;
}

/// Annotate a single value with its storage kind and owning method.
static void annotatePlacement(Value v, CppStorageKind kind, StringRef owner) {
  Operation *op = v.getDefiningOp();
  if (!op)
    return;
  auto *ctx = op->getContext();
  op->setAttr(kCppStorageAttr,
              StringAttr::get(ctx, kind == CppStorageKind::Local ? "local" : "struct"));
  if (!owner.empty())
    op->setAttr(kCppOwnerAttr, StringAttr::get(ctx, owner));
  else
    op->removeAttr(kCppOwnerAttr);
}

/// Set the per-module comb chunk size. Read-back lives in the emitter TU.
static void setModuleCombChunkNodes(ModuleOp module, unsigned combChunkNodes) {
  auto *ctx = module.getContext();
  module->setAttr(kCppCombChunkNodesAttr,
                  IntegerAttr::get(IntegerType::get(ctx, 64), combChunkNodes));
}

static void setFuncPlacementSummary(func::FuncOp f, const CppPlacementSummary &summary) {
  auto *ctx = f.getContext();
  llvm::SmallVector<NamedAttribute, 8> fields;
  fields.emplace_back(StringAttr::get(ctx, "struct_members"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.structMembers));
  fields.emplace_back(StringAttr::get(ctx, "local_in_method"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.localInMethod));
  fields.emplace_back(StringAttr::get(ctx, "probe_pinned_struct"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.probePinnedStruct));
  fields.emplace_back(StringAttr::get(ctx, "cross_part_promoted"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.crossPartPromoted));
  fields.emplace_back(StringAttr::get(ctx, "scheduled_cross_method"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.scheduledCrossMethod));
  fields.emplace_back(StringAttr::get(ctx, "scheduled_cut_weight"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.scheduledCutWeight));
  f->setAttr(kCppPlacementSummaryAttr, DictionaryAttr::get(ctx, fields));
}

/// Decide struct vs method-local storage for every value in \p f and annotate the IR.
/// Returns placement statistics consumed by the build profile JSON.
static CppPlacementSummary runCppMemberPlacement(func::FuncOp f, unsigned combChunkNodes) {
  CppPlacementSummary summary;

  // Phase A — Comb method assignment (independent of storage decisions):
  // assign each comb body op to an eval_comb_* / eval_comb_*_part_* method and
  // record which localizable values cross part boundaries. Storage is NOT
  // decided here; that is Phase B's job, using only the comb boundary.
  llvm::DenseMap<Operation *, std::string> opToMethod;
  llvm::DenseSet<Value> crossPartValues;
  llvm::SmallVector<pyc::CombOp> combs = collectTopLevelCombs(f);
  for (auto [i, comb] : llvm::enumerate(combs))
    assignCombOpMethods(comb, static_cast<unsigned>(i), combChunkNodes, opToMethod,
                        crossPartValues, summary);

  llvm::SmallVector<Value> candidates;
  f.walk([&](Operation *op) {
    if (op->getParentOfType<pyc::CombOp>() == nullptr)
      return;
    if (isa<pyc::YieldOp>(op))
      return;
    for (Value r : op->getResults())
      candidates.push_back(r);
  });

  // Phase B — Storage decision using ONLY the comb boundary:
  //   a value is Local iff it is defined inside a comb, is not a comb result,
  //   is not a block argument, and every use stays inside the same comb.
  // The comb region trait IsolatedFromAbove guarantees no SSA can escape the
  // region except via comb results / block args, so this rule is exact.
  //
  // One additional demotion applies: a value that crosses part methods cannot
  // be a method-local Wire<> (it would be invisible to the other part), so it
  // is promoted to a struct member even though it passes the boundary test.
  for (Value v : candidates) {
    Operation *def = v.getDefiningOp();

    // Owner method is only needed for Local values (emit uses it to gate where
    // the local Wire<> is declared). Struct values carry an empty owner.
    std::string owner;
    if (def) {
      auto it = opToMethod.find(def);
      if (it != opToMethod.end())
        owner = it->second;
    }

    // Pinned-to-struct values (block args, state ops, comb results, values
    // escaping their comb) always live on the struct.
    if (pinToStruct(v)) {
      annotatePlacement(v, CppStorageKind::Struct, {});
      summary.structMembers++;
      if (!def)
        summary.probePinnedStruct++;
      continue;
    }

    // Boundary rule says Local. Demote to struct if the value is used across
    // part methods (collected in Phase A), since a method-local Wire<> would
    // be invisible to the consuming part.
    if (crossPartValues.contains(v)) {
      annotatePlacement(v, CppStorageKind::Struct, {});
      summary.structMembers++;
      summary.crossPartPromoted++;
      continue;
    }

    annotatePlacement(v, CppStorageKind::Local, owner);
    summary.localInMethod++;
  }

  // Phase C: ensure non-comb values (regs, instances, ports, etc.) are struct members.
  // Storage kind read-back mirrors the emit-side getValueCppStorage contract:
  // default Struct when the attribute is absent.
  f.walk([&](Operation *op) {
    if (op->getParentOfType<pyc::CombOp>() != nullptr)
      return;
    for (Value r : op->getResults()) {
      auto a = op->getAttrOfType<StringAttr>(kCppStorageAttr);
      if (a && a.getValue() == "local")
        continue;
      annotatePlacement(r, CppStorageKind::Struct, {});
      summary.structMembers++;
    }
  });

  return summary;
}

} // namespace

// ---------------------------------------------------------------------------
// public entry points declared in Emit/CppEmitter.h
// ---------------------------------------------------------------------------

// Aggregate per-function summaries across the module. Reads back the summary
// attribute via the same field names the writer stamps. Kept on the pass side
// (not in the emitter TU) because it walks the placement contract written by
// this pass — keeping all placement-attribute knowledge together.
CppPlacementSummary accumulateModulePlacementSummary(ModuleOp module) {
  CppPlacementSummary totals;
  for (auto f : module.getOps<func::FuncOp>()) {
    if (f.isDeclaration())
      continue;
    auto dict = f->getAttrOfType<DictionaryAttr>(kCppPlacementSummaryAttr);
    if (!dict)
      continue;
    auto read = [&](StringRef key) -> std::optional<uint64_t> {
      if (auto intAttr = dyn_cast_or_null<IntegerAttr>(dict.get(key)))
        return intAttr.getValue().getZExtValue();
      return std::nullopt;
    };
    if (auto v = read("struct_members"))
      totals.structMembers += static_cast<unsigned>(*v);
    if (auto v = read("local_in_method"))
      totals.localInMethod += static_cast<unsigned>(*v);
    if (auto v = read("probe_pinned_struct"))
      totals.probePinnedStruct += static_cast<unsigned>(*v);
    if (auto v = read("cross_part_promoted"))
      totals.crossPartPromoted += static_cast<unsigned>(*v);
    if (auto v = read("scheduled_cross_method"))
      totals.scheduledCrossMethod += static_cast<unsigned>(*v);
    if (auto v = read("scheduled_cut_weight"))
      totals.scheduledCutWeight += *v;
  }
  return totals;
}

// ---------------------------------------------------------------------------
// pass entry point
// ---------------------------------------------------------------------------

struct CppPlacementPass : public PassWrapper<CppPlacementPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CppPlacementPass)

  CppPlacementPass(unsigned chunkNodes)
      : combChunkNodes(chunkNodes) {}

  StringRef getArgument() const override { return "pyc-cpp-placement"; }
  StringRef getDescription() const override {
    return "Set pyc.cpp.comb_chunk_nodes and annotate comb member placement for C++ emit";
  }

  void runOnOperation() override {
    ModuleOp module = getOperation();
    if (combChunkNodes == 0) {
      module.emitError("pyc-cpp-placement requires combChunkNodes > 0");
      return signalPassFailure();
    }
    setModuleCombChunkNodes(module, combChunkNodes);

    for (auto f : module.getOps<func::FuncOp>()) {
      if (f.isDeclaration())
        continue;
      CppPlacementSummary summary = runCppMemberPlacement(f, combChunkNodes);
      setFuncPlacementSummary(f, summary);
    }
  }

  unsigned combChunkNodes;
};

std::unique_ptr<Pass> createCppPlacementPass(unsigned combChunkNodes) {
  return std::make_unique<CppPlacementPass>(combChunkNodes);
}

} // namespace pyc
