#include "pyc/Transforms/CombDepGraph.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/IR/SymbolTable.h"
#include "llvm/ADT/SmallVector.h"

using namespace mlir;

namespace pyc {
namespace {

static constexpr int64_t kUnreachable = -1;

static bool isSequentialCut(Operation *op) {
  return isa<pyc::RegOp, pyc::FifoOp, pyc::ByteMemOp, pyc::SyncMemOp, pyc::SyncMemDPOp, pyc::AsyncFifoOp, pyc::CdcSyncOp>(op);
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
  int64_t dim = op.getDim().value_or(0);
  if (dim < 0 || dim >= vecTy.getRank())
    return 1;
  return std::max<int64_t>(1, ceilLog2(vecTy.getDimSize(dim)));
}

static int64_t opCost(Operation *op) {
  if (!op)
    return 0;
  if (isSequentialCut(op))
    return 0;
  if (isa<pyc::WireOp, pyc::AliasOp, pyc::ResetActiveOp, pyc::ConstantOp, pyc::CombOp, pyc::YieldOp,
          arith::ConstantOp>(op))
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

    if (isSequentialCut(def) || isa<pyc::InstanceOp>(def)) {
      // Sequential/stateful results are cut points.
      //
      // InstanceOp is NOT a cut point, but it is handled below.
      if (!isa<pyc::InstanceOp>(def)) {
        out.baseDepth = 0;
        visiting_.erase(v);
        memo_.try_emplace(v, out);
        return out;
      }
    }

    if (isa<arith::ConstantOp, pyc::ConstantOp>(def)) {
      out.baseDepth = 0;
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    if (auto w = dyn_cast<pyc::WireOp>(def)) {
      (void)w;
      out = analyzeWire(v);
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    if (auto a = dyn_cast<pyc::AliasOp>(def)) {
      out = analyze(a.getIn());
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    if (auto ra = dyn_cast<pyc::ResetActiveOp>(def)) {
      out = analyze(ra.getRst());
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    if (auto comb = dyn_cast<pyc::CombOp>(def)) {
      auto r = dyn_cast<OpResult>(v);
      unsigned resIdx = r ? r.getResultNumber() : 0u;
      out = analyzeCombResult(comb, resIdx);
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    if (auto inst = dyn_cast<pyc::InstanceOp>(def)) {
      auto r = dyn_cast<OpResult>(v);
      unsigned resIdx = r ? r.getResultNumber() : 0u;
      out = analyzeInstanceResult(inst, resIdx);
      visiting_.erase(v);
      memo_.try_emplace(v, out);
      return out;
    }

    out = analyzeGeneric(def);
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

  DepInfo analyzeInstanceResult(pyc::InstanceOp inst, unsigned resIdx) {
    DepInfo out = emptyInfo(numArgs_);

    auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
    if (!calleeAttr) {
      inst.emitError("pyc.instance missing required `callee` attr");
      return out;
    }

    auto sym = SymbolTable::lookupSymbolIn(module_, calleeAttr.getValue());
    auto callee = dyn_cast_or_null<func::FuncOp>(sym);
    if (!callee) {
      inst.emitError("pyc.instance callee is not a func.func symbol: ") << calleeAttr.getValue();
      return out;
    }

    const FuncCombSummary *sum = cache_.getFuncSummary(callee);
    if (!sum) {
      inst.emitError("failed to compute callee comb summary for ") << calleeAttr.getValue();
      return out;
    }
    if (resIdx >= sum->results.size())
      return out;

    const CombResultSummary &rs = sum->results[resIdx];
    out.baseDepth = std::max(out.baseDepth, rs.baseDepth);

    auto inputs = inst.getInputs();
    unsigned n = std::min<unsigned>(static_cast<unsigned>(inputs.size()), static_cast<unsigned>(rs.argDepth.size()));
    for (unsigned i = 0; i < n; ++i) {
      int64_t delta = rs.argDepth[i];
      if (delta == kUnreachable)
        continue;

      DepInfo inInfo = analyze(inputs[i]);

      if (inInfo.baseDepth != kUnreachable)
        out.baseDepth = std::max(out.baseDepth, inInfo.baseDepth + delta);

      for (unsigned a = 0; a < numArgs_; ++a) {
        if (inInfo.argDepth[a] == kUnreachable)
          continue;
        out.argDeps.set(a);
        out.argDepth[a] = std::max(out.argDepth[a], inInfo.argDepth[a] + delta);
      }
    }

    return out;
  }

  DepInfo analyzeGeneric(Operation *def) {
    DepInfo out = emptyInfo(numArgs_);
    int64_t cost = opCost(def);

    int64_t bestBase = kUnreachable;
    llvm::SmallVector<int64_t> bestArg(numArgs_, kUnreachable);
    llvm::BitVector argDeps(numArgs_, false);

    for (Value opnd : def->getOperands()) {
      DepInfo in = analyze(opnd);
      bestBase = std::max(bestBase, in.baseDepth);
      for (unsigned i = 0; i < numArgs_; ++i)
        bestArg[i] = std::max(bestArg[i], in.argDepth[i]);
      argDeps |= in.argDeps;
    }

    if (bestBase != kUnreachable)
      out.baseDepth = bestBase + cost;
    out.argDeps = std::move(argDeps);
    out.argDepth = std::move(bestArg);
    for (unsigned i = 0; i < numArgs_; ++i) {
      if (out.argDepth[i] != kUnreachable)
        out.argDepth[i] += cost;
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
};

} // namespace

CombDepGraphCache::CombDepGraphCache(ModuleOp module) : module_(module) {}

const FuncCombSummary *CombDepGraphCache::getFuncSummary(func::FuncOp func) {
  if (!func)
    return nullptr;
  Operation *key = func.getOperation();
  if (auto it = cache_.find(key); it != cache_.end())
    return it->second.get();

  // Multi-.pyc build mode emits declaration-only dependency stubs. In that case
  // the callee body is not available in this compilation unit, so we cannot
  // compute a precise summary here.
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

  const FuncCombSummary *out = summary.get();
  cache_.try_emplace(key, std::move(summary));
  inProgress_.erase(key);
  return out;
}

} // namespace pyc
