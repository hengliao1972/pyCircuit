#include "pyc/Emit/CppEmitter.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Types.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/SmallSet.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cstdint>
#include <vector>

using namespace mlir;

namespace pyc {
namespace {

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

static std::string cppType(Type ty) {
  if (isa<pyc::ClockType>(ty) || isa<pyc::ResetType>(ty))
    return "pyc::cpp::Wire<1>";
  auto intTy = dyn_cast<IntegerType>(ty);
  if (!intTy)
    return "pyc::cpp::Wire<1>";
  return "pyc::cpp::Wire<" + std::to_string(intTy.getWidth()) + ">";
}

static unsigned bitWidth(Type ty) {
  if (isa<pyc::ClockType>(ty) || isa<pyc::ResetType>(ty))
    return 1;
  auto intTy = dyn_cast<IntegerType>(ty);
  return intTy ? intTy.getWidth() : 0;
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

static LogicalResult emitCombAssign(Operation &op, llvm::raw_ostream &os, NameTable &nt) {
  if (auto c = dyn_cast<pyc::ConstantOp>(op)) {
    unsigned w = bitWidth(c.getType());
    if (w == 0 || w > 64)
      return c.emitError("C++ emitter only supports widths 1..64 for constants in the prototype");
    os << "    " << nt.get(c.getResult()) << " = pyc::cpp::Wire<" << w << ">("
       << c.getValueAttr().getValue().getZExtValue() << "ull);\n";
    return success();
  }
  if (auto a = dyn_cast<pyc::AliasOp>(op)) {
    os << "    " << nt.get(a.getResult()) << " = " << nt.get(a.getIn()) << ";\n";
    return success();
  }
  if (auto a = dyn_cast<pyc::AddOp>(op)) {
    os << "    " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " + " << nt.get(a.getRhs()) << ");\n";
    return success();
  }
  if (auto s = dyn_cast<pyc::SubOp>(op)) {
    os << "    " << nt.get(s.getResult()) << " = (" << nt.get(s.getLhs()) << " - " << nt.get(s.getRhs()) << ");\n";
    return success();
  }
  if (auto m = dyn_cast<pyc::MulOp>(op)) {
    os << "    " << nt.get(m.getResult()) << " = (" << nt.get(m.getLhs()) << " * " << nt.get(m.getRhs()) << ");\n";
    return success();
  }
  if (auto d = dyn_cast<pyc::UdivOp>(op)) {
    unsigned w = bitWidth(d.getResult().getType());
    if (w == 0 || w > 64)
      return d.emitError("C++ emitter only supports widths 1..64 for udiv in the prototype");
    os << "    " << nt.get(d.getResult()) << " = pyc::cpp::udiv<" << w << ">(" << nt.get(d.getLhs()) << ", "
       << nt.get(d.getRhs()) << ");\n";
    return success();
  }
  if (auto r = dyn_cast<pyc::UremOp>(op)) {
    unsigned w = bitWidth(r.getResult().getType());
    if (w == 0 || w > 64)
      return r.emitError("C++ emitter only supports widths 1..64 for urem in the prototype");
    os << "    " << nt.get(r.getResult()) << " = pyc::cpp::urem<" << w << ">(" << nt.get(r.getLhs()) << ", "
       << nt.get(r.getRhs()) << ");\n";
    return success();
  }
  if (auto d = dyn_cast<pyc::SdivOp>(op)) {
    unsigned w = bitWidth(d.getResult().getType());
    if (w == 0 || w > 64)
      return d.emitError("C++ emitter only supports widths 1..64 for sdiv in the prototype");
    os << "    " << nt.get(d.getResult()) << " = pyc::cpp::sdiv<" << w << ">(" << nt.get(d.getLhs()) << ", "
       << nt.get(d.getRhs()) << ");\n";
    return success();
  }
  if (auto r = dyn_cast<pyc::SremOp>(op)) {
    unsigned w = bitWidth(r.getResult().getType());
    if (w == 0 || w > 64)
      return r.emitError("C++ emitter only supports widths 1..64 for srem in the prototype");
    os << "    " << nt.get(r.getResult()) << " = pyc::cpp::srem<" << w << ">(" << nt.get(r.getLhs()) << ", "
       << nt.get(r.getRhs()) << ");\n";
    return success();
  }
  if (auto m = dyn_cast<pyc::MuxOp>(op)) {
    os << "    " << nt.get(m.getResult()) << " = (" << nt.get(m.getSel()) << ".toBool() ? " << nt.get(m.getA())
       << " : " << nt.get(m.getB()) << ");\n";
    return success();
  }
  if (auto s = dyn_cast<arith::SelectOp>(op)) {
    if (!s.getCondition().getType().isInteger(1))
      return s.emitError("C++ emitter only supports arith.select with i1 condition");
    os << "    " << nt.get(s.getResult()) << " = (" << nt.get(s.getCondition()) << ".toBool() ? " << nt.get(s.getTrueValue())
       << " : " << nt.get(s.getFalseValue()) << ");\n";
    return success();
  }
  if (auto a = dyn_cast<pyc::AndOp>(op)) {
    os << "    " << nt.get(a.getResult()) << " = (" << nt.get(a.getLhs()) << " & " << nt.get(a.getRhs()) << ");\n";
    return success();
  }
  if (auto o = dyn_cast<pyc::OrOp>(op)) {
    os << "    " << nt.get(o.getResult()) << " = (" << nt.get(o.getLhs()) << " | " << nt.get(o.getRhs()) << ");\n";
    return success();
  }
  if (auto x = dyn_cast<pyc::XorOp>(op)) {
    os << "    " << nt.get(x.getResult()) << " = (" << nt.get(x.getLhs()) << " ^ " << nt.get(x.getRhs()) << ");\n";
    return success();
  }
  if (auto n = dyn_cast<pyc::NotOp>(op)) {
    os << "    " << nt.get(n.getResult()) << " = (~" << nt.get(n.getIn()) << ");\n";
    return success();
  }
  if (auto e = dyn_cast<pyc::EqOp>(op)) {
    os << "    " << nt.get(e.getResult()) << " = pyc::cpp::Wire<1>((" << nt.get(e.getLhs()) << " == "
       << nt.get(e.getRhs()) << ") ? 1u : 0u);\n";
    return success();
  }
  if (auto u = dyn_cast<pyc::UltOp>(op)) {
    unsigned w = bitWidth(u.getLhs().getType());
    if (w == 0 || w > 64)
      return u.emitError("C++ emitter only supports widths 1..64 for ult in the prototype");
    os << "    " << nt.get(u.getResult()) << " = pyc::cpp::Wire<1>((" << nt.get(u.getLhs()) << ".value() < "
       << nt.get(u.getRhs()) << ".value()) ? 1u : 0u);\n";
    return success();
  }
  if (auto s = dyn_cast<pyc::SltOp>(op)) {
    unsigned w = bitWidth(s.getLhs().getType());
    if (w == 0 || w > 64)
      return s.emitError("C++ emitter only supports widths 1..64 for slt in the prototype");
    os << "    " << nt.get(s.getResult()) << " = pyc::cpp::Wire<1>((pyc::cpp::asSigned<" << w << ">(" << nt.get(s.getLhs())
       << ") < pyc::cpp::asSigned<" << w << ">(" << nt.get(s.getRhs()) << ")) ? 1u : 0u);\n";
    return success();
  }
  if (auto t = dyn_cast<pyc::TruncOp>(op)) {
    unsigned iw = bitWidth(t.getIn().getType());
    unsigned ow = bitWidth(t.getResult().getType());
    if (iw == 0 || ow == 0 || iw > 64 || ow > 64)
      return t.emitError("C++ emitter only supports widths 1..64 for trunc in the prototype");
    os << "    " << nt.get(t.getResult()) << " = pyc::cpp::trunc<" << ow << ", " << iw << ">(" << nt.get(t.getIn())
       << ");\n";
    return success();
  }
  if (auto z = dyn_cast<pyc::ZextOp>(op)) {
    unsigned iw = bitWidth(z.getIn().getType());
    unsigned ow = bitWidth(z.getResult().getType());
    if (iw == 0 || ow == 0 || iw > 64 || ow > 64)
      return z.emitError("C++ emitter only supports widths 1..64 for zext in the prototype");
    os << "    " << nt.get(z.getResult()) << " = pyc::cpp::zext<" << ow << ", " << iw << ">(" << nt.get(z.getIn())
       << ");\n";
    return success();
  }
  if (auto s = dyn_cast<pyc::SextOp>(op)) {
    unsigned iw = bitWidth(s.getIn().getType());
    unsigned ow = bitWidth(s.getResult().getType());
    if (iw == 0 || ow == 0 || iw > 64 || ow > 64)
      return s.emitError("C++ emitter only supports widths 1..64 for sext in the prototype");
    os << "    " << nt.get(s.getResult()) << " = pyc::cpp::sext<" << ow << ", " << iw << ">(" << nt.get(s.getIn())
       << ");\n";
    return success();
  }
  if (auto ex = dyn_cast<pyc::ExtractOp>(op)) {
    unsigned iw = bitWidth(ex.getIn().getType());
    unsigned ow = bitWidth(ex.getResult().getType());
    if (iw == 0 || ow == 0 || iw > 64 || ow > 64)
      return ex.emitError("C++ emitter only supports widths 1..64 for extract in the prototype");
    os << "    " << nt.get(ex.getResult()) << " = pyc::cpp::extract<" << ow << ", " << iw << ">("
       << nt.get(ex.getIn()) << ", " << ex.getLsbAttr().getInt() << "u);\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::ShliOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0 || w > 64)
      return sh.emitError("C++ emitter only supports widths 1..64 for shli in the prototype");
    os << "    " << nt.get(sh.getResult()) << " = pyc::cpp::shl<" << w << ">(" << nt.get(sh.getIn()) << ", "
       << sh.getAmountAttr().getInt() << "u);\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::LshriOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0 || w > 64)
      return sh.emitError("C++ emitter only supports widths 1..64 for lshri in the prototype");
    os << "    " << nt.get(sh.getResult()) << " = pyc::cpp::lshr<" << w << ">(" << nt.get(sh.getIn()) << ", "
       << sh.getAmountAttr().getInt() << "u);\n";
    return success();
  }
  if (auto sh = dyn_cast<pyc::AshriOp>(op)) {
    unsigned w = bitWidth(sh.getResult().getType());
    if (w == 0 || w > 64)
      return sh.emitError("C++ emitter only supports widths 1..64 for ashri in the prototype");
    os << "    " << nt.get(sh.getResult()) << " = pyc::cpp::ashr<" << w << ">(" << nt.get(sh.getIn()) << ", "
       << sh.getAmountAttr().getInt() << "u);\n";
    return success();
  }
  if (auto c = dyn_cast<pyc::ConcatOp>(op)) {
    unsigned w = bitWidth(c.getResult().getType());
    if (w == 0 || w > 64)
      return c.emitError("C++ emitter only supports total widths 1..64 for concat in the prototype");
    os << "    " << nt.get(c.getResult()) << " = pyc::cpp::concat(";
    for (auto [i, v] : llvm::enumerate(c.getInputs())) {
      if (i)
        os << ", ";
      os << nt.get(v);
    }
    os << ");\n";
    return success();
  }
  return op.emitError("unsupported combinational op for C++ emission");
}

static LogicalResult emitCombMethod(pyc::CombOp comb, llvm::raw_ostream &os, NameTable &nt, unsigned idx) {
  if (comb.getBody().empty())
    return comb.emitError("pyc.comb must have a non-empty region");

  Block &b = comb.getBody().front();
  if (b.getNumArguments() != comb.getNumOperands())
    return comb.emitError("pyc.comb body block argument count must match inputs");

  // Map region args to the corresponding input values (by name).
  for (auto [i, arg] : llvm::enumerate(b.getArguments()))
    nt.names.try_emplace(arg, nt.get(comb.getInputs()[i]));

  os << "  inline void eval_comb_" << idx << "() {\n";
  for (Operation &op : b) {
    if (isa<pyc::YieldOp>(op))
      break;
    if (failed(emitCombAssign(op, os, nt)))
      return failure();
  }

  auto y = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
  if (!y)
    return comb.emitError("pyc.comb must terminate with pyc.yield");
  if (y.getNumOperands() != comb.getNumResults())
    return comb.emitError("pyc.yield operand count must match pyc.comb results");

  for (auto [i, v] : llvm::enumerate(y.getOperands()))
    os << "    " << nt.get(comb.getResult(i)) << " = " << nt.get(v) << ";\n";
  os << "  }\n\n";
  return success();
}

static LogicalResult emitFunc(func::FuncOp f, llvm::raw_ostream &os) {
  NameTable nt;

  if (!llvm::hasSingleElement(f.getBody()))
    return f.emitError("C++ emitter currently supports single-block functions only");

  Block &top = f.getBody().front();

  std::string structName = sanitizeId(f.getSymName());
  os << "struct " << structName << " {\n";

  // Ports.
  std::vector<std::string> outNames;
  outNames.reserve(f.getNumResults());
  for (auto [i, arg] : llvm::enumerate(f.getArguments())) {
    std::string name = nt.unique(getPortName(f, i, /*isResult=*/false));
    nt.names.try_emplace(arg, name);
    os << "  " << cppType(arg.getType()) << " " << name << "{};\n";
  }
  for (unsigned i = 0; i < f.getNumResults(); ++i) {
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
  auto combKey = [&](pyc::CombOp c) { return nt.get(c.getResult(0)); };
  std::sort(combs.begin(), combs.end(), [&](pyc::CombOp a, pyc::CombOp b) { return combKey(a) < combKey(b); });

  llvm::DenseMap<Operation *, std::string> byteMemInstName;
  llvm::DenseMap<Operation *, std::string> syncMemInstName;
  llvm::DenseMap<Operation *, std::string> syncMemDPInstName;

  for (auto r : regs) {
    unsigned w = bitWidth(r.getQ().getType());
    if (w == 0 || w > 64)
      return r.emitError("C++ emitter only supports reg widths 1..64 in the prototype");
    os << "  pyc::cpp::pyc_reg<" << w << "> " << nt.get(r.getQ()) << "_inst;\n";
  }
  for (auto fifo : fifos) {
    unsigned w = bitWidth(fifo.getOutData().getType());
    if (w == 0 || w > 64)
      return fifo.emitError("C++ emitter only supports fifo widths 1..64 in the prototype");
    auto depthAttr = fifo->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return fifo.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    os << "  pyc::cpp::pyc_fifo<" << w << ", " << depth << "> " << nt.get(fifo.getInReady()) << "_inst;\n";
  }
  for (auto mem : byteMems) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer byte_mem types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    if (addrW == 0 || addrW > 64)
      return mem.emitError("C++ emitter only supports byte_mem addr widths 1..64 in the prototype");
    if (dataW == 0 || dataW > 64)
      return mem.emitError("C++ emitter only supports byte_mem data widths 1..64 in the prototype");

    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();

    std::string instName = nt.get(mem.getRdata()) + "_inst";
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      instName = sanitizeId(nameAttr.getValue());
    byteMemInstName.try_emplace(mem.getOperation(), instName);

    os << "  pyc::cpp::pyc_byte_mem<" << addrW << ", " << dataW << ", " << depth << "> " << instName << ";\n";
  }
  for (auto mem : syncMems) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    if (addrW == 0 || addrW > 64)
      return mem.emitError("C++ emitter only supports sync_mem addr widths 1..64 in the prototype");
    if (dataW == 0 || dataW > 64)
      return mem.emitError("C++ emitter only supports sync_mem data widths 1..64 in the prototype");

    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();

    std::string instName = nt.get(mem.getRdata()) + "_inst";
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      instName = sanitizeId(nameAttr.getValue());
    syncMemInstName.try_emplace(mem.getOperation(), instName);

    os << "  pyc::cpp::pyc_sync_mem<" << addrW << ", " << dataW << ", " << depth << "> " << instName << ";\n";
  }
  for (auto mem : syncMemDPs) {
    auto addrTy = dyn_cast<IntegerType>(mem.getRaddr0().getType());
    auto dataTy = dyn_cast<IntegerType>(mem.getRdata0().getType());
    if (!addrTy || !dataTy)
      return mem.emitError("C++ emitter only supports integer sync_mem_dp types");
    unsigned addrW = addrTy.getWidth();
    unsigned dataW = dataTy.getWidth();
    if (addrW == 0 || addrW > 64)
      return mem.emitError("C++ emitter only supports sync_mem_dp addr widths 1..64 in the prototype");
    if (dataW == 0 || dataW > 64)
      return mem.emitError("C++ emitter only supports sync_mem_dp data widths 1..64 in the prototype");

    auto depthAttr = mem->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return mem.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();

    std::string instName = nt.get(mem.getRdata0()) + "_inst";
    if (auto nameAttr = mem->getAttrOfType<StringAttr>("name"))
      instName = sanitizeId(nameAttr.getValue());
    syncMemDPInstName.try_emplace(mem.getOperation(), instName);

    os << "  pyc::cpp::pyc_sync_mem_dp<" << addrW << ", " << dataW << ", " << depth << "> " << instName << ";\n";
  }
  for (auto fifo : asyncFifos) {
    unsigned w = bitWidth(fifo.getOutData().getType());
    if (w == 0 || w > 64)
      return fifo.emitError("C++ emitter only supports async_fifo widths 1..64 in the prototype");
    auto depthAttr = fifo->getAttrOfType<IntegerAttr>("depth");
    if (!depthAttr)
      return fifo.emitError("missing integer attribute `depth`");
    auto depth = depthAttr.getValue().getZExtValue();
    os << "  pyc::cpp::pyc_async_fifo<" << w << ", " << depth << "> " << nt.get(fifo.getInReady()) << "_inst;\n";
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

  // Constructor (wire members default-initialize to 0).
  os << "  " << structName << "()";
  bool firstInit = true;
  for (auto r : regs) {
    os << (firstInit ? " :\n" : ",\n");
    firstInit = false;
    os << "      " << nt.get(r.getQ()) << "_inst(" << nt.get(r.getClk()) << ", " << nt.get(r.getRst()) << ", "
       << nt.get(r.getEn()) << ", " << nt.get(r.getNext()) << ", " << nt.get(r.getInit()) << ", " << nt.get(r.getQ())
       << ")";
  }
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
  for (auto mem : syncMems) {
    os << (firstInit ? " :\n" : ",\n");
    firstInit = false;
    std::string instName = syncMemInstName.lookup(mem.getOperation());
    os << "      " << instName << "(" << nt.get(mem.getClk()) << ", " << nt.get(mem.getRst()) << ", " << nt.get(mem.getRen())
       << ", " << nt.get(mem.getRaddr()) << ", " << nt.get(mem.getRdata()) << ", " << nt.get(mem.getWvalid()) << ", "
       << nt.get(mem.getWaddr()) << ", " << nt.get(mem.getWdata()) << ", " << nt.get(mem.getWstrb()) << ")";
  }
  for (auto mem : syncMemDPs) {
    os << (firstInit ? " :\n" : ",\n");
    firstInit = false;
    std::string instName = syncMemDPInstName.lookup(mem.getOperation());
    os << "      " << instName << "(" << nt.get(mem.getClk()) << ", " << nt.get(mem.getRst()) << ", " << nt.get(mem.getRen0())
       << ", " << nt.get(mem.getRaddr0()) << ", " << nt.get(mem.getRdata0()) << ", " << nt.get(mem.getRen1()) << ", "
       << nt.get(mem.getRaddr1()) << ", " << nt.get(mem.getRdata1()) << ", " << nt.get(mem.getWvalid()) << ", "
       << nt.get(mem.getWaddr()) << ", " << nt.get(mem.getWdata()) << ", " << nt.get(mem.getWstrb()) << ")";
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
  os << "    eval();\n";
  os << "  }\n\n";

  // Emit fused comb helpers.
  for (auto [i, comb] : llvm::enumerate(combs)) {
    if (failed(emitCombMethod(comb, os, nt, static_cast<unsigned>(i))))
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
      if (!includePrims && (isa<pyc::FifoOp>(op) || isa<pyc::AsyncFifoOp>(op) || isa<pyc::ByteMemOp>(op)))
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
    // (imperative evaluation becomes ambiguous); fall back to the legacy fixpoint.
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

  // eval(): attempt a single-pass topological netlist schedule; if the graph has
  // cycles, fall back to a small fixed-point iteration (legacy behavior).
  os << "  void eval() {\n";
  if (hasFullTopo) {
    for (Operation *op : fullOrdered) {
      if (auto fifo = dyn_cast<pyc::FifoOp>(*op)) {
        os << "    " << nt.get(fifo.getInReady()) << "_inst.eval();\n";
        continue;
      }
      if (auto fifo = dyn_cast<pyc::AsyncFifoOp>(*op)) {
        os << "    " << nt.get(fifo.getInReady()) << "_inst.eval();\n";
        continue;
      }
      if (auto mem = dyn_cast<pyc::ByteMemOp>(*op)) {
        os << "    " << byteMemInstName.lookup(mem.getOperation()) << ".eval();\n";
        continue;
      }
      if (auto a = dyn_cast<pyc::AssignOp>(*op)) {
        os << "    " << nt.get(a.getDst()) << " = " << nt.get(a.getSrc()) << ";\n";
        continue;
      }
      if (auto comb = dyn_cast<pyc::CombOp>(*op)) {
        os << "    eval_comb_" << combIndex.lookup(comb.getOperation()) << "();\n";
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
              arith::SelectOp>(*op)) {
        if (failed(emitCombAssign(*op, os, nt)))
          return failure();
        continue;
      }
      return op->emitError("unsupported op for C++ emission: ") << op->getName();
    }
  } else {
    os << "    eval_comb_pass();\n";

    unsigned numPrims = static_cast<unsigned>(fifos.size() + asyncFifos.size() + byteMems.size());
    if (numPrims > 0) {
      os << "    for (unsigned _i = 0; _i < " << numPrims << "u; ++_i) {\n";
      for (auto fifo : fifos)
        os << "      " << nt.get(fifo.getInReady()) << "_inst.eval();\n";
      for (auto fifo : asyncFifos)
        os << "      " << nt.get(fifo.getInReady()) << "_inst.eval();\n";
      for (auto mem : byteMems)
        os << "      " << byteMemInstName.lookup(mem.getOperation()) << ".eval();\n";
      os << "      eval_comb_pass();\n";
      os << "    }\n";
    }
  }

  // Connect return values to output ports.
  auto ret = dyn_cast_or_null<func::ReturnOp>(f.getBody().front().getTerminator());
  if (!ret)
    return f.emitError("missing return");
  for (auto [i, v] : llvm::enumerate(ret.getOperands()))
    os << "    " << outNames[i] << " = " << nt.get(v) << ";\n";

  os << "  }\n\n";

  // tick(): tick sequential primitives (posedge detection is inside primitives).
  os << "  void tick() {\n";
  os << "    // Two-phase update: compute next state for all sequential elements,\n";
  os << "    // then commit together. This avoids ordering artifacts between regs.\n";
  os << "    // Phase 1: compute.\n";
  for (auto r : regs)
    os << "    " << nt.get(r.getQ()) << "_inst.tick_compute();\n";
  for (auto fifo : fifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_compute();\n";
  for (auto mem : byteMems)
    os << "    " << byteMemInstName.lookup(mem.getOperation()) << ".tick_compute();\n";
  for (auto mem : syncMems)
    os << "    " << syncMemInstName.lookup(mem.getOperation()) << ".tick_compute();\n";
  for (auto mem : syncMemDPs)
    os << "    " << syncMemDPInstName.lookup(mem.getOperation()) << ".tick_compute();\n";
  for (auto fifo : asyncFifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_compute();\n";
  for (auto s : cdcSyncs)
    os << "    " << nt.get(s.getOut()) << "_inst.tick_compute();\n";
  os << "    // Phase 2: commit.\n";
  for (auto r : regs)
    os << "    " << nt.get(r.getQ()) << "_inst.tick_commit();\n";
  for (auto fifo : fifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_commit();\n";
  for (auto mem : byteMems)
    os << "    " << byteMemInstName.lookup(mem.getOperation()) << ".tick_commit();\n";
  for (auto mem : syncMems)
    os << "    " << syncMemInstName.lookup(mem.getOperation()) << ".tick_commit();\n";
  for (auto mem : syncMemDPs)
    os << "    " << syncMemDPInstName.lookup(mem.getOperation()) << ".tick_commit();\n";
  for (auto fifo : asyncFifos)
    os << "    " << nt.get(fifo.getInReady()) << "_inst.tick_commit();\n";
  for (auto s : cdcSyncs)
    os << "    " << nt.get(s.getOut()) << "_inst.tick_commit();\n";
  os << "  }\n";

  os << "};\n\n";
  return success();
}

} // namespace

LogicalResult emitCpp(ModuleOp module, llvm::raw_ostream &os, const CppEmitterOptions &) {
  os << "// pyCircuit C++ emission (prototype)\n";
  os << "#include <pyc/cpp/pyc_sim.hpp>\n\n";
  os << "namespace pyc::gen {\n\n";

  for (auto f : module.getOps<func::FuncOp>()) {
    if (failed(emitFunc(f, os)))
      return failure();
  }

  os << "} // namespace pyc::gen\n";
  return success();
}

} // namespace pyc
