// Merge parallel register copies: registers that sample the same value with
// the same clock/reset/enable/init are one flip-flop instantiated N times.
// Keep one and delete the rest. No IR is created, so path delays and every
// input-to-output register count are untouched.

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

// Looks through aliases and single-driver wires.
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

// Value::print is not thread-safe under the parallel function-pass scheduler,
// so real values key on pointer identity rather than their printed form.
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

// True when `value` reaches a delay line/tap/instance, which keep it as part
// of their history identity.
static bool reachesStateConsumer(Value value) {
  llvm::DenseSet<Value> seen;
  llvm::SmallVector<Value, 8> work{value};
  while (!work.empty()) {
    Value current = work.pop_back_val();
    if (!seen.insert(current).second)
      continue;
    for (Operation *user : current.getUsers()) {
      if (isa<pyc::DelayLineOp, pyc::DelayTapOp, pyc::InstanceOp>(user))
        return true;
      if (auto alias = dyn_cast<pyc::AliasOp>(user))
        work.push_back(alias.getResult());
      else if (auto assign = dyn_cast<pyc::AssignOp>(user))
        work.push_back(assign.getDst());
    }
  }
  return false;
}

class MergeRegCopies {
public:
  explicit MergeRegCopies(func::FuncOp function) : function(function) {}

  MergeStats run() {
    llvm::SmallVector<pyc::RegOp, 16> regs;
    function.walk([&](pyc::RegOp reg) { regs.push_back(reg); });

    llvm::StringMap<pyc::RegOp> owner;
    llvm::SmallVector<std::pair<pyc::RegOp, pyc::RegOp>, 16> merges;
    for (pyc::RegOp reg : regs) {
      // Uses must stay dominated by the survivor, so only the entry block.
      if (reg->getBlock() != &function.front())
        continue;
      if (coneReadsOwnQ(reg))
        continue;
      // A combinational producer shared by the copies would make the later
      // state passes interact over one producer with several former
      // consumers, which crashes the pipeline. Constants, function inputs and
      // bare register q's are state, not cones, and are safe.
      Operation *dataDef = resolve(reg.getNext()).getDefiningOp();
      if (dataDef && !isa<pyc::ConstantOp, pyc::RegOp>(dataDef))
        continue;
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
      // The survivor is upstream, so an alias placed here stays dominated and
      // a probe reads the identical value. The alias is a leaf: it only serves
      // name lookup and never enters the dataflow or the schedule.
      if (hasExternalObservationIdentity(reg.getOperation())) {
        OpBuilder builder(reg.getContext());
        builder.setInsertionPoint(reg);
        auto alias = builder.create<pyc::AliasOp>(
            reg.getLoc(), reg.getQ().getType(), survivor.getQ());
        copyExternalObservationAttrs(reg.getOperation(), alias.getOperation());
      }
      reg.getQ().replaceAllUsesWith(survivor.getQ());
      reg.erase();
      ++stats.merged;
    }
    return stats;
  }

private:
  func::FuncOp function;
  MergeStats stats;

  static std::string keyOf(pyc::RegOp reg) {
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
    os << '|';
    appendValueKey(os, reg.getInit());
    os.flush();
    return key;
  }
};

struct MergeRegCopiesPass
    : public PassWrapper<MergeRegCopiesPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(MergeRegCopiesPass)

  StringRef getArgument() const override { return "pyc-merge-reg-copies"; }
  StringRef getDescription() const override {
    return "Merge parallel register copies that sample the same value with "
           "the same clock/reset/enable/init into a single register";
  }

  void runOnOperation() override {
    MergeStats stats = MergeRegCopies(getOperation()).run();
    setI64Attr(getOperation(), "pyc.stats.merge_reg_copies_merged",
               stats.merged);
    setI64Attr(getOperation(), "pyc.stats.merge_reg_copies_groups",
               stats.groups);
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createMergeRegCopiesPass() {
  return std::make_unique<MergeRegCopiesPass>();
}

static PassRegistration<MergeRegCopiesPass> mergeRegCopiesPass;

} // namespace pyc
