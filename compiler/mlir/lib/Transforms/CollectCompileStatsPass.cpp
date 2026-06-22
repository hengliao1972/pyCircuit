#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/Pass/Pass.h"

#include <limits>

using namespace mlir;

namespace pyc {
namespace {

static int64_t satMul(int64_t a, int64_t b) {
  if (a <= 0 || b <= 0)
    return 0;
  __int128 v = static_cast<__int128>(a) * static_cast<__int128>(b);
  if (v > std::numeric_limits<int64_t>::max())
    return std::numeric_limits<int64_t>::max();
  return static_cast<int64_t>(v);
}

static int64_t hardwareBitCount(Type ty) {
  if (auto i = dyn_cast<IntegerType>(ty))
    return static_cast<int64_t>(i.getWidth());
  if (auto vt = dyn_cast<VectorType>(ty)) {
    int64_t lanes = 1;
    for (int64_t dim : vt.getShape())
      lanes = satMul(lanes, dim);
    return satMul(lanes, hardwareBitCount(vt.getElementType()));
  }
  return 0;
}

static int64_t depthAttr(Operation *op) {
  if (auto depth = op->getAttrOfType<IntegerAttr>("depth"))
    return static_cast<int64_t>(depth.getInt());
  return 0;
}

static int64_t satAddMul(int64_t acc, int64_t a, int64_t b) {
  if (a <= 0 || b <= 0)
    return acc;
  __int128 v = static_cast<__int128>(acc) + static_cast<__int128>(a) * static_cast<__int128>(b);
  if (v > std::numeric_limits<int64_t>::max())
    return std::numeric_limits<int64_t>::max();
  return static_cast<int64_t>(v);
}

class CollectCompileStatsPass : public PassWrapper<CollectCompileStatsPass, OperationPass<func::FuncOp>> {
public:
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CollectCompileStatsPass)

  StringRef getArgument() const override { return "pyc-collect-compile-stats"; }
  StringRef getDescription() const override {
    return "Collect per-function register/memory counts and bit totals";
  }

  void runOnOperation() override {
    func::FuncOp f = getOperation();

    int64_t regCount = 0;
    int64_t regBits = 0;
    int64_t memCount = 0;
    int64_t memBits = 0;

    f.walk([&](Operation *op) {
      if (auto r = dyn_cast<pyc::RegOp>(op)) {
        regCount += 1;
        regBits = satAddMul(regBits, hardwareBitCount(r.getQ().getType()), 1);
        return;
      }

      auto addMem = [&](Type dataTy) {
        memCount += 1;
        memBits = satAddMul(memBits, hardwareBitCount(dataTy), depthAttr(op));
      };

      if (auto m = dyn_cast<pyc::ByteMemOp>(op)) {
        addMem(m.getRdata().getType());
        return;
      }
      if (auto m = dyn_cast<pyc::SyncMemOp>(op)) {
        addMem(m.getRdata().getType());
        return;
      }
      if (auto m = dyn_cast<pyc::SyncMemDPOp>(op)) {
        // Physical storage is shared between read ports; count once.
        addMem(m.getRdata0().getType());
        return;
      }
    });

    auto *ctx = f.getContext();
    auto i64Ty = IntegerType::get(ctx, 64);
    f->setAttr("pyc.stats.reg_count", IntegerAttr::get(i64Ty, regCount));
    f->setAttr("pyc.stats.reg_bits", IntegerAttr::get(i64Ty, regBits));
    f->setAttr("pyc.stats.mem_count", IntegerAttr::get(i64Ty, memCount));
    f->setAttr("pyc.stats.mem_bits", IntegerAttr::get(i64Ty, memBits));
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCollectCompileStatsPass() {
  return std::make_unique<CollectCompileStatsPass>();
}

static PassRegistration<CollectCompileStatsPass> pass;

} // namespace pyc
