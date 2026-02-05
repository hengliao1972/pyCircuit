//===- PYCOps.td ----------------------------------------------------------===//
//
// PYC dialect operations.
//
//===----------------------------------------------------------------------===//

include "mlir/IR/OpBase.td"
include "mlir/IR/CommonAttrConstraints.td"
include "mlir/Interfaces/SideEffectInterfaces.td"
include "mlir/Interfaces/InferTypeOpInterface.td"
include "pyc/Dialect/PYC/PYCTypes.td"

class PYC_Op<string mnemonic, list<Trait> traits = []> :
    Op<PYCDialect, mnemonic, traits>;

def PYC_ConstantOp : PYC_Op<"constant", [Pure, ConstantLike]> {
  let summary = "Constant integer";
  let arguments = (ins APIntAttr:$value);
  let results = (outs AnyInteger:$result);
  let hasCustomAssemblyFormat = 1;
  let hasFolder = 1;
}

def PYC_AddOp : PYC_Op<"add", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Integer add (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_SubOp : PYC_Op<"sub", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Integer subtract (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_MulOp : PYC_Op<"mul", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Integer multiply (combinational, truncating)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_UdivOp : PYC_Op<"udiv", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Unsigned integer divide (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_UremOp : PYC_Op<"urem", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Unsigned integer remainder (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_SdivOp : PYC_Op<"sdiv", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Signed integer divide (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_SremOp : PYC_Op<"srem", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Signed integer remainder (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_MuxOp : PYC_Op<"mux", [Pure, AllTypesMatch<["a", "b", "result"]>]> {
  let summary = "2:1 mux (combinational)";
  let arguments = (ins I1:$sel, AnyInteger:$a, AnyInteger:$b);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$sel `,` $a `,` $b attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_AndOp : PYC_Op<"and", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Bitwise AND (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_OrOp : PYC_Op<"or", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Bitwise OR (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_XorOp : PYC_Op<"xor", [Pure, SameTypeOperands, SameOperandsAndResultType]> {
  let summary = "Bitwise XOR (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_NotOp : PYC_Op<"not", [Pure, SameOperandsAndResultType]> {
  let summary = "Bitwise NOT (combinational)";
  let arguments = (ins AnyInteger:$in);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_EqOp : PYC_Op<"eq", [Pure, SameTypeOperands]> {
  let summary = "Integer equality compare";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs I1:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs)";
  let hasFolder = 1;
}

def PYC_UltOp : PYC_Op<"ult", [Pure, SameTypeOperands]> {
  let summary = "Unsigned less-than compare";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs I1:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs)";
  let hasFolder = 1;
}

def PYC_SltOp : PYC_Op<"slt", [Pure, SameTypeOperands]> {
  let summary = "Signed less-than compare";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs I1:$result);
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs)";
  let hasFolder = 1;
}

def PYC_TruncOp : PYC_Op<"trunc", [Pure]> {
  let summary = "Integer truncate";
  let arguments = (ins AnyInteger:$in);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($in) `->` type($result)";
  let hasFolder = 1;
}

def PYC_ZextOp : PYC_Op<"zext", [Pure]> {
  let summary = "Integer zero-extend";
  let arguments = (ins AnyInteger:$in);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($in) `->` type($result)";
  let hasFolder = 1;
}

def PYC_SextOp : PYC_Op<"sext", [Pure]> {
  let summary = "Integer sign-extend";
  let arguments = (ins AnyInteger:$in);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($in) `->` type($result)";
  let hasFolder = 1;
}

def PYC_ExtractOp : PYC_Op<"extract", [Pure]> {
  let summary = "Bit-slice extraction";
  let arguments = (ins AnyInteger:$in, I64Attr:$lsb);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($in) `->` type($result)";
  let hasFolder = 1;
}

def PYC_ShliOp : PYC_Op<"shli", [Pure, SameOperandsAndResultType]> {
  let summary = "Shift-left by immediate";
  let arguments = (ins AnyInteger:$in, I64Attr:$amount);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_LshriOp : PYC_Op<"lshri", [Pure, SameOperandsAndResultType]> {
  let summary = "Logical shift-right by immediate";
  let arguments = (ins AnyInteger:$in, I64Attr:$amount);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_AshriOp : PYC_Op<"ashri", [Pure, SameOperandsAndResultType]> {
  let summary = "Arithmetic shift-right by immediate";
  let arguments = (ins AnyInteger:$in, I64Attr:$amount);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_ConcatOp : PYC_Op<"concat", [Pure]> {
  let summary = "Concatenate integers (MSB-first)";
  let arguments = (ins Variadic<AnyInteger>:$inputs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "`(` $inputs `)` attr-dict `:` `(` type($inputs) `)` `->` type($result)";
  let hasFolder = 1;
}

def PYC_AliasOp : PYC_Op<"alias", [Pure, SameOperandsAndResultType]> {
  let summary = "Alias a value (for naming / debug)";
  let arguments = (ins AnyType:$in);
  let results = (outs AnyType:$result);
  let assemblyFormat = "$in attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_WireOp : PYC_Op<"wire"> {
  let summary = "Declare a combinational wire (placeholder)";
  let results = (outs AnyInteger:$result);
  let assemblyFormat = "attr-dict `:` type($result)";
}

def PYC_AssignOp : PYC_Op<"assign", [SameTypeOperands, MemoryEffects<[MemWrite]>]> {
  let summary = "Drive a pyc.wire with a value";
  let arguments = (ins AnyInteger:$dst, AnyInteger:$src);
  let hasVerifier = 1;
  let assemblyFormat = "$dst `,` $src attr-dict `:` type($dst)";
}

def PYC_RegOp : PYC_Op<"reg", [MemoryEffects<[MemRead, MemWrite]>, AllTypesMatch<["next", "init", "q"]>]> {
  let summary = "Clocked register with reset + enable";
  let arguments = (ins PYC_ClockType:$clk, PYC_ResetType:$rst, I1:$en, AnyInteger:$next, AnyInteger:$init);
  let results = (outs AnyInteger:$q);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $rst `,` $en `,` $next `,` $init attr-dict `:` type($q)";
}

def PYC_FifoOp : PYC_Op<"fifo", [MemoryEffects<[MemRead, MemWrite]>, AllTypesMatch<["in_data", "out_data"]>]> {
  let summary = "Ready/valid FIFO (single-clock, strict handshake)";
  let arguments = (ins PYC_ClockType:$clk, PYC_ResetType:$rst, I1:$in_valid, AnyInteger:$in_data, I1:$out_ready);
  let results = (outs I1:$in_ready, I1:$out_valid, AnyInteger:$out_data);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $rst `,` $in_valid `,` $in_data `,` $out_ready attr-dict `:` type($in_data)";
}

def PYC_ByteMemOp : PYC_Op<"byte_mem", [MemoryEffects<[MemRead, MemWrite]>,
                                       AllTypesMatch<["raddr", "waddr"]>,
                                       AllTypesMatch<["wdata", "rdata"]>]> {
  let summary = "Byte-addressed memory (async read, sync write, prototype)";
  let arguments = (ins PYC_ClockType:$clk,
                       PYC_ResetType:$rst,
                       AnyInteger:$raddr,
                       I1:$wvalid,
                       AnyInteger:$waddr,
                       AnyInteger:$wdata,
                       AnyInteger:$wstrb);
  let results = (outs AnyInteger:$rdata);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $rst `,` $raddr `,` $wvalid `,` $waddr `,` $wdata `,` $wstrb attr-dict `:` type($raddr) `,` type($rdata) `,` type($wstrb)";
}

def PYC_SyncMemOp : PYC_Op<"sync_mem", [MemoryEffects<[MemRead, MemWrite]>,
                                       AllTypesMatch<["raddr", "waddr"]>,
                                       AllTypesMatch<["wdata", "rdata"]>]> {
  let summary = "Synchronous 1R1W memory (registered read data, prototype)";
  let arguments = (ins PYC_ClockType:$clk,
                       PYC_ResetType:$rst,
                       I1:$ren,
                       AnyInteger:$raddr,
                       I1:$wvalid,
                       AnyInteger:$waddr,
                       AnyInteger:$wdata,
                       AnyInteger:$wstrb);
  let results = (outs AnyInteger:$rdata);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $rst `,` $ren `,` $raddr `,` $wvalid `,` $waddr `,` $wdata `,` $wstrb attr-dict `:` type($raddr) `,` type($rdata) `,` type($wstrb)";
}

def PYC_SyncMemDPOp : PYC_Op<"sync_mem_dp", [MemoryEffects<[MemRead, MemWrite]>,
                                            AllTypesMatch<["raddr0", "raddr1", "waddr"]>,
                                            AllTypesMatch<["wdata", "rdata0", "rdata1"]>]> {
  let summary = "Synchronous 2R1W memory (dual-read, registered outputs, prototype)";
  let arguments = (ins PYC_ClockType:$clk,
                       PYC_ResetType:$rst,
                       I1:$ren0,
                       AnyInteger:$raddr0,
                       I1:$ren1,
                       AnyInteger:$raddr1,
                       I1:$wvalid,
                       AnyInteger:$waddr,
                       AnyInteger:$wdata,
                       AnyInteger:$wstrb);
  let results = (outs AnyInteger:$rdata0, AnyInteger:$rdata1);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $rst `,` $ren0 `,` $raddr0 `,` $ren1 `,` $raddr1 `,` $wvalid `,` $waddr `,` $wdata `,` $wstrb attr-dict `:` type($raddr0) `,` type($rdata0) `,` type($wstrb)";
}

def PYC_AsyncFifoOp : PYC_Op<"async_fifo", [MemoryEffects<[MemRead, MemWrite]>, AllTypesMatch<["in_data", "out_data"]>]> {
  let summary = "Ready/valid async FIFO (dual-clock, strict handshake, prototype)";
  let arguments = (ins PYC_ClockType:$in_clk,
                       PYC_ResetType:$in_rst,
                       PYC_ClockType:$out_clk,
                       PYC_ResetType:$out_rst,
                       I1:$in_valid,
                       AnyInteger:$in_data,
                       I1:$out_ready);
  let results = (outs I1:$in_ready, I1:$out_valid, AnyInteger:$out_data);
  let hasVerifier = 1;
  let assemblyFormat = "$in_clk `,` $in_rst `,` $out_clk `,` $out_rst `,` $in_valid `,` $in_data `,` $out_ready attr-dict `:` type($in_data)";
}

def PYC_CdcSyncOp : PYC_Op<"cdc_sync", [MemoryEffects<[MemRead, MemWrite]>, AllTypesMatch<["in", "out"]>]> {
  let summary = "CDC synchronizer (dst-clocked, prototype)";
  let arguments = (ins PYC_ClockType:$clk, PYC_ResetType:$rst, AnyInteger:$in);
  let results = (outs AnyInteger:$out);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $rst `,` $in attr-dict `:` type($out)";
}

// Fused combinational region (prototype).
//
// This is intended as a codegen-friendly wrapper that allows backends to emit
// a single flattened combinational block (Verilog assigns / inlined C++ function)
// instead of instantiating lots of tiny primitives.
def PYC_CombOp : PYC_Op<"comb", [Pure, IsolatedFromAbove]> {
  let summary = "Fused combinational region";
  let arguments = (ins Variadic<AnyType>:$inputs);
  let results = (outs Variadic<AnyType>:$outputs);
  let regions = (region SizedRegion<1>:$body);
  let hasVerifier = 1;
  let assemblyFormat = "`(` $inputs `)` attr-dict `:` functional-type($inputs, $outputs) $body";
}

def PYC_YieldOp : PYC_Op<"yield", [Terminator, HasParent<"CombOp">]> {
  let summary = "Yield values from pyc.comb";
  let arguments = (ins Variadic<AnyType>:$values);
  let assemblyFormat = "$values attr-dict `:` type($values)";
}
