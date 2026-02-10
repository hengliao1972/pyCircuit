/*
 * Janus BCC bring-up benchmark proxy: "CoreMark-lite"
 *
 * This is not the official EEMBC CoreMark workload. It is a deterministic
 * arithmetic mix designed to exercise Janus BCC 4-wide OOO execution on the
 * currently implemented LinxISA subset.
 */

    .section .text.start, "ax", @progbits
    .global _start
    .p2align 1
    .type _start, @function

_start:
    C.BSTART    FALL

    /* Seed state. */
    addi    zero, 1, ->a0
    addi    zero, 7, ->a1

    /* 4096 arithmetic operations (4 ops * 1024 reps). */
    .rept 1024
        addw    a0, a1, ->a0
        xorw    a0, a1, ->a1
        andw    a0, a1, ->a0
        orw     a0, a1, ->a1
    .endr

    /* Write result for TB visibility. */
    swi     a0, [zero, 256]

    ebreak  0
    C.BSTOP
    .size _start, . - _start

