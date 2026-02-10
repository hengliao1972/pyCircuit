#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"

#include <optional>

using namespace mlir;

namespace pyc {
namespace {

static bool isI1(Type t) {
  if (auto it = dyn_cast<IntegerType>(t))
    return it.getWidth() == 1;
  return false;
}

struct PackI1RegsPass : public PassWrapper<PackI1RegsPass, OperationPass<func::FuncOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(PackI1RegsPass)

  StringRef getArgument() const override { return "pyc-pack-i1-regs"; }
  StringRef getDescription() const override {
    return "Pack runs of i1 pyc.reg ops with the same clk/rst/en into a wider reg (reduces primitive count in codegen)";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();
    for (Block &b : f.getBody())
      packBlock(b);
  }

  void packBlock(Block &b) {
    // Scan the block and collect "runs" of i1 regs that:
    // - share clk/rst/en,
    // - are not used (via q or alias results) until after the run ends.
    //
    // This is conservative but matches the common "declare regs up-front" style
    // used by pyCircuit frontends.
    llvm::SmallVector<pyc::RegOp> runRegs;
    llvm::SmallVector<pyc::AliasOp> runAliases;
    llvm::DenseSet<Value> runVals;
    Value runClk, runRst, runEn;

    auto clearRun = [&]() {
      runRegs.clear();
      runAliases.clear();
      runVals.clear();
      runClk = Value();
      runRst = Value();
      runEn = Value();
    };

    auto sameKey = [&](pyc::RegOp r) -> bool {
      if (runRegs.empty())
        return true;
      return r.getClk() == runClk && r.getRst() == runRst && r.getEn() == runEn;
    };

    auto flushRun = [&](Operation *insertBefore) {
      if (runRegs.size() < 2) {
        clearRun();
        return;
      }
      if (!insertBefore)
        insertBefore = b.getTerminator();
      packRun(insertBefore, runRegs, runAliases);
      clearRun();
    };

    for (Operation &op : llvm::make_early_inc_range(b)) {
      // If we hit a non-alias use of any value in the current run, we must pack
      // (or give up) before this op to preserve SSA dominance.
      bool usesRun = false;
      for (Value v : op.getOperands()) {
        if (runVals.contains(v)) {
          usesRun = true;
          break;
        }
      }

      if (usesRun) {
        // Allow aliases of reg q values in the run (these will be re-created at
        // the pack insertion point).
        bool allowed = false;
        if (auto a = dyn_cast<pyc::AliasOp>(op)) {
          Value in = a.getIn();
          for (pyc::RegOp r : runRegs) {
            if (in == r.getQ()) {
              allowed = true;
              runAliases.push_back(a);
              runVals.insert(a.getResult());
              break;
            }
          }
        }
        if (!allowed) {
          flushRun(&op);
        }
      }

      // Start/extend a run on i1 regs with the same clk/rst/en.
      if (auto r = dyn_cast<pyc::RegOp>(op)) {
        if (!isI1(r.getType())) {
          flushRun(&op);
          continue;
        }
        if (!sameKey(r)) {
          flushRun(&op);
        }
        if (runRegs.empty()) {
          runClk = r.getClk();
          runRst = r.getRst();
          runEn = r.getEn();
        }
        runRegs.push_back(r);
        runVals.insert(r.getQ());
        continue;
      }
    }

    flushRun(/*insertBefore=*/nullptr);
  }

  void packRun(Operation *insertBefore, ArrayRef<pyc::RegOp> regs, ArrayRef<pyc::AliasOp> aliases) {
    MLIRContext *ctx = insertBefore->getContext();
    OpBuilder builder(ctx);
    builder.setInsertionPoint(insertBefore);

    const unsigned n = static_cast<unsigned>(regs.size());
    Type packedTy = IntegerType::get(ctx, n);
    Type bitTy = IntegerType::get(ctx, 1);

    // Pack order: reg[0] is LSB, reg[n-1] is MSB.
    llvm::SmallVector<Value> nextInputs;
    llvm::SmallVector<Value> initInputs;
    nextInputs.reserve(n);
    initInputs.reserve(n);
    for (pyc::RegOp r : llvm::reverse(regs)) {
      nextInputs.push_back(r.getNext());
      initInputs.push_back(r.getInit());
    }

    pyc::RegOp first = regs.front();
    Location loc = first.getLoc();
    Value nextPacked = builder.create<pyc::ConcatOp>(loc, packedTy, nextInputs).getResult();
    Value initPacked = builder.create<pyc::ConcatOp>(loc, packedTy, initInputs).getResult();
    auto packedReg =
        builder.create<pyc::RegOp>(loc, packedTy, first.getClk(), first.getRst(), first.getEn(), nextPacked, initPacked);

    llvm::SmallVector<Value> bits;
    bits.reserve(n);
    for (unsigned i = 0; i < n; ++i) {
      // Extract bit i (LSB = reg[0]).
      auto ext = builder.create<pyc::ExtractOp>(loc, bitTy, packedReg.getQ(), builder.getI64IntegerAttr(i));
      bits.push_back(ext.getResult());
    }

    llvm::DenseSet<Operation *> erased;
    erased.reserve(regs.size() + aliases.size());
    for (pyc::RegOp r : regs)
      erased.insert(r.getOperation());
    for (pyc::AliasOp a : aliases)
      erased.insert(a.getOperation());

    // Replace uses of each old reg q with its packed-bit extract, but avoid
    // rewriting alias ops that we're about to erase (they may appear earlier in
    // the block than the new extract defs).
    for (unsigned i = 0; i < n; ++i) {
      pyc::RegOp r = regs[i];
      Value oldQ = r.getQ();
      Value repl = bits[i];
      oldQ.replaceUsesWithIf(repl, [&](OpOperand &use) { return !erased.contains(use.getOwner()); });
    }

    // Re-create aliases at the insertion point, preserving attributes and
    // replacing all uses of the old alias results.
    for (pyc::AliasOp a : aliases) {
      Value in = a.getIn();
      std::optional<unsigned> idx;
      for (unsigned i = 0; i < n; ++i) {
        pyc::RegOp r = regs[i];
        if (in == r.getQ()) {
          idx = i;
          break;
        }
      }
      if (!idx)
        continue;

      auto newAlias = builder.create<pyc::AliasOp>(a.getLoc(), bitTy, bits[*idx]);
      newAlias->setAttrs(a->getAttrs());
      a.getResult().replaceAllUsesWith(newAlias.getResult());
    }

    // Erase old ops (reverse order to keep iterators stable and respect SSA uses).
    for (pyc::AliasOp a : llvm::reverse(aliases))
      a.erase();
    for (pyc::RegOp r : llvm::reverse(regs))
      r.erase();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createPackI1RegsPass() { return std::make_unique<PackI1RegsPass>(); }

static PassRegistration<PackI1RegsPass> pass;

} // namespace pyc
