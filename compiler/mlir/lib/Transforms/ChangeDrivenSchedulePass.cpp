#include "pyc/Transforms/ChangeDrivenSchedule.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Transforms/CombDepGraph.h"
#include "pyc/Transforms/Passes.h"

#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/STLExtras.h"

#include <queue>
#include <vector>

using namespace mlir;

namespace pyc {

bool isChangeScheduleNode(Operation *op) {
  return isa_and_nonnull<pyc::CombOp, pyc::InstanceOp, pyc::RegOp,
                         pyc::FifoOp, pyc::ByteMemOp, pyc::SyncMemOp,
                         pyc::SyncMemDPOp, pyc::AsyncFifoOp,
                         pyc::CdcSyncOp>(op);
}

FailureOr<ChangeSchedulePlan> buildChangeDrivenSchedule(func::FuncOp func) {
  ModuleOp module = func->getParentOfType<ModuleOp>();
  if (!module || func.isDeclaration())
    return failure();

  CombDepGraphCache cache(module);
  auto graph = FunctionCombDepGraph::build(func, cache);
  if (failed(graph))
    return failure();

  llvm::SmallVector<unsigned> valueCycle;
  if (failed((*graph)->stableTopologicalOrder(&valueCycle))) {
    func.emitError("change-driven schedule requires an acyclic "
                   "same-tick dependency graph");
    return failure();
  }

  struct Candidate {
    Operation *operation;
    unsigned resultIndex;
    unsigned valueNode;
  };
  llvm::SmallVector<Candidate> candidates;
  for (Block &block : func.getBody())
    for (Operation &op : block)
      if (isChangeScheduleNode(&op)) {
        for (auto result : llvm::enumerate(op.getResults())) {
          auto valueNode = (*graph)->lookupNodeId(result.value());
          if (!valueNode) {
            op.emitError("scheduled result is absent from canonical graph");
            return failure();
          }
          candidates.push_back(Candidate{
              &op, static_cast<unsigned>(result.index()), *valueNode});
        }
      }

  llvm::DenseMap<unsigned, unsigned> candidateIndex;
  for (auto indexed : llvm::enumerate(candidates))
    candidateIndex.try_emplace(indexed.value().valueNode, indexed.index());

  llvm::SmallVector<llvm::DenseSet<unsigned>> predecessors(candidates.size());
  const auto nodes = (*graph)->getNodes();
  const auto edges = (*graph)->getEdges();

  for (unsigned targetIndex = 0; targetIndex < candidates.size();
       ++targetIndex) {
    llvm::DenseSet<unsigned> visited;
    llvm::SmallVector<unsigned> worklist;
    worklist.push_back(candidates[targetIndex].valueNode);

    while (!worklist.empty()) {
      unsigned nodeId = worklist.pop_back_val();
      if (!visited.insert(nodeId).second)
        continue;
      auto producerIt = candidateIndex.find(nodeId);
      if (producerIt != candidateIndex.end() &&
          producerIt->second != targetIndex) {
        predecessors[targetIndex].insert(producerIt->second);
        continue;
      }
      for (unsigned edgeId : nodes[nodeId].incomingEdges)
        worklist.push_back(edges[edgeId].source);
    }
  }

  llvm::SmallVector<llvm::SmallVector<unsigned>> successors(candidates.size());
  std::vector<unsigned> indegree(candidates.size(), 0u);
  for (unsigned target = 0; target < candidates.size(); ++target) {
    indegree[target] = predecessors[target].size();
    for (unsigned source : predecessors[target])
      successors[source].push_back(target);
  }
  for (auto &fanout : successors)
    llvm::sort(fanout);

  std::priority_queue<unsigned, std::vector<unsigned>, std::greater<unsigned>>
      ready;
  for (unsigned index = 0; index < candidates.size(); ++index)
    if (indegree[index] == 0)
      ready.push(index);

  llvm::SmallVector<unsigned> textualToSchedule(candidates.size(), 0u);
  llvm::SmallVector<uint64_t> ranks(candidates.size(), 0u);
  llvm::SmallVector<unsigned> scheduleToTextual;
  while (!ready.empty()) {
    unsigned source = ready.top();
    ready.pop();
    textualToSchedule[source] = scheduleToTextual.size();
    scheduleToTextual.push_back(source);
    for (unsigned target : successors[source]) {
      ranks[target] = std::max(ranks[target], ranks[source] + 1);
      if (--indegree[target] == 0)
        ready.push(target);
    }
  }
  if (scheduleToTextual.size() != candidates.size()) {
    func.emitError("change-driven operation schedule is cyclic");
    return failure();
  }

  ChangeSchedulePlan plan;
  plan.nodes.reserve(candidates.size());
  for (auto scheduled : llvm::enumerate(scheduleToTextual)) {
    unsigned textualIndex = scheduled.value();
    ChangeScheduleNode node;
    node.operation = candidates[textualIndex].operation;
    node.resultIndex = candidates[textualIndex].resultIndex;
    node.id = scheduled.index();
    node.slot = scheduled.index();
    node.rank = ranks[textualIndex];
    for (unsigned target : successors[textualIndex]) {
      uint64_t targetId = textualToSchedule[target];
      // Schedule ids follow topological order, so a backward edge means the
      // successor numbering itself is wrong.
      if (targetId <= node.id) {
        func.emitError("schedule fanout edge is not forward");
        return failure();
      }
      node.fanout.push_back(targetId);
    }
    llvm::sort(node.fanout);
    plan.edgeCount += node.fanout.size();
    plan.rankCount = std::max(plan.rankCount, node.rank + 1);
    plan.nodes.push_back(std::move(node));
  }
  return plan;
}

namespace {

static ArrayAttr integerArrayAttr(MLIRContext *context,
                                  llvm::ArrayRef<uint64_t> values) {
  Builder builder(context);
  llvm::SmallVector<Attribute> attrs;
  attrs.reserve(values.size());
  for (uint64_t value : values)
    attrs.push_back(builder.getI64IntegerAttr(value));
  return builder.getArrayAttr(attrs);
}

struct ChangeDrivenSchedulePass
    : public PassWrapper<ChangeDrivenSchedulePass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(ChangeDrivenSchedulePass)

  StringRef getArgument() const override {
    return "pyc-change-driven-schedule";
  }
  StringRef getDescription() const override {
    return "Attach deterministic change-driven schedule metadata";
  }

  void runOnOperation() override {
    bool failedAny = false;
    for (func::FuncOp func : getOperation().getOps<func::FuncOp>()) {
      if (func.isDeclaration())
        continue;
      func.walk([](Operation *op) {
        op->removeAttr(kChangeScheduleNodeAttr);
        op->removeAttr(kChangeScheduleRankAttr);
        op->removeAttr(kChangeScheduleSlotAttr);
        op->removeAttr(kChangeScheduleFanoutAttr);
      });
      func->removeAttr(kChangeScheduleSummaryAttr);
      auto plan = buildChangeDrivenSchedule(func);
      if (failed(plan)) {
        failedAny = true;
        continue;
      }

      Builder builder(func.getContext());
      struct OperationMetadata {
        llvm::SmallVector<uint64_t> ids;
        llvm::SmallVector<uint64_t> ranks;
        llvm::SmallVector<uint64_t> slots;
        llvm::SmallVector<llvm::SmallVector<uint64_t>> fanouts;
      };
      llvm::DenseMap<Operation *, OperationMetadata> metadata;
      for (const ChangeScheduleNode &node : plan->nodes) {
        auto [entry, inserted] = metadata.try_emplace(node.operation);
        if (inserted) {
          unsigned resultCount = node.operation->getNumResults();
          entry->second.ids.resize(resultCount);
          entry->second.ranks.resize(resultCount);
          entry->second.slots.resize(resultCount);
          entry->second.fanouts.resize(resultCount);
        }
        OperationMetadata &opMetadata = entry->second;
        opMetadata.ids[node.resultIndex] = node.id;
        opMetadata.ranks[node.resultIndex] = node.rank;
        opMetadata.slots[node.resultIndex] = node.slot;
        opMetadata.fanouts[node.resultIndex] = node.fanout;
      }
      for (auto &entry : metadata) {
        Operation *op = entry.first;
        OperationMetadata &opMetadata = entry.second;
        op->setAttr(kChangeScheduleNodeAttr,
                    integerArrayAttr(func.getContext(), opMetadata.ids));
        op->setAttr(kChangeScheduleRankAttr,
                    integerArrayAttr(func.getContext(), opMetadata.ranks));
        op->setAttr(kChangeScheduleSlotAttr,
                    integerArrayAttr(func.getContext(), opMetadata.slots));
        llvm::SmallVector<Attribute> fanouts;
        fanouts.reserve(opMetadata.fanouts.size());
        for (const auto &fanout : opMetadata.fanouts)
          fanouts.push_back(integerArrayAttr(func.getContext(), fanout));
        op->setAttr(kChangeScheduleFanoutAttr, builder.getArrayAttr(fanouts));
      }

      NamedAttrList summary;
      summary.append("schema", builder.getStringAttr(kChangeScheduleSchema));
      summary.append("node_count",
                     builder.getI64IntegerAttr(plan->nodes.size()));
      summary.append("edge_count", builder.getI64IntegerAttr(plan->edgeCount));
      summary.append("rank_count", builder.getI64IntegerAttr(plan->rankCount));
      func->setAttr(kChangeScheduleSummaryAttr,
                    builder.getDictionaryAttr(summary));
    }
    if (failedAny)
      signalPassFailure();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createChangeDrivenSchedulePass() {
  return std::make_unique<ChangeDrivenSchedulePass>();
}

static PassRegistration<ChangeDrivenSchedulePass> schedulePass;

} // namespace pyc
