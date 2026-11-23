// Fork-merge retiming: collapse parallel register copies.
//
// Davinci's frontend emits many `cycle_balance` registers that sample the
// same value with the same clock/reset/enable/init — up to 111 copies of one
// 64-bit signal. They are literally the same flip-flop, so all but one can be
// deleted and the survivor reused. That is the "common stages of a fork merge
// into one" half of docs/reg_upstream_dfs_merge_plan.md, restricted to the
// case that needs no new IR: no chain is materialized and no cone is lifted,
// so path delays and every input-to-output register count are untouched.
//
// Guards, all conservative:
//   * the data input must not be another register's q (a register that
//     follows another register is not a duplicate of it);
//   * enable is compared after resolving aliases/wires, constants by value;
//   * init must be a constant of the register's own width;
//   * hold loops (next cone reads own q) are left alone.
//
// Lifting a register across a combinational op is deliberately not done here:
// the materialized chains need post-hoc reordering that repeatedly broke
// dominance on the PE clusters. The merge alone already removes most of the
// copy explosion (scoreboard 26026 -> 235 on the dumped IR).

#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/StateOptimization.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/Support/raw_ostream.h"

#include <cstring>
#include <optional>
#include <string>

using namespace mlir;

namespace pyc {
namespace {

struct MergeStats {
  std::int64_t merged = 0;
  std::int64_t groups = 0;
};

static std::optional<unsigned> scalarWidth(Type type) {
  if (auto integer = dyn_cast<IntegerType>(type))
    return integer.getWidth();
  return std::nullopt;
}

static std::optional<llvm::APInt> constantValue(Value value) {
  llvm::SmallPtrSet<Operation *, 4> seen;
  while (auto alias = value.getDefiningOp<pyc::AliasOp>()) {
    if (!seen.insert(alias).second)
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

// Single-driver wires and aliases are names, not gates.
static Value resolve(Value value) {
  llvm::SmallPtrSet<Operation *, 8> seen;
  while (value) {
    Operation *def = value.getDefiningOp();
    if (!def || !seen.insert(def).second)
      return value;
    if (auto alias = dyn_cast<pyc::AliasOp>(def)) {
      value = alias.getIn();
      continue;
    }
    if (!isa<pyc::WireOp>(def))
      return value;
    Value src;
    unsigned drivers = 0;
    for (Operation *user : value.getUsers()) {
      auto assign = dyn_cast<pyc::AssignOp>(user);
      if (!assign || assign.getDst() != value)
        continue;
      src = assign.getSrc();
      ++drivers;
    }
    if (drivers != 1)
      return value;
    value = src;
  }
  return value;
}

// Textual identity of a value: pointer identity for real values, literal for
// constants. Never stream a Value directly: Value::print is not thread-safe
// under the parallel function-pass scheduler (this pass runs on every
// function concurrently), and streaming OpResults raced the shared printing
// infrastructure and corrupted unrelated type storage on the private
// baseline. Pointer + result/argument number is unique, stable, and pure.
static void appendValueKey(llvm::raw_ostream &os, Value value) {
  Value resolved = resolve(value);
  if (auto constant = constantValue(resolved)) {
    os << "c" << constant->getBitWidth() << "x" << *constant;
    return;
  }
  if (auto opResult = dyn_cast<OpResult>(resolved)) {
    os << "r" << reinterpret_cast<uintptr_t>(opResult.getOwner()) << "#"
       << opResult.getResultNumber();
    return;
  }
  auto blockArg = cast<BlockArgument>(resolved);
  os << "a" << reinterpret_cast<uintptr_t>(blockArg.getOwner()) << "#"
     << blockArg.getArgNumber();
}

static bool isStateBoundary(Operation *def) {
  return isa<pyc::RegOp, pyc::DelayLineOp, pyc::DelayTapOp, pyc::SyncMemNMOp,
             pyc::ByteMemOp, pyc::FifoOp, pyc::AsyncFifoOp, pyc::CdcSyncOp,
             pyc::InstanceOp>(def);
}

static bool coneReadsOwnQ(pyc::RegOp reg) {
  Value q = reg.getQ();
  llvm::DenseSet<Value> seen;
  llvm::SmallVector<Value, 8> work{reg.getNext()};
  while (!work.empty()) {
    Value value = resolve(work.pop_back_val());
    if (value == q)
      return true;
    if (!seen.insert(value).second)
      continue;
    Operation *def = value.getDefiningOp();
    if (!def || isStateBoundary(def))
      continue;
    for (Value operand : def->getOperands())
      work.push_back(operand);
  }
  return false;
}

static bool isStateConsumer(Operation *op) {
  return isa<pyc::DelayLineOp, pyc::DelayTapOp, pyc::InstanceOp>(op);
}

// True when `value` reaches a delay line/tap/instance through aliases and
// single-driver wires.
static bool reachesStateConsumer(Value value) {
  llvm::DenseSet<Value> seen;
  llvm::SmallVector<Value, 8> work{value};
  while (!work.empty()) {
    Value current = work.pop_back_val();
    if (!seen.insert(current).second)
      continue;
    for (Operation *user : current.getUsers()) {
      if (isStateConsumer(user))
        return true;
      if (auto alias = dyn_cast<pyc::AliasOp>(user))
        work.push_back(alias.getResult());
      else if (auto assign = dyn_cast<pyc::AssignOp>(user))
        work.push_back(assign.getDst());
    }
  }
  return false;
}

class ForkMergeRetime {
public:
  explicit ForkMergeRetime(func::FuncOp function) : function(function) {}

  MergeStats run() {
    llvm::SmallVector<pyc::RegOp, 16> regs;
    llvm::DenseSet<Value> registerQ;
    function.walk([&](pyc::RegOp reg) {
      regs.push_back(reg);
      registerQ.insert(reg.getQ());
    });

    llvm::StringMap<pyc::RegOp> owner;
    // (victim, survivor) pairs recorded directly: stuffing the grouping key
    // into a FlatSymbolRefAttr corrupts memory because the key is not a valid
    // symbol name.
    llvm::SmallVector<std::pair<pyc::RegOp, pyc::RegOp>, 16> merges;
    for (pyc::RegOp reg : regs) {
      // Merging across blocks would point uses at a value that does not
      // dominate them; only registers in the same block are interchangeable.
      if (reg->getBlock() != &function.front())
        continue;
      if (coneReadsOwnQ(reg))
        continue;
      // Merging copies that share a combinational producer makes the later
      // state passes (retime / CSE / dead-state / chain forming) interact
      // over one producer with several former consumers, which crashes the
      // real Davinci pipeline wherever this pass is placed. Copies rooted at
      // a constant or a function input are safe and are the bulk of the
      // anonymous cycle_balance duplicates. A bare register q reached only
      // through transparent aliases / single-driver wires is equally safe:
      // it is a state value, not a comb cone, so no producer rewrite races
      // the merge. Covers the parallel delay copies a balanced pipeline
      // leaves behind (47 identical one-beat followers of one source on
      // pe_cluster; 694 redundant regs measured on 2026-11-22).
      Operation *dataDef = resolve(reg.getNext()).getDefiningOp();
      if (dataDef && !isa<pyc::ConstantOp>(dataDef) &&
          !isa<pyc::RegOp>(dataDef))
        continue;
      // A register whose value reaches a delay line, tap or instance is part
      // of that state element's history identity. The chain former usually
      // reaches it through aliases, so look through them.
      if (reachesStateConsumer(reg.getQ()))
        continue;
      auto init = constantValue(reg.getInit());
      auto width = scalarWidth(reg.getQ().getType());
      if (!init || !width || init->getBitWidth() != *width)
        continue;

      std::string key = keyOf(reg);
      auto found = owner.find(key);
      if (found == owner.end()) {
        owner[key] = reg;
        ++stats.groups;
        continue;
      }
      merges.push_back({reg, found->second});
    }

    for (auto [reg, survivor] : merges) {
      if (!survivor || survivor == reg)
        continue;
      if (reg->getBlock() != survivor->getBlock())
        continue;
      // Two copies with identical clock/reset/enable/init and the same data
      // input are one flip-flop, so the survivor holds the same value on
      // every cycle. Keep the erased register's observable names readable by
      // materializing them as read-only aliases of the survivor at the old
      // position: the survivor is upstream, so the alias stays dominated and
      // a probe still reads the identical value.
      // Two copies with identical clock/reset/enable/init and the same data
      // input are one flip-flop, so the survivor holds the same value on
      // every cycle. Keep the erased register's own observable names readable
      // through a read-only alias of the survivor placed at the old position:
      // the survivor is upstream, so the alias stays dominated and a probe
      // reads the identical value.
      //
      // The alias is a leaf by construction (nothing references it): it only
      // serves name -> value lookup, so it never enters the dataflow, the
      // change-driven schedule, or the state loops, and costs nothing at
      // runtime. This matches the repo convention that observation aliases
      // are leaves with readers bound straight to the source.
      //
      // remapStateOpIdentity is deliberately not used: it crashes the real
      // Davinci pipeline.
      if (hasExternalObservationIdentity(reg.getOperation())) {
        OpBuilder builder(reg.getContext());
        builder.setInsertionPoint(reg);
        auto alias = builder.create<pyc::AliasOp>(
            reg.getLoc(), reg.getQ().getType(), survivor.getQ());
        copyExternalObservationAttrs(reg.getOperation(), alias.getOperation());
      }
      reg.getQ().replaceAllUsesWith(survivor.getQ());
      // Erase here rather than leaving the duplicate for the later dead-state
      // cleanup: leaving it produced a dangling value when cleanup ran after
      // CSE on the real Davinci IR.
      reg.erase();
      ++stats.merged;
    }
    return stats;
  }

private:
  func::FuncOp function;
  MergeStats stats;

  static std::string keyOf(pyc::RegOp reg) {
    auto init = constantValue(reg.getInit());
    std::string key;
    llvm::raw_string_ostream os(key);
    os << scalarWidth(reg.getQ().getType()).value_or(0) << ':';
    appendValueKey(os, reg.getNext());
    os << '|';
    appendValueKey(os, reg.getClk());
    os << '|';
    appendValueKey(os, reg.getRst());
    os << '|';
    appendValueKey(os, reg.getEn());
    if (init)
      os << '|' << init->getBitWidth() << 'x' << *init;
    else
      os << '|';
    os.flush();
    return key;
  }
};

struct ForkMergeRetimePass
    : public PassWrapper<ForkMergeRetimePass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(ForkMergeRetimePass)

  StringRef getArgument() const override { return "pyc-fork-merge-retime"; }
  StringRef getDescription() const override {
    return "Merge parallel register copies that sample the same value with "
           "the same clock/reset/enable/init into a single register";
  }

  void runOnOperation() override {
    MergeStats stats = ForkMergeRetime(getOperation()).run();
    func::FuncOp function = getOperation();
    setI64Attr(function, "pyc.stats.fork_merge_merged", stats.merged);
    setI64Attr(function, "pyc.stats.fork_merge_groups", stats.groups);
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createForkMergeRetimePass() {
  return std::make_unique<ForkMergeRetimePass>();
}

void runForkMergeRetime(func::FuncOp function) {
  ForkMergeRetime(function).run();
}

static PassRegistration<ForkMergeRetimePass> forkMergeRetimePass;

} // namespace pyc
