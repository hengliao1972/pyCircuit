#include "pyc/Emit/VerilogEmitter.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Operation.h"
#include "mlir/IR/Value.h"
#include "mlir/IR/Visitors.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/SmallSet.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/ADT/StringRef.h"

#include <algorithm>
#include <optional>
#include <functional>
#include <vector>

using namespace mlir;

namespace pyc {
namespace {

/// Leaf integer type of a scalar hardware value.
static IntegerType leafIntType(Type ty) {
  return dyn_cast<IntegerType>(ty);
}

static std::string vRange(Type ty) {
  // Clocks/resets are treated as 1-bit scalar ports/nets in Verilog.
  if (isa<pyc::ClockType>(ty) || isa<pyc::ResetType>(ty))
    return "";
  auto intTy = dyn_cast<IntegerType>(ty);
  if (!intTy)
    return "";
  if (intTy.getWidth() <= 1)
    return "";
  return "[" + std::to_string(intTy.getWidth() - 1) + ":0]";
}

static std::optional<unsigned> leafWidth(Type ty) {
  auto intTy = leafIntType(ty);
  if (!intTy)
    return std::nullopt;
  return intTy.getWidth();
}

/// Packed range for module boundary ports.
static std::string vPortRange(Type ty) {
  auto bits = leafWidth(ty);
  if (!bits || *bits <= 1)
    return "";
  return "[" + std::to_string(*bits - 1) + ":0]";
}

static std::string vLiteral(IntegerAttr a, Type dstTy) {
  auto intTy = dyn_cast<IntegerType>(dstTy);
  if (!intTy)
    return "0";
  unsigned w = intTy.getWidth();
  const llvm::APInt v = a.getValue();
  // Decimal formatting is fine for small constants; wide values may exceed 64b
  // and cannot use APInt::getZExtValue().
  if (v.getActiveBits() <= 64) {
    return std::to_string(w) + "'d" + std::to_string(v.getZExtValue());
  }
  llvm::SmallString<256> hex;
  v.toStringUnsigned(hex, /*Radix=*/16);
  if (hex.empty())
    hex = "0";
  return std::to_string(w) + "'h" + hex.str().str();
}

static std::string vZero(Type dstTy) {
  auto intTy = leafIntType(dstTy);
  if (!intTy)
    return "0";
  unsigned w = intTy.getWidth();
  return std::to_string(w) + "'d0";
}

static std::string sanitizeId(llvm::StringRef s) {
  std::string out;
  out.reserve(s.size() + 1);
  auto isAlpha = [](char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); };
  auto isDigit = [](char c) { return (c >= '0' && c <= '9'); };
  auto isOk = [&](char c) { return isAlpha(c) || isDigit(c) || c == '_'; };

  for (char c : s)
    out.push_back(isOk(c) ? c : '_');
  if (out.empty() || isDigit(out.front()))
    out.insert(out.begin(), '_');
  return out;
}

struct NameTable {
  DenseMap<Value, std::string> names;
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

static std::string getPortName(func::FuncOp f, unsigned idx, bool isResult) {
  std::string raw;
  if (!isResult) {
    if (auto names = f->getAttrOfType<ArrayAttr>("arg_names")) {
      if (idx < names.size())
        if (auto s = dyn_cast<StringAttr>(names[idx]))
          raw = s.getValue().str();
    }
    if (raw.empty())
      raw = "arg" + std::to_string(idx);
    return sanitizeId(raw);
  }
  if (auto names = f->getAttrOfType<ArrayAttr>("result_names")) {
    if (idx < names.size())
      if (auto s = dyn_cast<StringAttr>(names[idx]))
        raw = s.getValue().str();
  }
  if (raw.empty())
    raw = "out" + std::to_string(idx);
  return sanitizeId(raw);
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

// Emit a single combinational assignment for the common scalar/elementwise op
// set shared by the pyc.comb region path and the top-level netlist path.
// Returns std::nullopt when `op` is not one of these ops (the caller handles
// container-specific ops such as pyc.assert / pyc.assign / pyc.comb).
static void emitConnectAssign(llvm::StringRef lhs, llvm::StringRef rhs, Type ty, raw_ostream &os);
static std::optional<LogicalResult> emitScalarOpAssign(Operation &op, raw_ostream &os, NameTable &nt) {
  if (auto c = dyn_cast<pyc::ConstantOp>(op)) {
    os << "assign " << nt.get(c.getResult()) << " = " << vLiteral(c.getValueAttr(), c.getType()) << ";\n";
    return success();
  }
  if (auto a = dyn_cast<pyc::AliasOp>(op)) {
    os << "assign " << nt.get(a.getResult()) << " = " << nt.get(a.getIn()) << ";\n";
    return success();
  }
  if (auto ra = dyn_cast<pyc::ResetActiveOp>(op)) {
    os << "assign " << nt.get(ra.getActive()) << " = " << nt.get(ra.getRst()) << ";\n";
    return success();
  }
  if (auto a = dyn_cast<pyc::AddOp>(op)) {
    os << "assign " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " + " << nt.get(a.getRhs()) << ");\n";
    return success();
  }
  if (auto s = dyn_cast<pyc::SubOp>(op)) {
    os << "assign " << nt.get(s.getResult()) << " = (" << nt.get(s.getLhs()) << " - " << nt.get(s.getRhs()) << ");\n";
    return success();
  }
  if (auto m = dyn_cast<pyc::MulOp>(op)) {
    os << "assign " << nt.get(m.getResult()) << " = (" << nt.get(m.getLhs()) << " * " << nt.get(m.getRhs()) << ");\n";
    return success();
  }
  if (auto d = dyn_cast<pyc::UdivOp>(op)) {
    os << "assign " << nt.get(d.getResult()) << " = (" << nt.get(d.getRhs()) << " == " << vZero(d.getRhs().getType())
       << " ? " << vZero(d.getResult().getType()) << " : (" << nt.get(d.getLhs()) << " / " << nt.get(d.getRhs())
       << "));\n";
    return success();
  }
  if (auto r = dyn_cast<pyc::UremOp>(op)) {
    os << "assign " << nt.get(r.getResult()) << " = (" << nt.get(r.getRhs()) << " == " << vZero(r.getRhs().getType())
       << " ? " << vZero(r.getResult().getType()) << " : (" << nt.get(r.getLhs()) << " % " << nt.get(r.getRhs())
       << "));\n";
    return success();
  }
  if (auto d = dyn_cast<pyc::SdivOp>(op)) {
    os << "assign " << nt.get(d.getResult()) << " = (" << nt.get(d.getRhs()) << " == " << vZero(d.getRhs().getType())
       << " ? $signed(" << vZero(d.getResult().getType()) << ") : ($signed(" << nt.get(d.getLhs()) << ") / $signed("
       << nt.get(d.getRhs()) << ")));\n";
    return success();
  }
  if (auto r = dyn_cast<pyc::SremOp>(op)) {
    os << "assign " << nt.get(r.getResult()) << " = (" << nt.get(r.getRhs()) << " == " << vZero(r.getRhs().getType())
       << " ? $signed(" << vZero(r.getResult().getType()) << ") : ($signed(" << nt.get(r.getLhs()) << ") % $signed("
       << nt.get(r.getRhs()) << ")));\n";
    return success();
  }
  if (auto m = dyn_cast<pyc::MuxOp>(op)) {
    os << "assign " << nt.get(m.getResult()) << " = (" << nt.get(m.getSel()) << " ? " << nt.get(m.getA()) << " : "
       << nt.get(m.getB()) << ");\n";
    return success();
  }
  if (auto s = dyn_cast<arith::SelectOp>(op)) {
    if (!s.getCondition().getType().isInteger(1))
      return {s.emitError("verilog emitter only supports arith.select with i1 condition")};
    os << "assign " << nt.get(s.getResult()) << " = (" << nt.get(s.getCondition()) << " ? "
       << nt.get(s.getTrueValue()) << " : " << nt.get(s.getFalseValue()) << ");\n";
    return success();
  }
  if (auto a = dyn_cast<pyc::AndOp>(op)) {
    os << "assign " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " & " << nt.get(a.getRhs()) << ");\n";
    return success();
  }
  if (auto o = dyn_cast<pyc::OrOp>(op)) {
    os << "assign " << nt.get(o.getResult()) << " = (" << nt.get(o.getLhs()) << " | " << nt.get(o.getRhs()) << ");\n";
    return success();
  }
  if (auto x = dyn_cast<pyc::XorOp>(op)) {
    os << "assign " << nt.get(x.getResult()) << " = (" << nt.get(x.getLhs()) << " ^ " << nt.get(x.getRhs()) << ");\n";
    return success();
  }
  if (auto n = dyn_cast<pyc::NotOp>(op)) {
    os << "assign " << nt.get(n.getResult()) << " = (~" << nt.get(n.getIn()) << ");\n";
    return success();
  }
  if (auto e = dyn_cast<pyc::EqOp>(op)) {
    os << "assign " << nt.get(e.getResult()) << " = (" << nt.get(e.getLhs()) << " == " << nt.get(e.getRhs()) << ");\n";
    return success();
  }
  if (auto u = dyn_cast<pyc::UltOp>(op)) {
    os << "assign " << nt.get(u.getResult()) << " = (" << nt.get(u.getLhs()) << " < " << nt.get(u.getRhs()) << ");\n";
    return success();
  }
  if (auto s = dyn_cast<pyc::SltOp>(op)) {
    os << "assign " << nt.get(s.getResult()) << " = ($signed(" << nt.get(s.getLhs()) << ") < $signed("
       << nt.get(s.getRhs()) << "));\n";
    return success();
  }
  if (auto t = dyn_cast<pyc::TruncOp>(op)) {
    auto outTy = leafIntType(t.getResult().getType());
    if (!outTy)
      return {t.emitError("verilog emitter only supports integer trunc")};
    unsigned w = outTy.getWidth();
    if (w == 1)
      os << "assign " << nt.get(t.getResult()) << " = " << nt.get(t.getIn()) << "[0];\n";
    else
      os << "assign " << nt.get(t.getResult()) << " = " << nt.get(t.getIn()) << "[" << (w - 1) << ":0];\n";
    return success();
  }
  if (auto z = dyn_cast<pyc::ZextOp>(op)) {
    auto inTy = leafIntType(z.getIn().getType());
    auto outTy = leafIntType(z.getResult().getType());
    if (!inTy || !outTy)
      return {z.emitError("verilog emitter only supports integer zext")};
    unsigned iw = inTy.getWidth();
    unsigned ow = outTy.getWidth();
    if (ow == iw)
      os << "assign " << nt.get(z.getResult()) << " = " << nt.get(z.getIn()) << ";\n";
    else
      os << "assign " << nt.get(z.getResult()) << " = {{" << (ow - iw) << "{1'b0}}, " << nt.get(z.getIn()) << "};\n";
    return success();
  }
  if (auto s = dyn_cast<pyc::SextOp>(op)) {
    auto inTy = leafIntType(s.getIn().getType());
    auto outTy = leafIntType(s.getResult().getType());
    if (!inTy || !outTy)
      return {s.emitError("verilog emitter only supports integer sext")};
    unsigned iw = inTy.getWidth();
    unsigned ow = outTy.getWidth();
    if (ow == iw)
      os << "assign " << nt.get(s.getResult()) << " = " << nt.get(s.getIn()) << ";\n";
    else
      os << "assign " << nt.get(s.getResult()) << " = {{" << (ow - iw) << "{" << nt.get(s.getIn()) << "["
         << (iw - 1) << "]}}, " << nt.get(s.getIn()) << "};\n";
    return success();
  }
  if (auto ex = dyn_cast<pyc::ExtractOp>(op)) {
    auto inTy = leafIntType(ex.getIn().getType());
    auto outTy = leafIntType(ex.getResult().getType());
    if (!inTy || !outTy)
      return {ex.emitError("verilog emitter only supports integer extract")};
    unsigned ow = outTy.getWidth();
    std::int64_t lsb = ex.getLsbAttr().getInt();
    if (ow == 1)
      os << "assign " << nt.get(ex.getResult()) << " = " << nt.get(ex.getIn()) << "[" << lsb << "];\n";
    else
      os << "assign " << nt.get(ex.getResult()) << " = " << nt.get(ex.getIn()) << "[" << (lsb + ow - 1) << ":"
         << lsb << "];\n";
    return success();
  }
  if (isa<pyc::DelayTapOp>(op))
    return {op.emitError("pyc.delay_tap is not supported by the Verilog "
                         "backend (delay lines are C++-only; re-emit with "
                         "--emit=cpp or disable state optimization)")};
  if (auto sh = dyn_cast<pyc::ShliOp>(op)) {
    os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " << " << sh.getAmountAttr().getInt()
       << ");\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::LshriOp>(op)) {
    os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " >> " << sh.getAmountAttr().getInt()
       << ");\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::AshriOp>(op)) {
    os << "assign " << nt.get(sh.getResult()) << " = ($signed(" << nt.get(sh.getIn()) << ") >>> "
       << sh.getAmountAttr().getInt() << ");\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::ShlOp>(op)) {
    os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " << " << nt.get(sh.getAmount())
       << ");\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::LshrOp>(op)) {
    os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " >> " << nt.get(sh.getAmount())
       << ");\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::AshrOp>(op)) {
    os << "assign " << nt.get(sh.getResult()) << " = ($signed(" << nt.get(sh.getIn()) << ") >>> "
       << nt.get(sh.getAmount()) << ");\n";
    return success();
  }
  if (auto c = dyn_cast<pyc::ConcatOp>(op)) {
    os << "assign " << nt.get(c.getResult()) << " = {";
    for (auto [i, v] : llvm::enumerate(c.getInputs())) {
      if (i)
        os << ", ";
      os << nt.get(v);
    }
    os << "};\n";
    return success();
  }
  return std::nullopt;
}

// Emit a netlist op.
static LogicalResult emitNetlistOp(Operation &op, raw_ostream &os, NameTable &nt) {
  std::optional<LogicalResult> handled = emitScalarOpAssign(op, os, nt);
  if (!handled)
    return op.emitError("internal error: missing verilog emission handler");
  return *handled;
}

// Emit a continuous connection \`lhs = rhs\`.
static void emitConnectAssign(llvm::StringRef lhs, llvm::StringRef rhs, Type ty, raw_ostream &os) {
  (void)ty;
  os << "assign " << lhs << " = " << rhs << ";\n";
}

static LogicalResult emitComb(pyc::CombOp comb, raw_ostream &os, NameTable &nt) {
  if (comb.getBody().empty())
    return comb.emitError("pyc.comb must have a non-empty region");
  Block &b = comb.getBody().front();
  if (b.getNumArguments() != comb.getNumOperands())
    return comb.emitError("pyc.comb region block args must match input operand count");

  for (auto [i, arg] : llvm::enumerate(b.getArguments()))
    nt.names.try_emplace(arg, nt.get(comb.getInputs()[i]));

  for (Operation &op : b) {
    if (isa<pyc::YieldOp>(op))
      break;
    if (failed(emitNetlistOp(op, os, nt)))
      return failure();
  }

  auto yield = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
  if (!yield)
    return comb.emitError("pyc.comb must terminate with pyc.yield");
  if (yield.getNumOperands() != comb.getNumResults())
    return comb.emitError("pyc.yield operand count must match pyc.comb results");

  for (auto [i, v] : llvm::enumerate(yield.getOperands()))
    emitConnectAssign(nt.get(comb.getResult(i)), nt.get(v), comb.getResult(i).getType(), os);

  return success();
}

struct NetDecl {
  std::string name;
  Type ty;
  std::string comment;
};

static std::string opSortKey(Operation *op, NameTable &nt) {
  if (auto a = dyn_cast<pyc::AssignOp>(op))
    return nt.get(a.getDst());
  if (auto mem = dyn_cast<pyc::ByteMemOp>(op)) {
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(mem.getRdata());
  }
  if (auto mem = dyn_cast<pyc::SyncMemNMOp>(op)) {
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(mem.getRdatas()[0]);
  }
  if (!op->getResults().empty())
    return nt.get(op->getResult(0));
  return "";
}

static bool topoSortCombOps(ArrayRef<Operation *> ops, NameTable &nt, llvm::SmallVectorImpl<Operation *> &ordered) {
  ordered.clear();
  if (ops.empty())
    return true;

  llvm::SmallVector<std::string> nodeKey;
  nodeKey.reserve(ops.size());
  llvm::DenseMap<Operation *, unsigned> nodeIndex;
  nodeIndex.reserve(ops.size());
  for (auto [i, op] : llvm::enumerate(ops)) {
    nodeIndex.try_emplace(op, static_cast<unsigned>(i));
    nodeKey.push_back(opSortKey(op, nt));
  }

  llvm::DenseMap<Value, unsigned> valueProducer;
  llvm::DenseMap<Value, unsigned> wireAssign;
  llvm::DenseMap<Value, unsigned> wireAssignCount;

  for (auto [idx, op] : llvm::enumerate(ops)) {
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

  // Verilog does not support multiple continuous drivers for a single net in this prototype.
  for (auto &it : wireAssignCount) {
    if (it.second > 1)
      return false;
  }
  for (auto &it : wireAssign)
    valueProducer[it.first] = it.second;

  llvm::SmallVector<llvm::SmallVector<unsigned>> succ(ops.size());
  llvm::SmallVector<unsigned> indeg(ops.size(), 0);

  for (auto it : llvm::enumerate(ops)) {
    unsigned idx = it.index();
    Operation *op = it.value();

    llvm::SmallDenseSet<unsigned, 8> deps;
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
  heap.reserve(ops.size());
  for (unsigned i = 0; i < ops.size(); ++i)
    if (indeg[i] == 0)
      heap.push_back(i);
  std::make_heap(heap.begin(), heap.end(), cmp);

  llvm::SmallVector<unsigned> out;
  out.reserve(ops.size());
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

  if (out.size() != ops.size())
    return false;

  for (unsigned idx : out)
    ordered.push_back(ops[idx]);
  return true;
}

static LogicalResult emitFunc(func::FuncOp f, raw_ostream &os, const VerilogEmitterOptions &opts) {
  (void)opts;
  NameTable nt;
  std::vector<std::string> outNames;
  outNames.reserve(f.getNumResults());
  os << "// Generated by pycc (pyCircuit)\n";
  os << "// Module: " << f.getSymName() << "\n\n";

  // Module header.
  os << "module " << f.getSymName() << " (\n";
  for (auto [i, arg] : llvm::enumerate(f.getArguments())) {
    std::string portName = nt.unique(getPortName(f, i, /*isResult=*/false));
    std::string range = vPortRange(arg.getType());
    os << "  input ";
    if (!range.empty())
      os << range << " ";
    os << portName;
    os << ((i + 1 == f.getNumArguments() && f.getNumResults() == 0) ? "\n" : ",\n");
    nt.names.try_emplace(arg, portName);
  }
  for (unsigned i = 0; i < f.getNumResults(); ++i) {
    std::string portName = nt.unique(getPortName(f, i, /*isResult=*/true));
    outNames.push_back(portName);
    std::string range = vPortRange(f.getResultTypes()[i]);
    os << "  output ";
    if (!range.empty())
      os << range << " ";
    os << portName;
    os << ((i + 1 == f.getNumResults()) ? "\n" : ",\n");
  }
  os << ");\n\n";

  // Declare internal nets for op results (including results inside pyc.comb regions).
  std::vector<NetDecl> decls;
  decls.reserve(256);
  f.walk([&](Operation *op) {
    for (Value r : op->getResults()) {
      NetDecl d;
      d.name = nt.get(r);
      d.ty = r.getType();
      if (auto nAttr = op->getAttrOfType<StringAttr>("pyc.name"))
        d.comment = "pyc.name=\"" + nAttr.getValue().str() + "\"";
      else
        d.comment = "op=" + op->getName().getStringRef().str();
      decls.push_back(std::move(d));
    }
  });
  std::sort(decls.begin(), decls.end(), [](const NetDecl &a, const NetDecl &b) { return a.name < b.name; });
  for (const NetDecl &d : decls) {
    std::string range = vRange(d.ty);
    os << "wire ";
    if (!range.empty())
      os << range << " ";
    os << d.name << ";";
    if (!d.comment.empty())
      os << " // " << d.comment;
    os << "\n";
  }
  os << "\n";

  // Reject delay lines early with a clear diagnostic: the Verilog backend
  // keeps the pre-optimization netlist contract and never emits them.
  bool sawDelayLine = false;
  f.walk([&](pyc::DelayLineOp) { sawDelayLine = true; });
  if (sawDelayLine)
    return f.emitError("pyc.delay_line is not supported by the Verilog "
                       "backend (delay lines are C++-only; re-emit with "
                       "--emit=cpp or disable state optimization)");
  os << "\n";

  // Collect top-level ops for netlist-friendly emission.
  llvm::SmallVector<Operation *> combAssignOps;
  llvm::SmallVector<Operation *> instOps;
  llvm::SmallVector<Operation *> seqInstOps;

  for (Block &b : f.getBody()) {
    for (Operation &op : b) {
      if (isa<func::ReturnOp>(op))
        continue;
      if (isa<pyc::WireOp>(op))
        continue;

      if (isa<pyc::ConstantOp,
              pyc::AliasOp,
              pyc::ResetActiveOp,
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
              pyc::AssertOp,
              pyc::AssignOp,
              pyc::CombOp,
              arith::SelectOp,
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
              pyc::ConcatOp>(op)) {
        combAssignOps.push_back(&op);
        continue;
      }
      if (isa<pyc::InstanceOp>(op)) {
        instOps.push_back(&op);
        continue;
      }
      if (isa<pyc::RegOp, pyc::FifoOp, pyc::ByteMemOp>(op)) {
        seqInstOps.push_back(&op);
        continue;
      }
      if (isa<pyc::SyncMemNMOp, pyc::AsyncFifoOp, pyc::CdcSyncOp>(op)) {
        seqInstOps.push_back(&op);
        continue;
      }
      return op.emitError("unsupported op for verilog emission: ") << op.getName();
    }
  }

  auto cmp = [&](Operation *a, Operation *b) { return opSortKey(a, nt) < opSortKey(b, nt); };
  std::sort(instOps.begin(), instOps.end(), cmp);
  std::sort(seqInstOps.begin(), seqInstOps.end(), cmp);
  llvm::SmallVector<Operation *> orderedComb;
  if (!topoSortCombOps(combAssignOps, nt, orderedComb))
    std::sort(combAssignOps.begin(), combAssignOps.end(), cmp);
  else
    combAssignOps.assign(orderedComb.begin(), orderedComb.end());

  if (!combAssignOps.empty()) {
    os << "// --- Combinational (netlist)\n";
    for (Operation *op : combAssignOps) {
      if (auto a = dyn_cast<pyc::AssertOp>(op)) {
        std::string msg = "pyc.assert failed";
        if (auto m = a.getMsgAttr())
          msg = m.getValue().str();
        std::string esc;
        esc.reserve(msg.size());
        for (char c : msg) {
          if (c == '"' || c == '\\')
            esc.push_back('\\');
          esc.push_back(c);
        }
        os << "`ifndef SYNTHESIS\n";
        os << "always @(*) begin\n";
        os << "  if (!(" << nt.get(a.getCond()) << ")) $fatal(1, \"" << esc << "\");\n";
        os << "end\n";
        os << "`endif\n";
        continue;
      }
      if (auto a = dyn_cast<pyc::AssignOp>(op)) {
        emitConnectAssign(nt.get(a.getDst()), nt.get(a.getSrc()), a.getDst().getType(), os);
        continue;
      }
      if (auto comb = dyn_cast<pyc::CombOp>(op)) {
        if (failed(emitComb(comb, os, nt)))
          return failure();
        continue;
      }
      if (failed(emitNetlistOp(*op, os, nt)))
        return failure();
    }
    os << "\n";
  }

  if (!instOps.empty()) {
    os << "// --- Submodules\n";
    ModuleOp mod = f->getParentOfType<ModuleOp>();
    if (!mod)
      return f.emitError("verilog emitter: missing parent module for instance resolution");
    for (Operation *op : instOps) {
      auto inst = dyn_cast<pyc::InstanceOp>(op);
      if (!inst)
        continue;

      auto calleeAttr = op->getAttrOfType<FlatSymbolRefAttr>("callee");
      if (!calleeAttr)
        return inst.emitError("missing required FlatSymbolRefAttr `callee`");
      auto callee = mod.lookupSymbol<func::FuncOp>(calleeAttr.getValue());
      if (!callee)
        return inst.emitError("callee symbol not found: ") << calleeAttr.getValue();

      std::vector<std::string> inPorts;
      std::vector<std::string> outPorts;
      computeUniquePortNames(callee, inPorts, outPorts);
      if (inPorts.size() != inst.getNumOperands())
        return inst.emitError("operand count does not match callee signature");
      if (outPorts.size() != inst.getNumResults())
        return inst.emitError("result count does not match callee signature");

      std::string instName = "inst";
      if (auto nameAttr = op->getAttrOfType<StringAttr>("name"))
        instName = sanitizeId(nameAttr.getValue());
      if (auto shortAttr = op->getAttrOfType<StringAttr>("short_name"))
        instName = sanitizeId(shortAttr.getValue());
      instName = nt.unique(instName);

      std::vector<std::string> inConn;
      std::vector<std::string> outConn;
      inConn.reserve(inPorts.size());
      outConn.reserve(outPorts.size());

      for (unsigned i = 0; i < inPorts.size(); ++i)
        inConn.push_back(nt.get(inst.getOperand(i)));
      for (unsigned i = 0; i < outPorts.size(); ++i)
        outConn.push_back(nt.get(inst.getResult(i)));

      os << callee.getSymName() << " " << instName << " (\n";
      unsigned totalPorts = static_cast<unsigned>(inPorts.size() + outPorts.size());
      unsigned emitted = 0;

      for (unsigned i = 0; i < inPorts.size(); ++i) {
        os << "  ." << inPorts[i] << "(" << inConn[i] << ")";
        emitted++;
        os << ((emitted == totalPorts) ? "\n" : ",\n");
      }
      for (unsigned i = 0; i < outPorts.size(); ++i) {
        os << "  ." << outPorts[i] << "(" << outConn[i] << ")";
        emitted++;
        os << ((emitted == totalPorts) ? "\n" : ",\n");
      }
      os << ");\n";
    }
    os << "\n";
  }

  if (!seqInstOps.empty()) {
    os << "// --- Sequential primitives\n";
    for (Operation *op : seqInstOps) {
      if (auto r = dyn_cast<pyc::RegOp>(op)) {
        auto qTy = r.getQ().getType();
        auto width = leafWidth(qTy);
        if (!width)
          return r.emitError("verilog emitter only supports integer reg data type");

        auto emitReg = [&](llvm::StringRef suffix, llvm::StringRef instanceSuffix) {
          std::string initParam;
          bool usePortInit = true;
          if (auto *initDef = r.getInit().getDefiningOp()) {
            if (auto constOp = dyn_cast<pyc::ConstantOp>(initDef)) {
              initParam = ", .INIT(" + vLiteral(constOp.getValueAttr(),
                                                 constOp.getType()) + ")";
              usePortInit = false;
            }
          }
          if (usePortInit) {
            // non-constant init: use the init port path
            os << "pyc_reg #(.WIDTH(" << *width << "), .USE_INIT_PORT(1)) "
               << nt.get(r.getQ()) << "_inst" << instanceSuffix << " (\n";
          } else {
            // constant init: use the INIT parameter path (no init port)
            os << "pyc_reg #(.WIDTH(" << *width << ")" << initParam << ") "
               << nt.get(r.getQ()) << "_inst" << instanceSuffix << " (\n";
          }
          os << "  .clk(" << nt.get(r.getClk()) << "),\n";
          os << "  .rst(" << nt.get(r.getRst()) << "),\n";
          os << "  .en(" << nt.get(r.getEn()) << "),\n";
          os << "  .d(" << nt.get(r.getNext()) << suffix << "),\n";
          os << "  .init(" << nt.get(r.getInit()) << suffix << "),\n";
          os << "  .q(" << nt.get(r.getQ()) << suffix << ")\n";
          os << ");\n";
        };

        emitReg("", "");
        continue;
      }
      if (auto fifo = dyn_cast<pyc::FifoOp>(op)) {
        auto inDataTy = dyn_cast<IntegerType>(fifo.getInData().getType());
        if (!inDataTy)
          return fifo.emitError("verilog emitter only supports integer fifo data type");
        auto depth = fifo->getAttrOfType<IntegerAttr>("depth").getValue().getZExtValue();
        os << "pyc_fifo #(.WIDTH(" << inDataTy.getWidth() << "), .DEPTH(" << depth << ")) "
           << nt.get(fifo.getInReady()) << "_inst (\n";
        os << "  .clk(" << nt.get(fifo.getClk()) << "),\n";
        os << "  .rst(" << nt.get(fifo.getRst()) << "),\n";
        os << "  .in_valid(" << nt.get(fifo.getInValid()) << "),\n";
        os << "  .in_ready(" << nt.get(fifo.getInReady()) << "),\n";
        os << "  .in_data(" << nt.get(fifo.getInData()) << "),\n";
        os << "  .out_valid(" << nt.get(fifo.getOutValid()) << "),\n";
        os << "  .out_ready(" << nt.get(fifo.getOutReady()) << "),\n";
        os << "  .out_data(" << nt.get(fifo.getOutData()) << ")\n";
        os << ");\n";
        continue;
      }
      if (auto mem = dyn_cast<pyc::ByteMemOp>(op)) {
        auto addrTy = dyn_cast<IntegerType>(mem.getRaddr().getType());
        auto dataTy = dyn_cast<IntegerType>(mem.getRdata().getType());
        if (!addrTy || !dataTy)
          return mem.emitError("verilog emitter only supports integer byte_mem types");

        auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
        if (!depthAttr)
          return mem.emitError("missing integer attribute `depth`");
        auto depth = depthAttr.getValue().getZExtValue();

        std::string inst = nt.get(mem.getRdata()) + "_inst";
        if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
          inst = sanitizeId(nameAttr.getValue());

        os << "pyc_byte_mem #(.ADDR_WIDTH(" << addrTy.getWidth() << "), .DATA_WIDTH(" << dataTy.getWidth() << "), .DEPTH("
           << depth << ")) " << inst << " (\n";
        os << "  .clk(" << nt.get(mem.getClk()) << "),\n";
        os << "  .raddr(" << nt.get(mem.getRaddr()) << "),\n";
        os << "  .rdata(" << nt.get(mem.getRdata()) << "),\n";
        os << "  .wvalid(" << nt.get(mem.getWvalid()) << "),\n";
        os << "  .waddr(" << nt.get(mem.getWaddr()) << "),\n";
        os << "  .wdata(" << nt.get(mem.getWdata()) << "),\n";
        os << "  .wstrb(" << nt.get(mem.getWstrb()) << ")\n";
        os << ");\n";
        continue;
      }
      if (auto mem = dyn_cast<pyc::SyncMemNMOp>(op)) {
        auto addrTy = dyn_cast<IntegerType>(mem.getRaddrs()[0].getType());
        auto dataTy = dyn_cast<IntegerType>(mem.getRdatas()[0].getType());
        if (!addrTy || !dataTy)
          return mem.emitError("verilog emitter only supports integer sync_mem_nm types");
        auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
        if (!depthAttr)
          return mem.emitError("missing integer attribute `depth`");
        auto depth = depthAttr.getValue().getZExtValue();
        unsigned rp = mem.getRens().size();
        unsigned wp = mem.getWvalids().size();
        std::string inst = nt.get(mem.getRdatas()[0]);
        if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
          inst = sanitizeId(nameAttr.getValue());
        // Pack port groups LSB-first (port k = bits [k*W +: W]); the
        // concatenation lists the high index first.
        auto pack = [&](auto range) {
          std::string cat;
          for (auto v : llvm::reverse(range)) {
            if (!cat.empty())
              cat += ", ";
            cat += nt.get(v);
          }
          return "{" + cat + "}";
        };
        os << "pyc_sync_mem_nm #(.ADDR_WIDTH(" << addrTy.getWidth()
           << "), .DATA_WIDTH(" << dataTy.getWidth() << "), .DEPTH(" << depth
           << "), .R_PORTS(" << rp << "), .W_PORTS(" << wp << "), .READ_MODE("
           << (mem.getReadMode() == "old" ? 1 : 0) << ")) " << inst << " (\n";
        os << "  .clk(" << nt.get(mem.getClk()) << "),\n";
        os << "  .rens(" << pack(mem.getRens()) << "),\n";
        os << "  .raddrs(" << pack(mem.getRaddrs()) << "),\n";
        os << "  .rdatas(" << pack(mem.getRdatas()) << "),\n";
        os << "  .wvalids(" << pack(mem.getWvalids()) << "),\n";
        os << "  .waddrs(" << pack(mem.getWaddrs()) << "),\n";
        os << "  .wdatas(" << pack(mem.getWdatas()) << "),\n";
        os << "  .wstrbs(" << pack(mem.getWstrbs()) << ")\n";
        os << ");\n";
        continue;
      }
      if (auto fifo = dyn_cast<pyc::AsyncFifoOp>(op)) {
        auto inDataTy = dyn_cast<IntegerType>(fifo.getInData().getType());
        if (!inDataTy)
          return fifo.emitError("verilog emitter only supports integer async_fifo data type");
        auto depth = fifo->getAttrOfType<IntegerAttr>("depth").getValue().getZExtValue();
        os << "pyc_async_fifo #(.WIDTH(" << inDataTy.getWidth() << "), .DEPTH(" << depth << ")) "
           << nt.get(fifo.getInReady()) << "_inst (\n";
        os << "  .in_clk(" << nt.get(fifo.getInClk()) << "),\n";
        os << "  .in_rst(" << nt.get(fifo.getInRst()) << "),\n";
        os << "  .in_valid(" << nt.get(fifo.getInValid()) << "),\n";
        os << "  .in_ready(" << nt.get(fifo.getInReady()) << "),\n";
        os << "  .in_data(" << nt.get(fifo.getInData()) << "),\n";
        os << "  .out_clk(" << nt.get(fifo.getOutClk()) << "),\n";
        os << "  .out_rst(" << nt.get(fifo.getOutRst()) << "),\n";
        os << "  .out_valid(" << nt.get(fifo.getOutValid()) << "),\n";
        os << "  .out_ready(" << nt.get(fifo.getOutReady()) << "),\n";
        os << "  .out_data(" << nt.get(fifo.getOutData()) << ")\n";
        os << ");\n";
        continue;
      }
      if (auto s = dyn_cast<pyc::CdcSyncOp>(op)) {
        auto ty = dyn_cast<IntegerType>(s.getIn().getType());
        if (!ty)
          return s.emitError("verilog emitter only supports integer cdc_sync types");
        std::uint64_t stages = 2;
        if (auto st = s->getAttrOfType<IntegerAttr>("stages"))
          stages = st.getValue().getZExtValue();
        os << "pyc_cdc_sync #(.WIDTH(" << ty.getWidth() << "), .STAGES(" << stages << ")) " << nt.get(s.getOut())
           << "_inst (\n";
        os << "  .clk(" << nt.get(s.getClk()) << "),\n";
        os << "  .rst(" << nt.get(s.getRst()) << "),\n";
        os << "  .in(" << nt.get(s.getIn()) << "),\n";
        os << "  .out(" << nt.get(s.getOut()) << ")\n";
        os << ");\n";
        continue;
      }
      return op->emitError("internal error: missing verilog sequential primitive emission handler");
    }
    os << "\n";
  }

  // Connect outputs from return.
  auto ret = dyn_cast_or_null<func::ReturnOp>(f.getBody().front().getTerminator());
  if (!ret)
    return f.emitError("missing return");
  for (auto [i, v] : llvm::enumerate(ret.getOperands())) {
    if (nt.get(v) == outNames[i])
      continue;
    emitConnectAssign(outNames[i], nt.get(v), f.getResultTypes()[i], os);
  }

  os << "\nendmodule\n\n";
  return success();
}

} // namespace

LogicalResult emitVerilog(ModuleOp module, llvm::raw_ostream &os, const VerilogEmitterOptions &opts) {
  if (opts.targetFpga) {
    os << "`define PYC_TARGET_FPGA 1\n\n";
  }
  if (opts.includePrimitives) {
    os << "`include \"pyc_reg.v\"\n";
    os << "`include \"pyc_fifo.v\"\n\n";
    os << "`include \"pyc_byte_mem.v\"\n\n";
    os << "`include \"pyc_sync_mem_nm.v\"\n";
    os << "`include \"pyc_async_fifo.v\"\n";
    os << "`include \"pyc_cdc_sync.v\"\n\n";
  }

  for (auto f : module.getOps<func::FuncOp>()) {
    if (failed(emitFunc(f, os, opts)))
      return failure();
  }
  return success();
}

LogicalResult emitVerilogFunc(ModuleOp module, func::FuncOp f, llvm::raw_ostream &os, const VerilogEmitterOptions &opts) {
  (void)module;
  return emitFunc(f, os, opts);
}

} // namespace pyc
