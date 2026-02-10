#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/PatternMatch.h"
#include "mlir/Pass/Pass.h"
#include "mlir/Transforms/GreedyPatternRewriteDriver.h"

using namespace mlir;

namespace pyc {
namespace {

static std::optional<uint64_t> getConstUInt(Value v) {
  if (auto c = v.getDefiningOp<pyc::ConstantOp>())
    return c.getValueAttr().getValue().getZExtValue();
  if (auto c = v.getDefiningOp<arith::ConstantOp>()) {
    if (auto i = dyn_cast<IntegerAttr>(c.getValue()))
      return i.getValue().getZExtValue();
  }
  return std::nullopt;
}

static Value stripAlias(Value v) {
  while (auto a = v.getDefiningOp<pyc::AliasOp>())
    v = a.getIn();
  return v;
}

static std::pair<Value, bool> stripNot(Value v) {
  v = stripAlias(v);
  if (auto n = v.getDefiningOp<pyc::NotOp>())
    return {stripAlias(n.getIn()), true};
  return {v, false};
}

static bool andMatches(pyc::AndOp op, Value a, bool aNot, Value b, bool bNot) {
  auto [lBase, lNot] = stripNot(op.getLhs());
  auto [rBase, rNot] = stripNot(op.getRhs());
  return (lBase == a && lNot == aNot && rBase == b && rNot == bNot) ||
         (lBase == b && lNot == bNot && rBase == a && rNot == aNot);
}

static Value constInt(PatternRewriter &rewriter, Location loc, Type ty, const llvm::APInt &v) {
  auto intTy = dyn_cast<IntegerType>(ty);
  if (!intTy)
    return {};
  llvm::APInt vv = v;
  if (vv.getBitWidth() != intTy.getWidth())
    vv = vv.zextOrTrunc(intTy.getWidth());
  return rewriter.create<pyc::ConstantOp>(loc, intTy, IntegerAttr::get(intTy, vv));
}

struct MuxSameSelSimplify : public OpRewritePattern<pyc::MuxOp> {
  using OpRewritePattern::OpRewritePattern;

  LogicalResult matchAndRewrite(pyc::MuxOp op, PatternRewriter &rewriter) const override {
    Value sel = op.getSel();

    // sel ? (sel ? x : y) : z  ==>  sel ? x : z
    if (auto aMux = op.getA().getDefiningOp<pyc::MuxOp>()) {
      if (aMux.getSel() == sel) {
        rewriter.replaceOpWithNewOp<pyc::MuxOp>(op, op.getType(), sel, aMux.getA(), op.getB());
        return success();
      }
    }

    // sel ? x : (sel ? y : z)  ==>  sel ? x : z
    if (auto bMux = op.getB().getDefiningOp<pyc::MuxOp>()) {
      if (bMux.getSel() == sel) {
        rewriter.replaceOpWithNewOp<pyc::MuxOp>(op, op.getType(), sel, op.getA(), bMux.getB());
        return success();
      }
    }

    // (~sel) ? a : b  ==>  sel ? b : a
    if (auto n = sel.getDefiningOp<pyc::NotOp>()) {
      rewriter.replaceOpWithNewOp<pyc::MuxOp>(op, op.getType(), n.getIn(), op.getB(), op.getA());
      return success();
    }

    return failure();
  }
};

struct MuxI1ToLogic : public OpRewritePattern<pyc::MuxOp> {
  using OpRewritePattern::OpRewritePattern;

  LogicalResult matchAndRewrite(pyc::MuxOp op, PatternRewriter &rewriter) const override {
    auto ty = dyn_cast<IntegerType>(op.getType());
    if (!ty || ty.getWidth() != 1)
      return failure();

    auto aConst = getConstUInt(op.getA());
    auto bConst = getConstUInt(op.getB());

    if (aConst && bConst) {
      uint64_t a = (*aConst) & 1;
      uint64_t b = (*bConst) & 1;
      if (a == b) {
        rewriter.replaceOp(op, a ? op.getA() : op.getB());
        return success();
      }
      if (a == 1 && b == 0) {
        rewriter.replaceOp(op, op.getSel());
        return success();
      }
      if (a == 0 && b == 1) {
        rewriter.replaceOpWithNewOp<pyc::NotOp>(op, op.getSel());
        return success();
      }
      return failure();
    }

    // For i1, convert muxes with constant arms into simple gates.
    if (bConst && (((*bConst) & 1) == 0)) {
      // sel ? a : 0  ==>  sel & a
      rewriter.replaceOpWithNewOp<pyc::AndOp>(op, op.getSel(), op.getA());
      return success();
    }
    if (aConst && (((*aConst) & 1) == 1)) {
      // sel ? 1 : b  ==>  sel | b
      rewriter.replaceOpWithNewOp<pyc::OrOp>(op, op.getSel(), op.getB());
      return success();
    }
    if (bConst && (((*bConst) & 1) == 1)) {
      // sel ? a : 1  ==>  (~sel) | a
      Value nsel = rewriter.create<pyc::NotOp>(op.getLoc(), op.getSel());
      rewriter.replaceOpWithNewOp<pyc::OrOp>(op, nsel, op.getA());
      return success();
    }
    if (aConst && (((*aConst) & 1) == 0)) {
      // sel ? 0 : b  ==>  (~sel) & b
      Value nsel = rewriter.create<pyc::NotOp>(op.getLoc(), op.getSel());
      rewriter.replaceOpWithNewOp<pyc::AndOp>(op, nsel, op.getB());
      return success();
    }

    return failure();
  }
};

struct AndBasicSimplify : public OpRewritePattern<pyc::AndOp> {
  using OpRewritePattern::OpRewritePattern;

  LogicalResult matchAndRewrite(pyc::AndOp op, PatternRewriter &rewriter) const override {
    Value lhs = stripAlias(op.getLhs());
    Value rhs = stripAlias(op.getRhs());

    // a & a ==> a
    if (lhs == rhs) {
      rewriter.replaceOp(op, op.getLhs());
      return success();
    }

    // a & ~a ==> 0
    auto [lb, ln] = stripNot(lhs);
    auto [rb, rn] = stripNot(rhs);
    if ((lb == rb) && (ln != rn)) {
      Value z = constInt(rewriter, op.getLoc(), op.getType(), llvm::APInt(1, 0));
      if (!z)
        return failure();
      rewriter.replaceOp(op, z);
      return success();
    }

    return failure();
  }
};

struct OrBasicSimplify : public OpRewritePattern<pyc::OrOp> {
  using OpRewritePattern::OpRewritePattern;

  LogicalResult matchAndRewrite(pyc::OrOp op, PatternRewriter &rewriter) const override {
    Value lhs = stripAlias(op.getLhs());
    Value rhs = stripAlias(op.getRhs());

    // a | a ==> a
    if (lhs == rhs) {
      rewriter.replaceOp(op, op.getLhs());
      return success();
    }

    // a | ~a ==> all-ones
    auto [lb, ln] = stripNot(lhs);
    auto [rb, rn] = stripNot(rhs);
    if ((lb == rb) && (ln != rn)) {
      auto intTy = dyn_cast<IntegerType>(op.getType());
      if (!intTy)
        return failure();
      Value ones = constInt(rewriter, op.getLoc(), intTy, llvm::APInt::getAllOnes(intTy.getWidth()));
      if (!ones)
        return failure();
      rewriter.replaceOp(op, ones);
      return success();
    }

    return failure();
  }
};

struct OrAndXorFactor : public OpRewritePattern<pyc::OrOp> {
  using OpRewritePattern::OpRewritePattern;

  LogicalResult matchAndRewrite(pyc::OrOp op, PatternRewriter &rewriter) const override {
    Value lhs = stripAlias(op.getLhs());
    Value rhs = stripAlias(op.getRhs());
    auto a0 = lhs.getDefiningOp<pyc::AndOp>();
    auto a1 = rhs.getDefiningOp<pyc::AndOp>();
    if (!a0 || !a1)
      return failure();

    // Try candidates based on the first AND's operands.
    auto [x0, _x0n] = stripNot(a0.getLhs());
    auto [x1, _x1n] = stripNot(a0.getRhs());
    llvm::SmallVector<std::pair<Value, Value>, 2> cands;
    cands.push_back({x0, x1});
    if (x0 != x1)
      cands.push_back({x1, x0});

    for (auto [A, B] : cands) {
      // XOR: (A & ~B) | (~A & B) ==> A ^ B
      if (andMatches(a0, A, /*aNot=*/false, B, /*bNot=*/true) && andMatches(a1, A, /*aNot=*/true, B, /*bNot=*/false)) {
        rewriter.replaceOpWithNewOp<pyc::XorOp>(op, op.getType(), A, B);
        return success();
      }
      if (andMatches(a0, A, /*aNot=*/true, B, /*bNot=*/false) && andMatches(a1, A, /*aNot=*/false, B, /*bNot=*/true)) {
        rewriter.replaceOpWithNewOp<pyc::XorOp>(op, op.getType(), A, B);
        return success();
      }

      // XNOR: (A & B) | (~A & ~B) ==> ~(A ^ B)
      if (andMatches(a0, A, /*aNot=*/false, B, /*bNot=*/false) && andMatches(a1, A, /*aNot=*/true, B, /*bNot=*/true)) {
        Value x = rewriter.create<pyc::XorOp>(op.getLoc(), op.getType(), A, B);
        rewriter.replaceOpWithNewOp<pyc::NotOp>(op, op.getType(), x);
        return success();
      }
      if (andMatches(a0, A, /*aNot=*/true, B, /*bNot=*/true) && andMatches(a1, A, /*aNot=*/false, B, /*bNot=*/false)) {
        Value x = rewriter.create<pyc::XorOp>(op.getLoc(), op.getType(), A, B);
        rewriter.replaceOpWithNewOp<pyc::NotOp>(op, op.getType(), x);
        return success();
      }
    }

    return failure();
  }
};

struct CombCanonicalizePass : public PassWrapper<CombCanonicalizePass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CombCanonicalizePass)

  StringRef getArgument() const override { return "pyc-comb-canonicalize"; }
  StringRef getDescription() const override {
    return "Simplify combinational PYC logic (mux canonicalization and small boolean rewrites)";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();
    RewritePatternSet patterns(f.getContext());
    patterns.add<MuxSameSelSimplify, MuxI1ToLogic, AndBasicSimplify, OrBasicSimplify, OrAndXorFactor>(f.getContext());

    GreedyRewriteConfig cfg;
    cfg.enableFolding();
    if (failed(applyPatternsGreedily(f, std::move(patterns), cfg)))
      signalPassFailure();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCombCanonicalizePass() { return std::make_unique<CombCanonicalizePass>(); }

static PassRegistration<CombCanonicalizePass> pass;

} // namespace pyc
