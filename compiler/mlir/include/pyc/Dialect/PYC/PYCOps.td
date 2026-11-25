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

//===----------------------------------------------------------------------===//
// Combinational ops
//===----------------------------------------------------------------------===//

def PYC_ConstantOp : PYC_Op<"constant", [Pure, ConstantLike]> {
  let summary = "Constant integer";
  let arguments = (ins APIntAttr:$value);
  let results = (outs AnyInteger:$result);
  let hasCustomAssemblyFormat = 1;
  let hasFolder = 1;
}

def PYC_AddOp : PYC_Op<"add", [Pure]> {
  let summary = "Integer add (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_SubOp : PYC_Op<"sub", [Pure]> {
  let summary = "Integer subtract (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_MulOp : PYC_Op<"mul", [Pure]> {
  let summary = "Integer multiply (combinational, truncating)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_UdivOp : PYC_Op<"udiv", [Pure]> {
  let summary = "Unsigned integer divide (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_UremOp : PYC_Op<"urem", [Pure]> {
  let summary = "Unsigned integer remainder (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_SdivOp : PYC_Op<"sdiv", [Pure]> {
  let summary = "Signed integer divide (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_SremOp : PYC_Op<"srem", [Pure]> {
  let summary = "Signed integer remainder (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_MuxOp : PYC_Op<"mux", [Pure]> {
  let summary = "2:1 mux (combinational)";
  let arguments = (ins AnyInteger:$sel, AnyInteger:$a, AnyInteger:$b);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$sel `,` $a `,` $b attr-dict `:` type($sel) `,` type($a) `,` type($b) `->` type($result)";
  let hasFolder = 1;
}

def PYC_AndOp : PYC_Op<"and", [Pure]> {
  let summary = "Bitwise AND (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_OrOp : PYC_Op<"or", [Pure]> {
  let summary = "Bitwise OR (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_XorOp : PYC_Op<"xor", [Pure]> {
  let summary = "Bitwise XOR (combinational)";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
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

def PYC_EqOp : PYC_Op<"eq", [Pure]> {
  let summary = "Integer equality compare";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_UltOp : PYC_Op<"ult", [Pure]> {
  let summary = "Unsigned less-than compare";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
  let hasFolder = 1;
}

def PYC_SltOp : PYC_Op<"slt", [Pure]> {
  let summary = "Signed less-than compare";
  let arguments = (ins AnyInteger:$lhs, AnyInteger:$rhs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$lhs `,` $rhs attr-dict `:` type($lhs) `,` type($rhs) `->` type($result)";
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
  let arguments = (ins AnyInteger:$in, I64Attr:$lsb, OptionalAttr<I64Attr>:$msb);
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

def PYC_ShlOp : PYC_Op<"shl", [Pure, AllTypesMatch<["in", "result"]>]> {
  let summary = "Shift-left by value";
  let arguments = (ins AnyInteger:$in, AnyInteger:$amount);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in `,` $amount attr-dict `:` type($result) `,` type($amount)";
}

def PYC_LshrOp : PYC_Op<"lshr", [Pure, AllTypesMatch<["in", "result"]>]> {
  let summary = "Logical shift-right by value";
  let arguments = (ins AnyInteger:$in, AnyInteger:$amount);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in `,` $amount attr-dict `:` type($result) `,` type($amount)";
}

def PYC_AshrOp : PYC_Op<"ashr", [Pure, AllTypesMatch<["in", "result"]>]> {
  let summary = "Arithmetic shift-right by value";
  let arguments = (ins AnyInteger:$in, AnyInteger:$amount);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "$in `,` $amount attr-dict `:` type($result) `,` type($amount)";
}

def PYC_ConcatOp : PYC_Op<"concat", [Pure]> {
  let summary = "Concatenate integers (MSB-first)";
  let arguments = (ins Variadic<AnyInteger>:$inputs);
  let results = (outs AnyInteger:$result);
  let hasVerifier = 1;
  let assemblyFormat = "`(` $inputs `)` attr-dict `:` `(` type($inputs) `)` `->` type($result)";
  let hasFolder = 1;
}

def PYC_AliasOp : PYC_Op<"alias", [SameOperandsAndResultType,
    DeclareOpInterfaceMethods<MemoryEffectsOpInterface>]> {
  let summary = "Alias a value (for naming / debug)";
  let arguments = (ins AnyType:$in);
  let results = (outs AnyType:$result);
  let assemblyFormat = "$in attr-dict `:` type($result)";
  let hasFolder = 1;
}

def PYC_ResetActiveOp : PYC_Op<"reset_active", [Pure]> {
  let summary = "View reset port as active-high i1 for data-path gating (1 = reset asserted; matches pyCircuit TB/SV)";
  let arguments = (ins PYC_ResetType:$rst);
  let results = (outs I1:$active);
  let assemblyFormat = "$rst attr-dict `:` type($active)";
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

// Compact IR form of DEPTH cascaded registers with common controls. Backends
// may choose an efficient simulator representation, but retain DEPTH states.
def PYC_DelayLineOp : PYC_Op<"delay_line", [MemoryEffects<[MemRead, MemWrite]>, AllTypesMatch<["next", "init", "q"]>]> {
  let summary = "Fixed-latency delay line with reset + enable";
  let arguments = (ins PYC_ClockType:$clk, PYC_ResetType:$rst, I1:$en, AnyInteger:$next, AnyInteger:$init);
  let results = (outs AnyInteger:$q);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $rst `,` $en `,` $next `,` $init attr-dict `:` type($q)";
}

// Read-only view of an internal stage of a fixed-depth delay line.  The
// producer remains the owner of the storage; this op only exposes the value
// that would be present after `depth` enabled edges.
def PYC_DelayTapOp : PYC_Op<"delay_tap",
    [AllTypesMatch<["line", "tap"]>,
     DeclareOpInterfaceMethods<MemoryEffectsOpInterface>]> {
  let summary = "Read-only fixed-depth delay-line tap";
  let arguments = (ins AnyInteger:$line);
  let results = (outs AnyInteger:$tap);
  let hasVerifier = 1;
  let assemblyFormat = "$line attr-dict `:` type($tap)";
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
                       AnyInteger:$raddr,
                       I1:$wvalid,
                       AnyInteger:$waddr,
                       AnyInteger:$wdata,
                       AnyInteger:$wstrb);
  let results = (outs AnyInteger:$rdata);
  let hasVerifier = 1;
  let assemblyFormat = "$clk `,` $raddr `,` $wvalid `,` $waddr `,` $wdata `,` $wstrb attr-dict `:` type($raddr) `,` type($rdata) `,` type($wstrb)";
}

def PYC_SyncMemNMOp : PYC_Op<"sync_mem_nm", [MemoryEffects<[MemRead, MemWrite]>, AttrSizedOperandSegments]> {
  let summary = "Synchronous NxM memory (N registered read ports, M write ports; higher write index wins same-address write conflicts; read_mode=forward returns the last matching same-edge write, read_mode=old returns pre-write data, prototype)";
  let arguments = (ins Variadic<I1>:$rens,
                        Variadic<AnyInteger>:$raddrs,
                        Variadic<I1>:$wvalids,
                        Variadic<AnyInteger>:$waddrs,
                        Variadic<AnyInteger>:$wdatas,
                        Variadic<AnyInteger>:$wstrbs,
                        PYC_ClockType:$clk,
                        DefaultValuedStrAttr<StrAttr, "forward">:$read_mode);
  let results = (outs Variadic<AnyInteger>:$rdatas);
  let hasVerifier = 1;
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

def PYC_InstanceOp : PYC_Op<"instance", []> {
  let summary = "Instantiate a sub-module";
  let arguments = (ins Variadic<AnyType>:$inputs, FlatSymbolRefAttr:$callee, OptionalAttr<StrAttr>:$name);
  let results = (outs Variadic<AnyType>:$outputs);
  let hasVerifier = 1;
  let assemblyFormat = "$inputs attr-dict `:` functional-type($inputs, $outputs)";
}

def PYC_AssertOp : PYC_Op<"assert", [MemoryEffects<[MemWrite]>]> {
  let summary = "Simulation-only assertion (prototype)";
  let arguments = (ins I1:$cond, OptionalAttr<StrAttr>:$msg);
  let hasVerifier = 1;
  let assemblyFormat = "$cond attr-dict";
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
