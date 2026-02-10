/*
 * LinxISA smoke test: SHI + LHI sign-extension.
 *
 * - Store halfword -128 (0xFF80) to mem[0x200].
 * - Load it back with LHI (signed) into a1.
 * - Store a1 (word) to mem[0x100] and halt.
 *
 * Expected: mem[0x100] == 0xFFFF_FF80.
 */

    .section .text.start, "ax", @progbits
    .global _start
    .p2align 1
    .type _start, @function

_start:
    C.BSTART    FALL

    /* a0 = -128 (0x...FF80) */
    addi    zero, -128, ->a0

    /* mem[0x200] = (uint16_t)a0 */
    shi     a0, [zero, 512]

    /* a1 = (int16_t)mem[0x200] */
    lhi     [zero, 512], ->a1

    /* mem[0x100] = (uint32_t)a1 */
    swi     a1, [zero, 256]

    ebreak  0
    C.BSTOP
    .size _start, . - _start

