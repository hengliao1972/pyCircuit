#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/StateOptimization.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/ADT/StringSet.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"

#include <optional>
#include <string>

using namespace mlir;

namespace pyc {
namespace {

static unsigned valueWidth(Value value) {
  if (auto integer = dyn_cast<IntegerType>(value.getType()))
    return integer.getWidth();
  return 0;
}

// After merge/retime/pack remapped identities, mark undeclared pyc.name
// values as lazy lookups. Decision 0091/0096: those names stay findable
// but must not pay per-cycle extract/pin cost. IR attrs debug_keep /
// observable / probe* / trace* still count as eager demand.
struct ApplyObservationDemandPass
    : public PassWrapper<ApplyObservationDemandPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(ApplyObservationDemandPass)

  ApplyObservationDemandPass() = default;
  ApplyObservationDemandPass(const ApplyObservationDemandPass &other)
      : PassWrapper(other) {}
  ApplyObservationDemandPass(std::string probePlan,
                             std::string traceCodegenPlan) {
    probePlanOption = std::move(probePlan);
    tracePlanOption = std::move(traceCodegenPlan);
  }

  StringRef getArgument() const override {
    return "pyc-apply-observation-demand";
  }
  StringRef getDescription() const override {
    return "Mark undeclared pyc.name as lazy lookups after state opt";
  }

  Option<std::string> probePlanOption{
      *this, "probe-plan",
      llvm::cl::desc("Resolved @probe alias plan JSON (source_path fields)"),
      llvm::cl::init("")};
  Option<std::string> tracePlanOption{
      *this, "trace-codegen-plan",
      llvm::cl::desc("Per-module internal fields selected for C++ trace"),
      llvm::cl::init("")};

  void runOnOperation() override {
    ModuleOp module = getOperation();
    ObservationPlans plans;
    if (failed(loadObservationPlans(module, probePlanOption, tracePlanOption,
                                    plans))) {
      signalPassFailure();
      return;
    }
    // Demand set: probe plan source_path field components. Paths are rooted
    // at the instance hierarchy (dut:...), not the function symbol, so the
    // demand set is built from every alias; only the top function carries
    // pyc.name values that match, matching the emitted addAlias surface.
    llvm::StringSet<> probeFields;
    for (const ProbePlanAlias &alias : plans.probeAliases) {
      StringRef source = alias.sourcePath;
      size_t colon = source.find(':');
      if (colon != StringRef::npos && colon + 1 < source.size())
        probeFields.insert(source.substr(colon + 1));
    }
    llvm::StringMap<llvm::StringSet<>> &traceFieldsByModule =
        plans.traceFieldsByModule;

    for (auto f : module.getOps<func::FuncOp>()) {
      if (f.isDeclaration())
        continue;
      if (failed(applyToFunction(f, probeFields, traceFieldsByModule))) {
        signalPassFailure();
        return;
      }
    }
  }

  LogicalResult
  applyToFunction(func::FuncOp function, const llvm::StringSet<> &probeFields,
                  const llvm::StringMap<llvm::StringSet<>> &traceFieldsByModule) {
    llvm::StringSet<> demand = probeFields;
    llvm::StringSet<> presentBefore;
    function.walk([&](Operation *op) {
      if (auto name = op->getAttrOfType<StringAttr>("pyc.name"))
        presentBefore.insert(name.getValue());
    });
    if (const auto it = traceFieldsByModule.find(function.getSymName());
        it != traceFieldsByModule.end()) {
      for (const auto &field : it->second) {
        demand.insert(field.getKey());
        if (!presentBefore.contains(field.getKey()))
          return function.emitError(
                     "C++ trace codegen field not found in module: ")
                 << field.getKey();
      }
    }

    int64_t lazy = 0;
    int64_t kept = 0;
    auto mark = [&](Operation *op) -> LogicalResult {
      auto name = op->getAttrOfType<StringAttr>("pyc.name");
      if (!name)
        return success();
      if (isCycleBalanceGenerated(op))
        return success();
      const bool demanded = shouldKeepStateOptimization(op) ||
                            demand.contains(name.getValue());
      if (demanded) {
        op->removeAttr("pyc.observe_lazy");
        ++kept;
        return success();
      }
      if (op->getNumResults() != 1) {
        ++kept;
        return success();
      }
      const unsigned width = valueWidth(op->getResult(0));
      // Wider-than-64b scalars also stay eager: the lazy read path exposes
      // only Entry::readU64() (low 64 bits), which would silently truncate.
      if (width == 0 || width > 64) {
        ++kept;
        return success();
      }
      if (auto source = findStateStorageSource(op->getResult(0))) {
        op->setAttr("pyc.observe_lazy", BoolAttr::get(op->getContext(), true));
        if (failed(appendLazyProbeSlice(source->storage, name.getValue(),
                                        source->lsb, width,
                                        source->tapDepth)))
          return failure();
        ++lazy;
        return success();
      }
      auto host = findWireProbeHost(op->getResult(0));
      if (!host) {
        ++kept;
        return success();
      }
      op->setAttr("pyc.observe_lazy", BoolAttr::get(op->getContext(), true));
      if (failed(appendLazyProbeWire(host->op, name.getValue(), width,
                                     host->resultIndex)))
        return failure();
      ++lazy;
      return success();
    };
    LogicalResult status = success();
    function.walk([&](Operation *op) {
      if (failed(status))
        return;
      status = mark(op);
    });
    if (failed(status))
      return failure();

    setI64Attr(function, "pyc.stats.observe_named_names_lazy", lazy);
    setI64Attr(function, "pyc.stats.observe_named_names_kept", kept);
    return success();
  }
};

} // namespace

std::unique_ptr<Pass>
createApplyObservationDemandPass(std::string probePlanPath,
                                 std::string traceCodegenPlanPath) {
  return std::make_unique<ApplyObservationDemandPass>(
      std::move(probePlanPath), std::move(traceCodegenPlanPath));
}

static PassRegistration<ApplyObservationDemandPass> pass;

} // namespace pyc
