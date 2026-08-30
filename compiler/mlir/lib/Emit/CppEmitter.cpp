#include "pyc/Emit/CppEmitter.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Attributes.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Types.h"
#include "llvm/ADT/DenseMap.h"
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
#include <string>
#include <vector>

using namespace mlir;

namespace pyc {
namespace {

// ---------------------------------------------------------------------------
// placement attribute readers (used internally by the emitter)
// ---------------------------------------------------------------------------

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
  if (auto vt = dyn_cast<VectorType>(ty)) {
    // Build nested pyc::cpp::Vec from the shape (outermost to innermost).
    // vector<4x8xi32> → pyc::cpp::Vec<pyc::cpp::Vec<Wire<32>, 8>, 4>
    auto shape = vt.getShape();
    std::string inner = cppType(vt.getElementType());
    for (int i = shape.size() - 1; i >= 0; --i)
      inner = "pyc::cpp::Vec<" + inner + ", " + std::to_string(shape[i]) + ">";
    return inner;
  }
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
  if (auto vt = dyn_cast<VectorType>(ty))
    return bitWidth(vt.getElementType());  // scalar element bit width
  return 0;
}

// Emit ProbeRegistry registration for a (possibly vector) wire-kind value.
// Scalars register directly via addWire; vectors lower to nested pyc::cpp::Vec
// and use ProbeRegistry::addVec, which recurses to per-lane Wire<W>* leaves
// under indexed paths (e.g. "a[0][1]").
static void emitWireProbes(llvm::raw_ostream &os, Type ty, unsigned w,
                           const std::string &fieldPath, const std::string &cppExpr) {
  if (isa<VectorType>(ty)) {
    os << "    reg.addVec(reg_path(" << cppStringLiteral(fieldPath) << "), &" << cppExpr << ");\n";
    return;
  }
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
    if (Operation *def = v.getDefiningOp()) {
      if (auto nAttr = def->getAttrOfType<StringAttr>("pyc.name")) {
        std::string cand = unique(sanitizeId(nAttr.getValue()));
        names.try_emplace(v, cand);
        return cand;
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

static std::string treeReduceExpr(llvm::SmallVectorImpl<std::string> &terms,
                                  llvm::StringRef op) {
  while (terms.size() > 1) {
    llvm::SmallVector<std::string> next;
    for (size_t i = 0; i < terms.size(); i += 2) {
      if (i + 1 < terms.size())
        next.push_back("(" + terms[i] + " " + op.str() + " " + terms[i + 1] + ")");
      else
        next.push_back(terms[i]);
    }
    terms = std::move(next);
  }
  return terms.empty() ? "" : terms[0];
}

static std::string chainReduceExpr(llvm::SmallVectorImpl<std::string> &terms,
                                   llvm::StringRef op) {
  if (terms.empty())
    return "";
  std::string out = terms[0];
  for (size_t i = 1; i < terms.size(); ++i)
    out = "(" + out + " " + op.str() + " " + terms[i] + ")";
  return out;
}

static bool isTreeReduceMode(Operation *op) {
  if (auto mode = op->getAttrOfType<StringAttr>("mode"))
    return mode.getValue() == "tree";
  return false;
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

static std::vector<ProbeAliasEntry> loadProbeAliasesForTop(llvm::StringRef planPath, llvm::StringRef symName) {
  std::vector<ProbeAliasEntry> out;
  if (planPath.empty())
    return out;
  auto fileOrErr = llvm::MemoryBuffer::getFile(planPath);
  if (!fileOrErr)
    return out;
  auto parsed = llvm::json::parse(fileOrErr.get()->getBuffer());
  if (!parsed)
    return out;
  auto *obj = parsed->getAsObject();
  if (!obj)
    return out;
  auto topSymbol = obj->getString("top_symbol");
  if (!topSymbol || *topSymbol != symName)
    return out;
  auto *aliases = obj->getArray("aliases");
  if (!aliases)
    return out;
  out.reserve(aliases->size());
  for (const llvm::json::Value &value : *aliases) {
    auto *entry = value.getAsObject();
    if (!entry)
      continue;
    auto canonical = entry->getString("canonical_path");
    auto source = entry->getString("source_path");
    if (!canonical || !source)
      continue;
    out.push_back(ProbeAliasEntry{canonical->str(), source->str()});
  }
  std::sort(out.begin(), out.end(), [](const ProbeAliasEntry &a, const ProbeAliasEntry &b) {
    return a.canonicalPath < b.canonicalPath;
  });
  return out;
}

static Value findRegQFromValue(Value v) {
  llvm::SmallVector<Value, 8> seen;
  while (true) {
    for (Value prev : seen) {
      if (prev == v)
        return Value();
    }
    seen.push_back(v);
    while (auto a = v.getDefiningOp<pyc::AliasOp>())
      v = a.getIn();
    if (auto rop = v.getDefiningOp<pyc::RegOp>())
      return rop.getQ();
    auto comb = v.getDefiningOp<pyc::CombOp>();
    if (!comb)
      return Value();

    auto res = dyn_cast<OpResult>(v);
    if (!res)
      return Value();
    auto yield = dyn_cast_or_null<pyc::YieldOp>(comb.getBody().front().getTerminator());
    if (!yield)
      return Value();
    if (res.getResultNumber() >= yield.getNumOperands())
      return Value();

    Value y = yield.getOperand(res.getResultNumber());
    while (auto a = y.getDefiningOp<pyc::AliasOp>())
      y = a.getIn();
    auto barg = dyn_cast<BlockArgument>(y);
    if (!barg)
      return Value();
    if (barg.getOwner() != &comb.getBody().front())
      return Value();
    if (barg.getArgNumber() >= comb.getNumOperands())
      return Value();
    v = comb.getOperand(barg.getArgNumber());
  }
}

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
            pyc::FifoOp,
            pyc::ByteMemOp,
            pyc::SyncMemOp,
            pyc::SyncMemDPOp,
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

static LogicalResult emitCombAssign(Operation &op, llvm::raw_ostream &os, NameTable &nt,
                                    CppEmitterPlacementState *ps = nullptr) {
  if (ps) {
    for (Value v : op.getOperands())
      (void)ps->emitLocalDeclIfNeeded(v, v.getType(), nt.get(v), os);
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
    bool selIsVec = isa<VectorType>(m.getSel().getType());
    assignExpr(m.getResult(), m.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 if (selIsVec)
                   e << "pyc::cpp::mux_vec<" << w << ">(" << nt.get(m.getSel()) << ", " << nt.get(m.getA()) << ", "
                     << nt.get(m.getB()) << ")";
                 else
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
  if (auto vg = dyn_cast<pyc::VGetOp>(op)) {
    auto vecTy = dyn_cast<VectorType>(vg.getVec().getType());
    if (!vecTy)
      return vg.emitError("C++ emitter expects vector operand for pyc.v_get");
    std::int64_t idx = vg.getIndexAttr().getInt();
    if (idx < 0 || idx >= vecTy.getDimSize(0))
      return vg.emitError("pyc.v_get index out of range for C++ emission");
    assignExpr(vg.getResult(), vg.getType(), os, nt,
               [&](llvm::raw_ostream &e) { e << nt.get(vg.getVec()) << "[" << idx << "]"; }, ps);
    return success();
  }
  if (auto vc = dyn_cast<pyc::VCreateOp>(op)) {
    assignExpr(vc.getResult(), vc.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << cppType(vc.getType()) << "{{";
                 for (auto [i, v] : llvm::enumerate(vc.getElements())) {
                   if (i)
                     e << ", ";
                   e << nt.get(v);
                 }
                 e << "}}";
               },
               ps);
    return success();
  }
  if (auto vb = dyn_cast<pyc::VBroadcastOp>(op)) {
    std::int64_t size = vb.getSizeAttr().getInt();
    if (size < 0)
      return vb.emitError("negative size is invalid for pyc.v_broadcast");
    assignExpr(vb.getResult(), vb.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << cppType(vb.getType()) << "{{";
                 for (std::int64_t i = 0; i < size; ++i) {
                   if (i)
                     e << ", ";
                   e << nt.get(vb.getScalar());
                 }
                 e << "}}";
               },
               ps);
    return success();
  }
  if (auto vbd = dyn_cast<pyc::VBroadcastDimOp>(op)) {
    auto srcVT = dyn_cast<VectorType>(vbd.getVec().getType());
    auto dstVT = dyn_cast<VectorType>(vbd.getResult().getType());
    if (!srcVT || !dstVT)
      return vbd.emitError("C++ emitter expects vector types for v_broadcast_dim");
    std::int64_t dim = vbd.getDimAttr().getInt();
    assignExpr(vbd.getResult(), vbd.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << cppType(dstVT) << "{{";
                 // Walk all result lanes, mapping to source.
                 std::function<void(unsigned, std::vector<int64_t> &)> walk;
                 bool emittedAny = false;
                 walk = [&](unsigned depth, std::vector<int64_t> &idx) {
                   if (depth == static_cast<unsigned>(dstVT.getRank())) {
                     if (emittedAny)
                       e << ", ";
                     emittedAny = true;
                     // Build source index by dropping the broadcast dim.
                     std::string srcIdx;
                     for (unsigned d = 0; d < dstVT.getRank(); ++d)
                       if (static_cast<int64_t>(d) != dim)
                         srcIdx += "[" + std::to_string(idx[d]) + "]";
                     e << nt.get(vbd.getVec()) << srcIdx;
                     return;
                   }
                   for (int64_t i = 0; i < dstVT.getDimSize(depth); ++i) {
                     idx.push_back(i);
                     walk(depth + 1, idx);
                     idx.pop_back();
                   }
                 };
                 std::vector<int64_t> idx;
                 walk(0, idx);
                 e << "}}";
               },
               ps);
    return success();
  }
  auto emitVectorReduce = [&](auto vr, const char *opName, const char *opToken) -> LogicalResult {
    auto vt = dyn_cast<VectorType>(vr.getVec().getType());
    if (!vt)
      return vr.emitError("C++ emitter expects vector operand for pyc.") << opName;
    if (vt.getRank() < 1 || vt.getRank() > 2)
      return vr.emitError("C++ emitter currently supports rank-1/rank-2 pyc.") << opName;
    for (std::int64_t lanes : vt.getShape()) {
      if (lanes <= 0)
        return vr.emitError("pyc.") << opName << " requires non-empty vector dimensions";
    }
    bool useTree = isTreeReduceMode(vr.getOperation());

    if (!vr.getDim()) {
      assignExpr(vr.getResult(), vr.getType(), os, nt,
                 [&](llvm::raw_ostream &e) {
                   llvm::SmallVector<std::string> terms;
                   if (vt.getRank() == 1) {
                     for (std::int64_t i = 0; i < vt.getShape()[0]; ++i)
                       terms.push_back(nt.get(vr.getVec()) + "[" +
                                       std::to_string(static_cast<long long>(i)) + "]");
                   } else {
                     for (std::int64_t i = 0; i < vt.getShape()[0]; ++i)
                       for (std::int64_t j = 0; j < vt.getShape()[1]; ++j)
                         terms.push_back(nt.get(vr.getVec()) + "[" +
                                         std::to_string(static_cast<long long>(i)) + "][" +
                                         std::to_string(static_cast<long long>(j)) + "]");
                   }
                   e << (useTree ? treeReduceExpr(terms, opToken) : chainReduceExpr(terms, opToken));
                 },
                 ps);
      return success();
    }

    std::int64_t dim = *vr.getDim();
    if (dim < 0 || dim >= vt.getRank())
      return vr.emitError("pyc.") << opName << " dim out of range";

    std::int64_t lanes = vt.getShape()[0];
    if (vt.getRank() == 1) {
      assignExpr(vr.getResult(), vr.getType(), os, nt,
                 [&](llvm::raw_ostream &e) {
                   llvm::SmallVector<std::string> terms;
                   for (std::int64_t i = 0; i < lanes; ++i) {
                     terms.push_back(nt.get(vr.getVec()) + "[" +
                                     std::to_string(static_cast<long long>(i)) + "]");
                   }
                   e << (useTree ? treeReduceExpr(terms, opToken) : chainReduceExpr(terms, opToken));
                 },
                 ps);
      return success();
    }

    std::int64_t rows = vt.getShape()[0];
    std::int64_t cols = vt.getShape()[1];
    if (rows <= 0 || cols <= 0)
      return vr.emitError("pyc.") << opName << " requires non-empty vector";
    assignExpr(vr.getResult(), vr.getType(), os, nt,
               [&](llvm::raw_ostream &e) {
                 e << cppType(vr.getResult().getType()) << "{{";
                 std::int64_t outLanes = (dim == 0) ? cols : rows;
                 std::int64_t reduceLanes = (dim == 0) ? rows : cols;
                 for (std::int64_t i = 0; i < outLanes; ++i) {
                   if (i)
                     e << ", ";
                   llvm::SmallVector<std::string> terms;
                   for (std::int64_t j = 0; j < reduceLanes; ++j) {
                     if (dim == 0)
                       terms.push_back(nt.get(vr.getVec()) + "[" +
                                       std::to_string(static_cast<long long>(j)) + "][" +
                                       std::to_string(static_cast<long long>(i)) + "]");
                     else
                       terms.push_back(nt.get(vr.getVec()) + "[" +
                                       std::to_string(static_cast<long long>(i)) + "][" +
                                       std::to_string(static_cast<long long>(j)) + "]");
                   }
                   e << (useTree ? treeReduceExpr(terms, opToken) : chainReduceExpr(terms, opToken));
                 }
                 e << "}}";
               },
               ps);
    return success();
  };
  if (auto vr = dyn_cast<pyc::VOrReduceOp>(op)) {
    return emitVectorReduce(vr, "v_or_reduce", "|");
  }
  if (auto vr = dyn_cast<pyc::VAndReduceOp>(op)) {
    return emitVectorReduce(vr, "v_and_reduce", "&");
  }
  if (auto vr = dyn_cast<pyc::VAddReduceOp>(op)) {
    return emitVectorReduce(vr, "v_add_reduce", "+");
  }
  return op.emitError("unsupported combinational op for C++ emission");
}

static LogicalResult emitCombMethod(pyc::CombOp comb,
                                    llvm::raw_ostream &os,
                                    NameTable &nt,
                                    unsigned idx,
                                    const CppEmitterOptions &opts) {
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
        if (failed(emitCombAssign(*op, os, nt, &placement)))
          return failure();
      }
      os << "  }\n\n";
    }

    os << "  inline void eval_comb_" << idx << "() {\n";
    for (const std::string &partName : partMethods)
      os << "    " << partName << "();\n";

    auto y = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
    if (!y)
      return comb.emitError("pyc.comb must terminate with pyc.yield");
    if (y.getNumOperands() != comb.getNumResults())
      return comb.emitError("pyc.yield operand count must match pyc.comb results");

    placement.beginMethod("eval_comb_" + std::to_string(idx));
    for (auto [i, v] : llvm::enumerate(y.getOperands())) {
      (void)placement.emitLocalDeclIfNeeded(v, v.getType(), nt.get(v), os);
      placement.emitValueAssign(comb.getResult(i), comb.getResult(i).getType(), nt.get(comb.getResult(i)),
                                nt.get(v), os);
    }
    os << "  }\n\n";
    return success();
  }

  std::string methodName = "eval_comb_" + std::to_string(idx);
  os << "  inline void " << methodName << "() {\n";
  placement.beginMethod(methodName);
  for (Operation *op : combOps) {
    if (failed(emitCombAssign(*op, os, nt, &placement)))
      return failure();
  }

  auto y = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
  if (!y)
    return comb.emitError("pyc.comb must terminate with pyc.yield");
  if (y.getNumOperands() != comb.getNumResults())
    return comb.emitError("pyc.yield operand count must match pyc.comb results");

  for (auto [i, v] : llvm::enumerate(y.getOperands())) {
    (void)placement.emitLocalDeclIfNeeded(v, v.getType(), nt.get(v), os);
    placement.emitValueAssign(comb.getResult(i), comb.getResult(i).getType(), nt.get(comb.getResult(i)),
                              nt.get(v), os);
  }
  os << "  }\n\n";
  return success();
}

static LogicalResult emitFunc(func::FuncOp f, llvm::raw_ostream &os, const CppEmitterOptions &opts) {
  NameTable nt;

  if (!f.isDeclaration() && !getFuncPlacementSummary(f))
      return f.emitError(
          "C++ member placement missing; run pyc-cpp-placement before emit");

  if (!llvm::hasSingleElement(f.getBody()))
    return f.emitError("C++ emitter currently supports single-block functions only");

  Block &top = f.getBody().front();

  std::string structName = sanitizeId(f.getSymName());
  os << "struct " << structName << " {\n";

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
      if (getValueCppStorage(r) == CppStorageKind::Local)
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
  llvm::SmallVector<pyc::FifoOp> fifos;
  llvm::SmallVector<pyc::ByteMemOp> byteMems;
  llvm::SmallVector<pyc::SyncMemOp> syncMems;
  llvm::SmallVector<pyc::SyncMemDPOp> syncMemDPs;
  llvm::SmallVector<pyc::AsyncFifoOp> asyncFifos;
  llvm::SmallVector<pyc::CdcSyncOp> cdcSyncs;
  llvm::SmallVector<pyc::InstanceOp> instances;
  llvm::SmallVector<pyc::CombOp> combs;

  for (Operation &op : top) {
    if (auto r = dyn_cast<pyc::RegOp>(op))
      regs.push_back(r);
    else if (auto fifo = dyn_cast<pyc::FifoOp>(op))
      fifos.push_back(fifo);
    else if (auto mem = dyn_cast<pyc::ByteMemOp>(op))
      byteMems.push_back(mem);
    else if (auto mem = dyn_cast<pyc::SyncMemOp>(op))
      syncMems.push_back(mem);
    else if (auto mem = dyn_cast<pyc::SyncMemDPOp>(op))
      syncMemDPs.push_back(mem);
    else if (auto fifo = dyn_cast<pyc::AsyncFifoOp>(op))
      asyncFifos.push_back(fifo);
    else if (auto s = dyn_cast<pyc::CdcSyncOp>(op))
      cdcSyncs.push_back(s);
    else if (auto inst = dyn_cast<pyc::InstanceOp>(op))
      instances.push_back(inst);
    else if (auto comb = dyn_cast<pyc::CombOp>(op))
      combs.push_back(comb);
  }

  auto regKey = [&](pyc::RegOp r) { return nt.get(r.getQ()); };
  auto fifoKey = [&](pyc::FifoOp f) { return nt.get(f.getInReady()); };
  auto memKey = [&](pyc::ByteMemOp m) -> std::string {
    if (auto nameAttr = m->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(m.getRdata());
  };
  auto syncMemKey = [&](pyc::SyncMemOp m) -> std::string {
    if (auto nameAttr = m->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(m.getRdata());
  };
  auto syncMemDPKey = [&](pyc::SyncMemDPOp m) -> std::string {
    if (auto nameAttr = m->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(m.getRdata0());
  };
  auto asyncFifoKey = [&](pyc::AsyncFifoOp f) { return nt.get(f.getInReady()); };
  auto cdcKey = [&](pyc::CdcSyncOp s) { return nt.get(s.getOut()); };

  std::sort(regs.begin(), regs.end(), [&](pyc::RegOp a, pyc::RegOp b) { return regKey(a) < regKey(b); });
  std::sort(fifos.begin(), fifos.end(), [&](pyc::FifoOp a, pyc::FifoOp b) { return fifoKey(a) < fifoKey(b); });
  std::sort(byteMems.begin(), byteMems.end(), [&](pyc::ByteMemOp a, pyc::ByteMemOp b) { return memKey(a) < memKey(b); });
  std::sort(syncMems.begin(), syncMems.end(), [&](pyc::SyncMemOp a, pyc::SyncMemOp b) { return syncMemKey(a) < syncMemKey(b); });
  std::sort(syncMemDPs.begin(), syncMemDPs.end(), [&](pyc::SyncMemDPOp a, pyc::SyncMemDPOp b) { return syncMemDPKey(a) < syncMemDPKey(b); });
  std::sort(asyncFifos.begin(), asyncFifos.end(), [&](pyc::AsyncFifoOp a, pyc::AsyncFifoOp b) { return asyncFifoKey(a) < asyncFifoKey(b); });
  std::sort(cdcSyncs.begin(), cdcSyncs.end(), [&](pyc::CdcSyncOp a, pyc::CdcSyncOp b) { return cdcKey(a) < cdcKey(b); });
  auto instKey = [&](pyc::InstanceOp i) -> std::string {
    if (auto nameAttr = i->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    if (i.getNumResults() > 0)
      return nt.get(i.getResult(0));
    return "inst";
  };
  std::sort(instances.begin(), instances.end(), [&](pyc::InstanceOp a, pyc::InstanceOp b) { return instKey(a) < instKey(b); });

  struct InstInfo {
    pyc::InstanceOp op;
    func::FuncOp callee;
    std::string member;
    std::string seg;
    std::vector<std::string> inPorts;
    std::vector<std::string> outPorts;
  };
  std::vector<InstInfo> instInfos;
  instInfos.reserve(instances.size());
  llvm::DenseMap<Operation *, unsigned> instIndex;
  ModuleOp mod = f->getParentOfType<ModuleOp>();
  if (!mod)
    return f.emitError("C++ emitter: missing parent module for instance resolution");
  std::vector<bool> instHasSequentialCallee{};

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

      unsigned idx = static_cast<unsigned>(instInfos.size());
      instIndex.try_emplace(inst.getOperation(), idx);
      instInfos.push_back(InstInfo{inst, callee, std::move(member), std::move(seg), std::move(inPorts), std::move(outPorts)});
    }

    llvm::DenseMap<Operation *, SeqStateKind> seqMemo{};
    instHasSequentialCallee.reserve(instInfos.size());
    for (const auto &ii : instInfos) {
      instHasSequentialCallee.push_back(functionHasSequentialState(ii.callee, mod, seqMemo));
    }
  }

  auto instancePackedCacheWordCount = [&](const InstInfo &ii) -> unsigned {
    unsigned words = 0;
    auto inst = ii.op;
    for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
      unsigned inW = bitWidth(inst.getOperand(i).getType());
      words += std::max(1u, (inW + 63u) / 64u);
    }
    return words;
  };

  // Medium fanout glue modules still explode into large TUs if they keep the
  // per-port versioned cache form. Prefer the exact packed-word cache once an
  // instance reaches moderate input width/count.
  constexpr unsigned kPackedInstanceCacheOperandThreshold = 12;
  constexpr unsigned kPackedInstanceCacheWordThreshold = 16;
  auto usePackedInstanceEvalCache = [&](const InstInfo &ii) -> bool {
    auto inst = ii.op;
    unsigned packedWords = instancePackedCacheWordCount(ii);
    return (inst.getNumOperands() >= kPackedInstanceCacheOperandThreshold) || (packedWords >= kPackedInstanceCacheWordThreshold);
  };

  llvm::DenseMap<Operation *, std::string> byteMemInstName;
  llvm::DenseMap<Operation *, std::string> syncMemInstName;
  llvm::DenseMap<Operation *, std::string> syncMemDPInstName;

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

    struct NamedProbeInfo {
      std::string fieldPath;
      std::string cppValue;
      std::string cppRegInst;
      unsigned width = 0;
      bool isReg = false;
      Type type;
    };

    // Decision 0003 / 0051-0052: infer probe kind for ports and named internal
    // objects. A value is considered stateful iff it directly returns the q
    // output of a local pyc.reg (through optional pyc.alias wrappers).
    std::vector<bool> outIsReg(f.getNumResults(), false);
    std::vector<Value> outRegQ(f.getNumResults(), Value());
    std::vector<NamedProbeInfo> namedProbes;
    if (!f.isDeclaration()) {
      auto ret = dyn_cast_or_null<func::ReturnOp>(f.getBody().front().getTerminator());
      if (!ret)
        return f.emitError("missing return");
      for (unsigned i = 0; i < f.getNumResults() && i < ret.getNumOperands(); ++i)
        outRegQ[i] = findRegQFromValue(ret.getOperand(i));
      for (unsigned i = 0; i < f.getNumResults(); ++i)
        outIsReg[i] = static_cast<bool>(outRegQ[i]);

      llvm::StringSet<> seenNamedFields;
      f.walk([&](Operation *op) {
        auto nameAttr = op->getAttrOfType<StringAttr>("pyc.name");
        if (!nameAttr || op->getNumResults() != 1)
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
        Value regQ = findRegQFromValue(value);
        namedProbes.push_back(NamedProbeInfo{
            fieldPath,
            nt.get(value),
            static_cast<bool>(regQ) ? (nt.get(regQ) + "_inst") : std::string(),
            width,
            static_cast<bool>(regQ),
            value.getType(),
        });
      });
      std::sort(namedProbes.begin(), namedProbes.end(), [](const NamedProbeInfo &a, const NamedProbeInfo &b) {
        return a.fieldPath < b.fieldPath;
      });
    }

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
		    if (outIsReg[i] && !isa<VectorType>(f.getResultTypes()[i])) {
		      os << "    reg.addReg<" << w << ">(reg_path(" << cppStringLiteral(outCanon[i]) << "), &" << outNames[i]
		         << ", &" << nt.get(outRegQ[i]) << "_inst->pending, &" << nt.get(outRegQ[i]) << "_inst->qNext);\n";
		    } else {
		      emitWireProbes(os, f.getResultTypes()[i], w, outCanon[i], outNames[i]);
		    }
		  }
      for (const auto &named : namedProbes) {
        if (named.isReg && !isa<VectorType>(named.type)) {
          os << "    reg.addReg<" << named.width << ">(reg_path(" << cppStringLiteral(named.fieldPath) << "), &"
             << named.cppValue << ", &" << named.cppRegInst << "->pending, &" << named.cppRegInst << "->qNext);\n";
        } else {
          emitWireProbes(os, named.type, named.width, named.fieldPath, named.cppValue);
        }
      }
		  for (auto mem : byteMems) {
		    std::string instName = nt.get(mem.getRdata()) + "_inst";
		    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
	      instName = sanitizeId(nameAttr.getValue());
	    os << "    reg.addMem(reg_path(\"" << instName << "\"), &" << instName << ", &" << instName
	       << ".pendingWrite, &" << instName << ".latchedAddr, &" << instName << ".latchedData, &" << instName
	       << ".latchedStrb);\n";
	  }
	  for (auto mem : syncMems) {
	    std::string instName = nt.get(mem.getRdata()) + "_inst";
	    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
	      instName = sanitizeId(nameAttr.getValue());
	    os << "    if (" << instName << ") reg.addMem(reg_path(\"" << instName << "\"), " << instName << ", &" << instName
	       << "->pendingWrite, &" << instName << "->latchedWaddr, &" << instName << "->latchedWdata, &" << instName
	       << "->latchedWstrb);\n";
	  }
	  for (auto mem : syncMemDPs) {
	    std::string instName = nt.get(mem.getRdata0()) + "_inst";
	    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
	      instName = sanitizeId(nameAttr.getValue());
	    os << "    if (" << instName << ") reg.addMem(reg_path(\"" << instName << "\"), " << instName << ", &" << instName
	       << "->pendingWrite, &" << instName << "->latchedWaddr, &" << instName << "->latchedWdata, &" << instName
	       << "->latchedWstrb);\n";
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
      auto probeAliases = loadProbeAliasesForTop(opts.probePlanPath, f.getSymName());
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
    if (isa<VectorType>(r.getQ().getType()))
      os << "  pyc::cpp::pyc_vec_reg<" << cppType(r.getQ().getType()) << "> *" << nt.get(r.getQ()) << "_inst = nullptr;\n";
    else
      os << "  pyc::cpp::pyc_reg<" << w << "> *" << nt.get(r.getQ()) << "_inst = nullptr;\n";
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
    os << "  pyc::cpp::pyc_fifo<" << w << ", " << depth << "> " << instName << ";\n";
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

    os << "  pyc::cpp::pyc_byte_mem<" << addrW << ", " << dataW << ", " << depth << "> " << instName << ";\n";
    os << "  bool " << instName << "_eval_cache_valid = false;\n";
    os << "  " << cppType(mem.getRst().getType()) << " " << instName << "_eval_cache_rst{};\n";
    os << "  std::uint64_t " << instName << "_eval_cache_rst_ver = 1ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_rst_seen_ver = 0ull;\n";
    os << "  std::uint64_t " << instName << "_eval_cache_rst_fp = 0ull;\n";
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
  for (auto mem : syncMems) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    if (addrW == 0)
      return mem.emitError("invalid sync_mem addr width");
    if (dataW == 0)
      return mem.emitError("invalid sync_mem data width");

    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();

    std::string instName = nt.get(mem.getRdata()) + "_inst";
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      instName = sanitizeId(nameAttr.getValue());
    syncMemInstName.try_emplace(mem.getOperation(), instName);

    os << "  pyc::cpp::pyc_sync_mem<" << addrW << ", " << dataW << ", " << depth << "> *" << instName
       << " = nullptr;\n";
  }
  for (auto mem : syncMemDPs) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr0().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata0().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem_dp types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    if (addrW == 0)
      return mem.emitError("invalid sync_mem_dp addr width");
    if (dataW == 0)
      return mem.emitError("invalid sync_mem_dp data width");

    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();

    std::string instName = nt.get(mem.getRdata0()) + "_inst";
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      instName = sanitizeId(nameAttr.getValue());
    syncMemDPInstName.try_emplace(mem.getOperation(), instName);

    os << "  pyc::cpp::pyc_sync_mem_dp<" << addrW << ", " << dataW << ", " << depth << "> *" << instName
       << " = nullptr;\n";
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
    os << "  pyc::cpp::pyc_async_fifo<" << w << ", " << depth << "> " << instName << ";\n";
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
    os << "  pyc::cpp::pyc_cdc_sync<" << w << ", " << stages << "> " << nt.get(s.getOut()) << "_inst;\n";
  }
  os << "\n";

  os << "  struct _pyc_sim_stats_t {\n";
  os << "    std::uint64_t instance_eval_calls = 0;\n";
  os << "    std::uint64_t instance_cache_skips = 0;\n";
  os << "    std::uint64_t primitive_eval_calls = 0;\n";
  os << "    std::uint64_t primitive_cache_skips = 0;\n";
  os << "    std::uint64_t fallback_iterations = 0;\n";
  os << "  };\n";
  os << "  bool _pyc_sim_stats_enable = false;\n";
  os << "  bool _pyc_sim_fast_enable = false;\n";
  os << "  std::string _pyc_sim_stats_path{};\n";
  os << "  _pyc_sim_stats_t _pyc_sim_stats{};\n\n";
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
  os << "    _pyc_sim_stats_enable = _pyc_parse_bool_env(\"PYC_SIM_STATS\", false);\n";
  os << "    _pyc_sim_fast_enable = _pyc_parse_bool_env(\"PYC_SIM_FAST\", false);\n";
  os << "    const char *path = std::getenv(\"PYC_SIM_STATS_PATH\");\n";
  os << "    if (path && *path)\n";
  os << "      _pyc_sim_stats_path = path;\n";
  os << "  }\n\n";
  os << "  void reset_sim_stats() { _pyc_sim_stats = _pyc_sim_stats_t{}; }\n\n";
  os << "  void dump_sim_stats(std::ostream &os) const {\n";
  os << "    os << \"instance_eval_calls=\" << _pyc_sim_stats.instance_eval_calls << \"\\n\";\n";
  os << "    os << \"instance_cache_skips=\" << _pyc_sim_stats.instance_cache_skips << \"\\n\";\n";
  os << "    os << \"primitive_eval_calls=\" << _pyc_sim_stats.primitive_eval_calls << \"\\n\";\n";
  os << "    os << \"primitive_cache_skips=\" << _pyc_sim_stats.primitive_cache_skips << \"\\n\";\n";
  os << "    os << \"fallback_iterations=\" << _pyc_sim_stats.fallback_iterations << \"\\n\";\n";
  os << "  }\n\n";
  os << "  void dump_sim_stats_to_path(const char *path = nullptr) const {\n";
  os << "    const char *outPath = path;\n";
  os << "    if (!outPath || !*outPath)\n";
  os << "      outPath = _pyc_sim_stats_path.c_str();\n";
  os << "    if (!outPath || !*outPath)\n";
  os << "      return;\n";
  os << "    std::ofstream ofs(outPath, std::ios::out | std::ios::trunc);\n";
  os << "    if (!ofs)\n";
  os << "      return;\n";
  os << "    dump_sim_stats(ofs);\n";
  os << "  }\n\n";

  os << "  void _pyc_validate_primitive_bindings() const {\n";
  for (auto r : regs)
    os << "    if (!" << nt.get(r.getQ()) << "_inst) { std::cerr << \"pyc null reg binding: " << nt.get(r.getQ())
       << "_inst\" << \"\\n\"; std::abort(); }\n";
  for (auto mem : syncMems) {
    std::string instName = syncMemInstName.lookup(mem.getOperation());
    os << "    if (!" << instName << ") { std::cerr << \"pyc null sync_mem binding: " << instName
       << "\" << \"\\n\"; std::abort(); }\n";
  }
  for (auto mem : syncMemDPs) {
    std::string instName = syncMemDPInstName.lookup(mem.getOperation());
    os << "    if (!" << instName << ") { std::cerr << \"pyc null sync_mem_dp binding: " << instName
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
    os << "      " << instName << "(" << nt.get(mem.getClk()) << ", " << nt.get(mem.getRst()) << ", " << nt.get(mem.getRaddr())
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
  for (auto r : regs) {
    unsigned w = bitWidth(r.getQ().getType());
    if (w == 0)
      return r.emitError("invalid reg width");
    if (isa<VectorType>(r.getQ().getType()))
      os << "    " << nt.get(r.getQ()) << "_inst = new pyc::cpp::pyc_vec_reg<"
         << cppType(r.getQ().getType()) << ">(";
    else
      os << "    " << nt.get(r.getQ()) << "_inst = new pyc::cpp::pyc_reg<" << w << ">(";
    os << nt.get(r.getClk()) << ", " << nt.get(r.getRst()) << ", " << nt.get(r.getEn()) << ", "
       << nt.get(r.getNext()) << ", " << nt.get(r.getInit()) << ", " << nt.get(r.getQ()) << ");\n";
  }
  for (auto mem : syncMems) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    std::string instName = syncMemInstName.lookup(mem.getOperation());
    os << "    " << instName << " = new pyc::cpp::pyc_sync_mem<" << addrW << ", " << dataW << ", " << depth << ">("
       << nt.get(mem.getClk()) << ", " << nt.get(mem.getRst()) << ", " << nt.get(mem.getRen()) << ", "
       << nt.get(mem.getRaddr()) << ", " << nt.get(mem.getRdata()) << ", " << nt.get(mem.getWvalid()) << ", "
       << nt.get(mem.getWaddr()) << ", " << nt.get(mem.getWdata()) << ", " << nt.get(mem.getWstrb()) << ");\n";
  }
  for (auto mem : syncMemDPs) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr0().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata0().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem_dp types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    std::string instName = syncMemDPInstName.lookup(mem.getOperation());
    os << "    " << instName << " = new pyc::cpp::pyc_sync_mem_dp<" << addrW << ", " << dataW << ", " << depth << ">("
       << nt.get(mem.getClk()) << ", " << nt.get(mem.getRst()) << ", " << nt.get(mem.getRen0()) << ", "
       << nt.get(mem.getRaddr0()) << ", " << nt.get(mem.getRdata0()) << ", " << nt.get(mem.getRen1()) << ", "
       << nt.get(mem.getRaddr1()) << ", " << nt.get(mem.getRdata1()) << ", " << nt.get(mem.getWvalid()) << ", "
       << nt.get(mem.getWaddr()) << ", " << nt.get(mem.getWdata()) << ", " << nt.get(mem.getWstrb()) << ");\n";
  }
  os << "    _pyc_validate_primitive_bindings();\n";
  os << "    _pyc_init_runtime_controls();\n";
  os << "    #ifdef PYC_ENABLE_CTOR_EVAL\n";
  os << "    eval();\n";
  os << "    #endif\n";
  os << "  }\n\n";

  // Emit fused comb helpers. Indices match body order (same as placement).
  for (auto [i, comb] : llvm::enumerate(combs)) {
    if (failed(emitCombMethod(comb, os, nt, static_cast<unsigned>(i), opts)))
      return failure();
  }

  llvm::DenseMap<Operation *, unsigned> combIndex;
  for (auto [i, comb] : llvm::enumerate(combs))
    combIndex.try_emplace(comb.getOperation(), static_cast<unsigned>(i));

  auto topoOrder = [&](bool includePrims, llvm::SmallVector<Operation *> &ordered) -> bool {
    ordered.clear();

    llvm::SmallVector<Operation *> nodes;
    llvm::SmallVector<std::string> nodeKey;
    llvm::DenseMap<Operation *, unsigned> nodeIndex;

    auto shouldInclude = [&](Operation &op) -> bool {
      if (isa<func::ReturnOp>(op) || isa<pyc::WireOp>(op) || isa<pyc::RegOp>(op) || isa<pyc::SyncMemOp>(op) ||
          isa<pyc::SyncMemDPOp>(op) || isa<pyc::CdcSyncOp>(op))
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
      if (auto a = dyn_cast<pyc::AssignOp>(op))
        k = nt.get(a.getDst());
      else if (op.getNumResults() > 0)
        k = nt.get(op.getResult(0));
      else
        k = sanitizeId(op.getName().getStringRef()) + "_" + std::to_string(idx);
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
  os << "  inline void eval_comb_pass() {\n";
  llvm::SmallVector<Operation *> ordered;
  if (!topoOrder(/*includePrims=*/false, ordered)) {
    for (Operation &op : top) {
      if (isa<func::ReturnOp>(op) || isa<pyc::WireOp>(op))
        continue;
      ordered.push_back(&op);
    }
  }

  for (Operation *op : ordered) {
    if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
      os << "    " << nt.get(a.getDst()) << " = " << nt.get(a.getSrc()) << ";\n";
      continue;
    }
    if (auto comb = dyn_cast<pyc::CombOp>(*op)) {
      os << "    eval_comb_" << combIndex.lookup(comb.getOperation()) << "();\n";
      continue;
    }
    if (auto a = dyn_cast<pyc::AssertOp>(*op)) {
      std::string msg = "pyc.assert failed";
      if (auto m = a.getMsgAttr())
        msg = m.getValue().str();
      os << "    if (!" << nt.get(a.getCond()) << ".toBool()) { std::cerr << " << cppStringLiteral(msg)
         << " << \"\\n\"; std::abort(); }\n";
      continue;
    }
    if (isa<pyc::ConstantOp,
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
            arith::SelectOp>(*op)) {
      if (failed(emitCombAssign(*op, os, nt)))
        return failure();
      continue;
    }
    if (isa<pyc::FifoOp,
            pyc::AsyncFifoOp,
            pyc::ByteMemOp,
            pyc::SyncMemOp,
            pyc::SyncMemDPOp,
            pyc::CdcSyncOp,
            pyc::InstanceOp,
            pyc::RegOp>(*op)) {
      // Primitives are evaluated in eval(), and regs only tick.
      continue;
    }
    if (isa<func::ReturnOp, pyc::WireOp>(*op))
      continue;
    return op->emitError("unsupported op for C++ emission: ") << op->getName();
  }
  os << "  }\n\n";

  llvm::SmallVector<Operation *> fullOrdered;
  bool hasFullTopo = topoOrder(/*includePrims=*/true, fullOrdered);

  llvm::SmallVector<std::string> instanceEvalHelperNames;
  instanceEvalHelperNames.reserve(instInfos.size());
  for (unsigned idx = 0; idx < instInfos.size(); ++idx) {
    std::string helperName = "eval_instance_cached_" + std::to_string(idx);
    instanceEvalHelperNames.push_back(helperName);
  }

  auto emitInstanceEvalHelperDefinition = [&](const InstInfo &ii, llvm::StringRef helperName) {
    auto inst = ii.op;
    bool usePackedCache = usePackedInstanceEvalCache(ii);
    os << "  inline bool " << helperName << "() {\n";
    os << "    bool _pyc_inst_changed = false;\n";
    os << "    #ifdef PYC_DISABLE_INSTANCE_EVAL_CACHE\n";
    for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
      std::string inValue = nt.get(inst.getOperand(i));
      os << "    " << ii.member << "->" << ii.inPorts[i] << " = " << inValue << ";\n";
    }
    os << "    " << ii.member << "->eval();\n";
    os << "    if (_pyc_sim_stats_enable) _pyc_sim_stats.instance_eval_calls++;\n";
    os << "    _pyc_inst_changed = true;\n";
    os << "    #else\n";
    std::string changedFlag = ii.member + "_eval_cache_changed";
    os << "    bool " << changedFlag << " = !" << ii.member << "_eval_cache_valid;\n";
    if (usePackedCache) {
      os << "    std::array<std::uint64_t, " << instancePackedCacheWordCount(ii) << "> _pyc_inputs{};\n";
      os << "    std::size_t _pyc_inputs_off = 0;\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
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
      os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.instance_eval_calls++;\n";
      os << "      " << ii.member << "_eval_cache_words = _pyc_inputs;\n";
      os << "    } else {\n";
      os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.instance_cache_skips++;\n";
      os << "    }\n";
      os << "    _pyc_inst_changed = " << changedFlag << ";\n";
      os << "    " << ii.member << "_eval_cache_valid = true;\n";
      os << "    #endif\n";
    } else {
      os << "    #ifndef PYC_DISABLE_VERSIONED_INPUT_CACHE\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
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
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
        os << "      " << ii.member << "->" << ii.inPorts[i] << " = " << cacheName << ";\n";
      }
      os << "      " << ii.member << "->eval();\n";
      os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.instance_eval_calls++;\n";
      os << "    } else {\n";
      os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.instance_cache_skips++;\n";
      os << "    }\n";
      os << "    #else\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
        std::string inValue = nt.get(inst.getOperand(i));
        os << "    if (!" << changedFlag << " && (" << cacheName << " != " << inValue << ")) " << changedFlag
           << " = true;\n";
      }
      os << "    if (" << changedFlag << ") {\n";
      for (unsigned i = 0; i < inst.getNumOperands(); ++i) {
        std::string cacheName = ii.member + "_eval_cache_in_" + std::to_string(i);
        std::string inValue = nt.get(inst.getOperand(i));
        os << "      " << ii.member << "->" << ii.inPorts[i] << " = " << inValue << ";\n";
        os << "      " << cacheName << " = " << inValue << ";\n";
      }
      os << "      " << ii.member << "->eval();\n";
      os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.instance_eval_calls++;\n";
      os << "    } else {\n";
      os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.instance_cache_skips++;\n";
      os << "    }\n";
      os << "    #endif\n";
      os << "    _pyc_inst_changed = " << changedFlag << ";\n";
      os << "    " << ii.member << "_eval_cache_valid = true;\n";
      os << "    #endif\n";
    }
    for (unsigned i = 0; i < inst.getNumResults(); ++i)
      os << "    " << nt.get(inst.getResult(i)) << " = " << ii.member << "->" << ii.outPorts[i] << ";\n";
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
    if (!changedAnyVar.empty()) {
      os << indent << "if (" << helperName << "()) " << changedAnyVar << " = true;\n";
    } else {
      os << indent << helperName << "();\n";
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
    os << indent << "#ifdef PYC_DISABLE_PRIMITIVE_EVAL_CACHE\n";
    os << indent << instName << ".eval();\n";
    os << indent << "if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_eval_calls++;\n";
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
    os << indent << "  if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_eval_calls++;\n";
    os << indent << "} else {\n";
    os << indent << "  if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_cache_skips++;\n";
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
    os << indent << "#ifdef PYC_DISABLE_PRIMITIVE_EVAL_CACHE\n";
    os << indent << instName << ".eval();\n";
    os << indent << "if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_eval_calls++;\n";
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
    os << indent << "  if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_eval_calls++;\n";
    os << indent << "} else {\n";
    os << indent << "  if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_cache_skips++;\n";
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
  };

  auto emitByteMemEvalWithCache =
      [&](pyc::ByteMemOp mem, llvm::StringRef indent, llvm::StringRef changedAnyVar = llvm::StringRef()) {
    std::string instName = byteMemInstName.lookup(mem.getOperation());
    std::string changedFlag = instName + "_eval_cache_changed";
    std::string rst = nt.get(mem.getRst());
    std::string raddr = nt.get(mem.getRaddr());
    std::string wvalid = nt.get(mem.getWvalid());
    std::string waddr = nt.get(mem.getWaddr());
    std::string wdata = nt.get(mem.getWdata());
    std::string wstrb = nt.get(mem.getWstrb());
    os << indent << "#ifdef PYC_DISABLE_PRIMITIVE_EVAL_CACHE\n";
    os << indent << instName << ".eval();\n";
    os << indent << "if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_eval_calls++;\n";
    if (!changedAnyVar.empty())
      os << indent << changedAnyVar << " = true;\n";
    os << indent << "#else\n";
    os << indent << "bool " << changedFlag << " = !" << instName << "_eval_cache_valid;\n";
    os << indent << "if (!" << changedFlag << " && (" << instName << "_eval_cache_rst != " << rst << ")) "
       << changedFlag << " = true;\n";
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
    os << indent << "  if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_eval_calls++;\n";
    os << indent << "} else {\n";
    os << indent << "  if (_pyc_sim_stats_enable) _pyc_sim_stats.primitive_cache_skips++;\n";
    os << indent << "}\n";
    if (!changedAnyVar.empty())
      os << indent << "if (" << changedFlag << ") " << changedAnyVar << " = true;\n";
    os << indent << "if (" << changedFlag << ") {\n";
    os << indent << "  " << instName << "_eval_cache_rst = " << rst << ";\n";
    os << indent << "  " << instName << "_eval_cache_raddr = " << raddr << ";\n";
    os << indent << "  " << instName << "_eval_cache_wvalid = " << wvalid << ";\n";
    os << indent << "  " << instName << "_eval_cache_waddr = " << waddr << ";\n";
    os << indent << "  " << instName << "_eval_cache_wdata = " << wdata << ";\n";
    os << indent << "  " << instName << "_eval_cache_wstrb = " << wstrb << ";\n";
    os << indent << "}\n";
    os << indent << instName << "_eval_cache_valid = true;\n";
    os << indent << "#endif\n";
  };

  auto emitEvalNode =
      [&](Operation *op, llvm::StringRef indent, llvm::StringRef changedAnyVar = llvm::StringRef()) -> LogicalResult {
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
      os << indent << nt.get(a.getDst()) << " = " << nt.get(a.getSrc()) << ";\n";
      return success();
    }
    if (auto comb = dyn_cast<pyc::CombOp>(*op)) {
      os << indent << "eval_comb_" << combIndex.lookup(comb.getOperation()) << "();\n";
      return success();
    }
    if (isa<pyc::ConstantOp,
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
            arith::SelectOp>(*op)) {
      if (failed(emitCombAssign(*op, os, nt)))
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
      if (isa<func::ReturnOp>(op) || isa<pyc::WireOp>(op) || isa<pyc::RegOp>(op) || isa<pyc::SyncMemOp>(op) ||
          isa<pyc::SyncMemDPOp>(op) || isa<pyc::CdcSyncOp>(op))
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
      os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.fallback_iterations++;\n";
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
          os << "      if (_pyc_sim_stats_enable) _pyc_sim_stats.fallback_iterations++;\n";
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
  if (hasFullTopo && !fullOrdered.empty()) {
    unsigned evalTopoChunkNodes = std::max(1u, opts.evalTopoChunkNodes);
    if (fullOrdered.size() > evalTopoChunkNodes) {
      topoEvalMethods.reserve((fullOrdered.size() + evalTopoChunkNodes - 1) / evalTopoChunkNodes);
      for (unsigned begin = 0, chunkIdx = 0; begin < fullOrdered.size(); begin += evalTopoChunkNodes, ++chunkIdx) {
        unsigned end = std::min<unsigned>(static_cast<unsigned>(fullOrdered.size()), begin + evalTopoChunkNodes);
        std::string methodName = "eval_topo_part_" + std::to_string(chunkIdx);
        topoEvalMethods.push_back(methodName);
        os << "  inline void " << methodName << "() {\n";
        for (unsigned i = begin; i < end; ++i)
          if (failed(emitEvalNode(fullOrdered[i], "    ")))
            return failure();
        os << "  }\n\n";
      }
    }
  }

  os << "  void eval() {\n";
  if (hasFullTopo) {
    if (!topoEvalMethods.empty()) {
      for (const std::string &methodName : topoEvalMethods)
        os << "    " << methodName << "();\n";
    } else {
      for (Operation *op : fullOrdered)
        if (failed(emitEvalNode(op, "    ")))
          return failure();
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
    os << "    " << outNames[i] << " = " << nt.get(v) << ";\n";

  os << "  }\n\n";

  // tick_compute/tick_commit: two-phase sequential update (hierarchy-aware).
  //
  // Large designs can produce enormous tick bodies (notably JanusBccBackendCompat), which
  // makes a single translation unit fragile and slow to compile. Split tick into helper
  // parts so --cpp-split=module can shard tick across multiple .cpp files.
  constexpr unsigned kTickChunk = 256;

  auto emitTickComputePart = [&](unsigned begin, unsigned end, unsigned partIdx) {
    os << "  inline void tick_compute_part_" << partIdx << "() {\n";
    // Sub-modules (inputs + tick_compute).
    for (unsigned i = begin; i < end && i < instInfos.size(); ++i) {
      const auto &ii = instInfos[i];
      auto inst = ii.op;
      for (unsigned j = 0; j < inst.getNumOperands(); ++j)
        os << "    " << ii.member << "->" << ii.inPorts[j] << " = " << nt.get(inst.getOperand(j)) << ";\n";
      os << "    " << ii.member << "->tick_compute();\n";
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

  // Emit chunked submodule tick helpers.
  unsigned subParts = 0;
  if (!instInfos.empty()) {
    for (unsigned b = 0; b < instInfos.size(); b += kTickChunk)
      emitTickComputePart(b, std::min<unsigned>(static_cast<unsigned>(instInfos.size()), b + kTickChunk), subParts++);
    unsigned commitParts = 0;
    for (unsigned b = 0; b < instInfos.size(); b += kTickChunk)
      emitTickCommitPart(b, std::min<unsigned>(static_cast<unsigned>(instInfos.size()), b + kTickChunk), commitParts++);
  }

  os << "  void tick_compute() {\n";
  if (!instInfos.empty()) {
    os << "    // Sub-modules.\n";
    for (unsigned i = 0; i < subParts; ++i)
      os << "    tick_compute_part_" << i << "();\n";
  }
  os << "    // Local sequential primitives.\n";
  for (auto r : regs)
    os << "    " << nt.get(r.getQ()) << "_inst->tick_compute();\n";
  for (auto fifo : fifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_compute();\n";
  for (auto mem : byteMems)
    os << "    " << byteMemInstName.lookup(mem.getOperation()) << ".tick_compute();\n";
  for (auto mem : syncMems)
    os << "    " << syncMemInstName.lookup(mem.getOperation()) << "->tick_compute();\n";
  for (auto mem : syncMemDPs)
    os << "    " << syncMemDPInstName.lookup(mem.getOperation()) << "->tick_compute();\n";
  for (auto fifo : asyncFifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_compute();\n";
  for (auto s : cdcSyncs)
    os << "    " << nt.get(s.getOut()) << "_inst.tick_compute();\n";
  os << "  }\n\n";

  os << "  void tick_commit() {\n";
  if (!instInfos.empty()) {
    os << "    // Sub-modules.\n";
    unsigned commitParts = (static_cast<unsigned>(instInfos.size()) + kTickChunk - 1) / kTickChunk;
    for (unsigned i = 0; i < commitParts; ++i)
      os << "    tick_commit_part_" << i << "();\n";
  }
  os << "    // Local sequential primitives.\n";
  for (auto r : regs)
    os << "    " << nt.get(r.getQ()) << "_inst->tick_commit();\n";
  for (auto fifo : fifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_commit();\n";
  for (auto mem : byteMems)
    os << "    " << byteMemInstName.lookup(mem.getOperation()) << ".tick_commit();\n";
  for (auto mem : syncMems)
    os << "    " << syncMemInstName.lookup(mem.getOperation()) << "->tick_commit();\n";
  for (auto mem : syncMemDPs)
    os << "    " << syncMemDPInstName.lookup(mem.getOperation()) << "->tick_commit();\n";
  for (auto fifo : asyncFifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_commit();\n";
  for (auto s : cdcSyncs)
    os << "    " << nt.get(s.getOut()) << "_inst.tick_commit();\n";
  if (!instInfos.empty()) {
    os << "    // Force re-eval on next eval() only for stateful sub-modules.\n";
    for (unsigned i = 0; i < instInfos.size(); ++i) {
      if (i < instHasSequentialCallee.size() && instHasSequentialCallee[i]) {
        os << "    " << instInfos[i].member << "_eval_cache_valid = false;\n";
      }
    }
  }
  for (auto fifo : fifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst_eval_cache_valid = false;\n";
  for (auto mem : byteMems)
    os << "    " << byteMemInstName.lookup(mem.getOperation()) << "_eval_cache_valid = false;\n";
	  for (auto fifo : asyncFifos)
	    os << "    " << nt.get(fifo.getInReady()) << "_inst_eval_cache_valid = false;\n";
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

  os << "// pyCircuit C++ emission (prototype)\n";
  os << "#include <cstdlib>\n";
  os << "#include <cstdint>\n";
  os << "#include <fstream>\n";
  os << "#include <iostream>\n";
  os << "#include <memory>\n";
  os << "#include <string>\n";
  os << "#include <cpp/pyc_sim.hpp>\n\n";
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
    if (failed(emitFunc(funcs[idx], os, effectiveOpts)))
      return failure();
  }

  os << "} // namespace pyc::gen\n";
  return success();
}

LogicalResult emitCppFunc(ModuleOp module, func::FuncOp f, llvm::raw_ostream &os, const CppEmitterOptions &opts) {
  if (failed(requireCombChunkConfig(module)))
    return failure();
  return emitFunc(f, os, effectiveEmitOpts(module, opts));
}

} // namespace pyc
