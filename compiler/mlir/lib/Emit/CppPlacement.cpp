#include "pyc/Emit/CppPlacement.h"

#include "mlir/IR/Attributes.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Operation.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/Support/raw_ostream.h"

using namespace mlir;

namespace pyc {

// ---------------------------------------------------------------------------
// comb op ordering (must agree between placement pass and C++ emitter)
// ---------------------------------------------------------------------------

std::string combSortKey(pyc::CombOp comb) {
  if (auto nameAttr = comb->getAttrOfType<StringAttr>("pyc.name"))
    return nameAttr.getValue().str();
  std::string locKey;
  llvm::raw_string_ostream locOs(locKey);
  comb.getLoc().print(locOs);
  return locOs.str();
}

void sortCombsByStableKey(llvm::SmallVectorImpl<pyc::CombOp> &combs) {
  llvm::sort(combs, [](pyc::CombOp a, pyc::CombOp b) { return combSortKey(a) < combSortKey(b); });
}

namespace {

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/// C++ type string for a local Wire<> declaration.
static std::string cppTypeForWire(Type ty) {
  if (isa<pyc::ClockType>(ty) || isa<pyc::ResetType>(ty))
    return "pyc::cpp::Wire<1>";
  if (auto intTy = dyn_cast<IntegerType>(ty))
    return "pyc::cpp::Wire<" + std::to_string(intTy.getWidth()) + ">";
  if (auto vt = dyn_cast<VectorType>(ty)) {
    std::string inner = cppTypeForWire(vt.getElementType());
    auto shape = vt.getShape();
    for (int i = static_cast<int>(shape.size()) - 1; i >= 0; --i)
      inner = "pyc::cpp::Vec<" + inner + ", " + std::to_string(shape[i]) + ">";
    return inner;
  }
  return "pyc::cpp::Wire<1>";
}

/// Assign every op inside \p comb to an eval_comb_* method, chunking if needed.
static void assignCombOpMethods(pyc::CombOp comb, unsigned combIdx, unsigned combChunkNodes,
                                llvm::DenseMap<Operation *, std::string> &opToMethod,
                                llvm::DenseMap<pyc::CombOp, std::string> &combWrappers) {
  combWrappers[comb] = "eval_comb_" + std::to_string(combIdx);

  Block &b = comb.getBody().front();
  llvm::SmallVector<Operation *> combOps;
  for (Operation &op : b) {
    if (isa<pyc::YieldOp>(op))
      break;
    combOps.push_back(&op);
  }

  if (combOps.size() <= combChunkNodes) {
    std::string m = "eval_comb_" + std::to_string(combIdx);
    for (Operation *op : combOps)
      opToMethod[op] = m;
    return;
  }

  for (unsigned begin = 0, partIdx = 0; begin < combOps.size(); begin += combChunkNodes, ++partIdx) {
    unsigned end = std::min<unsigned>(combOps.size(), begin + combChunkNodes);
    std::string m = "eval_comb_" + std::to_string(combIdx) + "_part_" + std::to_string(partIdx);
    for (unsigned i = begin; i < end; ++i)
      opToMethod[combOps[i]] = m;
  }
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

/// Resolve which method an op belongs to (for cross-method detection).
static StringRef methodForUserOp(Operation *user,
                                 const llvm::DenseMap<Operation *, std::string> &opToMethod,
                                 const llvm::DenseMap<pyc::CombOp, std::string> &combWrappers) {
  if (auto it = opToMethod.find(user); it != opToMethod.end())
    return it->second;
  // Yield ops forward to the comb's wrapper method.
  if (isa<pyc::YieldOp>(user)) {
    if (auto comb = user->getParentOfType<pyc::CombOp>()) {
      if (auto it = combWrappers.find(comb); it != combWrappers.end())
        return it->second;
    }
  }
  return "core";
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

} // namespace

// ---------------------------------------------------------------------------
// public attribute readers
// ---------------------------------------------------------------------------

CppStorageKind getValueCppStorage(Value v) {
  Operation *op = v.getDefiningOp();
  if (!op)
    return CppStorageKind::Struct;
  if (auto a = op->getAttrOfType<StringAttr>(kCppStorageAttr)) {
    if (a.getValue() == "local")
      return CppStorageKind::Local;
  }
  return CppStorageKind::Struct;
}

StringRef getValueCppOwner(Value v) {
  Operation *op = v.getDefiningOp();
  if (!op)
    return {};
  if (auto a = op->getAttrOfType<StringAttr>(kCppOwnerAttr))
    return a.getValue();
  return {};
}

// ---------------------------------------------------------------------------
// CppEmitterPlacementState (per-emission helpers used by CppEmitter)
// ---------------------------------------------------------------------------

bool CppEmitterPlacementState::emitLocalDeclIfNeeded(Value v, Type ty, StringRef name,
                                                   llvm::raw_ostream &os, unsigned indentSpaces) {
  if (getValueCppStorage(v) != CppStorageKind::Local)
    return false;
  StringRef owner = getValueCppOwner(v);
  if (!owner.empty() && owner != currentMethod)
    return false;
  if (!declaredLocals.insert(v).second)
    return false;
  for (unsigned i = 0; i < indentSpaces; ++i)
    os << ' ';
  os << cppTypeForWire(ty) << " " << name << "{};\n";
  return true;
}

void CppEmitterPlacementState::emitValueAssign(Value result, Type ty, StringRef name, StringRef expr,
                                               llvm::raw_ostream &os, unsigned indentSpaces) {
  for (unsigned i = 0; i < indentSpaces; ++i)
    os << ' ';

  // Struct members: plain assignment.
  if (getValueCppStorage(result) != CppStorageKind::Local) {
    os << name << " = " << expr << ";\n";
    return;
  }

  // Method-local Wire<>: declare-with-init on first assignment, plain assign on reuse.
  if (declaredLocals.insert(result).second)
    os << cppTypeForWire(ty) << " " << name << " = " << expr << ";\n";
  else
    os << name << " = " << expr << ";\n";
}

// ---------------------------------------------------------------------------
// member placement: decide struct vs local for every value
// ---------------------------------------------------------------------------

CppPlacementSummary runCppMemberPlacement(func::FuncOp f, unsigned combChunkNodes) {
  CppPlacementSummary summary;

  // Build method-assignment maps: collect top-level combs in stable order.
  llvm::DenseMap<Operation *, std::string> opToMethod;
  llvm::DenseMap<pyc::CombOp, std::string> combWrappers;
  if (!f.getBody().empty()) {
    llvm::SmallVector<pyc::CombOp> combs;
    for (Operation &op : f.getBody().front())
      if (auto comb = dyn_cast<pyc::CombOp>(op))
        combs.push_back(comb);
    sortCombsByStableKey(combs);
    for (auto [i, comb] : llvm::enumerate(combs))
      assignCombOpMethods(comb, static_cast<unsigned>(i), combChunkNodes, opToMethod, combWrappers);
  }

  // Collect all comb-region SSA values (excluding yield terminators).
  llvm::SmallVector<Value> candidates;
  f.walk([&](Operation *op) {
    if (op->getParentOfType<pyc::CombOp>() == nullptr)
      return;
    if (isa<pyc::YieldOp>(op))
      return;
    for (Value r : op->getResults())
      candidates.push_back(r);
  });

  // Phase 1: for each comb-region value, decide struct vs local.
  for (Value v : candidates) {
    Operation *def = v.getDefiningOp();

    // Determine the owner method for this value.
    StringRef owner = "core";
    if (def) {
      auto it = opToMethod.find(def);
      if (it != opToMethod.end())
        owner = it->second;
    }

    // Values pinned to struct (block args, state ops, cross-comb uses) stay struct.
    if (pinToStruct(v)) {
      annotatePlacement(v, CppStorageKind::Struct, owner);
      summary.structMembers++;
      if (!def)
        summary.probePinnedStruct++;
      continue;
    }

    // Promote to local only when all uses are in the same method.
    bool crossMethod = false;
    for (OpOperand &use : v.getUses()) {
      Operation *user = use.getOwner();
      if (methodForUserOp(user, opToMethod, combWrappers) != owner) {
        crossMethod = true;
        break;
      }
    }
    if (crossMethod) {
      annotatePlacement(v, CppStorageKind::Struct, owner);
      summary.structMembers++;
      summary.promotedCrossMethod++;
      continue;
    }

    // Single-method: make it a function-local Wire<>.
    annotatePlacement(v, CppStorageKind::Local, owner);
    summary.localInMethod++;
  }

  // Phase 2: ensure non-comb values (regs, instances, ports, etc.) are struct members.
  f.walk([&](Operation *op) {
    if (op->getParentOfType<pyc::CombOp>() != nullptr)
      return;
    for (Value r : op->getResults()) {
      if (getValueCppStorage(r) == CppStorageKind::Local)
        continue;
      annotatePlacement(r, CppStorageKind::Struct, {});
      summary.structMembers++;
    }
  });

  return summary;
}

// ---------------------------------------------------------------------------
// module / function attribute helpers
// ---------------------------------------------------------------------------

void setModuleCombChunkNodes(ModuleOp module, unsigned combChunkNodes) {
  auto *ctx = module.getContext();
  module->setAttr(kCppCombChunkNodesAttr,
                  IntegerAttr::get(IntegerType::get(ctx, 64), combChunkNodes));
}

std::optional<unsigned> getModuleCombChunkNodes(ModuleOp module) {
  auto attr = module->getAttrOfType<IntegerAttr>(kCppCombChunkNodesAttr);
  if (!attr)
    return std::nullopt;
  return static_cast<unsigned>(attr.getValue().getZExtValue());
}

void setFuncPlacementSummary(func::FuncOp f, const CppPlacementSummary &summary) {
  auto *ctx = f.getContext();
  llvm::SmallVector<NamedAttribute, 4> fields;
  fields.emplace_back(StringAttr::get(ctx, "struct_members"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.structMembers));
  fields.emplace_back(StringAttr::get(ctx, "local_in_method"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.localInMethod));
  fields.emplace_back(StringAttr::get(ctx, "promoted_cross_method"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.promotedCrossMethod));
  fields.emplace_back(StringAttr::get(ctx, "probe_pinned_struct"),
                      IntegerAttr::get(IntegerType::get(ctx, 64), summary.probePinnedStruct));
  f->setAttr(kCppPlacementSummaryAttr, DictionaryAttr::get(ctx, fields));
}

static std::optional<uint64_t> readSummaryField(func::FuncOp f, StringRef key) {
  auto dict = f->getAttrOfType<DictionaryAttr>(kCppPlacementSummaryAttr);
  if (!dict)
    return std::nullopt;
  auto attr = dict.get(key);
  if (!attr)
    return std::nullopt;
  if (auto intAttr = dyn_cast<IntegerAttr>(attr))
    return intAttr.getValue().getZExtValue();
  return std::nullopt;
}

std::optional<CppPlacementSummary> getFuncPlacementSummary(func::FuncOp f) {
  auto structMembers = readSummaryField(f, "struct_members");
  if (!structMembers)
    return std::nullopt;
  CppPlacementSummary summary;
  summary.structMembers = static_cast<unsigned>(*structMembers);
  if (auto v = readSummaryField(f, "local_in_method"))
    summary.localInMethod = static_cast<unsigned>(*v);
  if (auto v = readSummaryField(f, "promoted_cross_method"))
    summary.promotedCrossMethod = static_cast<unsigned>(*v);
  if (auto v = readSummaryField(f, "probe_pinned_struct"))
    summary.probePinnedStruct = static_cast<unsigned>(*v);
  return summary;
}

CppPlacementSummary accumulateModulePlacementSummary(ModuleOp module) {
  CppPlacementSummary totals;
  for (auto f : module.getOps<func::FuncOp>()) {
    if (f.isDeclaration())
      continue;
    if (auto summary = getFuncPlacementSummary(f)) {
      totals.structMembers += summary->structMembers;
      totals.localInMethod += summary->localInMethod;
      totals.promotedCrossMethod += summary->promotedCrossMethod;
      totals.probePinnedStruct += summary->probePinnedStruct;
    }
  }
  return totals;
}

} // namespace pyc
