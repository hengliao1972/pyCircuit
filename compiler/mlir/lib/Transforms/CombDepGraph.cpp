#include "pyc/Transforms/CombDepGraph.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/SymbolTable.h"
#include "llvm/ADT/BitVector.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"

#include <algorithm>
#include <functional>
#include <limits>
#include <queue>
#include <string>
#include <vector>

using namespace mlir;

namespace pyc {
namespace {

static constexpr int64_t kUnreachable = -1;

static int64_t addReachableDepth(int64_t lhs, int64_t rhs) {
  if (lhs == kUnreachable || rhs == kUnreachable)
    return kUnreachable;
  if (lhs > std::numeric_limits<int64_t>::max() - rhs)
    return std::numeric_limits<int64_t>::max();
  return lhs + rhs;
}

static bool isHardSequentialCut(Operation *op) {
  return isa<pyc::RegOp, pyc::SyncMemOp, pyc::SyncMemDPOp,
             pyc::AsyncFifoOp, pyc::CdcSyncOp>(op);
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
    if (*dim >= static_cast<uint64_t>(vecTy.getRank()))
      return 1;
    lanes = vecTy.getDimSize(static_cast<int64_t>(*dim));
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
  if (isHardSequentialCut(op))
    return 0;
  if (isa<pyc::WireOp, pyc::AliasOp, pyc::ResetActiveOp, pyc::ConstantOp, pyc::CombOp, pyc::YieldOp,
          arith::ConstantOp>(op))
    return 0;
  if (isa<pyc::VGetOp, pyc::VCreateOp, pyc::VBroadcastOp,
          pyc::VBroadcastDimOp>(op))
    return 0;
  if (auto vr = dyn_cast<pyc::VOrReduceOp>(op))
    return vectorReduceCost(vr);
  if (auto vr = dyn_cast<pyc::VAndReduceOp>(op))
    return vectorReduceCost(vr);
  if (auto vr = dyn_cast<pyc::VAddReduceOp>(op))
    return vectorReduceCost(vr);
  return 1;
}

/// One exact same-TICK path from an operation operand to one result.  Cost is
/// the logic depth added after the operand value reaches the operation.
struct OperandDependency {
  unsigned operandIndex = 0;
  int64_t cost = 0;
};

enum class ResultTransferKind : uint8_t {
  DirectOperands,
  WireDriver,
  CombRegion,
};

/// Canonical per-result transfer contract shared by graph construction and
/// summary/depth analysis.  baseDepth is the depth from state/internal sources
/// to this result, or kUnreachable when every path starts at an operand.
struct ResultTransfer {
  ResultTransferKind kind = ResultTransferKind::DirectOperands;
  llvm::SmallVector<OperandDependency> operandDependencies;
  int64_t baseDepth = kUnreachable;
  CombDepEdgeKind edgeKind = CombDepEdgeKind::SSA;
};

static bool isKnownDirectCombOperation(Operation *op) {
  return isa<pyc::AddOp, pyc::SubOp, pyc::MulOp, pyc::UdivOp,
             pyc::UremOp, pyc::SdivOp, pyc::SremOp, pyc::MuxOp,
             pyc::AndOp, pyc::OrOp, pyc::XorOp, pyc::NotOp,
             pyc::EqOp, pyc::UltOp, pyc::SltOp, pyc::TruncOp,
             pyc::ZextOp, pyc::SextOp, pyc::ExtractOp, pyc::ShliOp,
             pyc::LshriOp, pyc::AshriOp, pyc::ShlOp, pyc::LshrOp,
             pyc::AshrOp, pyc::ConcatOp, pyc::AliasOp,
             pyc::ResetActiveOp, pyc::VGetOp, pyc::VCreateOp,
             pyc::VBroadcastOp, pyc::VBroadcastDimOp, pyc::VOrReduceOp,
             pyc::VAndReduceOp, pyc::VAddReduceOp, arith::SelectOp>(op);
}

static FailureOr<ResultTransfer>
resolveResultTransfer(Operation *op, unsigned resultIndex, ModuleOp module,
                      CombDepGraphCache &cache) {
  if (!op || resultIndex >= op->getNumResults()) {
    if (op)
      op->emitError("invalid result index while resolving canonical "
                    "combinational dependency transfer");
    return failure();
  }

  ResultTransfer transfer;
  if (isa<pyc::WireOp>(op)) {
    transfer.kind = ResultTransferKind::WireDriver;
    return transfer;
  }
  if (isa<pyc::CombOp>(op)) {
    transfer.kind = ResultTransferKind::CombRegion;
    return transfer;
  }
  if (isHardSequentialCut(op)) {
    transfer.baseDepth = 0;
    return transfer;
  }
  if (isa<pyc::ConstantOp, arith::ConstantOp>(op)) {
    transfer.baseDepth = 0;
    return transfer;
  }
  if (isa<pyc::FifoOp>(op)) {
    // in_ready = f(state, out_ready); out_valid/out_data are state sources.
    transfer.edgeKind = CombDepEdgeKind::PrimitiveComb;
    transfer.baseDepth = resultIndex == 0u ? 1 : 0;
    if (resultIndex == 0u)
      transfer.operandDependencies.push_back({4u, 1});
    return transfer;
  }
  if (isa<pyc::ByteMemOp>(op)) {
    // The asynchronous read result depends on both memory state and raddr.
    transfer.edgeKind = CombDepEdgeKind::PrimitiveComb;
    transfer.baseDepth = 1;
    transfer.operandDependencies.push_back({2u, 1});
    return transfer;
  }
  if (auto instance = dyn_cast<pyc::InstanceOp>(op)) {
    auto calleeAttr = instance->getAttrOfType<FlatSymbolRefAttr>("callee");
    if (!calleeAttr) {
      instance.emitError("pyc.instance missing required `callee` attr");
      return failure();
    }
    Operation *symbol =
        module ? SymbolTable::lookupSymbolIn(module, calleeAttr.getValue())
               : nullptr;
    auto callee = dyn_cast_or_null<func::FuncOp>(symbol);
    if (!callee) {
      instance.emitError("pyc.instance callee is not a func.func symbol: ")
          << calleeAttr.getValue();
      return failure();
    }
    const FuncCombSummary *summary = cache.getFuncSummary(callee);
    if (!summary) {
      if (!(callee.isDeclaration() || callee.getBody().empty())) {
        instance.emitError("failed to compute callee comb summary for @")
            << calleeAttr.getValue();
        return failure();
      }
      // A separate .pyc has no callee body in this module. Depend on every
      // input so dirty scheduling cannot skip a real combinational path.
      transfer.baseDepth = 1;
      transfer.edgeKind = CombDepEdgeKind::InstancePort;
      transfer.operandDependencies.reserve(instance.getNumOperands());
      for (unsigned inputIndex = 0; inputIndex < instance.getNumOperands();
           ++inputIndex)
        transfer.operandDependencies.push_back({inputIndex, 1});
      return transfer;
    }
    if (summary->numArgs != instance.getNumOperands() ||
        summary->numResults != instance.getNumResults()) {
      instance.emitError(
          "callee dependency summary arity does not match instance signature "
          "for @")
          << calleeAttr.getValue();
      return failure();
    }
    if (resultIndex >= summary->results.size()) {
      instance.emitError(
          "instance result index is outside the hardened callee summary for @")
          << calleeAttr.getValue();
      return failure();
    }

    const CombResultSummary &result = summary->results[resultIndex];
    if (result.argDepth.size() != instance.getNumOperands()) {
      instance.emitError(
          "callee dependency summary depth arity does not match instance "
          "signature for @")
          << calleeAttr.getValue();
      return failure();
    }
    transfer.baseDepth = result.baseDepth;
    transfer.edgeKind = CombDepEdgeKind::InstancePort;
    for (unsigned inputIndex = 0; inputIndex < result.argDepth.size();
         ++inputIndex) {
      int64_t depth = result.argDepth[inputIndex];
      if (depth != kUnreachable)
        transfer.operandDependencies.push_back({inputIndex, depth});
    }
    return transfer;
  }
  if (isKnownDirectCombOperation(op)) {
    int64_t cost = opCost(op);
    transfer.operandDependencies.reserve(op->getNumOperands());
    for (unsigned operandIndex = 0; operandIndex < op->getNumOperands();
         ++operandIndex)
      transfer.operandDependencies.push_back({operandIndex, cost});
    return transfer;
  }

  op->emitError(
      "has results but no registered canonical per-result combinational "
      "dependency transfer");
  return failure();
}

struct DepInfo {
  llvm::BitVector argDeps;
  llvm::SmallVector<int64_t> argDepth;
  int64_t baseDepth = kUnreachable;
};

static DepInfo emptyInfo(unsigned numArgs) {
  DepInfo out;
  out.argDeps.resize(numArgs, false);
  out.argDepth.assign(numArgs, kUnreachable);
  out.baseDepth = kUnreachable;
  return out;
}

static void mergeMax(DepInfo &dst, const DepInfo &src) {
  dst.baseDepth = std::max(dst.baseDepth, src.baseDepth);
  if (dst.argDepth.size() == src.argDepth.size()) {
    for (unsigned i = 0; i < dst.argDepth.size(); ++i)
      dst.argDepth[i] = std::max(dst.argDepth[i], src.argDepth[i]);
  }
  dst.argDeps |= src.argDeps;
}

class FuncAnalyzer {
public:
  FuncAnalyzer(ModuleOp module, func::FuncOp func, CombDepGraphCache &cache)
      : module_(module), func_(func), cache_(cache), numArgs_(static_cast<unsigned>(func.getNumArguments())) {
    buildWireDrivers();
  }

  bool failed() const { return failed_; }

  DepInfo analyze(Value v) {
    if (!v)
      return emptyInfo(numArgs_);
    if (auto it = memo_.find(v); it != memo_.end())
      return it->second;
    if (!visiting_.insert(v).second) {
      // Combinational cycle (should be rejected by the comb-cycle verifier); conservatively return an unreachable
      // arg-dependent value to avoid infinite recursion.
      DepInfo out = emptyInfo(numArgs_);
      memo_.try_emplace(v, out);
      return out;
    }

    DepInfo out = emptyInfo(numArgs_);

    if (auto barg = dyn_cast<BlockArgument>(v)) {
      out = analyzeBlockArgument(barg);
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    Operation *def = v.getDefiningOp();
    if (!def) {
      // Treat unknown sources as internal base sources.
      out.baseDepth = 0;
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    auto result = dyn_cast<OpResult>(v);
    if (!result) {
      def->emitError("defining operation value is not an OpResult while "
                     "resolving combinational dependencies");
      failed_ = true;
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }
    auto transfer = resolveResultTransfer(
        def, static_cast<unsigned>(result.getResultNumber()), module_, cache_);
    if (mlir::failed(transfer)) {
      failed_ = true;
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    switch (transfer->kind) {
    case ResultTransferKind::WireDriver:
      out = analyzeWire(v);
      break;
    case ResultTransferKind::CombRegion:
      out = analyzeCombResult(cast<pyc::CombOp>(def),
                              result.getResultNumber());
      break;
    case ResultTransferKind::DirectOperands:
      out = analyzeDirectTransfer(def, *transfer);
      break;
    }
    visiting_.erase(v);
    memo_.try_emplace(v, out);
    return out;
  }

private:
  void buildWireDrivers() {
    func_.walk([&](pyc::AssignOp a) {
      Value dst = a.getDst();
      if (!dst || !dst.getDefiningOp<pyc::WireOp>())
        return;
      wireDrivers_[dst].push_back(a.getSrc());
    });
  }

  DepInfo analyzeBlockArgument(BlockArgument arg) {
    DepInfo out = emptyInfo(numArgs_);
    Operation *parent = arg.getOwner() ? arg.getOwner()->getParentOp() : nullptr;
    if (auto f = dyn_cast_or_null<func::FuncOp>(parent)) {
      if (f != func_)
        return out;
      unsigned idx = static_cast<unsigned>(arg.getArgNumber());
      if (idx >= numArgs_)
        return out;
      out.argDeps.set(idx);
      out.argDepth[idx] = 0;
      return out;
    }

    // Block arguments of pyc.comb map 1:1 to comb inputs.
    if (auto comb = dyn_cast_or_null<pyc::CombOp>(parent)) {
      unsigned idx = static_cast<unsigned>(arg.getArgNumber());
      auto inputs = comb.getInputs();
      if (idx < inputs.size())
        return analyze(inputs[idx]);
      return out;
    }

    // Unknown region arg; treat as internal base source.
    out.baseDepth = 0;
    return out;
  }

  DepInfo analyzeWire(Value wireVal) {
    DepInfo out = emptyInfo(numArgs_);
    auto it = wireDrivers_.find(wireVal);
    if (it == wireDrivers_.end() || it->second.empty()) {
      // Undriven wire; treat as internal base source.
      out.baseDepth = 0;
      return out;
    }
    for (Value src : it->second) {
      DepInfo srcInfo = analyze(src);
      mergeMax(out, srcInfo);
    }
    return out;
  }

  DepInfo analyzeCombResult(pyc::CombOp comb, unsigned resIdx) {
    DepInfo out = emptyInfo(numArgs_);
    if (comb.getBody().empty())
      return out;
    Block &b = comb.getBody().front();
    auto yield = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
    if (!yield)
      return out;
    if (resIdx >= yield.getValues().size())
      return out;
    return analyze(yield.getValues()[resIdx]);
  }

  DepInfo analyzeDirectTransfer(Operation *def,
                                const ResultTransfer &transfer) {
    DepInfo out = emptyInfo(numArgs_);
    out.baseDepth = transfer.baseDepth;
    for (const OperandDependency &dependency :
         transfer.operandDependencies) {
      if (dependency.operandIndex >= def->getNumOperands()) {
        def->emitError("canonical per-result dependency references invalid "
                       "operand index ")
            << dependency.operandIndex;
        failed_ = true;
        continue;
      }
      DepInfo input = analyze(def->getOperand(dependency.operandIndex));
      if (input.baseDepth != kUnreachable) {
        out.baseDepth = std::max(
            out.baseDepth,
            addReachableDepth(input.baseDepth, dependency.cost));
      }
      for (unsigned argIndex = 0; argIndex < numArgs_; ++argIndex) {
        if (input.argDepth[argIndex] == kUnreachable)
          continue;
        out.argDeps.set(argIndex);
        out.argDepth[argIndex] = std::max(
            out.argDepth[argIndex],
            addReachableDepth(input.argDepth[argIndex], dependency.cost));
      }
    }
    return out;
  }

private:
  ModuleOp module_;
  func::FuncOp func_;
  CombDepGraphCache &cache_;
  unsigned numArgs_ = 0;

  llvm::DenseMap<Value, llvm::SmallVector<Value>> wireDrivers_;
  llvm::DenseMap<Value, DepInfo> memo_;
  llvm::DenseSet<Value> visiting_;
  bool failed_ = false;
};

} // namespace

CombDepGraphCache::CombDepGraphCache(ModuleOp module) : module_(module) {}

const FuncCombSummary *CombDepGraphCache::getFuncSummary(func::FuncOp func) {
  if (!func)
    return nullptr;
  Operation *key = func.getOperation();
  if (auto it = cache_.find(key); it != cache_.end())
    return it->second.get();

  // Declaration stubs have no body in this compilation unit. Callers use a
  // pessimistic all-input dependence instead of a summary that nothing emits.
  if (func.isDeclaration() || func.getBody().empty())
    return nullptr;

  if (!inProgress_.insert(key).second) {
    func.emitError("recursive instance graph detected while computing comb summary");
    return nullptr;
  }

  auto summary = std::make_unique<FuncCombSummary>();
  summary->numArgs = static_cast<unsigned>(func.getNumArguments());
  summary->numResults = static_cast<unsigned>(func.getNumResults());
  summary->results.resize(summary->numResults);

  llvm::SmallVector<func::ReturnOp> returns;
  func.walk([&](func::ReturnOp r) { returns.push_back(r); });
  if (returns.size() != 1u) {
    func.emitError("expected exactly one func.return in a hardware module");
    inProgress_.erase(key);
    return nullptr;
  }
  func::ReturnOp ret = returns.front();
  if (ret.getNumOperands() != summary->numResults) {
    ret.emitError("return arity mismatch for comb summary: expected ")
        << summary->numResults << " values, got " << ret.getNumOperands();
    inProgress_.erase(key);
    return nullptr;
  }

  FuncAnalyzer analyzer(module_, func, *this);
  for (unsigned i = 0; i < summary->numResults; ++i) {
    DepInfo info = analyzer.analyze(ret.getOperand(i));
    CombResultSummary &rs = summary->results[i];
    rs.argDeps = std::move(info.argDeps);
    rs.baseDepth = info.baseDepth;
    rs.argDepth = std::move(info.argDepth);
  }
  if (analyzer.failed()) {
    inProgress_.erase(key);
    return nullptr;
  }

  const FuncCombSummary *out = summary.get();
  cache_.try_emplace(key, std::move(summary));
  inProgress_.erase(key);
  return out;
}

namespace {

static FailureOr<llvm::BitVector> traceCombBlockArguments(
    Value value, pyc::CombOp comb,
    llvm::DenseMap<Value, llvm::BitVector> &memo,
    llvm::DenseSet<Value> &visiting, ModuleOp module,
    CombDepGraphCache &cache) {
  const unsigned numInputs = static_cast<unsigned>(comb.getNumOperands());
  if (auto it = memo.find(value); it != memo.end())
    return it->second;

  llvm::BitVector deps(numInputs, false);
  if (!value || !visiting.insert(value).second)
    return deps;

  if (auto argument = dyn_cast<BlockArgument>(value)) {
    if (argument.getOwner() == &comb.getBody().front() &&
        argument.getArgNumber() < numInputs)
      deps.set(static_cast<unsigned>(argument.getArgNumber()));
  } else if (Operation *def = value.getDefiningOp()) {
    if (def->getBlock() == &comb.getBody().front()) {
      auto result = dyn_cast<OpResult>(value);
      if (!result) {
        def->emitError("comb body value is not an OpResult while tracing "
                       "canonical dependencies");
        visiting.erase(value);
        return failure();
      }
      auto transfer = resolveResultTransfer(
          def, static_cast<unsigned>(result.getResultNumber()), module, cache);
      if (failed(transfer)) {
        visiting.erase(value);
        return failure();
      }
      if (transfer->kind != ResultTransferKind::DirectOperands) {
        def->emitError("indirect dependency transfer is not supported inside "
                       "a pyc.comb body");
        visiting.erase(value);
        return failure();
      }
      for (const OperandDependency &dependency :
           transfer->operandDependencies) {
        if (dependency.operandIndex >= def->getNumOperands()) {
          def->emitError("canonical per-result dependency references invalid "
                         "operand index ")
              << dependency.operandIndex;
          visiting.erase(value);
          return failure();
        }
        auto operandDeps = traceCombBlockArguments(
            def->getOperand(dependency.operandIndex), comb, memo, visiting,
            module, cache);
        if (failed(operandDeps)) {
          visiting.erase(value);
          return failure();
        }
        deps |= *operandDeps;
      }
    }
  }

  visiting.erase(value);
  memo.try_emplace(value, deps);
  return deps;
}

} // namespace

FailureOr<std::unique_ptr<FunctionCombDepGraph>>
FunctionCombDepGraph::build(func::FuncOp func, CombDepGraphCache &cache) {
  if (!func || func.isDeclaration() || func.getBody().empty())
    return failure();
  auto graph = std::unique_ptr<FunctionCombDepGraph>(
      new FunctionCombDepGraph(func));
  if (failed(graph->construct(cache)))
    return failure();
  return std::move(graph);
}

unsigned FunctionCombDepGraph::ensureNode(Value value) {
  auto found = valueToNode_.find(value);
  if (found != valueToNode_.end())
    return found->second;

  CombDepValueNode node;
  node.value = value;
  node.producer = value ? value.getDefiningOp() : nullptr;
  if (auto result = dyn_cast<OpResult>(value))
    node.resultIndex = result.getResultNumber();
  node.stableOrdinal = static_cast<unsigned>(nodes_.size());
  unsigned id = static_cast<unsigned>(nodes_.size());
  nodes_.push_back(std::move(node));
  valueToNode_.try_emplace(value, id);
  return id;
}

void FunctionCombDepGraph::addEdge(Value source, Value target,
                                   CombDepEdgeKind kind, Operation *owner,
                                   unsigned operandIndex) {
  if (!source || !target)
    return;
  unsigned sourceId = ensureNode(source);
  unsigned targetId = ensureNode(target);
  uint64_t key = (static_cast<uint64_t>(sourceId) << 32) |
                 static_cast<uint64_t>(targetId);
  if (!edgeKeys_.insert(key).second)
    return;

  unsigned edgeId = static_cast<unsigned>(edges_.size());
  edges_.push_back(
      CombDepEdge{sourceId, targetId, kind, owner, operandIndex});
  nodes_[sourceId].outgoingEdges.push_back(edgeId);
  nodes_[targetId].incomingEdges.push_back(edgeId);
}

LogicalResult FunctionCombDepGraph::construct(CombDepGraphCache &cache) {
  // Assign stable ordinals before adding edges so DenseMap/set iteration can
  // never influence diagnostics, scheduling, or partition plans.
  for (Block &block : func_.getBody()) {
    for (BlockArgument argument : block.getArguments())
      ensureNode(argument);
    for (Operation &op : block)
      for (Value result : op.getResults())
        ensureNode(result);
  }

  ModuleOp module = func_->getParentOfType<ModuleOp>();
  for (Block &block : func_.getBody()) {
    for (Operation &op : block) {
      if (auto assign = dyn_cast<pyc::AssignOp>(op)) {
        addEdge(assign.getSrc(), assign.getDst(),
                CombDepEdgeKind::WireDriver, &op, 1u);
        continue;
      }
      if (isa<func::ReturnOp, pyc::AssertOp>(op) || op.getNumResults() == 0)
        continue;

      if (auto comb = dyn_cast<pyc::CombOp>(op)) {
        if (comb.getBody().empty() ||
            !llvm::hasSingleElement(comb.getBody())) {
          comb.emitOpError(
              "cannot build CombDepGraph for a non-single-block comb");
          return failure();
        }
        auto yield = dyn_cast_or_null<pyc::YieldOp>(
            comb.getBody().front().getTerminator());
        if (!yield || yield.getNumOperands() != comb.getNumResults()) {
          comb.emitOpError("invalid yield while building CombDepGraph");
          return failure();
        }
        llvm::DenseMap<Value, llvm::BitVector> memo;
        llvm::DenseSet<Value> visiting;
        for (auto [resultIndex, yielded] :
             llvm::enumerate(yield.getValues())) {
          auto dependencies = traceCombBlockArguments(
              yielded, comb, memo, visiting, module, cache);
          if (failed(dependencies))
            return failure();
          for (unsigned inputIndex = 0; inputIndex < dependencies->size();
               ++inputIndex) {
            if (!dependencies->test(inputIndex))
              continue;
            addEdge(comb.getInputs()[inputIndex], comb.getResult(resultIndex),
                    CombDepEdgeKind::CombBoundary, &op, inputIndex);
          }
        }
        continue;
      }

      for (auto [resultIndex, result] : llvm::enumerate(op.getResults())) {
        auto transfer = resolveResultTransfer(
            &op, static_cast<unsigned>(resultIndex), module, cache);
        if (failed(transfer))
          return failure();
        if (transfer->kind == ResultTransferKind::CombRegion) {
          op.emitError("unexpected nested comb transfer while building the "
                       "function CombDepGraph");
          return failure();
        }
        if (transfer->kind == ResultTransferKind::WireDriver)
          continue;
        for (const OperandDependency &dependency :
             transfer->operandDependencies) {
          if (dependency.operandIndex >= op.getNumOperands()) {
            op.emitError("canonical per-result dependency references invalid "
                         "operand index ")
                << dependency.operandIndex;
            return failure();
          }
          addEdge(op.getOperand(dependency.operandIndex), result,
                  transfer->edgeKind, &op, dependency.operandIndex);
        }
      }
    }
  }
  return success();
}

std::optional<unsigned>
FunctionCombDepGraph::lookupNodeId(Value value) const {
  auto found = valueToNode_.find(value);
  if (found == valueToNode_.end())
    return std::nullopt;
  return found->second;
}

FailureOr<llvm::SmallVector<unsigned>> FunctionCombDepGraph::stableTopologicalOrder(
    llvm::SmallVectorImpl<unsigned> *cycleWitness) const {
  std::vector<unsigned> indegree(nodes_.size(), 0u);
  using Ready = std::pair<unsigned, unsigned>; // stable ordinal, node id
  std::priority_queue<Ready, std::vector<Ready>, std::greater<Ready>> ready;
  for (unsigned nodeId = 0; nodeId < nodes_.size(); ++nodeId) {
    indegree[nodeId] = nodes_[nodeId].incomingEdges.size();
    if (indegree[nodeId] == 0)
      ready.emplace(nodes_[nodeId].stableOrdinal, nodeId);
  }

  llvm::SmallVector<unsigned> order;
  order.reserve(nodes_.size());
  while (!ready.empty()) {
    unsigned nodeId = ready.top().second;
    ready.pop();
    order.push_back(nodeId);

    llvm::SmallVector<unsigned> outgoing(nodes_[nodeId].outgoingEdges.begin(),
                                         nodes_[nodeId].outgoingEdges.end());
    llvm::sort(outgoing, [&](unsigned lhs, unsigned rhs) {
      return nodes_[edges_[lhs].target].stableOrdinal <
             nodes_[edges_[rhs].target].stableOrdinal;
    });
    for (unsigned edgeId : outgoing) {
      unsigned target = edges_[edgeId].target;
      if (--indegree[target] == 0)
        ready.emplace(nodes_[target].stableOrdinal, target);
    }
  }

  if (order.size() == nodes_.size())
    return order;

  if (cycleWitness) {
    enum Visit : uint8_t { Unvisited = 0, Visiting = 1, Done = 2 };
    std::vector<Visit> state(nodes_.size(), Unvisited);
    llvm::SmallVector<unsigned> stack;
    bool foundCycle = false;
    std::function<void(unsigned)> dfs = [&](unsigned nodeId) {
      if (foundCycle)
        return;
      state[nodeId] = Visiting;
      stack.push_back(nodeId);
      llvm::SmallVector<unsigned> targets;
      for (unsigned edgeId : nodes_[nodeId].outgoingEdges)
        targets.push_back(edges_[edgeId].target);
      llvm::sort(targets, [&](unsigned lhs, unsigned rhs) {
        return nodes_[lhs].stableOrdinal < nodes_[rhs].stableOrdinal;
      });
      for (unsigned target : targets) {
        if (state[target] == Unvisited) {
          dfs(target);
        } else if (state[target] == Visiting) {
          auto begin = llvm::find(stack, target);
          cycleWitness->append(begin, stack.end());
          cycleWitness->push_back(target);
          foundCycle = true;
        }
        if (foundCycle)
          break;
      }
      stack.pop_back();
      state[nodeId] = Done;
    };
    for (unsigned nodeId = 0; nodeId < nodes_.size() && !foundCycle;
         ++nodeId)
      if (state[nodeId] == Unvisited)
        dfs(nodeId);
  }
  return failure();
}

} // namespace pyc
