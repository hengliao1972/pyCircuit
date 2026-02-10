/*
 * Janus BCC bring-up benchmark proxy: "Dhrystone-lite"
 *
 * This is not the official Dhrystone source. It is a deterministic mixed
 * integer + load/store stress kernel for DMIPS-style proxy reporting.
 */

    .section .text.start, "ax", @progbits
    .global _start
    .p2align 1
    .type _start, @function

_start:
    C.BSTART    FALL

    addi    zero, 13, ->a0
    addi    zero, 29, ->a1

    /*
     * 4096 operations total (8 ops * 512 reps), including load/store traffic.
     * Uses only the currently modeled LinxISA subset in Janus BCC bring-up.
     */
    .rept 512
        addw    a0, a1, ->a0
        swi     a0, [zero, 320]
        lwi     [zero, 320], ->a1
        xorw    a0, a1, ->a0
        andw    a0, a1, ->a1
        orw     a0, a1, ->a0
        addiw   a0, 1, ->a0
        subi    a1, 1, ->a1
    .endr

    swi     a0, [zero, 256]

    ebreak  0
    C.BSTOP
    .size _start, . - _start

