#pragma once

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/Support/LogicalResult.h"
#include "llvm/Support/raw_ostream.h"

namespace pyc {

struct VerilogEmitterOptions {
  bool includePrimitives = true;
  bool targetFpga = false;
  // Emit a net that has more than one `pyc.assign` anyway, instead of failing.
  // Two continuous drivers on one net is undefined in Verilog and the emitter
  // has no way to pick a winner, so this produces a netlist that does not
  // implement the design; it exists only to reproduce the historical output.
  bool allowMultiDriven = false;
};

::mlir::LogicalResult emitVerilog(::mlir::ModuleOp module, ::llvm::raw_ostream &os,
                                  const VerilogEmitterOptions &opts = {});

::mlir::LogicalResult emitVerilogFunc(::mlir::ModuleOp module, ::mlir::func::FuncOp f, ::llvm::raw_ostream &os,
                                      const VerilogEmitterOptions &opts = {});

} // namespace pyc
