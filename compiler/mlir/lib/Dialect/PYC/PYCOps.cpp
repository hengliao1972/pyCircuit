#include "pyc/Dialect/PYC/PYCOps.h"

#include "pyc/Dialect/PYC/PYCDialect.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/OpImplementation.h"
#include "mlir/Interfaces/SideEffectInterfaces.h"
#include "mlir/IR/SymbolTable.h"
#include "mlir/IR/Types.h"
#include "mlir/Support/LogicalResult.h"
#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/StringRef.h"
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
  // Unsigned decimal of any width: getZExtValue() asserts above 64 bits, and
  // streaming the APInt would print it as a signed value.
  llvm::SmallString<40> value;
  getValueAttr().getValue().toStringUnsigned(value);
  p << " " << value << " : " << getType();
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
static bool constMatch(Value v, Pred pred) {
  if (!v)
    return false;
  if (auto c = v.getDefiningOp<ConstantOp>())
    return pred(c.getValueAttr().getValue());
  if (auto c = v.getDefiningOp<arith::ConstantOp>()) {
    auto attr = c.getValue();
    if (auto ia = dyn_cast<IntegerAttr>(attr))
      return pred(ia.getValue());
    return false;
  }
  return false;
}

static bool isConstZero(Value v) { return constMatch(v, [](const APInt &x) { return x.isZero(); }); }
static bool isConstOne(Value v) { return constMatch(v, [](const APInt &x) { return x.isOne(); }); }
static bool isConstAllOnes(Value v) { return constMatch(v, [](const APInt &x) { return x.isAllOnes(); }); }

static IntegerAttr intAttrFor(Type ty, const llvm::APInt &v) {
  auto intTy = dyn_cast<IntegerType>(ty);
  if (!intTy)
    return {};  // non-integer type, cannot create integer constant
  llvm::APInt vv = v;
  if (vv.getBitWidth() != intTy.getWidth())
    vv = vv.zextOrTrunc(intTy.getWidth());
  return IntegerAttr::get(intTy, vv);
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
  auto inTy = dyn_cast<IntegerType>(getIn().getType());
  auto outTy = dyn_cast<IntegerType>(getResult().getType());
  if (!inTy || !outTy)
    return {};
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

static bool carriesExternalObservationIdentity(Operation *op) {
  auto generated = op->getAttrOfType<StringAttr>("pyc.generated");
  const bool skipCycleBalanceName =
      generated && generated.getValue() == "cycle_balance";
  for (NamedAttribute attr : op->getAttrs()) {
    llvm::StringRef name = attr.getName().strref();
    if (name == "pyc.name") {
      if (skipCycleBalanceName)
        continue;
      if (auto lazy = op->getAttrOfType<BoolAttr>("pyc.observe_lazy");
          lazy && lazy.getValue())
        continue;
      return true;
    }
    if (name == "pyc.debug_keep" || name == "pyc.observable" ||
        name.starts_with("pyc.probe") || name.starts_with("pyc.trace"))
      return true;
  }
  return false;
}

void AliasOp::getEffects(
    SmallVectorImpl<SideEffects::EffectInstance<MemoryEffects::Effect>>
        &effects) {
  // Unused observation aliases must survive DCE so probe/VCD can still
  // register the identity after a state rewrite. Comb treats AliasOp as
  // identity anyway; this Write is liveness, not a real store.
  if (carriesExternalObservationIdentity(*this))
    effects.emplace_back(MemoryEffects::Write::get());
}

OpFoldResult AliasOp::fold(FoldAdaptor) {
  // Preserve aliases that carry external observation identity.
  if (carriesExternalObservationIdentity(*this))
    return {};
  return getIn();
}

LogicalResult MuxOp::verify() {
  auto selTy = getSel().getType();
  auto aTy = getA().getType();
  auto bTy = getB().getType();
  if (aTy != bTy)
    return emitOpError("requires a and b to have the same type");
  if (getResult().getType() != aTy)
    return emitOpError("result type must match the a/b type");
  auto selInt = dyn_cast<IntegerType>(selTy);
  if (!selInt || selInt.getWidth() != 1)
    return emitOpError("requires i1 select");
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
  if (auto msbAttr = getMsbAttr()) {
    std::int64_t msb = msbAttr.getInt();
    std::int64_t expected = lsb + static_cast<std::int64_t>(outTy.getWidth()) - 1;
    if (msb != expected)
      return emitOpError("msb must equal lsb + result_width - 1 (expected ")
             << expected << ", got " << msb << ")";
  }
  return success();
}

LogicalResult ShliOp::verify() {
  if (!isa<IntegerType>(getIn().getType()))
    return emitOpError("only supports integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

LogicalResult LshriOp::verify() {
  if (!isa<IntegerType>(getIn().getType()))
    return emitOpError("only supports integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

LogicalResult AshriOp::verify() {
  if (!isa<IntegerType>(getIn().getType()))
    return emitOpError("only supports integer types");
  std::int64_t amt = getAmountAttr().getInt();
  if (amt < 0)
    return emitOpError("amount must be >= 0");
  return success();
}

static LogicalResult verifyDynShift(Operation *op, Type inTyRaw, Type amtTyRaw, Type outTyRaw) {
  // amt must be scalar integer; in/out must be integer
  if (!isa<IntegerType>(inTyRaw) || !isa<IntegerType>(amtTyRaw) || !isa<IntegerType>(outTyRaw))
    return op->emitOpError("only supports integer types (amount must be scalar)");
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

LogicalResult DelayLineOp::verify() {
  auto nextTy = getNext().getType();
  if (getInit().getType() != nextTy)
    return emitOpError("init type must match next type");
  if (getQ().getType() != nextTy)
    return emitOpError("result type must match next type");
  auto depthAttr = (*this)->getAttrOfType<IntegerAttr>("depth");
  if (!depthAttr)
    return emitOpError("requires integer attribute `depth`");
  if (depthAttr.getValue().getSExtValue() <= 1)
    return emitOpError("depth must be > 1");
  return success();
}

void DelayTapOp::getEffects(
    SmallVectorImpl<SideEffects::EffectInstance<MemoryEffects::Effect>>
        &effects) {
  if (carriesExternalObservationIdentity(*this))
    effects.emplace_back(MemoryEffects::Write::get());
}

LogicalResult DelayTapOp::verify() {
  auto line = getLine();
  auto delay = line.getDefiningOp<DelayLineOp>();
  if (!delay)
    return emitOpError("line must be defined by pyc.delay_line");
  auto depthAttr = delay->getAttrOfType<IntegerAttr>("depth");
  if (!depthAttr)
    return emitOpError("line delay_line is missing integer attribute `depth`");
  auto tapAttr = (*this)->getAttrOfType<IntegerAttr>("depth");
  if (!tapAttr)
    return emitOpError("requires integer attribute `depth`");
  int64_t tapDepth = tapAttr.getInt();
  int64_t lineDepth = depthAttr.getInt();
  if (tapDepth <= 0)
    return emitOpError("tap depth must be > 0");
  if (tapDepth > lineDepth)
    return emitOpError("tap depth must not exceed delay_line depth");
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

LogicalResult SyncMemNMOp::verify() {
  auto rdAttr = (*this)->getAttrOfType<IntegerAttr>("rd_ports");
  auto wrAttr = (*this)->getAttrOfType<IntegerAttr>("wr_ports");
  if (!rdAttr || !wrAttr)
    return emitOpError("requires integer attributes `rd_ports`/`wr_ports`");
  int64_t R = rdAttr.getValue().getSExtValue();
  int64_t W = wrAttr.getValue().getSExtValue();
  if (R <= 0 || R > 64)
    return emitOpError("rd_ports must be in 1..64");
  if (W <= 0)
    return emitOpError("wr_ports must be > 0");
  if ((int64_t)getRens().size() != R || (int64_t)getRaddrs().size() != R)
    return emitOpError("rens/raddrs sizes must equal rd_ports");
  if ((int64_t)getResultTypes().size() != R)
    return emitOpError("result count must equal rd_ports");
  if ((int64_t)getWvalids().size() != W || (int64_t)getWaddrs().size() != W ||
      (int64_t)getWdatas().size() != W || (int64_t)getWstrbs().size() != W)
    return emitOpError("write operand group sizes must equal wr_ports");

  auto mode = getReadMode();
  if (mode != "forward" && mode != "old")
    return emitOpError("read_mode must be \"forward\" or \"old\"");

  auto addrTy = dyn_cast<IntegerType>(getRaddrs()[0].getType());
  if (!addrTy)
    return emitOpError("only supports integer address types");
  for (auto v : getRaddrs())
    if (v.getType() != addrTy)
      return emitOpError("all raddrs must share one type");
  for (auto v : getWaddrs())
    if (v.getType() != addrTy)
      return emitOpError("all waddrs must match raddr type");

  auto dataTy = dyn_cast<IntegerType>(getWdatas()[0].getType());
  if (!dataTy)
    return emitOpError("only supports integer data types");
  unsigned dataW = dataTy.getWidth();
  if (dataW == 0)
    return emitOpError("data width must be >= 1");
  for (auto v : getWdatas())
    if (v.getType() != dataTy)
      return emitOpError("all wdatas must share one type");
  for (auto ty : getResultTypes())
    if (ty != dataTy)
      return emitOpError("all rdatas must match wdata type");

  auto strbTy = dyn_cast<IntegerType>(getWstrbs()[0].getType());
  if (!strbTy)
    return emitOpError("only supports integer wstrb types");
  if (strbTy.getWidth() != ((dataW + 7) / 8))
    return emitOpError("wstrb width must be ceil(data_width / 8)");
  for (auto v : getWstrbs())
    if (v.getType() != strbTy)
      return emitOpError("all wstrbs must share one type");

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

  // A pyc.comb body is freely topologically reorderable by code-generation
  // placement passes. Keep that contract explicit instead of trusting the
  // producer that originally formed the region.
  //
  // Observation aliases report Write only so unused probe names survive DCE.
  // They are still identity ops and stay safe to reorder.
  for (Operation &op : b.without_terminator()) {
    if (op.getNumRegions() != 0)
      return emitOpError("body operation ")
             << op.getName() << " must not contain nested regions";
    if (!isMemoryEffectFree(&op) && !isa<AliasOp>(&op))
      return emitOpError("body operation ")
             << op.getName() << " must be memory-effect-free";
  }

  return success();
}

//===----------------------------------------------------------------------===//
// Element-wise binary op verifiers
//===----------------------------------------------------------------------===//

static LogicalResult verifyElementwiseBinary(Operation *op,
                                             Type lhsTy,
                                             Type rhsTy,
                                             Type resultTy,
                                             bool compareResult) {
  auto lhsInt = dyn_cast<IntegerType>(lhsTy);
  auto rhsInt = dyn_cast<IntegerType>(rhsTy);
  if (!lhsInt || !rhsInt)
    return op->emitOpError("operands must be integer");
  if (lhsInt.getWidth() != rhsInt.getWidth())
    return op->emitOpError("operand integer widths must match: ")
           << lhsInt << " vs " << rhsInt;

  Type expected = compareResult ? Type(IntegerType::get(op->getContext(), 1)) : lhsTy;
  if (resultTy != expected)
    return op->emitOpError("result type must be ") << expected
           << " (lhs " << lhsTy << ", rhs " << rhsTy
           << ", actual " << resultTy << ")";
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


#define GET_OP_CLASSES
#include "pyc/Dialect/PYC/PYCOps.cpp.inc"
