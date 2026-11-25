#include "pyc/Emit/CppEmitter.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Dialect/PYC/PYCTypes.h"
#include "pyc/Transforms/ChangeDrivenSchedule.h"
#include "pyc/Transforms/StateOptimization.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Attributes.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Types.h"
#include "mlir/IR/Visitors.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/SmallSet.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/ADT/StringSet.h"
#include "llvm/ADT/StringExtras.h"
#include "llvm/Support/ErrorHandling.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cstdint>
#include <functional>
#include <map>
#include <tuple>
#include <limits>
#include <optional>
#include <string>
#include <thread>
#include <vector>

using namespace mlir;

namespace pyc {
namespace {

// A function argument feeds only sequential clock ports when every use is a
// clock operand, an alias/wire, or an instance port that is itself clock-only.
// Those values do not change combinational outputs, so the instance eval cache
// must not treat a clock toggle as a new input.
static bool isSequentialClockUse(OpOperand &use) {
  Operation *owner = use.getOwner();
  Value value = use.get();
  if (auto reg = dyn_cast<pyc::RegOp>(owner))
    return reg.getClk() == value;
  if (auto delay = dyn_cast<pyc::DelayLineOp>(owner))
    return delay.getClk() == value;
  if (auto mem = dyn_cast<pyc::SyncMemNMOp>(owner))
    return mem.getClk() == value;
  if (auto mem = dyn_cast<pyc::ByteMemOp>(owner))
    return mem.getClk() == value;
  if (auto fifo = dyn_cast<pyc::FifoOp>(owner))
    return fifo.getClk() == value;
  if (auto fifo = dyn_cast<pyc::AsyncFifoOp>(owner))
    return fifo.getInClk() == value || fifo.getOutClk() == value;
  if (auto cdc = dyn_cast<pyc::CdcSyncOp>(owner))
    return cdc.getClk() == value;
  return false;
}

static bool argumentIsClockOnly(
    func::FuncOp func, unsigned argIdx, ModuleOp module,
    llvm::DenseMap<std::pair<Operation *, unsigned>, bool> &memo) {
  auto key = std::make_pair(func.getOperation(), argIdx);
  if (auto it = memo.find(key); it != memo.end())
    return it->second;
  // Assume comb-sensitive while recursing so a cycle cannot skip a real cone.
  memo[key] = false;
  if (!func || func.isDeclaration() || func.getBody().empty() ||
      argIdx >= func.getNumArguments())
    return false;

  bool clockOnly = true;
  llvm::SmallVector<Value> work{func.getArgument(argIdx)};
  llvm::DenseSet<Value> seen;
  while (!work.empty() && clockOnly) {
    Value value = work.pop_back_val();
    if (!seen.insert(value).second)
      continue;
    for (OpOperand &use : value.getUses()) {
      Operation *user = use.getOwner();
      if (auto alias = dyn_cast<pyc::AliasOp>(user)) {
        work.push_back(alias.getResult());
        continue;
      }
      if (isSequentialClockUse(use))
        continue;
      if (auto inst = dyn_cast<pyc::InstanceOp>(user)) {
        auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
        func::FuncOp callee;
        if (calleeAttr)
          callee = module.lookupSymbol<func::FuncOp>(calleeAttr.getValue());
        if (!callee ||
            !argumentIsClockOnly(callee, use.getOperandNumber(), module, memo)) {
          clockOnly = false;
          break;
        }
        continue;
      }
      clockOnly = false;
      break;
    }
  }
  memo[key] = clockOnly;
  return clockOnly;
}

// ---------------------------------------------------------------------------
// placement attribute readers (used internally by the emitter)
// ---------------------------------------------------------------------------

// Above this many sibling instances the per-instance switch body of the fork
// group costs more I-cache than the fork saves, so the serial chunked emission
// is kept instead.
static constexpr unsigned kMaxParallelInstances = 32;

static CppEmitterOptions effectiveEmitOpts(ModuleOp module, const CppEmitterOptions &opts) {
  CppEmitterOptions out = opts;
  if (auto chunk = getModuleCombChunkNodes(module)) {
    out.combChunkNodes = *chunk;
    out.evalTopoChunkNodes = *chunk;
  }
  return out;
}

static LogicalResult requireCombChunkConfig(ModuleOp module) {
  if (getModuleCombChunkNodes(module))
    return success();
  return module.emitError(
      "missing pyc.cpp.comb_chunk_nodes; run pyc-cpp-placement before C++ emit");
}

// RegLoop grouping keys are (id * 4096 + width) * 2 + fused. Fused loops
// keep the legacy key name (id * 4096 + width) so their member names stay
// identical to the two-phase-only emitter output; phased sub-loops add a
// _phased suffix so the two never collide.
static std::string regLoopId(int64_t loopId) {
  std::string name = std::to_string(loopId >> 1);
  if ((loopId & 1) == 0)
    name += "_phased";
  return name;
}

static std::string regLoopName(int64_t loopId) {
  return "reg_loop_" + regLoopId(loopId);
}


static std::string sanitizeId(llvm::StringRef s) {
  std::string out;
  out.reserve(s.size() + 1);
  auto isAlpha = [](char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); };
  auto isDigit = [](char c) { return (c >= '0' && c <= '9'); };
  auto isOk = [&](char c) { return isAlpha(c) || isDigit(c) || c == '_'; };

  for (char c : s) {
    out.push_back(isOk(c) ? c : '_');
  }
  if (out.empty() || isDigit(out.front()))
    out.insert(out.begin(), '_');
  return out;
}

static std::string cppStringLiteral(llvm::StringRef s) {
  std::string out;
  out.reserve(s.size() + 2);
  out.push_back('"');
  for (char c : s) {
    switch (c) {
    case '\\':
      out += "\\\\\\\\";
      break;
    case '"':
      out += "\\\\\"";
      break;
    case '\n':
      out += "\\\\n";
      break;
    case '\r':
      out += "\\\\r";
      break;
    case '\t':
      out += "\\\\t";
      break;
    default:
      out.push_back(c);
      break;
    }
  }
  out.push_back('"');
  return out;
}

static std::string cppType(Type ty) {
  if (isa<pyc::ClockType>(ty) || isa<pyc::ResetType>(ty))
    return "pyc::cpp::Wire<1>";
  if (auto intTy = dyn_cast<IntegerType>(ty))
    return "pyc::cpp::Wire<" + std::to_string(intTy.getWidth()) + ">";
  std::string msg;
  llvm::raw_string_ostream os(msg);
  os << "CppEmitter: unsupported type in cppType(): " << ty;
  llvm::report_fatal_error(llvm::StringRef(os.str()));
}


static unsigned bitWidth(Type ty) {
  if (isa<pyc::ClockType>(ty) || isa<pyc::ResetType>(ty))
    return 1;
  if (auto intTy = dyn_cast<IntegerType>(ty))
    return intTy.getWidth();
  return 0;
}

static unsigned planeWords(Type ty) {
  return (bitWidth(ty) + 63u) / 64u;
}

// Raw-word expression for a value's storage.
static std::string firstLeafData(Type ty, const std::string &name) {
  (void)ty;
  return name + ".data()";
}

static std::size_t packedWireWordCount(Type ty) {
  if (isa<pyc::ClockType, pyc::ResetType>(ty))
    return 1;
  if (auto intTy = dyn_cast<IntegerType>(ty))
    return std::max<std::size_t>(1, (intTy.getWidth() + 63u) / 64u);
  return 0;
}

// Emit ProbeRegistry registration for a wire-kind value.
static void emitWireProbes(llvm::raw_ostream &os, Type ty, unsigned w,
                           const std::string &fieldPath, const std::string &cppExpr) {
  (void)ty;
  os << "    reg.addWire<" << w << ">(reg_path(" << cppStringLiteral(fieldPath) << "), &" << cppExpr << ");\n";
}

struct NameTable {
  llvm::DenseMap<Value, std::string> names;
  llvm::StringMap<unsigned> used;
  int next = 0;

  std::string unique(std::string base) {
    unsigned &n = used[base];
    n++;
    if (n == 1)
      return base;
    return base + "_" + std::to_string(n);
  }

  std::string get(Value v) {
    if (auto it = names.find(v); it != names.end())
      return it->second;
    if (Value target = getValueCppAliasTarget(v)) {
      std::string n = get(target);
      names.try_emplace(v, n);
      return n;
    }
    if (Operation *def = v.getDefiningOp()) {
      if (auto nAttr = def->getAttrOfType<StringAttr>("pyc.name")) {
        auto lazy = def->getAttrOfType<BoolAttr>("pyc.observe_lazy");
        if (!lazy || !lazy.getValue()) {
          std::string cand = unique(sanitizeId(nAttr.getValue()));
          names.try_emplace(v, cand);
          return cand;
        }
      }
      // Fall back to op-based names for readability (instead of v1/v2/...).
      std::string base = sanitizeId(def->getName().getStringRef());
      if (base.empty())
        base = "v";
      base += "_" + std::to_string(++next);
      std::string cand = unique(base);
      names.try_emplace(v, cand);
      return cand;
    }
    std::string n = unique("arg_" + std::to_string(++next));
    names.try_emplace(v, n);
    return n;
  }
};

struct EmissionScheduleNode {
  Operation *operation = nullptr;
  unsigned resultIndex = 0;
  uint64_t id = 0;
  uint64_t rank = 0;
  uint64_t slot = 0;
  llvm::SmallVector<uint64_t> fanout;
};

struct EmissionSchedule {
  llvm::SmallVector<EmissionScheduleNode> nodes;
  llvm::DenseMap<Value, unsigned> valueToNode;
};

static FailureOr<uint64_t> readScheduleUnsigned(Operation *op,
                                                StringRef attrName,
                                                unsigned resultIndex) {
  auto values = op->getAttrOfType<ArrayAttr>(attrName);
  if (!values || values.size() != op->getNumResults() ||
      resultIndex >= values.size())
    return op->emitError("C++ emitter requires valid per-result schedule attribute '")
           << attrName << "'";
  auto value = dyn_cast<IntegerAttr>(values[resultIndex]);
  if (!value || value.getInt() < 0)
    return op->emitError("C++ emitter requires unsigned values in schedule attribute '")
           << attrName << "'";
  return static_cast<uint64_t>(value.getInt());
}

/// Reads the verified pyc.change_schedule.v1 contract without rebuilding any
/// dependency edges. The emitter deliberately treats these attributes as the
/// sole semantic source for change-driven fanout.
static FailureOr<EmissionSchedule> readEmissionSchedule(func::FuncOp func) {
  auto summary =
      func->getAttrOfType<DictionaryAttr>(kChangeScheduleSummaryAttr);
  if (!summary)
    return func.emitError(
        "C++ emission requires pyc.change_schedule.summary");
  auto schema = summary.getAs<StringAttr>("schema");
  auto nodeCountAttr = summary.getAs<IntegerAttr>("node_count");
  auto edgeCountAttr = summary.getAs<IntegerAttr>("edge_count");
  auto rankCountAttr = summary.getAs<IntegerAttr>("rank_count");
  if (!schema || schema.getValue() != kChangeScheduleSchema ||
      !nodeCountAttr || nodeCountAttr.getInt() < 0 || !edgeCountAttr ||
      edgeCountAttr.getInt() < 0 || !rankCountAttr ||
      rankCountAttr.getInt() < 0)
    return func.emitError(
        "C++ emission requires valid pyc.change_schedule.v1 summary schema");

  const uint64_t nodeCount = static_cast<uint64_t>(nodeCountAttr.getInt());
  const uint64_t edgeCount = static_cast<uint64_t>(edgeCountAttr.getInt());
  const uint64_t rankCount = static_cast<uint64_t>(rankCountAttr.getInt());
  EmissionSchedule schedule;
  schedule.nodes.resize(nodeCount);
  llvm::SmallVector<bool> seen(nodeCount, false);
  llvm::SmallVector<bool> seenSlots(nodeCount, false);
  uint64_t actualEdgeCount = 0;
  uint64_t actualRankCount = 0;

  for (Block &block : func.getBody()) {
    for (Operation &op : block) {
      if (!isChangeScheduleNode(&op))
        continue;
      for (auto result : llvm::enumerate(op.getResults())) {
        unsigned resultIndex = static_cast<unsigned>(result.index());
        auto id = readScheduleUnsigned(&op, kChangeScheduleNodeAttr,
                                       resultIndex);
        auto rank = readScheduleUnsigned(&op, kChangeScheduleRankAttr,
                                         resultIndex);
        auto slot = readScheduleUnsigned(&op, kChangeScheduleSlotAttr,
                                         resultIndex);
        if (failed(id) || failed(rank) || failed(slot))
          return failure();
        if (*id >= nodeCount || *slot >= nodeCount || *rank >= rankCount ||
            seen[*id] || seenSlots[*slot])
          return op.emitError(
              "C++ emitter cannot map duplicate or out-of-range schedule node/rank/slot");

        auto allFanouts =
            op.getAttrOfType<ArrayAttr>(kChangeScheduleFanoutAttr);
        if (!allFanouts || allFanouts.size() != op.getNumResults())
          return op.emitError(
              "C++ emitter requires valid per-result schedule fanout arrays");
        auto fanout = dyn_cast<ArrayAttr>(allFanouts[resultIndex]);
        if (!fanout)
          return op.emitError(
              "C++ emitter requires each schedule fanout entry to be an array");

        EmissionScheduleNode node;
        node.operation = &op;
        node.resultIndex = resultIndex;
        node.id = *id;
        node.rank = *rank;
        node.slot = *slot;
        uint64_t previous = 0;
        bool hasPrevious = false;
        for (Attribute targetAttr : fanout) {
          auto target = dyn_cast<IntegerAttr>(targetAttr);
          if (!target || target.getInt() < 0 ||
              static_cast<uint64_t>(target.getInt()) >= nodeCount)
            return op.emitError(
                "C++ emitter cannot map invalid schedule fanout target");
          uint64_t targetId = static_cast<uint64_t>(target.getInt());
          if (targetId <= node.id || (hasPrevious && targetId <= previous))
            return op.emitError(
                "C++ emitter requires sorted forward schedule fanout");
          node.fanout.push_back(targetId);
          previous = targetId;
          hasPrevious = true;
        }
        actualEdgeCount += node.fanout.size();
        actualRankCount = std::max(actualRankCount, node.rank + 1);
        seen[node.id] = true;
        seenSlots[node.slot] = true;
        schedule.nodes[node.id] = std::move(node);
        schedule.valueToNode.try_emplace(result.value(),
                                         static_cast<unsigned>(*id));
      }
    }
  }

  if (llvm::any_of(seen, [](bool present) { return !present; }) ||
      llvm::any_of(seenSlots, [](bool present) { return !present; }) ||
      actualEdgeCount != edgeCount || actualRankCount != rankCount)
    return func.emitError(
        "C++ emitter schedule nodes do not match summary counts");
  return schedule;
}

static std::string getPortName(func::FuncOp f, unsigned idx, bool isResult) {
  if (!isResult) {
    if (auto names = f->getAttrOfType<ArrayAttr>("arg_names")) {
      if (idx < names.size())
        if (auto s = dyn_cast<StringAttr>(names[idx]))
          return sanitizeId(s.getValue());
    }
    return "arg" + std::to_string(idx);
  }
  if (auto names = f->getAttrOfType<ArrayAttr>("result_names")) {
    if (idx < names.size())
      if (auto s = dyn_cast<StringAttr>(names[idx]))
        return sanitizeId(s.getValue());
  }
  return "out" + std::to_string(idx);
}

static std::string getPortCanonicalFieldPath(func::FuncOp f, unsigned idx, bool isResult) {
  if (!isResult) {
    if (auto names = f->getAttrOfType<ArrayAttr>("arg_names")) {
      if (idx < names.size())
        if (auto s = dyn_cast<StringAttr>(names[idx]))
          return s.getValue().str();
    }
    return "arg" + std::to_string(idx);
  }
  if (auto names = f->getAttrOfType<ArrayAttr>("result_names")) {
    if (idx < names.size())
      if (auto s = dyn_cast<StringAttr>(names[idx]))
        return s.getValue().str();
  }
  return "out" + std::to_string(idx);
}

struct ProbeAliasEntry {
  std::string canonicalPath;
  std::string sourcePath;
};

static void computeUniquePortNames(func::FuncOp f, std::vector<std::string> &inNames, std::vector<std::string> &outNames) {
  NameTable nt;
  inNames.clear();
  outNames.clear();
  auto fTy = f.getFunctionType();
  unsigned numInputs = fTy.getNumInputs();
  unsigned numResults = fTy.getNumResults();
  inNames.reserve(numInputs);
  outNames.reserve(numResults);
  for (unsigned i = 0; i < numInputs; ++i) {
    inNames.push_back(nt.unique(getPortName(f, static_cast<unsigned>(i), /*isResult=*/false)));
  }
  for (unsigned i = 0; i < numResults; ++i) {
    outNames.push_back(nt.unique(getPortName(f, i, /*isResult=*/true)));
  }
}

enum class SeqStateKind : std::uint8_t {
  Unknown = 0,
  Visiting = 1,
  NoSequential = 2,
  HasSequential = 3,
};

static bool functionHasSequentialState(func::FuncOp f,
                                       ModuleOp mod,
                                       llvm::DenseMap<Operation *, SeqStateKind> &memo) {
  if (!f)
    return true;

  if (auto it = memo.find(f.getOperation()); it != memo.end()) {
    if (it->second == SeqStateKind::Visiting) {
      // Cyclic/self-recursive hierarchy is treated as sequential to stay safe.
      return true;
    }
    return it->second == SeqStateKind::HasSequential;
  }

  memo[f.getOperation()] = SeqStateKind::Visiting;
  bool hasSeq = false;

  Region *callable = f.getCallableRegion();
  auto bodyBlock = (!callable || callable->empty()) ? nullptr : &callable->front();
  if (!bodyBlock) {
    memo[f.getOperation()] = SeqStateKind::HasSequential;
    return true;
  }

  for (Operation &op : *bodyBlock) {
    if (isa<pyc::RegOp,
            pyc::DelayLineOp,
            pyc::FifoOp,
            pyc::ByteMemOp,
            pyc::SyncMemNMOp,
            pyc::AsyncFifoOp,
            pyc::CdcSyncOp>(op)) {
      hasSeq = true;
      break;
    }

    if (auto inst = dyn_cast<pyc::InstanceOp>(op)) {
      auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
      if (!calleeAttr) {
        hasSeq = true;
        break;
      }
      auto callee = mod.lookupSymbol<func::FuncOp>(calleeAttr.getValue());
      if (!callee) {
        hasSeq = true;
        break;
      }
      if (functionHasSequentialState(callee, mod, memo)) {
        hasSeq = true;
        break;
      }
    }
  }

  memo[f.getOperation()] = hasSeq ? SeqStateKind::HasSequential : SeqStateKind::NoSequential;
  return hasSeq;
}

static void assignExpr(Value result, Type ty, llvm::raw_ostream &os, NameTable &nt,
                       llvm::function_ref<void(llvm::raw_ostream &)> buildExpr,
                       CppEmitterPlacementState *ps) {
  std::string expr;
  llvm::raw_string_ostream ess(expr);
  buildExpr(ess);
  if (ps)
    ps->emitValueAssign(result, ty, nt.get(result), ess.str(), os);
  else
    os << "    " << nt.get(result) << " = " << ess.str() << ";\n";
}

inline const llvm::DenseMap<int64_t, llvm::SmallVector<pyc::DelayLineOp>>
    kEmptyDelayLoops{};

// Loop-aware delay tap read. Empty delayLoopLines = per-object expression.
static std::string delayTapReadExpr(
    pyc::DelayLineOp delay, unsigned depth, NameTable &nt,
    const llvm::DenseMap<int64_t, llvm::SmallVector<pyc::DelayLineOp>> &delayLoopLines) {
  const std::string inst = nt.get(delay.getQ()) + "_inst";
  if (auto idAttr = delay->getAttrOfType<IntegerAttr>("pyc.loop_id")) {
    const int64_t raw = idAttr.getValue().getSExtValue();
    const unsigned dw = bitWidth(delay.getQ().getType());
    if (auto da = delay->getAttrOfType<IntegerAttr>("depth")) {
      const int64_t key = raw * 256 + da.getValue().getZExtValue();
      auto it = delayLoopLines.find(key);
      if (it != delayLoopLines.end()) {
        long lane = 0;
        for (pyc::DelayLineOp c : it->second) {
          if (c == delay)
            break;
          ++lane;
        }
        return "delay_loop_" + std::to_string(key) + ".lane_tap<" +
               std::to_string(dw) + ">(" +
               std::to_string(lane) + "u, " + std::to_string(depth) + "u)";
      }
    }
  }
  return inst + "->tap(" + std::to_string(depth) + "u)";
}

static LogicalResult emitCombAssign(Operation &op, llvm::raw_ostream &os, NameTable &nt,
                                    CppEmitterPlacementState *ps = nullptr,
                                    const llvm::DenseMap<int64_t, llvm::SmallVector<pyc::DelayLineOp>> *delayLoopLines = nullptr) {
  if (ps) {
    for (Value v : op.getOperands())
      (void)ps->emitLocalDeclIfNeeded(v, v.getType(), nt.get(v), os);
  }
  if (auto c = dyn_cast<arith::ConstantOp>(op)) {
    auto value = dyn_cast<IntegerAttr>(c.getValue());
    unsigned w = bitWidth(c.getType());
    if (!value || w == 0)
      return c.emitError(
          "C++ emitter only supports integer arith.constant values");
    const llvm::APInt &bits = value.getValue();
    unsigned words = (w + 63u) / 64u;
    assignExpr(c.getResult(), c.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::Wire<" << w << ">({";
                 for (unsigned i = 0; i < words; ++i) {
                   if (i)
                     e << ", ";
                   e << "0x"
                     << llvm::utohexstr(bits.extractBitsAsZExtValue(
                            std::min(64u, w - i * 64u), i * 64u))
                     << "ull";
                 }
                 e << "})";
               },
               ps);
    return success();
  }
  if (auto c = dyn_cast<pyc::ConstantOp>(op)) {
    unsigned w = bitWidth(c.getType());
    if (w == 0)
      return c.emitError("invalid constant width");
    auto v = c.getValueAttr().getValue();
    unsigned words = (w + 63u) / 64u;
    assignExpr(c.getResult(), c.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::Wire<" << w << ">({";
                 for (unsigned i = 0; i < words; i++) {
                   if (i)
                     e << ", ";
                   std::uint64_t word = v.getRawData()[i];
                   e << "0x" << llvm::utohexstr(word) << "ull";
                 }
                 e << "})";
               },
               ps);
    return success();
  }
  if (auto a = dyn_cast<pyc::AliasOp>(op)) {
    if (getValueCppStorage(a.getResult()) == CppStorageKind::Alias)
      return success();
    assignExpr(a.getResult(), a.getType(), os, nt,
               [&](llvm::raw_ostream &e) { e << nt.get(a.getIn()); }, ps);
    return success();
  }
  if (auto ra = dyn_cast<pyc::ResetActiveOp>(op)) {
    assignExpr(ra.getActive(), ra.getActive().getType(), os, nt,
               [&](llvm::raw_ostream &e) { e << nt.get(ra.getRst()); }, ps);
    return success();
  }
  if (auto a = dyn_cast<pyc::AddOp>(op)) {
    assignExpr(a.getResult(), a.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "(" << nt.get(a.getLhs()) << " + " << nt.get(a.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto s = dyn_cast<pyc::SubOp>(op)) {
    assignExpr(s.getResult(), s.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "(" << nt.get(s.getLhs()) << " - " << nt.get(s.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto m = dyn_cast<pyc::MulOp>(op)) {
    assignExpr(m.getResult(), m.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "(" << nt.get(m.getLhs()) << " * " << nt.get(m.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto d = dyn_cast<pyc::UdivOp>(op)) {
    unsigned w = bitWidth(d.getResult().getType());
    if (w == 0)
      return d.emitError("invalid udiv width");
    assignExpr(d.getResult(), d.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::udiv<" << w << ">(" << nt.get(d.getLhs()) << ", " << nt.get(d.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto r = dyn_cast<pyc::UremOp>(op)) {
    unsigned w = bitWidth(r.getResult().getType());
    if (w == 0)
      return r.emitError("invalid urem width");
    assignExpr(r.getResult(), r.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::urem<" << w << ">(" << nt.get(r.getLhs()) << ", " << nt.get(r.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto d = dyn_cast<pyc::SdivOp>(op)) {
    unsigned w = bitWidth(d.getResult().getType());
    if (w == 0)
      return d.emitError("invalid sdiv width");
    assignExpr(d.getResult(), d.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::sdiv<" << w << ">(" << nt.get(d.getLhs()) << ", " << nt.get(d.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto r = dyn_cast<pyc::SremOp>(op)) {
    unsigned w = bitWidth(r.getResult().getType());
    if (w == 0)
      return r.emitError("invalid srem width");
    assignExpr(r.getResult(), r.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::srem<" << w << ">(" << nt.get(r.getLhs()) << ", " << nt.get(r.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto m = dyn_cast<pyc::MuxOp>(op)) {
    unsigned w = bitWidth(m.getResult().getType());
    if (w == 0)
      return m.emitError("invalid mux width");
    assignExpr(m.getResult(), m.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::mux<" << w << ">(" << nt.get(m.getSel()) << ", " << nt.get(m.getA()) << ", "
                   << nt.get(m.getB()) << ")";
               },
               ps);
    return success();
  }
  if (auto s = dyn_cast<arith::SelectOp>(op)) {
    if (!s.getCondition().getType().isInteger(1))
      return s.emitError("C++ emitter only supports arith.select with i1 condition");
    assignExpr(s.getResult(), s.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "(" << nt.get(s.getCondition()) << ".toBool() ? " << nt.get(s.getTrueValue()) << " : "
                   << nt.get(s.getFalseValue()) << ")";
               },
               ps);
    return success();
  }
  if (auto a = dyn_cast<pyc::AndOp>(op)) {
    assignExpr(a.getResult(), a.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "(" << nt.get(a.getLhs()) << " & " << nt.get(a.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto o = dyn_cast<pyc::OrOp>(op)) {
    assignExpr(o.getResult(), o.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "(" << nt.get(o.getLhs()) << " | " << nt.get(o.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto x = dyn_cast<pyc::XorOp>(op)) {
    assignExpr(x.getResult(), x.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "(" << nt.get(x.getLhs()) << " ^ " << nt.get(x.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto n = dyn_cast<pyc::NotOp>(op)) {
    assignExpr(n.getResult(), n.getType(), os, nt,
               [&](llvm::raw_ostream &e) { e << "(~" << nt.get(n.getIn()) << ")"; }, ps);
    return success();
  }
  if (auto e = dyn_cast<pyc::EqOp>(op)) {
    unsigned w = bitWidth(e.getLhs().getType());
    if (w == 0)
      return e.emitError("invalid eq width");
    assignExpr(e.getResult(), e.getType(), os, nt,
               [&](llvm::raw_ostream &eout) {
                 eout << "pyc::cpp::eq<" << w << ">(" << nt.get(e.getLhs()) << ", " << nt.get(e.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto u = dyn_cast<pyc::UltOp>(op)) {
    unsigned w = bitWidth(u.getLhs().getType());
    if (w == 0)
      return u.emitError("invalid ult width");
    assignExpr(u.getResult(), u.getType(), os, nt,
               [&](llvm::raw_ostream &eout) {
                 eout << "pyc::cpp::ult<" << w << ">(" << nt.get(u.getLhs()) << ", " << nt.get(u.getRhs()) << ")";
               },
               ps);
    return success();
  }
  if (auto s = dyn_cast<pyc::SltOp>(op)) {
    unsigned w = bitWidth(s.getLhs().getType());
    if (w == 0)
      return s.emitError("invalid slt width");
    assignExpr(s.getResult(), s.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::Wire<1>((pyc::cpp::slt<" << w << ">(" << nt.get(s.getLhs()) << ", "
                   << nt.get(s.getRhs()) << ")) ? 1u : 0u)";
               },
               ps);
    return success();
  }
  if (auto t = dyn_cast<pyc::TruncOp>(op)) {
    unsigned iw = bitWidth(t.getIn().getType());
    unsigned ow = bitWidth(t.getResult().getType());
    if (iw == 0 || ow == 0)
      return t.emitError("invalid trunc width");
    assignExpr(t.getResult(), t.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::trunc<" << ow << ", " << iw << ">(" << nt.get(t.getIn()) << ")";
               },
               ps);
    return success();
  }
  if (auto z = dyn_cast<pyc::ZextOp>(op)) {
    unsigned iw = bitWidth(z.getIn().getType());
    unsigned ow = bitWidth(z.getResult().getType());
    if (iw == 0 || ow == 0)
      return z.emitError("invalid zext width");
    assignExpr(z.getResult(), z.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::zext<" << ow << ", " << iw << ">(" << nt.get(z.getIn()) << ")";
               },
               ps);
    return success();
  }
  if (auto s = dyn_cast<pyc::SextOp>(op)) {
    unsigned iw = bitWidth(s.getIn().getType());
    unsigned ow = bitWidth(s.getResult().getType());
    if (iw == 0 || ow == 0)
      return s.emitError("invalid sext width");
    assignExpr(s.getResult(), s.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::sext<" << ow << ", " << iw << ">(" << nt.get(s.getIn()) << ")";
               },
               ps);
    return success();
  }
  if (auto ex = dyn_cast<pyc::ExtractOp>(op)) {
    unsigned iw = bitWidth(ex.getIn().getType());
    unsigned ow = bitWidth(ex.getResult().getType());
    if (iw == 0 || ow == 0)
      return ex.emitError("invalid extract width");
    assignExpr(ex.getResult(), ex.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::extract<" << ow << ", " << iw << ">(" << nt.get(ex.getIn()) << ", "
                   << ex.getLsbAttr().getInt() << "u)";
               },
               ps);
    return success();
  }
  if (auto tap = dyn_cast<pyc::DelayTapOp>(op)) {
    auto delay = tap.getLine().getDefiningOp<pyc::DelayLineOp>();
    auto depth = tap->getAttrOfType<IntegerAttr>("depth");
    if (!delay || !depth)
      return tap.emitError("invalid delay tap source or depth");
    assignExpr(tap.getTap(), tap.getTap().getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << delayTapReadExpr(delay,
                                       static_cast<unsigned>(depth.getInt()),
                                       nt,
                                       delayLoopLines ? *delayLoopLines
                                                   : kEmptyDelayLoops);
               },
               ps);
    return success();
  }
  if (auto sh = dyn_cast<pyc::ShliOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0)
      return sh.emitError("invalid shli width");
    assignExpr(sh.getResult(), sh.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::shl<" << w << ">(" << nt.get(sh.getIn()) << ", " << sh.getAmountAttr().getInt()
                   << "u)";
               },
               ps);
    return success();
  }
  if (auto sh = dyn_cast<pyc::LshriOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0)
      return sh.emitError("invalid lshri width");
    assignExpr(sh.getResult(), sh.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::lshr<" << w << ">(" << nt.get(sh.getIn()) << ", " << sh.getAmountAttr().getInt()
                   << "u)";
               },
               ps);
    return success();
  }
  if (auto sh = dyn_cast<pyc::AshriOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0)
      return sh.emitError("invalid ashri width");
    assignExpr(sh.getResult(), sh.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::ashr<" << w << ">(" << nt.get(sh.getIn()) << ", " << sh.getAmountAttr().getInt()
                   << "u)";
               },
               ps);
    return success();
  }
  if (auto sh = dyn_cast<pyc::ShlOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0)
      return sh.emitError("invalid shl width");
    assignExpr(sh.getResult(), sh.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::shl<" << w << ">(" << nt.get(sh.getIn()) << ", static_cast<unsigned>("
                   << nt.get(sh.getAmount()) << ".value()))";
               },
               ps);
    return success();
  }
  if (auto sh = dyn_cast<pyc::LshrOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0)
      return sh.emitError("invalid lshr width");
    assignExpr(sh.getResult(), sh.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::lshr<" << w << ">(" << nt.get(sh.getIn()) << ", static_cast<unsigned>("
                   << nt.get(sh.getAmount()) << ".value()))";
               },
               ps);
    return success();
  }
  if (auto sh = dyn_cast<pyc::AshrOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0)
      return sh.emitError("invalid ashr width");
    assignExpr(sh.getResult(), sh.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::ashr<" << w << ">(" << nt.get(sh.getIn()) << ", static_cast<unsigned>("
                   << nt.get(sh.getAmount()) << ".value()))";
               },
               ps);
    return success();
  }
  if (auto c = dyn_cast<pyc::ConcatOp>(op)) {
    unsigned w = bitWidth(c.getResult().getType());
    if (w == 0)
      return c.emitError("invalid concat width");
    assignExpr(c.getResult(), c.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << "pyc::cpp::concat(";
                 for (auto [i, v] : llvm::enumerate(c.getInputs())) {
                   if (i)
                     e << ", ";
                   e << nt.get(v);
                 }
                 e << ")";
               },
               ps);
    return success();
  }
  return op.emitError("unsupported combinational op for C++ emission");
}

// Fanout wake of `value` as sorted (comb dirty word, OR mask) pairs, merged
// per word: up to 64 combs sharing a dirty word collapse to one store.
// Appends to `out` without merging across prior entries.
static void collectFanoutWordMasks(
    Value value, const EmissionSchedule &schedule,
    const llvm::DenseMap<Operation *, unsigned> &combIndices,
    llvm::SmallVectorImpl<std::pair<unsigned, uint64_t>> &out) {
  auto sourceIt = schedule.valueToNode.find(value);
  if (sourceIt == schedule.valueToNode.end())
    return;

  llvm::SmallSet<unsigned, 8> uniqueFanouts;
  for (uint64_t targetId : schedule.nodes[sourceIt->second].fanout) {
    const EmissionScheduleNode &target = schedule.nodes[targetId];
    if (!isa<pyc::CombOp>(target.operation))
      continue;
    auto it = combIndices.find(target.operation);
    if (it != combIndices.end())
      uniqueFanouts.insert(it->second);
  }
  llvm::SmallVector<unsigned> fanouts(uniqueFanouts.begin(),
                                      uniqueFanouts.end());
  llvm::sort(fanouts);
  const std::size_t start = out.size();
  for (unsigned fanout : fanouts) {
    unsigned word = fanout / 64;
    uint64_t mask = uint64_t{1} << (fanout % 64);
    if (out.size() > start && out.back().first == word)
      out.back().second |= mask;
    else
      out.emplace_back(word, mask);
  }
}

static void emitMetadataCombFanout(
    Value value, llvm::raw_ostream &os, const EmissionSchedule &schedule,
    const llvm::DenseMap<Operation *, unsigned> &combIndices, NameTable &nt,
    llvm::StringRef indent = "      ", bool deferredWake = false) {
  llvm::SmallVector<std::pair<unsigned, uint64_t>> wordMasks;
  collectFanoutWordMasks(value, schedule, combIndices, wordMasks);
  if (wordMasks.empty())
    return;
  std::string fanoutName = "_pyc_direct_fanout_" + sanitizeId(nt.get(value));
  os << indent << "static constexpr std::array<std::pair<unsigned, std::uint64_t>, "
     << wordMasks.size() << "> " << fanoutName << "{{";
  llvm::interleaveComma(wordMasks, os, [&](const auto &wm) {
    os << "{" << wm.first << "u, 0x" << llvm::utohexstr(wm.second) << "ull}";
  });
  os << "}};\n";
  os << indent << "for (const auto &[_pyc_w, _pyc_m] : " << fanoutName << ")\n";
  if (deferredWake)
    os << indent << "  _pyc_wake[_pyc_w] |= _pyc_m;\n";
  else
    os << indent << "  _pyc_comb_dirty.markWordUnchecked(_pyc_w, _pyc_m);\n";
}

static bool isDirtyDrivenInput(Value input) {
  Operation *producer = input.getDefiningOp();
  return producer && isChangeScheduleNode(producer);
}

static bool hasDirtyPolledBoundaryInput(pyc::CombOp comb) {
  return llvm::any_of(comb.getInputs(), [](Value input) {
    return !isDirtyDrivenInput(input);
  });
}

static void emitCombInputGuard(
    pyc::CombOp comb, llvm::raw_ostream &os, NameTable &nt, unsigned idx) {
  os << "    bool _pyc_comb_should_eval = _pyc_comb_dirty.take(" << idx
     << "u);\n";
  os << "    bool _pyc_comb_inputs_changed = _pyc_comb_should_eval;\n";
  for (auto [inputIndex, input] : llvm::enumerate(comb.getInputs())) {
    if (isDirtyDrivenInput(input))
      continue;
    std::string cacheName = "_pyc_comb_" + std::to_string(idx) + "_input_" +
                            std::to_string(inputIndex);
    os << "    if (" << cacheName << " != " << nt.get(input) << ") {\n";
    os << "      " << cacheName << " = " << nt.get(input) << ";\n";
    os << "      _pyc_comb_inputs_changed = true;\n";
    os << "    }\n";
  }
  os << "    if (!_pyc_comb_inputs_changed) {\n";
  os << "      return;\n";
  os << "    }\n";
}

static void emitCombResultPublish(
    pyc::CombOp comb, unsigned combIndex, unsigned resultIndex, Value candidate,
    llvm::raw_ostream &os, NameTable &nt, const EmissionSchedule &schedule,
    const llvm::DenseMap<Operation *, unsigned> &combIndices) {
  Value result = comb.getResult(resultIndex);
  std::string resultName = nt.get(result);
  std::string candidateName = "_pyc_comb_" + std::to_string(combIndex) +
                              "_candidate_" + std::to_string(resultIndex);
  os << "    const auto &" << candidateName << " = " << nt.get(candidate)
     << ";\n";
  os << "    if (" << resultName << " != " << candidateName << ") {\n";
  os << "      " << resultName << " = " << candidateName << ";\n";
  emitMetadataCombFanout(result, os, schedule, combIndices, nt);
  os << "    }\n";
}

static LogicalResult emitCombMethod(pyc::CombOp comb,
                                    llvm::raw_ostream &os,
                                    NameTable &nt,
                                    unsigned idx,
                                    const CppEmitterOptions &opts,
                                    const EmissionSchedule &schedule,
                                    const llvm::DenseMap<Operation *, unsigned>
                                        &combIndices,
                                    const llvm::DenseMap<int64_t, llvm::SmallVector<pyc::DelayLineOp>>
                                        *delayLoopLines = nullptr) {
  if (comb.getBody().empty())
    return comb.emitError("pyc.comb must have a non-empty region");

  Block &b = comb.getBody().front();
  if (b.getNumArguments() != comb.getNumOperands())
    return comb.emitError("pyc.comb body block argument count must match inputs");

  // Map region args to the corresponding input values (by name).
  for (auto [i, arg] : llvm::enumerate(b.getArguments()))
    nt.names.try_emplace(arg, nt.get(comb.getInputs()[i]));

  llvm::SmallVector<Operation *> combOps;
  combOps.reserve(b.getOperations().size());
  for (Operation &op : b) {
    if (isa<pyc::YieldOp>(op))
      break;
    combOps.push_back(&op);
  }

  CppEmitterPlacementState placement;
  unsigned combChunkNodes = std::max(1u, opts.combChunkNodes);
  if (combOps.size() > combChunkNodes) {
    // Placement is always-on: part ownership comes from pyc.cpp.method attrs.
    std::vector<std::pair<std::string, llvm::SmallVector<Operation *>>> parts;
    for (Operation *op : combOps) {
      auto method = op->getAttrOfType<StringAttr>(kCppMethodAttr);
      if (!method)
        return op->emitError("missing pyc.cpp.method; run pyc-cpp-placement before C++ emit");
      if (parts.empty() || parts.back().first != method.getValue())
        parts.emplace_back(method.getValue().str(), llvm::SmallVector<Operation *>());
      parts.back().second.push_back(op);
    }

    std::vector<std::string> partMethods;
    partMethods.reserve(parts.size());
    for (auto &part : parts) {
      const std::string &partName = part.first;
      partMethods.push_back(partName);
      os << "  inline void " << partName << "() {\n";
      placement.beginMethod(partName);
      for (Operation *op : part.second) {
        if (failed(emitCombAssign(*op, os, nt, &placement, delayLoopLines)))
          return failure();
      }
      os << "  }\n\n";
    }

    os << "  inline void eval_comb_" << idx << "() {\n";
    emitCombInputGuard(comb, os, nt, idx);
    for (const std::string &partName : partMethods)
      os << "    " << partName << "();\n";

    auto y = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
    if (!y)
      return comb.emitError("pyc.comb must terminate with pyc.yield");
    if (y.getNumOperands() != comb.getNumResults())
      return comb.emitError("pyc.yield operand count must match pyc.comb results");

    for (auto [i, v] : llvm::enumerate(y.getOperands()))
      emitCombResultPublish(comb, idx, static_cast<unsigned>(i), v, os, nt,
                            schedule, combIndices);
    os << "  }\n\n";
    return success();
  }

  std::string methodName = "eval_comb_" + std::to_string(idx);
  os << "  inline void " << methodName << "() {\n";
  placement.beginMethod(methodName);
  emitCombInputGuard(comb, os, nt, idx);
  for (Operation *op : combOps) {
    if (failed(emitCombAssign(*op, os, nt, &placement, delayLoopLines)))
      return failure();
  }

  auto y = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
  if (!y)
    return comb.emitError("pyc.comb must terminate with pyc.yield");
  if (y.getNumOperands() != comb.getNumResults())
    return comb.emitError("pyc.yield operand count must match pyc.comb results");

  for (auto [i, v] : llvm::enumerate(y.getOperands()))
    emitCombResultPublish(comb, idx, static_cast<unsigned>(i), v, os, nt,
                          schedule, combIndices);
  os << "  }\n\n";
  return success();
}

static LogicalResult emitFunc(func::FuncOp f, llvm::raw_ostream &os,
                              const CppEmitterOptions &opts,
                              const ObservationPlans &probePlans) {
  NameTable nt;

  if (!f.isDeclaration() && !getFuncPlacementSummary(f))
      return f.emitError(
          "C++ member placement missing; run pyc-cpp-placement before emit");

  if (!llvm::hasSingleElement(f.getBody()))
    return f.emitError("C++ emitter currently supports single-block functions only");
  auto schedule = readEmissionSchedule(f);
  if (failed(schedule))
    return failure();

  Block &top = f.getBody().front();

  std::string structName = sanitizeId(f.getSymName());
  os << "struct " << structName << " {\n";
  os << "  static constexpr const char *_pyc_change_schedule_schema = \""
     << kChangeScheduleSchema << "\";\n";
  os << "  static constexpr unsigned _pyc_change_schedule_node_count = "
     << schedule->nodes.size() << "u;\n";
  os << "  static constexpr std::array<unsigned, " << schedule->nodes.size()
     << "> _pyc_change_schedule_rank{{";
  llvm::interleaveComma(schedule->nodes, os, [&](const EmissionScheduleNode &node) {
    os << node.rank << "u";
  });
  os << "}};\n";
  os << "  static constexpr std::array<unsigned, " << schedule->nodes.size()
     << "> _pyc_change_schedule_slot{{";
  llvm::interleaveComma(schedule->nodes, os, [&](const EmissionScheduleNode &node) {
    os << node.slot << "u";
  });
  os << "}};\n\n";

  // Ports.
  std::vector<std::string> inNames;
  inNames.reserve(f.getNumArguments());
  std::vector<std::string> inCanon;
  inCanon.reserve(f.getNumArguments());
  std::vector<std::string> outNames;
  outNames.reserve(f.getNumResults());
  std::vector<std::string> outCanon;
  outCanon.reserve(f.getNumResults());
  for (auto [i, arg] : llvm::enumerate(f.getArguments())) {
    inCanon.push_back(getPortCanonicalFieldPath(f, i, /*isResult=*/false));
    std::string name = nt.unique(getPortName(f, i, /*isResult=*/false));
    inNames.push_back(name);
    nt.names.try_emplace(arg, name);
    os << "  " << cppType(arg.getType()) << " " << name << "{};\n";
  }
  for (unsigned i = 0; i < f.getNumResults(); ++i) {
    outCanon.push_back(getPortCanonicalFieldPath(f, i, /*isResult=*/true));
    std::string name = nt.unique(getPortName(f, i, /*isResult=*/true));
    outNames.push_back(name);
    os << "  " << cppType(f.getResultTypes()[i]) << " " << name << "{};\n";
  }
  os << "\n";

  // Internal wires for op results (including inside pyc.comb regions).
  struct Decl {
    std::string name;
    Type ty;
  };
  std::vector<Decl> decls;
  decls.reserve(256);
  f.walk([&](Operation *op) {
    for (Value r : op->getResults()) {
      if (getValueCppStorage(r) != CppStorageKind::Struct)
        continue;
      decls.push_back(Decl{nt.get(r), r.getType()});
    }
  });
  std::sort(decls.begin(), decls.end(), [](const Decl &a, const Decl &b) { return a.name < b.name; });
  for (const Decl &d : decls)
    os << "  " << cppType(d.ty) << " " << d.name << "{};\n";
  os << "\n";

  // Sequential primitive instances.
  llvm::SmallVector<pyc::RegOp> regs;
  llvm::SmallVector<pyc::DelayLineOp> delayLines;
  llvm::SmallVector<pyc::DelayTapOp> delayTaps;
  llvm::SmallVector<pyc::FifoOp> fifos;
  llvm::SmallVector<pyc::ByteMemOp> byteMems;
  llvm::SmallVector<pyc::SyncMemNMOp> syncMemNMs;
  llvm::SmallVector<pyc::AsyncFifoOp> asyncFifos;
  llvm::SmallVector<pyc::CdcSyncOp> cdcSyncs;
  llvm::SmallVector<pyc::InstanceOp> instances;
  llvm::SmallVector<pyc::CombOp> combs;

  for (Operation &op : top) {
    if (auto r = dyn_cast<pyc::RegOp>(op))
      regs.push_back(r);
    else if (auto delay = dyn_cast<pyc::DelayLineOp>(op))
      delayLines.push_back(delay);
    else if (auto tap = dyn_cast<pyc::DelayTapOp>(op))
      delayTaps.push_back(tap);
    else if (auto fifo = dyn_cast<pyc::FifoOp>(op))
      fifos.push_back(fifo);
    else if (auto mem = dyn_cast<pyc::ByteMemOp>(op))
      byteMems.push_back(mem);
    else if (auto mem = dyn_cast<pyc::SyncMemNMOp>(op))
      syncMemNMs.push_back(mem);
    else if (auto fifo = dyn_cast<pyc::AsyncFifoOp>(op))
      asyncFifos.push_back(fifo);
    else if (auto s = dyn_cast<pyc::CdcSyncOp>(op))
      cdcSyncs.push_back(s);
    else if (auto inst = dyn_cast<pyc::InstanceOp>(op))
      instances.push_back(inst);
    else if (auto comb = dyn_cast<pyc::CombOp>(op))
      combs.push_back(comb);
  }
  for (pyc::DelayTapOp tap : delayTaps)
    os << "  " << cppType(tap.getTap().getType()) << " " << nt.get(tap.getTap())
       << "_qNext{};\n";

  // Tick-fuse (compute/commit merge) classification. A reg is direct-write-safe
  // iff its d/rst/en do not depend, through the value graph, on any state
  // output of this module (reg/delay q and tap, mem/fifo/cdc outputs,
  // instance results): those only change at their own commit, so reading them
  // in another element's single-phase update is an ordering hazard. Comb
  // results, top ports and pure ops over them are stable snapshots for this
  // edge. Only enabled when every reg/delay shares one clock (fast edge).
  llvm::DenseSet<Value> stateOutputs;
  for (auto r : regs)
    stateOutputs.insert(r.getQ());
  for (auto delay : delayLines)
    stateOutputs.insert(delay.getQ());
  for (auto tap : delayTaps)
    stateOutputs.insert(tap.getTap());
  for (auto fifo : fifos) {
    stateOutputs.insert(fifo.getInReady());
    stateOutputs.insert(fifo.getOutValid());
    stateOutputs.insert(fifo.getOutData());
  }
  for (auto mem : byteMems)
    stateOutputs.insert(mem.getRdata());
  for (auto mem : syncMemNMs)
    for (auto rd : mem.getRdatas())
      stateOutputs.insert(rd);
  for (auto fifo : asyncFifos) {
    stateOutputs.insert(fifo.getInReady());
    stateOutputs.insert(fifo.getOutValid());
    stateOutputs.insert(fifo.getOutData());
  }
  for (auto sync : cdcSyncs)
    stateOutputs.insert(sync.getOut());
  for (auto inst : instances)
    for (Value out : inst.getResults())
      stateOutputs.insert(out);
  auto dependsOnState = [&](Value root) -> bool {
    llvm::SmallSet<Operation *, 32> visited;
    llvm::SmallVector<Value> work{root};
    while (!work.empty()) {
      Value v = work.pop_back_val();
      if (stateOutputs.count(v))
        return true;
      Operation *def = v.getDefiningOp();
      if (!def || isa<pyc::CombOp>(def))
        continue; // top port, or comb boundary evaluated before the edge
      if (!visited.insert(def).second)
        continue;
      for (Value operand : def->getOperands())
        work.push_back(operand);
    }
    return false;
  };
  bool tickFuseSingleClock = true;
  std::string tickFuseClk;
  auto noteTickFuseClk = [&](Value clk) {
    std::string name = nt.get(clk);
    if (tickFuseClk.empty())
      tickFuseClk = std::move(name);
    else if (tickFuseClk != name)
      tickFuseSingleClock = false;
  };
  for (auto r : regs)
    noteTickFuseClk(r.getClk());
  for (auto delay : delayLines)
    noteTickFuseClk(delay.getClk());
  llvm::DenseSet<Operation *> fusedRegOps;
  if (tickFuseSingleClock && !tickFuseClk.empty())
    for (auto r : regs)
      if (!dependsOnState(r.getNext()) && !dependsOnState(r.getRst()) &&
          !dependsOnState(r.getEn()))
        fusedRegOps.insert(r.getOperation());

  // RegLoop grouping: scalar regs carrying pyc.loop_id lower into per-loop
  // width-erased pyc::cpp::RegLoop members; everything else keeps the
  // per-reg expansion. Map: loop id -> regs in IR order (widths may mix).
  llvm::DenseSet<int64_t> fusedRegLoops; // loops whose lanes are all fused
  llvm::DenseMap<int64_t, llvm::SmallVector<pyc::RegOp>> regLoopRegs;
  llvm::SmallVector<pyc::RegOp> unloopedRegs;
  // Control refinement: lanes share one RegLoop only when their (rst, en)
  // wires match too (clk is already the loop id), so every emitted loop has
  // uniform control pointers and the runtime sharedCtrl_ fast path always
  // applies. Groups are numbered by first appearance; the rewritten
  // pyc.loop_id below keeps every consumer consistent.
  std::map<std::tuple<int64_t, const void *, const void *>, int64_t>
      regCtrlGroups;
  for (pyc::RegOp r : regs) {
    auto idAttr = r->getAttrOfType<IntegerAttr>("pyc.loop_id");
    if (!idAttr) {
      unloopedRegs.push_back(r);
      continue;
    }
    const int64_t id = idAttr.getValue().getSExtValue();
    // Fused (single-phase) and phased lanes live in separate loops; widths
    // mix inside one width-erased RegLoopDyn (per-lane word count).
    const bool fused = fusedRegOps.count(r.getOperation());
    const auto cgKey = std::make_tuple(
        id, r.getRst().getAsOpaquePointer(), r.getEn().getAsOpaquePointer());
    const auto [git, inserted] = regCtrlGroups.try_emplace(
        cgKey, static_cast<int64_t>(regCtrlGroups.size()));
    const int64_t key = git->second * 2 + fused;
    if (fused)
      fusedRegLoops.insert(key);
    regLoopRegs[key].push_back(r);
    r->setAttr("pyc.loop_id",
               IntegerAttr::get(IntegerType::get(r->getContext(), 64), key));
  }
  llvm::SmallVector<int64_t> regLoopIds;
  for (auto &kv : regLoopRegs)
    regLoopIds.push_back(kv.first);
  std::sort(regLoopIds.begin(), regLoopIds.end());
  // Loops that keep the two-phase compute/commit protocol.
  llvm::SmallVector<int64_t> phasedRegLoopIds;
  llvm::SmallVector<int64_t> fusedRegLoopIds;
  for (int64_t loopId : regLoopIds)
    (fusedRegLoops.count(loopId) ? fusedRegLoopIds : phasedRegLoopIds)
        .push_back(loopId);
  // Rewrite the reg list so the per-reg paths skip looped regs.
  regs.clear();
  for (pyc::RegOp r : unloopedRegs)
    regs.push_back(r);

  // DelayLoop buckets: key = (loop_id, depth); width is per-lane now.
  llvm::DenseMap<int64_t, llvm::SmallVector<pyc::DelayLineOp>> delayLoopLines;
  llvm::SmallVector<pyc::DelayLineOp> unloopedDelays;
  // Same control refinement as RegLoop: (rst, en) must match within one
  // DelayLoop. The rewritten pyc.loop_id carries the refined group id, so
  // every raw*256+depth recomputation downstream stays consistent.
  std::map<std::tuple<int64_t, const void *, const void *>, int64_t>
      delayCtrlGroups;
  for (pyc::DelayLineOp dl : delayLines) {
    auto idAttr = dl->getAttrOfType<IntegerAttr>("pyc.loop_id");
    auto depthAttr = dl->getAttrOfType<IntegerAttr>("depth");
    if (!idAttr || !depthAttr) {
      unloopedDelays.push_back(dl);
      continue;
    }
    const int64_t raw = idAttr.getValue().getSExtValue();
    const auto cgKey = std::make_tuple(
        raw, dl.getRst().getAsOpaquePointer(), dl.getEn().getAsOpaquePointer());
    const auto [git, inserted] = delayCtrlGroups.try_emplace(
        cgKey, static_cast<int64_t>(delayCtrlGroups.size()));
    dl->setAttr("pyc.loop_id",
                IntegerAttr::get(IntegerType::get(dl->getContext(), 64),
                                 git->second));
    const int64_t key =
        git->second * 256 + depthAttr.getValue().getZExtValue();
    delayLoopLines[key].push_back(dl);
  }
  llvm::SmallVector<int64_t> delayLoopIds;
  for (auto &kv : delayLoopLines)
    delayLoopIds.push_back(kv.first);
  std::sort(delayLoopIds.begin(), delayLoopIds.end());
  delayLines.clear();
  for (pyc::DelayLineOp dl : unloopedDelays)
    delayLines.push_back(dl);

  auto regKey = [&](pyc::RegOp r) { return nt.get(r.getQ()); };
  // DelayLoop-aware binding text: {inst, pending, qNext}. inst is empty when
  // looped; callers use the accessors (pointer-returning) verbatim.
  auto delayBinding = [&](pyc::DelayLineOp delay)
      -> std::tuple<std::string, std::string, std::string> {
    const std::string inst = nt.get(delay.getQ()) + "_inst";
    if (auto idAttr = delay->getAttrOfType<IntegerAttr>("pyc.loop_id")) {
      const int64_t raw = idAttr.getValue().getSExtValue();
      const unsigned dw = bitWidth(delay.getQ().getType());
      if (auto da = delay->getAttrOfType<IntegerAttr>("depth")) {
        const int64_t key = raw * 256 + da.getValue().getZExtValue();
        auto it = delayLoopLines.find(key);
        if (it != delayLoopLines.end()) {
          long lane = 0;
          for (pyc::DelayLineOp c : it->second) {
            if (c == delay)
              break;
            ++lane;
          }
          const std::string loop = "delay_loop_" + std::to_string(key);
          return {"", loop + ".lane_pending(" + std::to_string(lane) + "u)",
                  "reinterpret_cast<pyc::cpp::Wire<" + std::to_string(dw) +
                      "> *>(" + loop + ".lane_qnext_raw(" +
                      std::to_string(lane) + "u))"};
        }
      }
    }
    return {inst, inst + "->pending", inst + "->qNext"};
  };
  // Probe write-intent binding for a scalar reg: either its pycReg inst
  // (unlooped) or the owning RegLoop's lane storage. Returns the C++ text
  // for `&...pending` and the write-data storage. RegLoop lanes report q
  // itself (read after commit); unlooped regs keep the two-phase qNext.
  auto probePendingNext = [&](pyc::RegOp r) -> std::pair<std::string, std::string> {
    if (auto idAttr = r->getAttrOfType<IntegerAttr>("pyc.loop_id")) {
      const int64_t id = idAttr.getValue().getSExtValue();
      // Lane index within the loop = position in regLoopRegs[id].
      long lane = 0;
      if (auto it = regLoopRegs.find(id); it != regLoopRegs.end()) {
        for (pyc::RegOp cand : it->second) {
          if (cand == r)
            break;
          ++lane;
        }
      }
      std::string loop = regLoopName(id);
      // lane_pending/lane_write_data return pointers; keep the '&' at the
      // call site out of this string (callers concatenate it verbatim
      // after &). lane_write_data is the lane's own q storage, so it holds
      // the written value once commit ran; the cast reinterprets that
      // standard-layout storage as the value (a scalar Wire<W>).
      const std::string valTy = "pyc::cpp::Wire<" +
                               std::to_string(bitWidth(r.getQ().getType())) + ">";
      return {loop + ".lane_pending(" + std::to_string(lane) + "u)",
              "reinterpret_cast<" + valTy + " *>(" + loop +
                  ".lane_write_data(" + std::to_string(lane) + "u))"};
    }
    return {nt.get(r.getQ()) + "_inst->pending",
            nt.get(r.getQ()) + "_inst->qNext"};
  };
  auto delayKey = [&](pyc::DelayLineOp delay) { return nt.get(delay.getQ()); };
  auto delayTapKey = [&](pyc::DelayTapOp tap) { return nt.get(tap.getTap()); };
  auto fifoKey = [&](pyc::FifoOp f) { return nt.get(f.getInReady()); };
  auto memKey = [&](pyc::ByteMemOp m) -> std::string {
    if (auto nameAttr = m->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(m.getRdata());
  };
  llvm::DenseMap<Operation *, std::string> syncMemNMInstName;  // NM names, filled post-sort
  auto syncMemNMKey = [&](pyc::SyncMemNMOp m) -> std::string {
    if (auto nameAttr = m->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return m.getRdatas().empty() ? "" : nt.get(*m.getRdatas().begin());
  };
  auto cdcKey = [&](pyc::CdcSyncOp s) { return nt.get(s.getOut()); };

  std::sort(regs.begin(), regs.end(), [&](pyc::RegOp a, pyc::RegOp b) { return regKey(a) < regKey(b); });
  std::sort(delayLines.begin(), delayLines.end(), [&](pyc::DelayLineOp a, pyc::DelayLineOp b) {
    return delayKey(a) < delayKey(b);
  });
  std::sort(delayTaps.begin(), delayTaps.end(), [&](pyc::DelayTapOp a, pyc::DelayTapOp b) {
    return delayTapKey(a) < delayTapKey(b);
  });
  std::sort(fifos.begin(), fifos.end(), [&](pyc::FifoOp a, pyc::FifoOp b) { return fifoKey(a) < fifoKey(b); });
  std::sort(byteMems.begin(), byteMems.end(), [&](pyc::ByteMemOp a, pyc::ByteMemOp b) { return memKey(a) < memKey(b); });
  std::sort(syncMemNMs.begin(), syncMemNMs.end(), [&](pyc::SyncMemNMOp a, pyc::SyncMemNMOp b) { return syncMemNMKey(a) < syncMemNMKey(b); });
  {
    unsigned nmSeq = 0;
    for (auto mem : syncMemNMs) {
      std::string instName = "m" + std::to_string(nmSeq++) + "_inst";
      if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
        instName = sanitizeId(nameAttr.getValue());
      syncMemNMInstName.try_emplace(mem.getOperation(), instName);
    }
  }
  std::sort(cdcSyncs.begin(), cdcSyncs.end(), [&](pyc::CdcSyncOp a, pyc::CdcSyncOp b) { return cdcKey(a) < cdcKey(b); });
  auto scheduleSlot = [&](Operation *op) -> uint64_t {
    if (!op || op->getNumResults() == 0)
      return std::numeric_limits<uint64_t>::max();
    auto it = schedule->valueToNode.find(op->getResult(0));
    if (it == schedule->valueToNode.end())
      return std::numeric_limits<uint64_t>::max();
    return schedule->nodes[it->second].slot;
  };
  auto sortBySchedule = [&](auto &operations) {
    llvm::stable_sort(operations, [&](auto lhs, auto rhs) {
      return scheduleSlot(lhs.getOperation()) < scheduleSlot(rhs.getOperation());
    });
  };
  sortBySchedule(fifos);
  sortBySchedule(byteMems);
  sortBySchedule(asyncFifos);
  sortBySchedule(combs);

  llvm::DenseMap<Operation *, unsigned> combIndex;
  for (auto [i, comb] : llvm::enumerate(combs))
    combIndex.try_emplace(comb.getOperation(), static_cast<unsigned>(i));
  for (const EmissionScheduleNode &node : schedule->nodes) {
    if (isa<pyc::CombOp>(node.operation) &&
        !combIndex.contains(node.operation))
      return node.operation->emitError(
          "C++ emitter cannot map schedule node to a comb execution unit");
    if (!isa<pyc::CombOp, pyc::InstanceOp, pyc::RegOp, pyc::DelayLineOp,
             pyc::FifoOp, pyc::ByteMemOp, pyc::SyncMemNMOp, pyc::AsyncFifoOp,
             pyc::CdcSyncOp>(node.operation))
      return node.operation->emitError(
          "C++ emitter cannot map schedule node to a supported execution or state-source unit");
  }

  // Change-driven inputs use producer activity; external boundaries are polled.
  if (!combs.empty()) {
    os << "  // Per-comb input snapshots for change-driven evaluation.\n";
    for (auto [index, comb] : llvm::enumerate(combs)) {
      for (auto [inputIndex, input] : llvm::enumerate(comb.getInputs())) {
        if (isDirtyDrivenInput(input))
          continue;
        os << "  " << cppType(input.getType()) << " _pyc_comb_" << index
           << "_input_" << inputIndex << "{};\n";
      }
    }
    os << "\n";
  }

  if (!combs.empty()) {
    os << "  // Runtime-owned local fused-comb dirty set.\n";
    os << "  pyc::cpp::DirtyBitset<" << combs.size()
       << "> _pyc_comb_dirty{};\n";
  }

  sortBySchedule(instances);

  struct InstInfo {
    pyc::InstanceOp op;
    func::FuncOp callee;
    std::string member;
    std::string seg;
    std::vector<std::string> inPorts;
    std::vector<std::string> outPorts;
    std::vector<char> clockOnlyIn;
  };
  std::vector<InstInfo> instInfos;
  instInfos.reserve(instances.size());
  llvm::DenseMap<Operation *, unsigned> instIndex;
  ModuleOp mod = f->getParentOfType<ModuleOp>();
  if (!mod)
    return f.emitError("C++ emitter: missing parent module for instance resolution");
  std::vector<bool> instHasSequentialCallee{};

  llvm::DenseMap<std::pair<Operation *, unsigned>, bool> clockOnlyMemo;
  if (!instances.empty()) {
    for (auto inst : instances) {
      auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
      if (!calleeAttr)
        return inst.emitError("missing required FlatSymbolRefAttr `callee`");
      auto callee = mod.lookupSymbol<func::FuncOp>(calleeAttr.getValue());
      if (!callee)
        return inst.emitError("callee symbol not found: ") << calleeAttr.getValue();

      std::vector<std::string> inPorts;
      std::vector<std::string> outPorts;
      computeUniquePortNames(callee, inPorts, outPorts);
      if (inPorts.size() != inst.getNumOperands())
        return inst.emitError("operand count does not match callee signature: inst=")
               << inst.getNumOperands() << ", callee=" << inPorts.size()
               << " (" << callee.getSymName() << ")";
      if (outPorts.size() != inst.getNumResults())
        return inst.emitError("result count does not match callee signature: inst=")
               << inst.getNumResults() << ", callee=" << outPorts.size()
               << " (" << callee.getSymName() << ")";

      std::string base = "inst";
      if (auto nameAttr = inst->getAttrOfType<StringAttr>("name"))
        base = sanitizeId(nameAttr.getValue());
      else
        base = sanitizeId(callee.getSymName()) + std::string("_inst");
      std::string seg = base;
      if (auto shortAttr = inst->getAttrOfType<StringAttr>("short_name"))
        seg = sanitizeId(shortAttr.getValue());
      std::string member = nt.unique(base);

      std::vector<char> clockOnlyIn(inst.getNumOperands(), 0);
      for (unsigned i = 0; i < inst.getNumOperands(); ++i)
        clockOnlyIn[i] =
            argumentIsClockOnly(callee, i, mod, clockOnlyMemo) ? 1 : 0;

      unsigned idx = static_cast<unsigned>(instInfos.size());
      instIndex.try_emplace(inst.getOperation(), idx);
      instInfos.push_back(InstInfo{inst, callee, std::move(member), std::move(seg),
                                   std::move(inPorts), std::move(outPorts),
                                   std::move(clockOnlyIn)});
    }

    llvm::DenseMap<Operation *, SeqStateKind> seqMemo{};
    instHasSequentialCallee.reserve(instInfos.size());
    for (const auto &ii : instInfos) {
      instHasSequentialCallee.push_back(functionHasSequentialState(ii.callee, mod, seqMemo));
    }
  }

  auto instancePackedCacheWordCount = [&](const InstInfo &ii) -> unsigned {
    std::size_t words = 0;
    auto inst = ii.op;
    for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
      if (i < ii.clockOnlyIn.size() && ii.clockOnlyIn[i])
        continue;
      words += packedWireWordCount(inst.getOperand(i).getType());
    }
    return static_cast<unsigned>(words);
  };

  // Medium fanout glue modules still explode into large TUs if they keep the
  // per-port versioned cache form. Prefer the exact packed-word cache once an
  // instance reaches moderate input width/count.
  constexpr unsigned kPackedInstanceCacheOperandThreshold = 12;
  constexpr unsigned kPackedInstanceCacheWordThreshold = 16;
  auto usePackedInstanceEvalCache = [&](const InstInfo &ii) -> bool {
    auto inst = ii.op;
    unsigned packedWords = instancePackedCacheWordCount(ii);
    return packedWords > 0 &&
           ((inst.getNumOperands() >= kPackedInstanceCacheOperandThreshold) ||
            (packedWords >= kPackedInstanceCacheWordThreshold));
  };

  llvm::DenseMap<Operation *, std::string> byteMemInstName;

	  if (!instInfos.empty()) {
	    os << "  // Sub-modules.\n";
	    for (const auto &ii : instInfos) {
	      auto callee = ii.callee;
	      // Decision 0012: Parent SimObjects own children via unique_ptr.
	      os << "  std::unique_ptr<" << sanitizeId(callee.getSymName()) << "> " << ii.member
	         << " = std::make_unique<" << sanitizeId(callee.getSymName()) << ">();\n";
	    }
	    os << "\n";

    os << "  // Sub-module eval cache (default-on in C++; can be disabled with\n";
    os << "  // -DPYC_DISABLE_INSTANCE_EVAL_CACHE).\n";
    for (const auto &ii : instInfos) {
      auto inst = ii.op;
      os << "  bool " << ii.member << "_eval_cache_valid = false;\n";
      if (usePackedInstanceEvalCache(ii)) {
        os << "  std::array<std::uint64_t, " << instancePackedCacheWordCount(ii) << "> " << ii.member
           << "_eval_cache_words{};\n";
      } else {
        for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
          if (i < ii.clockOnlyIn.size() && ii.clockOnlyIn[i])
            continue;
          std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
          unsigned inW = bitWidth(inst.getOperand(i).getType());
          os << "  " << cppType(inst.getOperand(i).getType()) << " " << cacheName << "{};\n";
          os << "  std::uint64_t " << ii.member << "_eval_cache_in_ver_" << i << " = 1ull;\n";
          os << "  std::uint64_t " << ii.member << "_eval_cache_in_seen_ver_" << i << " = 0ull;\n";
          if (inW <= 64)
            os << "  std::uint64_t " << ii.member << "_eval_cache_in_fp_" << i << " = 0ull;\n";
        }
      }
    }
    os << "\n";
  }

    struct NamedProbeInfo {
      std::string fieldPath;
      std::string cppValue;
      std::string cppRegInst;
      std::string cppRegNext;
      unsigned width = 0;
      unsigned stateLsb = 0;
      unsigned stateStorageWidth = 0;
      bool isReg = false;
      Type type;
    };

    // Decision 0003 / 0051-0052: infer probe kind for ports and named internal
    // objects. Packed state extracts retain the logical lane's q/pending view.
    std::vector<bool> outIsReg(f.getNumResults(), false);
    std::vector<std::optional<StateStorageSource>> outRegQ(f.getNumResults());
    std::vector<NamedProbeInfo> namedProbes;
    if (!f.isDeclaration()) {
      auto ret = dyn_cast_or_null<func::ReturnOp>(f.getBody().front().getTerminator());
      if (!ret)
        return f.emitError("missing return");
      for (unsigned i = 0; i < f.getNumResults() && i < ret.getNumOperands(); ++i)
        outRegQ[i] = findStateStorageSource(ret.getOperand(i));
      for (unsigned i = 0; i < f.getNumResults(); ++i)
        outIsReg[i] = static_cast<bool>(outRegQ[i]);

      llvm::StringSet<> seenNamedFields;
      f.walk([&](Operation *op) {
        // Cycle-balance names are compiler temporaries, not stable user probes.
        if (auto generated = op->getAttrOfType<StringAttr>("pyc.generated");
            generated && generated.getValue() == "cycle_balance")
          return;
        auto nameAttr = op->getAttrOfType<StringAttr>("pyc.name");
        if (!nameAttr || op->getNumResults() != 1)
          return;
        if (auto lazy = op->getAttrOfType<BoolAttr>("pyc.observe_lazy");
            lazy && lazy.getValue())
          return;
        Value value = op->getResult(0);
        if (getValueCppStorage(value) == CppStorageKind::Local)
          return;
        unsigned width = bitWidth(value.getType());
        if (width == 0)
          return;
        std::string fieldPath = nameAttr.getValue().str();
        if (!seenNamedFields.insert(fieldPath).second)
          return;
        auto regQ = findStateStorageSource(value);
        std::string regInst;
        std::string regNext;
        if (regQ) {
          // RegLoop lanes carry the pending/qNext storage for their reg.
          pyc::RegOp srcReg;
          if (!regQ->tap) {
            Value qVal = regQ->q;
            if (auto *def = qVal.getDefiningOp())
              if (auto alias = dyn_cast<pyc::AliasOp>(def))
                qVal = alias.getOperand();
            if (auto *d2 = qVal.getDefiningOp())
              srcReg = dyn_cast<pyc::RegOp>(d2);
          }
          if (srcReg) {
            auto pn = probePendingNext(srcReg);
            regInst = pn.first;   // full `&expr` text
            regNext = pn.second;
          } else if (auto srcDelayOnly =
                         dyn_cast<pyc::DelayLineOp>(regQ->storage)) {
            // Looped/unlooped delay tail probe (no tap indirection).
            auto [dinst, dpend, dnext] = delayBinding(srcDelayOnly);
            if (dinst.empty()) {
              regInst = dpend;
              regNext = dnext;
            } else {
              regInst = "&" + dpend;
              regNext = "&" + dnext;
            }
          } else if (regQ->tap) {
            // Delay-tap probe: pending lives on the source delay; route
            // through DelayLoop when looped (TapView static, pointer text).
            if (auto srcDelay = regQ->tap.getDefiningOp<pyc::DelayLineOp>()) {
              auto [dinst, dpend, dnext] = delayBinding(srcDelay);
              if (dinst.empty()) {
                regInst = dpend;  // looped accessor (pointer, no &)
              } else {
                regInst = "&" + dpend;
              }
            } else {
              regInst = "&" + nt.get(regQ->q) + "_inst->pending";
            }
            regNext = "&" + nt.get(regQ->tap) + "_qNext";
          } else {
            regInst = "&" + nt.get(regQ->q) + "_inst->pending";
            regNext = "&" + nt.get(regQ->q) + "_inst->qNext";
          }
        }
        namedProbes.push_back(NamedProbeInfo{
            fieldPath,
            nt.get(value),
            regInst,
            regNext,
            width,
            regQ ? regQ->lsb : 0,
            regQ ? bitWidth(regQ->q.getType()) : 0,
            static_cast<bool>(regQ),
            value.getType(),
        });
      });
      std::sort(namedProbes.begin(), namedProbes.end(), [](const NamedProbeInfo &a, const NamedProbeInfo &b) {
        return a.fieldPath < b.fieldPath;
      });
    }
    // DFX trace registration (Decision 0145).
	  os << "  template <typename TbT, typename EnabledInstT, typename EnabledSigT>\n";
	  os << "  void pyc_trace_vcd(TbT &tb, const std::string &prefix, EnabledInstT &&enabledInst, EnabledSigT &&enabledSig) {\n";
	  os << "    std::string inst = pyc::cpp::shortenInstancePath(prefix);\n";
	  os << "    auto trace_port = [&](auto &sig, const char *leaf) {\n";
    os << "      std::string p = inst;\n";
    // Decision 0023: canonical_path uses <instance_path>:<field_path>.
    os << "      p += \":\";\n";
    os << "      p += leaf;\n";
    os << "      if (enabledSig(p)) tb.vcdTrace(sig, p);\n";
	  os << "    };\n";
	  for (unsigned i = 0; i < inNames.size(); ++i)
	    os << "    trace_port(" << inNames[i] << ", " << cppStringLiteral(inCanon[i]) << ");\n";
	  for (unsigned i = 0; i < outNames.size(); ++i)
	    os << "    trace_port(" << outNames[i] << ", " << cppStringLiteral(outCanon[i]) << ");\n";
	  for (const auto &named : namedProbes)
	    os << "    trace_port(" << named.cppValue << ", "
	       << cppStringLiteral(named.fieldPath) << ");\n";
	  if (!instInfos.empty()) {
	    os << "    auto trace_child = [&](auto &child, const char *seg) {\n";
	    os << "      std::string full = prefix;\n";
	    os << "      full += \".\";\n";
      os << "      full += seg;\n";
      os << "      std::string inst_path = pyc::cpp::shortenInstancePath(full);\n";
      os << "      if (enabledInst(inst_path) && child) child->pyc_trace_vcd(tb, full, enabledInst, enabledSig);\n";
      os << "    };\n";
      for (const auto &ii : instInfos)
        os << "    trace_child(" << ii.member << ", \"" << ii.seg << "\");\n";
    }
	  os << "  }\n\n";

	  // ProbeRegistry registration (Decisions 0004, 0018-0021).
	  os << "  void pyc_register_probes(pyc::cpp::ProbeRegistry &reg, const std::string &prefix) {\n";
	  os << "    std::string inst = pyc::cpp::shortenInstancePath(prefix);\n";
	  os << "    auto reg_path = [&](const char *leaf) {\n";
	  os << "      std::string p = inst;\n";
	  // Decision 0023: canonical_path uses <instance_path>:<field_path>.
	  os << "      p += \":\";\n";
	  os << "      p += leaf;\n";
	  os << "      return p;\n";
	  os << "    };\n";
		  for (auto [i, arg] : llvm::enumerate(f.getArguments())) {
		    unsigned w = bitWidth(arg.getType());
		    if (w == 0)
		      return f.emitError("invalid input port width for ProbeRegistry: ") << getPortCanonicalFieldPath(f, i, /*isResult=*/false);
		    emitWireProbes(os, arg.getType(), w, inCanon[static_cast<unsigned>(i)], inNames[static_cast<unsigned>(i)]);
		  }
		  for (unsigned i = 0; i < f.getNumResults(); ++i) {
		    unsigned w = bitWidth(f.getResultTypes()[i]);
		    if (w == 0)
		      return f.emitError("invalid output port width for ProbeRegistry: ") << getPortCanonicalFieldPath(f, i, /*isResult=*/true);
		    if (outIsReg[i]) {
		      const auto &source = *outRegQ[i];
		      const unsigned storageWidth = bitWidth(source.q.getType());
		      // Resolve the owning pyc.reg (if any) for RegLoop lane bindings.
		      pyc::RegOp srcReg;
		      if (!source.tap) {
		        Value qVal = source.q;
		        if (auto *def = qVal.getDefiningOp())
		          if (auto alias = dyn_cast<pyc::AliasOp>(def))
		            qVal = alias.getOperand();
		        if (auto *d2 = qVal.getDefiningOp())
		          srcReg = dyn_cast<pyc::RegOp>(d2);
		      }
		      std::string refP, refN;
		      std::string pendBase = nt.get(source.q) + "_inst->pending";
		      std::string nextBase = source.tap
		                                         ? (nt.get(source.tap) + "_qNext")
		                                         : (nt.get(source.q) + "_inst->qNext");
		      if (srcReg) {
		        auto pn = probePendingNext(srcReg);
		        pendBase = pn.first;
		        nextBase = pn.second;
		      } else if (auto tailDelay =
		                     dyn_cast<pyc::DelayLineOp>(source.storage)) {
		        auto [dinst, dpend, dnext] = delayBinding(tailDelay);
		        if (dinst.empty()) {
		          pendBase = dpend;
		          nextBase = dnext;
		        }
		      } else if (source.tap) {
		        if (auto srcDelay = dyn_cast<pyc::DelayLineOp>(source.storage)) {
		          auto [dinst, dpend, dnext] = delayBinding(srcDelay);
		          if (dinst.empty()) {
		            pendBase = dpend;  // looped accessor (pointer)
		            nextBase = "&" + nt.get(source.tap) + "_qNext";
		            nextBase = dnext;  // looped qNext accessor (pointer)
		          }
		        }
		      }
		      const auto pendNext = std::pair<std::string, std::string>{pendBase, nextBase};
		      const bool looped = pendNext.first.rfind("reg_loop_", 0) == 0 ||
		                              pendNext.first.rfind("delay_loop_", 0) == 0;
		      // RegLoop lanes report q as write data: sample their Write event
		      // after commit. Delay lanes keep the pre-transfer qNext view.
		      const bool regPost = pendNext.first.rfind("reg_loop_", 0) == 0;
		      refP = looped ? pendNext.first : "&" + pendNext.first;
		      refN = looped ? pendNext.second : "&" + pendNext.second;
		      if (source.lsb != 0 || storageWidth != w) {
			        os << "    reg.addRegSlice<" << w << ", " << storageWidth
			           << ">(reg_path(" << cppStringLiteral(outCanon[i]) << "), &"
			           << outNames[i] << ", " << refP << ", "
                       << refN
                       << ", " << source.lsb << "u, "
                       << (regPost ? "true" : "false") << ");\n";
			      } else {
			        os << "    reg.addReg<" << w << ">(reg_path(" << cppStringLiteral(outCanon[i]) << "), &" << outNames[i]
			           << ", " << refP << ", "
                       << refN << ", " << (regPost ? "true" : "false")
                       << ");\n";
		      }
		    } else {
		      emitWireProbes(os, f.getResultTypes()[i], w, outCanon[i], outNames[i]);
		    }
		  }
      for (const auto &named : namedProbes) {
        if (named.isReg) {
          const bool loopedN = named.cppRegInst.rfind("reg_loop_", 0) == 0 ||
                       named.cppRegInst.rfind("delay_loop_", 0) == 0;
          const bool namedPost = named.cppRegInst.rfind("reg_loop_", 0) == 0;
          if (named.stateLsb != 0 ||
              named.stateStorageWidth != named.width) {
            os << "    reg.addRegSlice<" << named.width << ", "
               << named.stateStorageWidth << ">(reg_path("
               << cppStringLiteral(named.fieldPath) << "), &"
               << named.cppValue << ", "
               << (loopedN ? named.cppRegInst : "&" + named.cppRegInst)
               << ", " << named.cppRegNext << ", "
               << named.stateLsb << "u, "
               << (namedPost ? "true" : "false") << ");\n";
          } else {
            os << "    reg.addReg<" << named.width << ">(reg_path("
               << cppStringLiteral(named.fieldPath) << "), &"
               << named.cppValue << ", "
               << (loopedN ? named.cppRegInst : "&" + named.cppRegInst)
               << ", " << named.cppRegNext << ", "
               << (namedPost ? "true" : "false") << ");\n";
          }
        } else {
          emitWireProbes(os, named.type, named.width, named.fieldPath, named.cppValue);
        }
      }
      llvm::StringSet<> emittedNamed;
      for (const auto &named : namedProbes)
        emittedNamed.insert(named.fieldPath);
      auto emitLazySlices = [&](Operation *storage, Value q) -> LogicalResult {
        auto slices = storage->getAttrOfType<ArrayAttr>("pyc.lazy_probe_slices");
        if (!slices)
          return success();
        const unsigned storageWidth = bitWidth(q.getType());
        if (storageWidth == 0)
          return storage->emitError("lazy probe storage has empty width");
        const std::string inst = nt.get(q) + "_inst";
        // RegLoop lane bindings when the storage is a looped reg's q: the q
        // wire address is shared with the loop lane, so only pending/qNext
        // switch to the loop accessors.
        std::string pendText = "&" + inst + "->pending";
        std::string nextText = "&" + inst + "->qNext";
        std::string qText = "&" + inst + "->q";
        {
          Value qVal = q;
          if (auto *def = qVal.getDefiningOp())
            if (auto alias = dyn_cast<pyc::AliasOp>(def))
              qVal = alias.getOperand();
          if (auto *d2 = qVal.getDefiningOp())
            if (auto srcReg = dyn_cast<pyc::RegOp>(d2)) {
              auto pn = probePendingNext(srcReg);
              if (pn.first.rfind("reg_loop_", 0) == 0) {
                pendText = pn.first;
                nextText = "&" + nt.get(q);
                qText = "&" + nt.get(q);
              }
            }
        }
        for (Attribute item : slices) {
          auto dict = dyn_cast<DictionaryAttr>(item);
          if (!dict)
            return storage->emitError("invalid pyc.lazy_probe_slices entry");
          auto name = dict.getAs<StringAttr>("name");
          auto lsbAttr = dict.getAs<IntegerAttr>("lsb");
          auto widthAttr = dict.getAs<IntegerAttr>("width");
          auto tapAttr = dict.getAs<IntegerAttr>("tap_depth");
          if (!name || name.getValue().empty() || !lsbAttr || !widthAttr)
            return storage->emitError("lazy probe slice requires name/lsb/width");
          const std::string field = name.getValue().str();
          if (!emittedNamed.insert(field).second)
            continue;
          const unsigned width = static_cast<unsigned>(widthAttr.getInt());
          const unsigned lsb = static_cast<unsigned>(lsbAttr.getInt());
          const int64_t tapDepth = tapAttr ? tapAttr.getInt() : -1;
          if (width == 0)
            return storage->emitError("lazy probe slice has empty width: ")
                   << field;
          if (tapDepth >= 0) {
            // Bind write-next to this stage's own qNext (emitTapUpdates writes
            // per-tap _qNext wires); the delay line's inst->qNext is stage 0.
            pyc::DelayTapOp matchingTap;
            for (auto tap : delayTaps) {
              if (tap.getLine() != q)
                continue;
              auto d = tap->getAttrOfType<IntegerAttr>("depth");
              if (d && d.getInt() == tapDepth) {
                matchingTap = tap;
                break;
              }
            }
            std::string tapWriteNext;
            // Looped detection for the fallback write-next.
            std::string loopPend;
            std::string loopQNext;
            std::string tapViewRef;
            std::string tapLane;
            {
              Value qv = q;
              if (auto *def = qv.getDefiningOp())
                if (auto alias = dyn_cast<pyc::AliasOp>(def))
                  qv = alias.getOperand();
              if (auto *d2 = qv.getDefiningOp())
                if (auto srcDelay = dyn_cast<pyc::DelayLineOp>(d2)) {
                  auto [dinst, dpend, dnext] = delayBinding(srcDelay);
                  if (dinst.empty()) {
                    if (auto idAttr = srcDelay->getAttrOfType<IntegerAttr>(
                            "pyc.loop_id")) {
                      const int64_t raw = idAttr.getValue().getSExtValue();
                      const unsigned dw = bitWidth(srcDelay.getQ().getType());
                      if (auto da = srcDelay->getAttrOfType<IntegerAttr>("depth")) {
                        const int64_t key = raw * 256 +
                                            da.getValue().getZExtValue();
                        if (auto it = delayLoopLines.find(key);
                            it != delayLoopLines.end()) {
                          long lane = 0;
                          for (pyc::DelayLineOp c : it->second) {
                            if (c == srcDelay)
                              break;
                            ++lane;
                          }
                          const std::string loop =
                              "delay_loop_" + std::to_string(key);
                          loopPend = loop + ".lane_pending(" +
                                     std::to_string(lane) + "u)";
                          loopQNext = "reinterpret_cast<pyc::cpp::Wire<" +
                                      std::to_string(dw) + "> *>(" + loop +
                                      ".lane_qnext_raw(" +
                                      std::to_string(lane) + "u))";
                          tapViewRef = "&" + loop;
                          tapLane = std::to_string(lane) + "u";
                        }
                      }
                    }
                  }
                }
            }
            if (matchingTap)
              tapWriteNext = "&" + nt.get(matchingTap.getTap()) + "_qNext";
            else if (!loopQNext.empty())
              tapWriteNext = loopQNext;
            else
              tapWriteNext = "&" + inst + "->qNext";
            if (tapViewRef.empty()) {
              tapViewRef = inst;
              if (loopPend.empty())
                loopPend = "&" + inst + "->pending";
            }
            // Looped source delay: register the loop lane directly (tap reads
            // go through DelayLoop::lane_tap), with write-intent from the
            // loop lane; per-object delays keep the DelayInst argument.
            const bool looped = !tapLane.empty();
            if (lsb == 0 && width == storageWidth) {
              if (looped)
                os << "    reg.addDelayLoopTap<" << width << ">(reg_path("
                   << cppStringLiteral(field) << "), " << tapViewRef << ", "
                   << tapLane << ", " << tapDepth << "u, "
                   << (loopPend.empty() ? ("&" + inst + "->pending") : loopPend)
                   << ", " << tapWriteNext << ");\n";
              else
                os << "    reg.addRegLazyTap<" << width << ">(reg_path("
                   << cppStringLiteral(field) << "), " << tapViewRef << ", "
                   << tapDepth << "u, "
                   << (loopPend.empty() ? ("&" + inst + "->pending") : loopPend)
                   << ", " << tapWriteNext << ");\n";
            } else {
              if (looped)
                os << "    reg.addDelayLoopTapSlice<" << width << ", "
                   << storageWidth << ">(reg_path(" << cppStringLiteral(field)
                   << "), " << tapViewRef << ", " << tapLane << ", "
                   << tapDepth << "u, " << lsb << "u, "
                   << (loopPend.empty() ? ("&" + inst + "->pending") : loopPend)
                   << ", " << tapWriteNext << ");\n";
              else
                os << "    reg.addRegLazyTapSlice<" << width << ", "
                   << storageWidth << ">(reg_path(" << cppStringLiteral(field)
                   << "), " << tapViewRef << ", " << tapDepth << "u, " << lsb
                   << "u, "
                   << (loopPend.empty() ? ("&" + inst + "->pending") : loopPend)
                   << ", " << tapWriteNext << ");\n";
            }
            continue;
          }
          const bool lazyPost = pendText.rfind("reg_loop_", 0) == 0;
          if (lsb == 0 && width == storageWidth) {
            os << "    reg.addReg<" << width << ">(reg_path("
               << cppStringLiteral(field) << "), " << qText << ", "
               << pendText << ", " << nextText << ", "
               << (lazyPost ? "true" : "false") << ");\n";
            continue;
          }
          os << "    reg.addRegLazySlice<" << width << ", " << storageWidth
             << ">(reg_path(" << cppStringLiteral(field) << "), " << qText
             << ", " << lsb << "u, " << pendText << ", " << nextText << ", "
             << (lazyPost ? "true" : "false") << ");\n";
        }
        return success();
      };
      bool lazyFailed = false;
      f.walk([&](Operation *op) {
        if (lazyFailed)
          return;
        if (auto reg = dyn_cast<pyc::RegOp>(op)) {
          if (failed(emitLazySlices(op, reg.getQ())))
            lazyFailed = true;
        } else if (auto delay = dyn_cast<pyc::DelayLineOp>(op)) {
          if (failed(emitLazySlices(op, delay.getQ())))
            lazyFailed = true;
        }
      });
      if (lazyFailed)
        return failure();
      bool lazyWireFailed = false;
      f.walk([&](Operation *op) {
        if (lazyWireFailed)
          return;
        if (op->getParentOfType<pyc::CombOp>())
          return;
        auto wires = op->getAttrOfType<ArrayAttr>("pyc.lazy_probe_wires");
        if (!wires)
          return;
        for (Attribute item : wires) {
          auto dict = dyn_cast<DictionaryAttr>(item);
          if (!dict) {
            op->emitError("invalid pyc.lazy_probe_wires entry");
            lazyWireFailed = true;
            return;
          }
          auto name = dict.getAs<StringAttr>("name");
          auto widthAttr = dict.getAs<IntegerAttr>("width");
          auto resultAttr = dict.getAs<IntegerAttr>("result");
          if (!name || name.getValue().empty() || !widthAttr) {
            op->emitError("lazy probe wire requires name/width");
            lazyWireFailed = true;
            return;
          }
          const std::string field = name.getValue().str();
          if (!emittedNamed.insert(field).second)
            continue;
          const unsigned width = static_cast<unsigned>(widthAttr.getInt());
          const unsigned resultIndex =
              resultAttr ? static_cast<unsigned>(resultAttr.getInt()) : 0;
          if (width == 0 || resultIndex >= op->getNumResults()) {
            op->emitError("lazy probe wire has invalid width/result: ")
                << field;
            lazyWireFailed = true;
            return;
          }
          os << "    reg.addWire<" << width << ">(reg_path("
             << cppStringLiteral(field) << "), &"
             << nt.get(op->getResult(resultIndex)) << ");\n";
        }
      });
      if (lazyWireFailed)
        return failure();
		  for (auto mem : byteMems) {
		    std::string instName = nt.get(mem.getRdata()) + "_inst";
		    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
	      instName = sanitizeId(nameAttr.getValue());
	    os << "    reg.addMem(reg_path(\"" << instName << "\"), &" << instName << ", &" << instName
	       << ".pendingWrite, &" << instName << ".latchedAddr, &" << instName << ".latchedData, &" << instName
	       << ".latchedStrb);\n";
	  }
	  for (auto mem : syncMemNMs) {
	    std::string instName = syncMemNMInstName.lookup(mem.getOperation());
	    os << "    if (" << instName << ") reg.addMem(reg_path(\"" << instName << "\"), " << instName << ", &" << instName
	       << "->pendingWrite[0], &" << instName << "->latchedWaddr[0], &" << instName << "->latchedWdata[0], &" << instName
	       << "->latchedWstrb[0]);\n";
	  }
	  if (!instInfos.empty()) {
	    os << "    auto reg_child = [&](auto &child, const char *seg) {\n";
	    os << "      std::string p = prefix;\n";
	    os << "      p += \".\";\n";
	    os << "      p += seg;\n";
	    os << "      if (child) child->pyc_register_probes(reg, p);\n";
	    os << "    };\n";
	    for (const auto &ii : instInfos)
	      os << "    reg_child(" << ii.member << ", \"" << ii.seg << "\");\n";
	  }
      auto probeAliases = probeAliasesForTop(probePlans, f.getSymName());
      if (!probeAliases.empty()) {
        for (const auto &alias : probeAliases) {
          os << "    if (const auto *src = reg.findByPath(" << cppStringLiteral(alias.sourcePath) << "))\n";
          os << "      reg.addAlias(" << cppStringLiteral(alias.canonicalPath) << ", *src);\n";
        }
      }
	  os << "  }\n\n";

	  for (auto r : regs) {
	    unsigned w = bitWidth(r.getQ().getType());
	    if (w == 0)
	      return r.emitError("invalid reg width");
    os << "  pyc::cpp::pycReg<" << w << "> *" << nt.get(r.getQ()) << "_inst = nullptr;\n";
  }
  for (int64_t loopId : regLoopIds) {
    os << "  pyc::cpp::RegLoop " << regLoopName(loopId) << ";\n";
  }
  for (int64_t dbk : delayLoopIds) {
    os << "  pyc::cpp::DelayLoop delay_loop_" << dbk << ";\n";
  }
  std::string fastClkName;
  bool fastClkUnique = true;
  auto noteFastClk = [&](Value clk) {
    std::string name = nt.get(clk);
    if (fastClkName.empty())
      fastClkName = std::move(name);
    else if (fastClkName != name)
      fastClkUnique = false;
  };
  for (auto r : regs)
    noteFastClk(r.getClk());
  for (auto delay : delayLines)
    noteFastClk(delay.getClk());
  for (int64_t loopId : regLoopIds)
    for (pyc::RegOp r : regLoopRegs[loopId])
      noteFastClk(r.getClk());
  const bool fastEdge = fastClkUnique && !fastClkName.empty();
  if (!fastEdge && !fusedRegLoopIds.empty())
    return f.emitError("tick-fuse loops require a single-clock fast edge");
  if (fastEdge) {
    os << "  bool _pyc_clk_prev = false;\n";
    os << "  bool _pyc_seq_armed = false;\n";
    os << "  bool _pyc_commit_needed = false;\n";
  }
  for (auto delay : delayLines) {
    unsigned w = bitWidth(delay.getQ().getType());
    if (w == 0)
      return delay.emitError("invalid delay_line width");
    auto depthAttr = delay->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return delay.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    os << "  pyc::cpp::pycDelayLine<" << w << ", " << depth << "> *" << nt.get(delay.getQ())
       << "_inst = nullptr;\n";
  }
  for (auto fifo : fifos) {
    unsigned w = bitWidth(fifo.getOutData().getType());
    if (w == 0)
      return fifo.emitError("invalid fifo width");
    auto depthAttr = fifo->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return fifo.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    std::string instName = nt.get(fifo.getInReady()) + "_inst";
    os << "  pyc::cpp::pycFifo<" << w << ", " << depth << "> " << instName << ";\n";
    os << "  bool " << instName << "_eval_cache_valid = false;\n";
    os << "  " << cppType(fifo.getInValid().getType()) << " " << instName << "_eval_cache_in_valid{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_valid_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_valid_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_valid_fp = 0ull;\n";
    os << "  " << cppType(fifo.getInData().getType()) << " " << instName << "_eval_cache_in_data{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_data_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_data_seen_ver = 0ull;\n";
    if (w <= 64)
      os << "  std::uint64_t " << instName << "_eval_cache_in_data_fp = 0ull;\n";
    os << "  " << cppType(fifo.getOutReady().getType()) << " " << instName << "_eval_cache_out_ready{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_ready_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_ready_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_ready_fp = 0ull;\n";
  }
  for (auto mem : byteMems) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer byte_mem types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    if (addrW == 0)
      return mem.emitError("invalid byte_mem addr width");
    if (dataW == 0)
      return mem.emitError("invalid byte_mem data width");

    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();

    std::string instName = nt.get(mem.getRdata()) + "_inst";
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      instName = sanitizeId(nameAttr.getValue());
    byteMemInstName.try_emplace(mem.getOperation(), instName);

    os << "  pyc::cpp::pycByteMem<" << addrW << ", " << dataW << ", " << depth << "> " << instName << ";\n";
    os << "  bool " << instName << "_eval_cache_valid = false;\n";
      os << "  " << cppType(mem.getRaddr().getType()) << " " << instName << "_eval_cache_raddr{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_raddr_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_raddr_seen_ver = 0ull;\n";
    if (addrW <= 64)
      os << "  std::uint64_t " << instName << "_eval_cache_raddr_fp = 0ull;\n";
    os << "  " << cppType(mem.getWvalid().getType()) << " " << instName << "_eval_cache_wvalid{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wvalid_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wvalid_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wvalid_fp = 0ull;\n";
    os << "  " << cppType(mem.getWaddr().getType()) << " " << instName << "_eval_cache_waddr{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_waddr_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_waddr_seen_ver = 0ull;\n";
    if (addrW <= 64)
      os << "  std::uint64_t " << instName << "_eval_cache_waddr_fp = 0ull;\n";
    os << "  " << cppType(mem.getWdata().getType()) << " " << instName << "_eval_cache_wdata{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wdata_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wdata_seen_ver = 0ull;\n";
    if (dataW <= 64)
      os << "  std::uint64_t " << instName << "_eval_cache_wdata_fp = 0ull;\n";
    os << "  " << cppType(mem.getWstrb().getType()) << " " << instName << "_eval_cache_wstrb{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wstrb_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wstrb_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_wstrb_fp = 0ull;\n";
  }
  for (auto mem : syncMemNMs) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddrs()[0].getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdatas()[0].getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem_nm types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    if (addrW == 0)
      return mem.emitError("invalid sync_mem_nm addr width");
    if (dataW == 0)
      return mem.emitError("invalid sync_mem_nm data width");

    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    unsigned rp = mem.getRens().size();
    unsigned wp = mem.getWvalids().size();

    std::string instName = syncMemNMInstName.lookup(mem.getOperation());
    os << "  pyc::cpp::pycSyncMemNM<" << addrW << ", " << dataW << ", " << depth << ", " << rp
       << ", " << wp << ", "
       << (mem.getReadMode() == "old" ? "true" : "false")
       << "> *" << instName << " = nullptr;\n";
  }
  for (auto fifo : asyncFifos) {
    unsigned w = bitWidth(fifo.getOutData().getType());
    if (w == 0 || w > 64)
      return fifo.emitError("C++ emitter only supports async_fifo widths 1..64 in the prototype");
    auto depthAttr = fifo->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return fifo.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    std::string instName = nt.get(fifo.getInReady()) + "_inst";
    os << "  pyc::cpp::pycAsyncFifo<" << w << ", " << depth << "> " << instName << ";\n";
    os << "  bool " << instName << "_eval_cache_valid = false;\n";
    os << "  " << cppType(fifo.getInRst().getType()) << " " << instName << "_eval_cache_in_rst{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_rst_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_rst_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_rst_fp = 0ull;\n";
    os << "  " << cppType(fifo.getOutRst().getType()) << " " << instName << "_eval_cache_out_rst{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_rst_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_rst_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_rst_fp = 0ull;\n";
    os << "  " << cppType(fifo.getInValid().getType()) << " " << instName << "_eval_cache_in_valid{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_valid_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_valid_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_valid_fp = 0ull;\n";
    os << "  " << cppType(fifo.getInData().getType()) << " " << instName << "_eval_cache_in_data{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_data_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_in_data_seen_ver = 0ull;\n";
    if (w <= 64)
      os << "  std::uint64_t " << instName << "_eval_cache_in_data_fp = 0ull;\n";
    os << "  " << cppType(fifo.getOutReady().getType()) << " " << instName << "_eval_cache_out_ready{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_ready_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_ready_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_out_ready_fp = 0ull;\n";
  }
  for (auto s : cdcSyncs) {
    unsigned w = bitWidth(s.getOut().getType());
    if (w == 0 || w > 64)
      return s.emitError("C++ emitter only supports cdc_sync widths 1..64 in the prototype");
    std::uint64_t stages = 2;
    if (auto st = s->getAttrOfType<IntegerAttr>("stages"))
      stages = st.getValue().getZExtValue();
    os << "  pyc::cpp::pycCdcSync<" << w << ", " << stages << "> " << nt.get(s.getOut()) << "_inst;\n";
  }
  auto emitTickSnapshotDecls = [&](llvm::StringRef key,
                                   OperandRange operands) {
    os << "  bool " << key << "_tick_inputs_valid = false;\n";
    for (auto [index, operand] : llvm::enumerate(operands))
      os << "  " << cppType(operand.getType()) << " " << key
         << "_tick_input_" << index << "{};\n";
  };
  os << "\n  // State-source snapshots used by dirty tick_compute.\n";
  for (const auto &ii : instInfos)
    emitTickSnapshotDecls(ii.member, ii.op->getOperands());
  for (auto r : regs)
    emitTickSnapshotDecls(nt.get(r.getQ()) + "_inst", r->getOperands());
  for (auto fifo : fifos)
    emitTickSnapshotDecls(nt.get(fifo.getInReady()) + "_inst",
                          fifo->getOperands());
  for (auto mem : byteMems)
    emitTickSnapshotDecls(byteMemInstName.lookup(mem.getOperation()),
                          mem->getOperands());
  for (auto mem : syncMemNMs)
    emitTickSnapshotDecls(syncMemNMInstName.lookup(mem.getOperation()),
                          mem->getOperands());
  for (auto fifo : asyncFifos)
    emitTickSnapshotDecls(nt.get(fifo.getInReady()) + "_inst",
                          fifo->getOperands());
  for (auto sync : cdcSyncs)
    emitTickSnapshotDecls(nt.get(sync.getOut()) + "_inst",
                          sync->getOperands());
  os << "\n";

  os << "  bool _pyc_sim_fast_enable = false;\n";
  os << "\n";
  os << "  static bool _pyc_parse_bool_env(const char *name, bool dflt = false) {\n";
  os << "    const char *v = std::getenv(name);\n";
  os << "    if (!v || !*v)\n";
  os << "      return dflt;\n";
  os << "    if (v[0] == '0')\n";
  os << "      return false;\n";
  os << "    if (v[0] == '1')\n";
  os << "      return true;\n";
  os << "    return dflt;\n";
  os << "  }\n\n";
  os << "  void _pyc_init_runtime_controls() {\n";
  os << "    _pyc_sim_fast_enable = _pyc_parse_bool_env(\"PYC_SIM_FAST\", false);\n";
  os << "  }\n\n";

  os << "  void _pyc_validate_primitive_bindings() const {\n";
  for (auto r : regs)
    os << "    if (!" << nt.get(r.getQ()) << "_inst) { std::cerr << \"pyc null reg binding: " << nt.get(r.getQ())
       << "_inst\" << \"\\n\"; std::abort(); }\n";
  for (auto delay : delayLines)
    os << "    if (!" << nt.get(delay.getQ()) << "_inst) { std::cerr << \"pyc null delay_line binding: "
       << nt.get(delay.getQ()) << "_inst\" << \"\\n\"; std::abort(); }\n";
  for (auto mem : syncMemNMs) {
    std::string instName = syncMemNMInstName.lookup(mem.getOperation());
    os << "    if (!" << instName << ") { std::cerr << \"pyc null sync_mem_nm binding: " << instName
       << "\" << \"\\n\"; std::abort(); }\n";
  }
  os << "  }\n\n";

  // Constructor (wire members default-initialize to 0).
  os << "  " << structName << "()";
  bool firstInit = true;
  for (auto fifo : fifos) {
    os << (firstInit ? " :\n" : ",\n");
    firstInit = false;
    unsigned w = bitWidth(fifo.getOutData().getType());
    auto depth = fifo->getAttrOfType<IntegerAttr>("depth").getValue().getZExtValue();
    (void)w;
    (void)depth;
    os << "      " << nt.get(fifo.getInReady()) << "_inst(" << nt.get(fifo.getClk()) << ", " << nt.get(fifo.getRst())
       << ", " << nt.get(fifo.getInValid()) << ", " << nt.get(fifo.getInReady()) << ", " << nt.get(fifo.getInData())
       << ", " << nt.get(fifo.getOutValid()) << ", " << nt.get(fifo.getOutReady()) << ", " << nt.get(fifo.getOutData())
       << ")";
  }
  for (auto mem : byteMems) {
    os << (firstInit ? " :\n" : ",\n");
    firstInit = false;
    std::string instName = byteMemInstName.lookup(mem.getOperation());
    os << "      " << instName << "(" << nt.get(mem.getClk()) << ", " << nt.get(mem.getRaddr())
       << ", " << nt.get(mem.getRdata()) << ", " << nt.get(mem.getWvalid()) << ", " << nt.get(mem.getWaddr()) << ", "
       << nt.get(mem.getWdata()) << ", " << nt.get(mem.getWstrb()) << ")";
  }
  for (auto fifo : asyncFifos) {
    os << (firstInit ? " :\n" : ",\n");
    firstInit = false;
    os << "      " << nt.get(fifo.getInReady()) << "_inst(" << nt.get(fifo.getInClk()) << ", " << nt.get(fifo.getInRst())
       << ", " << nt.get(fifo.getInValid()) << ", " << nt.get(fifo.getInReady()) << ", " << nt.get(fifo.getInData())
       << ", " << nt.get(fifo.getOutClk()) << ", " << nt.get(fifo.getOutRst()) << ", " << nt.get(fifo.getOutValid())
       << ", " << nt.get(fifo.getOutReady()) << ", " << nt.get(fifo.getOutData()) << ")";
  }
  for (auto s : cdcSyncs) {
    os << (firstInit ? " :\n" : ",\n");
    firstInit = false;
    os << "      " << nt.get(s.getOut()) << "_inst(" << nt.get(s.getClk()) << ", " << nt.get(s.getRst()) << ", "
       << nt.get(s.getIn()) << ", " << nt.get(s.getOut()) << ")";
  }
  os << " {\n";
  if (!combs.empty()) {
    // The only guarantee that every comb runs on its first eval.
    os << "    for (unsigned _pyc_id = 0; _pyc_id < " << combs.size()
       << "u; ++_pyc_id)\n";
    os << "      _pyc_comb_dirty.mark(_pyc_id);\n";
  }
  for (auto r : regs) {
    unsigned w = bitWidth(r.getQ().getType());
    if (w == 0)
      return r.emitError("invalid reg width");
    os << "    " << nt.get(r.getQ()) << "_inst = new pyc::cpp::pycReg<" << w << ">(";
    os << nt.get(r.getClk()) << ", " << nt.get(r.getRst()) << ", " << nt.get(r.getEn()) << ", "
       << nt.get(r.getNext()) << ", " << nt.get(r.getInit()) << ", " << nt.get(r.getQ()) << ");\n";
  }
  for (int64_t loopId : regLoopIds) {
    os << "    " << regLoopName(loopId) << ".add_lanes({";
    bool firstLane = true;
    for (pyc::RegOp r : regLoopRegs[loopId]) {
      if (!firstLane)
        os << ", ";
      firstLane = false;
      const Type qTy = r.getQ().getType();
      os << "{" << firstLeafData(qTy, nt.get(r.getQ())) << ", "
         << firstLeafData(r.getNext().getType(), nt.get(r.getNext()))
         << ", &" << nt.get(r.getRst()) << ", &" << nt.get(r.getEn())
         << ", " << firstLeafData(r.getInit().getType(), nt.get(r.getInit()))
         << ", " << planeWords(qTy) << "u}";
    }
    os << "});\n";
  }
  for (int64_t dbk : delayLoopIds) {
    os << "    delay_loop_" << dbk << ".add_lanes({";
    bool firstDL = true;
    for (pyc::DelayLineOp dl : delayLoopLines[dbk]) {
      if (!firstDL)
        os << ", ";
      firstDL = false;
      auto da = dl->getAttrOfType<IntegerAttr>("depth");
      const unsigned words =
          (bitWidth(dl.getQ().getType()) + 63u) / 64u;
      os << "{" << nt.get(dl.getQ()) << ".data(), " << nt.get(dl.getNext())
         << ".data(), &" << nt.get(dl.getRst()) << ", &" << nt.get(dl.getEn())
         << ", " << nt.get(dl.getInit()) << ".data(), "
         << da.getValue().getZExtValue() << "u, " << words << "u}";
    }
    os << "});\n";
  }
  for (auto delay : delayLines) {
    unsigned w = bitWidth(delay.getQ().getType());
    auto depth = delay->getAttrOfType<IntegerAttr>("depth").getValue().getZExtValue();
    os << "    " << nt.get(delay.getQ()) << "_inst = new pyc::cpp::pycDelayLine<" << w << ", " << depth
       << ">(";
    os << nt.get(delay.getClk()) << ", " << nt.get(delay.getRst()) << ", " << nt.get(delay.getEn()) << ", "
       << nt.get(delay.getNext()) << ", " << nt.get(delay.getInit()) << ", " << nt.get(delay.getQ()) << ");\n";
  }
  for (auto mem : syncMemNMs) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddrs()[0].getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdatas()[0].getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem_nm types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    unsigned rp = mem.getRens().size();
    unsigned wp = mem.getWvalids().size();
    std::string instName = syncMemNMInstName.lookup(mem.getOperation());
    auto joinNames = [&](auto range) {
      std::string s;
      for (auto v : range) {
        if (!s.empty()) s += ", ";
        s += "&" + nt.get(v);
      }
      return s;
    };
    os << "    {\n";
    os << "      static const pyc::cpp::Wire<1> *const nm_rens_" << instName << "[] = {" << joinNames(mem.getRens()) << "};\n";
    os << "      static const pyc::cpp::Wire<" << addrW << "> *const nm_ras_" << instName << "[] = {" << joinNames(mem.getRaddrs()) << "};\n";
    os << "      pyc::cpp::Wire<" << dataW << "> *const nm_rds_" << instName << "[] = {" << joinNames(mem.getRdatas()) << "};\n";
    os << "      static const pyc::cpp::Wire<1> *const nm_wvs_" << instName << "[] = {" << joinNames(mem.getWvalids()) << "};\n";
    os << "      static const pyc::cpp::Wire<" << addrW << "> *const nm_was_" << instName << "[] = {" << joinNames(mem.getWaddrs()) << "};\n";
    os << "      static const pyc::cpp::Wire<" << dataW << "> *const nm_wds_" << instName << "[] = {" << joinNames(mem.getWdatas()) << "};\n";
    os << "      static const pyc::cpp::Wire<" << ((dataW + 7) / 8) << "> *const nm_wss_" << instName << "[] = {" << joinNames(mem.getWstrbs()) << "};\n";
    os << "      " << instName << " = new pyc::cpp::pycSyncMemNM<" << addrW << ", " << dataW << ", " << depth
       << ", " << rp << ", " << wp << ", "
       << (mem.getReadMode() == "old" ? "true" : "false") << ">("
       << nt.get(mem.getClk()) << ", "
       << "nm_rens_" << instName << ", nm_ras_" << instName << ", nm_rds_" << instName << ", "
       << "nm_wvs_" << instName << ", nm_was_" << instName << ", nm_wds_" << instName << ", nm_wss_" << instName << ");\n";
    os << "    }\n";
  }
  os << "    _pyc_validate_primitive_bindings();\n";
  os << "    _pyc_init_runtime_controls();\n";
  os << "    #ifdef PYC_ENABLE_CTOR_EVAL\n";
  os << "    eval();\n";
  os << "    #endif\n";
  os << "  }\n\n";

  // Emit fused comb helpers. Indices match body order (same as placement).
  for (auto [i, comb] : llvm::enumerate(combs)) {
    if (failed(emitCombMethod(comb, os, nt, static_cast<unsigned>(i), opts,
                              *schedule, combIndex, &delayLoopLines)))
      return failure();
  }

  auto topoOrder = [&](bool includePrims, llvm::SmallVector<Operation *> &ordered) -> bool {
    ordered.clear();

    llvm::SmallVector<Operation *> nodes;
    llvm::SmallVector<std::string> nodeKey;
    llvm::DenseMap<Operation *, unsigned> nodeIndex;

    auto shouldInclude = [&](Operation &op) -> bool {
      if (isa<func::ReturnOp>(op) || isa<pyc::WireOp>(op) || isa<pyc::RegOp>(op) ||
          isa<pyc::DelayLineOp>(op) || isa<pyc::SyncMemNMOp>(op) || isa<pyc::CdcSyncOp>(op))
        return false;
      if (!includePrims &&
          (isa<pyc::FifoOp>(op) || isa<pyc::AsyncFifoOp>(op) || isa<pyc::ByteMemOp>(op) || isa<pyc::InstanceOp>(op)))
        return false;
      return true;
    };

    for (Operation &op : top) {
      if (!shouldInclude(op))
        continue;
      unsigned idx = static_cast<unsigned>(nodes.size());
      nodes.push_back(&op);
      nodeIndex.try_emplace(&op, idx);

      std::string k;
      if (op.getNumResults() > 0) {
        auto scheduleIt = schedule->valueToNode.find(op.getResult(0));
        if (scheduleIt != schedule->valueToNode.end()) {
          uint64_t slot = schedule->nodes[scheduleIt->second].slot;
          k = "schedule_" + llvm::utostr(slot);
        }
      }
      if (k.empty()) {
        if (auto a = dyn_cast<pyc::AssignOp>(op))
          k = nt.get(a.getDst());
        else if (op.getNumResults() > 0)
          k = nt.get(op.getResult(0));
        else
          k = sanitizeId(op.getName().getStringRef()) + "_" +
              std::to_string(idx);
      }
      nodeKey.push_back(std::move(k));
    }

    llvm::DenseMap<Value, unsigned> valueProducer;
    llvm::DenseMap<Value, unsigned> wireAssign;
    llvm::DenseMap<Value, unsigned> wireAssignCount;

    for (auto [idx, op] : llvm::enumerate(nodes)) {
      for (Value r : op->getResults())
        valueProducer.try_emplace(r, static_cast<unsigned>(idx));

      if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
        Value dst = a.getDst();
        unsigned &cnt = wireAssignCount[dst];
        cnt++;
        if (cnt == 1)
          wireAssign[dst] = static_cast<unsigned>(idx);
      }
    }

    // Multiple drivers to the same wire are not supported by the topo scheduler
    // (imperative evaluation becomes ambiguous); fall back to bounded fixpoint.
    for (auto &it : wireAssignCount) {
      if (it.second > 1)
        return false;
    }
    for (auto &it : wireAssign)
      valueProducer[it.first] = it.second;

    llvm::SmallVector<llvm::SmallVector<unsigned>> succ(nodes.size());
    llvm::SmallVector<unsigned> indeg(nodes.size(), 0);

    for (auto it : llvm::enumerate(nodes)) {
      unsigned idx = it.index();
      Operation *op = it.value();

      llvm::SmallSet<unsigned, 8> deps;
      auto addDep = [&](Value v) {
        auto it = valueProducer.find(v);
        if (it == valueProducer.end())
          return;
        unsigned p = it->second;
        if (p == idx)
          return;
        deps.insert(p);
      };

      if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
        addDep(a.getSrc());
      } else {
        for (Value v : op->getOperands())
          addDep(v);
      }

      indeg[idx] = static_cast<unsigned>(deps.size());
      for (unsigned p : deps)
        succ[p].push_back(static_cast<unsigned>(idx));
    }

    auto cmp = [&](unsigned a, unsigned b) { return nodeKey[a] > nodeKey[b]; };
    std::vector<unsigned> heap;
    heap.reserve(nodes.size());
    for (unsigned i = 0; i < nodes.size(); ++i)
      if (indeg[i] == 0)
        heap.push_back(i);
    std::make_heap(heap.begin(), heap.end(), cmp);

    llvm::SmallVector<unsigned> out;
    out.reserve(nodes.size());
    while (!heap.empty()) {
      std::pop_heap(heap.begin(), heap.end(), cmp);
      unsigned n = heap.back();
      heap.pop_back();
      out.push_back(n);
      for (unsigned s : succ[n]) {
        if (--indeg[s] == 0) {
          heap.push_back(s);
          std::push_heap(heap.begin(), heap.end(), cmp);
        }
      }
    }

    if (out.size() != nodes.size())
      return false;

    for (unsigned idx : out)
      ordered.push_back(nodes[idx]);
    return true;
  };

  // eval_comb_pass(): evaluate all combinational ops/assigns.
  //
  // Note: The IR is allowed to have "late" pyc.assign ops (e.g. queue wrappers
  // that defer wiring). To keep C++ simulation correct, eval() runs a small
  // fixed-point iteration that alternates comb evaluation and primitive eval.
  //
  // Guard grouping: consecutive guarded combs that share one 64-bit dirty
  // word are wrapped in a single wordClean(word) probe; when the whole word is
  // clean one load+branch skips up to 64 per-comb checks.
  os << "  inline void eval_comb_pass() {\n";
  llvm::SmallVector<Operation *> ordered;
  const bool blockOrder = !topoOrder(/*includePrims=*/false, ordered);
  if (blockOrder) {
    for (Operation &op : top) {
      if (isa<func::ReturnOp>(op) || isa<pyc::WireOp>(op))
        continue;
      ordered.push_back(&op);
    }
    // Block order may wake a comb that already ran, so one pass can leave the
    // design stale; repeat until every wake has been consumed.
    os << "    for (unsigned _pyc_iter = 0; ; ++_pyc_iter) {\n";
  }

  unsigned openGuardWord = ~0u;
  auto closeGuardGroup = [&]() {
    if (openGuardWord != ~0u) {
      os << "    }\n";
      openGuardWord = ~0u;
    }
  };

  // A wordClean group pays off only when it can skip checks for several
  // combs. In the per-comb dispatch most guarded combs are interleaved with
  // plain wire ops, so only runs of >= 2 consecutive same-word guarded combs
  // are wrapped; a singleton group would add a load+branch on the hot path.
  llvm::SmallVector<int64_t> combGuardWordOf;
  combGuardWordOf.reserve(ordered.size());
  for (Operation *op : ordered) {
    int64_t word = -1;
    if (auto comb = dyn_cast<pyc::CombOp>(*op))
      if (!hasDirtyPolledBoundaryInput(comb))
        word = combIndex.lookup(comb.getOperation()) / 64;
    combGuardWordOf.push_back(word);
  }
  llvm::SmallVector<bool> runMember(ordered.size(), false);
  for (unsigned i = 0; i + 1 < ordered.size(); ++i)
    if (combGuardWordOf[i] >= 0 && combGuardWordOf[i] == combGuardWordOf[i + 1])
      runMember[i] = runMember[i + 1] = true;

  unsigned opIdx = 0;
  for (Operation *op : ordered) {
    if (!runMember[opIdx])
      closeGuardGroup();
    if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
      closeGuardGroup();
      if (getValueCppStorage(a.getDst()) != CppStorageKind::Alias)
        os << "    " << nt.get(a.getDst()) << " = " << nt.get(a.getSrc()) << ";\n";
      ++opIdx;
      continue;
    }
    if (auto comb = dyn_cast<pyc::CombOp>(*op)) {
      unsigned index = combIndex.lookup(comb.getOperation());
      if (!hasDirtyPolledBoundaryInput(comb)) {
        if (runMember[opIdx]) {
          unsigned guardWord = index / 64;
          if (openGuardWord != guardWord) {
            closeGuardGroup();
            os << "    if (!_pyc_comb_dirty.wordClean(" << guardWord
               << "u)) {\n";
            openGuardWord = guardWord;
          }
          os << "      if (_pyc_comb_dirty.test(" << index << "u)) {\n";
          os << "        eval_comb_" << index << "();\n";
          os << "      }\n";
        } else {
          os << "    if (_pyc_comb_dirty.test(" << index << "u)) {\n";
          os << "      eval_comb_" << index << "();\n";
          os << "    }\n";
        }
      } else {
        closeGuardGroup();
        os << "    eval_comb_" << index << "();\n";
      }
      ++opIdx;
      continue;
    }
    if (auto a = dyn_cast<pyc::AssertOp>(*op)) {
      std::string msg = "pyc.assert failed";
      if (auto m = a.getMsgAttr())
        msg = m.getValue().str();
      os << "    if (!" << nt.get(a.getCond()) << ".toBool()) { std::cerr << " << cppStringLiteral(msg)
         << " << \"\\n\"; std::abort(); }\n";
      ++opIdx;
      continue;
    }
    if (isa<arith::ConstantOp,
            pyc::ConstantOp,
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
            pyc::DelayTapOp,
            pyc::ShliOp,
            pyc::LshriOp,
            pyc::AshriOp,
            pyc::ShlOp,
            pyc::LshrOp,
            pyc::AshrOp,
            arith::SelectOp>(*op)) {
      // Plain wire computation must run every pass: it feeds outputs, polled
      // combs, and downstream combs regardless of dirty state, so it must
      // never sit inside a wordClean guard group.
      closeGuardGroup();
      if (failed(emitCombAssign(*op, os, nt, /*ps=*/nullptr, &delayLoopLines)))
        return failure();
      ++opIdx;
      continue;
    }
    if (isa<pyc::FifoOp,
            pyc::AsyncFifoOp,
            pyc::ByteMemOp,
            pyc::SyncMemNMOp,
            pyc::CdcSyncOp,
            pyc::InstanceOp,
            pyc::RegOp,
            pyc::DelayLineOp>(*op)) {
      // Primitives are evaluated in eval(), and regs only tick.
      ++opIdx;
      continue;
    }
    if (isa<func::ReturnOp, pyc::WireOp>(*op)) {
      ++opIdx;
      continue;
    }
    closeGuardGroup();
    ++opIdx;
    return op->emitError("unsupported op for C++ emission: ") << op->getName();
  }
  closeGuardGroup();
  if (blockOrder) {
    os << "    if (_pyc_comb_dirty.empty()) break;\n";
    os << "    if (_pyc_iter >= " << (combs.size() + 2) << "u) {\n";
    os << "      std::cerr << \"pyc eval_comb_pass failed to converge\" << \"\\n\";\n";
    os << "      std::abort();\n";
    os << "    }\n";
    os << "    }\n";
  }
  os << "  }\n\n";

  llvm::SmallVector<Operation *> fullOrdered;
  bool hasFullTopo = topoOrder(/*includePrims=*/true, fullOrdered);

  // Coarse-grained instance parallelism (--cpp-parallel-instances=N).
  //
  // Nothing here changes semantics: the stages are derived from the very same
  // dependency edges topoOrder used, so ordering ops by stage is still a valid
  // topological order and two ops sharing a stage are mutually independent.
  // Instances of one stage may therefore be evaluated on separate threads; the
  // only cross-thread state they touch (the fused-comb dirty words reached by
  // their result fanout) is deferred into a per-instance word buffer and merged
  // by the forking thread after the join, so no atomic and no lost wakeup.
  //
  // With --cpp-parallel-instances=1 (the default) every emission below is the
  // untouched serial path, which keeps the default byte-identical to the base
  // emitter.
  const unsigned requestedInstances =
      resolveCppParallelInstances(opts.parallelInstances);
  const bool parallelInstances = requestedInstances >= 2 && hasFullTopo &&
                                 instInfos.size() >= 2 &&
                                 instInfos.size() <= kMaxParallelInstances;
  const unsigned parallelThreads = parallelInstances ? requestedInstances : 0u;
  const unsigned wakeWords =
      std::max(1u, static_cast<unsigned>((combs.size() + 63) / 64));
  // stage(op) is a valid topological position: ops that share a stage are
  // mutually independent, so the instances of one stage can be forked.
  //
  // Levelization alone is not enough to group instances: two independent
  // instances can sit at different levels only because their *input* comb
  // cones have different depths (in the motivating design one subsystem's
  // ports go through one extra top-level select stage). Sibling instances
  // therefore group by the layer
  // of instance-to-instance same-tick dependencies (all zero for two PE
  // clusters), take the deepest member's level as their fork point, and the
  // remaining ops are re-staged on top of that with the usual
  // "one past the deepest dependency" rule, which keeps the order valid.
  llvm::DenseMap<Operation *, unsigned> opStage;
  unsigned parallelStageCount = 0;
  if (parallelInstances) {
    // producer mirrors topoOrder's valueProducer: results first, then the
    // single driver of each wire (a late pyc.assign overrides, as there).
    llvm::DenseMap<Value, unsigned> producer;
    for (unsigned i = 0; i < fullOrdered.size(); ++i) {
      Operation *op = fullOrdered[i];
      for (Value r : op->getResults())
        producer.try_emplace(r, i);
    }
    for (unsigned i = 0; i < fullOrdered.size(); ++i)
      if (auto a = dyn_cast<pyc::AssignOp>(*fullOrdered[i]))
        producer[a.getDst()] = i;

    auto depValues = [](Operation *op, llvm::SmallVectorImpl<Value> &out) {
      if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
        out.push_back(a.getSrc());
        return;
      }
      for (Value v : op->getOperands())
        out.push_back(v);
    };

    llvm::SmallVector<unsigned> level(fullOrdered.size(), 0);
    llvm::SmallVector<Value, 16> deps;
    for (unsigned i = 0; i < fullOrdered.size(); ++i) {
      deps.clear();
      depValues(fullOrdered[i], deps);
      for (Value v : deps) {
        auto it = producer.find(v);
        if (it == producer.end() || it->second >= i)
          continue;
        level[i] = std::max(level[i], level[it->second] + 1);
      }
    }

    // Instance layer: 1 + deepest instance reachable through same-tick comb
    // logic. The walk stops at instance results, so a pure comb path between
    // two instances counts as a dependency while shared input cone does not.
    llvm::DenseMap<Operation *, unsigned> instLayer;
    llvm::DenseMap<unsigned, unsigned> groupLevel;
    for (unsigned i = 0; i < fullOrdered.size(); ++i) {
      Operation *op = fullOrdered[i];
      if (!isa<pyc::InstanceOp>(*op))
        continue;
      llvm::SmallDenseSet<Operation *, 16> visited;
      llvm::SmallVector<Operation *, 8> instDeps;
      llvm::SmallVector<Value, 32> work;
      depValues(op, work);
      while (!work.empty()) {
        Value v = work.pop_back_val();
        auto it = producer.find(v);
        if (it == producer.end())
          continue;
        Operation *p = fullOrdered[it->second];
        if (p == op)
          continue;
        if (isa<pyc::InstanceOp>(*p)) {
          instDeps.push_back(p);
          continue;
        }
        if (!visited.insert(p).second)
          continue;
        depValues(p, work);
      }
      unsigned layer = 0;
      for (Operation *dep : instDeps) {
        auto it = instLayer.find(dep);
        layer = std::max(layer, it == instLayer.end() ? 0u : it->second + 1);
      }
      instLayer[op] = layer;
      unsigned &deepest = groupLevel[layer];
      deepest = std::max(deepest, level[i]);
    }

    llvm::SmallVector<unsigned> stage(fullOrdered.size(), 0);
    for (unsigned i = 0; i < fullOrdered.size(); ++i) {
      Operation *op = fullOrdered[i];
      unsigned base = level[i];
      if (auto inst = dyn_cast<pyc::InstanceOp>(*op))
        base = std::max(base, groupLevel.lookup(instLayer.lookup(op)));
      deps.clear();
      depValues(op, deps);
      for (Value v : deps) {
        auto it = producer.find(v);
        if (it == producer.end() || it->second >= i)
          continue;
        base = std::max(base, stage[it->second] + 1);
      }
      stage[i] = base;
      opStage[op] = base;
      parallelStageCount = std::max(parallelStageCount, base + 1);
    }
  }

  llvm::SmallVector<std::string> instanceEvalHelperNames;
  instanceEvalHelperNames.reserve(instInfos.size());
  for (unsigned idx = 0; idx < instInfos.size(); ++idx) {
    std::string helperName = "eval_instance_cached_" + std::to_string(idx);
    instanceEvalHelperNames.push_back(helperName);
  }

  auto emitInstanceEvalHelperDefinition = [&](const InstInfo &ii, llvm::StringRef helperName) {
    auto inst = ii.op;
    bool usePackedCache = usePackedInstanceEvalCache(ii);
    os << "  inline bool " << helperName << "(";
    if (parallelInstances)
      os << "std::uint64_t *_pyc_wake";
    os << ") {\n";
    os << "    bool _pyc_inst_changed = false;\n";
    os << "    #ifdef PYC_DISABLE_INSTANCE_EVAL_CACHE\n";
    for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
      std::string inValue = nt.get(inst.getOperand(i));
      os << "    " << ii.member << "->" << ii.inPorts[i] << " = " << inValue << ";\n";
    }
    os << "    " << ii.member << "->eval();\n";
    os << "    _pyc_inst_changed = true;\n";
    os << "    #else\n";
    std::string changedFlag = ii.member + "_eval_cache_changed";
    os << "    bool " << changedFlag << " = !" << ii.member << "_eval_cache_valid;\n";
    auto clockOnlyIn = [&](unsigned i) {
      return i < ii.clockOnlyIn.size() && ii.clockOnlyIn[i];
    };
    auto emitClockOnlyAssigns = [&](llvm::StringRef indent) {
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        if (!clockOnlyIn(i))
          continue;
        os << indent << ii.member << "->" << ii.inPorts[i] << " = "
           << nt.get(inst.getOperand(i)) << ";\n";
      }
    };
    if (usePackedCache) {
      os << "    std::array<std::uint64_t, " << instancePackedCacheWordCount(ii) << "> _pyc_inputs{};\n";
      os << "    std::size_t _pyc_inputs_off = 0;\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        if (clockOnlyIn(i))
          continue;
        std::string inValue = nt.get(inst.getOperand(i));
        os << "    pyc::cpp::appendPackedWireWords(_pyc_inputs, _pyc_inputs_off, " << inValue << ");\n";
      }
      os << "    if (!" << changedFlag << " && (" << ii.member << "_eval_cache_words != _pyc_inputs)) " << changedFlag
         << " = true;\n";
      os << "    if (" << changedFlag << ") {\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        std::string inValue = nt.get(inst.getOperand(i));
        os << "      " << ii.member << "->" << ii.inPorts[i] << " = " << inValue << ";\n";
      }
      os << "      " << ii.member << "->eval();\n";
      os << "      " << ii.member << "_eval_cache_words = _pyc_inputs;\n";
      os << "    } else {\n";
      emitClockOnlyAssigns("      ");
      os << "    }\n";
      os << "    _pyc_inst_changed = " << changedFlag << ";\n";
      os << "    " << ii.member << "_eval_cache_valid = true;\n";
      os << "    #endif\n";
    } else {
      os << "    #ifndef PYC_DISABLE_VERSIONED_INPUT_CACHE\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        if (clockOnlyIn(i))
          continue;
        std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
        std::string inValue = nt.get(inst.getOperand(i));
        std::string verName = ii.member + "_eval_cache_in_ver_" + std::to_string(i);
        std::string seenName = ii.member + "_eval_cache_in_seen_ver_" + std::to_string(i);
        unsigned inW = bitWidth(inst.getOperand(i).getType());
        os << "    if (" << ii.member << "_eval_cache_valid) {\n";
        if (inW <= 64) {
          std::string fpName = ii.member + "_eval_cache_in_fp_" + std::to_string(i);
          os << "      std::uint64_t _pyc_fp_" << i << " = static_cast<std::uint64_t>(" << inValue << ".value());\n";
          os << "      if (" << fpName << " != _pyc_fp_" << i << ") {\n";
          os << "        " << fpName << " = _pyc_fp_" << i << ";\n";
          os << "        " << cacheName << " = " << inValue << ";\n";
          os << "        ++" << verName << ";\n";
          os << "      }\n";
        } else {
          os << "      if (" << cacheName << " != " << inValue << ") {\n";
          os << "        " << cacheName << " = " << inValue << ";\n";
          os << "        ++" << verName << ";\n";
          os << "      }\n";
        }
        os << "    } else {\n";
        os << "      " << cacheName << " = " << inValue << ";\n";
        if (inW <= 64) {
          std::string fpName = ii.member + "_eval_cache_in_fp_" + std::to_string(i);
          os << "      " << fpName << " = static_cast<std::uint64_t>(" << inValue << ".value());\n";
        }
        os << "      ++" << verName << ";\n";
        os << "    }\n";
        os << "    if (!" << changedFlag << " && (" << seenName << " != " << verName << ")) " << changedFlag
           << " = true;\n";
        os << "    " << seenName << " = " << verName << ";\n";
      }
      os << "    if (" << changedFlag << ") {\n";
      emitClockOnlyAssigns("      ");
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        if (clockOnlyIn(i))
          continue;
        std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
        os << "      " << ii.member << "->" << ii.inPorts[i] << " = " << cacheName << ";\n";
      }
      os << "      " << ii.member << "->eval();\n";
      os << "    } else {\n";
      emitClockOnlyAssigns("      ");
      os << "    }\n";
      os << "    #else\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        if (clockOnlyIn(i))
          continue;
        std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
        std::string inValue = nt.get(inst.getOperand(i));
        os << "    if (!" << changedFlag << " && (" << cacheName << " != " << inValue << ")) " << changedFlag
           << " = true;\n";
      }
      os << "    if (" << changedFlag << ") {\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        if (clockOnlyIn(i))
          continue;
        std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
        std::string inValue = nt.get(inst.getOperand(i));
        os << "      " << ii.member << "->" << ii.inPorts[i] << " = " << inValue << ";\n";
        os << "      " << cacheName << " = " << inValue << ";\n";
      }
      emitClockOnlyAssigns("      ");
      os << "      " << ii.member << "->eval();\n";
      os << "    } else {\n";
      emitClockOnlyAssigns("      ");
      os << "    }\n";
      os << "    #endif\n";
      os << "    _pyc_inst_changed = " << changedFlag << ";\n";
      os << "    " << ii.member << "_eval_cache_valid = true;\n";
      os << "    #endif\n";
    }
    // The current IR does not carry a callee output-to-input dependency
    // summary. Keep the conservative child input cache/eval, then publish each
    // output independently and wake only its local direct-comb users.
    os << "    bool _pyc_inst_output_changed = false;\n";
    for (unsigned i = 0; i < inst.getNumResults(); ++i) {
      Value result = inst.getResult(i);
      std::string resultName = nt.get(result);
      std::string childOutput = ii.member + "->" + ii.outPorts[i];
      os << "    if (" << resultName << " != " << childOutput << ") {\n";
      os << "      " << resultName << " = " << childOutput << ";\n";
      os << "      _pyc_inst_output_changed = true;\n";
      emitMetadataCombFanout(result, os, *schedule, combIndex, nt, "      ",
                             parallelInstances);
      os << "    }\n";
    }
    // The fixed-point fallback converges on semantic output changes.
    os << "    _pyc_inst_changed = _pyc_inst_output_changed;\n";
    os << "    return _pyc_inst_changed;\n";
    os << "  }\n\n";
  };

  for (unsigned idx = 0; idx < instInfos.size(); ++idx)
    emitInstanceEvalHelperDefinition(instInfos[idx], instanceEvalHelperNames[idx]);

  auto emitInstanceEvalWithCache =
      [&](const InstInfo &ii, llvm::StringRef indent, llvm::StringRef changedAnyVar = llvm::StringRef()) {
    auto it = instIndex.find(const_cast<pyc::InstanceOp &>(ii.op).getOperation());
    if (it == instIndex.end())
      return;
    llvm::StringRef helperName = instanceEvalHelperNames[it->second];
    if (parallelInstances) {
      // Sequential call site of a wake-deferring helper: buffer the fanout
      // words, then merge them into the shared dirty set in order.
      os << indent << "{\n";
      os << indent << "  std::uint64_t _pyc_wake[" << wakeWords << "] = {};\n";
      if (!changedAnyVar.empty())
        os << indent << "  if (" << helperName << "(_pyc_wake)) " << changedAnyVar << " = true;\n";
      else
        os << indent << "  " << helperName << "(_pyc_wake);\n";
      if (!combs.empty()) {
        os << indent << "  for (unsigned _pyc_w = 0; _pyc_w < " << wakeWords << "u; ++_pyc_w)\n";
        os << indent << "    if (_pyc_wake[_pyc_w]) _pyc_comb_dirty.markWordUnchecked(_pyc_w, _pyc_wake[_pyc_w]);\n";
      }
      os << indent << "}\n";
    } else if (!changedAnyVar.empty()) {
      os << indent << "if (" << helperName << "()) " << changedAnyVar << " = true;\n";
    } else {
      os << indent << helperName << "();\n";
    }
  };

  auto emitPrimitiveOutputSnapshots =
      [&](ArrayRef<Value> outputs, llvm::StringRef indent,
          llvm::StringRef prefix) {
        // Primitive APIs update bound output wires by reference. Snapshot
        // those values around the existing cache/eval path so dirty fanout is
        // based on semantic output changes, while fallback convergence remains
        // conservatively driven by the primitive input cache.
        for (auto [i, output] : llvm::enumerate(outputs))
          os << indent << "const auto " << prefix << "_" << i << " = "
             << nt.get(output) << ";\n";
      };
  auto emitPrimitiveOutputWake =
      [&](ArrayRef<Value> outputs, llvm::StringRef indent,
          llvm::StringRef prefix) {
        std::string nestedIndent = indent.str() + "  ";
        for (auto [i, output] : llvm::enumerate(outputs)) {
          os << indent << "if (" << prefix << "_" << i << " != "
             << nt.get(output) << ") {\n";
          emitMetadataCombFanout(output, os, *schedule, combIndex, nt,
                                 nestedIndent);
          os << indent << "}\n";
        }
      };

  auto emitFifoEvalWithCache =
      [&](pyc::FifoOp fifo, llvm::StringRef indent, llvm::StringRef changedAnyVar = llvm::StringRef()) {
    std::string instName = nt.get(fifo.getInReady()) + "_inst";
    std::string changedFlag = instName + "_eval_cache_changed";
    std::string inValid = nt.get(fifo.getInValid());
    std::string inData = nt.get(fifo.getInData());
    std::string outReady = nt.get(fifo.getOutReady());
    unsigned dataW = bitWidth(fifo.getInData().getType());
    llvm::SmallVector<Value> outputs{fifo.getInReady(), fifo.getOutValid(),
                                     fifo.getOutData()};
    std::string outputSnapshot = "_pyc_" + instName + "_output_before";
    emitPrimitiveOutputSnapshots(outputs, indent, outputSnapshot);
    os << indent << "#ifdef PYC_DISABLE_PRIMITIVE_EVAL_CACHE\n";
    os << indent << instName << ".eval();\n";
    if (!changedAnyVar.empty())
      os << indent << changedAnyVar << " = true;\n";
    os << indent << "#else\n";
    os << indent << "#ifndef PYC_DISABLE_VERSIONED_INPUT_CACHE\n";
    os << indent << "bool " << changedFlag << " = !" << instName << "_eval_cache_valid;\n";
    os << indent << "if (" << instName << "_eval_cache_valid) {\n";
    os << indent << "  std::uint64_t _pyc_fp_v = static_cast<std::uint64_t>(" << inValid << ".value());\n";
    os << indent << "  if (" << instName << "_eval_cache_in_valid_fp != _pyc_fp_v) {\n";
    os << indent << "    " << instName << "_eval_cache_in_valid_fp = _pyc_fp_v;\n";
    os << indent << "    " << instName << "_eval_cache_in_valid = " << inValid << ";\n";
    os << indent << "    ++" << instName << "_eval_cache_in_valid_ver;\n";
    os << indent << "  }\n";
    if (dataW <= 64) {
      os << indent << "  std::uint64_t _pyc_fp_d = static_cast<std::uint64_t>(" << inData << ".value());\n";
      os << indent << "  if (" << instName << "_eval_cache_in_data_fp != _pyc_fp_d) {\n";
      os << indent << "    " << instName << "_eval_cache_in_data_fp = _pyc_fp_d;\n";
      os << indent << "    " << instName << "_eval_cache_in_data = " << inData << ";\n";
      os << indent << "    ++" << instName << "_eval_cache_in_data_ver;\n";
      os << indent << "  }\n";
    } else {
      os << indent << "  if (" << instName << "_eval_cache_in_data != " << inData << ") {\n";
      os << indent << "    " << instName << "_eval_cache_in_data = " << inData << ";\n";
      os << indent << "    ++" << instName << "_eval_cache_in_data_ver;\n";
      os << indent << "  }\n";
    }
    os << indent << "  std::uint64_t _pyc_fp_r = static_cast<std::uint64_t>(" << outReady << ".value());\n";
    os << indent << "  if (" << instName << "_eval_cache_out_ready_fp != _pyc_fp_r) {\n";
    os << indent << "    " << instName << "_eval_cache_out_ready_fp = _pyc_fp_r;\n";
    os << indent << "    " << instName << "_eval_cache_out_ready = " << outReady << ";\n";
    os << indent << "    ++" << instName << "_eval_cache_out_ready_ver;\n";
    os << indent << "  }\n";
    os << indent << "} else {\n";
    os << indent << "  " << instName << "_eval_cache_in_valid = " << inValid << ";\n";
    os << indent << "  " << instName << "_eval_cache_in_valid_fp = static_cast<std::uint64_t>(" << inValid << ".value());\n";
    os << indent << "  ++" << instName << "_eval_cache_in_valid_ver;\n";
    os << indent << "  " << instName << "_eval_cache_in_data = " << inData << ";\n";
    if (dataW <= 64)
      os << indent << "  " << instName << "_eval_cache_in_data_fp = static_cast<std::uint64_t>(" << inData << ".value());\n";
    os << indent << "  ++" << instName << "_eval_cache_in_data_ver;\n";
    os << indent << "  " << instName << "_eval_cache_out_ready = " << outReady << ";\n";
    os << indent << "  " << instName << "_eval_cache_out_ready_fp = static_cast<std::uint64_t>(" << outReady
       << ".value());\n";
    os << indent << "  ++" << instName << "_eval_cache_out_ready_ver;\n";
    os << indent << "}\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_in_valid_seen_ver != " << instName
       << "_eval_cache_in_valid_ver)) " << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_in_data_seen_ver != " << instName
       << "_eval_cache_in_data_ver)) " << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_out_ready_seen_ver != " << instName
       << "_eval_cache_out_ready_ver)) " << changedFlag << " = true;\n";
    os << indent << instName << "_eval_cache_in_valid_seen_ver = " << instName << "_eval_cache_in_valid_ver;\n";
    os << indent << instName << "_eval_cache_in_data_seen_ver = " << instName << "_eval_cache_in_data_ver;\n";
    os << indent << instName << "_eval_cache_out_ready_seen_ver = " << instName << "_eval_cache_out_ready_ver;\n";
    os << indent << "#else\n";
    os << indent << "bool " << changedFlag << " = !" << instName << "_eval_cache_valid;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_in_valid != " << inValid << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_in_data != " << inData << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_out_ready != " << outReady
       << ")) " << changedFlag << " = true;\n";
    os << indent << "#endif\n";
    os << indent << "if (" << changedFlag << ") {\n";
    os << indent << "  " << instName << ".eval();\n";
    os << indent << "}\n";
    if (!changedAnyVar.empty())
      os << indent << "if (" << changedFlag << ") " << changedAnyVar << " = true;\n";
    os << indent << "#ifdef PYC_DISABLE_VERSIONED_INPUT_CACHE\n";
    os << indent << "if (" << changedFlag << ") {\n";
    os << indent << "  " << instName << "_eval_cache_in_valid = " << inValid << ";\n";
    os << indent << "  " << instName << "_eval_cache_in_data = " << inData << ";\n";
    os << indent << "  " << instName << "_eval_cache_out_ready = " << outReady << ";\n";
    os << indent << "}\n";
    os << indent << "#endif\n";
    os << indent << instName << "_eval_cache_valid = true;\n";
    os << indent << "#endif\n";
    emitPrimitiveOutputWake(outputs, indent, outputSnapshot);
  };

  auto emitAsyncFifoEvalWithCache =
      [&](pyc::AsyncFifoOp fifo, llvm::StringRef indent, llvm::StringRef changedAnyVar = llvm::StringRef()) {
    std::string instName = nt.get(fifo.getInReady()) + "_inst";
    std::string changedFlag = instName + "_eval_cache_changed";
    std::string inRst = nt.get(fifo.getInRst());
    std::string outRst = nt.get(fifo.getOutRst());
    std::string inValid = nt.get(fifo.getInValid());
    std::string inData = nt.get(fifo.getInData());
    std::string outReady = nt.get(fifo.getOutReady());
    llvm::SmallVector<Value> outputs{fifo.getInReady(), fifo.getOutValid(),
                                     fifo.getOutData()};
    std::string outputSnapshot = "_pyc_" + instName + "_output_before";
    emitPrimitiveOutputSnapshots(outputs, indent, outputSnapshot);
    os << indent << "#ifdef PYC_DISABLE_PRIMITIVE_EVAL_CACHE\n";
    os << indent << instName << ".eval();\n";
    if (!changedAnyVar.empty())
      os << indent << changedAnyVar << " = true;\n";
    os << indent << "#else\n";
    os << indent << "bool " << changedFlag << " = !" << instName << "_eval_cache_valid;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_in_rst != " << inRst << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_out_rst != " << outRst << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_in_valid != " << inValid
       << ")) " << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_in_data != " << inData
       << ")) " << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_out_ready != " << outReady
       << ")) " << changedFlag << " = true;\n";
    os << indent << "if (" << changedFlag << ") {\n";
    os << indent << "  " << instName << ".eval();\n";
    os << indent << "}\n";
    if (!changedAnyVar.empty())
      os << indent << "if (" << changedFlag << ") " << changedAnyVar << " = true;\n";
    os << indent << "if (" << changedFlag << ") {\n";
    os << indent << "  " << instName << "_eval_cache_in_rst = " << inRst << ";\n";
    os << indent << "  " << instName << "_eval_cache_out_rst = " << outRst << ";\n";
    os << indent << "  " << instName << "_eval_cache_in_valid = " << inValid << ";\n";
    os << indent << "  " << instName << "_eval_cache_in_data = " << inData << ";\n";
    os << indent << "  " << instName << "_eval_cache_out_ready = " << outReady << ";\n";
    os << indent << "}\n";
    os << indent << instName << "_eval_cache_valid = true;\n";
    os << indent << "#endif\n";
    emitPrimitiveOutputWake(outputs, indent, outputSnapshot);
  };

  auto emitByteMemEvalWithCache =
      [&](pyc::ByteMemOp mem, llvm::StringRef indent, llvm::StringRef changedAnyVar = llvm::StringRef()) {
    std::string instName = byteMemInstName.lookup(mem.getOperation());
    std::string changedFlag = instName + "_eval_cache_changed";
    std::string raddr = nt.get(mem.getRaddr());
    std::string wvalid = nt.get(mem.getWvalid());
    std::string waddr = nt.get(mem.getWaddr());
    std::string wdata = nt.get(mem.getWdata());
    std::string wstrb = nt.get(mem.getWstrb());
    llvm::SmallVector<Value> outputs{mem.getRdata()};
    std::string outputSnapshot = "_pyc_" + instName + "_output_before";
    emitPrimitiveOutputSnapshots(outputs, indent, outputSnapshot);
    os << indent << "#ifdef PYC_DISABLE_PRIMITIVE_EVAL_CACHE\n";
    os << indent << instName << ".eval();\n";
    if (!changedAnyVar.empty())
      os << indent << changedAnyVar << " = true;\n";
    os << indent << "#else\n";
    os << indent << "bool " << changedFlag << " = !" << instName << "_eval_cache_valid;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_raddr != " << raddr << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_wvalid != " << wvalid << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_waddr != " << waddr << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_wdata != " << wdata << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_wstrb != " << wstrb << ")) "
       << changedFlag << " = true;\n";
    os << indent << "if (" << changedFlag << ") {\n";
    os << indent << "  " << instName << ".eval();\n";
    os << indent << "}\n";
    if (!changedAnyVar.empty())
      os << indent << "if (" << changedFlag << ") " << changedAnyVar << " = true;\n";
    os << indent << "if (" << changedFlag << ") {\n";
    os << indent << "  " << instName << "_eval_cache_raddr = " << raddr << ";\n";
    os << indent << "  " << instName << "_eval_cache_wvalid = " << wvalid << ";\n";
    os << indent << "  " << instName << "_eval_cache_waddr = " << waddr << ";\n";
    os << indent << "  " << instName << "_eval_cache_wdata = " << wdata << ";\n";
    os << indent << "  " << instName << "_eval_cache_wstrb = " << wstrb << ";\n";
    os << indent << "}\n";
    os << indent << instName << "_eval_cache_valid = true;\n";
    os << indent << "#endif\n";
    emitPrimitiveOutputWake(outputs, indent, outputSnapshot);
  };

  // Guard-group state for one emitted eval function: consecutive guarded
  // combs sharing one 64-bit dirty word share a single wordClean probe.
  struct CombGuardGroupState {
    unsigned openGuardWord = ~0u;
  };
  auto closeCombGuardGroup = [&](CombGuardGroupState &state, llvm::StringRef indent) {
    if (state.openGuardWord != ~0u) {
      os << indent << "}\n";
      state.openGuardWord = ~0u;
    }
  };

  // A wordClean group pays off only when it can skip checks for several
  // combs (see eval_comb_pass above). Runs are computed on the exact emitted
  // sequence so a group never degenerates to a single member.
  auto buildCombRunMember = [&](llvm::ArrayRef<Operation *> ops, unsigned begin,
                                unsigned end) -> llvm::DenseMap<Operation *, bool> {
    llvm::DenseMap<Operation *, bool> member;
    llvm::SmallVector<int64_t> words(end - begin, -1);
    for (unsigned i = begin; i < end; ++i) {
      int64_t word = -1;
      if (auto comb = dyn_cast<pyc::CombOp>(ops[i]))
        if (!hasDirtyPolledBoundaryInput(comb))
          word = combIndex.lookup(comb.getOperation()) / 64;
      words[i - begin] = word;
    }
    for (unsigned i = 0; i + 1 < words.size(); ++i)
      if (words[i] >= 0 && words[i] == words[i + 1]) {
        member[ops[begin + i]] = true;
        member[ops[begin + i + 1]] = true;
      }
    return member;
  };

  auto emitEvalNode =
      [&](Operation *op, llvm::StringRef indent, llvm::StringRef changedAnyVar = llvm::StringRef(),
          CombGuardGroupState *guard = nullptr,
          const llvm::DenseMap<Operation *, bool> *runMember = nullptr) -> LogicalResult {
    if (guard) {
      bool inRun = false;
      if (auto c = dyn_cast<pyc::CombOp>(*op))
        inRun = !hasDirtyPolledBoundaryInput(c) && runMember && runMember->lookup(op);
      if (!inRun)
        closeCombGuardGroup(*guard, indent);
    }
    if (auto fifo = dyn_cast<pyc::FifoOp>(*op)) {
      emitFifoEvalWithCache(fifo, indent, changedAnyVar);
      return success();
    }
    if (auto fifo = dyn_cast<pyc::AsyncFifoOp>(*op)) {
      emitAsyncFifoEvalWithCache(fifo, indent, changedAnyVar);
      return success();
    }
    if (auto mem = dyn_cast<pyc::ByteMemOp>(*op)) {
      emitByteMemEvalWithCache(mem, indent, changedAnyVar);
      return success();
    }
    if (auto inst = dyn_cast<pyc::InstanceOp>(*op)) {
      auto it = instIndex.find(inst.getOperation());
      if (it == instIndex.end())
        return inst.emitError("internal error: missing instance metadata");
      auto &ii = instInfos[it->second];
      emitInstanceEvalWithCache(ii, indent, changedAnyVar);
      return success();
    }
    if (auto a = dyn_cast<pyc::AssertOp>(*op)) {
      std::string msg = "pyc.assert failed";
      if (auto m = a.getMsgAttr())
        msg = m.getValue().str();
      os << indent << "if (!" << nt.get(a.getCond()) << ".toBool()) { std::cerr << " << cppStringLiteral(msg)
         << " << \"\\n\"; std::abort(); }\n";
      return success();
    }
    if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
      if (getValueCppStorage(a.getDst()) != CppStorageKind::Alias)
        os << indent << nt.get(a.getDst()) << " = " << nt.get(a.getSrc()) << ";\n";
      return success();
    }
    if (auto comb = dyn_cast<pyc::CombOp>(*op)) {
      unsigned index = combIndex.lookup(comb.getOperation());
      if (!hasDirtyPolledBoundaryInput(comb)) {
        if (guard && runMember && runMember->lookup(op)) {
          unsigned guardWord = index / 64;
          if (guard->openGuardWord != guardWord) {
            closeCombGuardGroup(*guard, indent);
            os << indent << "if (!_pyc_comb_dirty.wordClean(" << guardWord
               << "u)) {\n";
            guard->openGuardWord = guardWord;
          }
          os << indent << "  if (_pyc_comb_dirty.test(" << index << "u)) {\n";
          os << indent << "    eval_comb_" << index << "();\n";
          os << indent << "  }\n";
        } else {
          os << indent << "if (_pyc_comb_dirty.test(" << index << "u)) {\n";
          os << indent << "  eval_comb_" << index << "();\n";
          os << indent << "}\n";
        }
      } else {
        os << indent << "eval_comb_" << index << "();\n";
      }
      return success();
    }
    if (isa<arith::ConstantOp,
            pyc::ConstantOp,
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
            pyc::DelayTapOp,
            pyc::ShliOp,
            pyc::LshriOp,
            pyc::AshriOp,
            pyc::ShlOp,
            pyc::LshrOp,
            pyc::AshrOp,
            arith::SelectOp>(*op)) {
      if (failed(emitCombAssign(*op, os, nt, /*ps=*/nullptr, &delayLoopLines)))
        return failure();
      return success();
    }
    return op->emitError("unsupported op for C++ emission: ") << op->getName();
  };

  struct SccCompPlan {
    llvm::SmallVector<unsigned> nodes;
    llvm::SmallVector<unsigned> succ;
    unsigned indeg = 0;
    std::string key;
    bool cyclic = false;
  };

  auto buildEvalGraph = [&](llvm::SmallVector<Operation *> &nodes,
                            llvm::SmallVector<std::string> &nodeKeys,
                            llvm::SmallVector<llvm::SmallVector<unsigned>> &succ,
                            llvm::SmallVector<unsigned> &indeg) -> bool {
    nodes.clear();
    nodeKeys.clear();
    succ.clear();
    indeg.clear();

    llvm::DenseMap<Operation *, unsigned> nodeIndex;
    for (Operation &op : top) {
      if (isa<func::ReturnOp>(op) || isa<pyc::WireOp>(op) || isa<pyc::RegOp>(op) ||
          isa<pyc::DelayLineOp>(op) || isa<pyc::SyncMemNMOp>(op) || isa<pyc::CdcSyncOp>(op))
        continue;

      unsigned idx = static_cast<unsigned>(nodes.size());
      nodes.push_back(&op);
      nodeIndex.try_emplace(&op, idx);
      if (auto a = dyn_cast<pyc::AssignOp>(op))
        nodeKeys.push_back(nt.get(a.getDst()));
      else if (op.getNumResults() > 0)
        nodeKeys.push_back(nt.get(op.getResult(0)));
      else
        nodeKeys.push_back(sanitizeId(op.getName().getStringRef()) + "_" + std::to_string(idx));
    }

    if (nodes.empty())
      return false;

    llvm::DenseMap<Value, unsigned> valueProducer;
    llvm::DenseMap<Value, unsigned> wireAssign;
    llvm::DenseMap<Value, unsigned> wireAssignCount;
    for (auto it : llvm::enumerate(nodes)) {
      unsigned idx = static_cast<unsigned>(it.index());
      Operation *op = it.value();
      for (Value r : op->getResults())
        valueProducer.try_emplace(r, idx);
      if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
        Value dst = a.getDst();
        unsigned &cnt = wireAssignCount[dst];
        cnt++;
        if (cnt == 1)
          wireAssign[dst] = idx;
      }
    }
    for (auto &it : wireAssignCount) {
      if (it.second > 1)
        return false;
    }
    for (auto &it : wireAssign)
      valueProducer[it.first] = it.second;

    succ.assign(nodes.size(), {});
    indeg.assign(nodes.size(), 0u);
    for (auto it : llvm::enumerate(nodes)) {
      unsigned idx = static_cast<unsigned>(it.index());
      Operation *op = it.value();
      llvm::SmallSet<unsigned, 8> deps;
      auto addDep = [&](Value v) {
        auto pIt = valueProducer.find(v);
        if (pIt == valueProducer.end())
          return;
        unsigned p = pIt->second;
        if (p == idx)
          return;
        deps.insert(p);
      };
      if (auto a = dyn_cast<pyc::AssignOp>(*op))
        addDep(a.getSrc());
      else
        for (Value v : op->getOperands())
          addDep(v);
      indeg[idx] = static_cast<unsigned>(deps.size());
      for (unsigned p : deps)
        succ[p].push_back(idx);
    }
    return true;
  };

  llvm::SmallVector<Operation *> sccNodes;
  llvm::SmallVector<std::string> sccNodeKeys;
  llvm::SmallVector<llvm::SmallVector<unsigned>> sccSucc;
  llvm::SmallVector<unsigned> sccIndeg;
  llvm::SmallVector<SccCompPlan> sccPlans;
  llvm::SmallVector<unsigned> sccCompOrder;
  bool hasSccWorklistPlan = false;

  if (buildEvalGraph(sccNodes, sccNodeKeys, sccSucc, sccIndeg)) {
    std::vector<int> index(sccNodes.size(), -1);
    std::vector<int> low(sccNodes.size(), 0);
    std::vector<unsigned> stack;
    std::vector<bool> inStack(sccNodes.size(), false);
    llvm::SmallVector<unsigned> nodeToComp(sccNodes.size(), 0);
    int nextIndex = 0;

    std::function<void(unsigned)> strongconnect = [&](unsigned v) {
      index[v] = nextIndex;
      low[v] = nextIndex;
      ++nextIndex;
      stack.push_back(v);
      inStack[v] = true;

      for (unsigned w : sccSucc[v]) {
        if (index[w] < 0) {
          strongconnect(w);
          low[v] = std::min(low[v], low[w]);
        } else if (inStack[w]) {
          low[v] = std::min(low[v], index[w]);
        }
      }

      if (low[v] == index[v]) {
        SccCompPlan comp;
        while (!stack.empty()) {
          unsigned w = stack.back();
          stack.pop_back();
          inStack[w] = false;
          nodeToComp[w] = static_cast<unsigned>(sccPlans.size());
          comp.nodes.push_back(w);
          if (w == v)
            break;
        }
        std::sort(comp.nodes.begin(), comp.nodes.end(), [&](unsigned a, unsigned b) { return sccNodeKeys[a] < sccNodeKeys[b]; });
        comp.key = sccNodeKeys[comp.nodes.front()];
        sccPlans.push_back(std::move(comp));
      }
    };

    for (unsigned v = 0; v < sccNodes.size(); ++v)
      if (index[v] < 0)
        strongconnect(v);

    llvm::SmallVector<llvm::SmallSet<unsigned, 8>> compSuccSet(sccPlans.size());
    for (unsigned v = 0; v < sccNodes.size(); ++v) {
      unsigned cv = nodeToComp[v];
      for (unsigned w : sccSucc[v]) {
        unsigned cw = nodeToComp[w];
        if (cv == cw) {
          if (v == w || sccPlans[cv].nodes.size() > 1)
            sccPlans[cv].cyclic = true;
          continue;
        }
        compSuccSet[cv].insert(cw);
      }
      if (sccPlans[cv].nodes.size() > 1)
        sccPlans[cv].cyclic = true;
    }

    for (unsigned c = 0; c < sccPlans.size(); ++c) {
      for (unsigned s : compSuccSet[c]) {
        sccPlans[c].succ.push_back(s);
        sccPlans[s].indeg++;
      }
      std::sort(sccPlans[c].succ.begin(), sccPlans[c].succ.end(),
                [&](unsigned a, unsigned b) { return sccPlans[a].key < sccPlans[b].key; });
    }

    auto cmpComp = [&](unsigned a, unsigned b) { return sccPlans[a].key > sccPlans[b].key; };
    std::vector<unsigned> heap;
    for (unsigned i = 0; i < sccPlans.size(); ++i)
      if (sccPlans[i].indeg == 0)
        heap.push_back(i);
    std::make_heap(heap.begin(), heap.end(), cmpComp);
    while (!heap.empty()) {
      std::pop_heap(heap.begin(), heap.end(), cmpComp);
      unsigned c = heap.back();
      heap.pop_back();
      sccCompOrder.push_back(c);
      for (unsigned s : sccPlans[c].succ) {
        if (--sccPlans[s].indeg == 0) {
          heap.push_back(s);
          std::push_heap(heap.begin(), heap.end(), cmpComp);
        }
      }
    }

    if (sccCompOrder.size() == sccPlans.size()) {
      for (const auto &comp : sccPlans) {
        if (comp.cyclic) {
          hasSccWorklistPlan = true;
          break;
        }
      }
    } else {
      hasSccWorklistPlan = false;
    }
  }

  // eval(): attempt a single-pass topological netlist schedule; if the graph has
  // cycles, fall back to a bounded fixed-point iteration.
  if (!hasFullTopo) {
    unsigned numPrims = static_cast<unsigned>(instInfos.size() + fifos.size() + asyncFifos.size() + byteMems.size());

    std::vector<std::string> primGroupMethods;
    if (numPrims > 0) {
      constexpr unsigned kPrimGroupSize = 64;
      unsigned groupIdx = 0;
      unsigned inGroup = 0;
      auto openGroup = [&]() {
        std::string methodName = "eval_prim_group_" + std::to_string(groupIdx++);
        primGroupMethods.push_back(methodName);
        os << "  inline void " << methodName << "(bool &_pyc_prim_changed) {\n";
        inGroup = 0;
      };
      auto closeGroup = [&]() { os << "  }\n\n"; };
      auto ensureGroup = [&]() {
        if (primGroupMethods.empty() || inGroup >= kPrimGroupSize) {
          if (!primGroupMethods.empty())
            closeGroup();
          openGroup();
        }
      };
      auto bumpGroup = [&]() { ++inGroup; };

      for (const auto &ii : instInfos) {
        ensureGroup();
        emitInstanceEvalWithCache(ii, "    ", "_pyc_prim_changed");
        bumpGroup();
      }
      for (auto fifo : fifos) {
        ensureGroup();
        emitFifoEvalWithCache(fifo, "    ", "_pyc_prim_changed");
        bumpGroup();
      }
      for (auto fifo : asyncFifos) {
        ensureGroup();
        emitAsyncFifoEvalWithCache(fifo, "    ", "_pyc_prim_changed");
        bumpGroup();
      }
      for (auto mem : byteMems) {
        ensureGroup();
        emitByteMemEvalWithCache(mem, "    ", "_pyc_prim_changed");
        bumpGroup();
      }
      if (!primGroupMethods.empty())
        closeGroup();
    }

    os << "  inline void eval_fixpoint_fallback_path() {\n";
    if (numPrims > 0) {
      os << "    for (unsigned _i = 0; _i < " << numPrims << "u; ++_i) {\n";
      os << "      bool _pyc_prim_changed = false;\n";
      for (const std::string &methodName : primGroupMethods)
        os << "      " << methodName << "(_pyc_prim_changed);\n";
      os << "      eval_comb_pass();\n";
      os << "      if (!_pyc_prim_changed) break;\n";
      os << "    }\n";
    }
    os << "  }\n\n";

    if (hasSccWorklistPlan && numPrims > 0) {
      std::vector<std::string> sccCompMethods;
      sccCompMethods.reserve(sccCompOrder.size());
      constexpr unsigned kSccNodeChunk = 256;
      unsigned compOrdinal = 0;
      for (unsigned c : sccCompOrder) {
        const auto &comp = sccPlans[c];
        std::string methodName = "eval_scc_comp_" + std::to_string(compOrdinal++);
        std::vector<std::string> partMethods;
        partMethods.reserve((comp.nodes.size() + kSccNodeChunk - 1) / kSccNodeChunk);

        for (size_t begin = 0, partIdx = 0; begin < comp.nodes.size(); begin += kSccNodeChunk, ++partIdx) {
          size_t end = std::min(comp.nodes.size(), begin + static_cast<size_t>(kSccNodeChunk));
          std::string partName = methodName + "_part_" + std::to_string(partIdx);
          partMethods.push_back(partName);
          if (comp.cyclic) {
            os << "  inline void " << partName << "(bool &_pyc_prim_changed) {\n";
            for (size_t i = begin; i < end; ++i)
              if (failed(emitEvalNode(sccNodes[comp.nodes[i]], "    ", "_pyc_prim_changed")))
                return failure();
          } else {
            os << "  inline void " << partName << "() {\n";
            for (size_t i = begin; i < end; ++i)
              if (failed(emitEvalNode(sccNodes[comp.nodes[i]], "    ")))
                return failure();
          }
          os << "  }\n\n";
        }

        sccCompMethods.push_back(methodName);
        os << "  inline void " << methodName << "() {\n";
        if (comp.cyclic) {
          unsigned iterCap = std::max(2u, numPrims + static_cast<unsigned>(comp.nodes.size()) + 2u);
          os << "    bool _pyc_converged = false;\n";
          os << "    for (unsigned _pyc_iter = 0; _pyc_iter < " << iterCap << "u; ++_pyc_iter) {\n";
          os << "      bool _pyc_prim_changed = false;\n";
          for (const std::string &partName : partMethods)
            os << "      " << partName << "(_pyc_prim_changed);\n";
          os << "      if (!_pyc_prim_changed) { _pyc_converged = true; break; }\n";
          os << "    }\n";
          os << "    if (!_pyc_converged) {\n";
          os << "      std::cerr << \"pyc SCC fallback failed to converge\" << \"\\n\";\n";
          os << "      std::abort();\n";
          os << "    }\n";
        } else {
          for (const std::string &partName : partMethods)
            os << "    " << partName << "();\n";
        }
        os << "  }\n\n";
      }

      os << "  inline void eval_fast_scc_path() {\n";
      for (const std::string &methodName : sccCompMethods)
        os << "    " << methodName << "();\n";
      os << "  }\n\n";
    }
  }

  std::vector<std::string> topoEvalMethods;
  std::vector<std::vector<std::string>> stageEvalMethods;
  std::vector<std::vector<unsigned>> stageInstIndices;
  if (parallelInstances) {
    stageEvalMethods.resize(parallelStageCount);
    stageInstIndices.resize(parallelStageCount);
    std::vector<std::vector<Operation *>> stageOps(parallelStageCount);
    for (Operation *op : fullOrdered) {
      unsigned stage = opStage.lookup(op);
      if (auto inst = dyn_cast<pyc::InstanceOp>(*op)) {
        auto it = instIndex.find(inst.getOperation());
        if (it != instIndex.end()) {
          stageInstIndices[stage].push_back(it->second);
          continue;
        }
      }
      stageOps[stage].push_back(op);
    }
    unsigned stageChunkNodes = std::max(1u, opts.evalTopoChunkNodes);
    for (unsigned stage = 0; stage < parallelStageCount; ++stage) {
      auto &ops = stageOps[stage];
      for (unsigned begin = 0, part = 0; begin < ops.size();
           begin += stageChunkNodes, ++part) {
        unsigned end = std::min<unsigned>(static_cast<unsigned>(ops.size()),
                                         begin + stageChunkNodes);
        std::string methodName = "eval_stage_" + std::to_string(stage) +
                                 "_part_" + std::to_string(part);
        stageEvalMethods[stage].push_back(methodName);
        os << "  inline void " << methodName << "() {\n";
        CombGuardGroupState stageGuard;
        llvm::DenseMap<Operation *, bool> stageRunMember =
            buildCombRunMember(ops, begin, end);
        for (unsigned i = begin; i < end; ++i)
          if (failed(emitEvalNode(ops[i], "    ", llvm::StringRef(), &stageGuard,
                                  &stageRunMember)))
            return failure();
        closeCombGuardGroup(stageGuard, "    ");
        os << "  }\n\n";
      }
    }
  } else if (hasFullTopo && !fullOrdered.empty()) {
    unsigned evalTopoChunkNodes = std::max(1u, opts.evalTopoChunkNodes);
    if (fullOrdered.size() > evalTopoChunkNodes) {
      topoEvalMethods.reserve((fullOrdered.size() + evalTopoChunkNodes - 1) / evalTopoChunkNodes);
      for (unsigned begin = 0, chunkIdx = 0; begin < fullOrdered.size(); begin += evalTopoChunkNodes, ++chunkIdx) {
        unsigned end = std::min<unsigned>(static_cast<unsigned>(fullOrdered.size()), begin + evalTopoChunkNodes);
        std::string methodName = "eval_topo_part_" + std::to_string(chunkIdx);
        topoEvalMethods.push_back(methodName);
        os << "  inline void " << methodName << "() {\n";
        CombGuardGroupState topoGuard;
        llvm::DenseMap<Operation *, bool> chunkRunMember = buildCombRunMember(fullOrdered, begin, end);
        for (unsigned i = begin; i < end; ++i)
          if (failed(emitEvalNode(fullOrdered[i], "    ", llvm::StringRef(), &topoGuard, &chunkRunMember)))
            return failure();
        closeCombGuardGroup(topoGuard, "    ");
        os << "  }\n\n";
      }
    }
  }

  os << "  void eval() {\n";
  if (parallelInstances) {
    for (unsigned stage = 0; stage < parallelStageCount; ++stage) {
      for (const std::string &methodName : stageEvalMethods[stage])
        os << "    " << methodName << "();\n";
      const std::vector<unsigned> &insts = stageInstIndices[stage];
      if (insts.empty())
        continue;
      // One fork per stage. Each instance publishes its result wires and its
      // comb dirty words into its own buffer; the forking thread merges the
      // buffers in instance order, so the shared dirty set never sees two
      // concurrent writers.
      os << "    {\n";
      os << "      std::uint64_t _pyc_wake[" << insts.size() << "]["
         << wakeWords << "] = {};\n";
      if (insts.size() == 1) {
        os << "      eval_instance_cached_" << insts[0] << "(_pyc_wake[0]);\n";
      } else {
        os << "      pyc::cpp::ForkJoinPool<" << parallelThreads
           << ">::instance().run(" << insts.size()
           << ", [&](unsigned _pyc_worker) {\n";
        os << "        switch (_pyc_worker) {\n";
        for (unsigned slot = 0; slot < insts.size(); ++slot)
          os << "          case " << slot << "u: eval_instance_cached_"
             << insts[slot] << "(_pyc_wake[" << slot << "]); return;\n";
        os << "        }\n";
        os << "      });\n";
      }
      if (!combs.empty()) {
        os << "      for (unsigned _pyc_i = 0; _pyc_i < " << insts.size()
           << "u; ++_pyc_i)\n";
        os << "        for (unsigned _pyc_w = 0; _pyc_w < " << wakeWords
           << "u; ++_pyc_w)\n";
        os << "          if (_pyc_wake[_pyc_i][_pyc_w])\n";
        os << "            _pyc_comb_dirty.markWordUnchecked(_pyc_w, _pyc_wake[_pyc_i][_pyc_w]);\n";
      }
      os << "    }\n";
    }
  } else if (hasFullTopo) {
    if (!topoEvalMethods.empty()) {
      for (const std::string &methodName : topoEvalMethods)
        os << "    " << methodName << "();\n";
    } else {
      CombGuardGroupState evalGuard;
      llvm::DenseMap<Operation *, bool> evalRunMember = buildCombRunMember(fullOrdered, 0, fullOrdered.size());
      for (Operation *op : fullOrdered)
        if (failed(emitEvalNode(op, "    ", llvm::StringRef(), &evalGuard, &evalRunMember)))
          return failure();
      closeCombGuardGroup(evalGuard, "    ");
    }
  } else {
    os << "    eval_comb_pass();\n";
    unsigned numPrims = static_cast<unsigned>(instInfos.size() + fifos.size() + asyncFifos.size() + byteMems.size());
    if (hasSccWorklistPlan && numPrims > 0) {
      os << "    if (_pyc_sim_fast_enable) {\n";
      os << "      #ifndef PYC_DISABLE_SCC_WORKLIST_EVAL\n";
      os << "      eval_fast_scc_path();\n";
      os << "      #else\n";
      os << "      eval_fixpoint_fallback_path();\n";
      os << "      #endif\n";
      os << "    } else {\n";
      os << "      eval_fixpoint_fallback_path();\n";
      os << "    }\n";
    } else {
      os << "    eval_fixpoint_fallback_path();\n";
    }
  }

  // Connect return values to output ports.
  auto ret = dyn_cast_or_null<func::ReturnOp>(f.getBody().front().getTerminator());
  if (!ret)
    return f.emitError("missing return");
  for (auto [i, v] : llvm::enumerate(ret.getOperands()))
    os << "    if (" << outNames[i] << " != " << nt.get(v) << ") "
       << outNames[i] << " = " << nt.get(v) << ";\n";

  os << "  }\n\n";

  // tick_compute/tick_commit: two-phase sequential update (hierarchy-aware).
  //
  // Large designs can produce enormous tick bodies (notably JanusBccBackendCompat), which
  // makes a single translation unit fragile and slow to compile. Split tick into helper
  // parts so --cpp-split=module can shard tick across multiple .cpp files.
  constexpr unsigned kTickChunk = 256;

  auto emitStateTickCompute =
      [&](llvm::StringRef key, OperandRange operands, llvm::StringRef indent,
          auto &&emitCall) {
        std::string dirty = "_pyc_tick_dirty_" + key.str();
        os << indent << "bool " << dirty << " = !" << key
           << "_tick_inputs_valid;\n";
        for (auto [index, operand] : llvm::enumerate(operands)) {
          os << indent << "if (" << key << "_tick_input_" << index << " != "
             << nt.get(operand) << ") {\n";
          os << indent << "  " << key << "_tick_input_" << index << " = "
             << nt.get(operand) << ";\n";
          os << indent << "  " << dirty << " = true;\n";
          os << indent << "}\n";
        }
        os << indent << key << "_tick_inputs_valid = true;\n";
        os << indent << "if (" << dirty << ") {\n";
        emitCall((indent + "  ").str());
        os << indent << "}\n";
      };

  auto emitTickComputePart = [&](unsigned begin, unsigned end, unsigned partIdx) {
    os << "  inline void tick_compute_part_" << partIdx << "() {\n";
    // Sub-modules (inputs + tick_compute).
    for (unsigned i = begin; i < end && i < instInfos.size(); ++i) {
      const auto &ii = instInfos[i];
      auto inst = ii.op;
      emitStateTickCompute(
          ii.member, inst->getOperands(), "    ",
          [&](llvm::StringRef indent) {
            for (unsigned j = 0; j < inst.getNumOperands(); ++j)
              os << indent << ii.member << "->" << ii.inPorts[j] << " = "
                 << nt.get(inst.getOperand(j)) << ";\n";
            os << indent << ii.member << "->tick_compute();\n";
          });
    }
    os << "  }\n\n";
  };

  auto emitTickCommitPart = [&](unsigned begin, unsigned end, unsigned partIdx) {
    os << "  inline void tick_commit_part_" << partIdx << "() {\n";
    // Sub-modules.
    for (unsigned i = begin; i < end && i < instInfos.size(); ++i)
      os << "    " << instInfos[i].member << "->tick_commit();\n";
    os << "  }\n\n";
  };

  // Parallel path: one tiny method per instance, so the fork body stays a flat
  // switch and tick_compute/tick_commit can fork every submodule in one group
  // (next-state sampling and commit are independent per instance: each reads
  // only its own ports and its own inputs, and writes only its own state).
  auto emitTickInstanceMethods = [&](unsigned idx) {
    const auto &ii = instInfos[idx];
    auto inst = ii.op;
    os << "  inline void tick_compute_inst_" << idx << "() {\n";
    emitStateTickCompute(
        ii.member, inst->getOperands(), "    ", [&](llvm::StringRef indent) {
          for (unsigned j = 0; j < inst.getNumOperands(); ++j)
            os << indent << ii.member << "->" << ii.inPorts[j] << " = "
               << nt.get(inst.getOperand(j)) << ";\n";
          os << indent << ii.member << "->tick_compute();\n";
        });
    os << "  }\n\n";
    os << "  inline void tick_commit_inst_" << idx << "() {\n";
    os << "    " << ii.member << "->tick_commit();\n";
    os << "  }\n\n";
  };

  // Emit chunked submodule tick helpers.
  unsigned subParts = 0;
  if (!instInfos.empty()) {
    if (parallelInstances) {
      for (unsigned i = 0; i < instInfos.size(); ++i)
        emitTickInstanceMethods(i);
    } else {
      for (unsigned b = 0; b < instInfos.size(); b += kTickChunk)
        emitTickComputePart(b, std::min<unsigned>(static_cast<unsigned>(instInfos.size()), b + kTickChunk), subParts++);
      unsigned commitParts = 0;
      for (unsigned b = 0; b < instInfos.size(); b += kTickChunk)
        emitTickCommitPart(b, std::min<unsigned>(static_cast<unsigned>(instInfos.size()), b + kTickChunk), commitParts++);
    }
  }

  os << "  void tick_compute() {\n";
  if (!instInfos.empty()) {
    if (parallelInstances) {
      os << "    // Sub-modules: independent next-state sampling, forked.\n";
      os << "    pyc::cpp::ForkJoinPool<" << parallelThreads << ">::instance().run("
         << instInfos.size() << ", [&](unsigned _pyc_worker) {\n";
      os << "      switch (_pyc_worker) {\n";
      for (unsigned i = 0; i < instInfos.size(); ++i)
        os << "        case " << i << "u: tick_compute_inst_" << i << "(); return;\n";
      os << "      }\n";
      os << "    });\n";
    } else {
      os << "    // Sub-modules.\n";
      for (unsigned i = 0; i < subParts; ++i)
        os << "    tick_compute_part_" << i << "();\n";
    }
  }
  os << "    // Local sequential primitives.\n";
  // Call every edge-driven leaf. arg is the already-classified clock level,
  // so leaves never re-read the clock wire on the fast path.
  auto emitEdgeCall = [&](const char *method, llvm::StringRef indent,
                          const char *arg = nullptr) {
    for (auto r : regs)
      os << indent << nt.get(r.getQ()) << "_inst->" << method << "("
         << (arg ? arg : "") << ");\n";
    for (auto delay : delayLines)
      os << indent << nt.get(delay.getQ()) << "_inst->" << method << "("
         << (arg ? arg : "") << ");\n";
  };
  auto emitTapUpdates = [&](llvm::StringRef indent) -> LogicalResult {
    for (auto tap : delayTaps) {
      auto delay = tap.getLine().getDefiningOp<pyc::DelayLineOp>();
      auto depth = tap->getAttrOfType<IntegerAttr>("depth");
      if (!delay || !depth)
        return tap.emitError("invalid delay tap source or depth");
      std::string tapExpr;
      if (auto idAttr = delay->getAttrOfType<IntegerAttr>("pyc.loop_id")) {
        // The tap's own depth indexes the stage, not the loop; the loop
        // key is (clock id, lane depth).
        if (auto da = delay->getAttrOfType<IntegerAttr>("depth")) {
          const unsigned w = bitWidth(delay.getQ().getType());
          const int64_t key =
              idAttr.getValue().getSExtValue() * 256 +
              da.getValue().getZExtValue();
          auto it = delayLoopLines.find(key);
          if (it != delayLoopLines.end()) {
            long lane = 0;
            bool hit = false;
            for (pyc::DelayLineOp c : it->second) {
              if (c == delay) { hit = true; break; }
              ++lane;
            }
            if (hit)
              tapExpr = "delay_loop_" + std::to_string(key) +
                        ".lane_tap_next<" + std::to_string(w) + ">(" +
                        std::to_string(lane) + "u, " +
                        std::to_string(depth.getValue().getZExtValue()) + "u)";
          }
        }
      }
      if (tapExpr.empty())
        tapExpr = nt.get(delay.getQ()) + "_inst->tap_next(" +
                  std::to_string(depth.getInt()) + "u)";
      os << indent << nt.get(tap.getTap()) << "_qNext = " << tapExpr << ";\n";
    }
    return success();
  };
  if (fastEdge) {
    // One edge check for every register on this clock. A falling edge after
    // commit only keeps clkPrev; it does not sample next-state again.
    if (!fusedRegLoopIds.empty())
      os << "    bool _pyc_fused_rise = false;\n";
    os << "    {\n";
    os << "      bool _pyc_now = " << fastClkName << ".toBool();\n";
    os << "      bool _pyc_rise = !_pyc_clk_prev && _pyc_now;\n";
    os << "      bool _pyc_fall = _pyc_clk_prev && !_pyc_now;\n";
    os << "      _pyc_clk_prev = _pyc_now;\n";
    os << "      if (_pyc_rise) {\n";
    if (!fusedRegLoopIds.empty())
      os << "        _pyc_fused_rise = true;\n";
    for (int64_t loopId : phasedRegLoopIds) {
      os << "        " << regLoopName(loopId) << ".compute_rise();\n";
    }
    for (int64_t dbk : delayLoopIds)
      os << "        delay_loop_" << dbk << ".compute_rise();\n";
    emitEdgeCall("tick_sample", "        ");
    if (failed(emitTapUpdates("        ")))
      return failure();
    os << "        _pyc_seq_armed = true;\n";
    os << "        _pyc_commit_needed = true;\n";
    os << "      } else if (_pyc_fall) {\n";
    os << "        if (_pyc_seq_armed) {\n";
    for (int64_t loopId : phasedRegLoopIds) {
      os << "          " << regLoopName(loopId) << ".negedge();\n";
    }
    for (int64_t dbk : delayLoopIds)
      os << "          delay_loop_" << dbk << ".negedge();\n";
    os << "          _pyc_seq_armed = false;\n";
    os << "        }\n";
    // tick_sample() detects the edge itself, so it keeps the stored level in
    // sync on every step; the armed gate above only trims the loops.
    emitEdgeCall("tick_sample", "        ");
    // Tap probes are pure reads of the loop state that this rising edge just
    // committed, so the fall recomputation is an identity: keep the value the
    // rise wrote instead of recomputing every tap once more.
    os << "      } else {\n";
    for (int64_t loopId : phasedRegLoopIds) {
      os << "        " << regLoopName(loopId) << ".compute_generic(_pyc_now);\n";
    }
    for (int64_t dbk : delayLoopIds)
      os << "        delay_loop_" << dbk << ".compute_generic(_pyc_now);\n";
    emitEdgeCall("tick_sample", "        ");
    if (failed(emitTapUpdates("        ")))
      return failure();
    os << "        _pyc_commit_needed = true;\n";
    os << "      }\n";
    os << "    }\n";
  } else {
    for (auto r : regs) {
      std::string key = nt.get(r.getQ()) + "_inst";
      emitStateTickCompute(key, r->getOperands(), "    ",
                           [&](llvm::StringRef indent) {
                             os << indent << key << "->tick_sample();\n";
                           });
    }
    for (auto delay : delayLines) {
      std::string key = nt.get(delay.getQ()) + "_inst";
      emitStateTickCompute(key, delay->getOperands(), "    ",
                           [&](llvm::StringRef indent) {
                             os << indent << key << "->tick_sample();\n";
                           });
    }
    for (int64_t dbk : delayLoopIds) {
      llvm::StringRef clkName =
          fastClkName.empty()
              ? nt.get(delayLoopLines[dbk].front().getClk())
              : llvm::StringRef(fastClkName);
      os << "    delay_loop_" << dbk << ".compute_generic(" << clkName
         << ".toBool());\n";
    }
    if (failed(emitTapUpdates("    ")))
      return failure();
  }
  for (auto fifo : fifos) {
    std::string key = nt.get(fifo.getInReady()) + "_inst";
    emitStateTickCompute(key, fifo->getOperands(), "    ",
                         [&](llvm::StringRef indent) {
                           os << indent << key << ".tick_sample();\n";
                         });
  }
  for (auto mem : byteMems) {
    std::string key = byteMemInstName.lookup(mem.getOperation());
    emitStateTickCompute(key, mem->getOperands(), "    ",
                         [&](llvm::StringRef indent) {
                           os << indent << key << ".tick_sample();\n";
                         });
  }
  for (auto mem : syncMemNMs) {
    std::string key = syncMemNMInstName.lookup(mem.getOperation());
    emitStateTickCompute(key, mem->getOperands(), "    ",
                         [&](llvm::StringRef indent) {
                           os << indent << key << "->tick_sample();\n";
                         });
  }
  for (auto fifo : asyncFifos) {
    std::string key = nt.get(fifo.getInReady()) + "_inst";
    emitStateTickCompute(key, fifo->getOperands(), "    ",
                         [&](llvm::StringRef indent) {
                           os << indent << key << ".tick_sample();\n";
                         });
  }
  for (auto sync : cdcSyncs) {
    std::string key = nt.get(sync.getOut()) + "_inst";
    emitStateTickCompute(key, sync->getOperands(), "    ",
                         [&](llvm::StringRef indent) {
                           os << indent << key << ".tick_sample();\n";
                         });
  }
  if (!fusedRegLoopIds.empty()) {
    // Single-phase update for fused lanes: runs after every other element
    // sampled its inputs, so no compute stage can observe a fused q early.
    // The wake is table-driven: direct_commit()'s per-lane dirty bitmap is
    // walked by FusedLaneWake::apply (set-bit iteration per 64-lane word),
    // replacing one emitted branch per lane with a shared tiny loop; the
    // per-lane fanout (comb word, OR mask) pairs live in constexpr rodata.
    os << "    if (_pyc_fused_rise) {\n";
    for (int64_t loopId : fusedRegLoopIds) {
      std::string loop = regLoopName(loopId);
      llvm::SmallVector<unsigned> wakeOffsets;
      llvm::SmallVector<std::pair<unsigned, uint64_t>> wakeEntries;
      for (pyc::RegOp r : regLoopRegs[loopId]) {
        wakeOffsets.push_back(wakeEntries.size());
        collectFanoutWordMasks(r.getQ(), *schedule, combIndex, wakeEntries);
      }
      wakeOffsets.push_back(wakeEntries.size());
      if (wakeEntries.empty()) {
        // No comb fanout at all (e.g. a fixture whose only state is one
        // fused reg): the wake table would be empty and apply() dead code —
        // and _pyc_comb_dirty is not declared when the function has no
        // combs. Commit directly.
        os << "      " << loop << ".direct_commit();\n";
        continue;
      }
      std::string wake = "_pyc_fused_wake_" + loop;
      os << "      static constexpr pyc::cpp::FusedLaneWake<"
         << wakeOffsets.size() - 1 << ", " << wakeEntries.size() << "> "
         << wake << "{{{";
      llvm::interleaveComma(wakeOffsets, os,
                            [&](unsigned o) { os << o << "u"; });
      os << "}},{{";
      llvm::interleaveComma(wakeEntries, os, [&](const auto &e) {
        os << "{" << e.first << "u, 0x" << llvm::utohexstr(e.second)
           << "ull}";
      });
      os << "}}};\n";
      os << "      " << wake << ".apply(" << loop
         << ".direct_commit(), _pyc_comb_dirty);\n";
    }
    os << "    }\n";
  }
  os << "  }\n\n";

  os << "  void tick_commit() {\n";
  if (fastEdge)
    os << "    _pyc_seq_armed = false;\n";
  if (!instInfos.empty()) {
    if (parallelInstances) {
      os << "    // Sub-modules: independent commits, forked.\n";
      os << "    pyc::cpp::ForkJoinPool<" << parallelThreads << ">::instance().run("
         << instInfos.size() << ", [&](unsigned _pyc_worker) {\n";
      os << "      switch (_pyc_worker) {\n";
      for (unsigned i = 0; i < instInfos.size(); ++i)
        os << "        case " << i << "u: tick_commit_inst_" << i << "(); return;\n";
      os << "      }\n";
      os << "    });\n";
    } else {
      os << "    // Sub-modules.\n";
      unsigned commitParts = (static_cast<unsigned>(instInfos.size()) + kTickChunk - 1) / kTickChunk;
      for (unsigned i = 0; i < commitParts; ++i)
        os << "    tick_commit_part_" << i << "();\n";
    }
  }
  os << "    // Local sequential primitives.\n";
  auto emitCommitWake = [&](llvm::StringRef condition, Value output,
                            llvm::StringRef indent = "    ") {
    os << indent << "if (" << condition << ") {\n";
    emitMetadataCombFanout(output, os, *schedule, combIndex, nt,
                           (indent + "  ").str());
    os << indent << "}\n";
  };
  const bool gateLocalCommit = fastEdge;
  if (gateLocalCommit) {
    os << "    if (_pyc_commit_needed) {\n";
    os << "      _pyc_commit_needed = false;\n";
  }
  // A clean 64-lane dirty word skips all of its per-lane wake checks.
  auto openLaneWord = [&](unsigned lane, const std::string &cond) {
    if (lane & 63)
      return;
    if (lane)
      os << "    }\n";
    os << "    if (" << cond << ") {\n";
  };
  // DelayLoop commit: tail-dirty wakes q consumers, advanced wakes tap
  // consumers (every enabled edge shifts every tap).
  for (int64_t dbk : delayLoopIds) {
    std::string loop = "delay_loop_" + std::to_string(dbk);
    os << "    const auto &_pyc_dloop_res_" << dbk << " = " << loop
       << ".commit();\n";
    os << "    const auto &_pyc_dloop_tail_" << dbk
       << " = _pyc_dloop_res_" << dbk << ".tailDirty;\n";
    os << "    const auto &_pyc_dloop_adv_" << dbk
       << " = _pyc_dloop_res_" << dbk << ".advanced;\n";
    unsigned lane = 0;
    for (pyc::DelayLineOp dl : delayLoopLines[dbk]) {
      const std::string condT = "((_pyc_dloop_tail_" + std::to_string(dbk) +
          "[" + std::to_string(lane >> 6) + "u] >> " +
          std::to_string(lane & 63) + "u) & 1u)";
      const std::string condA = "((_pyc_dloop_adv_" + std::to_string(dbk) +
          "[" + std::to_string(lane >> 6) + "u] >> " +
          std::to_string(lane & 63) + "u) & 1u)";
      const std::string word =
          std::to_string(dbk) + "[" + std::to_string(lane >> 6) + "u]";
      openLaneWord(lane,
                   "_pyc_dloop_tail_" + word + " | _pyc_dloop_adv_" + word);
      os << "      if (" << condT << ") {\n";
      emitMetadataCombFanout(dl.getQ(), os, *schedule, combIndex, nt, "        ");
      os << "      }\n";
      for (auto tap : delayTaps) {
        auto src = tap.getLine().getDefiningOp<pyc::DelayLineOp>();
        if (src == dl) {
          os << "      if (" << condA << ") {\n";
          emitMetadataCombFanout(tap.getTap(), os, *schedule, combIndex, nt,
                                 "        ");
          os << "      }\n";
        }
      }
      ++lane;
    }
    if (lane)
      os << "    }\n";
  }
  // RegLoop commit: per-lane dirty bitmap keeps the change-driven wake as
  // precise as the per-reg path.
  for (int64_t loopId : phasedRegLoopIds) {
    std::string loop = regLoopName(loopId);
    llvm::SmallVector<unsigned> wakeOffsets;
    llvm::SmallVector<std::pair<unsigned, uint64_t>> wakeEntries;
    for (pyc::RegOp r : regLoopRegs[loopId]) {
      wakeOffsets.push_back(wakeEntries.size());
      collectFanoutWordMasks(r.getQ(), *schedule, combIndex, wakeEntries);
    }
    wakeOffsets.push_back(wakeEntries.size());
    if (wakeEntries.empty()) {
      os << "    " << loop << ".commit();\n";
      continue;
    }
    std::string wake = "_pyc_commit_wake_" + loop;
    const unsigned laneCount = wakeOffsets.size() - 1;
    if (wakeEntries.size() == laneCount) {
      os << "    static constexpr pyc::cpp::FlatLaneWake<" << laneCount
         << "> " << wake << "{{{";
      llvm::interleaveComma(wakeEntries, os, [&](const auto &e) {
        os << "{" << e.first << "u, 0x" << llvm::utohexstr(e.second)
           << "ull}";
      });
      os << "}}};\n";
    } else {
      os << "    static constexpr pyc::cpp::FusedLaneWake<" << laneCount
         << ", " << wakeEntries.size() << "> " << wake << "{{{";
      llvm::interleaveComma(wakeOffsets, os,
                            [&](unsigned o) { os << o << "u"; });
      os << "}},{{";
      llvm::interleaveComma(wakeEntries, os, [&](const auto &e) {
        os << "{" << e.first << "u, 0x" << llvm::utohexstr(e.second)
           << "ull}";
      });
      os << "}}};\n";
    }
    os << "    " << wake << ".apply(" << loop
       << ".commit(), _pyc_comb_dirty);\n";
  }
  for (auto r : regs) {
    std::string key = nt.get(r.getQ()) + "_inst";
    std::string changed = "_pyc_commit_changed_" + key;
    os << "    const bool " << changed << " = " << key
       << "->tick_commit();\n";
    emitCommitWake(changed, r.getQ());
  }
  for (auto delay : delayLines) {
    std::string key = nt.get(delay.getQ()) + "_inst";
    std::string changed = "_pyc_commit_changed_" + key;
    // Two wake domains: tail-`q` consumers re-evaluate only when the tail
    // changes, while tap consumers must wake on every advanced edge (each
    // enabled edge shifts every tap) even when the tail repeats.
    std::string advanced = "_pyc_commit_advanced_" + key;
    os << "    const bool " << advanced << " = " << key
       << "->has_pending();\n";
    os << "    const bool " << changed << " = " << key
       << "->tick_commit();\n";
    emitCommitWake(changed, delay.getQ());
    for (auto tap : delayTaps) {
      if (tap.getLine() != delay.getQ())
        continue;
      emitCommitWake(advanced, tap.getTap());
    }
  }
  if (gateLocalCommit)
    os << "    }\n";
  for (auto fifo : fifos) {
    std::string key = nt.get(fifo.getInReady()) + "_inst";
    std::string changed = "_pyc_commit_changed_" + key;
    os << "    const auto " << changed << " = " << key
       << ".tick_commit();\n";
    os << "    if (" << changed << " != 0) " << key << ".eval();\n";
    emitCommitWake(changed + " & decltype(" + key +
                       ")::kInReadyChanged",
                   fifo.getInReady());
    emitCommitWake(changed + " & decltype(" + key +
                       ")::kOutValidChanged",
                   fifo.getOutValid());
    emitCommitWake(changed + " & decltype(" + key +
                       ")::kOutDataChanged",
                   fifo.getOutData());
  }
  for (auto mem : byteMems) {
    std::string key = byteMemInstName.lookup(mem.getOperation());
    std::string changed = "_pyc_commit_changed_" + key;
    os << "    const bool " << changed << " = " << key
       << ".tick_commit();\n";
    emitCommitWake(changed, mem.getRdata());
  }
  for (auto mem : syncMemNMs) {
    std::string key = syncMemNMInstName.lookup(mem.getOperation());
    std::string changed = "_pyc_commit_changed_" + key;
    os << "    const auto " << changed << " = " << key
       << "->tick_commit();\n";
    for (auto [i, rd] : llvm::enumerate(mem.getRdatas()))
      emitCommitWake(changed + " & (std::remove_reference_t<decltype(*" + key +
                         ")>::change_mask_t{1} << " + std::to_string(i) + ")",
                     rd);
  }
  for (auto fifo : asyncFifos) {
    std::string key = nt.get(fifo.getInReady()) + "_inst";
    std::string changed = "_pyc_commit_changed_" + key;
    os << "    const auto " << changed << " = " << key
       << ".tick_commit();\n";
    emitCommitWake(changed + " & decltype(" + key +
                       ")::kInReadyChanged",
                   fifo.getInReady());
    emitCommitWake(changed + " & decltype(" + key +
                       ")::kOutValidChanged",
                   fifo.getOutValid());
    emitCommitWake(changed + " & decltype(" + key +
                       ")::kOutDataChanged",
                   fifo.getOutData());
  }
  for (auto sync : cdcSyncs) {
    std::string key = nt.get(sync.getOut()) + "_inst";
    std::string changed = "_pyc_commit_changed_" + key;
    os << "    const bool " << changed << " = " << key
       << ".tick_commit();\n";
    emitCommitWake(changed, sync.getOut());
  }
  if (!instInfos.empty()) {
    os << "    // Force re-eval on next eval() only for stateful sub-modules.\n";
    for (unsigned i = 0; i < instInfos.size(); ++i) {
      if (i < instHasSequentialCallee.size() && instHasSequentialCallee[i]) {
        os << "    " << instInfos[i].member << "_eval_cache_valid = false;\n";
      }
    }
  }
	  os << "  }\n\n";

	  // Decision 0027: provide explicit comb/tick/commit APIs + high-level step().
	  // Decision 0001: expose transfer() as an alias of commit().
	  os << "  void comb() { eval(); }\n";
	  os << "  void tick() { tick_compute(); }\n";
	  os << "  void commit() { tick_commit(); }\n";
	  os << "  void transfer() { tick_commit(); }\n";
	  os << "  void step() {\n";
	  os << "    comb();\n";
	  os << "    tick();\n";
	  os << "    commit();\n";
	  os << "    comb();\n";
	  os << "  }\n";

	  os << "};\n\n";
	  return success();
	}

} // namespace

unsigned resolveCppParallelInstances(unsigned flag) {
  if (flag != 0)
    return flag;
  return std::min(std::max(1u, std::thread::hardware_concurrency()),
                  kMaxParallelInstances);
}

// ---------------------------------------------------------------------------
// placement attribute readers (used by emit + profile consumers)
// ---------------------------------------------------------------------------

std::optional<unsigned> getModuleCombChunkNodes(ModuleOp module) {
  auto attr = module->getAttrOfType<IntegerAttr>(kCppCombChunkNodesAttr);
  if (!attr)
    return std::nullopt;
  return static_cast<unsigned>(attr.getValue().getZExtValue());
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
  if (auto v = readSummaryField(f, "probe_pinned_struct"))
    summary.probePinnedStruct = static_cast<unsigned>(*v);
  if (auto v = readSummaryField(f, "cross_part_promoted"))
    summary.crossPartPromoted = static_cast<unsigned>(*v);
  if (auto v = readSummaryField(f, "scheduled_cross_method"))
    summary.scheduledCrossMethod = static_cast<unsigned>(*v);
  if (auto v = readSummaryField(f, "scheduled_cut_weight"))
    summary.scheduledCutWeight = *v;
  return summary;
}

CppStorageKind getValueCppStorage(Value v) {
  Operation *op = v.getDefiningOp();
  if (!op)
    return CppStorageKind::Struct;
  if (auto a = op->getAttrOfType<StringAttr>(kCppStorageAttr)) {
    if (a.getValue() == "local")
      return CppStorageKind::Local;
    if (a.getValue() == "alias")
      return CppStorageKind::Alias;
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
// CppEmitterPlacementState (per-emission helpers used by emit code)
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
  os << cppType(ty) << " " << name << "{};\n";
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
    os << cppType(ty) << " " << name << " = " << expr << ";\n";
  else
    os << name << " = " << expr << ";\n";
}

LogicalResult emitCpp(ModuleOp module, llvm::raw_ostream &os, const CppEmitterOptions &opts) {
  if (failed(requireCombChunkConfig(module)))
    return failure();
  const CppEmitterOptions effectiveOpts = effectiveEmitOpts(module, opts);
  ObservationPlans probePlans;
  if (failed(loadObservationPlans(module, effectiveOpts.probePlanPath,
                                  /*traceCodegenPlanPath=*/"", probePlans)))
    return failure();

  os << "// pyCircuit C++ emission (prototype)\n";
  os << "#include <array>\n";
  os << "#include <cstdlib>\n";
  os << "#include <cstdint>\n";
  os << "#include <iostream>\n";
  os << "#include <memory>\n";
  os << "#include <string>\n";
  os << "#include <type_traits>\n";
  os << "#include <utility>\n";
  os << "#include <cpp/pyc_sim.hpp>\n";
  os << "#include <cpp/pyc_state_loop.hpp>\n";
  os << "#include <cpp/pyc_change_scheduler.hpp>\n";
  if (resolveCppParallelInstances(opts.parallelInstances) >= 2)
    os << "#include <cpp/pyc_parallel.hpp>\n";
  os << "\n";
  os << "namespace pyc::gen {\n\n";

  // Emit structs in dependency order so submodule types are defined before use.
  llvm::SmallVector<func::FuncOp> funcs;
  for (auto f : module.getOps<func::FuncOp>())
    funcs.push_back(f);

  llvm::StringMap<unsigned> indexByName;
  for (auto [i, f] : llvm::enumerate(funcs))
    indexByName.try_emplace(f.getSymName(), static_cast<unsigned>(i));

  llvm::SmallVector<llvm::SmallVector<unsigned>> succ(funcs.size());
  llvm::SmallVector<unsigned> indeg(funcs.size(), 0);

  for (auto it : llvm::enumerate(funcs)) {
    unsigned callerIdx = static_cast<unsigned>(it.index());
    func::FuncOp f = it.value();
    f.walk([&](pyc::InstanceOp inst) {
      auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
      if (!calleeAttr)
        return;
      auto it = indexByName.find(calleeAttr.getValue());
      if (it == indexByName.end())
        return;
      unsigned calleeIdx = it->second;
      succ[calleeIdx].push_back(callerIdx);
      indeg[callerIdx]++;
    });
  }

  // Kahn topological sort; tie-break by symbol name for determinism.
  auto cmp = [&](unsigned a, unsigned b) { return funcs[a].getSymName() > funcs[b].getSymName(); };
  std::vector<unsigned> heap;
  heap.reserve(funcs.size());
  for (unsigned i = 0; i < funcs.size(); ++i)
    if (indeg[i] == 0)
      heap.push_back(i);
  std::make_heap(heap.begin(), heap.end(), cmp);

  llvm::SmallVector<unsigned> order;
  order.reserve(funcs.size());
  while (!heap.empty()) {
    std::pop_heap(heap.begin(), heap.end(), cmp);
    unsigned n = heap.back();
    heap.pop_back();
    order.push_back(n);
    for (unsigned s : succ[n]) {
      if (--indeg[s] == 0) {
        heap.push_back(s);
        std::push_heap(heap.begin(), heap.end(), cmp);
      }
    }
  }

  if (order.size() != funcs.size())
    return module.emitError("C++ emitter: module instance graph has a cycle");

  for (unsigned idx : order) {
    if (failed(emitFunc(funcs[idx], os, effectiveOpts, probePlans)))
      return failure();
  }

  os << "} // namespace pyc::gen\n";
  return success();
}

LogicalResult emitCppFunc(ModuleOp module, func::FuncOp f, llvm::raw_ostream &os, const CppEmitterOptions &opts) {
  if (failed(requireCombChunkConfig(module)))
    return failure();
  ObservationPlans probePlans;
  if (failed(loadObservationPlans(module, opts.probePlanPath,
                                  /*traceCodegenPlanPath=*/"", probePlans)))
    return failure();
  return emitFunc(f, os, effectiveEmitOpts(module, opts), probePlans);
}

} // namespace pyc