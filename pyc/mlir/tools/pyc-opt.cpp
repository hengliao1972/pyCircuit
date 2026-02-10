#include "pyc/Dialect/PYC/PYCDialect.h"
#include "pyc/Transforms/Passes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/Func/Extensions/InlinerExtension.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/InitAllPasses.h"
#include "mlir/Tools/mlir-opt/MlirOptMain.h"

using namespace mlir;

static void forceLinkPycPasses() {
  // `pyc_transforms` is typically a static library; if nothing references its
  // symbols, the linker may drop entire object files (and the passes won't show
  // up in `pyc-opt --help`).
  //
  // Touch each pass factory to force-link the implementations.
  (void)pyc::createCombCanonicalizePass();
  (void)pyc::createFuseCombPass();
  (void)pyc::createEliminateWiresPass();
  (void)pyc::createPackI1RegsPass();
  (void)pyc::createLowerSCFToPYCStaticPass();
  (void)pyc::createCheckFlatTypesPass();
  (void)pyc::createPrunePortsPass();
}

int main(int argc, char **argv) {
  DialectRegistry registry;
  registry.insert<pyc::PYCDialect, mlir::arith::ArithDialect, mlir::func::FuncDialect, mlir::scf::SCFDialect>();
  mlir::func::registerInlinerExtension(registry);
  registerAllPasses();
  forceLinkPycPasses();
  return asMainReturnCode(MlirOptMain(argc, argv, "pyc-opt\n", registry));
}
