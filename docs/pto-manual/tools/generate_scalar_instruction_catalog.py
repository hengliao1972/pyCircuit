#!/usr/bin/env python3
"""Generate PTO manual scalar instruction pages from the scalar profile JSON."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from html import escape as html_escape
from pathlib import Path


REPO_ROOT = Path("/Users/zhoubot/linx-isa/tools/pyCircuit")
MANUAL_ROOT = REPO_ROOT / "docs/pto-manual"
INPUT_JSON = Path("/Users/zhoubot/linx-isa/.tmp/linxisa-v0.4-scalar-instructions.json")
INPUT_WAVEDROM_DIR = Path("/Users/zhoubot/linx-isa/.tmp/wavedrom-encodings")

DATA_DIR = MANUAL_ROOT / "data/scalar"
FIG_DIR = MANUAL_ROOT / "figs/encoding/scalar_instruction_entries"
SCALAR_DIR = MANUAL_ROOT / "scalar-instructions"
ENTRY_DIR = SCALAR_DIR / "entries"

MIGRATED_JSON = DATA_DIR / "scalar-instructions-32bit.json"
CATALOG_TEX = SCALAR_DIR / "generated_scalar_instruction_catalog.tex"


CATEGORY_ORDER = ["ALU", "BRU", "AGU", "AMO", "FSU", "SYS", "OTHER"]
CATEGORY_TITLES = {
    "ALU": "Integer ALU and Bit-Manipulation",
    "BRU": "Branch, Compare, and PC Control",
    "AGU": "Load, Store, and Address Generation",
    "AMO": "Atomic Memory Operations",
    "FSU": "Floating-Point and Conversion",
    "SYS": "System, Cache, SSR, and Execution Control",
    "OTHER": "Other Scalar Operations",
}
CATEGORY_DESCRIPTIONS = {
    "ALU": (
        "Integer ALU instructions compute register, immediate, word-width, "
        "bit-manipulation, multi-cycle arithmetic, max/min, and compound results."
    ),
    "BRU": (
        "Branch and compare instructions update control flow or materialize "
        "integer condition results using the scalar register file and PC state."
    ),
    "AGU": (
        "Address-generation instructions calculate load/store addresses and "
        "perform scalar memory transfers through base-register, immediate, "
        "symbol, and unscaled addressing forms."
    ),
    "AMO": (
        "Atomic memory operations perform load-reserved, store-conditional, swap, "
        "and arithmetic or logic read-modify-write transfers with encoded ordering "
        "qualifiers."
    ),
    "FSU": (
        "Floating-point and conversion instructions execute scalar FP arithmetic, "
        "FP compares, and integer/FP format conversions."
    ),
    "SYS": (
        "System instructions cover cache maintenance, SSR access, architectural "
        "control, fences, traps, and bring-up or debug-oriented execution controls."
    ),
    "OTHER": "Instructions without a classified execution unit are listed here.",
}

def slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "instruction"


def tex_escape(text: object) -> str:
    s = "" if text is None else str(text)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in s)


def tt(text: object) -> str:
    return r"\texttt{" + tex_escape(text) + "}"


def normalize_operation(text: str) -> str:
    text = text.replace("Write(Dst", "Write(RegDst")
    text = text.replace("Write(Dst,", "Write(RegDst,")
    return text


def is_generic_operation(text: str) -> bool:
    normalized = " ".join(text.strip().split())
    return normalized.startswith("Execute operation") and normalized.endswith("encoded modifiers.")


def derived_purpose(inst: dict) -> str:
    mnemonic = inst["mnemonic"].upper()
    raw = inst.get("manual_description") or ""

    cmp_purpose = {
        "CMP.AND": "Tests whether the bitwise AND of SrcL and the modified SrcR is nonzero, then writes 1 or 0 to RegDst.",
        "CMP.ANDI": "Tests whether the bitwise AND of SrcL and the immediate operand is nonzero, then writes 1 or 0 to RegDst.",
        "CMP.EQ": "Compares SrcL and the modified SrcR for equality and writes 1 or 0 to RegDst.",
        "CMP.EQI": "Compares SrcL and the immediate operand for equality and writes 1 or 0 to RegDst.",
        "CMP.GE": "Performs a signed greater-than-or-equal comparison between SrcL and SrcR and writes 1 or 0 to RegDst.",
        "CMP.GEI": "Performs a signed greater-than-or-equal comparison between SrcL and the immediate operand and writes 1 or 0 to RegDst.",
        "CMP.GEU": "Performs an unsigned greater-than-or-equal comparison between SrcL and SrcR and writes 1 or 0 to RegDst.",
        "CMP.GEUI": "Performs an unsigned greater-than-or-equal comparison between SrcL and the immediate operand and writes 1 or 0 to RegDst.",
        "CMP.LT": "Performs a signed less-than comparison between SrcL and SrcR and writes 1 or 0 to RegDst.",
        "CMP.LTI": "Performs a signed less-than comparison between SrcL and the immediate operand and writes 1 or 0 to RegDst.",
        "CMP.LTU": "Performs an unsigned less-than comparison between SrcL and SrcR and writes 1 or 0 to RegDst.",
        "CMP.LTUI": "Performs an unsigned less-than comparison between SrcL and the immediate operand and writes 1 or 0 to RegDst.",
        "CMP.NE": "Compares SrcL and the modified SrcR for inequality and writes 1 or 0 to RegDst.",
        "CMP.NEI": "Compares SrcL and the immediate operand for inequality and writes 1 or 0 to RegDst.",
        "CMP.OR": "Tests whether the bitwise OR of SrcL and the modified SrcR is nonzero, then writes 1 or 0 to RegDst.",
        "CMP.ORI": "Tests whether the bitwise OR of SrcL and the immediate operand is nonzero, then writes 1 or 0 to RegDst.",
    }
    if mnemonic in cmp_purpose:
        return cmp_purpose[mnemonic]

    fp_purpose = {
        "FABS": "Clears the sign bit of the floating-point source operand and writes the absolute value to RegDst.",
        "FADD": "Adds two floating-point operands of type T and writes the rounded result to RegDst.",
        "FCVT": "Converts SrcL between encoded floating-point formats using the default rounding mode and writes the result to RegDst.",
        "FCVTA": "Converts SrcL between encoded numeric formats using round-away-from-zero and writes the result to RegDst.",
        "FCVTM": "Converts SrcL between encoded numeric formats using round-toward-minus-infinity and writes the result to RegDst.",
        "FCVTN": "Converts SrcL between encoded numeric formats using round-to-nearest and writes the result to RegDst.",
        "FCVTP": "Converts SrcL between encoded numeric formats using round-toward-plus-infinity and writes the result to RegDst.",
        "FCVTZ": "Converts SrcL between encoded numeric formats using round-toward-zero and writes the result to RegDst.",
        "FDIV": "Divides the floating-point left operand by the right operand and writes the rounded result to RegDst.",
        "FEQS": "Performs a signaling floating-point equality comparison and writes 1 or 0 to RegDst.",
        "FEXP": "Computes the floating-point exponential of SrcL and writes the rounded result to RegDst.",
        "FGES": "Performs a signaling floating-point greater-than-or-equal comparison and writes 1 or 0 to RegDst.",
        "FLTS": "Performs a signaling floating-point less-than comparison and writes 1 or 0 to RegDst.",
        "FMADD": "Multiplies two floating-point operands, adds SrcA as a fused operation, and writes the rounded result to RegDst.",
        "FMAX": "Selects the larger floating-point operand and writes it to RegDst.",
        "FMIN": "Selects the smaller floating-point operand and writes it to RegDst.",
        "FMSUB": "Multiplies two floating-point operands, subtracts SrcA as a fused operation, and writes the rounded result to RegDst.",
        "FMUL": "Multiplies two floating-point operands of type T and writes the rounded result to RegDst.",
        "FNES": "Performs a signaling floating-point not-equal comparison and writes 1 or 0 to RegDst.",
        "FNMADD": "Negates the product of two floating-point operands, subtracts SrcA as a fused operation, and writes the rounded result to RegDst.",
        "FNMSUB": "Negates the product of two floating-point operands, adds SrcA as a fused operation, and writes the rounded result to RegDst.",
        "FRECIP": "Computes the reciprocal of the floating-point source operand and writes the rounded result to RegDst.",
        "FSQRT": "Computes the square root of the floating-point source operand and writes the rounded result to RegDst.",
        "FSUB": "Subtracts the right floating-point operand from the left operand and writes the rounded result to RegDst.",
    }
    if mnemonic in fp_purpose:
        return fp_purpose[mnemonic]

    cache_purpose = {
        "BC.IALL": "Invalidates all branch predictor or branch-cache state defined by the scalar profile.",
        "BC.IVA": "Invalidates branch predictor or branch-cache state for the virtual address operand.",
        "DC.CISW": "Cleans and invalidates data-cache state by set/way operand.",
        "DC.CIVA": "Cleans and invalidates data-cache state for the virtual address operand.",
        "DC.CSW": "Cleans data-cache state by set/way operand.",
        "DC.CVA": "Cleans data-cache state for the virtual address operand.",
        "DC.IALL": "Invalidates all data-cache state defined by the scalar profile.",
        "DC.ISW": "Invalidates data-cache state by set/way operand.",
        "DC.IVA": "Invalidates data-cache state for the virtual address operand.",
        "DC.ZVA": "Zeros the data-cache block associated with the virtual address operand.",
        "IC.IALL": "Invalidates all instruction-cache state defined by the scalar profile.",
        "IC.IVA": "Invalidates instruction-cache state for the virtual address operand.",
        "TLB.IA": "Invalidates address-translation state for the supplied address operand.",
        "TLB.IALL": "Invalidates all address-translation state defined by the scalar profile.",
        "TLB.IAV": "Invalidates address-translation state for the supplied virtual address operand.",
        "TLB.IV": "Invalidates virtual-address translation state selected by the encoded operand.",
    }
    if mnemonic in cache_purpose:
        return cache_purpose[mnemonic]

    if re.search(r"selected\s+by\s+the\s+mnemonic", raw):
        return re.sub(
            r"\s+as\s+selected\s+by\s+the\s+mnemonic(?:\s+suffix|\s+and\s+operand\s+type\s+qualifiers)?",
            "",
            raw,
        )

    return raw


def derived_operation(inst: dict) -> str:
    mnemonic = inst["mnemonic"].upper()
    raw = normalize_operation(inst.get("manual_operation_informative") or "")
    if raw and not is_generic_operation(raw):
        return raw

    op_map = {
        "ADDIW": "lhs = Read(SrcL)[31:0]\nrhs = ZeroExtend(uimm)\nresult32 = lhs + rhs\nWrite(RegDst, SignExtend32(result32))",
        "ADDW": "lhs = Read(SrcL)[31:0]\nrhs = ModifiedSrcR(SrcR, SrcRType, shamt)[31:0]\nresult32 = lhs + rhs\nWrite(RegDst, SignExtend32(result32))",
        "ANDW": "lhs = Read(SrcL)[31:0]\nrhs = ModifiedSrcR(SrcR, SrcRType, shamt)[31:0]\nresult32 = lhs & rhs\nWrite(RegDst, SignExtend32(result32))",
        "ORW": "lhs = Read(SrcL)[31:0]\nrhs = ModifiedSrcR(SrcR, SrcRType, shamt)[31:0]\nresult32 = lhs | rhs\nWrite(RegDst, SignExtend32(result32))",
        "XORW": "lhs = Read(SrcL)[31:0]\nrhs = ModifiedSrcR(SrcR, SrcRType, shamt)[31:0]\nresult32 = lhs ^ rhs\nWrite(RegDst, SignExtend32(result32))",
        "SUBIW": "lhs = Read(SrcL)[31:0]\nrhs = ZeroExtend(uimm)\nresult32 = lhs - rhs\nWrite(RegDst, SignExtend32(result32))",
        "SUBW": "lhs = Read(SrcL)[31:0]\nrhs = ModifiedSrcR(SrcR, SrcRType, shamt)[31:0]\nresult32 = lhs - rhs\nWrite(RegDst, SignExtend32(result32))",
        "BCNT": "field = ExtractBits(Read(SrcL), M, N)\nWrite(RegDst, CountOnes(field))",
        "BIC": "mask = BitMask(M, N)\nWrite(RegDst, Read(SrcL) & ~mask)",
        "BIS": "mask = BitMask(M, N)\nWrite(RegDst, Read(SrcL) | mask)",
        "BXS": "field = ExtractBits(Read(SrcL), M, N)\nWrite(RegDst, SignExtend(field))",
        "BXU": "field = ExtractBits(Read(SrcL), M, N)\nWrite(RegDst, ZeroExtend(field))",
        "CLZ": "field = ExtractBits(Read(SrcL), M, N)\nWrite(RegDst, CountLeadingZeros(field))",
        "CTZ": "field = ExtractBits(Read(SrcL), M, N)\nWrite(RegDst, CountTrailingZeros(field))",
        "REV": "field = ExtractBits(Read(SrcL), M, N)\nWrite(RegDst, ReverseBits(field))",
        "FADD": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\nWrite(RegDst, FPRound(lhs + rhs, T))",
        "FSUB": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\nWrite(RegDst, FPRound(lhs - rhs, T))",
        "FMUL": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\nWrite(RegDst, FPRound(lhs * rhs, T))",
        "FDIV": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\nWrite(RegDst, FPRound(lhs / rhs, T))",
        "FMADD": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\naddend = FPRead(SrcA, T)\nWrite(RegDst, FPFusedRound(lhs * rhs + addend, T))",
        "FMSUB": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\nsubtrahend = FPRead(SrcA, T)\nWrite(RegDst, FPFusedRound(lhs * rhs - subtrahend, T))",
        "FNMADD": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\naddend = FPRead(SrcA, T)\nWrite(RegDst, FPFusedRound(-(lhs * rhs) - addend, T))",
        "FNMSUB": "lhs = FPRead(SrcL, T)\nrhs = FPRead(SrcR, T)\nsubtrahend = FPRead(SrcA, T)\nWrite(RegDst, FPFusedRound(-(lhs * rhs) + subtrahend, T))",
        "FEXP": "value = FPRead(SrcL, T)\nWrite(RegDst, FPRound(exp(value), T))",
        "FRECIP": "value = FPRead(SrcL, T)\nWrite(RegDst, FPRound(1.0 / value, T))",
        "FSQRT": "value = FPRead(SrcL, T)\nWrite(RegDst, FPRound(sqrt(value), T))",
        "FCVT": "value = Read(SrcL)\nWrite(RegDst, ConvertFormat(value, srcT, dstT, default_rounding))",
        "FCVTA": "value = Read(SrcL)\nWrite(RegDst, ConvertFormat(value, srcT, dstT, round_away_from_zero))",
        "FCVTM": "value = Read(SrcL)\nWrite(RegDst, ConvertFormat(value, srcT, dstT, round_toward_minus_infinity))",
        "FCVTN": "value = Read(SrcL)\nWrite(RegDst, ConvertFormat(value, srcT, dstT, round_to_nearest))",
        "FCVTP": "value = Read(SrcL)\nWrite(RegDst, ConvertFormat(value, srcT, dstT, round_toward_plus_infinity))",
        "FCVTZ": "value = Read(SrcL)\nWrite(RegDst, ConvertFormat(value, srcT, dstT, round_toward_zero))",
        "SCVTF": "value = SignExtend(Read(SrcL))\nWrite(RegDst, IntToFloat(value, dstT))",
        "UCVTF": "value = ZeroExtend(Read(SrcL))\nWrite(RegDst, UIntToFloat(value, dstT))",
    }
    if mnemonic in op_map:
        return op_map[mnemonic]

    return "Decode operands as shown in the encoding diagram.\nExecute the architectural behavior described by the instruction purpose."


def class_summary(inst: dict) -> str:
    uop_class = inst.get("uop_class")
    if isinstance(uop_class, dict):
        parts = []
        for key in ("uop_kind", "alu_kind", "bru_kind", "agu_kind", "addr_mode", "sys_kind"):
            if key in uop_class:
                parts.append(f"{key}={uop_class[key]}")
        if uop_class.get("split"):
            parts.append("split=" + "+".join(uop_class["split"]))
        return ", ".join(parts) or "-"
    return str(uop_class or "-")


def inst_mnemonic(inst: dict) -> str:
    return str(inst.get("mnemonic", "")).upper()


def inst_asm(inst: dict) -> str:
    return str(inst.get("asm", ""))


def is_load(inst: dict) -> bool:
    return str(inst.get("uop_group", "")).startswith("LDA") or inst_mnemonic(inst).startswith(("L", "LR."))


def is_store(inst: dict) -> bool:
    return str(inst.get("uop_group", "")).startswith("STA") or inst_mnemonic(inst).startswith(("S", "SC."))


def is_memory(inst: dict) -> bool:
    return inst.get("uop_big_kind") in {"AGU", "AMO"} or "Cache" in str(inst.get("group", ""))


def is_branch(inst: dict) -> bool:
    return inst.get("uop_big_kind") == "BRU" and str(inst.get("group")) in {"Branch", "PC-Relative"}


def field_bit_ranges(field: dict) -> str:
    pieces = []
    for piece in field.get("pieces", []):
        msb = piece["insn_msb"]
        lsb = piece["insn_lsb"]
        width = piece["width"]
        pieces.append(f"{msb}:{lsb} ({width}b)")
    return ", ".join(pieces)


def field_width(field: dict) -> int:
    return sum(int(piece.get("width", 0)) for piece in field.get("pieces", []))


def split_note(field: dict) -> str:
    if len(field.get("pieces", [])) > 1:
        return " The immediate is split across non-contiguous encoding slices and reassembled before use."
    return ""


def format_context(inst: dict) -> str:
    asm = inst_asm(inst)
    if "{T}" in asm:
        return " The assembly suffix {T} names the selected floating-point element format."
    if "srcT2dstT" in asm:
        return " The assembly suffix {srcT2dstT} names the source and destination formats."
    return ""


def field_description(inst: dict, field: dict) -> str:
    name = field.get("name", "")
    mnemonic = inst_mnemonic(inst)
    group = str(inst.get("group", ""))
    category = str(inst.get("uop_big_kind", ""))
    asm = inst_asm(inst)
    width = field_width(field)
    signed = field.get("signed")

    if name == "RegDst":
        if mnemonic.startswith("SC."):
            return "5-bit scalar destination register that receives the store-conditional status code; X0 discards the status write."
        if category == "AMO":
            return "5-bit scalar destination register that receives the original memory value returned by the atomic operation; X0 discards the returned value."
        if group == "Compare Instruction" or mnemonic.startswith(("FEQ", "FNE", "FLT", "FGE")):
            return "5-bit scalar destination register that receives the boolean compare result, encoded as 1 for true and 0 for false; X0 discards the write."
        if is_load(inst):
            return "5-bit scalar destination register that receives the loaded value after any sign or zero extension; X0 discards the load result."
        if category == "FSU":
            return "5-bit scalar destination register that receives the floating-point, conversion, or FP-compare result encoded in scalar register storage; X0 discards the write."
        return "5-bit scalar destination register written by this instruction; X0 is hardwired to zero, so writes to X0 are ignored."

    if name == "SrcL":
        if mnemonic.startswith("SC."):
            return "5-bit scalar source register containing the value to store if the reservation check succeeds."
        if category == "AMO" and mnemonic.startswith("LR."):
            return "5-bit scalar source register containing the memory address for the load-reserved operation."
        if category == "AMO":
            return "5-bit scalar source register containing the memory address for the atomic read-modify-write operation."
        if "Cache Maintain" in group or mnemonic.startswith(("DC.", "IC.", "TLB.", "BC.")):
            return "5-bit scalar source register supplying the address or selector operand for this maintenance operation."
        if is_memory(inst):
            return "5-bit scalar source register used as the base address for the memory access."
        if category == "FSU":
            return "5-bit scalar source register holding the left or only floating-point/conversion operand; SrcType or the assembly suffix determines the interpreted format."
        if group == "Branch":
            return "5-bit scalar source register used as the left operand of the branch condition."
        if group == "Compare Instruction":
            return "5-bit scalar source register used as the left operand of the comparison."
        return "5-bit scalar source register used as the left input operand."

    if name == "SrcR":
        if mnemonic.startswith("SC."):
            return "5-bit scalar source register containing the memory address checked against the active reservation."
        if category == "AMO":
            return "5-bit scalar source register containing the update operand for the atomic read-modify-write operation."
        if is_memory(inst):
            return "5-bit scalar source register used as the register offset component of the effective address."
        if category == "FSU":
            return "5-bit scalar source register holding the right floating-point operand."
        if group == "Branch":
            return "5-bit scalar source register used as the right operand of the branch condition."
        if group == "Compare Instruction":
            return "5-bit scalar source register used as the right operand of the comparison before any SrcRType modifier is applied."
        return "5-bit scalar source register used as the right input operand before any encoded modifier or shift is applied."

    if name == "SrcD":
        if is_store(inst):
            return "5-bit scalar source register containing the data value written to memory; the instruction size selects the stored byte, halfword, word, or doubleword portion."
        return "5-bit scalar source register containing the data operand consumed by the instruction."

    if name == "SrcA":
        return "5-bit scalar source register supplying the fused-add or fused-subtract addend operand."

    if name == "SrcP":
        return "5-bit scalar source register supplying the predicate or condition input used by the conditional select operation."

    if name == "SrcZero":
        return "5-bit source-register field that must encode X0; it occupies the standard source slot for encodings that do not consume a second register operand."

    if name == "SrcRType":
        return "2-bit modifier for SrcR. It selects the encoded source form shown in the syntax, such as signed-word, unsigned-word, negated, or unmodified register input."

    if name == "SrcType":
        return "2-bit source format selector for floating-point or conversion operands." + format_context(inst)

    if name == "DstType":
        return "5-bit destination format selector for conversion results." + format_context(inst)

    if name == "shamt":
        return f"{width}-bit shift amount applied to the modified right operand before the ALU or address calculation uses it."

    if name in {"simm", "simm12", "simm17", "simm20", "simm22"}:
        if group == "Branch":
            return f"{width}-bit signed PC-relative branch displacement. The decoded offset is sign-extended and added to the current PC when the condition is true.{split_note(field)}"
        if group == "PC-Relative":
            return f"{width}-bit signed PC-relative displacement used to form the jump or address result.{split_note(field)}"
        if is_memory(inst):
            return f"{width}-bit signed address displacement added to the base register to form the effective address.{split_note(field)}"
        return f"{width}-bit signed immediate operand that is sign-extended before use.{split_note(field)}"

    if name in {"uimm12", "imm20", "imm4", "imml", "immr", "imms"}:
        if is_memory(inst):
            return f"{width}-bit unsigned address displacement or scaled offset used by the addressing mode.{split_note(field)}"
        return f"{width}-bit unsigned immediate operand consumed directly by the operation.{split_note(field)}"

    if name == "aq":
        return "Acquire ordering bit for atomic memory operations. When set, later memory operations cannot be observed before this operation in the selected ordering domain."

    if name == "rl":
        return "Release ordering bit for atomic memory operations. When set, earlier memory operations cannot be observed after this operation in the selected ordering domain."

    if name == "far":
        return "Atomic ordering-domain qualifier used with aq/rl suffixes; the assembly suffix containing f selects this encoded bit."

    if name == "SSR_ID":
        return "12-bit system scalar register identifier selecting the SSR read, write, set, or swap target."

    if name == "LSR_ID":
        return "12-bit local/system register identifier selected by this register-access instruction."

    if name in {"PRED_IMM", "SUCC_IMM"}:
        side = "predecessor" if name == "PRED_IMM" else "successor"
        return f"4-bit fence {side} set. Each bit names a memory or I/O access class participating in the ordering constraint."

    if name in {"RRA_Type", "RST_Type"}:
        return "4-bit architectural event-state selector used by the ACRE event-control instruction."

    if signed is True:
        return f"{width}-bit signed operand field; the decoded value is sign-extended before use.{split_note(field)}"
    if signed is False:
        return f"{width}-bit unsigned operand field; the decoded value is zero-extended before use.{split_note(field)}"
    return f"{width}-bit encoded operand/control field used by the instruction form shown in the syntax."


def field_rows(inst: dict) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    parts = inst.get("encoding_summary", {}).get("parts", [])
    for part in parts:
        for field in part.get("fields", []):
            rows.append((field.get("name", "-"), field_bit_ranges(field), field_description(inst, field)))
    return rows


def field_sentence(field_name: str, bits: str, meaning: str) -> str:
    return rf"\item[{tt(field_name)}] Bits {tt(bits)}. {tex_escape(meaning)}"


def manual_operand_field_map(inst: dict) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    parts = inst.get("encoding_summary", {}).get("parts", [])
    for part in parts:
        for field in part.get("fields", []):
            descriptions[field.get("name", "-")] = field_description(inst, field)
    return descriptions


def encoding_part(inst: dict) -> dict:
    parts = inst.get("encoding_summary", {}).get("parts", [])
    if not parts:
        return {}
    return parts[0]


def asset_paths(inst: dict) -> tuple[Path, Path, Path]:
    source_path = Path(inst["wavedrom_encoding"].get("source_json") or inst["wavedrom_encoding"].get("wavedrom_json"))
    svg_path = Path(inst["wavedrom_encoding"]["svg"])
    if not source_path.exists():
        source_path = INPUT_WAVEDROM_DIR / f"enc_{slug(inst['mnemonic'])}.wavedrom.json"
    if not svg_path.exists():
        svg_path = INPUT_WAVEDROM_DIR / f"enc_{slug(inst['mnemonic'])}.svg"
    base = source_path.name.removesuffix(".wavedrom.json")
    return FIG_DIR / f"{base}.wavedrom.json", FIG_DIR / f"{base}.svg", FIG_DIR / f"{base}.pdf"


def svg_text(text: object) -> str:
    return html_escape("" if text is None else str(text), quote=True)


def field_color(name: str, is_const: bool) -> str:
    if is_const:
        return "#f3f4f6"
    lowered = name.lower()
    if "imm" in lowered:
        return "#fef3c7"
    if name == "RegDst" or name.startswith("Dst"):
        return "#dbeafe"
    if name.startswith("Src"):
        return "#dcfce7"
    if name in {"aq", "rl", "far"} or "type" in lowered:
        return "#ede9fe"
    return "#e0f2fe"


def display_field_name(name: str, width: int) -> str:
    aliases = {
        "SrcRType": "SrcRT",
        "SrcType": "SrcT",
        "DstType": "DstT",
    }
    label = aliases.get(name, name)
    if width <= 1:
        return label
    if width <= 2 and len(label) > 5:
        return label[:5]
    if width <= 4 and len(label) > 8:
        return label[:8]
    return label


def scalar_encoding_segments(inst: dict) -> list[dict]:
    part = encoding_part(inst)
    pattern = part.get("pattern") or "." * int(part.get("width_bits", inst.get("length_bits", 32)))
    width_bits = int(part.get("width_bits", len(pattern)))
    bit_info: dict[int, dict] = {}

    # Pattern strings are stored MSB first.
    for index, char in enumerate(pattern):
        bit = width_bits - 1 - index
        if char in "01":
            bit_info[bit] = {"name": char, "kind": "const", "signed": None}
        else:
            bit_info[bit] = {"name": "", "kind": "reserved", "signed": None}

    for field in part.get("fields", []):
        for piece in field.get("pieces", []):
            for bit in range(int(piece["insn_lsb"]), int(piece["insn_msb"]) + 1):
                bit_info[bit] = {
                    "name": field.get("name", ""),
                    "kind": "field",
                    "signed": field.get("signed"),
                }

    segments: list[dict] = []
    current = None
    for bit in range(width_bits - 1, -1, -1):
        info = bit_info[bit]
        key = (info["name"], info["kind"], info.get("signed"))
        if current and current["key"] == key and current["lsb"] == bit + 1:
            current["lsb"] = bit
        else:
            if current:
                segments.append(current)
            current = {
                "key": key,
                "name": info["name"],
                "kind": info["kind"],
                "signed": info.get("signed"),
                "msb": bit,
                "lsb": bit,
            }
    if current:
        segments.append(current)

    return segments


def write_scalar_encoding_svg(inst: dict, svg_path: Path) -> None:
    """Render scalar encodings with the same manual figure style as B.* diagrams."""
    part = encoding_part(inst)
    width_bits = int(part.get("width_bits", inst.get("length_bits", 32)))
    segments = scalar_encoding_segments(inst)

    svg_width = 760
    svg_height = 96
    margin_x = 28
    lane_y = 30
    lane_h = 42
    bit_w = (svg_width - 2 * margin_x) / width_bits
    stroke = "#111827"

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}">',
        '  <rect width="100%" height="100%" fill="white"/>',
        '  <g font-family="sans-serif" text-anchor="middle">',
    ]

    # Light per-bit ticks keep the picture readable without turning it into a table.
    for bit in range(width_bits + 1):
        x = margin_x + bit * bit_w
        tick_h = lane_h if bit in {0, width_bits} else 5
        y1 = lane_y + lane_h - tick_h
        lines.append(
            f'    <line x1="{x:.2f}" y1="{y1:.2f}" x2="{x:.2f}" y2="{lane_y + lane_h:.2f}" '
            f'stroke="{stroke}" stroke-width="0.75"/>'
        )

    boundary_labels: list[tuple[float, int]] = [(margin_x, width_bits - 1)]
    for seg in segments:
        x_right = margin_x + (width_bits - seg["lsb"]) * bit_w
        boundary_labels.append((x_right, seg["lsb"]))

    last_x = None
    for x, bit in boundary_labels:
        # Avoid unreadable overprint when one-bit fields are adjacent.
        if last_x is not None and abs(x - last_x) < 11:
            continue
        lines.append(f'    <text x="{x:.2f}" y="17" font-size="12" fill="{stroke}">{bit}</text>')
        last_x = x

    for seg in segments:
        x = margin_x + (width_bits - 1 - seg["msb"]) * bit_w
        w = (seg["msb"] - seg["lsb"] + 1) * bit_w
        is_const = seg["kind"] == "const"
        fill = field_color(seg["name"], is_const)
        lines.append(
            f'    <rect x="{x:.2f}" y="{lane_y}" width="{w:.2f}" height="{lane_h}" '
            f'fill="{fill}" fill-opacity="0.42" stroke="{stroke}" stroke-width="1"/>'
        )
        label = seg["name"]
        if label:
            label = display_field_name(label, seg["msb"] - seg["lsb"] + 1)
            lines.append(
                f'    <text x="{x + w / 2:.2f}" y="{lane_y + 26}" font-size="13" '
                f'font-weight="700" fill="{stroke}">{svg_text(label)}</text>'
            )
        if seg["kind"] == "field":
            bit_range = f'{seg["msb"]}:{seg["lsb"]}' if seg["msb"] != seg["lsb"] else str(seg["lsb"])
            lines.append(
                f'    <text x="{x + w / 2:.2f}" y="{lane_y + lane_h + 16}" font-size="12" '
                f'fill="#4b5563">{svg_text(bit_range)}</text>'
            )

    lines.extend(["  </g>", "</svg>", ""])
    svg_path.write_text("\n".join(lines))


def convert_svg_to_pdf(svg: Path, pdf: Path) -> None:
    subprocess.run(["rsvg-convert", "-f", "pdf1.5", "-o", str(pdf), str(svg)], check=True)


def scrub_manual_metadata(data: dict) -> None:
    """Remove provenance and edit-history fields from the manual-facing JSON."""
    top_level_remove = {
        "edits",
        "filter",
        "migrated_from_tmp",
        "removed_counts",
        "source",
        "source_instruction_count",
    }
    for key in top_level_remove:
        data.pop(key, None)

    recursive_remove = {
        "manual_description_original",
        "manual_description_source",
        "source",
    }

    def scrub(value):
        if isinstance(value, dict):
            for key in list(value.keys()):
                if key in recursive_remove or "source" in key:
                    value.pop(key, None)
                else:
                    scrub(value[key])
        elif isinstance(value, list):
            for item in value:
                scrub(item)

    scrub(data)


def write_instruction_page(inst: dict, index: int, category: str, pdf_rel: str) -> str:
    name = inst["mnemonic"]
    label = f"sec:scalar-{slug(name)}"
    filename = f"{index:03d}_{slug(name)}.tex"
    part = encoding_part(inst)
    fields = field_rows(inst)
    op = derived_operation(inst)
    purpose = derived_purpose(inst)
    encoding_kind = inst.get("encoding_kind", "L32")
    bits = str(inst.get("length_bits", 32))

    lines = [
        "% Scalar instruction entry source: tools/generate_scalar_instruction_catalog.py.",
        rf"\subsection{{{tt(name)}}}",
        rf"\label{{{label}}}",
        rf"\index{{{tex_escape(name)}@{tt(name)}}}",
        rf"\begin{{sloppypar}}\noindent\textbf{{Purpose.}} {tex_escape(purpose or '-')}\end{{sloppypar}}\smallskip",
        r"\noindent\textbf{Syntax.}\par",
        r"\begin{lstlisting}[style=ptoscalarcode]",
        inst.get("asm", "-"),
        r"\end{lstlisting}",
        r"\noindent\textbf{Encoding.}\par",
        rf"\noindent\includegraphics[width=\textwidth]{{{pdf_rel}}}\par\smallskip",
        r"\begin{description}[leftmargin=0.18\linewidth,style=nextline]",
        rf"\item[Format] {tt(encoding_kind)} {tt(bits + '-bit')} scalar word.",
        rf"\item[Pattern] {tt(part.get('pattern', '-'))}.",
        r"\end{description}",
        r"\noindent\textbf{Classification.}\par",
        r"\begin{description}[leftmargin=0.18\linewidth,style=nextline]",
        rf"\item[Category] {tex_escape(CATEGORY_TITLES.get(category, category))}.",
        rf"\item[Family] {tex_escape(inst.get('group', '-'))}.",
        rf"\item[Execution class] {tt(inst.get('uop_group') or inst.get('uop_big_kind') or category)}; {tex_escape(class_summary(inst))}.",
        r"\end{description}",
    ]

    if fields:
        lines.extend(
            [
                r"\noindent\textbf{Operand fields.}\par",
                r"\begin{description}[leftmargin=0.18\linewidth,style=nextline]",
            ]
        )
        for field_name, bits, meaning in fields:
            lines.append(field_sentence(field_name, bits, meaning))
        lines.append(r"\end{description}")
    else:
        lines.extend(
            [
                r"\noindent\textbf{Operand fields.}\par",
                r"\noindent No variable operand fields are present in this fixed encoding.\par\smallskip",
            ]
        )

    lines.extend(
        [
            r"\noindent\textbf{Operation.}\par",
            r"\begin{lstlisting}[style=ptoscalarcode]",
            op,
            r"\end{lstlisting}",
            r"\Needspace{0.08\textheight}",
            "",
        ]
    )

    (ENTRY_DIR / filename).write_text("\n".join(lines))
    return f"entries/{filename}"


def write_catalog(instructions: list[dict], entry_map: dict[str, str]) -> None:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for inst in instructions:
        by_category[inst.get("uop_big_kind") or "OTHER"].append(inst)

    lines = [
        "% Scalar catalog source: tools/generate_scalar_instruction_catalog.py.",
        r"\section{Scalar Instruction Categories}",
        (
            "The scalar profile imported here is the 32-bit scalar "
            "instruction subset used by this revision. The TeX manual carries the scalar profile JSON as "
            r"\path{data/scalar/scalar-instructions-32bit.json}; "
            "encoding JSON and SVG/PDF renderings are stored under "
            r"\path{figs/encoding/scalar_instruction_entries}. "
            r"Each instruction below gives its category, family, assembly form, "
            r"encoding pattern, operand fields, encoding diagram, and description."
        ),
        r"\begin{longtable}{@{}L{0.22\linewidth}R{0.08\linewidth}L{0.60\linewidth}@{}}",
        r"\toprule",
        r"Category & Count & Description \\",
        r"\midrule",
    ]

    for category in CATEGORY_ORDER:
        items = by_category.get(category, [])
        if not items:
            continue
        lines.append(
            rf"{tex_escape(CATEGORY_TITLES.get(category, category))} & {len(items)} & "
            rf"{tex_escape(CATEGORY_DESCRIPTIONS.get(category, ''))} \\"
        )
    lines.extend([r"\bottomrule", r"\end{longtable}", ""])

    for category in CATEGORY_ORDER:
        items = by_category.get(category, [])
        if not items:
            continue
        group_counts = Counter(inst.get("group", "-") for inst in items)
        lines.extend(
            [
                rf"\section{{{tex_escape(CATEGORY_TITLES.get(category, category))}}}",
                rf"{tex_escape(CATEGORY_DESCRIPTIONS.get(category, ''))}\par\smallskip",
                r"{\scriptsize",
                r"\begin{longtable}{@{}L{0.24\linewidth}R{0.09\linewidth}L{0.56\linewidth}@{}}",
                r"\toprule",
                r"Family & Count & Representative instructions \\",
                r"\midrule",
            ]
        )
        for family, count in sorted(group_counts.items()):
            reps = [inst["mnemonic"] for inst in items if inst.get("group", "-") == family][:8]
            rep_text = ", ".join(reps)
            lines.append(rf"{tex_escape(family)} & {count} & {tt(rep_text)} \\")
        lines.extend([r"\bottomrule", r"\end{longtable}", r"}", ""])
        for inst in items:
            lines.append(rf"\input{{scalar-instructions/{entry_map[inst['mnemonic']]}}}")
            lines.append(r"\medskip")
        lines.append("")

    CATALOG_TEX.write_text("\n".join(lines))


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    ENTRY_DIR.mkdir(parents=True, exist_ok=True)

    data = json.loads(INPUT_JSON.read_text())
    instructions = data["instructions"]

    entry_map: dict[str, str] = {}
    for index, inst in enumerate(instructions, start=1):
        json_dst, svg_dst, pdf_dst = asset_paths(inst)
        source_json = Path(inst["wavedrom_encoding"].get("source_json") or inst["wavedrom_encoding"].get("wavedrom_json"))
        shutil.copyfile(source_json, json_dst)
        write_scalar_encoding_svg(inst, svg_dst)
        convert_svg_to_pdf(svg_dst, pdf_dst)
        inst["wavedrom_encoding"] = {
            "generator": inst["wavedrom_encoding"].get("generator", "wavedrom"),
            "wavedrom_json": str(json_dst.relative_to(MANUAL_ROOT)),
            "svg": str(svg_dst.relative_to(MANUAL_ROOT)),
            "pdf": str(pdf_dst.relative_to(MANUAL_ROOT)),
            "bits": inst["wavedrom_encoding"].get("bits", 32),
        }
        inst["manual_description"] = derived_purpose(inst)
        inst["manual_operation_informative"] = derived_operation(inst)
        inst["manual_operand_fields"] = manual_operand_field_map(inst)
        pdf_rel = str(pdf_dst.relative_to(MANUAL_ROOT))
        entry_map[inst["mnemonic"]] = write_instruction_page(inst, index, inst.get("uop_big_kind") or "OTHER", pdf_rel)

    data["wavedrom_encoding_dir"] = str(FIG_DIR.relative_to(MANUAL_ROOT))
    data["scalar_instruction_tex_dir"] = str(SCALAR_DIR.relative_to(MANUAL_ROOT))
    scrub_manual_metadata(data)
    MIGRATED_JSON.write_text(json.dumps(data, indent=2) + "\n")
    write_catalog(instructions, entry_map)

    print(f"scalar instructions: {len(instructions)}")
    print(f"json: {MIGRATED_JSON}")
    print(f"figures: {FIG_DIR}")
    print(f"catalog: {CATALOG_TEX}")


if __name__ == "__main__":
    main()
