#include <stdbool.h>
#include <stdint.h>

#include "linx_platform.h"

#include "xil_io.h"
#include "xil_printf.h"
#include "xil_types.h"

// Standalone BSP provides inbyte() for PS UART input in most templates.
extern char inbyte(void);

static inline void reg_write(uint32_t off, uint32_t v) { Xil_Out32(linx_reg(off), v); }
static inline uint32_t reg_read(uint32_t off) { return Xil_In32(linx_reg(off)); }

static inline void linx_set_reset(bool rst) { reg_write(LINX_REG_CTRL, rst ? 1u : 0u); }

static inline void linx_set_boot(uint64_t pc, uint64_t sp) {
  reg_write(LINX_REG_BOOT_PC_LO, (uint32_t)(pc & 0xffffffffu));
  reg_write(LINX_REG_BOOT_PC_HI, (uint32_t)(pc >> 32));
  reg_write(LINX_REG_BOOT_SP_LO, (uint32_t)(sp & 0xffffffffu));
  reg_write(LINX_REG_BOOT_SP_HI, (uint32_t)(sp >> 32));
}

static inline void linx_host_write(uint64_t addr, uint64_t data, uint8_t strb) {
  reg_write(LINX_REG_HOST_ADDR_LO, (uint32_t)(addr & 0xffffffffu));
  reg_write(LINX_REG_HOST_ADDR_HI, (uint32_t)(addr >> 32));
  reg_write(LINX_REG_HOST_DATA_LO, (uint32_t)(data & 0xffffffffu));
  reg_write(LINX_REG_HOST_DATA_HI, (uint32_t)(data >> 32));
  reg_write(LINX_REG_HOST_STRB, (uint32_t)strb);
  reg_write(LINX_REG_HOST_CMD, 1u);
}

static inline uint64_t linx_cycles(void) {
  const uint32_t lo = reg_read(LINX_REG_CYCLES_LO);
  const uint32_t hi = reg_read(LINX_REG_CYCLES_HI);
  return ((uint64_t)hi << 32) | lo;
}

static inline uint32_t linx_exit_code(void) { return reg_read(LINX_REG_EXIT_CODE); }

static inline bool linx_halted(void) { return (reg_read(LINX_REG_STATUS) & 1u) != 0u; }

static inline uint32_t linx_uart_status(void) { return reg_read(LINX_REG_UART_STATUS); }

static void linx_drain_uart(void) {
  for (;;) {
    const uint32_t st = reg_read(LINX_REG_UART_STATUS);
    const uint32_t count = st & 0xffffu;
    if (count == 0)
      break;
    const uint32_t d = reg_read(LINX_REG_UART_DATA);
    xil_printf("%c", (char)(d & 0xffu));
  }
}

static int hex_nibble(int c) {
  if (c >= '0' && c <= '9')
    return c - '0';
  if (c >= 'a' && c <= 'f')
    return 10 + (c - 'a');
  if (c >= 'A' && c <= 'F')
    return 10 + (c - 'A');
  return -1;
}

static bool parse_hex_u64(const char *s, uint64_t *out) {
  uint64_t v = 0;
  bool any = false;
  for (const char *p = s; *p; ++p) {
    const int n = hex_nibble(*p);
    if (n < 0)
      break;
    any = true;
    v = (v << 4) | (uint64_t)n;
  }
  if (!any)
    return false;
  *out = v;
  return true;
}

static void memh_flush(uint64_t *base, uint64_t *data, uint8_t *strb, uint32_t *writes) {
  if (*strb == 0)
    return;
  linx_host_write(*base, *data, *strb);
  *data = 0;
  *strb = 0;
  (*writes)++;
}

static void memh_feed_byte(uint64_t addr, uint8_t byte, uint64_t *base, uint64_t *data, uint8_t *strb, uint32_t *writes) {
  const uint64_t next_base = addr & ~7ull;
  const uint32_t idx = (uint32_t)(addr & 7ull);
  if (*strb == 0) {
    *base = next_base;
  } else if (*base != next_base) {
    memh_flush(base, data, strb, writes);
    *base = next_base;
  }
  *data |= ((uint64_t)byte) << (idx * 8u);
  *strb |= (uint8_t)(1u << idx);
  if (*strb == 0xffu) {
    memh_flush(base, data, strb, writes);
  }
}

static void load_memh_stream(void) {
  // Parses the same format produced by janus/tools/ihex_to_memh.py:
  //   @<addr>
  //   <byte>
  //   <byte>
  //   ...
  //
  // For robustness, also accepts multiple byte tokens per line separated by whitespace.
  uint64_t addr = 0;
  uint64_t base = 0;
  uint64_t data = 0;
  uint8_t strb = 0;
  uint32_t bytes = 0;
  uint32_t writes = 0;

  char line[256];
  unsigned idx = 0;

  xil_printf("OK LOAD_MEMH\r\n");
  for (;;) {
    const char ch = inbyte();
    if (ch == '\r')
      continue;
    if (ch != '\n') {
      if (idx + 1 < sizeof(line)) {
        line[idx++] = ch;
      }
      continue;
    }
    line[idx] = 0;
    idx = 0;

    // Trim leading whitespace.
    const char *p = line;
    while (*p == ' ' || *p == '\t')
      ++p;
    if (*p == 0)
      continue;

    // END terminates.
    if (p[0] == 'E' && p[1] == 'N' && p[2] == 'D' && (p[3] == 0 || p[3] == ' ' || p[3] == '\t')) {
      break;
    }

    if (*p == '@') {
      memh_flush(&base, &data, &strb, &writes);
      ++p;
      uint64_t a = 0;
      if (parse_hex_u64(p, &a))
        addr = a;
      continue;
    }

    // Parse 1+ byte tokens.
    while (*p) {
      while (*p == ' ' || *p == '\t')
        ++p;
      if (!*p)
        break;
      // Stop on comment leader.
      if (p[0] == '#' || (p[0] == '/' && p[1] == '/'))
        break;

      int n0 = hex_nibble(p[0]);
      int n1 = hex_nibble(p[1]);
      if (n0 < 0 || n1 < 0)
        break;
      const uint8_t byte = (uint8_t)((n0 << 4) | n1);
      memh_feed_byte(addr, byte, &base, &data, &strb, &writes);
      addr++;
      bytes++;
      p += 2;
    }
  }

  memh_flush(&base, &data, &strb, &writes);
  xil_printf("OK LOADED bytes=%lu writes=%lu\r\n", (unsigned long)bytes, (unsigned long)writes);
}

static void read_line(char *out, unsigned cap) {
  unsigned i = 0;
  for (;;) {
    char ch = inbyte();
    if (ch == '\r')
      continue;
    if (ch == '\n') {
      break;
    }
    if (i + 1 < cap)
      out[i++] = ch;
  }
  out[i] = 0;
}

static const char *skip_ws(const char *p) {
  while (*p == ' ' || *p == '\t')
    ++p;
  return p;
}

static bool parse_u64_token(const char **p_inout, uint64_t *out) {
  const char *p = skip_ws(*p_inout);
  uint64_t v = 0;
  if (!parse_hex_u64(p, &v))
    return false;
  while (*p && *p != ' ' && *p != '\t')
    ++p;
  *p_inout = p;
  *out = v;
  return true;
}

int main(void) {
  xil_printf("linx-monitor: base=0x%08x\r\n", (unsigned)LINX_PLAT_BASE);
  xil_printf("linx-monitor: ready\r\n");

  // Default boot contract (matches sim conventions).
  linx_set_reset(true);
  reg_write(LINX_REG_UART_STATUS, 1u); // clear overflow
  linx_set_boot(0x0000000000010000ull, 0x000000000003ff00ull);

  char line[256];
  for (;;) {
    // Print a newline-terminated prompt for PC automation (readline-based).
    xil_printf("> \r\n");
    read_line(line, sizeof(line));
    const char *p = skip_ws(line);

    if (p[0] == 0) {
      continue;
    } else if (p[0] == 'P' && p[1] == 'I' && p[2] == 'N' && p[3] == 'G' && p[4] == 0) {
      xil_printf("OK PONG\r\n");
    } else if (p[0] == 'R' && p[1] == 'E' && p[2] == 'S' && p[3] == 'E' && p[4] == 'T') {
      p += 5;
      p = skip_ws(p);
      bool rst = (*p == '1');
      linx_set_reset(rst);
      xil_printf("OK RESET %d\r\n", rst ? 1 : 0);
    } else if (p[0] == 'B' && p[1] == 'O' && p[2] == 'O' && p[3] == 'T') {
      p += 4;
      uint64_t pc = 0, sp = 0;
      if (!parse_u64_token(&p, &pc) || !parse_u64_token(&p, &sp)) {
        xil_printf("ERR BOOT expects: BOOT <pc_hex> <sp_hex>\r\n");
        continue;
      }
      linx_set_boot(pc, sp);
      xil_printf("OK BOOT pc=0x%08x%08x sp=0x%08x%08x\r\n",
                 (unsigned)(pc >> 32), (unsigned)pc,
                 (unsigned)(sp >> 32), (unsigned)sp);
    } else if (p[0] == 'L' && p[1] == 'O' && p[2] == 'A' && p[3] == 'D' && p[4] == '_' && p[5] == 'M' && p[6] == 'E' && p[7] == 'M' && p[8] == 'H') {
      // Always load while core is in reset.
      linx_set_reset(true);
      load_memh_stream();
    } else if (p[0] == 'S' && p[1] == 'T' && p[2] == 'A' && p[3] == 'T' && p[4] == 'U' && p[5] == 'S') {
      const uint32_t st = linx_uart_status();
      xil_printf("STATUS halted=%d exit=0x%08x cycles=%llu uart_count=%lu overflow=%lu\r\n",
                 linx_halted() ? 1 : 0,
                 (unsigned)linx_exit_code(),
                 (unsigned long long)linx_cycles(),
                 (unsigned long)(st & 0xffffu),
                 (unsigned long)((st >> 16) & 1u));
    } else if (p[0] == 'R' && p[1] == 'U' && p[2] == 'N') {
      xil_printf("OK RUN\r\n");
      linx_set_reset(false);
      while (!linx_halted()) {
        linx_drain_uart();
      }
      linx_drain_uart();
      const uint32_t exit_code = linx_exit_code();
      const uint64_t cycles = linx_cycles();
      xil_printf("HALT exit=0x%08x cycles=%llu\r\n", exit_code, (unsigned long long)cycles);
      linx_set_reset(true);
    } else {
      xil_printf("ERR unknown\r\n");
    }
  }
}
