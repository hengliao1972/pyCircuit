#include "pyc/Dialect/PYC/PYCDialect.h"
#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Emit/CppEmitter.h"
#include "pyc/Emit/VerilogEmitter.h"
#include "pyc/Transforms/Passes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/Func/Extensions/InlinerExtension.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/DialectRegistry.h"
#include "mlir/Parser/Parser.h"
#include "mlir/Pass/PassManager.h"
#include "mlir/Transforms/Passes.h"
#include "llvm/ADT/SmallSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/InitLLVM.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/SourceMgr.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cstdlib>
#include <optional>

using namespace mlir;

static llvm::cl::opt<std::string> inputFilename(llvm::cl::Positional, llvm::cl::desc("<input .pyc>"),
                                                llvm::cl::init("-"));

static llvm::cl::opt<std::string> outputFilename("o", llvm::cl::desc("Output file"), llvm::cl::init("-"));

static llvm::cl::opt<std::string> emitKind("emit", llvm::cl::desc("Emission target: verilog|cpp"),
                                           llvm::cl::init("verilog"));

static llvm::cl::opt<std::string> targetKind("target", llvm::cl::desc("Target: default|fpga"),
                                             llvm::cl::init("default"));

static llvm::cl::opt<bool> includePrims("include-primitives",
                                        llvm::cl::desc("Emit `include` for PYC Verilog primitives"),
                                        llvm::cl::init(true));

static llvm::cl::opt<std::string>
    outDir("out-dir", llvm::cl::desc("Output directory (split per module; emits manifest.json)"), llvm::cl::init(""));

static std::string topSymbol(ModuleOp module) {
  if (auto topAttr = module->getAttrOfType<FlatSymbolRefAttr>("pyc.top"))
    return topAttr.getValue().str();
  if (auto first = module.getOps<func::FuncOp>().begin(); first != module.getOps<func::FuncOp>().end())
    return (*first).getSymName().str();
  return "";
}

static LogicalResult writeFile(llvm::StringRef path, llvm::StringRef contents) {
  std::error_code ec;
  llvm::raw_fd_ostream os(path, ec, llvm::sys::fs::OF_Text);
  if (ec) {
    llvm::errs() << "error: cannot open " << path << ": " << ec.message() << "\n";
    return failure();
  }
  os << contents;
  return success();
}

static std::optional<std::string> findPrimitivesDir(const char *argv0) {
  // Allow explicit override (useful for installed toolchains).
  if (const char *env = std::getenv("PYC_PRIMITIVES_DIR")) {
    llvm::SmallString<256> cand(env);
    llvm::sys::path::append(cand, "pyc_reg.v");
    if (llvm::sys::fs::exists(cand))
      return std::string(env);
  }

  auto tryRoot = [&](llvm::StringRef root) -> std::optional<std::string> {
    llvm::SmallString<256> dir(root);
    llvm::sys::path::append(dir, "include", "pyc", "verilog");
    llvm::SmallString<256> probe(dir);
    llvm::sys::path::append(probe, "pyc_reg.v");
    if (llvm::sys::fs::exists(probe))
      return dir.str().str();
    return std::nullopt;
  };

  // Current working directory.
  llvm::SmallString<256> cwd;
  if (!llvm::sys::fs::current_path(cwd)) {
    if (auto d = tryRoot(cwd))
      return d;
  }

  // Walk up from the executable path (common for in-tree builds).
  llvm::SmallString<256> exe(argv0 ? argv0 : "");
  if (!exe.empty()) {
    llvm::SmallString<256> rp;
    if (!llvm::sys::fs::real_path(exe, rp)) {
      llvm::SmallString<256> cur = llvm::sys::path::parent_path(rp);
      for (unsigned i = 0; i < 6 && !cur.empty(); ++i) {
        if (auto d = tryRoot(cur))
          return d;
        cur = llvm::sys::path::parent_path(cur);
      }
    }
  }

  return std::nullopt;
}

static LogicalResult emitPrimitivesFile(llvm::StringRef outPath, llvm::StringRef primDir, bool targetFpga) {
  static const char *kFiles[] = {
      "pyc_reg.v",
      "pyc_fifo.v",
      "pyc_byte_mem.v",
      "pyc_sync_mem.v",
      "pyc_sync_mem_dp.v",
      "pyc_async_fifo.v",
      "pyc_cdc_sync.v",
  };

  std::string buf;
  llvm::raw_string_ostream ss(buf);
  ss << "// pyCircuit Verilog primitives (concatenated)\n\n";
  if (targetFpga)
    ss << "`define PYC_TARGET_FPGA 1\n\n";
  for (const char *name : kFiles) {
    llvm::SmallString<256> path(primDir);
    llvm::sys::path::append(path, name);
    auto fileOrErr = llvm::MemoryBuffer::getFile(path);
    if (!fileOrErr) {
      llvm::errs() << "error: cannot read primitive file: " << path << "\n";
      return failure();
    }
    ss << "// --- " << name << "\n";
    ss << fileOrErr->get()->getBuffer() << "\n\n";
  }
  ss.flush();
  return writeFile(outPath, buf);
}

static LogicalResult updateManifest(llvm::StringRef outDirPath, llvm::StringRef top,
                                   std::optional<llvm::json::Array> verilogMods,
                                   std::optional<llvm::json::Array> cppMods) {
  llvm::SmallString<256> path(outDirPath);
  llvm::sys::path::append(path, "manifest.json");

  llvm::json::Object manifest;
  if (llvm::sys::fs::exists(path)) {
    if (auto mb = llvm::MemoryBuffer::getFile(path)) {
      if (auto parsed = llvm::json::parse(mb->get()->getBuffer())) {
        if (auto *obj = parsed->getAsObject())
          manifest = *obj;
      }
    }
  }

  manifest["top"] = top.str();
  if (!manifest.get("verilog_modules"))
    manifest["verilog_modules"] = llvm::json::Array();
  if (!manifest.get("cpp_modules"))
    manifest["cpp_modules"] = llvm::json::Array();
  if (verilogMods)
    manifest["verilog_modules"] = std::move(*verilogMods);
  if (cppMods)
    manifest["cpp_modules"] = std::move(*cppMods);

  std::string buf;
  llvm::raw_string_ostream ss(buf);
  llvm::json::OStream j(ss, 2);
  llvm::json::Value v(std::move(manifest));
  j.value(v);
  ss << "\n";
  ss.flush();
  return writeFile(path, buf);
}

int main(int argc, char **argv) {
  llvm::InitLLVM y(argc, argv);
  llvm::cl::ParseCommandLineOptions(argc, argv, "pyc-compile\n");

  DialectRegistry registry;
  registry.insert<pyc::PYCDialect, mlir::arith::ArithDialect, mlir::func::FuncDialect, mlir::scf::SCFDialect>();
  mlir::func::registerInlinerExtension(registry);

  MLIRContext ctx(registry);
  ctx.loadAllAvailableDialects();

  llvm::SourceMgr sm;
  auto fileOrErr = llvm::MemoryBuffer::getFileOrSTDIN(inputFilename);
  if (!fileOrErr) {
    llvm::errs() << "error: cannot read " << inputFilename << "\n";
    return 1;
  }
  sm.AddNewSourceBuffer(std::move(*fileOrErr), llvm::SMLoc());

  OwningOpRef<ModuleOp> module = parseSourceFile<ModuleOp>(sm, &ctx);
  if (!module) {
    llvm::errs() << "error: failed to parse MLIR\n";
    return 1;
  }

  // Cleanup + optimization pipeline tuned for netlist-style emission.
  PassManager pm(&ctx);
  pm.addPass(createInlinerPass());
  pm.addPass(createCanonicalizerPass());
  pm.addPass(createCSEPass());

  // Constprop / DCE before lowering and comb fusion (keep IR small).
  pm.addPass(createSCCPPass());
  pm.addPass(createCanonicalizerPass());
  pm.addPass(createCSEPass());
  pm.addPass(createRemoveDeadValuesPass());
  pm.addPass(createSymbolDCEPass());

  pm.addNestedPass<func::FuncOp>(pyc::createLowerSCFToPYCStaticPass());
  pm.addNestedPass<func::FuncOp>(pyc::createEliminateWiresPass());
  pm.addNestedPass<func::FuncOp>(pyc::createCombCanonicalizePass());
  pm.addNestedPass<func::FuncOp>(pyc::createCheckCombCyclesPass());
  pm.addNestedPass<func::FuncOp>(pyc::createPackI1RegsPass());
  pm.addPass(createCanonicalizerPass());
  pm.addPass(createCSEPass());

  // Re-run simplification after CFG lowering.
  pm.addPass(createSCCPPass());
  pm.addPass(createCanonicalizerPass());
  pm.addPass(createCSEPass());
  pm.addPass(createSymbolDCEPass());
  pm.addPass(createRemoveDeadValuesPass());

  pm.addNestedPass<func::FuncOp>(pyc::createFuseCombPass());
  pm.addPass(createCanonicalizerPass());
  pm.addPass(createCSEPass());
  pm.addPass(createRemoveDeadValuesPass());
  pm.addPass(createSymbolDCEPass());
  pm.addNestedPass<func::FuncOp>(pyc::createCheckFlatTypesPass());
  pm.addNestedPass<func::FuncOp>(pyc::createCheckNoDynamicPass());
  if (failed(pm.run(*module))) {
    llvm::errs() << "error: pass pipeline failed\n";
    return 1;
  }

  if (!outDir.empty()) {
    std::error_code ec = llvm::sys::fs::create_directories(outDir);
    if (ec) {
      llvm::errs() << "error: cannot create --out-dir " << outDir << ": " << ec.message() << "\n";
      return 1;
    }

    std::string top = topSymbol(*module);
    if (top.empty()) {
      llvm::errs() << "error: cannot determine top symbol (missing pyc.top and no func.func)\n";
      return 1;
    }

    if (emitKind == "verilog") {
      llvm::json::Array verilogFiles;
      bool targetFpga = (targetKind == "fpga");
      if (!targetFpga && targetKind != "default") {
        llvm::errs() << "error: unknown --target: " << targetKind << " (expected: default|fpga)\n";
        return 1;
      }
      if (includePrims) {
        auto primDir = findPrimitivesDir(argv[0]);
        if (!primDir) {
          llvm::errs() << "error: cannot locate include/pyc/verilog for primitives; set PYC_PRIMITIVES_DIR\n";
          return 1;
        }
        llvm::SmallString<256> primOut(outDir);
        llvm::sys::path::append(primOut, "pyc_primitives.v");
        if (failed(emitPrimitivesFile(primOut, *primDir, targetFpga)))
          return 1;
        verilogFiles.push_back("pyc_primitives.v");
      }

      pyc::VerilogEmitterOptions opts;
      opts.includePrimitives = false; // out-dir mode uses pyc_primitives.v (or expects external primitives)
      opts.targetFpga = targetFpga;

      for (auto f : module->getOps<func::FuncOp>()) {
        std::string fname = (f.getSymName() + ".v").str();
        llvm::SmallString<256> path(outDir);
        llvm::sys::path::append(path, fname);

        std::error_code fe;
        llvm::raw_fd_ostream os(path, fe, llvm::sys::fs::OF_Text);
        if (fe) {
          llvm::errs() << "error: cannot open " << path << ": " << fe.message() << "\n";
          return 1;
        }
        if (failed(pyc::emitVerilogFunc(*module, f, os, opts)))
          return 1;
        verilogFiles.push_back(fname);
      }

      if (failed(updateManifest(outDir, top, std::move(verilogFiles), /*cppMods=*/std::nullopt)))
        return 1;

      // Optional Yosys stub (sanity synth).
      llvm::SmallString<256> ysPath(outDir);
      llvm::sys::path::append(ysPath, "yosys_synth.ys");
      std::string ys;
      llvm::raw_string_ostream yss(ys);
      yss << "# Generated by pyc-compile\n";
      if (includePrims)
        yss << "read_verilog -sv pyc_primitives.v\n";
      for (auto f : module->getOps<func::FuncOp>()) {
        yss << "read_verilog -sv " << f.getSymName().str() << ".v\n";
      }
      yss << "hierarchy -top " << top << "\n";
      yss << "proc; opt; memory; opt\n";
      yss << "synth -top " << top << "\n";
      yss.flush();
      if (failed(writeFile(ysPath, ys)))
        return 1;

      return 0;
    }

    if (emitKind == "cpp") {
      llvm::json::Array cppFiles;

      // Collect direct dependencies per module for header includes.
      llvm::StringMap<llvm::SmallVector<std::string>> deps;
      for (auto f : module->getOps<func::FuncOp>()) {
        auto &v = deps[f.getSymName()];
        f.walk([&](pyc::InstanceOp inst) {
          auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
          if (!calleeAttr)
            return;
          v.push_back(calleeAttr.getValue().str());
        });
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
      }

      for (auto f : module->getOps<func::FuncOp>()) {
        std::string fname = (f.getSymName() + ".hpp").str();
        llvm::SmallString<256> path(outDir);
        llvm::sys::path::append(path, fname);

        std::error_code fe;
        llvm::raw_fd_ostream os(path, fe, llvm::sys::fs::OF_Text);
        if (fe) {
          llvm::errs() << "error: cannot open " << path << ": " << fe.message() << "\n";
          return 1;
        }

        os << "// pyCircuit C++ emission (prototype)\n";
        os << "#pragma once\n";
        os << "#include <cstdlib>\n";
        os << "#include <iostream>\n";
        os << "#include <pyc/cpp/pyc_sim.hpp>\n";
        for (const std::string &dep : deps[f.getSymName()]) {
          os << "#include \"" << dep << ".hpp\"\n";
        }
        os << "\nnamespace pyc::gen {\n\n";
        if (failed(pyc::emitCppFunc(*module, f, os)))
          return 1;
        os << "} // namespace pyc::gen\n";

        cppFiles.push_back(fname);
      }

      if (failed(updateManifest(outDir, top, /*verilogMods=*/std::nullopt, std::move(cppFiles))))
        return 1;
      return 0;
    }

    llvm::errs() << "error: unknown --emit kind: " << emitKind << "\n";
    return 1;
  }

  std::error_code ec;
  llvm::raw_fd_ostream os(outputFilename, ec, llvm::sys::fs::OF_Text);
  if (ec) {
    llvm::errs() << "error: cannot open " << outputFilename << ": " << ec.message() << "\n";
    return 1;
  }

  if (emitKind == "verilog") {
    pyc::VerilogEmitterOptions opts;
    opts.includePrimitives = includePrims;
    if (targetKind == "fpga")
      opts.targetFpga = true;
    else if (targetKind != "default") {
      llvm::errs() << "error: unknown --target: " << targetKind << " (expected: default|fpga)\n";
      return 1;
    }
    if (failed(pyc::emitVerilog(*module, os, opts)))
      return 1;
    return 0;
  }
  if (emitKind == "cpp") {
    if (failed(pyc::emitCpp(*module, os)))
      return 1;
    return 0;
  }

  llvm::errs() << "error: unknown --emit kind: " << emitKind << "\n";
  return 1;
}
