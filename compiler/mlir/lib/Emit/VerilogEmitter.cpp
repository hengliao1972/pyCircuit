#include "pyc/Emit/VerilogEmitter.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Operation.h"
#include "mlir/IR/Value.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/MapVector.h"
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

/// Unwrap (possibly nested) VectorType down to the leaf IntegerType. Returns a
/// null IntegerType when the leaf is not an integer.
static IntegerType leafIntType(Type ty) {
  while (auto vt = dyn_cast<VectorType>(ty))
    ty = vt.getElementType();
  return dyn_cast<IntegerType>(ty);
}

static std::string vRange(Type ty) {
  // Clocks/resets are treated as 1-bit scalar ports/nets in Verilog.
  if (isa<pyc::ClockType>(ty) || isa<pyc::ResetType>(ty))
    return "";
  // VectorType uses element packed width for internal unpacked-array views.
  if (auto vt = dyn_cast<VectorType>(ty))
    return vRange(vt.getElementType());
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

/// Flatten recursively nested vector dimensions from outermost to innermost.
static SmallVector<int64_t> vectorShape(Type ty) {
  SmallVector<int64_t> shape;
  while (auto vt = dyn_cast<VectorType>(ty)) {
    llvm::append_range(shape, vt.getShape());
    ty = vt.getElementType();
  }
  return shape;
}

static int64_t vectorLaneCount(ArrayRef<int64_t> shape) {
  int64_t lanes = 1;
  for (int64_t d : shape)
    lanes *= d;
  return lanes;
}

static std::optional<int64_t> flatBitWidth(Type ty) {
  auto width = leafWidth(ty);
  if (!width)
    return std::nullopt;
  if (auto vt = dyn_cast<VectorType>(ty))
    return vectorLaneCount(vectorShape(vt)) * static_cast<int64_t>(*width);
  return static_cast<int64_t>(*width);
}

/// Packed range for module boundary ports.  Vector ports are flattened into a
/// single packed bus so Yosys does not have to parse unpacked array ports.
static std::string vPortRange(Type ty) {
  auto bits = flatBitWidth(ty);
  if (!bits || *bits <= 1)
    return "";
  return "[" + std::to_string(*bits - 1) + ":0]";
}

/// Return the unpacked array dimensions for a VectorType, e.g. " [0:3][0:7]".
/// Returns empty string for non-VectorType.
static std::string vUnpacked(Type ty) {
  if (auto vt = dyn_cast<VectorType>(ty)) {
    std::string dims;
    for (int64_t d : vectorShape(vt))
      dims += " [0:" + std::to_string(d - 1) + "]";
    return dims;
  }
  return "";
}

static std::string indexSuffix(ArrayRef<int64_t> indices) {
  std::string out;
  for (int64_t i : indices)
    out += "[" + std::to_string(i) + "]";
  return out;
}

static int64_t flatLaneIndex(ArrayRef<int64_t> shape, ArrayRef<int64_t> indices) {
  int64_t flat = 0;
  for (size_t i = 0; i < indices.size(); ++i)
    flat = flat * shape[i] + indices[i];
  return flat;
}

static std::string packedSlice(llvm::StringRef base, ArrayRef<int64_t> shape, ArrayRef<int64_t> indices, unsigned width) {
  int64_t lane = flatLaneIndex(shape, indices);
  int64_t lsb = lane * static_cast<int64_t>(width);
  if (width == 1)
    return base.str() + "[" + std::to_string(lsb) + "]";
  return base.str() + "[" + std::to_string(lsb + static_cast<int64_t>(width) - 1) + ":" + std::to_string(lsb) + "]";
}

static void walkVectorIndices(ArrayRef<int64_t> shape,
                              const std::function<void(ArrayRef<int64_t>)> &emit) {
  llvm::SmallVector<int64_t> indices;
  std::function<void(unsigned)> walk = [&](unsigned depth) {
    if (depth == shape.size()) {
      emit(indices);
      return;
    }
    for (int64_t i = 0; i < shape[depth]; ++i) {
      indices.push_back(i);
      walk(depth + 1);
      indices.pop_back();
    }
  };
  walk(0);
}

static void emitUnpackFromPacked(llvm::StringRef arrayBase, llvm::StringRef packedBase, Type ty, raw_ostream &os) {
  auto vt = dyn_cast<VectorType>(ty);
  if (!vt) {
    os << "assign " << arrayBase << " = " << packedBase << ";\n";
    return;
  }
  auto width = leafWidth(ty);
  if (!width)
    return;
  SmallVector<int64_t> shape = vectorShape(vt);
  walkVectorIndices(shape, [&](ArrayRef<int64_t> indices) {
    os << "assign " << arrayBase << indexSuffix(indices) << " = "
       << packedSlice(packedBase, shape, indices, *width) << ";\n";
  });
}

static void emitPackToPacked(llvm::StringRef packedBase, llvm::StringRef arrayBase, Type ty, raw_ostream &os) {
  auto vt = dyn_cast<VectorType>(ty);
  if (!vt) {
    os << "assign " << packedBase << " = " << arrayBase << ";\n";
    return;
  }
  auto width = leafWidth(ty);
  if (!width)
    return;
  SmallVector<int64_t> shape = vectorShape(vt);
  walkVectorIndices(shape, [&](ArrayRef<int64_t> indices) {
    os << "assign " << packedSlice(packedBase, shape, indices, *width)
       << " = " << arrayBase << indexSuffix(indices) << ";\n";
  });
}

/// Build a balanced binary-tree expression from a list of term strings.
/// e.g. {"v[0]","v[1]","v[2]","v[3]"} with "|" → "((v[0] | v[1]) | (v[2] | v[3]))"
static std::string treeReduceExpr(llvm::SmallVectorImpl<std::string> &terms,
                                  const std::string &op) {
  while (terms.size() > 1) {
    llvm::SmallVector<std::string> next;
    for (size_t i = 0; i < terms.size(); i += 2) {
      if (i + 1 < terms.size())
        next.push_back("(" + terms[i] + " " + op + " " + terms[i + 1] + ")");
      else
        next.push_back(terms[i]);
    }
    terms = std::move(next);
  }
  return terms.empty() ? "" : terms[0];
}

static std::string chainReduceExpr(llvm::SmallVectorImpl<std::string> &terms,
                                   const std::string &op) {
  if (terms.empty())
    return "";
  std::string out = terms[0];
  for (size_t i = 1; i < terms.size(); ++i)
    out = "(" + out + " " + op + " " + terms[i] + ")";
  return out;
}

static bool isTreeReduceMode(Operation *op) {
  if (auto mode = op->getAttrOfType<StringAttr>("mode"))
    return mode.getValue() == "tree";
  return false;
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
  if (auto vg = dyn_cast<pyc::VGetOp>(op)) {
    auto vt = dyn_cast<VectorType>(vg.getVec().getType());
    if (!vt)
      return {vg.emitError("verilog emitter expects vector operand for pyc.v_get")};
    std::int64_t idx = vg.getIndexAttr().getInt();
    if (idx < 0 || idx >= vt.getShape()[0])
      return {vg.emitError("pyc.v_get index out of range for verilog emission")};
    emitConnectAssign(
        nt.get(vg.getResult()),
        nt.get(vg.getVec()) + "[" + std::to_string(static_cast<long long>(idx)) + "]",
        vg.getResult().getType(),
        os);
    return success();
  }
  if (auto vc = dyn_cast<pyc::VCreateOp>(op)) {
    auto vt = dyn_cast<VectorType>(vc.getResult().getType());
    if (!vt)
      return {vc.emitError("verilog emitter expects vector result for pyc.v_create")};
    if (static_cast<int64_t>(vc.getElements().size()) != vt.getShape()[0])
      return {vc.emitError("pyc.v_create element count mismatch for verilog emission")};
    std::string dstBase = nt.get(vc.getResult());
    for (auto [i, e] : llvm::enumerate(vc.getElements())) {
      emitConnectAssign(
          dstBase + "[" + std::to_string(static_cast<unsigned>(i)) + "]",
          nt.get(e),
          e.getType(),
          os);
    }
    return success();
  }
  if (auto vb = dyn_cast<pyc::VBroadcastOp>(op)) {
    auto vt = dyn_cast<VectorType>(vb.getResult().getType());
    if (!vt)
      return {vb.emitError("verilog emitter expects vector result for pyc.v_broadcast")};
    if (vt.getRank() != 1)
      return {vb.emitError("verilog emitter currently supports rank-1 pyc.v_broadcast")};
    std::string dst = nt.get(vb.getResult());
    std::string src = nt.get(vb.getScalar());
    for (int64_t i = 0; i < vt.getShape()[0]; ++i)
      os << "assign " << dst << "[" << i << "] = " << src << ";\n";
    return success();
  }
  if (auto vbd = dyn_cast<pyc::VBroadcastDimOp>(op)) {
    auto srcVT = dyn_cast<VectorType>(vbd.getVec().getType());
    auto dstVT = dyn_cast<VectorType>(vbd.getResult().getType());
    if (!srcVT || !dstVT)
      return {vbd.emitError("verilog emitter expects vector types for v_broadcast_dim")};
    int64_t dim = vbd.getDimAttr().getInt();
    std::string dstBase = nt.get(vbd.getResult());
    std::string srcBase = nt.get(vbd.getVec());
    // Walk all result lanes, mapping each to the appropriate source lane.
    std::function<void(unsigned, std::string &, std::string &)> walkDst;
    walkDst = [&](unsigned depth, std::string &dstIdx, std::string &srcIdx) -> void {
      if (depth == static_cast<unsigned>(dstVT.getRank())) {
        os << "assign " << dstBase << dstIdx << " = " << srcBase << srcIdx << ";\n";
        return;
      }
      for (int64_t i = 0; i < dstVT.getDimSize(depth); ++i) {
        std::string d = "[" + std::to_string(i) + "]";
        size_t dOld = dstIdx.size();
        dstIdx += d;
        if (static_cast<int64_t>(depth) != dim) {
          size_t sOld = srcIdx.size();
          srcIdx += d;
          walkDst(depth + 1, dstIdx, srcIdx);
          srcIdx.resize(sOld);
        } else {
          walkDst(depth + 1, dstIdx, srcIdx);
        }
        dstIdx.resize(dOld);
      }
    };
    std::string dstIdx, srcIdx;
    walkDst(0, dstIdx, srcIdx);
    return success();
  }
  auto emitVectorReduce = [&](auto vr, const char *opName, const std::string &opToken) -> LogicalResult {
    auto vt = dyn_cast<VectorType>(vr.getVec().getType());
    if (!vt)
      return vr.emitError("verilog emitter expects vector operand for pyc.") << opName;
    if (vt.getRank() < 1 || vt.getRank() > 2)
      return vr.emitError("verilog emitter currently supports rank-1/rank-2 pyc.") << opName;
    for (std::int64_t lanes : vt.getShape()) {
      if (lanes <= 0)
        return vr.emitError("pyc.") << opName << " requires non-empty vector dimensions for verilog emission";
    }
    bool useTree = isTreeReduceMode(vr.getOperation());

    if (!vr.getDim()) {
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
      std::string expr = useTree ? treeReduceExpr(terms, opToken) : chainReduceExpr(terms, opToken);
      emitConnectAssign(nt.get(vr.getResult()), expr, vr.getResult().getType(), os);
      return success();
    }

    std::int64_t dim = *vr.getDim();
    if (dim < 0 || dim >= vt.getRank())
      return vr.emitError("pyc.") << opName << " dim out of range for verilog emission";

    if (vt.getRank() == 1) {
      std::int64_t lanes = vt.getShape()[0];
      llvm::SmallVector<std::string> terms;
      for (std::int64_t i = 0; i < lanes; ++i) {
        terms.push_back(nt.get(vr.getVec()) + "[" +
                        std::to_string(static_cast<long long>(i)) + "]");
      }
      std::string expr = useTree ? treeReduceExpr(terms, opToken) : chainReduceExpr(terms, opToken);
      emitConnectAssign(nt.get(vr.getResult()), expr, vr.getResult().getType(), os);
      return success();
    }

    std::int64_t rows = vt.getShape()[0];
    std::int64_t cols = vt.getShape()[1];
    std::int64_t outLanes = (dim == 0) ? cols : rows;
    std::int64_t reduceLanes = (dim == 0) ? rows : cols;
    for (std::int64_t i = 0; i < outLanes; ++i) {
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
      std::string expr = useTree ? treeReduceExpr(terms, opToken) : chainReduceExpr(terms, opToken);
      emitConnectAssign(
          nt.get(vr.getResult()) + "[" + std::to_string(static_cast<long long>(i)) + "]",
          expr,
          vt.getElementType(),
          os);
    }
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
  return std::nullopt;
}

// Unroll element-wise vector ops into per-lane scalar assigns.
static LogicalResult emitVectorElementwise(Operation &op, VectorType vt, raw_ostream &os, NameTable &nt) {
  SmallVector<int64_t> shape = vectorShape(vt);
  unsigned rank = static_cast<unsigned>(shape.size());
  Value res = op.getResult(0);

  std::function<LogicalResult(unsigned, std::string &)> walk;
  std::vector<std::pair<Value, std::string>> saved;
  llvm::SmallDenseSet<Value, 8> rebound;
  walk = [&](unsigned depth, std::string &idx) -> LogicalResult {
    if (depth == rank) {
      saved.clear();
      rebound.clear();
      auto rebind = [&](Value v) {
        if (!rebound.insert(v).second)
          return;
        std::string base = nt.get(v);
        saved.emplace_back(v, base);
        nt.names[v] = base + idx;
      };
      rebind(res);
      for (Value operand : op.getOperands()) {
        if (auto ovt = dyn_cast<VectorType>(operand.getType()))
          if (vectorShape(ovt) == shape)
            rebind(operand);
      }
      auto handled = emitScalarOpAssign(op, os, nt);
      for (auto &s : saved)
        nt.names[s.first] = s.second;
      if (!handled)
        return op.emitError("verilog emitter: unsupported vector op for element-wise emission");
      return *handled;
    }
    for (int64_t i = 0; i < shape[depth]; ++i) {
      size_t old = idx.size();
      idx += "[" + std::to_string(i) + "]";
      if (failed(walk(depth + 1, idx)))
        return failure();
      idx.resize(old);
    }
    return success();
  };
  std::string idx;
  return walk(0, idx);
}

// Emit a netlist op, expanding vector results element-wise when needed.
static LogicalResult emitNetlistOp(Operation &op, raw_ostream &os, NameTable &nt) {
  if (op.getNumResults() == 1)
    if (auto vt = dyn_cast<VectorType>(op.getResult(0).getType()))
      if (!isa<pyc::VGetOp,
               pyc::VCreateOp,
               pyc::VBroadcastOp,
               pyc::VBroadcastDimOp,
               pyc::VOrReduceOp,
               pyc::VAndReduceOp,
               pyc::VAddReduceOp>(op))
        return emitVectorElementwise(op, vt, os, nt);
  std::optional<LogicalResult> handled = emitScalarOpAssign(op, os, nt);
  if (!handled)
    return op.emitError("internal error: missing verilog emission handler");
  return *handled;
}

// Emit a continuous connection `lhs = rhs`, expanding unpacked vector arrays
// element-wise (whole-array continuous assignment is not portable).
static void emitConnectAssign(llvm::StringRef lhs, llvm::StringRef rhs, Type ty, raw_ostream &os) {
  auto vt = dyn_cast<VectorType>(ty);
  if (!vt) {
    os << "assign " << lhs << " = " << rhs << ";\n";
    return;
  }
  SmallVector<int64_t> shape = vectorShape(vt);
  walkVectorIndices(shape, [&](ArrayRef<int64_t> indices) {
    std::string suffix = indexSuffix(indices);
    os << "assign " << lhs << suffix << " = " << rhs << suffix << ";\n";
  });
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

// A `pyc.assign` lowers to a continuous `assign`, which is a DRIVER, not a
// statement: two of them on one net are two drivers fighting, and Verilog
// leaves the result undefined. The C++ emitter lowers the same op to `dst =
// src;`, a statement with last-write-wins semantics, so a design that assigns
// one net twice means two different things in the two backends — and the
// Verilog one means nothing in particular.
//
// topoSortCombOps() has always detected this (its wireAssignCount guard) but
// signals it with the same `false` it uses for a genuine combinational cycle,
// and emitFunc's response to that `false` is to fall back to a name-order sort
// and emit every assign regardless. So the condition was recognised by name,
// discarded, and the broken netlist written out silently. Downstream tools do
// not save you: yosys warns but proceeds, and verilator 5.044 accepts such a
// netlist with no diagnostic at all, even under -Wall.
//
// Refuse instead, and name the nets. Order is deterministic (MapVector) so the
// diagnostic is diffable.
static LogicalResult checkSingleDriver(func::FuncOp f, ArrayRef<Operation *> ops, NameTable &nt,
                                       bool allowMultiDriven) {
  llvm::MapVector<Value, unsigned> assignCount;
  for (Operation *op : ops)
    if (auto a = dyn_cast<pyc::AssignOp>(op))
      assignCount[a.getDst()]++;

  llvm::SmallVector<std::pair<std::string, unsigned>> offenders;
  unsigned extraDrivers = 0;
  for (auto &kv : assignCount)
    if (kv.second > 1) {
      offenders.emplace_back(nt.get(kv.first), kv.second);
      extraDrivers += kv.second - 1;
    }

  if (offenders.empty())
    return success();

  const unsigned kMaxListed = 10;
  std::string detail;
  {
    llvm::raw_string_ostream d(detail);
    d << offenders.size() << " net(s) in module '" << f.getSymName() << "' have more than one continuous driver ("
      << extraDrivers << " redundant assign(s)):";
    for (auto [i, o] : llvm::enumerate(offenders)) {
      if (i >= kMaxListed) {
        d << "\n    ... and " << (offenders.size() - kMaxListed) << " more";
        break;
      }
      d << "\n    " << o.first << " <- " << o.second << " drivers";
    }
  }

  if (allowMultiDriven) {
    // Deliberately llvm::errs() and not f.emitWarning(): pycc registers no
    // MLIR diagnostic handler, and DiagnosticEngine's fallback prints errors
    // only — a warning would be dropped on the floor. Silently emitting a
    // netlist known to be undefined is the exact failure this check exists to
    // end, so the escape hatch has to be audible.
    llvm::errs() << "warning: " << detail
                 << "\n  emitting anyway (--allow-multidriven): the result is undefined Verilog"
                    " and does not implement the design\n";
    return success();
  }

  f.emitError() << detail
                << "\n  a continuous `assign` is a driver, not a statement, so this is undefined Verilog."
                   "\n  The usual cause is a register written through several guarded writes: each write must"
                   "\n  chain onto the running next-state value, leaving ONE assign per net, rather than emit"
                   "\n  its own assign defaulting to the register's current value."
                   "\n  Pass --allow-multidriven to emit it anyway (reproduces the historical, broken output).";
  return failure();
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
  struct VectorInputAlias {
    std::string alias;
    std::string port;
    Type ty;
  };
  std::vector<VectorInputAlias> vectorInputAliases;

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
    if (isa<VectorType>(arg.getType())) {
      std::string alias = nt.unique(portName + "__vec");
      nt.names.try_emplace(arg, alias);
      vectorInputAliases.push_back({alias, portName, arg.getType()});
    } else {
      nt.names.try_emplace(arg, portName);
    }
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

  for (auto &aliasInfo : vectorInputAliases) {
    std::string range = vRange(aliasInfo.ty);
    os << "wire ";
    if (!range.empty())
      os << range << " ";
    os << aliasInfo.alias << vUnpacked(aliasInfo.ty) << "; // port=" << aliasInfo.port << "\n";
    emitUnpackFromPacked(aliasInfo.alias, aliasInfo.port, aliasInfo.ty, os);
  }
  if (!vectorInputAliases.empty())
    os << "\n";

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
    std::string unpacked = vUnpacked(d.ty);
    os << "wire ";
    if (!range.empty())
      os << range << " ";
    os << d.name << unpacked << ";";
    if (!d.comment.empty())
      os << " // " << d.comment;
    os << "\n";
  }
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
              pyc::ConcatOp,
              pyc::VGetOp,
              pyc::VCreateOp,
              pyc::VBroadcastOp,
              pyc::VBroadcastDimOp,
              pyc::VOrReduceOp,
              pyc::VAndReduceOp,
              pyc::VAddReduceOp>(op)) {
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
      if (isa<pyc::SyncMemOp, pyc::SyncMemDPOp, pyc::AsyncFifoOp, pyc::CdcSyncOp>(op)) {
        seqInstOps.push_back(&op);
        continue;
      }
      return op.emitError("unsupported op for verilog emission: ") << op.getName();
    }
  }

  auto cmp = [&](Operation *a, Operation *b) { return opSortKey(a, nt) < opSortKey(b, nt); };
  std::sort(instOps.begin(), instOps.end(), cmp);
  std::sort(seqInstOps.begin(), seqInstOps.end(), cmp);

  // Before sorting: refuse multi-driver nets. This has to happen here rather
  // than inside topoSortCombOps, because by the time that function's `false`
  // reaches the fallback below, the name-order sort has already destroyed the
  // program order that says which assign was meant to win — so there is no
  // longer enough information to report, let alone repair, the conflict.
  if (failed(checkSingleDriver(f, combAssignOps, nt, opts.allowMultiDriven)))
    return failure();

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

      for (unsigned i = 0; i < inPorts.size(); ++i) {
        Value operand = inst.getOperand(i);
        if (isa<VectorType>(operand.getType())) {
          std::string bridge = nt.unique(instName + "_" + inPorts[i] + "__flat");
          std::string range = vPortRange(operand.getType());
          os << "wire ";
          if (!range.empty())
            os << range << " ";
          os << bridge << ";\n";
          emitPackToPacked(bridge, nt.get(operand), operand.getType(), os);
          inConn.push_back(bridge);
        } else {
          inConn.push_back(nt.get(operand));
        }
      }
      for (unsigned i = 0; i < outPorts.size(); ++i) {
        Value result = inst.getResult(i);
        if (isa<VectorType>(result.getType())) {
          std::string bridge = nt.unique(instName + "_" + outPorts[i] + "__flat");
          std::string range = vPortRange(result.getType());
          os << "wire ";
          if (!range.empty())
            os << range << " ";
          os << bridge << ";\n";
          outConn.push_back(bridge);
        } else {
          outConn.push_back(nt.get(result));
        }
      }

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
      for (unsigned i = 0; i < outPorts.size(); ++i) {
        Value result = inst.getResult(i);
        if (isa<VectorType>(result.getType()))
          emitUnpackFromPacked(nt.get(result), outConn[i], result.getType(), os);
      }
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
          os << "pyc_reg #(.WIDTH(" << *width << ")) " << nt.get(r.getQ()) << "_inst" << instanceSuffix << " (\n";
          os << "  .clk(" << nt.get(r.getClk()) << "),\n";
          os << "  .rst(" << nt.get(r.getRst()) << "),\n";
          os << "  .en(" << nt.get(r.getEn()) << "),\n";
          os << "  .d(" << nt.get(r.getNext()) << suffix << "),\n";
          os << "  .init(" << nt.get(r.getInit()) << suffix << "),\n";
          os << "  .q(" << nt.get(r.getQ()) << suffix << ")\n";
          os << ");\n";
        };

        if (auto vt = dyn_cast<VectorType>(qTy)) {
          SmallVector<int64_t> shape = vectorShape(vt);
          walkVectorIndices(shape, [&](ArrayRef<int64_t> indices) {
            std::string suffix = indexSuffix(indices);
            std::string instanceSuffix;
            for (int64_t index : indices)
              instanceSuffix += "_" + std::to_string(index);
            emitReg(suffix, instanceSuffix);
          });
        } else {
          emitReg("", "");
        }
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
    if (!isa<VectorType>(f.getResultTypes()[i]) && nt.get(v) == outNames[i])
      continue;
    if (isa<VectorType>(f.getResultTypes()[i]))
      emitPackToPacked(outNames[i], nt.get(v), f.getResultTypes()[i], os);
    else
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

LogicalResult emitVerilogFunc(ModuleOp module, func::FuncOp f, llvm::raw_ostream &os, const VerilogEmitterOptions &opts) {
  (void)module;
  return emitFunc(f, os, opts);
}

} // namespace pyc
