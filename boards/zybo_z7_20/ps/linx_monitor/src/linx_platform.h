#pragma once

#include <stdint.h>

// Default AXI base address (Vivado scripts assign 0x43C0_0000).
#ifndef LINX_PLAT_BASE
#define LINX_PLAT_BASE 0x43C00000u
#endif

// Register offsets (must match boards/zybo_z7_20/rtl/linx_platform_regs_axi.sv).
enum {
  LINX_REG_CTRL = 0x00,        // [0]=reset (1=assert)
  LINX_REG_STATUS = 0x04,      // [0]=halted
  LINX_REG_BOOT_PC_LO = 0x08,
  LINX_REG_BOOT_PC_HI = 0x0C,
  LINX_REG_BOOT_SP_LO = 0x10,
  LINX_REG_BOOT_SP_HI = 0x14,

  LINX_REG_HOST_ADDR_LO = 0x18,
  LINX_REG_HOST_ADDR_HI = 0x1C,
  LINX_REG_HOST_DATA_LO = 0x20,
  LINX_REG_HOST_DATA_HI = 0x24,
  LINX_REG_HOST_STRB = 0x28,
  LINX_REG_HOST_CMD = 0x2C,    // write 1 => host_wvalid pulse

  LINX_REG_UART_STATUS = 0x30, // [15:0]=count [16]=overflow
  LINX_REG_UART_DATA = 0x34,   // read => pop, [7:0]=byte

  LINX_REG_EXIT_CODE = 0x38,
  LINX_REG_CYCLES_LO = 0x3C,
  LINX_REG_CYCLES_HI = 0x40,
};

static inline uintptr_t linx_reg(uint32_t off) { return (uintptr_t)(LINX_PLAT_BASE + off); }

