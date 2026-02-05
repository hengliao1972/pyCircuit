#include "pyc/Dialect/PYC/PYCOps.h"

#include "pyc/Dialect/PYC/PYCDialect.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/IR/OpImplementation.h"
#include "mlir/IR/Types.h"
#include "mlir/Support/LogicalResult.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/Support/raw_ostream.h"

#include <optional>

using namespace mlir;
using namespace pyc;

ParseResult ConstantOp::parse(OpAsmParser &parser, OperationState &result) {
  // Parse: `pyc.constant <integer> : <type>`
  SMLoc loc = parser.getCurrentLocation();

  // Parse the literal as an APInt (avoid consuming `: <type>` as part of the attribute).
  APInt v;
  Type type;
  if (parser.parseInteger(v) || parser.parseColonType(type))
    return failure();

  auto intTy = dyn_cast<IntegerType>(type);
  if (!intTy)
    return parser.emitError(loc, "pyc.constant requires an integer result type");

  // Re-type the value to match the result type width.
  if (v.getBitWidth() != (unsigned)intTy.getWidth())
    v = v.zextOrTrunc(intTy.getWidth());

  result.addAttribute("value", IntegerAttr::get(intTy, v));
  result.addTypes(type);
  return success();
}

void ConstantOp::print(OpAsmPrinter &p) {
  p << " " << getValueAttr().getValue().getZExtValue() << " : " << getType();
}

OpFoldResult ConstantOp::fold(FoldAdaptor) { return getValueAttr(); }

static std::optional<llvm::APInt> asIntAttr(Attribute a) {
  if (!a)
    return std::nullopt;
  if (auto ia = dyn_cast<IntegerAttr>(a))
    return ia.getValue();
  return std::nullopt;
}

static IntegerAttr intAttrFor(Type ty, const llvm::APInt &v) {
  auto intTy = cast<IntegerType>(ty);
  llvm::APInt vv = v;
  if (vv.getBitWidth() != intTy.getWidth())
    vv = vv.zextOrTrunc(intTy.getWidth());
  return IntegerAttr::get(intTy, vv);
}

OpFoldResult AddOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b)
    return intAttrFor(outTy, (*a + *b).trunc(outTy.getWidth()));
  if (a && a->isZero())
    return getRhs();
  if (b && b->isZero())
    return getLhs();
  return {};
}

OpFoldResult SubOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b)
    return intAttrFor(outTy, (*a - *b).trunc(outTy.getWidth()));
  if (b && b->isZero())
    return getLhs();
  if (getLhs() == getRhs())
    return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  return {};
}

OpFoldResult MulOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b)
    return intAttrFor(outTy, (*a * *b).trunc(outTy.getWidth()));
  if (a) {
    if (a->isZero())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
    if (a->isOne())
      return getRhs();
  }
  if (b) {
    if (b->isZero())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
    if (b->isOne())
      return getLhs();
  }
  return {};
}

OpFoldResult UdivOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (b) {
    if (b->isZero())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
    if (b->isOne())
      return getLhs();
  }
  if (a && a->isZero())
    return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  if (a && b)
    return intAttrFor(outTy, a->udiv(*b).trunc(outTy.getWidth()));
  return {};
}

OpFoldResult UremOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (b) {
    if (b->isZero())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
    if (b->isOne())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  }
  if (a && a->isZero())
    return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  if (a && b)
    return intAttrFor(outTy, a->urem(*b).trunc(outTy.getWidth()));
  return {};
}

OpFoldResult SdivOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (b) {
    if (b->isZero())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
    if (b->isOne())
      return getLhs();
  }
  if (a && a->isZero())
    return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  if (a && b)
    return intAttrFor(outTy, a->sdiv(*b).trunc(outTy.getWidth()));
  return {};
}

OpFoldResult SremOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (b) {
    if (b->isZero())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
    if (b->isOne())
      return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  }
  if (a && a->isZero())
    return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  if (a && b)
    return intAttrFor(outTy, a->srem(*b).trunc(outTy.getWidth()));
  return {};
}

OpFoldResult AndOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b)
    return intAttrFor(outTy, (*a & *b).trunc(outTy.getWidth()));
  if (a) {
    if (a->isZero())
      return intAttrFor(outTy, *a);
    if (a->isAllOnes())
      return getRhs();
  }
  if (b) {
    if (b->isZero())
      return intAttrFor(outTy, *b);
    if (b->isAllOnes())
      return getLhs();
  }
  return {};
}

OpFoldResult OrOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b)
    return intAttrFor(outTy, (*a | *b).trunc(outTy.getWidth()));
  if (a) {
    if (a->isZero())
      return getRhs();
    if (a->isAllOnes())
      return intAttrFor(outTy, *a);
  }
  if (b) {
    if (b->isZero())
      return getLhs();
    if (b->isAllOnes())
      return intAttrFor(outTy, *b);
  }
  return {};
}

OpFoldResult XorOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b)
    return intAttrFor(outTy, (*a ^ *b).trunc(outTy.getWidth()));
  if (a && a->isZero())
    return getRhs();
  if (b && b->isZero())
    return getLhs();
  if (getLhs() == getRhs())
    return intAttrFor(outTy, llvm::APInt(outTy.getWidth(), 0));
  return {};
}

OpFoldResult NotOp::fold(FoldAdaptor adaptor) {
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getIn());
  if (a)
    return intAttrFor(outTy, (~(*a)).trunc(outTy.getWidth()));
  if (auto inner = getIn().getDefiningOp<NotOp>())
    return inner.getIn();
  return {};
}

OpFoldResult MuxOp::fold(FoldAdaptor adaptor) {
  auto sel = asIntAttr(adaptor.getSel());
  if (sel) {
    if (sel->isZero())
      return getB();
    return getA();
  }
  if (getA() == getB())
    return getA();
  return {};
}

OpFoldResult EqOp::fold(FoldAdaptor adaptor) {
  if (getLhs() == getRhs())
    return IntegerAttr::get(IntegerType::get(getContext(), 1), 1);
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b) {
    bool eq = (*a == *b);
    return IntegerAttr::get(IntegerType::get(getContext(), 1), eq ? 1 : 0);
  }
  return {};
}

OpFoldResult UltOp::fold(FoldAdaptor adaptor) {
  if (getLhs() == getRhs())
    return IntegerAttr::get(IntegerType::get(getContext(), 1), 0);
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b) {
    bool lt = a->ult(*b);
    return IntegerAttr::get(IntegerType::get(getContext(), 1), lt ? 1 : 0);
  }
  return {};
}

OpFoldResult SltOp::fold(FoldAdaptor adaptor) {
  if (getLhs() == getRhs())
    return IntegerAttr::get(IntegerType::get(getContext(), 1), 0);
  auto a = asIntAttr(adaptor.getLhs());
  auto b = asIntAttr(adaptor.getRhs());
  if (a && b) {
    bool lt = a->slt(*b);
    return IntegerAttr::get(IntegerType::get(getContext(), 1), lt ? 1 : 0);
  }
  return {};
}

OpFoldResult TruncOp::fold(FoldAdaptor adaptor) {
  if (getIn().getType() == getResult().getType())
    return getIn();
  auto a = asIntAttr(adaptor.getIn());
  if (a)
    return intAttrFor(getResult().getType(), a->trunc(cast<IntegerType>(getResult().getType()).getWidth()));
  return {};
}

OpFoldResult ZextOp::fold(FoldAdaptor adaptor) {
  if (getIn().getType() == getResult().getType())
    return getIn();
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    unsigned ow = cast<IntegerType>(getResult().getType()).getWidth();
    return intAttrFor(getResult().getType(), a->zext(ow));
  }
  return {};
}

OpFoldResult SextOp::fold(FoldAdaptor adaptor) {
  if (getIn().getType() == getResult().getType())
    return getIn();
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    unsigned ow = cast<IntegerType>(getResult().getType()).getWidth();
    return intAttrFor(getResult().getType(), a->sext(ow));
  }
  return {};
}

OpFoldResult ExtractOp::fold(FoldAdaptor adaptor) {
  auto inTy = cast<IntegerType>(getIn().getType());
  auto outTy = cast<IntegerType>(getResult().getType());
  std::int64_t lsb = getLsbAttr().getInt();
  if (lsb == 0 && outTy.getWidth() == inTy.getWidth())
    return getIn();
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    llvm::APInt shifted = a->lshr(static_cast<unsigned>(lsb));
    llvm::APInt sliced = shifted.trunc(outTy.getWidth());
    return intAttrFor(getResult().getType(), sliced);
  }
  return {};
}

OpFoldResult ShliOp::fold(FoldAdaptor adaptor) {
  std::int64_t amt = getAmountAttr().getInt();
  if (amt == 0)
    return getIn();
  auto outTy = cast<IntegerType>(getResult().getType());
  if (static_cast<std::uint64_t>(amt) >= outTy.getWidth())
    return intAttrFor(getResult().getType(), llvm::APInt(outTy.getWidth(), 0));
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    llvm::APInt shifted = (*a << static_cast<unsigned>(amt)).trunc(outTy.getWidth());
    return intAttrFor(getResult().getType(), shifted);
  }
  return {};
}

OpFoldResult LshriOp::fold(FoldAdaptor adaptor) {
  std::int64_t amt = getAmountAttr().getInt();
  if (amt == 0)
    return getIn();
  auto outTy = cast<IntegerType>(getResult().getType());
  if (static_cast<std::uint64_t>(amt) >= outTy.getWidth())
    return intAttrFor(getResult().getType(), llvm::APInt(outTy.getWidth(), 0));
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    llvm::APInt shifted = a->lshr(static_cast<unsigned>(amt)).trunc(outTy.getWidth());
    return intAttrFor(getResult().getType(), shifted);
  }
  return {};
}

OpFoldResult AshriOp::fold(FoldAdaptor adaptor) {
  std::int64_t amt = getAmountAttr().getInt();
  if (amt == 0)
    return getIn();
  auto outTy = cast<IntegerType>(getResult().getType());
  auto a = asIntAttr(adaptor.getIn());
  if (static_cast<std::uint64_t>(amt) >= outTy.getWidth()) {
    if (a) {
      bool neg = a->isNegative();
      return intAttrFor(getResult().getType(), neg ? llvm::APInt::getAllOnes(outTy.getWidth())
                                                   : llvm::APInt(outTy.getWidth(), 0));
    }
  }
  if (a) {
    llvm::APInt shifted = a->ashr(static_cast<unsigned>(amt)).trunc(outTy.getWidth());
    return intAttrFor(getResult().getType(), shifted);
  }
  return {};
}

OpFoldResult ConcatOp::fold(FoldAdaptor adaptor) {
  if (getInputs().size() == 1)
    return getInputs().front();

  auto outTy = cast<IntegerType>(getResult().getType());
  llvm::APInt acc(outTy.getWidth(), 0);

  bool allConst = true;
  unsigned offset = outTy.getWidth();
  for (auto [v, a] : llvm::zip(getInputs(), adaptor.getInputs())) {
    auto inTy = cast<IntegerType>(v.getType());
    offset -= inTy.getWidth();
    auto av = asIntAttr(a);
    if (!av) {
      allConst = false;
      break;
    }
    llvm::APInt piece = av->zextOrTrunc(inTy.getWidth());
    acc.insertBits(piece, offset);
  }
  if (allConst)
    return intAttrFor(getResult().getType(), acc);

  return {};
}

OpFoldResult AliasOp::fold(FoldAdaptor) {
  // Preserve alias ops that carry a debug name (used for codegen name mangling).
  if (auto nAttr = (*this)->getAttrOfType<StringAttr>("pyc.name"))
    return {};
  return getIn();
}

LogicalResult MuxOp::verify() {
  auto aTy = getA().getType();
  auto bTy = getB().getType();
  auto rTy = getResult().getType();
  if (aTy != bTy)
    return emitOpError("requires a and b to have the same type");
  if (rTy != aTy)
    return emitOpError("result type must match a/b type");
  return success();
}

LogicalResult NotOp::verify() {
  if (getIn().getType() != getResult().getType())
    return emitOpError("result type must match input type");
  return success();
}

static LogicalResult verifyIntCast(Operation *op, Type inTyRaw, Type outTyRaw, bool requireWiden, bool signExtend) {
  (void)signExtend;
  auto inTy = dyn_cast<IntegerType>(inTyRaw);
  auto outTy = dyn_cast<IntegerType>(outTyRaw);
  if (!inTy || !outTy)
    return op->emitOpError("only supports integer types");
  if (requireWiden) {
    if (outTy.getWidth() < inTy.getWidth())
      return op->emitOpError("result width must be >= input width");
  } else {
    if (outTy.getWidth() > inTy.getWidth())
      return op->emitOpError("result width must be <= input width");
  }
  return success();
}

LogicalResult TruncOp::verify() { return verifyIntCast(*this, getIn().getType(), getResult().getType(), /*requireWiden=*/false, /*signExtend=*/false); }

LogicalResult ZextOp::verify() { return verifyIntCast(*this, getIn().getType(), getResult().getType(), /*requireWiden=*/true, /*signExtend=*/false); }

LogicalResult SextOp::verify() { return verifyIntCast(*this, getIn().getType(), getResult().getType(), /*requireWiden=*/true, /*signExtend=*/true); }

LogicalResult ExtractOp::verify() {
  auto inTy = dyn_cast<IntegerType>(getIn().getType());
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!inTy || !outTy)
    return emitOpError("only supports integer types");
  if (outTy.getWidth() == 0)
    return emitOpError("result width must be > 0");
  std::int64_t lsb = getLsbAttr().getInt();
  if (lsb < 0)
    return emitOpError("lsb must be >= 0");
  if (static_cast<std::uint64_t>(lsb) + static_cast<std::uint64_t>(outTy.getWidth()) >
      static_cast<std::uint64_t>(inTy.getWidth()))
    return emitOpError("slice out of range for input type");
  return success();
}

LogicalResult ShliOp::verify() {
  auto ty = dyn_cast<IntegerType>(getIn().getType());
  if (!ty)
    return emitOpError("only supports integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

LogicalResult LshriOp::verify() {
  auto ty = dyn_cast<IntegerType>(getIn().getType());
  if (!ty)
    return emitOpError("only supports integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

LogicalResult AshriOp::verify() {
  auto ty = dyn_cast<IntegerType>(getIn().getType());
  if (!ty)
    return emitOpError("only supports integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

LogicalResult ConcatOp::verify() {
  if (getInputs().empty())
    return emitOpError("requires at least one input");

  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy)
    return emitOpError("only supports integer result types");

  std::uint64_t sum = 0;
  for (Value v : getInputs()) {
    auto ty = dyn_cast<IntegerType>(v.getType());
    if (!ty)
      return emitOpError("only supports integer input types");
    sum += static_cast<std::uint64_t>(ty.getWidth());
  }

  if (sum != static_cast<std::uint64_t>(outTy.getWidth()))
    return emitOpError("result width must equal sum of input widths");

  return success();
}

LogicalResult AssignOp::verify() {
  if (!getDst().getDefiningOp<WireOp>())
    return emitOpError("dst must be defined by pyc.wire");
  return success();
}

LogicalResult RegOp::verify() {
  auto nextTy = getNext().getType();
  if (getInit().getType() != nextTy)
    return emitOpError("init type must match next type");
  if (getQ().getType() != nextTy)
    return emitOpError("result type must match next type");
  return success();
}

LogicalResult FifoOp::verify() {
  auto inTy = getInData().getType();
  auto outTy = getOutData().getType();
  if (inTy != outTy)
    return emitOpError("out_data type must match in_data type");
  auto depthAttr = (*this)->getAttrOfType<IntegerAttr>("depth");
  if (!depthAttr)
    return emitOpError("requires integer attribute `depth`");
  if (depthAttr.getValue().getSExtValue() <= 0)
    return emitOpError("depth must be > 0");
  return success();
}

LogicalResult ByteMemOp::verify() {
  auto addrTy = dyn_cast<IntegerType>(getRaddr().getType());
  auto waddrTy = dyn_cast<IntegerType>(getWaddr().getType());
  if (!addrTy || !waddrTy)
    return emitOpError("only supports integer address types");
  if (addrTy != waddrTy)
    return emitOpError("waddr type must match raddr type");

  auto dataTy = dyn_cast<IntegerType>(getWdata().getType());
  auto rdataTy = dyn_cast<IntegerType>(getRdata().getType());
  if (!dataTy || !rdataTy)
    return emitOpError("only supports integer data types");
  if (dataTy != rdataTy)
    return emitOpError("rdata type must match wdata type");

  unsigned dataW = dataTy.getWidth();
  if (dataW == 0 || dataW > 64)
    return emitOpError("prototype supports data widths 1..64");
  if ((dataW % 8) != 0)
    return emitOpError("prototype requires data width divisible by 8");

  auto strbTy = dyn_cast<IntegerType>(getWstrb().getType());
  if (!strbTy)
    return emitOpError("only supports integer wstrb types");
  if (strbTy.getWidth() != (dataW / 8))
    return emitOpError("wstrb width must be (data_width / 8)");

  auto depthAttr = (*this)->getAttrOfType<IntegerAttr>("depth");
  if (!depthAttr)
    return emitOpError("requires integer attribute `depth` (bytes)");
  if (depthAttr.getValue().getSExtValue() <= 0)
    return emitOpError("depth must be > 0");

  if (auto nameAttr = (*this)->getAttrOfType<StringAttr>("name")) {
    if (nameAttr.getValue().empty())
      return emitOpError("name must be non-empty when provided");
  }

  return success();
}

LogicalResult SyncMemOp::verify() {
  auto addrTy = dyn_cast<IntegerType>(getRaddr().getType());
  auto waddrTy = dyn_cast<IntegerType>(getWaddr().getType());
  if (!addrTy || !waddrTy)
    return emitOpError("only supports integer address types");
  if (addrTy != waddrTy)
    return emitOpError("waddr type must match raddr type");

  auto dataTy = dyn_cast<IntegerType>(getWdata().getType());
  auto rdataTy = dyn_cast<IntegerType>(getRdata().getType());
  if (!dataTy || !rdataTy)
    return emitOpError("only supports integer data types");
  if (dataTy != rdataTy)
    return emitOpError("rdata type must match wdata type");

  unsigned dataW = dataTy.getWidth();
  if (dataW == 0 || dataW > 64)
    return emitOpError("prototype supports data widths 1..64");
  if ((dataW % 8) != 0)
    return emitOpError("prototype requires data width divisible by 8");

  auto strbTy = dyn_cast<IntegerType>(getWstrb().getType());
  if (!strbTy)
    return emitOpError("only supports integer wstrb types");
  if (strbTy.getWidth() != (dataW / 8))
    return emitOpError("wstrb width must be (data_width / 8)");

  auto depthAttr = (*this)->getAttrOfType<IntegerAttr>("depth");
  if (!depthAttr)
    return emitOpError("requires integer attribute `depth` (entries)");
  if (depthAttr.getValue().getSExtValue() <= 0)
    return emitOpError("depth must be > 0");

  if (auto nameAttr = (*this)->getAttrOfType<StringAttr>("name")) {
    if (nameAttr.getValue().empty())
      return emitOpError("name must be non-empty when provided");
  }

  return success();
}

LogicalResult SyncMemDPOp::verify() {
  auto addrTy0 = dyn_cast<IntegerType>(getRaddr0().getType());
  auto addrTy1 = dyn_cast<IntegerType>(getRaddr1().getType());
  auto waddrTy = dyn_cast<IntegerType>(getWaddr().getType());
  if (!addrTy0 || !addrTy1 || !waddrTy)
    return emitOpError("only supports integer address types");
  if (addrTy0 != addrTy1 || addrTy0 != waddrTy)
    return emitOpError("raddr0/raddr1/waddr types must match");

  auto dataTy = dyn_cast<IntegerType>(getWdata().getType());
  auto rdataTy0 = dyn_cast<IntegerType>(getRdata0().getType());
  auto rdataTy1 = dyn_cast<IntegerType>(getRdata1().getType());
  if (!dataTy || !rdataTy0 || !rdataTy1)
    return emitOpError("only supports integer data types");
  if (dataTy != rdataTy0 || dataTy != rdataTy1)
    return emitOpError("rdata types must match wdata type");

  unsigned dataW = dataTy.getWidth();
  if (dataW == 0 || dataW > 64)
    return emitOpError("prototype supports data widths 1..64");
  if ((dataW % 8) != 0)
    return emitOpError("prototype requires data width divisible by 8");

  auto strbTy = dyn_cast<IntegerType>(getWstrb().getType());
  if (!strbTy)
    return emitOpError("only supports integer wstrb types");
  if (strbTy.getWidth() != (dataW / 8))
    return emitOpError("wstrb width must be (data_width / 8)");

  auto depthAttr = (*this)->getAttrOfType<IntegerAttr>("depth");
  if (!depthAttr)
    return emitOpError("requires integer attribute `depth` (entries)");
  if (depthAttr.getValue().getSExtValue() <= 0)
    return emitOpError("depth must be > 0");

  if (auto nameAttr = (*this)->getAttrOfType<StringAttr>("name")) {
    if (nameAttr.getValue().empty())
      return emitOpError("name must be non-empty when provided");
  }

  return success();
}

LogicalResult AsyncFifoOp::verify() {
  auto inTy = getInData().getType();
  auto outTy = getOutData().getType();
  if (inTy != outTy)
    return emitOpError("out_data type must match in_data type");
  auto depthAttr = (*this)->getAttrOfType<IntegerAttr>("depth");
  if (!depthAttr)
    return emitOpError("requires integer attribute `depth`");
  std::int64_t depth = depthAttr.getValue().getSExtValue();
  if (depth < 2)
    return emitOpError("depth must be >= 2");
  // Prototype async FIFO assumes a power-of-two depth for gray-code pointers.
  std::uint64_t d = static_cast<std::uint64_t>(depth);
  if ((d & (d - 1)) != 0)
    return emitOpError("depth must be a power of two in the prototype");
  return success();
}

LogicalResult CdcSyncOp::verify() {
  auto ty = dyn_cast<IntegerType>(getIn().getType());
  if (!ty)
    return emitOpError("only supports integer types");
  if (ty.getWidth() == 0 || ty.getWidth() > 64)
    return emitOpError("prototype supports widths 1..64");
  auto stagesAttr = (*this)->getAttrOfType<IntegerAttr>("stages");
  if (stagesAttr) {
    if (stagesAttr.getValue().getSExtValue() < 1)
      return emitOpError("stages must be >= 1");
  }
  return success();
}

LogicalResult CombOp::verify() {
  if (getBody().empty())
    return emitOpError("requires a non-empty region");
  if (!llvm::hasSingleElement(getBody()))
    return emitOpError("requires a single block region");

  Block &b = getBody().front();
  if (b.getNumArguments() != getNumOperands())
    return emitOpError("body block argument count must match comb inputs");

  for (auto [arg, in] : llvm::zip(b.getArguments(), getInputs())) {
    if (arg.getType() != in.getType())
      return emitOpError("body block argument types must match comb input types");
  }

  auto yield = dyn_cast<YieldOp>(b.getTerminator());
  if (!yield)
    return emitOpError("body must terminate with pyc.yield");

  if (yield.getNumOperands() != getNumResults())
    return emitOpError("pyc.yield operand count must match comb results");

  for (auto [v, r] : llvm::zip(yield.getOperands(), getResults())) {
    if (v.getType() != r.getType())
      return emitOpError("pyc.yield operand types must match comb result types");
  }

  return success();
}

#define GET_OP_CLASSES
#include "pyc/Dialect/PYC/PYCOps.cpp.inc"
