#include "pyc/Transforms/StateOptimization.h"

#include "mlir/IR/BuiltinAttributes.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/Hashing.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/JSON.h"

#include <string>

using namespace mlir;

namespace pyc {

namespace {

static bool hasObservationAttribute(Operation *op) {
  for (NamedAttribute attr : op->getAttrs()) {
    llvm::StringRef name = attr.getName().strref();
    if (name.starts_with("pyc.probe") || name.starts_with("pyc.trace") ||
        name == "pyc.observable")
      return true;
  }
  return false;
}

static bool isObservationAttrName(llvm::StringRef name) {
  return name == "pyc.name" || name == "pyc.debug_keep" ||
         name == "pyc.observable" || name.starts_with("pyc.probe") ||
         name.starts_with("pyc.trace");
}

static std::size_t opaqueHash(const void *ptr) {
  return static_cast<std::size_t>(llvm::hash_value(ptr));
}

// A delay tap may replace only a read of an intermediate state.  A second
// stateful consumer would impose another write/hold boundary and cannot be
// represented by a read-only view of the compact history.

} // namespace

std::size_t semanticValueHash(Value value) {
  value = stripStateAliases(value);
  if (auto constant = value.getDefiningOp<pyc::ConstantOp>()) {
    Attribute literal = constant->getAttr("value");
    return static_cast<std::size_t>(llvm::hash_combine(
        value.getType().getAsOpaquePointer(), literal.getAsOpaquePointer(), 1));
  }
  return static_cast<std::size_t>(llvm::hash_combine(
      value.getType().getAsOpaquePointer(), value.getAsOpaquePointer(), 0));
}

bool isStatefulConsumer(Operation *op) {
  return isa<pyc::RegOp, pyc::DelayLineOp, pyc::FifoOp,
             pyc::ByteMemOp, pyc::SyncMemOp, pyc::SyncMemDPOp,
             pyc::AsyncFifoOp, pyc::CdcSyncOp, pyc::InstanceOp>(op);
}

bool isCycleBalanceGenerated(Operation *op) {
  if (!op)
    return false;
  auto generated = op->getAttrOfType<StringAttr>("pyc.generated");
  return generated && generated.getValue() == "cycle_balance";
}

bool shouldKeepStateOptimization(Operation *op) {
  if (!op)
    return false;
  if (auto keep = op->getAttrOfType<BoolAttr>("pyc.debug_keep"))
    return keep.getValue();
  return hasObservationAttribute(op);
}

bool isObserveLazy(Operation *op) {
  if (!op)
    return false;
  if (auto lazy = op->getAttrOfType<BoolAttr>("pyc.observe_lazy"))
    return lazy.getValue();
  return false;
}

bool hasStableStateName(Operation *op) {
  if (!op || !op->hasAttrOfType<StringAttr>("pyc.name"))
    return false;
  if (isObserveLazy(op))
    return false;
  // Frontend cycle-balance names are explicitly excluded from both probe
  // manifest generation and C++ ProbeRegistry registration.
  return !isCycleBalanceGenerated(op);
}

bool hasExternalObservationIdentity(Operation *op) {
  if (!op)
    return false;
  const bool skipCycleBalanceName = isCycleBalanceGenerated(op);
  for (NamedAttribute attr : op->getAttrs()) {
    llvm::StringRef name = attr.getName().strref();
    if (!isObservationAttrName(name))
      continue;
    if (name == "pyc.name" && skipCycleBalanceName)
      continue;
    return true;
  }
  return false;
}

bool stateHasExternalObservation(Operation *state, Value q) {
  if (hasExternalObservationIdentity(state))
    return true;
  llvm::SmallVector<Value> worklist{q};
  llvm::DenseSet<Value> seen;
  while (!worklist.empty()) {
    Value value = worklist.pop_back_val();
    if (!seen.insert(value).second)
      continue;
    for (Operation *user : value.getUsers()) {
      auto alias = dyn_cast<pyc::AliasOp>(user);
      if (!alias)
        continue;
      if (hasExternalObservationIdentity(alias))
        return true;
      worklist.push_back(alias.getResult());
    }
  }
  return false;
}

void copyExternalObservationAttrs(Operation *from, Operation *to) {
  if (!from || !to)
    return;
  const bool skipCycleBalanceName = isCycleBalanceGenerated(from);
  for (NamedAttribute attr : from->getAttrs()) {
    llvm::StringRef name = attr.getName().strref();
    if (!isObservationAttrName(name))
      continue;
    if (name == "pyc.name" && skipCycleBalanceName)
      continue;
    to->setAttr(attr.getName(), attr.getValue());
  }
}

pyc::AliasOp materializeObservationAlias(OpBuilder &builder, Location loc,
                                         Value source,
                                         Operation *attrSource) {
  auto alias =
      builder.create<pyc::AliasOp>(loc, source.getType(), source);
  copyExternalObservationAttrs(attrSource, alias);
  return alias;
}

void remapStateOpIdentity(OpBuilder &builder, Operation *oldState,
                          Value newSource) {
  if (!oldState || !newSource || !hasExternalObservationIdentity(oldState))
    return;
  materializeObservationAlias(builder, oldState->getLoc(), newSource,
                              oldState);
}

Value stripStateAliases(Value value) {
  while (auto alias = value.getDefiningOp<pyc::AliasOp>())
    value = alias.getIn();
  return value;
}

bool equivalentStateValue(Value lhs, Value rhs) {
  lhs = stripStateAliases(lhs);
  rhs = stripStateAliases(rhs);
  if (lhs == rhs)
    return true;
  if (lhs.getType() != rhs.getType())
    return false;
  auto lhsConstant = lhs.getDefiningOp<pyc::ConstantOp>();
  auto rhsConstant = rhs.getDefiningOp<pyc::ConstantOp>();
  if (!lhsConstant || !rhsConstant)
    return false;
  return lhsConstant->getAttr("value") == rhsConstant->getAttr("value");
}

std::optional<StateChainLink>
matchStateChainPredecessor(pyc::RegOp consumer, pyc::RegOp keyReg) {
  Value value = consumer.getNext();
  StateChainLink link;
  while (auto alias = value.getDefiningOp<pyc::AliasOp>()) {
    link.aliasesFromConsumerToProducer.push_back(alias);
    value = alias.getIn();
  }

  auto predecessor = value.getDefiningOp<pyc::RegOp>();
  if (!predecessor)
    return std::nullopt;
  if (predecessor.getQ().getType() != keyReg.getQ().getType() ||
      !equivalentStateValue(predecessor.getClk(), keyReg.getClk()) ||
      !equivalentStateValue(predecessor.getRst(), keyReg.getRst()) ||
      !equivalentStateValue(predecessor.getEn(), keyReg.getEn()) ||
      !equivalentStateValue(predecessor.getInit(), keyReg.getInit()))
    return std::nullopt;

  Value expectedProducer = predecessor.getQ();
  auto hasRequiredUser = [&](Value value, Operation *required) {
    for (Operation *user : value.getUsers()) {
      if (user != required && isStatefulConsumer(user))
        return false;
    }
    return llvm::is_contained(value.getUsers(), required);
  };
  for (pyc::AliasOp alias :
       llvm::reverse(link.aliasesFromConsumerToProducer)) {
    if (!hasRequiredUser(expectedProducer, alias.getOperation()))
      return std::nullopt;
    expectedProducer = alias.getResult();
  }
  if (!hasRequiredUser(expectedProducer, consumer.getOperation()))
    return std::nullopt;

  link.predecessor = predecessor;
  return link;
}

bool equivalentDelayLineState(pyc::DelayLineOp lhs, pyc::DelayLineOp rhs) {
  auto lhsDepth = lhs->getAttrOfType<IntegerAttr>("depth");
  auto rhsDepth = rhs->getAttrOfType<IntegerAttr>("depth");
  return lhsDepth && rhsDepth && lhsDepth == rhsDepth &&
         lhs.getQ().getType() == rhs.getQ().getType() &&
         equivalentStateValue(lhs.getClk(), rhs.getClk()) &&
         equivalentStateValue(lhs.getRst(), rhs.getRst()) &&
         equivalentStateValue(lhs.getEn(), rhs.getEn()) &&
         equivalentStateValue(lhs.getNext(), rhs.getNext()) &&
         equivalentStateValue(lhs.getInit(), rhs.getInit());
}

std::size_t delayLineStateHash(pyc::DelayLineOp delay) {
  auto depth = delay->getAttrOfType<IntegerAttr>("depth");
  return static_cast<std::size_t>(llvm::hash_combine(
      opaqueHash(delay.getQ().getType().getAsOpaquePointer()),
      depth ? depth.getInt() : 0,
      semanticValueHash(delay.getClk()), semanticValueHash(delay.getRst()),
      semanticValueHash(delay.getEn()), semanticValueHash(delay.getNext()),
      semanticValueHash(delay.getInit())));
}

//===----------------------------------------------------------------------===//
// Lazy probe metadata and observation plan loading
//===----------------------------------------------------------------------===//

StringRef lazyProbeSlicesAttrName() { return "pyc.lazy_probe_slices"; }

StringRef lazyProbeWiresAttrName() { return "pyc.lazy_probe_wires"; }

namespace {
/// Unified walk from an SSA value down to the state storage producing it:
/// aliases are transparent, packed extracts accumulate lsb, comb yields are
/// traversed to their operands, and delay taps report their depth.
struct StateStorageWalkResult {
  Operation *storage = nullptr;
  bool isRegOrDelay = false;
  Value q{};
  unsigned lsb = 0;
  int64_t tapDepth = -1;
  Value tap{};
};

static std::optional<StateStorageWalkResult>
walkToStateStorage(Value value) {
  llvm::SmallVector<Value, 8> seen;
  unsigned lsb = 0;
  while (true) {
    for (Value prev : seen) {
      if (prev == value)
        return std::nullopt;
    }
    seen.push_back(value);
    while (auto alias = value.getDefiningOp<pyc::AliasOp>())
      value = alias.getIn();
    if (auto reg = value.getDefiningOp<pyc::RegOp>())
      return StateStorageWalkResult{reg.getOperation(), true, reg.getQ(), lsb};
    if (auto delay = value.getDefiningOp<pyc::DelayLineOp>())
      return StateStorageWalkResult{delay.getOperation(), true, delay.getQ(),
                                    lsb};
    if (auto tap = value.getDefiningOp<pyc::DelayTapOp>()) {
      auto delay = tap.getLine().getDefiningOp<pyc::DelayLineOp>();
      auto depth = tap->getAttrOfType<IntegerAttr>("depth");
      if (!delay || !depth)
        return std::nullopt;
      return StateStorageWalkResult{delay.getOperation(), true, delay.getQ(),
                                    lsb, depth.getInt(), tap.getTap()};
    }
    if (auto extract = value.getDefiningOp<pyc::ExtractOp>()) {
      auto packedLsb =
          extract->getAttrOfType<IntegerAttr>("pyc.state_pack_lsb");
      // Outside packed storage only the declared lsb applies; packed lanes
      // must always carry their pyc.state_pack_lsb marker.
      const int64_t sliceLsb = packedLsb ? packedLsb.getInt() : -1;
      if (sliceLsb < 0)
        return std::nullopt;
      lsb += static_cast<unsigned>(sliceLsb);
      value = extract.getIn();
      continue;
    }
    auto comb = value.getDefiningOp<pyc::CombOp>();
    if (!comb)
      return std::nullopt;
    auto res = dyn_cast<OpResult>(value);
    if (!res)
      return std::nullopt;
    auto yield =
        dyn_cast_or_null<pyc::YieldOp>(comb.getBody().front().getTerminator());
    if (!yield || res.getResultNumber() >= yield.getNumOperands())
      return std::nullopt;
    Value y = yield.getOperand(res.getResultNumber());
    while (auto alias = y.getDefiningOp<pyc::AliasOp>())
      y = alias.getIn();
    if (auto extract = y.getDefiningOp<pyc::ExtractOp>()) {
      auto packedLsb =
          extract->getAttrOfType<IntegerAttr>("pyc.state_pack_lsb");
      const int64_t sliceLsb = packedLsb ? packedLsb.getInt() : -1;
      if (sliceLsb < 0)
        return std::nullopt;
      lsb += static_cast<unsigned>(sliceLsb);
      y = extract.getIn();
      while (auto alias = y.getDefiningOp<pyc::AliasOp>())
        y = alias.getIn();
    }
    auto barg = dyn_cast<BlockArgument>(y);
    if (!barg || barg.getOwner() != &comb.getBody().front() ||
        barg.getArgNumber() >= comb.getNumOperands())
      return std::nullopt;
    value = comb.getOperand(barg.getArgNumber());
  }
}
} // namespace

std::optional<StateStorageSource> findStateStorageSource(Value value) {
  auto walk = walkToStateStorage(value);
  if (!walk || !walk->isRegOrDelay)
    return std::nullopt;
  return StateStorageSource{walk->storage, walk->q, walk->lsb,
                            walk->tapDepth, walk->tap};
}

std::optional<WireProbeHostSource> findWireProbeHost(Value value) {
  while (auto alias = value.getDefiningOp<pyc::AliasOp>())
    value = alias.getIn();
  if (auto comb = value.getDefiningOp<pyc::CombOp>()) {
    auto res = dyn_cast<OpResult>(value);
    if (!res)
      return std::nullopt;
    return WireProbeHostSource{comb.getOperation(),
                               static_cast<unsigned>(res.getResultNumber())};
  }
  Operation *def = value.getDefiningOp();
  if (!def)
    return std::nullopt;
  if (isa<pyc::RegOp, pyc::DelayLineOp, pyc::DelayTapOp>(def))
    return std::nullopt;
  if (auto comb = def->getParentOfType<pyc::CombOp>()) {
    auto yield =
        dyn_cast_or_null<pyc::YieldOp>(comb.getBody().front().getTerminator());
    if (!yield)
      return std::nullopt;
    for (auto [i, operand] : llvm::enumerate(yield.getOperands())) {
      Value y = operand;
      while (auto alias = y.getDefiningOp<pyc::AliasOp>())
        y = alias.getIn();
      if (y == value)
        return WireProbeHostSource{comb.getOperation(),
                                   static_cast<unsigned>(i)};
    }
    return std::nullopt;
  }
  auto res = dyn_cast<OpResult>(value);
  if (!res)
    return std::nullopt;
  return WireProbeHostSource{def, static_cast<unsigned>(res.getResultNumber())};
}

LogicalResult appendLazyProbeSlice(Operation *storage, StringRef name,
                                   unsigned lsb, unsigned width,
                                   int64_t tapDepth) {
  MLIRContext *ctx = storage->getContext();
  SmallVector<Attribute> items;
  if (auto existing =
          storage->getAttrOfType<ArrayAttr>(lazyProbeSlicesAttrName()))
    items.append(existing.begin(), existing.end());
  for (Attribute item : items) {
    auto dict = dyn_cast<DictionaryAttr>(item);
    if (!dict)
      continue;
    if (auto prev = dict.getAs<StringAttr>("name");
        prev && prev.getValue() == name)
      return success();
  }
  NamedAttrList fields;
  fields.set("name", StringAttr::get(ctx, name));
  fields.set("lsb", IntegerAttr::get(IntegerType::get(ctx, 64), lsb));
  fields.set("width", IntegerAttr::get(IntegerType::get(ctx, 64), width));
  fields.set("tap_depth",
             IntegerAttr::get(IntegerType::get(ctx, 64), tapDepth));
  items.push_back(DictionaryAttr::get(ctx, fields));
  storage->setAttr(lazyProbeSlicesAttrName(), ArrayAttr::get(ctx, items));
  return success();
}

LogicalResult appendLazyProbeWire(Operation *host, StringRef name,
                                  unsigned width, unsigned resultIndex) {
  MLIRContext *ctx = host->getContext();
  SmallVector<Attribute> items;
  if (auto existing =
          host->getAttrOfType<ArrayAttr>(lazyProbeWiresAttrName()))
    items.append(existing.begin(), existing.end());
  for (Attribute item : items) {
    auto dict = dyn_cast<DictionaryAttr>(item);
    if (!dict)
      continue;
    if (auto prev = dict.getAs<StringAttr>("name");
        prev && prev.getValue() == name)
      return success();
  }
  NamedAttrList fields;
  fields.set("name", StringAttr::get(ctx, name));
  fields.set("width", IntegerAttr::get(IntegerType::get(ctx, 64), width));
  fields.set("result",
             IntegerAttr::get(IntegerType::get(ctx, 64), resultIndex));
  items.push_back(DictionaryAttr::get(ctx, fields));
  host->setAttr(lazyProbeWiresAttrName(), ArrayAttr::get(ctx, items));
  return success();
}

LogicalResult loadObservationPlans(ModuleOp module, StringRef probePlanPath,
                                   StringRef traceCodegenPlanPath,
                                   ObservationPlans &out) {
  if (!probePlanPath.empty()) {
    auto fileOrErr = llvm::MemoryBuffer::getFile(probePlanPath);
    if (!fileOrErr)
      return module.emitError("cannot read probe plan: ") << probePlanPath;
    auto parsed = llvm::json::parse(fileOrErr.get()->getBuffer());
    if (!parsed || !parsed->getAsObject())
      return module.emitError("invalid probe plan JSON: ") << probePlanPath;
    const auto *root = parsed->getAsObject();
    auto topSymbol = root->getString("top_symbol");
    out.probeTopSymbol = topSymbol ? topSymbol->str() : std::string();
    const auto *aliases = root->getArray("aliases");
    if (!aliases)
      return module.emitError("probe plan requires array `aliases`");
    for (const llvm::json::Value &value : *aliases) {
      const auto *entry = value.getAsObject();
      if (!entry)
        return module.emitError("probe plan alias must be an object");
      auto source = entry->getString("source_path");
      if (!source || source->empty())
        return module.emitError(
                   "probe plan alias requires non-empty source_path: ")
               << probePlanPath;
      auto canonical = entry->getString("canonical_path");
      if (!canonical || canonical->empty())
        return module.emitError(
                   "probe plan alias requires non-empty canonical_path: ")
               << probePlanPath;
      out.probeAliases.push_back(
          ProbePlanAlias{canonical->str(), source->str()});
    }
  }
  if (!traceCodegenPlanPath.empty()) {
    auto fileOrErr = llvm::MemoryBuffer::getFile(traceCodegenPlanPath);
    if (!fileOrErr)
      return module.emitError("cannot read C++ trace codegen plan: ")
             << traceCodegenPlanPath;
    auto parsed = llvm::json::parse(fileOrErr.get()->getBuffer());
    if (!parsed || !parsed->getAsObject())
      return module.emitError("invalid C++ trace codegen plan JSON: ")
             << traceCodegenPlanPath;
    const auto *root = parsed->getAsObject();
    auto version = root->getInteger("version");
    const auto *traceModules = root->getObject("modules");
    if (!version || *version != 1 || !traceModules)
      return module.emitError(
          "C++ trace codegen plan requires version=1 and object `modules`");
    for (const auto &moduleEntry : *traceModules) {
      const auto *listed = moduleEntry.second.getAsArray();
      if (!listed)
        return module.emitError(
                   "C++ trace codegen module entry must be an array: ")
               << moduleEntry.first.str();
      llvm::StringSet<> &fields = out.traceFieldsByModule[moduleEntry.first];
      for (const llvm::json::Value &field : *listed) {
        auto name = field.getAsString();
        if (!name || name->empty())
          return module.emitError(
              "C++ trace codegen module fields must be non-empty strings");
        fields.insert(*name);
      }
    }
  }
  return success();
}

SmallVector<ProbePlanAlias> probeAliasesForTop(const ObservationPlans &plans,
                                               StringRef topSymbol) {
  // Only the plan whose top_symbol matches carries aliases for this
  // emission; canonical/source paths are instance-rooted (dut:...) and are
  // kept verbatim.
  if (StringRef(plans.probeTopSymbol) != topSymbol)
    return {};
  SmallVector<ProbePlanAlias> out = plans.probeAliases;
  llvm::sort(out, [](const ProbePlanAlias &a, const ProbePlanAlias &b) {
    return a.canonicalPath < b.canonicalPath;
  });
  return out;
}

} // namespace pyc
