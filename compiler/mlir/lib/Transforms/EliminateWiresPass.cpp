#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Dominance.h"
#include "mlir/IR/Operation.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"

#include <queue>

using namespace mlir;

namespace pyc {
namespace {

static bool dependsOn(Value root, Value target) {
  if (!root || !target)
    return false;

  llvm::SmallVector<Value> worklist;
  worklist.push_back(root);

  llvm::DenseSet<Value> seen;
  seen.reserve(64);

  while (!worklist.empty()) {
    Value v = worklist.pop_back_val();
    if (v == target)
      return true;
    if (!seen.insert(v).second)
      continue;
    Operation *def = v.getDefiningOp();
    if (!def)
      continue;
    for (Value opnd : def->getOperands())
      worklist.push_back(opnd);
  }

  return false;
}

// Ops whose wire inputs are not read combinationally within eval_comb_pass.
static bool isWireReadCut(Operation *op) {
  return isa<pyc::AssignOp, pyc::RegOp, pyc::DelayLineOp, pyc::SyncMemOp,
             pyc::SyncMemDPOp, pyc::CdcSyncOp>(op);
}

// Stable topological sort in which a combinational reader of a wire also
// follows the wire's driver, so block order is a valid evaluation order and
// any contiguous run of ops can be fused without a comb-level cycle.  A true
// combinational loop keeps the original order for pyc-check-comb-cycles.
static bool trySortByWireDrivers(Block &block) {
  llvm::SmallVector<Operation *> ops;
  llvm::DenseMap<Operation *, unsigned> index;
  for (Operation &op : block.without_terminator()) {
    index[&op] = ops.size();
    ops.push_back(&op);
  }
  llvm::SmallVector<llvm::SmallVector<unsigned>> succ(ops.size());
  llvm::SmallVector<unsigned> indeg(ops.size(), 0);
  auto addEdge = [&](Value v, unsigned to) {
    Operation *def = v.getDefiningOp();
    if (!def || !(def = block.findAncestorOpInBlock(*def)))
      return;
    auto it = index.find(def);
    if (it == index.end() || it->second == to)
      return;
    succ[it->second].push_back(to);
    ++indeg[to];
  };
  for (auto [i, op] : llvm::enumerate(ops)) {
    bool cut = isWireReadCut(op);
    op->walk([&, i = i](Operation *inner) {
      for (Value v : inner->getOperands()) {
        addEdge(v, i);
        if (cut || !v.getDefiningOp<pyc::WireOp>())
          continue;
        for (OpOperand &use : v.getUses())
          if (auto a = dyn_cast<pyc::AssignOp>(use.getOwner()))
            if (use.getOperandNumber() == 0)
              addEdge(a.getSrc(), i);
      }
    });
  }

  std::priority_queue<unsigned, std::vector<unsigned>, std::greater<unsigned>> ready;
  for (unsigned i = 0; i < ops.size(); ++i)
    if (indeg[i] == 0)
      ready.push(i);
  llvm::SmallVector<Operation *> order;
  while (!ready.empty()) {
    unsigned n = ready.top();
    ready.pop();
    order.push_back(ops[n]);
    for (unsigned s : succ[n])
      if (--indeg[s] == 0)
        ready.push(s);
  }
  if (order.size() != ops.size())
    return false;
  for (Operation *op : order)
    op->moveBefore(block.getTerminator());
  return true;
}

// A state or primitive op reading combinational logic directly is pinned by
// SSA between that logic and its own fanout; reading through a wire unpins it.
static void wireCutInputs(Block &block) {
  OpBuilder builder(block.getParentOp()->getContext());
  for (Operation &op : block.without_terminator()) {
    if (!isWireReadCut(&op) || isa<pyc::AssignOp>(op))
      continue;
    for (OpOperand &use : op.getOpOperands()) {
      Value v = use.get();
      Operation *def = v.getDefiningOp();
      if (!def || def->getBlock() != &block || !isa<IntegerType, VectorType>(v.getType()) ||
          isWireReadCut(def) || isa<pyc::WireOp, pyc::ConstantOp>(def))
        continue;
      builder.setInsertionPointToStart(&block);
      auto w = builder.create<pyc::WireOp>(op.getLoc(), v.getType());
      builder.setInsertionPoint(block.getTerminator());
      builder.create<pyc::AssignOp>(op.getLoc(), w, v);
      use.set(w);
    }
  }
}

static void sortByWireDrivers(Block &block) {
  if (!trySortByWireDrivers(block)) {
    wireCutInputs(block);
    trySortByWireDrivers(block);
  }
}

struct EliminateWiresPass : public PassWrapper<EliminateWiresPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(EliminateWiresPass)

  StringRef getArgument() const override { return "pyc-eliminate-wires"; }
  StringRef getDescription() const override {
    return "Eliminate trivial pyc.wire + pyc.assign pairs (netlist cleanup)";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();
    for (Block &block : f.getBody())
      sortByWireDrivers(block);
    DominanceInfo dom(f);

    OpBuilder builder(f.getContext());
    bool changed = true;
    while (changed) {
      changed = false;
      // Collect wires up-front since we may erase ops during the walk.
      llvm::SmallVector<pyc::WireOp> wires;
      f.walk([&](pyc::WireOp w) { wires.push_back(w); });
      for (pyc::WireOp w : wires) {
        FailureOr<bool> eliminated = tryEliminateWire(w, dom, builder);
        if (failed(eliminated))
          return signalPassFailure();
        changed |= *eliminated;
      }
    }

    // Continuous assignment is position-independent, so declare every wire
    // first and drive it last; wires then never split a fusable comb run.
    for (Block &block : f.getBody()) {
      llvm::SmallVector<Operation *> wires, assigns;
      for (Operation &op : block) {
        if (isa<pyc::WireOp>(op))
          wires.push_back(&op);
        else if (isa<pyc::AssignOp>(op))
          assigns.push_back(&op);
      }
      for (Operation *op : llvm::reverse(wires))
        op->moveBefore(&block.front());
      for (Operation *op : assigns)
        op->moveBefore(block.getTerminator());
    }
  }

  FailureOr<bool> tryEliminateWire(pyc::WireOp w, DominanceInfo &dom,
                                   OpBuilder &builder) {
    Value wireVal = w.getResult();

    llvm::SmallVector<pyc::AssignOp> assigns;
    llvm::SmallVector<OpOperand *> reads;

    for (OpOperand &use : wireVal.getUses()) {
      if (auto a = dyn_cast<pyc::AssignOp>(use.getOwner())) {
        if (use.getOperandNumber() == 0) {
          assigns.push_back(a);
          continue;
        }
      }
      reads.push_back(&use);
    }

    // If the wire is never read, drop it and all of its drivers.
    if (reads.empty()) {
      for (pyc::AssignOp a : assigns)
        a.erase();
      w.erase();
      return true;
    }

    if (assigns.size() > 1) {
      w.emitError("pyc.wire has ")
          << assigns.size()
          << " pyc.assign drivers; a wire must have a single driver";
      return failure();
    }
    if (assigns.empty())
      return false;

    pyc::AssignOp driver = assigns.front();
    Value src = driver.getSrc();

    // Avoid creating self-referential definitions.
    if (src == wireVal)
      return false;
    if (dependsOn(src, wireVal))
      return false;

    // Readers after the driver take its source directly; only the readers
    // before it keep the wire.
    auto early = llvm::partition(reads, [&](OpOperand *use) {
      return dom.dominates(src, use->getOwner());
    });
    if (early != reads.end()) {
      for (OpOperand *use : llvm::make_range(reads.begin(), early))
        use->set(src);
      return early != reads.begin();
    }

    Value replacement = src;
    if (auto nameAttr = w->getAttrOfType<StringAttr>("pyc.name")) {
      if (Operation *def = src.getDefiningOp())
        builder.setInsertionPointAfter(def);
      else
        builder.setInsertionPointToStart(src.getParentBlock());
      auto alias = builder.create<pyc::AliasOp>(w.getLoc(), src.getType(), src);
      // Preserve ownership/debug attributes when a named wire becomes an
      // alias; the delay-chain pass relies on pyc.generated surviving here.
      alias->setAttrs(w->getAttrs());
      replacement = alias.getResult();
    }

    for (OpOperand *use : reads)
      use->set(replacement);

    driver.erase();
    w.erase();
    return true;
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createEliminateWiresPass() { return std::make_unique<EliminateWiresPass>(); }

static PassRegistration<EliminateWiresPass> pass;

} // namespace pyc
