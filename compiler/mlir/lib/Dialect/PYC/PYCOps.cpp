#include "pyc/Dialect/PYC/PYCOps.h"

#include "pyc/Dialect/PYC/PYCDialect.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/OpImplementation.h"
#include "mlir/IR/SymbolTable.h"
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

template <typename Pred>
static bool allDenseIntElementsMatch(DenseIntElementsAttr dense, Pred pred) {
  for (const APInt &e : dense.getValues<APInt>()) {
    if (!pred(e))
      return false;
  }
  return true;
}

template <typename Pred>
static bool vectorConstMatch(Value v, Pred pred) {
  if (!v)
    return false;
  if (auto c = v.getDefiningOp<ConstantOp>())
    return pred(c.getValueAttr().getValue());
  if (auto c = v.getDefiningOp<arith::ConstantOp>()) {
    auto attr = c.getValue();
    if (auto ia = dyn_cast<IntegerAttr>(attr))
      return pred(ia.getValue());
    if (auto dense = dyn_cast<DenseIntElementsAttr>(attr))
      return allDenseIntElementsMatch(dense, pred);
    return false;
  }
  if (auto b = v.getDefiningOp<VBroadcastOp>())
    return vectorConstMatch(b.getScalar(), pred);
  if (auto c = v.getDefiningOp<VCreateOp>()) {
    for (Value e : c.getElements()) {
      if (!vectorConstMatch(e, pred))
        return false;
    }
    return true;
  }
  return false;
}

static bool isConstZero(Value v) { return vectorConstMatch(v, [](const APInt &x) { return x.isZero(); }); }
static bool isConstOne(Value v) { return vectorConstMatch(v, [](const APInt &x) { return x.isOne(); }); }
static bool isConstAllOnes(Value v) { return vectorConstMatch(v, [](const APInt &x) { return x.isAllOnes(); }); }

static IntegerAttr intAttrFor(Type ty, const llvm::APInt &v) {
  auto intTy = dyn_cast<IntegerType>(ty);
  if (!intTy)
    return {};  // vector or non-integer type, cannot create integer constant
  llvm::APInt vv = v;
  if (vv.getBitWidth() != intTy.getWidth())
    vv = vv.zextOrTrunc(intTy.getWidth());
  return IntegerAttr::get(intTy, vv);
}

static std::optional<llvm::APInt> intConstOfValue(Value v, unsigned width) {
  if (!v)
    return std::nullopt;
  if (auto c = v.getDefiningOp<ConstantOp>())
    return c.getValueAttr().getValue().zextOrTrunc(width);
  if (auto c = v.getDefiningOp<arith::ConstantOp>()) {
    if (auto ia = dyn_cast<IntegerAttr>(c.getValue()))
      return ia.getValue().zextOrTrunc(width);
  }
  return std::nullopt;
}

static OpFoldResult foldValueIfResultTypeMatches(Value v, Type resultTy) {
  if (v && v.getType() == resultTy)
    return v;
  return {};
}

OpFoldResult AddOp::fold(FoldAdaptor adaptor) {
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    if (isConstOne(getLhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstOne(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstOne(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstOne(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    if (isConstAllOnes(getLhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstAllOnes(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstAllOnes(getLhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    if (isConstAllOnes(getRhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    return {};
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) {
    if (isConstZero(getLhs()))
      return foldValueIfResultTypeMatches(getRhs(), getResult().getType());
    if (isConstZero(getRhs()))
      return foldValueIfResultTypeMatches(getLhs(), getResult().getType());
    return {};
  }
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
  if (auto inner = getIn().getDefiningOp<NotOp>())
    return inner.getIn();
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) return {};
  auto a = asIntAttr(adaptor.getIn());
  if (a)
    return intAttrFor(outTy, (~(*a)).trunc(outTy.getWidth()));
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
  if (!isa<IntegerType>(getResult().getType()))
    return {};
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
  if (!isa<IntegerType>(getResult().getType()))
    return {};
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
  if (!isa<IntegerType>(getResult().getType()))
    return {};
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
  if (auto z = getIn().getDefiningOp<ZextOp>()) {
    if (z.getIn().getType() == getResult().getType())
      return z.getIn();
  }
  if (auto s = getIn().getDefiningOp<SextOp>()) {
    if (s.getIn().getType() == getResult().getType())
      return s.getIn();
  }
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    auto outTy = dyn_cast<IntegerType>(getResult().getType());
    if (!outTy) return {};
    return intAttrFor(getResult().getType(), a->trunc(outTy.getWidth()));
  }
  return {};
}

OpFoldResult ZextOp::fold(FoldAdaptor adaptor) {
  if (getIn().getType() == getResult().getType())
    return getIn();
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    auto outTy = dyn_cast<IntegerType>(getResult().getType());
    if (!outTy) return {};
    return intAttrFor(getResult().getType(), a->zext(outTy.getWidth()));
  }
  return {};
}

OpFoldResult SextOp::fold(FoldAdaptor adaptor) {
  if (getIn().getType() == getResult().getType())
    return getIn();
  auto a = asIntAttr(adaptor.getIn());
  if (a) {
    auto outTy = dyn_cast<IntegerType>(getResult().getType());
    if (!outTy) return {};
    return intAttrFor(getResult().getType(), a->sext(outTy.getWidth()));
  }
  return {};
}

OpFoldResult ExtractOp::fold(FoldAdaptor adaptor) {
  auto inTy = cast<IntegerType>(getIn().getType());
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) return {};
  std::int64_t lsb = getLsbAttr().getInt();
  if (lsb == 0 && outTy.getWidth() == inTy.getWidth())
    return getIn();
  if (auto c = getIn().getDefiningOp<ConcatOp>()) {
    auto cTy = cast<IntegerType>(c.getResult().getType());
    std::int64_t pos = static_cast<std::int64_t>(cTy.getWidth());
    for (Value v : c.getInputs()) {
      auto vTy = cast<IntegerType>(v.getType());
      pos -= static_cast<std::int64_t>(vTy.getWidth());
      if (pos == lsb && vTy.getWidth() == outTy.getWidth())
        return v;
    }
  }
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) return {};
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) return {};
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
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) return {};
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

  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!outTy) return {};
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

enum class VectorReduceKind { Or, And, Add };

template <typename ReduceOp>
static OpFoldResult foldVectorReduce(ReduceOp op, Attribute adaptedVec, VectorReduceKind kind) {
  auto vecTy = dyn_cast<VectorType>(op.getVec().getType());
  if (!vecTy)
    return {};
  auto elemTy = dyn_cast<IntegerType>(vecTy.getElementType());
  if (!elemTy)
    return {};
  int64_t dim = op.getDim().value_or(0);
  if (dim < 0 || dim >= vecTy.getRank())
    return {};

  if (auto c = op.getVec().template getDefiningOp<VCreateOp>()) {
    if (vecTy.getRank() == 1 && c.getElements().size() == 1)
      return c.getElements().front();
    if (vecTy.getRank() == 2 && dim == 0 && c.getElements().size() == 1)
      return c.getElements().front();
  }

  auto reduceAPInt = [&](llvm::APInt acc, const llvm::APInt &e) {
    if (kind == VectorReduceKind::Or)
      acc |= e.zextOrTrunc(acc.getBitWidth());
    else if (kind == VectorReduceKind::And)
      acc &= e.zextOrTrunc(acc.getBitWidth());
    else
      acc = (acc + e.zextOrTrunc(acc.getBitWidth())).trunc(acc.getBitWidth());
    return acc;
  };

  if (auto dense = dyn_cast_or_null<DenseIntElementsAttr>(adaptedVec)) {
    unsigned width = elemTy.getWidth();
    llvm::APInt identity =
        kind == VectorReduceKind::And ? llvm::APInt::getAllOnes(width) : llvm::APInt(width, 0);
    if (vecTy.getRank() == 1) {
      llvm::APInt acc = identity;
      for (const APInt &e : dense.getValues<APInt>())
        acc = reduceAPInt(acc, e);
      return intAttrFor(op.getResult().getType(), acc);
    }
    if (vecTy.getRank() == 2) {
      auto resultVT = dyn_cast<VectorType>(op.getResult().getType());
      if (!resultVT)
        return {};
      int64_t rows = vecTy.getShape()[0];
      int64_t cols = vecTy.getShape()[1];
      SmallVector<APInt> inputVals;
      for (const APInt &e : dense.getValues<APInt>())
        inputVals.push_back(e.zextOrTrunc(width));
      SmallVector<APInt> outVals;
      if (dim == 0) {
        outVals.reserve(cols);
        for (int64_t c = 0; c < cols; ++c) {
          llvm::APInt acc = identity;
          for (int64_t r = 0; r < rows; ++r)
            acc = reduceAPInt(acc, inputVals[r * cols + c]);
          outVals.push_back(acc);
        }
      } else if (dim == 1) {
        outVals.reserve(rows);
        for (int64_t r = 0; r < rows; ++r) {
          llvm::APInt acc = identity;
          for (int64_t c = 0; c < cols; ++c)
            acc = reduceAPInt(acc, inputVals[r * cols + c]);
          outVals.push_back(acc);
        }
      } else {
        return {};
      }
      return DenseIntElementsAttr::get(resultVT, outVals);
    }
  }

  auto outTy = dyn_cast<IntegerType>(op.getResult().getType());
  if (!outTy)
    return {};

  unsigned width = outTy.getWidth();
  if (isConstZero(op.getVec()))
    return intAttrFor(outTy, llvm::APInt(width, 0));
  if (kind == VectorReduceKind::And && isConstAllOnes(op.getVec()))
    return intAttrFor(outTy, llvm::APInt::getAllOnes(width));

  if (kind != VectorReduceKind::Add) {
    if (auto b = op.getVec().template getDefiningOp<VBroadcastOp>())
      return b.getScalar();
  }
  if (auto c = op.getVec().template getDefiningOp<VCreateOp>()) {
    llvm::APInt acc =
        kind == VectorReduceKind::And ? llvm::APInt::getAllOnes(width) : llvm::APInt(width, 0);
    bool allConst = true;
    for (Value e : c.getElements()) {
      auto ei = intConstOfValue(e, width);
      if (!ei) {
        allConst = false;
        break;
      }
      if (kind == VectorReduceKind::Or)
        acc |= *ei;
      else if (kind == VectorReduceKind::And)
        acc &= *ei;
      else
        acc = (acc + *ei).trunc(width);
    }
    if (allConst)
      return intAttrFor(outTy, acc);
  }

  return {};
}

OpFoldResult VOrReduceOp::fold(FoldAdaptor adaptor) {
  return foldVectorReduce(*this, adaptor.getVec(), VectorReduceKind::Or);
}

OpFoldResult VAndReduceOp::fold(FoldAdaptor adaptor) {
  return foldVectorReduce(*this, adaptor.getVec(), VectorReduceKind::And);
}

OpFoldResult VAddReduceOp::fold(FoldAdaptor adaptor) {
  return foldVectorReduce(*this, adaptor.getVec(), VectorReduceKind::Add);
}

OpFoldResult AliasOp::fold(FoldAdaptor) {
  // Preserve alias ops that carry a debug name (used for codegen name mangling).
  if (auto nAttr = (*this)->getAttrOfType<StringAttr>("pyc.name"))
    return {};
  return getIn();
}

LogicalResult MuxOp::verify() {
  auto selTy = getSel().getType();
  auto aTy = getA().getType();
  auto bTy = getB().getType();
  auto rTy = getResult().getType();
  if (aTy != bTy)
    return emitOpError("requires a and b to have the same type");
  if (rTy != aTy)
    return emitOpError("result type must match a/b type");
  if (auto selI1 = dyn_cast<IntegerType>(selTy)) {
    if (selI1.getWidth() != 1)
      return emitOpError("requires i1 select");
    return success();
  }
  auto selVT = dyn_cast<VectorType>(selTy);
  auto valueVT = dyn_cast<VectorType>(aTy);
  if (!selVT || !valueVT)
    return emitOpError("requires i1 select or same-shape vector-of-i1 select for vector a/b");
  auto selElemTy = dyn_cast<IntegerType>(selVT.getElementType());
  if (!selElemTy || selElemTy.getWidth() != 1)
    return emitOpError("vector select element type must be i1");
  if (selVT.getShape() != valueVT.getShape())
    return emitOpError("vector select shape must match a/b shape: ") << selVT << " vs " << valueVT;
  return success();
}

LogicalResult NotOp::verify() {
  if (getIn().getType() != getResult().getType())
    return emitOpError("result type must match input type");
  return success();
}

/// Returns the scalar element type if both types are integer or vector-of-integer
/// with matching shapes, or null if the types are incompatible.
static Type matchVectorShape(Type inTyRaw, Type outTyRaw) {
  auto inVT = dyn_cast<VectorType>(inTyRaw);
  auto outVT = dyn_cast<VectorType>(outTyRaw);
  if (inVT && outVT) {
    if (inVT.getShape() != outVT.getShape())
      return {};
    return matchVectorShape(inVT.getElementType(), outVT.getElementType());
  }
  if (inVT || outVT)
    return {};
  return inTyRaw;
}

static LogicalResult verifyIntCast(Operation *op, Type inTyRaw, Type outTyRaw, bool requireWiden, bool signExtend) {
  (void)signExtend;
  // Check structural compatibility (same vector shape if any).
  Type scalarIn = matchVectorShape(inTyRaw, outTyRaw);
  if (!scalarIn)
    return op->emitOpError("incompatible types for width cast");
  auto inTy = dyn_cast<IntegerType>(scalarIn);
  auto outTy = dyn_cast<IntegerType>(matchVectorShape(outTyRaw, inTyRaw));
  if (!inTy || !outTy)
    return op->emitOpError("only supports integer or vector-of-integer types");
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
  if (!isa<IntegerType, VectorType>(getIn().getType()))
    return emitOpError("only supports integer or vector-of-integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

LogicalResult LshriOp::verify() {
  if (!isa<IntegerType, VectorType>(getIn().getType()))
    return emitOpError("only supports integer or vector-of-integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

LogicalResult AshriOp::verify() {
  if (!isa<IntegerType, VectorType>(getIn().getType()))
    return emitOpError("only supports integer or vector-of-integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

static LogicalResult verifyDynShift(Operation *op, Type inTyRaw, Type amtTyRaw, Type outTyRaw) {
  // amt must be scalar integer; in/out can be integer or vector-of-integer
  if (!isa<IntegerType, VectorType>(inTyRaw) || !isa<IntegerType>(amtTyRaw) || !isa<IntegerType, VectorType>(outTyRaw))
    return op->emitOpError("only supports integer or vector-of-integer types (amount must be scalar)");
  if (outTyRaw != inTyRaw)
    return op->emitOpError("result type must match input type");
  return success();
}

LogicalResult ShlOp::verify() { return verifyDynShift(*this, getIn().getType(), getAmount().getType(), getResult().getType()); }

LogicalResult LshrOp::verify() { return verifyDynShift(*this, getIn().getType(), getAmount().getType(), getResult().getType()); }

LogicalResult AshrOp::verify() { return verifyDynShift(*this, getIn().getType(), getAmount().getType(), getResult().getType()); }

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
  if (dataW == 0)
    return emitOpError("data width must be >= 1");

  auto strbTy = dyn_cast<IntegerType>(getWstrb().getType());
  if (!strbTy)
    return emitOpError("only supports integer wstrb types");
  if (strbTy.getWidth() != ((dataW + 7) / 8))
    return emitOpError("wstrb width must be ceil(data_width / 8)");

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
  if (dataW == 0)
    return emitOpError("data width must be >= 1");

  auto strbTy = dyn_cast<IntegerType>(getWstrb().getType());
  if (!strbTy)
    return emitOpError("only supports integer wstrb types");
  if (strbTy.getWidth() != ((dataW + 7) / 8))
    return emitOpError("wstrb width must be ceil(data_width / 8)");

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
  if (dataW == 0)
    return emitOpError("data width must be >= 1");

  auto strbTy = dyn_cast<IntegerType>(getWstrb().getType());
  if (!strbTy)
    return emitOpError("only supports integer wstrb types");
  if (strbTy.getWidth() != ((dataW + 7) / 8))
    return emitOpError("wstrb width must be ceil(data_width / 8)");

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

LogicalResult InstanceOp::verify() {
  auto calleeAttr = getCalleeAttr();
  if (!calleeAttr)
    return emitOpError("requires FlatSymbolRefAttr attribute `callee`");

  auto module = (*this)->getParentOfType<ModuleOp>();
  if (!module)
    return emitOpError("must be contained in an MLIR module");

  Operation *sym = SymbolTable::lookupSymbolIn(module, calleeAttr);
  auto callee = dyn_cast_or_null<func::FuncOp>(sym);
  if (!callee)
    return emitOpError("callee must reference a func.func");

  FunctionType ft = callee.getFunctionType();
  if (ft.getNumInputs() != getNumOperands())
    return emitOpError("operand count does not match callee signature");
  if (ft.getNumResults() != getNumResults())
    return emitOpError("result count does not match callee signature");

  for (auto [i, ty] : llvm::enumerate(ft.getInputs())) {
    if (getOperand(i).getType() != ty)
      return emitOpError() << "operand type mismatch at #" << i << ": got " << getOperand(i).getType()
                           << " expected " << ty;
  }
  for (auto [i, ty] : llvm::enumerate(ft.getResults())) {
    if (getResult(i).getType() != ty)
      return emitOpError() << "result type mismatch at #" << i << ": got " << getResult(i).getType()
                           << " expected " << ty;
  }

  if (auto n = getNameAttr()) {
    if (n.getValue().empty())
      return emitOpError("name must be non-empty when provided");
  }

  return success();
}

LogicalResult AssertOp::verify() {
  if (auto m = getMsgAttr()) {
    if (m.getValue().empty())
      return emitOpError("msg must be non-empty when provided");
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

//===----------------------------------------------------------------------===//
// Element-wise binary op verifiers (vector-aware, scalar broadcast)
//===----------------------------------------------------------------------===//

static IntegerType leafIntegerType(Type ty) {
  if (auto intTy = dyn_cast<IntegerType>(ty))
    return intTy;
  if (auto vt = dyn_cast<VectorType>(ty))
    return dyn_cast<IntegerType>(vt.getElementType());
  return {};
}

static Type elementwiseValueResultType(MLIRContext *ctx, Type lhsTy, Type rhsTy) {
  (void)ctx;
  auto lhsVT = dyn_cast<VectorType>(lhsTy);
  auto rhsVT = dyn_cast<VectorType>(rhsTy);
  return lhsVT ? Type(lhsVT) : (rhsVT ? Type(rhsVT) : lhsTy);
}

static Type elementwiseCompareResultType(MLIRContext *ctx, Type lhsTy, Type rhsTy) {
  Type shapeTy = elementwiseValueResultType(ctx, lhsTy, rhsTy);
  if (auto vt = dyn_cast<VectorType>(shapeTy))
    return VectorType::get(vt.getShape(), IntegerType::get(ctx, 1));
  return IntegerType::get(ctx, 1);
}

static LogicalResult verifyElementwiseBinary(Operation *op,
                                             Type lhsTy,
                                             Type rhsTy,
                                             Type resultTy,
                                             bool compareResult) {
  auto lhsLeaf = leafIntegerType(lhsTy);
  auto rhsLeaf = leafIntegerType(rhsTy);
  if (!lhsLeaf || !rhsLeaf)
    return op->emitOpError("operands must be integer or vector-of-integer");
  if (lhsLeaf.getWidth() != rhsLeaf.getWidth())
    return op->emitOpError("operand leaf integer widths must match: ")
           << lhsLeaf << " vs " << rhsLeaf;

  auto lhsVT = dyn_cast<VectorType>(lhsTy);
  auto rhsVT = dyn_cast<VectorType>(rhsTy);
  if (lhsVT && rhsVT && lhsVT.getShape() != rhsVT.getShape())
    return op->emitOpError("vector operand shapes must match for element-wise op: ")
           << lhsVT << " vs " << rhsVT;

  Type expected = compareResult
                      ? elementwiseCompareResultType(op->getContext(), lhsTy, rhsTy)
                      : elementwiseValueResultType(op->getContext(), lhsTy, rhsTy);
  if (resultTy != expected)
    return op->emitOpError("result type must be ") << expected;
  return success();
}

#define DEFINE_VALUE_BINARY_VERIFY(OP)                                                   \
  LogicalResult OP::verify() {                                                           \
    return verifyElementwiseBinary(getOperation(), getLhs().getType(), getRhs().getType(), \
                                   getResult().getType(), /*compareResult=*/false);       \
  }

DEFINE_VALUE_BINARY_VERIFY(AddOp)
DEFINE_VALUE_BINARY_VERIFY(SubOp)
DEFINE_VALUE_BINARY_VERIFY(MulOp)
DEFINE_VALUE_BINARY_VERIFY(UdivOp)
DEFINE_VALUE_BINARY_VERIFY(UremOp)
DEFINE_VALUE_BINARY_VERIFY(SdivOp)
DEFINE_VALUE_BINARY_VERIFY(SremOp)
DEFINE_VALUE_BINARY_VERIFY(AndOp)
DEFINE_VALUE_BINARY_VERIFY(OrOp)
DEFINE_VALUE_BINARY_VERIFY(XorOp)

#undef DEFINE_VALUE_BINARY_VERIFY

#define DEFINE_COMPARE_BINARY_VERIFY(OP)                                                 \
  LogicalResult OP::verify() {                                                           \
    return verifyElementwiseBinary(getOperation(), getLhs().getType(), getRhs().getType(), \
                                   getResult().getType(), /*compareResult=*/true);        \
  }

DEFINE_COMPARE_BINARY_VERIFY(EqOp)
DEFINE_COMPARE_BINARY_VERIFY(UltOp)
DEFINE_COMPARE_BINARY_VERIFY(SltOp)

#undef DEFINE_COMPARE_BINARY_VERIFY

//===----------------------------------------------------------------------===//
// Vector op verifiers
//===----------------------------------------------------------------------===//

LogicalResult VGetOp::verify() {
  auto vecTy = dyn_cast<VectorType>(getVec().getType());
  if (!vecTy || vecTy.getRank() < 1)
    return emitOpError("vec operand must have vector type");
  std::int64_t idx = getIndexAttr().getInt();
  if (idx < 0 || idx >= vecTy.getDimSize(0))
    return emitOpError("index out of range: ") << idx
      << " (outer dim size is " << vecTy.getDimSize(0) << ")";
  // Result: drop outermost dimension. vector<4x8xi32>[2] → vector<8xi32>.
  auto shape = vecTy.getShape().drop_front();
  Type expectedResult;
  if (shape.empty())
    expectedResult = vecTy.getElementType();
  else
    expectedResult = VectorType::get(shape, vecTy.getElementType());
  if (getResult().getType() != expectedResult)
    return emitOpError("result type must be ") << expectedResult;
  return success();
}

LogicalResult VCreateOp::verify() {
  if (getElements().empty())
    return emitOpError("requires at least one element");
  auto resultVT = dyn_cast<VectorType>(getResult().getType());
  if (!resultVT)
    return emitOpError("result type must be a vector");
  if (static_cast<std::uint64_t>(resultVT.getDimSize(0)) != getElements().size())
    return emitOpError("element count (") << getElements().size()
           << ") must match result vector size (" << resultVT.getDimSize(0) << ")";
  Type firstTy = getElements().front().getType();
  if (!isa<IntegerType, VectorType>(firstTy))
    return emitOpError("elements must be integer or vector-of-integer");
  Type expectedResult;
  if (auto elemVT = dyn_cast<VectorType>(firstTy)) {
    auto elemTy = dyn_cast<IntegerType>(elemVT.getElementType());
    if (!elemTy)
      return emitOpError("vector element leaf type must be integer");
    SmallVector<int64_t> shape;
    shape.push_back(static_cast<int64_t>(getElements().size()));
    llvm::append_range(shape, elemVT.getShape());
    expectedResult = VectorType::get(shape, elemTy);
  } else {
    expectedResult = VectorType::get({static_cast<int64_t>(getElements().size())}, firstTy);
  }
  if (getResult().getType() != expectedResult)
    return emitOpError("result type must be ") << expectedResult;
  for (auto [i, el] : llvm::enumerate(getElements())) {
    if (el.getType() != firstTy)
      return emitOpError("element #") << i << " type must match first element type: "
               << el.getType() << " vs " << firstTy;
  }
  return success();
}

LogicalResult VBroadcastOp::verify() {
  auto scalarTy = dyn_cast<IntegerType>(getScalar().getType());
  if (!scalarTy)
    return emitOpError("scalar operand must have integer type");
  auto resultVT = dyn_cast<VectorType>(getResult().getType());
  if (!resultVT || resultVT.getRank() != 1)
    return emitOpError("result type must be a 1-D vector");
  if (resultVT.getDimSize(0) != getSizeAttr().getInt())
    return emitOpError("result vector size must match `size` attribute");
  if (resultVT.getElementType() != scalarTy)
    return emitOpError("result element type must match scalar type");
  return success();
}

static LogicalResult verifyVectorReduce(Operation *op, Value vec, std::optional<int64_t> dimAttr, Type resultTy) {
  auto vecTy = dyn_cast<VectorType>(vec.getType());
  if (!vecTy)
    return op->emitOpError("vec operand must have vector type");
  auto elemTy = dyn_cast<IntegerType>(vecTy.getElementType());
  if (!elemTy)
    return op->emitOpError("vec element type must be integer");
  if (vecTy.getRank() < 1 || vecTy.getRank() > 2)
    return op->emitOpError("currently supports rank-1/rank-2 vectors");
  for (int64_t d : vecTy.getShape()) {
    if (d <= 0)
      return op->emitOpError("vec must have non-empty dimensions");
  }
  int64_t dim = dimAttr.value_or(0);
  if (dim < 0 || dim >= vecTy.getRank())
    return op->emitOpError("dim out of range: ") << dim << " for rank " << vecTy.getRank();
  if (!dimAttr && vecTy.getRank() != 1)
    return op->emitOpError("omitted dim is only valid for rank-1 vectors");

  SmallVector<int64_t> outShape;
  for (auto [i, d] : llvm::enumerate(vecTy.getShape())) {
    if (static_cast<int64_t>(i) != dim)
      outShape.push_back(d);
  }
  Type expectedResult = outShape.empty()
                            ? Type(elemTy)
                            : Type(VectorType::get(outShape, elemTy));
  if (resultTy != expectedResult)
    return op->emitOpError("result type must be ") << expectedResult;
  return success();
}

LogicalResult VOrReduceOp::verify() {
  return verifyVectorReduce(getOperation(), getVec(), getDim(), getResult().getType());
}

LogicalResult VAndReduceOp::verify() {
  return verifyVectorReduce(getOperation(), getVec(), getDim(), getResult().getType());
}

LogicalResult VAddReduceOp::verify() {
  return verifyVectorReduce(getOperation(), getVec(), getDim(), getResult().getType());
}

#define GET_OP_CLASSES
#include "pyc/Dialect/PYC/PYCOps.cpp.inc"
