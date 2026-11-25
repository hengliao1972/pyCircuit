#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Interfaces/SideEffectInterfaces.h"
#include "mlir/IR/Matchers.h"
#include "mlir/IR/PatternMatch.h"
#include "mlir/Pass/Pass.h"
#include "mlir/Rewrite/PatternApplicator.h"
#include "mlir/Transforms/FoldUtils.h"


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

// Create a constant value for a scalar type.
static Value constInt(PatternRewriter &rewriter, Location loc, Type ty, const llvm::APInt &v) {
  auto intTy = dyn_cast<IntegerType>(ty);
  if (!intTy)
    return {};
  llvm::APInt vv = v;
  if (vv.getBitWidth() != intTy.getWidth())
    vv = vv.zextOrTrunc(intTy.getWidth());
  return rewriter.create<pyc::ConstantOp>(loc, intTy, IntegerAttr::get(intTy, vv));
}

static bool isI1(Type ty) {
  auto intTy = dyn_cast<IntegerType>(ty);
  return intTy && intTy.getWidth() == 1;
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
    if (!isI1(op.getType()))
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
      rewriter.replaceOpWithNewOp<pyc::AndOp>(op, op.getResult().getType(), op.getSel(), op.getA());
      return success();
    }
    if (aConst && (((*aConst) & 1) == 1)) {
      // sel ? 1 : b  ==>  sel | b
      rewriter.replaceOpWithNewOp<pyc::OrOp>(op, op.getResult().getType(), op.getSel(), op.getB());
      return success();
    }
    if (bConst && (((*bConst) & 1) == 1)) {
      // sel ? a : 1  ==>  (~sel) | a
      Value nsel = rewriter.create<pyc::NotOp>(op.getLoc(), op.getSel());
      rewriter.replaceOpWithNewOp<pyc::OrOp>(op, op.getResult().getType(), nsel, op.getA());
      return success();
    }
    if (aConst && (((*aConst) & 1) == 0)) {
      // sel ? 0 : b  ==>  (~sel) & b
      Value nsel = rewriter.create<pyc::NotOp>(op.getLoc(), op.getSel());
      rewriter.replaceOpWithNewOp<pyc::AndOp>(op, op.getResult().getType(), nsel, op.getB());
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
      Value ones = constInt(rewriter, op.getLoc(), op.getType(),
                            llvm::APInt::getAllOnes(cast<IntegerType>(op.getType()).getWidth()));
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


/// A worklist driver for the five patterns above. It follows the upstream
/// greedy driver's processing semantics (fold, then match patterns, LIFO over
/// a postorder population, constant materialization included) with one crucial
/// difference: the users of every replaced value are re-enqueued immediately.
/// The upstream greedy driver restarts from a full region walk each iteration
/// and never re-enqueues users of replaced values, so a folding chain in which
/// each level only becomes foldable after the previous one collapsed advances
/// exactly one level per iteration and aborts once it outgrows the iteration
/// budget. With users re-enqueued, such chains collapse within a single
/// worklist drain and no iteration budget is needed.
class ChainCollapseDriver : public RewriterBase::Listener {
public:
  ChainCollapseDriver(MLIRContext *ctx, const FrozenRewritePatternSet &patterns)
      : context(ctx), rewriter(ctx), matcher(patterns) {
    matcher.applyDefaultCostModel();
    rewriter.setListener(this);
  }

  ~ChainCollapseDriver() override { rewriter.setListener(nullptr); }

  LogicalResult collapse(Region &region) {
    bool changed = false;
    int64_t iteration = 0;
    do {
      // Safety cap: one drain converges all use-side chains; iterations only
      // guard against a hypothetical non-confluent pattern interaction.
      if (++iteration > 100)
        return failure();
      worklist.clear();
      worklistMap.clear();
      OperationFolder folder(context, this);
      auto insertKnownConstant = [&](Operation *op) {
        Attribute constValue;
        if (matchPattern(op, m_Constant(&constValue)))
          if (!folder.insertKnownConstant(op, constValue))
            return true;
        return false;
      };
      region.walk([&](Operation *op) {
        if (!insertKnownConstant(op))
          addToWorklist(op);
      });
      changed = processWorklist();
    } while (changed);
    return success();
  }

private:
  void addToWorklist(Operation *op) {
    // Mirror the upstream scope handling: enqueue the op and its ancestors.
    SmallVector<Operation *, 8> ancestors;
    Region *region = nullptr;
    do {
      ancestors.push_back(op);
      region = op->getParentRegion();
      if (region == nullptr)
        break;
      op = region->getParentOp();
    } while (op);
    for (Operation *op : ancestors)
      push(op);
  }

  void push(Operation *op) {
    if (!worklistMap.insert({op, worklist.size()}).second)
      return;
    worklist.push_back(op);
  }

  Operation *pop() {
    while (!worklist.empty()) {
      Operation *op = worklist.back();
      worklist.pop_back();
      if (!op)
        continue;
      worklistMap.erase(op);
      return op;
    }
    return nullptr;
  }

  bool empty() const {
    return !llvm::any_of(worklist, [](Operation *op) { return op != nullptr; });
  }

  void removeFromList(Operation *op) {
    auto it = worklistMap.find(op);
    if (it != worklistMap.end()) {
      worklist[it->second] = nullptr;
      worklistMap.erase(it);
    }
  }

  bool processWorklist() {
    bool changed = false;
    while (!empty()) {
      Operation *op = pop();

      // If the operation is trivially dead - remove it.
      if (isOpTriviallyDead(op)) {
        rewriter.eraseOp(op);
        changed = true;
        continue;
      }

      // Try to fold this op. Do not fold constant ops; that would loop with
      // the materialized constants.
      if (!op->hasTrait<OpTrait::ConstantLike>()) {
        SmallVector<OpFoldResult> foldResults;
        if (succeeded(op->fold(foldResults))) {
          if (foldResults.empty()) {
            // In-place update; revisit the op itself.
            push(op);
            changed = true;
            continue;
          }
          OpBuilder::InsertionGuard g(rewriter);
          rewriter.setInsertionPoint(op);
          SmallVector<Value> replacements;
          bool materialized = true;
          for (auto [ofr, resultType] :
               llvm::zip_equal(foldResults, op->getResultTypes())) {
            if (auto value = ofr.dyn_cast<Value>()) {
              replacements.push_back(value);
              continue;
            }
            Operation *constOp = op->getDialect()->materializeConstant(
                rewriter, ofr.get<Attribute>(), resultType, op->getLoc());
            if (!constOp) {
              for (Value replacement : replacements)
                if (Operation *def = replacement.getDefiningOp())
                  rewriter.eraseOp(def);
              materialized = false;
              break;
            }
            replacements.push_back(constOp->getResult(0));
          }
          if (materialized) {
            rewriter.replaceOp(op, replacements);
            changed = true;
            continue;
          }
        }
      }

      // Try to match one of the patterns.
      if (succeeded(matcher.matchAndRewrite(op, rewriter)))
        changed = true;
    }
    return changed;
  }

  void notifyOperationInserted(Operation *op,
                               OpBuilder::InsertPoint previous) override {
    push(op);
  }

  void notifyOperationModified(Operation *op) override { push(op); }

  void notifyOperationReplaced(Operation *op,
                               ValueRange replacement) override {
    for (Value v : replacement)
      for (Operation *user : v.getUsers())
        push(user);
  }

  void notifyOperationErased(Operation *op) override {
    removeFromList(op);
    for (Value operand : op->getOperands())
      if (Operation *def = operand.getDefiningOp())
        push(def);
  }

  MLIRContext *context;
  PatternRewriter rewriter;
  PatternApplicator matcher;
  std::vector<Operation *> worklist;
  DenseMap<Operation *, unsigned> worklistMap;
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
    patterns.add<MuxSameSelSimplify,
                 MuxI1ToLogic,
                 AndBasicSimplify,
                 OrBasicSimplify,
                 OrAndXorFactor>(f.getContext());

    FrozenRewritePatternSet frozen(std::move(patterns));
    ChainCollapseDriver driver(f.getContext(), frozen);
    if (failed(driver.collapse(f.getBody())))
      signalPassFailure();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCombCanonicalizePass() { return std::make_unique<CombCanonicalizePass>(); }

static PassRegistration<CombCanonicalizePass> pass;

} // namespace pyc
