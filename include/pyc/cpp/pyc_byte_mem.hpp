#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// Byte-addressed memory with async read + sync write (prototype).
//
// - `DepthBytes` is in bytes.
// - `rdata` is assembled little-endian from successive bytes at `raddr`.
// - Write uses `wstrb` byte enables relative to `waddr`.
template <unsigned AddrWidth, unsigned DataWidth, std::size_t DepthBytes>
class pyc_byte_mem {
public:
  static_assert(DataWidth > 0 && DataWidth <= 64, "pyc_byte_mem supports DataWidth 1..64 in the prototype");
  static_assert((DataWidth % 8) == 0, "pyc_byte_mem requires DataWidth divisible by 8 in the prototype");
  static constexpr unsigned StrbWidth = DataWidth / 8;

  pyc_byte_mem(Wire<1> &clk,
               Wire<1> &rst,
               Wire<AddrWidth> &raddr,
               Wire<DataWidth> &rdata,
               Wire<1> &wvalid,
               Wire<AddrWidth> &waddr,
               Wire<DataWidth> &wdata,
               Wire<StrbWidth> &wstrb)
      : clk(clk), rst(rst), raddr(raddr), rdata(rdata), wvalid(wvalid), waddr(waddr), wdata(wdata), wstrb(wstrb),
        mem_(DepthBytes, 0u) {
    eval();
  }

  void eval() {
    std::uint64_t base = raddr.value();
    std::uint64_t v = 0;
    for (unsigned i = 0; i < StrbWidth; i++) {
      std::uint64_t ai = base + i;
      std::uint8_t b = (ai < DepthBytes) ? mem_[static_cast<std::size_t>(ai)] : 0u;
      v |= (static_cast<std::uint64_t>(b) << (8u * i));
    }
    rdata = Wire<DataWidth>(v);
  }

  void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) && clkNow;
    clkPrev = clkNow;
    if (!posedge)
      return;

    pendingWrite = false;
    if (rst.toBool())
      return;

    if (wvalid.toBool()) {
      pendingWrite = true;
      latchedAddr = waddr.value();
      latchedData = wdata.value();
      latchedStrb = wstrb.value();
    }
  }

  void tick_commit() {
    if (pendingWrite) {
      std::uint64_t base = latchedAddr;
      for (unsigned i = 0; i < StrbWidth; i++) {
        if (!(latchedStrb & (std::uint64_t{1} << i)))
          continue;
        std::uint64_t ai = base + i;
        if (ai >= DepthBytes)
          continue;
        mem_[static_cast<std::size_t>(ai)] = static_cast<std::uint8_t>((latchedData >> (8u * i)) & 0xFFu);
      }
    }
    pendingWrite = false;
    eval();
  }

  // Convenience for testbenches.
  void pokeByte(std::size_t addr, std::uint8_t value) {
    if (addr < DepthBytes)
      mem_[addr] = value;
  }
  std::uint8_t peekByte(std::size_t addr) const { return (addr < DepthBytes) ? mem_[addr] : 0u; }

  std::uint32_t peek32(std::size_t addr) const {
    std::uint32_t v = 0;
    for (unsigned i = 0; i < 4; i++) {
      std::size_t ai = addr + i;
      std::uint8_t b = (ai < DepthBytes) ? mem_[ai] : 0u;
      v |= (static_cast<std::uint32_t>(b) << (8u * i));
    }
    return v;
  }

public:
  Wire<1> &clk;
  Wire<1> &rst;

  Wire<AddrWidth> &raddr;
  Wire<DataWidth> &rdata;

  Wire<1> &wvalid;
  Wire<AddrWidth> &waddr;
  Wire<DataWidth> &wdata;
  Wire<StrbWidth> &wstrb;

  bool clkPrev = false;
  bool pendingWrite = false;
  std::uint64_t latchedAddr = 0;
  std::uint64_t latchedData = 0;
  std::uint64_t latchedStrb = 0;

  std::vector<std::uint8_t> mem_;
};

} // namespace pyc::cpp
