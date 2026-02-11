# -*- coding: utf-8 -*-
"""BF16 Fused Multiply-Accumulate (FMAC) — 4-stage pipeline.

Computes:  acc += a * b
  where a, b are BF16 (1-8-7 format), acc is FP32 (1-8-23 format).

BF16 format:  sign(1) | exponent(8) | mantissa(7)   bias=127
FP32 format:  sign(1) | exponent(8) | mantissa(23)  bias=127

Pipeline stages (each separated by domain.next()):
  Stage 1 (cycle 0→1): Unpack BF16 operands, compute product sign/exponent
                        depth ≈ 8 (exponent add via RCA)
  Stage 2 (cycle 1→2): 8×8 mantissa multiply (partial product + reduction)
                        depth ≈ 12 (Wallace tree + final RCA)
  Stage 3 (cycle 2→3): Align product to accumulator (barrel shift), add mantissas
                        depth ≈ 14 (shift + 26-bit RCA)
  Stage 4 (cycle 3→4): Normalize result (LZC + shift + exponent adjust), pack FP32
                        depth ≈ 14 (LZC + barrel shift + RCA)

All arithmetic built from primitive standard cells (HA, FA, RCA, MUX).
"""
from __future__ import annotations

import sys
from pathlib import Path

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareSignal,
    compile_cycle_aware,
    mux,
)

try:
    from .primitive_standard_cells import (
        unsigned_multiplier, ripple_carry_adder_packed,
        barrel_shift_right, barrel_shift_left, leading_zero_count,
        multiplier_pp_and_partial_reduce, multiplier_complete_reduce,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from primitive_standard_cells import (
        unsigned_multiplier, ripple_carry_adder_packed,
        barrel_shift_right, barrel_shift_left, leading_zero_count,
        multiplier_pp_and_partial_reduce, multiplier_complete_reduce,
    )


# ── Format constants ─────────────────────────────────────────
BF16_W = 16;  BF16_EXP = 8;  BF16_MAN = 7;   BF16_BIAS = 127
FP32_W = 32;  FP32_EXP = 8;  FP32_MAN = 23;  FP32_BIAS = 127

# Internal mantissa with implicit 1: 8 bits for BF16 (1.7), 24 for FP32 (1.23)
BF16_MANT_FULL = BF16_MAN + 1   # 8
FP32_MANT_FULL = FP32_MAN + 1   # 24

# Product mantissa: 8 × 8 = 16 bits (1.7 × 1.7 = 2.14, normalized to 1.15 → 16 bits)
PROD_MANT_W = BF16_MANT_FULL * 2  # 16

# Accumulator mantissa with guard bits for alignment: 26 bits
ACC_MANT_W = FP32_MANT_FULL + 2  # 26 (24 + 2 guard bits)


def _bf16_fmac_impl(m, domain):
    c = lambda v, w: domain.const(v, width=w)
    pipeline_depths = {}  # stage_name → depth

    # ════════════════════════════════════════════════════════════
    # Inputs
    # ════════════════════════════════════════════════════════════
    a_in       = domain.input("a_in",       width=BF16_W)
    b_in       = domain.input("b_in",       width=BF16_W)
    acc_in     = domain.input("acc_in",     width=FP32_W)
    valid_in   = domain.input("valid_in",   width=1)

    # ════════════════════════════════════════════════════════════
    # Pipeline registers (declared at their Q-read cycle)
    # ════════════════════════════════════════════════════════════

    # Stage 1→2 registers (Q at cycle 1)
    # After partial product generation + 2 CSA rounds, the intermediate
    # carry-save rows (up to ~4-6 rows of PROD_MANT_W bits) are stored here.
    MAX_INTER_ROWS = 6  # max rows after 2 CSA rounds from 8 PP rows
    domain.push()
    domain.next()  # cycle 1
    s1_prod_sign  = domain.signal("s1_prod_sign",  width=1,  reset=0)
    s1_prod_exp   = domain.signal("s1_prod_exp",   width=10, reset=0)
    s1_acc_sign   = domain.signal("s1_acc_sign",   width=1,  reset=0)
    s1_acc_exp    = domain.signal("s1_acc_exp",    width=8,  reset=0)
    s1_acc_mant   = domain.signal("s1_acc_mant",   width=FP32_MANT_FULL, reset=0)
    s1_prod_zero  = domain.signal("s1_prod_zero",  width=1,  reset=0)
    s1_acc_zero   = domain.signal("s1_acc_zero",   width=1,  reset=0)
    s1_valid      = domain.signal("s1_valid",      width=1,  reset=0)
    s1_mul_rows   = [domain.signal(f"s1_mul_row{i}", width=PROD_MANT_W, reset=0)
                     for i in range(MAX_INTER_ROWS)]
    s1_mul_nrows  = domain.signal("s1_mul_nrows", width=4, reset=0)  # actual row count

    # Stage 2→3 registers (Q at cycle 2)
    domain.next()  # cycle 2
    s2_prod_mant  = domain.signal("s2_prod_mant",  width=PROD_MANT_W, reset=0)
    s2_prod_sign  = domain.signal("s2_prod_sign",  width=1,  reset=0)
    s2_prod_exp   = domain.signal("s2_prod_exp",   width=10, reset=0)
    s2_acc_sign   = domain.signal("s2_acc_sign",   width=1,  reset=0)
    s2_acc_exp    = domain.signal("s2_acc_exp",    width=8,  reset=0)
    s2_acc_mant   = domain.signal("s2_acc_mant",   width=FP32_MANT_FULL, reset=0)
    s2_prod_zero  = domain.signal("s2_prod_zero",  width=1,  reset=0)
    s2_acc_zero   = domain.signal("s2_acc_zero",   width=1,  reset=0)
    s2_valid      = domain.signal("s2_valid",      width=1,  reset=0)

    # Stage 3→4 registers (Q at cycle 3)
    domain.next()  # cycle 3
    s3_result_sign = domain.signal("s3_result_sign", width=1,  reset=0)
    s3_result_exp  = domain.signal("s3_result_exp",  width=10, reset=0)
    s3_result_mant = domain.signal("s3_result_mant", width=ACC_MANT_W, reset=0)
    s3_valid       = domain.signal("s3_valid",       width=1,  reset=0)

    domain.pop()  # back to cycle 0

    # ════════════════════════════════════════════════════════════
    # STAGE 1 (cycle 0): Unpack + exponent add
    # ════════════════════════════════════════════════════════════
    s1_depth = 0

    # Unpack BF16 a
    a_sign = a_in[15]
    a_exp  = a_in[7:15]   # 8 bits
    a_mant_raw = a_in[0:7]   # 7 bits
    a_is_zero  = a_exp.eq(c(0, 8))
    # Implicit 1: if exp != 0, mantissa = {1, raw_mant}
    a_mant = mux(a_is_zero, c(0, BF16_MANT_FULL),
                 c(1, 1).zext(width=BF16_MANT_FULL) << BF16_MAN | a_mant_raw.zext(width=BF16_MANT_FULL))
    s1_depth = max(s1_depth, 3)  # mux + or

    # Unpack BF16 b
    b_sign = b_in[15]
    b_exp  = b_in[7:15]
    b_mant_raw = b_in[0:7]
    b_is_zero  = b_exp.eq(c(0, 8))
    b_mant = mux(b_is_zero, c(0, BF16_MANT_FULL),
                 c(1, 1).zext(width=BF16_MANT_FULL) << BF16_MAN | b_mant_raw.zext(width=BF16_MANT_FULL))

    # Unpack FP32 accumulator
    acc_sign = acc_in[31]
    acc_exp  = acc_in[23:31]  # 8 bits
    acc_mant_raw = acc_in[0:23]  # 23 bits
    acc_is_zero  = acc_exp.eq(c(0, 8))
    acc_mant = mux(acc_is_zero, c(0, FP32_MANT_FULL),
                   c(1, 1).zext(width=FP32_MANT_FULL) << FP32_MAN | acc_mant_raw.zext(width=FP32_MANT_FULL))

    # Product sign = a_sign XOR b_sign
    prod_sign = a_sign ^ b_sign
    s1_depth = max(s1_depth, 1)

    # Product exponent = a_exp + b_exp - bias (10-bit to handle overflow)
    # Use built-in + for simplicity (maps to RCA in hardware)
    prod_exp_sum = a_exp.zext(width=10) + b_exp.zext(width=10)
    prod_exp = prod_exp_sum - c(BF16_BIAS, 10)
    s1_depth = max(s1_depth, 8)  # two 10-bit RCA adds ≈ 2×8=16, but in parallel ≈ 8

    # Product is zero if either input is zero
    prod_zero = a_is_zero | b_is_zero

    # ── Partial product generation + 2 CSA rounds (still in Stage 1) ──
    CSA_ROUNDS_IN_S1 = 2
    mul_inter_rows, pp_csa_depth = multiplier_pp_and_partial_reduce(
        domain, a_mant, b_mant,
        BF16_MANT_FULL, BF16_MANT_FULL,
        csa_rounds=CSA_ROUNDS_IN_S1, name="mantmul"
    )
    s1_depth = max(s1_depth, 8 + pp_csa_depth)  # unpack(~8) + PP+CSA in parallel
    n_inter_rows = len(mul_inter_rows)

    pipeline_depths["Stage 1: Unpack + PP + 2×CSA"] = s1_depth

    # ──── Pipeline register write (cycle 0 → 1) ────
    domain.next()  # → cycle 1

    s1_prod_sign.set(prod_sign)
    s1_prod_exp.set(prod_exp)
    s1_acc_sign.set(acc_sign)
    s1_acc_exp.set(acc_exp)
    s1_acc_mant.set(acc_mant)
    s1_prod_zero.set(prod_zero)
    s1_acc_zero.set(acc_is_zero)
    s1_valid.set(valid_in)
    # Store intermediate multiply rows
    for i in range(MAX_INTER_ROWS):
        if i < n_inter_rows:
            s1_mul_rows[i].set(mul_inter_rows[i])
        else:
            s1_mul_rows[i].set(c(0, PROD_MANT_W))
    s1_mul_nrows.set(c(n_inter_rows, 4))

    # ════════════════════════════════════════════════════════════
    # STAGE 2 (cycle 1): Complete multiply (remaining CSA + carry-select)
    # ════════════════════════════════════════════════════════════
    prod_mant, mul_depth = multiplier_complete_reduce(
        domain, s1_mul_rows[:n_inter_rows], PROD_MANT_W, name="mantmul"
    )
    pipeline_depths["Stage 2: Complete Multiply"] = mul_depth

    # ──── Pipeline register write (cycle 1 → 2) ────
    domain.next()  # → cycle 2

    s2_prod_mant.set(prod_mant)
    s2_prod_sign.set(s1_prod_sign)
    s2_prod_exp.set(s1_prod_exp)
    s2_acc_sign.set(s1_acc_sign)
    s2_acc_exp.set(s1_acc_exp)
    s2_acc_mant.set(s1_acc_mant)
    s2_prod_zero.set(s1_prod_zero)
    s2_acc_zero.set(s1_acc_zero)
    s2_valid.set(s1_valid)

    # ════════════════════════════════════════════════════════════
    # STAGE 3 (cycle 2): Align + Add
    # ════════════════════════════════════════════════════════════
    s3_depth = 0

    # Normalize product mantissa: 8×8 product is in 2.14 format (16 bits).
    # If bit[15] is set → 2.14, shift right 1 and exp+1.
    # Otherwise → 1.14, just extend.
    prod_msb = s2_prod_mant[PROD_MANT_W - 1]
    prod_mant_norm = mux(prod_msb,
                         s2_prod_mant >> 1,
                         s2_prod_mant)
    prod_exp_norm = mux(prod_msb,
                        s2_prod_exp + 1,
                        s2_prod_exp)
    s3_depth += 3  # mux + add

    # Extend product mantissa to ACC_MANT_W (26 bits)
    # Product is 1.14 (15 significant bits), pad LSBs for FP32's 1.23 alignment
    # Shift left by (23 - 14) = 9 to align to FP32 mantissa position
    prod_mant_ext = prod_mant_norm.zext(width=ACC_MANT_W) << 9

    # Extend accumulator mantissa to ACC_MANT_W
    acc_mant_ext = s2_acc_mant.zext(width=ACC_MANT_W)

    # Determine exponent difference and align
    prod_exp_8 = prod_exp_norm.trunc(width=8)
    exp_diff_raw = prod_exp_8.as_signed() - s2_acc_exp.as_signed()
    exp_diff_pos = exp_diff_raw.as_unsigned()  # for shifting

    prod_bigger = prod_exp_8.gt(s2_acc_exp)
    exp_diff_abs = mux(prod_bigger,
                       (prod_exp_8 - s2_acc_exp).trunc(width=8),
                       (s2_acc_exp - prod_exp_8).trunc(width=8))
    s3_depth += 2  # compare + subtract

    # Shift the smaller operand right to align
    shift_5 = exp_diff_abs.trunc(width=5)
    # Cap shift at ACC_MANT_W to avoid shifting everything out
    shift_capped = mux(exp_diff_abs.gt(c(ACC_MANT_W, 8)),
                       c(ACC_MANT_W, 5), shift_5)

    prod_aligned = mux(prod_bigger, prod_mant_ext,
                        barrel_shift_right(domain, prod_mant_ext, shift_capped, ACC_MANT_W, 5, "prod_bsr")[0])
    acc_aligned  = mux(prod_bigger,
                        barrel_shift_right(domain, acc_mant_ext, shift_capped, ACC_MANT_W, 5, "acc_bsr")[0],
                        acc_mant_ext)
    s3_depth += 12  # barrel shift (5 MUX levels × 2) + mux

    result_exp = mux(prod_bigger, prod_exp_8, s2_acc_exp)

    # Add or subtract mantissas based on signs
    same_sign = ~(s2_prod_sign ^ s2_acc_sign)
    # If same sign: result = prod + acc
    # If diff sign: result = |larger| - |smaller|  (sign of larger)
    sum_mant = (prod_aligned.zext(width=ACC_MANT_W+1) +
                acc_aligned.zext(width=ACC_MANT_W+1)).trunc(width=ACC_MANT_W)

    # For subtraction: compare aligned magnitudes (not just exponents)
    mag_prod_ge = prod_aligned.ge(acc_aligned)
    diff_mant = mux(mag_prod_ge,
                    (prod_aligned - acc_aligned),
                    (acc_aligned - prod_aligned))

    result_mant = mux(same_sign, sum_mant, diff_mant)
    result_sign = mux(same_sign, s2_prod_sign,
                      mux(mag_prod_ge, s2_prod_sign, s2_acc_sign))
    s3_depth += 4  # add/sub + mux

    # Handle zeros
    result_mant_final = mux(s2_prod_zero, acc_mant_ext, result_mant)
    result_exp_final  = mux(s2_prod_zero, s2_acc_exp, result_exp)
    result_sign_final = mux(s2_prod_zero, s2_acc_sign, result_sign)

    pipeline_depths["Stage 3: Align + Add"] = s3_depth

    # ──── Pipeline register write (cycle 2 → 3) ────
    domain.next()  # → cycle 3

    s3_result_sign.set(result_sign_final)
    s3_result_exp.set(result_exp_final.zext(width=10))
    s3_result_mant.set(result_mant_final)
    s3_valid.set(s2_valid)

    # ════════════════════════════════════════════════════════════
    # STAGE 4 (cycle 3): Normalize + Pack FP32
    # ════════════════════════════════════════════════════════════
    s4_depth = 0

    # Leading-zero count for normalization
    # ACC_MANT_W=26 bits.  The implicit 1 should land at bit 23 (FP32 position).
    # Normal result: LZC=2 (bits 25,24 are 0, bit 23 is the leading 1).
    # LZC<2: carry overflow from addition → need right shift.
    # LZC>2: cancellation → need left shift.
    # Effective shift = LZC - 2 (positive = left, negative = right).
    lzc, lzc_depth = leading_zero_count(domain, s3_result_mant, ACC_MANT_W, "norm_lzc")
    s4_depth += lzc_depth

    GUARD_BITS = 2  # bits 25:24 are guard bits
    lzc_5 = lzc.trunc(width=5)

    # Determine direction: left-shift if lzc > GUARD_BITS, right-shift if lzc < GUARD_BITS
    need_left  = lzc_5.gt(c(GUARD_BITS, 5))
    need_right = lzc_5.lt(c(GUARD_BITS, 5))

    left_amt  = (lzc_5 - c(GUARD_BITS, 5)).trunc(width=5)
    right_amt = (c(GUARD_BITS, 5) - lzc_5).trunc(width=5)

    left_shifted,  bsl_depth = barrel_shift_left(
        domain, s3_result_mant, left_amt, ACC_MANT_W, 5, "norm_bsl")
    right_shifted, _ = barrel_shift_right(
        domain, s3_result_mant, right_amt, ACC_MANT_W, 5, "norm_bsr")

    norm_mant = mux(need_left, left_shifted,
                    mux(need_right, right_shifted, s3_result_mant))
    s4_depth += bsl_depth + 4  # barrel shift + muxes

    # Adjust exponent: exp = exp + GUARD_BITS - lzc
    norm_exp = s3_result_exp + c(GUARD_BITS, 10) - lzc.zext(width=10)
    s4_depth += 4  # add/sub

    # Extract FP32 mantissa: implicit 1 now at bit 23.
    # Drop the implicit 1, take bits [22:0] as the 23-bit fraction.
    fp32_mant = norm_mant[0:23]  # 23 fractional bits

    # Pack FP32: sign(1) | exp(8) | mantissa(23)
    fp32_exp = norm_exp.trunc(width=8)

    # Handle zero result
    result_is_zero = s3_result_mant.eq(c(0, ACC_MANT_W))
    fp32_packed = mux(result_is_zero,
                      c(0, FP32_W),
                      (s3_result_sign.zext(width=FP32_W) << 31) |
                      (fp32_exp.zext(width=FP32_W) << 23) |
                      fp32_mant.zext(width=FP32_W))
    s4_depth += 3  # mux + or

    pipeline_depths["Stage 4: Normalize + Pack"] = s4_depth

    # ──── Pipeline register write (cycle 3 → 4) ────
    domain.next()  # → cycle 4

    # Output registers — only update when valid (hold otherwise)
    result_r = domain.signal("result", width=FP32_W, reset=0)
    valid_r  = domain.signal("result_valid", width=1, reset=0)
    result_r.set(result_r)                            # hold
    result_r.set(fp32_packed, when=s3_valid)           # update on valid
    valid_r.set(s3_valid)

    # ════════════════════════════════════════════════════════════
    # Outputs
    # ════════════════════════════════════════════════════════════
    m.output("result",       result_r)
    m.output("result_valid", valid_r)


    return pipeline_depths


# ── Entry points ─────────────────────────────────────────────

# Pipeline depths collected during compilation (module-level, no `global` needed in JIT)
_pipeline_depths: dict = {}


def bf16_fmac(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    depths = _bf16_fmac_impl(m, domain)
    _pipeline_depths.update(depths)


def build():
    _pipeline_depths.clear()
    circuit = compile_cycle_aware(bf16_fmac, name="bf16_fmac")

    print("\n" + "=" * 60)
    print("  BF16 FMAC — Pipeline Critical Path Analysis")
    print("=" * 60)
    total = 0
    for stage, depth in _pipeline_depths.items():
        print(f"  {stage:<35s}  depth = {depth:>3d}")
        total += depth
    print(f"  {'─' * 50}")
    print(f"  {'Total combinational depth':<35s}  depth = {total:>3d}")
    print(f"  {'Max stage depth (critical path)':<35s}  depth = {max(_pipeline_depths.values()):>3d}")
    print("=" * 60 + "\n")

    return circuit


if __name__ == "__main__":
    circuit = build()
    mlir = circuit.emit_mlir()
    print(f"MLIR: {len(mlir)} chars")
