#include "pyc/Dialect/PYC/PYCDialect.h"
#include "pyc/Emit/CppEmitter.h"
#include "pyc/Support/PassIRDumper.h"
#include "pyc/Transforms/Passes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/Func/Extensions/InlinerExtension.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/InitAllPasses.h"
#include "mlir/Tools/mlir-opt/MlirOptMain.h"

#include "llvm/Support/CommandLine.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/raw_ostream.h"

using namespace mlir;

// Per-pass IR dump flags (diagnostics). These mirror pycc's options so the same
// workflow works on pyc-opt. Default: disabled (empty dir).
static llvm::cl::opt<std::string> dumpPassIrDir(
    "dump-pass-ir",
    llvm::cl::desc("Dump IR before/after each pass to this directory"),
    llvm::cl::init(""));
static llvm::cl::opt<std::string> dumpPassIrPhase(
    "dump-pass-ir-phase",
    llvm::cl::desc("Which phase to dump: before | after | both"),
    llvm::cl::init("both"));
static llvm::cl::opt<std::string> dumpPassIrFilter(
    "dump-pass-ir-filter",
    llvm::cl::desc("Regex filtering pass short names"),
    llvm::cl::init(""));
static llvm::cl::opt<unsigned> dumpPassIrMaxLines(
    "dump-pass-ir-max-lines",
    llvm::cl::desc("Truncate each IR dump file after N lines (0 = unlimited)"),
    llvm::cl::init(0));

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
  (void)pyc::createCppPlacementPass(pyc::CppEmitterOptions::kDefaultCombChunkNodes);
  (void)pyc::createVectorUnrollPass();
  (void)pyc::createSLPPackWiresPass();
  (void)pyc::createFlattenInstancesPass();
  (void)pyc::createEliminateDeadStatePass();
  (void)pyc::createEliminateDeadInstancesPass();
  (void)pyc::createCheckNoDynamicPass();
  (void)pyc::createCheckCombCyclesPass();
  (void)pyc::createCheckClockDomainsPass();
  (void)pyc::createCheckLogicDepthPass(0);
  (void)pyc::createCollectCompileStatsPass();
  (void)pyc::createInlineFunctionsPass();
  (void)pyc::createCheckFrontendContractPass();
  (void)pyc::createCheckHierarchyDisciplinePass();
}

int main(int argc, char **argv) {
  DialectRegistry registry;
  registry.insert<pyc::PYCDialect, mlir::arith::ArithDialect, mlir::func::FuncDialect, mlir::scf::SCFDialect>();
  mlir::func::registerInlinerExtension(registry);
  registerAllPasses();
  forceLinkPycPasses();

  // Parse CLI ourselves so we can read our custom dump flags before MlirOptMain
  // builds and runs the pass manager. The returned pair is {input, output}.
  auto [inputFilename, outputFilename] =
      registerAndParseCLIOptions(argc, argv, "pyc-opt\n", registry);

  // Build the config from the (now-parsed) standard mlir-opt CL options, then
  // install our IR-dump instrumentation via the pass-pipeline setup callback.
  MlirOptMainConfig config = MlirOptMainConfig::createFromCLOptions();
  config.setPassPipelineSetupFn([](PassManager &pm) -> LogicalResult {
    if (dumpPassIrDir.empty())
      return success();
    if (dumpPassIrDir.getValue() == "auto") {
      // pycc resolves `auto` to <--out-dir>/pass_ir; pyc-opt has no --out-dir.
      llvm::errs() << "error: --dump-pass-ir=auto is not supported by pyc-opt; "
                      "pass an explicit directory\n";
      return failure();
    }
    pyc::PassIRDumperOptions opts;
    opts.dir = dumpPassIrDir.getValue();
    opts.phase = dumpPassIrPhase.getValue();
    opts.filterRegex = dumpPassIrFilter.getValue();
    opts.maxLines = static_cast<uint64_t>(dumpPassIrMaxLines.getValue());
    // addInstrumentation takes ownership; the PassManager keeps the
    // instrumentation alive for the duration of pm.run().
    pm.addInstrumentation(std::make_unique<pyc::PassIRDumper>(std::move(opts)));
    return success();
  });

  // Read input buffer (stdin when input is "-").
  auto buffer = llvm::MemoryBuffer::getFileOrSTDIN(inputFilename);
  if (!buffer) {
    llvm::errs() << "error: cannot read " << inputFilename << ": "
                 << buffer.getError().message() << "\n";
    return 1;
  }

  // Route output: "-" (the mlir-opt default) goes to stdout; otherwise open
  // the named file. MlirOptMain handles IR splitting/output markers from there.
  if (outputFilename == "-") {
    return asMainReturnCode(
        MlirOptMain(llvm::outs(), std::move(*buffer), registry, config));
  }
  std::error_code ec;
  llvm::raw_fd_ostream fileOs(outputFilename, ec, llvm::sys::fs::OF_Text);
  if (ec) {
    llvm::errs() << "error: cannot open " << outputFilename << ": "
                 << ec.message() << "\n";
    return 1;
  }
  return asMainReturnCode(
      MlirOptMain(fileOs, std::move(*buffer), registry, config));
}
