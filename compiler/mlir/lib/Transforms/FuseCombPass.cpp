#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/StateOptimization.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/IRMapping.h"
#include "mlir/Pass/Pass.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SetVector.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/STLExtras.h"

#include <map>
#include <numeric>

using namespace mlir;

namespace pyc {
namespace {

static bool isFusableCombKind(Operation *op) {
  return isa<pyc::ConstantOp,
             pyc::AddOp,
             pyc::SubOp,
             pyc::MulOp,
             pyc::UdivOp,
             pyc::UremOp,
             pyc::SdivOp,
             pyc::SremOp,
             pyc::MuxOp,
             pyc::AndOp,
             pyc::OrOp,
             pyc::XorOp,
             pyc::NotOp,
             pyc::ConcatOp,
             pyc::AliasOp,
             pyc::ResetActiveOp,
             pyc::EqOp,
             pyc::UltOp,
             pyc::SltOp,
             pyc::TruncOp,
             pyc::ZextOp,
             pyc::SextOp,
             pyc::ExtractOp,
             pyc::ShliOp,
             pyc::LshriOp,
             pyc::AshriOp,
             pyc::ShlOp,
             pyc::LshrOp,
             pyc::AshrOp,
             pyc::VGetOp,
             pyc::VCreateOp,
             pyc::VBroadcastOp,
             pyc::VBroadcastDimOp,
             pyc::VOrReduceOp,
             pyc::VAndReduceOp,
             pyc::VAddReduceOp,
             arith::SelectOp>(op);
}

static bool isFusableCombOp(Operation *op) {
  if (shouldKeepStateOptimization(op) || hasStableStateName(op))
    return false;
  return isFusableCombKind(op);
}

// Observation attributes keep a plain combinational op out of a fusable run.
// Move them onto a leaf alias at the end of the block: the computation then
// fuses, and probes read the same value through the alias.  An alias only
// forwards its source, so its readers take the source directly and the alias
// itself becomes that leaf, or goes away when it carries no attributes.
static void detachObservationAttrs(Block &block) {
  OpBuilder builder(block.getTerminator());
  llvm::SmallVector<Operation *> pinned;
  for (Operation &op : block.without_terminator())
    if (isa<pyc::AliasOp>(op) || (op.getNumResults() == 1 &&
                                  !isFusableCombOp(&op) &&
                                  isFusableCombKind(&op)))
      pinned.push_back(&op);
  for (Operation *op : pinned) {
    if (auto alias = dyn_cast<pyc::AliasOp>(op)) {
      alias.getResult().replaceAllUsesWith(alias.getIn());
      if (alias->getAttrs().empty())
        alias->erase();
      else
        alias->moveBefore(block.getTerminator());
      continue;
    }
    pyc::AliasOp alias = materializeObservationAlias(
        builder, op->getLoc(), op->getResult(0), op);
    for (NamedAttribute attr : alias->getAttrs())
      op->removeAttr(attr.getName());
  }
}

static bool isSourceOp(Operation *op) {
  return isa<pyc::RegOp, pyc::DelayLineOp, pyc::DelayTapOp, pyc::SyncMemOp,
             pyc::SyncMemDPOp, pyc::CdcSyncOp>(op);
}

// State outputs are fixed during one eval, so state ops never need to sit
// between combinational ops.  Lay the block out as constants, wires, then
// state ops reading combinational values through new wires, keeping every
// combinational run contiguous.
static void hoistSources(Block &block) {
  llvm::SmallVector<Operation *> consts, wires, sources;
  llvm::DenseSet<Operation *> head;
  // An operand left in place must be wirable and must not be state itself
  // (delay_tap requires its delay_line directly).
  auto hoistable = [&](Operation &op) {
    return isSourceOp(&op) && llvm::none_of(op.getOperands(), [&](Value v) {
             Operation *def = v.getDefiningOp();
             return def && !head.count(def) &&
                    (isSourceOp(def) || !isa<IntegerType, VectorType>(v.getType()));
           });
  };
  for (Operation &op : block.without_terminator()) {
    if (isa<pyc::ConstantOp>(op))
      consts.push_back(&op);
    else if (isa<pyc::WireOp>(op))
      wires.push_back(&op);
    else if (hoistable(op))
      sources.push_back(&op);
    else
      continue;
    head.insert(&op);
  }
  OpBuilder builder(block.getTerminator());
  llvm::DenseMap<Value, Value> wired;
  for (Operation *op : sources)
    for (OpOperand &use : op->getOpOperands()) {
      Value v = use.get();
      Operation *def = v.getDefiningOp();
      if (!def || head.count(def))
        continue;
      Value &w = wired[v];
      if (!w) {
        w = builder.create<pyc::WireOp>(op->getLoc(), v.getType());
        builder.create<pyc::AssignOp>(op->getLoc(), w, v);
        wires.push_back(w.getDefiningOp());
      }
      use.set(w);
    }
  Operation *prev = nullptr;
  for (Operation *op : llvm::concat<Operation *>(consts, wires, sources)) {
    if (prev)
      op->moveAfter(prev);
    else
      op->moveBefore(&block.front());
    prev = op;
  }
}

// Ports and delay taps are polled on every pass by each comb reading them.
// Read them through one comb instead, so the other combs wake on dirty bits.
static void latchPolledInputs(ArrayRef<Operation *> run) {
  llvm::SetVector<Value> polled;
  for (Operation *op : run)
    for (Value v : op->getOperands())
      if ((!v.getDefiningOp() || v.getDefiningOp<pyc::DelayTapOp>()) &&
          isa<IntegerType, VectorType>(v.getType()))
        polled.insert(v);
  if (polled.empty())
    return;
  OpBuilder builder(run.front());
  auto latch = builder.create<pyc::CombOp>(
      run.front()->getLoc(), ValueRange(polled.getArrayRef()).getTypes(), polled.getArrayRef());
  Block *body = new Block();
  latch.getBody().push_back(body);
  for (Value v : polled)
    body->addArgument(v.getType(), latch.getLoc());
  OpBuilder::atBlockEnd(body).create<pyc::YieldOp>(latch.getLoc(), body->getArguments());
  llvm::DenseSet<Operation *> inRun(run.begin(), run.end());
  for (auto [i, v] : llvm::enumerate(polled))
    Value(v).replaceUsesWithIf(latch.getResult(i),
                               [&](OpOperand &use) { return inRun.contains(use.getOwner()); });
}

// Split a combinational run into groups that wake on their own inputs, and
// lay them out before `end`, adding each group's first op to cuts.  Ops are
// grouped by the set of run inputs they read through any path.  An operand
// reads a subset of what its user reads, so ordering groups by set size and
// then id is topological.  A group whose users, or whose operands, all lie
// in one other group then joins it under that group's key, which keeps the
// order topological; visiting groups from the far side keeps each one's
// edges complete at its turn.
static void splitRun(ArrayRef<Operation *> run, Operation *end,
                     llvm::DenseSet<Operation *> &cuts) {
  if (run.size() < 2)
    return;
  latchPolledInputs(run);
  std::map<std::vector<unsigned>, unsigned> sigIds;
  std::vector<const std::vector<unsigned> *> sigs;
  llvm::DenseMap<Value, unsigned> inputs;
  llvm::DenseMap<Operation *, unsigned> group;
  for (Operation *op : run) {
    std::vector<unsigned> sig, one, merged;
    for (Value v : op->getOperands()) {
      Operation *def = v.getDefiningOp();
      if (def && isa<pyc::ConstantOp>(def))
        continue;
      auto it = group.find(def);
      if (it == group.end())
        one = {inputs.try_emplace(v, inputs.size()).first->second};
      const std::vector<unsigned> &add = it == group.end() ? one : *sigs[it->second];
      merged.clear();
      std::set_union(sig.begin(), sig.end(), add.begin(), add.end(),
                     std::back_inserter(merged));
      sig.swap(merged);
    }
    auto [it, fresh] = sigIds.try_emplace(std::move(sig), sigs.size());
    if (fresh)
      sigs.push_back(&it->first);
    group[op] = it->second;
  }

  unsigned n = sigs.size();
  std::vector<unsigned> parent(n);
  std::iota(parent.begin(), parent.end(), 0);
  llvm::SmallVector<std::pair<unsigned, unsigned>> edges;
  for (Operation *op : run)
    for (Value v : op->getOperands())
      if (auto it = group.find(v.getDefiningOp()); it != group.end() && it->second != group[op])
        edges.emplace_back(it->second, group[op]);
  auto find = [&](unsigned g) {
    while (parent[g] != g)
      g = parent[g] = parent[parent[g]];
    return g;
  };
  auto key = [&](unsigned g) { return std::make_pair(sigs[g]->size(), g); };
  auto joinSole = [&](bool toUser) {
    std::vector<llvm::SmallVector<unsigned, 2>> next(n);
    for (auto [from, to] : edges)
      if (unsigned a = find(from), b = find(to); a != b)
        toUser ? next[a].push_back(b) : next[b].push_back(a);
    llvm::SmallVector<unsigned> roots;
    for (unsigned g = 0; g < n; ++g)
      if (find(g) == g)
        roots.push_back(g);
    llvm::sort(roots, [&](unsigned a, unsigned b) { return (key(a) < key(b)) != toUser; });
    for (unsigned g : roots) {
      if (next[g].empty())
        continue;
      unsigned to = find(next[g].front());
      if (llvm::all_of(next[g], [&](unsigned u) { return find(u) == to; }))
        parent[g] = to;
    }
  };
  joinSole(true);
  joinSole(false);

  llvm::SmallVector<std::pair<std::pair<size_t, unsigned>, unsigned>> order;
  for (auto [i, op] : llvm::enumerate(run))
    order.emplace_back(key(find(group[op])), i);
  llvm::sort(order);
  for (auto [i, entry] : llvm::enumerate(order)) {
    Operation *op = run[entry.second];
    op->moveBefore(end);
    if (i == 0 || order[i - 1].first != entry.first)
      cuts.insert(op);
  }
}

static llvm::DenseSet<Operation *> splitRuns(Block &block) {
  llvm::DenseSet<Operation *> cuts;
  llvm::SmallVector<Operation *> run;
  for (Operation &op : llvm::make_early_inc_range(block)) {
    if (isFusableCombOp(&op)) {
      run.push_back(&op);
      continue;
    }
    splitRun(run, &op, cuts);
    run.clear();
  }
  return cuts;
}

struct FuseCombPass : public PassWrapper<FuseCombPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(FuseCombPass)

  StringRef getArgument() const override { return "pyc-fuse-comb"; }
  StringRef getDescription() const override {
    return "Fuse consecutive pyc combinational ops into codegen-friendly pyc.comb regions";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();
    if (auto structural = f->getAttrOfType<StringAttr>("pyc.emit.structural")) {
      llvm::StringRef v = structural.getValue();
      if (v.equals_insensitive("true") || v == "1")
        return;
    }
    for (Block &b : f.getBody()) {
      detachObservationAttrs(b);
      hoistSources(b);
      fuseBlock(b, splitRuns(b));
    }
  }

  void fuseBlock(Block &block, const llvm::DenseSet<Operation *> &cuts) {
    llvm::SmallVector<Operation *> run;
    llvm::DenseSet<Operation *> inRun;

    auto flushRun = [&]() {
      if (!llvm::all_of(run, [](Operation *op) { return isa<pyc::ConstantOp>(op); }))
        fuseRun(run);
      run.clear();
      inRun.clear();
    };

    // A comb body computes into locals and publishes at the end, so the wire
    // member it reads still holds the pre-pass value.  An op reading a wire
    // that an earlier op of the run drives must start a new run, which does
    // publish that value first.
    auto readsWireDrivenInRun = [&](Operation *op) {
      for (Value v : op->getOperands()) {
        if (!v.getDefiningOp<pyc::WireOp>())
          continue;
        for (OpOperand &use : v.getUses()) {
          auto assign = dyn_cast<pyc::AssignOp>(use.getOwner());
          if (assign && use.getOperandNumber() == 0 &&
              inRun.contains(assign.getSrc().getDefiningOp()))
            return true;
        }
      }
      return false;
    };

    for (Operation &op : llvm::make_early_inc_range(block)) {
      if (isFusableCombOp(&op)) {
        if (cuts.contains(&op) || readsWireDrivenInRun(&op))
          flushRun();
        run.push_back(&op);
        inRun.insert(&op);
        continue;
      }
      flushRun();
    }
    flushRun();
  }

  void fuseRun(ArrayRef<Operation *> run) {
    llvm::DenseSet<Operation *> runSet;
    runSet.reserve(run.size());
    for (Operation *op : run)
      runSet.insert(op);

    // Live-outs: results that are used by an op outside the run.
    llvm::SmallVector<Value> outputs;
    llvm::DenseSet<Value> outputSet;
    for (Operation *op : run) {
      for (Value r : op->getResults()) {
        bool usedOutside = false;
        for (OpOperand &use : r.getUses()) {
          if (!runSet.contains(use.getOwner())) {
            usedOutside = true;
            break;
          }
        }
        if (usedOutside && outputSet.insert(r).second)
          outputs.push_back(r);
      }
    }

    // Promote any pyc.lazy_probe_wires host in the run to a live-out so
    // its wired result survives as a comb output. Observation demand
    // hangs the attribute on the alias-stripped producer (see
    // findWireHost in ApplyObservationDemandPass), so a named alias that
    // is the live-out, or a mid-pipeline named op consumed only by other
    // fused ops, would otherwise be cloned into the pyc.comb body and
    // become invisible to the emitter (which skips ops inside pyc.comb).
    for (Operation *op : run) {
      auto wires = op->getAttrOfType<ArrayAttr>("pyc.lazy_probe_wires");
      if (!wires)
        continue;
      for (Attribute item : wires) {
        auto dict = dyn_cast<DictionaryAttr>(item);
        if (!dict)
          continue;
        auto resultAttr = dict.getAs<IntegerAttr>("result");
        const unsigned srcResult =
            resultAttr ? static_cast<unsigned>(resultAttr.getInt()) : 0;
        if (srcResult >= op->getNumResults())
          continue;
        Value r = op->getResult(srcResult);
        if (outputSet.insert(r).second)
          outputs.push_back(r);
      }
    }

    if (outputs.empty())
      return;

    // External inputs: operands that are not defined by an op in the run.
    // Plain constants are cloned in instead, so the body keeps literals.
    llvm::SmallVector<Value> inputs;
    llvm::DenseSet<Value> seenInputs;
    llvm::SetVector<Operation *> consts;
    for (Operation *op : run) {
      for (Value v : op->getOperands()) {
        if (Operation *def = v.getDefiningOp()) {
          if (runSet.contains(def))
            continue;
          if (isa<pyc::ConstantOp>(def) && isFusableCombOp(def)) {
            consts.insert(def);
            continue;
          }
        }
        if (seenInputs.insert(v).second)
          inputs.push_back(v);
      }
    }

    llvm::SmallVector<Type> outTypes;
    outTypes.reserve(outputs.size());
    for (Value v : outputs)
      outTypes.push_back(v.getType());

    OpBuilder builder(run.front());
    auto comb = builder.create<pyc::CombOp>(run.front()->getLoc(), outTypes, inputs);

    // Build a single-block region and clone ops into it.
    Region &region = comb.getBody();
    Block *body = new Block();
    region.push_back(body);
    for (Value in : inputs)
      body->addArgument(in.getType(), comb.getLoc());

    IRMapping mapping;
    for (auto [i, in] : llvm::enumerate(inputs))
      mapping.map(in, body->getArgument(i));

    builder.setInsertionPointToStart(body);
    for (Operation *op : llvm::concat<Operation *const>(consts.getArrayRef(), run)) {
      Operation *cloned = builder.clone(*op, mapping);
      for (auto [oldRes, newRes] : llvm::zip(op->getResults(), cloned->getResults()))
        mapping.map(oldRes, newRes);
    }

    llvm::SmallVector<Value> yieldVals;
    yieldVals.reserve(outputs.size());
    for (Value out : outputs)
      yieldVals.push_back(mapping.lookup(out));
    builder.create<pyc::YieldOp>(comb.getLoc(), yieldVals);

    // Keep undeclared combinational names lookupable after fusion: they
    // register as addWire of this comb result, not a second named copy.
    SmallVector<Attribute> lazyWires;
    auto copyLazyWires = [&](Value out, int64_t combResult) {
      Operation *def = out.getDefiningOp();
      if (!def)
        return;
      auto wires = def->getAttrOfType<ArrayAttr>("pyc.lazy_probe_wires");
      if (!wires)
        return;
      unsigned outResult = 0;
      if (auto res = dyn_cast<OpResult>(out))
        outResult = res.getResultNumber();
      for (Attribute item : wires) {
        auto dict = dyn_cast<DictionaryAttr>(item);
        if (!dict)
          continue;
        auto resultAttr = dict.getAs<IntegerAttr>("result");
        const unsigned srcResult =
            resultAttr ? static_cast<unsigned>(resultAttr.getInt()) : 0;
        if (srcResult != outResult)
          continue;
        NamedAttrList fields;
        if (auto name = dict.getAs<StringAttr>("name"))
          fields.set("name", name);
        if (auto width = dict.getAs<IntegerAttr>("width"))
          fields.set("width", width);
        fields.set("result", builder.getI64IntegerAttr(combResult));
        lazyWires.push_back(DictionaryAttr::get(def->getContext(), fields));
      }
    };
    for (auto [i, out] : llvm::enumerate(outputs))
      copyLazyWires(out, static_cast<int64_t>(i));
    if (!lazyWires.empty())
      comb->setAttr("pyc.lazy_probe_wires",
                    ArrayAttr::get(comb.getContext(), lazyWires));

    // Replace uses outside the run with comb results.
    for (auto [out, res] : llvm::zip(outputs, comb.getResults())) {
      out.replaceUsesWithIf(res, [&](OpOperand &use) { return !runSet.contains(use.getOwner()); });
    }

    for (Operation *op : llvm::reverse(run))
      op->erase();
  }
};

} // namespace

std::unique_ptr<mlir::Pass> createFuseCombPass() { return std::make_unique<FuseCombPass>(); }

static PassRegistration<FuseCombPass> pass;

} // namespace pyc
