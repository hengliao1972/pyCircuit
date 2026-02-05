from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Signal:
    ref: str
    ty: str

    def __str__(self) -> str:
        return self.ref


class Module:
    def __init__(self, name: str) -> None:
        self.name = name
        self._args: list[tuple[str, Signal]] = []
        self._results: list[tuple[str, Signal]] = []
        self._lines: list[str] = []
        self._next_tmp = 0
        self._indent_level = 1
        self._finalizers: list[Callable[[], None]] = []
        self._finalized = False

    # --- types ---
    def clock(self, name: str) -> Signal:
        return self._arg(name, "!pyc.clock")

    def reset(self, name: str) -> Signal:
        return self._arg(name, "!pyc.reset")

    def i(self, width: int) -> str:
        if width <= 0:
            raise ValueError("width must be > 0")
        return f"i{int(width)}"

    def input(self, name: str, *, width: int) -> Signal:
        return self._arg(name, self.i(width))

    def output(self, name: str, value: Signal) -> None:
        self._results.append((name, value))

    # --- builders ---
    def const(self, value: int, *, width: int) -> Signal:
        ty = self.i(width)
        if width <= 0:
            raise ValueError("width must be > 0")
        # Represent negative literals in two's complement at the requested width.
        value = int(value) & ((1 << int(width)) - 1)
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.constant {value} : {ty}")
        return Signal(ref=tmp, ty=ty)

    def add(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "add")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.add {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def sub(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "sub")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.sub {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def mul(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "mul")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.mul {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def udiv(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "udiv")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.udiv {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def urem(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "urem")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.urem {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def sdiv(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "sdiv")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.sdiv {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def srem(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "srem")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.srem {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def mux(self, sel: Signal, a: Signal, b: Signal) -> Signal:
        if sel.ty != "i1":
            raise TypeError("mux sel must be i1")
        self._require_same_ty(a, b, "mux")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.mux {sel.ref}, {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def and_(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "and")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.and {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def or_(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "or")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.or {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def xor(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "xor")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.xor {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def not_(self, a: Signal) -> Signal:
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.not {a.ref} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def eq(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "eq")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.eq {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty="i1")

    def ult(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "ult")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.ult {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty="i1")

    def slt(self, a: Signal, b: Signal) -> Signal:
        self._require_same_ty(a, b, "slt")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.slt {a.ref}, {b.ref} : {a.ty}")
        return Signal(ref=tmp, ty="i1")

    def trunc(self, a: Signal, *, width: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("trunc requires an integer input")
        out_ty = self.i(width)
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.trunc {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def zext(self, a: Signal, *, width: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("zext requires an integer input")
        out_ty = self.i(width)
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.zext {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def sext(self, a: Signal, *, width: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("sext requires an integer input")
        out_ty = self.i(width)
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.sext {a.ref} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def extract(self, a: Signal, *, lsb: int, width: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("extract requires an integer input")
        if lsb < 0:
            raise ValueError("extract lsb must be >= 0")
        out_ty = self.i(width)
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.extract {a.ref} {{lsb = {int(lsb)}}} : {a.ty} -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def shli(self, a: Signal, *, amount: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("shli requires an integer input")
        if amount < 0:
            raise ValueError("shli amount must be >= 0")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.shli {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def lshri(self, a: Signal, *, amount: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("lshri requires an integer input")
        if amount < 0:
            raise ValueError("lshri amount must be >= 0")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.lshri {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def ashri(self, a: Signal, *, amount: int) -> Signal:
        if not a.ty.startswith("i"):
            raise TypeError("ashri requires an integer input")
        if amount < 0:
            raise ValueError("ashri amount must be >= 0")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.ashri {a.ref} {{amount = {int(amount)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    def concat(self, *inputs: Signal) -> Signal:
        """Concatenate integer signals into a packed bus (MSB-first)."""
        if not inputs:
            raise ValueError("concat requires at least one input")

        def w(ty: str) -> int:
            if not ty.startswith("i"):
                raise TypeError("concat only supports integer types")
            try:
                return int(ty[1:])
            except ValueError as e:
                raise TypeError(f"invalid integer type: {ty!r}") from e

        out_w = sum(w(s.ty) for s in inputs)
        out_ty = self.i(out_w)
        tmp = self._tmp()
        op_list = ", ".join(s.ref for s in inputs)
        ty_list = ", ".join(s.ty for s in inputs)
        self._emit(f"{tmp} = pyc.concat ({op_list}) : ({ty_list}) -> {out_ty}")
        return Signal(ref=tmp, ty=out_ty)

    def alias(self, a: Signal, *, name: str | None = None) -> Signal:
        """Alias a value (pure) to attach a debug name in codegen."""
        tmp = self._tmp()
        if name is None:
            self._emit(f"{tmp} = pyc.alias {a.ref} : {a.ty}")
        else:
            self._emit(f'{tmp} = pyc.alias {a.ref} {{pyc.name = "{name}"}} : {a.ty}')
        return Signal(ref=tmp, ty=a.ty)

    def new_wire(self, *, width: int, name: str | None = None) -> Signal:
        ty = self.i(width)
        tmp = self._tmp()
        if name is None:
            self._emit(f"{tmp} = pyc.wire : {ty}")
        else:
            self._emit(f'{tmp} = pyc.wire {{pyc.name = "{name}"}} : {ty}')
        return Signal(ref=tmp, ty=ty)

    def assign(self, dst: Signal, src: Signal) -> None:
        self._require_same_ty(dst, src, "assign")
        self._emit(f"pyc.assign {dst.ref}, {src.ref} : {dst.ty}")

    def reg(self, clk: Signal, rst: Signal, en: Signal, next_: Signal, init: Signal) -> Signal:
        if clk.ty != "!pyc.clock":
            raise TypeError("reg clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("reg rst must be !pyc.reset")
        if en.ty != "i1":
            raise TypeError("reg en must be i1")
        self._require_same_ty(next_, init, "reg")
        tmp = self._tmp()
        self._emit(f"{tmp} = pyc.reg {clk.ref}, {rst.ref}, {en.ref}, {next_.ref}, {init.ref} : {next_.ty}")
        return Signal(ref=tmp, ty=next_.ty)

    def fifo(
        self,
        clk: Signal,
        rst: Signal,
        in_valid: Signal,
        in_data: Signal,
        out_ready: Signal,
        *,
        depth: int,
    ) -> tuple[Signal, Signal, Signal]:
        if clk.ty != "!pyc.clock":
            raise TypeError("fifo clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("fifo rst must be !pyc.reset")
        if in_valid.ty != "i1":
            raise TypeError("fifo in_valid must be i1")
        if out_ready.ty != "i1":
            raise TypeError("fifo out_ready must be i1")
        if depth <= 0:
            raise ValueError("fifo depth must be > 0")
        in_ready = self._tmp()
        out_valid = self._tmp()
        out_data = self._tmp()
        self._emit(
            f"{in_ready}, {out_valid}, {out_data} = pyc.fifo {clk.ref}, {rst.ref}, {in_valid.ref}, {in_data.ref}, {out_ready.ref} "
            + f'{{depth = {int(depth)}}} : {in_data.ty}'
        )
        return Signal(in_ready, "i1"), Signal(out_valid, "i1"), Signal(out_data, in_data.ty)

    def byte_mem(
        self,
        clk: Signal,
        rst: Signal,
        raddr: Signal,
        wvalid: Signal,
        waddr: Signal,
        wdata: Signal,
        wstrb: Signal,
        *,
        depth: int,
        name: str | None = None,
    ) -> Signal:
        """Byte-addressed memory (async read + sync write, prototype)."""
        if clk.ty != "!pyc.clock":
            raise TypeError("byte_mem clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("byte_mem rst must be !pyc.reset")
        if wvalid.ty != "i1":
            raise TypeError("byte_mem wvalid must be i1")
        if raddr.ty != waddr.ty:
            raise TypeError("byte_mem raddr/waddr must have the same type")
        if wdata.ty != "i64" and not wdata.ty.startswith("i"):
            raise TypeError("byte_mem wdata must be an integer type")
        if wstrb.ty != "i8" and not wstrb.ty.startswith("i"):
            raise TypeError("byte_mem wstrb must be an integer type")
        if depth <= 0:
            raise ValueError("byte_mem depth must be > 0")

        tmp = self._tmp()
        attrs = f"{{depth = {int(depth)}"
        if name is not None:
            attrs += f', name = "{name}"'
        attrs += "}"
        self._emit(
            f"{tmp} = pyc.byte_mem {clk.ref}, {rst.ref}, {raddr.ref}, {wvalid.ref}, {waddr.ref}, {wdata.ref}, {wstrb.ref} "
            + f"{attrs} : {raddr.ty}, {wdata.ty}, {wstrb.ty}"
        )
        return Signal(ref=tmp, ty=wdata.ty)

    def sync_mem(
        self,
        clk: Signal,
        rst: Signal,
        ren: Signal,
        raddr: Signal,
        wvalid: Signal,
        waddr: Signal,
        wdata: Signal,
        wstrb: Signal,
        *,
        depth: int,
        name: str | None = None,
    ) -> Signal:
        """Synchronous 1R1W memory (registered read data, prototype)."""
        if clk.ty != "!pyc.clock":
            raise TypeError("sync_mem clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("sync_mem rst must be !pyc.reset")
        if ren.ty != "i1":
            raise TypeError("sync_mem ren must be i1")
        if wvalid.ty != "i1":
            raise TypeError("sync_mem wvalid must be i1")
        if raddr.ty != waddr.ty:
            raise TypeError("sync_mem raddr/waddr must have the same type")
        if depth <= 0:
            raise ValueError("sync_mem depth must be > 0")

        tmp = self._tmp()
        attrs = f"{{depth = {int(depth)}"
        if name is not None:
            attrs += f', name = "{name}"'
        attrs += "}"
        self._emit(
            f"{tmp} = pyc.sync_mem {clk.ref}, {rst.ref}, {ren.ref}, {raddr.ref}, {wvalid.ref}, {waddr.ref}, {wdata.ref}, {wstrb.ref} "
            + f"{attrs} : {raddr.ty}, {wdata.ty}, {wstrb.ty}"
        )
        return Signal(ref=tmp, ty=wdata.ty)

    def sync_mem_dp(
        self,
        clk: Signal,
        rst: Signal,
        ren0: Signal,
        raddr0: Signal,
        ren1: Signal,
        raddr1: Signal,
        wvalid: Signal,
        waddr: Signal,
        wdata: Signal,
        wstrb: Signal,
        *,
        depth: int,
        name: str | None = None,
    ) -> tuple[Signal, Signal]:
        """Synchronous 2R1W memory (registered outputs, prototype)."""
        if clk.ty != "!pyc.clock":
            raise TypeError("sync_mem_dp clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("sync_mem_dp rst must be !pyc.reset")
        if ren0.ty != "i1" or ren1.ty != "i1":
            raise TypeError("sync_mem_dp ren0/ren1 must be i1")
        if wvalid.ty != "i1":
            raise TypeError("sync_mem_dp wvalid must be i1")
        if raddr0.ty != raddr1.ty or raddr0.ty != waddr.ty:
            raise TypeError("sync_mem_dp raddr0/raddr1/waddr must have the same type")
        if depth <= 0:
            raise ValueError("sync_mem_dp depth must be > 0")

        out0 = self._tmp()
        out1 = self._tmp()
        attrs = f"{{depth = {int(depth)}"
        if name is not None:
            attrs += f', name = "{name}"'
        attrs += "}"
        self._emit(
            f"{out0}, {out1} = pyc.sync_mem_dp {clk.ref}, {rst.ref}, {ren0.ref}, {raddr0.ref}, {ren1.ref}, {raddr1.ref}, "
            + f"{wvalid.ref}, {waddr.ref}, {wdata.ref}, {wstrb.ref} {attrs} : {raddr0.ty}, {wdata.ty}, {wstrb.ty}"
        )
        return Signal(ref=out0, ty=wdata.ty), Signal(ref=out1, ty=wdata.ty)

    def async_fifo(
        self,
        in_clk: Signal,
        in_rst: Signal,
        out_clk: Signal,
        out_rst: Signal,
        in_valid: Signal,
        in_data: Signal,
        out_ready: Signal,
        *,
        depth: int,
    ) -> tuple[Signal, Signal, Signal]:
        if in_clk.ty != "!pyc.clock" or out_clk.ty != "!pyc.clock":
            raise TypeError("async_fifo clk must be !pyc.clock")
        if in_rst.ty != "!pyc.reset" or out_rst.ty != "!pyc.reset":
            raise TypeError("async_fifo rst must be !pyc.reset")
        if in_valid.ty != "i1":
            raise TypeError("async_fifo in_valid must be i1")
        if out_ready.ty != "i1":
            raise TypeError("async_fifo out_ready must be i1")
        if depth <= 0:
            raise ValueError("async_fifo depth must be > 0")
        in_ready = self._tmp()
        out_valid = self._tmp()
        out_data = self._tmp()
        self._emit(
            f"{in_ready}, {out_valid}, {out_data} = pyc.async_fifo {in_clk.ref}, {in_rst.ref}, {out_clk.ref}, {out_rst.ref}, "
            + f"{in_valid.ref}, {in_data.ref}, {out_ready.ref} {{depth = {int(depth)}}} : {in_data.ty}"
        )
        return Signal(in_ready, "i1"), Signal(out_valid, "i1"), Signal(out_data, in_data.ty)

    def cdc_sync(self, clk: Signal, rst: Signal, a: Signal, *, stages: int | None = None) -> Signal:
        if clk.ty != "!pyc.clock":
            raise TypeError("cdc_sync clk must be !pyc.clock")
        if rst.ty != "!pyc.reset":
            raise TypeError("cdc_sync rst must be !pyc.reset")
        tmp = self._tmp()
        if stages is None:
            self._emit(f"{tmp} = pyc.cdc_sync {clk.ref}, {rst.ref}, {a.ref} : {a.ty}")
        else:
            self._emit(f"{tmp} = pyc.cdc_sync {clk.ref}, {rst.ref}, {a.ref} {{stages = {int(stages)}}} : {a.ty}")
        return Signal(ref=tmp, ty=a.ty)

    # --- structured emission helpers (for AST/JIT frontends) ---
    def emit_line(self, line: str) -> None:
        """Emit a raw line at the current indentation level (inside func body)."""
        self._emit(line)

    def push_indent(self) -> None:
        self._indent_level += 1

    def pop_indent(self) -> None:
        if self._indent_level <= 1:
            raise RuntimeError("indent underflow")
        self._indent_level -= 1

    def index_const(self, value: int) -> Signal:
        tmp = self._tmp()
        self._emit(f"{tmp} = arith.constant {int(value)} : index")
        return Signal(ref=tmp, ty="index")

    # --- emission ---
    def emit_mlir(self) -> str:
        if not self._finalized:
            self._finalized = True
            for fn in list(self._finalizers):
                fn()

        arg_sig = ", ".join(f"{sig.ref}: {sig.ty}" for _, sig in self._args)
        res_types = [v.ty for _, v in self._results]
        if len(res_types) == 0:
            res_sig = "-> ()"
            ret_ty = ""
        elif len(res_types) == 1:
            res_sig = f"-> {res_types[0]}"
            ret_ty = res_types[0]
        else:
            res_sig = f"-> ({', '.join(res_types)})"
            ret_ty = ", ".join(res_types)
        in_names = ", ".join(f"\"{n}\"" for n, _ in self._args)
        out_names = ", ".join(f"\"{n}\"" for n, _ in self._results)
        header = (
            f"module {{\n"
            f"func.func @{self.name}({arg_sig}) {res_sig} "
            f"attributes {{arg_names = [{in_names}], result_names = [{out_names}]}} {{\n"
        )
        body = "\n".join(self._lines)
        outs = ", ".join(v.ref for _, v in self._results)
        if outs:
            tail = f"\n  func.return {outs} : {ret_ty}\n}}\n}}\n"
        else:
            tail = "\n  func.return\n}\n}\n"
        return header + body + tail

    # --- finalizers ---
    def add_finalizer(self, fn: Callable[[], None]) -> None:
        if self._finalized:
            raise RuntimeError("cannot add finalizers after emit_mlir()")
        self._finalizers.append(fn)

    # --- internals ---
    def _arg(self, name: str, ty: str) -> Signal:
        ref = f"%{name}"
        s = Signal(ref=ref, ty=ty)
        self._args.append((name, s))
        return s

    def _tmp(self) -> str:
        self._next_tmp += 1
        return f"%v{self._next_tmp}"

    def _emit(self, line: str) -> None:
        self._lines.append(("  " * self._indent_level) + line)

    @staticmethod
    def _require_same_ty(a: Signal, b: Signal, op: str) -> None:
        if a.ty != b.ty:
            raise TypeError(f"{op} requires same types, got {a.ty} and {b.ty}")
