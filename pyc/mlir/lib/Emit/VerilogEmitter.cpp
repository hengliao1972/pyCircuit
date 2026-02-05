#include "pyc/Emit/VerilogEmitter.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Operation.h"
#include "mlir/IR/Value.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SmallSet.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/ADT/StringRef.h"

#include <algorithm>
#include <vector>

using namespace mlir;

namespace pyc {
namespace {

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

static std::string vLiteral(IntegerAttr a, Type dstTy) {
  auto intTy = dyn_cast<IntegerType>(dstTy);
  if (!intTy)
    return "0";
  unsigned w = intTy.getWidth();
  return std::to_string(w) + "'d" + std::to_string(a.getValue().getZExtValue());
}

static std::string vZero(Type dstTy) {
  auto intTy = dyn_cast<IntegerType>(dstTy);
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

    if (auto c = dyn_cast<pyc::ConstantOp>(op)) {
      os << "assign " << nt.get(c.getResult()) << " = " << vLiteral(c.getValueAttr(), c.getType()) << ";\n";
      continue;
    }
    if (auto a = dyn_cast<pyc::AliasOp>(op)) {
      os << "assign " << nt.get(a.getResult()) << " = " << nt.get(a.getIn()) << ";\n";
      continue;
    }
    if (auto a = dyn_cast<pyc::AddOp>(op)) {
      os << "assign " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " + " << nt.get(a.getRhs())
         << ");\n";
      continue;
    }
    if (auto s = dyn_cast<pyc::SubOp>(op)) {
      os << "assign " << nt.get(s.getResult()) << " = (" << nt.get(s.getLhs()) << " - " << nt.get(s.getRhs())
         << ");\n";
      continue;
    }
    if (auto m = dyn_cast<pyc::MulOp>(op)) {
      os << "assign " << nt.get(m.getResult()) << " = (" << nt.get(m.getLhs()) << " * " << nt.get(m.getRhs())
         << ");\n";
      continue;
    }
    if (auto d = dyn_cast<pyc::UdivOp>(op)) {
      os << "assign " << nt.get(d.getResult()) << " = (" << nt.get(d.getRhs()) << " == " << vZero(d.getRhs().getType())
         << " ? " << vZero(d.getResult().getType()) << " : (" << nt.get(d.getLhs()) << " / " << nt.get(d.getRhs())
         << "));\n";
      continue;
    }
    if (auto r = dyn_cast<pyc::UremOp>(op)) {
      os << "assign " << nt.get(r.getResult()) << " = (" << nt.get(r.getRhs()) << " == " << vZero(r.getRhs().getType())
         << " ? " << vZero(r.getResult().getType()) << " : (" << nt.get(r.getLhs()) << " % " << nt.get(r.getRhs())
         << "));\n";
      continue;
    }
    if (auto d = dyn_cast<pyc::SdivOp>(op)) {
      os << "assign " << nt.get(d.getResult()) << " = (" << nt.get(d.getRhs()) << " == " << vZero(d.getRhs().getType())
         << " ? " << vZero(d.getResult().getType()) << " : ($signed(" << nt.get(d.getLhs()) << ") / $signed("
         << nt.get(d.getRhs()) << ")));\n";
      continue;
    }
    if (auto r = dyn_cast<pyc::SremOp>(op)) {
      os << "assign " << nt.get(r.getResult()) << " = (" << nt.get(r.getRhs()) << " == " << vZero(r.getRhs().getType())
         << " ? " << vZero(r.getResult().getType()) << " : ($signed(" << nt.get(r.getLhs()) << ") % $signed("
         << nt.get(r.getRhs()) << ")));\n";
      continue;
    }
    if (auto m = dyn_cast<pyc::MuxOp>(op)) {
      os << "assign " << nt.get(m.getResult()) << " = (" << nt.get(m.getSel()) << " ? " << nt.get(m.getA())
         << " : " << nt.get(m.getB()) << ");\n";
      continue;
    }
    if (auto s = dyn_cast<arith::SelectOp>(op)) {
      if (!s.getCondition().getType().isInteger(1))
        return s.emitError("verilog emitter only supports arith.select with i1 condition");
      os << "assign " << nt.get(s.getResult()) << " = (" << nt.get(s.getCondition()) << " ? "
         << nt.get(s.getTrueValue()) << " : " << nt.get(s.getFalseValue()) << ");\n";
      continue;
    }
    if (auto a = dyn_cast<pyc::AndOp>(op)) {
      os << "assign " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " & " << nt.get(a.getRhs())
         << ");\n";
      continue;
    }
    if (auto o = dyn_cast<pyc::OrOp>(op)) {
      os << "assign " << nt.get(o.getResult()) << " = (" << nt.get(o.getLhs()) << " | " << nt.get(o.getRhs())
         << ");\n";
      continue;
    }
    if (auto x = dyn_cast<pyc::XorOp>(op)) {
      os << "assign " << nt.get(x.getResult()) << " = (" << nt.get(x.getLhs()) << " ^ " << nt.get(x.getRhs())
         << ");\n";
      continue;
    }
    if (auto n = dyn_cast<pyc::NotOp>(op)) {
      os << "assign " << nt.get(n.getResult()) << " = (~" << nt.get(n.getIn()) << ");\n";
      continue;
    }
    if (auto e = dyn_cast<pyc::EqOp>(op)) {
      os << "assign " << nt.get(e.getResult()) << " = (" << nt.get(e.getLhs()) << " == " << nt.get(e.getRhs())
         << ");\n";
      continue;
    }
    if (auto u = dyn_cast<pyc::UltOp>(op)) {
      os << "assign " << nt.get(u.getResult()) << " = (" << nt.get(u.getLhs()) << " < " << nt.get(u.getRhs())
         << ");\n";
      continue;
    }
    if (auto s = dyn_cast<pyc::SltOp>(op)) {
      os << "assign " << nt.get(s.getResult()) << " = ($signed(" << nt.get(s.getLhs()) << ") < $signed("
         << nt.get(s.getRhs()) << "));\n";
      continue;
    }
    if (auto t = dyn_cast<pyc::TruncOp>(op)) {
      auto outTy = dyn_cast<IntegerType>(t.getResult().getType());
      if (!outTy)
        return t.emitError("verilog emitter only supports integer trunc");
      unsigned w = outTy.getWidth();
      if (w == 1)
        os << "assign " << nt.get(t.getResult()) << " = " << nt.get(t.getIn()) << "[0];\n";
      else
        os << "assign " << nt.get(t.getResult()) << " = " << nt.get(t.getIn()) << "[" << (w - 1) << ":0];\n";
      continue;
    }
    if (auto z = dyn_cast<pyc::ZextOp>(op)) {
      auto inTy = dyn_cast<IntegerType>(z.getIn().getType());
      auto outTy = dyn_cast<IntegerType>(z.getResult().getType());
      if (!inTy || !outTy)
        return z.emitError("verilog emitter only supports integer zext");
      unsigned iw = inTy.getWidth();
      unsigned ow = outTy.getWidth();
      if (ow == iw) {
        os << "assign " << nt.get(z.getResult()) << " = " << nt.get(z.getIn()) << ";\n";
      } else {
        os << "assign " << nt.get(z.getResult()) << " = {{" << (ow - iw) << "{1'b0}}, " << nt.get(z.getIn())
           << "};\n";
      }
      continue;
    }
    if (auto s = dyn_cast<pyc::SextOp>(op)) {
      auto inTy = dyn_cast<IntegerType>(s.getIn().getType());
      auto outTy = dyn_cast<IntegerType>(s.getResult().getType());
      if (!inTy || !outTy)
        return s.emitError("verilog emitter only supports integer sext");
      unsigned iw = inTy.getWidth();
      unsigned ow = outTy.getWidth();
      if (ow == iw) {
        os << "assign " << nt.get(s.getResult()) << " = " << nt.get(s.getIn()) << ";\n";
      } else {
        os << "assign " << nt.get(s.getResult()) << " = {{" << (ow - iw) << "{" << nt.get(s.getIn()) << "["
           << (iw - 1) << "]}}, " << nt.get(s.getIn()) << "};\n";
      }
      continue;
    }
    if (auto ex = dyn_cast<pyc::ExtractOp>(op)) {
      auto inTy = dyn_cast<IntegerType>(ex.getIn().getType());
      auto outTy = dyn_cast<IntegerType>(ex.getResult().getType());
      if (!inTy || !outTy)
        return ex.emitError("verilog emitter only supports integer extract");
      unsigned ow = outTy.getWidth();
      std::int64_t lsb = ex.getLsbAttr().getInt();
      if (ow == 1) {
        os << "assign " << nt.get(ex.getResult()) << " = " << nt.get(ex.getIn()) << "[" << lsb << "];\n";
      } else {
        os << "assign " << nt.get(ex.getResult()) << " = " << nt.get(ex.getIn()) << "[" << (lsb + ow - 1) << ":"
           << lsb << "];\n";
      }
      continue;
    }
    if (auto sh = dyn_cast<pyc::ShliOp>(op)) {
      os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " << "
         << sh.getAmountAttr().getInt() << ");\n";
      continue;
    }
    if (auto sh = dyn_cast<pyc::LshriOp>(op)) {
      os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " >> "
         << sh.getAmountAttr().getInt() << ");\n";
      continue;
    }
    if (auto sh = dyn_cast<pyc::AshriOp>(op)) {
      os << "assign " << nt.get(sh.getResult()) << " = ($signed(" << nt.get(sh.getIn()) << ") >>> "
         << sh.getAmountAttr().getInt() << ");\n";
      continue;
    }
    if (auto c = dyn_cast<pyc::ConcatOp>(op)) {
      os << "assign " << nt.get(c.getResult()) << " = {";
      for (auto [i, v] : llvm::enumerate(c.getInputs())) {
        if (i)
          os << ", ";
        os << nt.get(v);
      }
      os << "};\n";
      continue;
    }

    return op.emitError("unsupported op inside pyc.comb for verilog emission");
  }

  auto yield = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
  if (!yield)
    return comb.emitError("pyc.comb must terminate with pyc.yield");
  if (yield.getNumOperands() != comb.getNumResults())
    return comb.emitError("pyc.yield operand count must match pyc.comb results");

  for (auto [i, v] : llvm::enumerate(yield.getOperands()))
    os << "assign " << nt.get(comb.getResult(i)) << " = " << nt.get(v) << ";\n";

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
  if (auto mem = dyn_cast<pyc::SyncMemOp>(op)) {
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(mem.getRdata());
  }
  if (auto mem = dyn_cast<pyc::SyncMemDPOp>(op)) {
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      return sanitizeId(nameAttr.getValue());
    return nt.get(mem.getRdata0());
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

  os << "// Generated by pyc-compile (pyCircuit)\n";
  os << "// Module: " << f.getSymName() << "\n\n";

  // Module header.
  os << "module " << f.getSymName() << " (\n";
  for (auto [i, arg] : llvm::enumerate(f.getArguments())) {
    std::string portName = nt.unique(getPortName(f, i, /*isResult=*/false));
    std::string range = vRange(arg.getType());
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
    std::string range = vRange(f.getResultTypes()[i]);
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

  // Collect top-level ops for netlist-friendly emission.
  llvm::SmallVector<Operation *> combAssignOps;
  llvm::SmallVector<Operation *> seqInstOps;

  for (Block &b : f.getBody()) {
    for (Operation &op : b) {
      if (isa<func::ReturnOp>(op))
        continue;
      if (isa<pyc::WireOp>(op))
        continue;

      if (isa<pyc::ConstantOp,
              pyc::AliasOp,
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
              pyc::ConcatOp>(op)) {
        combAssignOps.push_back(&op);
        continue;
      }
      if (isa<pyc::RegOp, pyc::FifoOp, pyc::ByteMemOp>(op)) {
        seqInstOps.push_back(&op);
        continue;
      }
      if (isa<pyc::SyncMemOp, pyc::SyncMemDPOp, pyc::AsyncFifoOp, pyc::CdcSyncOp>(op)) {
        seqInstOps.push_back(&op);
        continue;
      }
      return op.emitError("unsupported op for verilog emission: ") << op.getName();
    }
  }

  auto cmp = [&](Operation *a, Operation *b) { return opSortKey(a, nt) < opSortKey(b, nt); };
  std::sort(seqInstOps.begin(), seqInstOps.end(), cmp);
  llvm::SmallVector<Operation *> orderedComb;
  if (!topoSortCombOps(combAssignOps, nt, orderedComb))
    std::sort(combAssignOps.begin(), combAssignOps.end(), cmp);
  else
    combAssignOps.assign(orderedComb.begin(), orderedComb.end());

  if (!combAssignOps.empty()) {
    os << "// --- Combinational (netlist)\n";
    for (Operation *op : combAssignOps) {
      if (auto c = dyn_cast<pyc::ConstantOp>(op)) {
        os << "assign " << nt.get(c.getResult()) << " = " << vLiteral(c.getValueAttr(), c.getType()) << ";\n";
        continue;
      }
      if (auto a = dyn_cast<pyc::AliasOp>(op)) {
        os << "assign " << nt.get(a.getResult()) << " = " << nt.get(a.getIn()) << ";\n";
        continue;
      }
      if (auto a = dyn_cast<pyc::AddOp>(op)) {
        os << "assign " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " + " << nt.get(a.getRhs())
           << ");\n";
        continue;
      }
      if (auto s = dyn_cast<pyc::SubOp>(op)) {
        os << "assign " << nt.get(s.getResult()) << " = (" << nt.get(s.getLhs()) << " - " << nt.get(s.getRhs())
           << ");\n";
        continue;
      }
      if (auto m = dyn_cast<pyc::MulOp>(op)) {
        os << "assign " << nt.get(m.getResult()) << " = (" << nt.get(m.getLhs()) << " * " << nt.get(m.getRhs())
           << ");\n";
        continue;
      }
      if (auto d = dyn_cast<pyc::UdivOp>(op)) {
        os << "assign " << nt.get(d.getResult()) << " = (" << nt.get(d.getRhs()) << " == "
           << vZero(d.getRhs().getType()) << " ? " << vZero(d.getResult().getType()) << " : (" << nt.get(d.getLhs())
           << " / " << nt.get(d.getRhs()) << "));\n";
        continue;
      }
      if (auto r = dyn_cast<pyc::UremOp>(op)) {
        os << "assign " << nt.get(r.getResult()) << " = (" << nt.get(r.getRhs()) << " == "
           << vZero(r.getRhs().getType()) << " ? " << vZero(r.getResult().getType()) << " : (" << nt.get(r.getLhs())
           << " % " << nt.get(r.getRhs()) << "));\n";
        continue;
      }
      if (auto d = dyn_cast<pyc::SdivOp>(op)) {
        os << "assign " << nt.get(d.getResult()) << " = (" << nt.get(d.getRhs()) << " == "
           << vZero(d.getRhs().getType()) << " ? " << vZero(d.getResult().getType()) << " : ($signed("
           << nt.get(d.getLhs()) << ") / $signed(" << nt.get(d.getRhs()) << ")));\n";
        continue;
      }
      if (auto r = dyn_cast<pyc::SremOp>(op)) {
        os << "assign " << nt.get(r.getResult()) << " = (" << nt.get(r.getRhs()) << " == "
           << vZero(r.getRhs().getType()) << " ? " << vZero(r.getResult().getType()) << " : ($signed("
           << nt.get(r.getLhs()) << ") % $signed(" << nt.get(r.getRhs()) << ")));\n";
        continue;
      }
      if (auto m = dyn_cast<pyc::MuxOp>(op)) {
        os << "assign " << nt.get(m.getResult()) << " = (" << nt.get(m.getSel()) << " ? " << nt.get(m.getA())
           << " : " << nt.get(m.getB()) << ");\n";
        continue;
      }
      if (auto a = dyn_cast<pyc::AndOp>(op)) {
        os << "assign " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " & " << nt.get(a.getRhs())
           << ");\n";
        continue;
      }
      if (auto o = dyn_cast<pyc::OrOp>(op)) {
        os << "assign " << nt.get(o.getResult()) << " = (" << nt.get(o.getLhs()) << " | " << nt.get(o.getRhs())
           << ");\n";
        continue;
      }
      if (auto x = dyn_cast<pyc::XorOp>(op)) {
        os << "assign " << nt.get(x.getResult()) << " = (" << nt.get(x.getLhs()) << " ^ " << nt.get(x.getRhs())
           << ");\n";
        continue;
      }
      if (auto n = dyn_cast<pyc::NotOp>(op)) {
        os << "assign " << nt.get(n.getResult()) << " = (~" << nt.get(n.getIn()) << ");\n";
        continue;
      }
      if (auto a = dyn_cast<pyc::AssignOp>(op)) {
        os << "assign " << nt.get(a.getDst()) << " = " << nt.get(a.getSrc()) << ";\n";
        continue;
      }
      if (auto comb = dyn_cast<pyc::CombOp>(op)) {
        if (failed(emitComb(comb, os, nt)))
          return failure();
        continue;
      }
      if (auto s = dyn_cast<arith::SelectOp>(op)) {
        if (!s.getCondition().getType().isInteger(1))
          return s.emitError("verilog emitter only supports arith.select with i1 condition");
        os << "assign " << nt.get(s.getResult()) << " = (" << nt.get(s.getCondition()) << " ? "
           << nt.get(s.getTrueValue()) << " : " << nt.get(s.getFalseValue()) << ");\n";
        continue;
      }
      if (auto e = dyn_cast<pyc::EqOp>(op)) {
        os << "assign " << nt.get(e.getResult()) << " = (" << nt.get(e.getLhs()) << " == " << nt.get(e.getRhs())
           << ");\n";
        continue;
      }
      if (auto u = dyn_cast<pyc::UltOp>(op)) {
        os << "assign " << nt.get(u.getResult()) << " = (" << nt.get(u.getLhs()) << " < " << nt.get(u.getRhs())
           << ");\n";
        continue;
      }
      if (auto s = dyn_cast<pyc::SltOp>(op)) {
        os << "assign " << nt.get(s.getResult()) << " = ($signed(" << nt.get(s.getLhs()) << ") < $signed("
           << nt.get(s.getRhs()) << "));\n";
        continue;
      }
      if (auto t = dyn_cast<pyc::TruncOp>(op)) {
        auto outTy = dyn_cast<IntegerType>(t.getResult().getType());
        if (!outTy)
          return t.emitError("verilog emitter only supports integer trunc");
        unsigned w = outTy.getWidth();
        if (w == 1)
          os << "assign " << nt.get(t.getResult()) << " = " << nt.get(t.getIn()) << "[0];\n";
        else
          os << "assign " << nt.get(t.getResult()) << " = " << nt.get(t.getIn()) << "[" << (w - 1) << ":0];\n";
        continue;
      }
      if (auto z = dyn_cast<pyc::ZextOp>(op)) {
        auto inTy = dyn_cast<IntegerType>(z.getIn().getType());
        auto outTy = dyn_cast<IntegerType>(z.getResult().getType());
        if (!inTy || !outTy)
          return z.emitError("verilog emitter only supports integer zext");
        unsigned iw = inTy.getWidth();
        unsigned ow = outTy.getWidth();
        if (ow == iw) {
          os << "assign " << nt.get(z.getResult()) << " = " << nt.get(z.getIn()) << ";\n";
        } else {
          os << "assign " << nt.get(z.getResult()) << " = {{" << (ow - iw) << "{1'b0}}, " << nt.get(z.getIn())
             << "};\n";
        }
        continue;
      }
      if (auto s = dyn_cast<pyc::SextOp>(op)) {
        auto inTy = dyn_cast<IntegerType>(s.getIn().getType());
        auto outTy = dyn_cast<IntegerType>(s.getResult().getType());
        if (!inTy || !outTy)
          return s.emitError("verilog emitter only supports integer sext");
        unsigned iw = inTy.getWidth();
        unsigned ow = outTy.getWidth();
        if (ow == iw) {
          os << "assign " << nt.get(s.getResult()) << " = " << nt.get(s.getIn()) << ";\n";
        } else {
          os << "assign " << nt.get(s.getResult()) << " = {{" << (ow - iw) << "{" << nt.get(s.getIn()) << "["
             << (iw - 1) << "]}}, " << nt.get(s.getIn()) << "};\n";
        }
        continue;
      }
      if (auto ex = dyn_cast<pyc::ExtractOp>(op)) {
        auto inTy = dyn_cast<IntegerType>(ex.getIn().getType());
        auto outTy = dyn_cast<IntegerType>(ex.getResult().getType());
        if (!inTy || !outTy)
          return ex.emitError("verilog emitter only supports integer extract");
        unsigned ow = outTy.getWidth();
        std::int64_t lsb = ex.getLsbAttr().getInt();
        if (ow == 1) {
          os << "assign " << nt.get(ex.getResult()) << " = " << nt.get(ex.getIn()) << "[" << lsb << "];\n";
        } else {
          os << "assign " << nt.get(ex.getResult()) << " = " << nt.get(ex.getIn()) << "[" << (lsb + ow - 1) << ":"
             << lsb << "];\n";
        }
        continue;
      }
      if (auto sh = dyn_cast<pyc::ShliOp>(op)) {
        os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " << "
           << sh.getAmountAttr().getInt() << ");\n";
        continue;
      }
      if (auto sh = dyn_cast<pyc::LshriOp>(op)) {
        os << "assign " << nt.get(sh.getResult()) << " = (" << nt.get(sh.getIn()) << " >> "
           << sh.getAmountAttr().getInt() << ");\n";
        continue;
      }
      if (auto sh = dyn_cast<pyc::AshriOp>(op)) {
        os << "assign " << nt.get(sh.getResult()) << " = ($signed(" << nt.get(sh.getIn()) << ") >>> "
           << sh.getAmountAttr().getInt() << ");\n";
        continue;
      }
      if (auto c = dyn_cast<pyc::ConcatOp>(op)) {
        os << "assign " << nt.get(c.getResult()) << " = {";
        for (auto [i, v] : llvm::enumerate(c.getInputs())) {
          if (i)
            os << ", ";
          os << nt.get(v);
        }
        os << "};\n";
        continue;
      }
      return op->emitError("internal error: missing verilog emission handler");
    }
    os << "\n";
  }

  if (!seqInstOps.empty()) {
    os << "// --- Sequential primitives\n";
    for (Operation *op : seqInstOps) {
      if (auto r = dyn_cast<pyc::RegOp>(op)) {
        auto qTy = dyn_cast<IntegerType>(r.getQ().getType());
        if (!qTy)
          return r.emitError("verilog emitter only supports integer reg data type");
        os << "pyc_reg #(.WIDTH(" << qTy.getWidth() << ")) " << nt.get(r.getQ()) << "_inst (\n";
        os << "  .clk(" << nt.get(r.getClk()) << "),\n";
        os << "  .rst(" << nt.get(r.getRst()) << "),\n";
        os << "  .en(" << nt.get(r.getEn()) << "),\n";
        os << "  .d(" << nt.get(r.getNext()) << "),\n";
        os << "  .init(" << nt.get(r.getInit()) << "),\n";
        os << "  .q(" << nt.get(r.getQ()) << ")\n";
        os << ");\n";
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
        os << "  .rst(" << nt.get(mem.getRst()) << "),\n";
        os << "  .raddr(" << nt.get(mem.getRaddr()) << "),\n";
        os << "  .rdata(" << nt.get(mem.getRdata()) << "),\n";
        os << "  .wvalid(" << nt.get(mem.getWvalid()) << "),\n";
        os << "  .waddr(" << nt.get(mem.getWaddr()) << "),\n";
        os << "  .wdata(" << nt.get(mem.getWdata()) << "),\n";
        os << "  .wstrb(" << nt.get(mem.getWstrb()) << ")\n";
        os << ");\n";
        continue;
      }
      if (auto mem = dyn_cast<pyc::SyncMemOp>(op)) {
        auto addrTy = dyn_cast<IntegerType>(mem.getRaddr().getType());
        auto dataTy = dyn_cast<IntegerType>(mem.getRdata().getType());
        if (!addrTy || !dataTy)
          return mem.emitError("verilog emitter only supports integer sync_mem types");

        auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
        if (!depthAttr)
          return mem.emitError("missing integer attribute `depth`");
        auto depth = depthAttr.getValue().getZExtValue();

        std::string inst = nt.get(mem.getRdata()) + "_inst";
        if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
          inst = sanitizeId(nameAttr.getValue());

        os << "pyc_sync_mem #(.ADDR_WIDTH(" << addrTy.getWidth() << "), .DATA_WIDTH(" << dataTy.getWidth()
           << "), .DEPTH(" << depth << ")) " << inst << " (\n";
        os << "  .clk(" << nt.get(mem.getClk()) << "),\n";
        os << "  .rst(" << nt.get(mem.getRst()) << "),\n";
        os << "  .ren(" << nt.get(mem.getRen()) << "),\n";
        os << "  .raddr(" << nt.get(mem.getRaddr()) << "),\n";
        os << "  .rdata(" << nt.get(mem.getRdata()) << "),\n";
        os << "  .wvalid(" << nt.get(mem.getWvalid()) << "),\n";
        os << "  .waddr(" << nt.get(mem.getWaddr()) << "),\n";
        os << "  .wdata(" << nt.get(mem.getWdata()) << "),\n";
        os << "  .wstrb(" << nt.get(mem.getWstrb()) << ")\n";
        os << ");\n";
        continue;
      }
      if (auto mem = dyn_cast<pyc::SyncMemDPOp>(op)) {
        auto addrTy = dyn_cast<IntegerType>(mem.getRaddr0().getType());
        auto dataTy = dyn_cast<IntegerType>(mem.getRdata0().getType());
        if (!addrTy || !dataTy)
          return mem.emitError("verilog emitter only supports integer sync_mem_dp types");

        auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
        if (!depthAttr)
          return mem.emitError("missing integer attribute `depth`");
        auto depth = depthAttr.getValue().getZExtValue();

        std::string inst = nt.get(mem.getRdata0()) + "_inst";
        if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
          inst = sanitizeId(nameAttr.getValue());

        os << "pyc_sync_mem_dp #(.ADDR_WIDTH(" << addrTy.getWidth() << "), .DATA_WIDTH(" << dataTy.getWidth()
           << "), .DEPTH(" << depth << ")) " << inst << " (\n";
        os << "  .clk(" << nt.get(mem.getClk()) << "),\n";
        os << "  .rst(" << nt.get(mem.getRst()) << "),\n";
        os << "  .ren0(" << nt.get(mem.getRen0()) << "),\n";
        os << "  .raddr0(" << nt.get(mem.getRaddr0()) << "),\n";
        os << "  .rdata0(" << nt.get(mem.getRdata0()) << "),\n";
        os << "  .ren1(" << nt.get(mem.getRen1()) << "),\n";
        os << "  .raddr1(" << nt.get(mem.getRaddr1()) << "),\n";
        os << "  .rdata1(" << nt.get(mem.getRdata1()) << "),\n";
        os << "  .wvalid(" << nt.get(mem.getWvalid()) << "),\n";
        os << "  .waddr(" << nt.get(mem.getWaddr()) << "),\n";
        os << "  .wdata(" << nt.get(mem.getWdata()) << "),\n";
        os << "  .wstrb(" << nt.get(mem.getWstrb()) << ")\n";
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
    os << "assign " << outNames[i] << " = " << nt.get(v) << ";\n";
  }

  os << "\nendmodule\n\n";
  return success();
}

} // namespace

LogicalResult emitVerilog(ModuleOp module, llvm::raw_ostream &os, const VerilogEmitterOptions &opts) {
  if (opts.includePrimitives) {
    os << "`include \"pyc_reg.v\"\n";
    os << "`include \"pyc_fifo.v\"\n\n";
    os << "`include \"pyc_byte_mem.v\"\n\n";
    os << "`include \"pyc_sync_mem.v\"\n";
    os << "`include \"pyc_sync_mem_dp.v\"\n";
    os << "`include \"pyc_async_fifo.v\"\n";
    os << "`include \"pyc_cdc_sync.v\"\n\n";
  }

  for (auto f : module.getOps<func::FuncOp>()) {
    if (failed(emitFunc(f, os, opts)))
      return failure();
  }
  return success();
}

} // namespace pyc
