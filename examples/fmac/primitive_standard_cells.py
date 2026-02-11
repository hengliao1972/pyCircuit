# -*- coding: utf-8 -*-
"""Primitive standard cells for building arithmetic from first principles.

All functions accept and return CycleAwareSignal.  Inputs are at most
4 bits wide.  Higher-level structures (RCA, multiplier, etc.) are
composed by calling these primitives hierarchically.

Logic depth tracking: each function returns (result, depth) where depth
is the combinational gate-level depth (AND/OR/XOR = 1 level each).
"""
from __future__ import annotations
from pycircuit import CycleAwareSignal, CycleAwareDomain, mux


# ═══════════════════════════════════════════════════════════════════
# Level 0 — single-gate primitives (depth = 1)
# ═══════════════════════════════════════════════════════════════════

def inv(a: CycleAwareSignal) -> tuple[CycleAwareSignal, int]:
    """Inverter. depth=1."""
    return ~a, 1


def and2(a, b) -> tuple[CycleAwareSignal, int]:
    """2-input AND. depth=1."""
    return a & b, 1


def or2(a, b) -> tuple[CycleAwareSignal, int]:
    """2-input OR. depth=1."""
    return a | b, 1


def xor2(a, b) -> tuple[CycleAwareSignal, int]:
    """2-input XOR. depth=1."""
    return a ^ b, 1


def mux2(sel, a_true, a_false) -> tuple[CycleAwareSignal, int]:
    """2:1 MUX (sel=1 → a_true). depth=2 (AND-OR)."""
    return mux(sel, a_true, a_false), 2


# ═══════════════════════════════════════════════════════════════════
# Level 1 — half adder, full adder (depth = 2–3)
# ═══════════════════════════════════════════════════════════════════

def half_adder(a, b) -> tuple[CycleAwareSignal, CycleAwareSignal, int]:
    """Half adder.  Returns (sum, carry_out, depth).
    sum = a ^ b       (depth 1)
    cout = a & b      (depth 1)
    Total depth = 1.
    """
    s = a ^ b
    c = a & b
    return s, c, 1


def full_adder(a, b, cin) -> tuple[CycleAwareSignal, CycleAwareSignal, int]:
    """Full adder.  Returns (sum, carry_out, depth).
    sum  = a ^ b ^ cin    (depth 2: xor chain)
    cout = (a & b) | (cin & (a ^ b))  (depth 2: xor+and | and, then or)
    Total depth = 2.
    """
    ab = a ^ b           # depth 1
    s = ab ^ cin          # depth 2
    c = (a & b) | (cin & ab)  # depth 2 (and + or in parallel with xor)
    return s, c, 2


# ═══════════════════════════════════════════════════════════════════
# Level 2 — multi-bit adders (ripple-carry, depth = 2*N)
# ═══════════════════════════════════════════════════════════════════

def ripple_carry_adder(domain, a_bits, b_bits, cin, name="rca"):
    """N-bit ripple carry adder from full adders.

    Args:
        a_bits, b_bits: lists of 1-bit signals, LSB first [bit0, bit1, ...]
        cin: 1-bit carry-in

    Returns:
        (sum_bits, cout, depth)
        sum_bits: list of 1-bit signals LSB first
        cout: carry out
        depth: combinational depth
    """
    n = len(a_bits)
    assert len(b_bits) == n, f"bit width mismatch: {n} vs {len(b_bits)}"
    sums = []
    carry = cin
    depth = 0
    for i in range(n):
        s, carry, d = full_adder(a_bits[i], b_bits[i], carry)
        depth = max(depth, 2 * (i + 1))  # ripple carry depth
        sums.append(s)
    return sums, carry, depth


def carry_select_adder(domain, a_bits, b_bits, cin, name="csa"):
    """N-bit carry-select adder — splits into halves for faster carry propagation.

    Low half:  normal RCA (produces carry_out_low)
    High half: two RCAs in parallel (cin=0 and cin=1), mux on carry_out_low.
    depth = max(2*half, 2*half + 2) = N + 2   (vs 2*N for plain RCA).
    """
    n = len(a_bits)
    assert len(b_bits) == n
    if n <= 4:
        return ripple_carry_adder(domain, a_bits, b_bits, cin, name)

    half = n // 2
    lo_a, hi_a = a_bits[:half], a_bits[half:]
    lo_b, hi_b = b_bits[:half], b_bits[half:]

    # Low half — standard RCA
    lo_sum, lo_cout, lo_depth = ripple_carry_adder(
        domain, lo_a, lo_b, cin, f"{name}_lo")

    # High half — two RCAs in parallel (cin=0 and cin=1)
    from pycircuit import mux as mux_fn
    c = lambda v, w: domain.const(v, width=w)
    hi_sum0, hi_cout0, _ = ripple_carry_adder(
        domain, hi_a, hi_b, c(0, 1), f"{name}_hi0")
    hi_sum1, hi_cout1, _ = ripple_carry_adder(
        domain, hi_a, hi_b, c(1, 1), f"{name}_hi1")

    # MUX select based on low carry-out
    hi_sum = [mux_fn(lo_cout, hi_sum1[i], hi_sum0[i]) for i in range(len(hi_a))]
    cout = mux_fn(lo_cout, hi_cout1, hi_cout0)

    depth = lo_depth + 2  # RCA(half) + MUX
    return lo_sum + hi_sum, cout, depth


def ripple_carry_adder_packed(domain, a, b, cin, width, name="rca"):
    """Packed version: takes N-bit signals, returns N-bit sum + cout.

    Splits into individual bits, runs RCA, recombines.
    """
    c = lambda v, w: domain.const(v, width=w)

    a_bits = [a[i] for i in range(width)]
    b_bits = [b[i] for i in range(width)]
    cin_1 = cin if cin.width == 1 else cin[0]

    sum_bits, cout, depth = ripple_carry_adder(domain, a_bits, b_bits, cin_1, name)

    # Recombine bits into a single signal
    result = sum_bits[0].zext(width=width)
    for i in range(1, width):
        bit_shifted = sum_bits[i].zext(width=width) << i
        result = result | bit_shifted

    return result, cout, depth


# ═══════════════════════════════════════════════════════════════════
# Level 3 — partial-product generation for multiplier
# ═══════════════════════════════════════════════════════════════════

def and_gate_array(a_bit, b_bits):
    """AND a single bit with each bit of b.  Returns list of 1-bit signals.
    depth = 1 (single AND gate per bit).
    """
    return [a_bit & bb for bb in b_bits], 1


def partial_product_array(a_bits, b_bits):
    """Generate partial products for a*b (unsigned).

    Args:
        a_bits: list of 1-bit signals (multiplicand), LSB first
        b_bits: list of 1-bit signals (multiplier), LSB first

    Returns:
        pp_rows: list of (shifted_bits, shift_amount) — partial product rows
        depth: 1 (just AND gates)
    """
    pp_rows = []
    for i, ab in enumerate(a_bits):
        row, _ = and_gate_array(ab, b_bits)
        pp_rows.append((row, i))  # shifted left by i
    return pp_rows, 1


# ═══════════════════════════════════════════════════════════════════
# Level 4 — partial-product reduction (Wallace/Dadda tree)
# Using carry-save adder (CSA) = row of full adders
# ═══════════════════════════════════════════════════════════════════

def compress_3to2(a_bits, b_bits, c_bits):
    """3:2 compressor (carry-save adder): reduces 3 rows to 2.

    Each column: FA(a, b, c) → (sum, carry).
    Returns (sum_bits, carry_bits, depth_increment=2).
    """
    n = max(len(a_bits), len(b_bits), len(c_bits))
    sums = []
    carries = []
    for i in range(n):
        a = a_bits[i] if i < len(a_bits) else None
        b = b_bits[i] if i < len(b_bits) else None
        c = c_bits[i] if i < len(c_bits) else None

        if a is None and b is None and c is None:
            continue
        if a is not None and b is not None and c is not None:
            s, co, _ = full_adder(a, b, c)
            sums.append(s)
            carries.append(co)
        elif a is not None and b is not None:
            s, co, _ = half_adder(a, b)
            sums.append(s)
            carries.append(co)
        elif a is not None:
            sums.append(a)
        elif b is not None:
            sums.append(b)
        else:
            sums.append(c)

    return sums, carries, 2


def reduce_partial_products(domain, pp_rows, result_width, name="mul"):
    """Reduce partial product rows to 2 rows using 3:2 compressors,
    then final ripple-carry addition.

    Args:
        pp_rows: list of (bits, shift) from partial_product_array
        result_width: total width of product

    Returns:
        (product_bits, total_depth)
    """
    c = lambda v, w: domain.const(v, width=w)

    # Expand partial products into column-aligned bit arrays
    rows = []
    for bits, shift in pp_rows:
        padded = [None] * shift + list(bits) + [None] * (result_width - shift - len(bits))
        padded = padded[:result_width]
        rows.append(padded)

    # Fill None with zero constants
    zero = c(0, 1)
    for r in range(len(rows)):
        for col in range(result_width):
            if rows[r][col] is None:
                rows[r][col] = zero

    depth = 1  # initial AND depth from partial products

    # Reduce rows using 3:2 compressors until 2 rows remain
    while len(rows) > 2:
        new_rows = []
        i = 0
        round_depth = 0
        while i + 2 < len(rows):
            a_row = rows[i]
            b_row = rows[i + 1]
            c_row = rows[i + 2]
            s_row, c_row_out, d = compress_3to2(a_row, b_row, c_row)
            # Carry row is shifted left by 1
            c_shifted = [zero] + c_row_out
            # Pad to result_width
            while len(s_row) < result_width:
                s_row.append(zero)
            while len(c_shifted) < result_width:
                c_shifted.append(zero)
            new_rows.append(s_row[:result_width])
            new_rows.append(c_shifted[:result_width])
            round_depth = max(round_depth, d)  # parallel CSAs — same depth
            i += 3
        # Remaining rows (0, 1, or 2) pass through
        while i < len(rows):
            new_rows.append(rows[i])
            i += 1
        depth += round_depth
        rows = new_rows

    # Final addition of 2 rows using carry-select adder (faster than RCA)
    if len(rows) == 2:
        sum_bits, _, final_depth = carry_select_adder(
            domain, rows[0], rows[1], zero, name=f"{name}_final"
        )
        depth += final_depth
    elif len(rows) == 1:
        sum_bits = rows[0]
    else:
        sum_bits = [zero] * result_width

    return sum_bits, depth


# ═══════════════════════════════════════════════════════════════════
# Level 5 — N×M unsigned multiplier
# ═══════════════════════════════════════════════════════════════════

def unsigned_multiplier(domain, a, b, a_width, b_width, name="umul"):
    """Unsigned multiplier built from partial products + reduction tree.

    Args:
        a, b: CycleAwareSignal inputs
        a_width, b_width: bit widths

    Returns:
        (product, depth)
        product: (a_width + b_width)-bit CycleAwareSignal
    """
    result_width = a_width + b_width
    c = lambda v, w: domain.const(v, width=w)

    a_bits = [a[i] for i in range(a_width)]
    b_bits = [b[i] for i in range(b_width)]

    pp_rows, pp_depth = partial_product_array(a_bits, b_bits)
    product_bits, tree_depth = reduce_partial_products(
        domain, pp_rows, result_width, name=name
    )

    # Recombine bits
    result = _recombine_bits(product_bits, result_width)
    return result, pp_depth + tree_depth


def _recombine_bits(bits, width):
    """Pack a list of 1-bit signals into a single N-bit signal."""
    result = bits[0].zext(width=width)
    for i in range(1, min(len(bits), width)):
        bit_shifted = bits[i].zext(width=width) << i
        result = result | bit_shifted
    return result


# ── Split multiplier (for cross-pipeline-stage multiply) ─────

def multiplier_pp_and_partial_reduce(domain, a, b, a_width, b_width,
                                     csa_rounds=2, name="umul"):
    """Stage A of a split multiplier: generate partial products and
    run *csa_rounds* levels of 3:2 compression.

    Returns:
        packed_rows: list of CycleAwareSignal (each result_width bits)
                     — intermediate carry-save rows, packed for pipeline regs
        depth:       combinational depth of this stage
    """
    result_width = a_width + b_width
    c = lambda v, w: domain.const(v, width=w)
    zero = c(0, 1)

    a_bits = [a[i] for i in range(a_width)]
    b_bits = [b[i] for i in range(b_width)]

    pp_rows, _ = partial_product_array(a_bits, b_bits)
    depth = 1  # AND gates

    # Expand to column-aligned bit arrays
    rows = []
    for bits, shift in pp_rows:
        padded = [None] * shift + list(bits) + [None] * (result_width - shift - len(bits))
        padded = padded[:result_width]
        rows.append(padded)
    for r in range(len(rows)):
        for col in range(result_width):
            if rows[r][col] is None:
                rows[r][col] = zero

    # Run csa_rounds of 3:2 compression
    for _round in range(csa_rounds):
        if len(rows) <= 2:
            break
        new_rows = []
        i = 0
        round_depth = 0
        while i + 2 < len(rows):
            s_row, c_row_out, d = compress_3to2(rows[i], rows[i+1], rows[i+2])
            c_shifted = [zero] + c_row_out
            while len(s_row) < result_width: s_row.append(zero)
            while len(c_shifted) < result_width: c_shifted.append(zero)
            new_rows.append(s_row[:result_width])
            new_rows.append(c_shifted[:result_width])
            round_depth = max(round_depth, d)
            i += 3
        while i < len(rows):
            new_rows.append(rows[i])
            i += 1
        depth += round_depth
        rows = new_rows

    # Pack each row into a single result_width-bit signal
    packed = []
    for row in rows:
        packed.append(_recombine_bits(row, result_width))

    return packed, depth


def multiplier_complete_reduce(domain, packed_rows, result_width, name="umul"):
    """Stage B of a split multiplier: finish compression and final addition.

    Args:
        packed_rows: list of CycleAwareSignal (each result_width bits)
                     from multiplier_pp_and_partial_reduce
        result_width: product bit width

    Returns:
        (product, depth)
    """
    c = lambda v, w: domain.const(v, width=w)
    zero = c(0, 1)

    # Unpack rows back to bit arrays
    rows = []
    for packed in packed_rows:
        rows.append([packed[i] for i in range(result_width)])

    depth = 0

    # Continue 3:2 compression until 2 rows
    while len(rows) > 2:
        new_rows = []
        i = 0
        round_depth = 0
        while i + 2 < len(rows):
            s_row, c_row_out, d = compress_3to2(rows[i], rows[i+1], rows[i+2])
            c_shifted = [zero] + c_row_out
            while len(s_row) < result_width: s_row.append(zero)
            while len(c_shifted) < result_width: c_shifted.append(zero)
            new_rows.append(s_row[:result_width])
            new_rows.append(c_shifted[:result_width])
            round_depth = max(round_depth, d)
            i += 3
        while i < len(rows):
            new_rows.append(rows[i])
            i += 1
        depth += round_depth
        rows = new_rows

    # Final carry-select addition
    if len(rows) == 2:
        sum_bits, _, final_depth = carry_select_adder(
            domain, rows[0], rows[1], zero, name=f"{name}_final")
        depth += final_depth
        product = _recombine_bits(sum_bits, result_width)
    elif len(rows) == 1:
        product = _recombine_bits(rows[0], result_width)
    else:
        product = c(0, result_width)

    return product, depth


# ═══════════════════════════════════════════════════════════════════
# Level 6 — shifters (barrel shifter from MUX layers)
# ═══════════════════════════════════════════════════════════════════

def barrel_shift_right(domain, data, shift_amt, data_width, shift_bits, name="bsr"):
    """Barrel right-shifter built from MUX layers.

    Each layer handles one bit of the shift amount.
    depth = 2 * shift_bits (each MUX = depth 2).
    """
    result = data
    depth = 0
    for i in range(shift_bits):
        shift_by = 1 << i
        shifted = result >> shift_by
        result = mux(shift_amt[i], shifted, result)
        depth += 2
    return result, depth


def barrel_shift_left(domain, data, shift_amt, data_width, shift_bits, name="bsl"):
    """Barrel left-shifter built from MUX layers.

    depth = 2 * shift_bits.
    """
    result = data
    depth = 0
    for i in range(shift_bits):
        shift_by = 1 << i
        shifted = result << shift_by
        result = mux(shift_amt[i], shifted, result)
        depth += 2
    return result, depth


# ═══════════════════════════════════════════════════════════════════
# Level 7 — leading-zero counter
# ═══════════════════════════════════════════════════════════════════

def leading_zero_count(domain, data, width, name="lzc"):
    """Count leading zeros using a priority encoder (MUX tree).

    depth ≈ 2 * log2(width).
    """
    c = lambda v, w: domain.const(v, width=w)
    lzc_width = (width - 1).bit_length() + 1

    count = domain.signal(f"{name}_cnt", width=lzc_width)
    count.set(c(width, lzc_width))  # default: all zeros → count = width
    # Scan LSB→MSB so highest set bit has last-write-wins priority
    for bit_pos in range(width):
        leading_zeros = width - 1 - bit_pos
        count.set(c(leading_zeros, lzc_width), when=data[bit_pos])

    depth = 2 * ((width - 1).bit_length())  # approx MUX tree depth
    return count, depth
