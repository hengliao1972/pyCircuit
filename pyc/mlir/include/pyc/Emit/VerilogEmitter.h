#pragma once

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/Support/LogicalResult.h"
#include "llvm/Support/raw_ostream.h"

namespace pyc {

struct VerilogEmitterOptions {
  bool includePrimitives = true;
  bool targetFpga = false;
};

::mlir::LogicalResult emitVerilog(::mlir::ModuleOp module, ::llvm::raw_ostream &os,
                                  const VerilogEmitterOptions &opts = {});

::mlir::LogicalResult emitVerilogFunc(::mlir::ModuleOp module, ::mlir::func::FuncOp f, ::llvm::raw_ostream &os,
                                      const VerilogEmitterOptions &opts = {});

} // namespace pyc
