#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// Synchronous 1R1W memory with registered read output (prototype).
//
// - `DepthEntries` is in entries (not bytes).
// - Read output updates on the next posedge of `clk` when `ren` is asserted.
// - Write occurs on posedge when `wvalid` is asserted, with byte enables `wstrb`.
// - Read-during-write to the same address returns written data ("write-first")
//   via forwarding in the model.
template <unsigned AddrWidth, unsigned DataWidth, std::size_t DepthEntries>
class pyc_sync_mem {
public:
  static_assert(DataWidth > 0 && DataWidth <= 64, "pyc_sync_mem supports DataWidth 1..64 in the prototype");
  static_assert((DataWidth % 8) == 0, "pyc_sync_mem requires DataWidth divisible by 8 in the prototype");
  static_assert(DepthEntries > 0, "pyc_sync_mem DepthEntries must be > 0");
  static constexpr unsigned StrbWidth = DataWidth / 8;

  pyc_sync_mem(Wire<1> &clk,
               Wire<1> &rst,
               Wire<1> &ren,
               Wire<AddrWidth> &raddr,
               Wire<DataWidth> &rdata,
               Wire<1> &wvalid,
               Wire<AddrWidth> &waddr,
               Wire<DataWidth> &wdata,
               Wire<StrbWidth> &wstrb)
      : clk(clk), rst(rst), ren(ren), raddr(raddr), rdata(rdata), wvalid(wvalid), waddr(waddr), wdata(wdata),
        wstrb(wstrb) {}

  void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) && clkNow;
    clkPrev = clkNow;
    pendingWrite = false;
    pendingRead = false;
    if (!posedge)
      return;

    if (rst.toBool()) {
      pendingRead = true;
      rdataNext = Wire<DataWidth>(0);
      return;
    }

    if (wvalid.toBool()) {
      pendingWrite = true;
      latchedWaddr = waddr.value();
      latchedWdata = wdata.value();
      latchedWstrb = wstrb.value();
    }

    if (ren.toBool()) {
      pendingRead = true;
      latchedRaddr = raddr.value();
      std::uint64_t v = (latchedRaddr < DepthEntries) ? mem_[static_cast<std::size_t>(latchedRaddr)] : 0u;
      if (pendingWrite && (latchedWaddr == latchedRaddr))
        v = applyStrb(v, latchedWdata, latchedWstrb);
      rdataNext = Wire<DataWidth>(v);
    }
  }

  void tick_commit() {
    if (pendingWrite && (latchedWaddr < DepthEntries)) {
      std::size_t a = static_cast<std::size_t>(latchedWaddr);
      mem_[a] = applyStrb(mem_[a], latchedWdata, latchedWstrb);
    }
    if (pendingRead)
      rdata = rdataNext;
    pendingWrite = false;
    pendingRead = false;
  }

  // Convenience for testbenches.
  void pokeEntry(std::size_t addr, std::uint64_t value) {
    if (addr < DepthEntries)
      mem_[addr] = value & Wire<DataWidth>::mask();
  }
  std::uint64_t peekEntry(std::size_t addr) const { return (addr < DepthEntries) ? mem_[addr] : 0u; }

public:
  Wire<1> &clk;
  Wire<1> &rst;

  Wire<1> &ren;
  Wire<AddrWidth> &raddr;
  Wire<DataWidth> &rdata;

  Wire<1> &wvalid;
  Wire<AddrWidth> &waddr;
  Wire<DataWidth> &wdata;
  Wire<StrbWidth> &wstrb;

  bool clkPrev = false;
  bool pendingWrite = false;
  bool pendingRead = false;
  std::uint64_t latchedWaddr = 0;
  std::uint64_t latchedWdata = 0;
  std::uint64_t latchedWstrb = 0;
  std::uint64_t latchedRaddr = 0;
  Wire<DataWidth> rdataNext{};

private:
  static constexpr std::uint64_t applyStrb(std::uint64_t oldV, std::uint64_t newV, std::uint64_t strb) {
    std::uint64_t v = oldV;
    for (unsigned i = 0; i < StrbWidth; i++) {
      if (!(strb & (std::uint64_t{1} << i)))
        continue;
      v &= ~(std::uint64_t{0xFF} << (8u * i));
      v |= ((newV >> (8u * i)) & 0xFFu) << (8u * i);
    }
    return v & Wire<DataWidth>::mask();
  }

  std::array<std::uint64_t, DepthEntries> mem_{};
};

// Synchronous 2R1W memory (dual read ports) with registered read outputs (prototype).
template <unsigned AddrWidth, unsigned DataWidth, std::size_t DepthEntries>
class pyc_sync_mem_dp {
public:
  static_assert(DataWidth > 0 && DataWidth <= 64, "pyc_sync_mem_dp supports DataWidth 1..64 in the prototype");
  static_assert((DataWidth % 8) == 0, "pyc_sync_mem_dp requires DataWidth divisible by 8 in the prototype");
  static_assert(DepthEntries > 0, "pyc_sync_mem_dp DepthEntries must be > 0");
  static constexpr unsigned StrbWidth = DataWidth / 8;

  pyc_sync_mem_dp(Wire<1> &clk,
                  Wire<1> &rst,
                  Wire<1> &ren0,
                  Wire<AddrWidth> &raddr0,
                  Wire<DataWidth> &rdata0,
                  Wire<1> &ren1,
                  Wire<AddrWidth> &raddr1,
                  Wire<DataWidth> &rdata1,
                  Wire<1> &wvalid,
                  Wire<AddrWidth> &waddr,
                  Wire<DataWidth> &wdata,
                  Wire<StrbWidth> &wstrb)
      : clk(clk), rst(rst), ren0(ren0), raddr0(raddr0), rdata0(rdata0), ren1(ren1), raddr1(raddr1), rdata1(rdata1),
        wvalid(wvalid), waddr(waddr), wdata(wdata), wstrb(wstrb) {}

  void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) && clkNow;
    clkPrev = clkNow;
    pendingWrite = false;
    pendingRead0 = false;
    pendingRead1 = false;
    if (!posedge)
      return;

    if (rst.toBool()) {
      pendingRead0 = true;
      pendingRead1 = true;
      rdata0Next = Wire<DataWidth>(0);
      rdata1Next = Wire<DataWidth>(0);
      return;
    }

    if (wvalid.toBool()) {
      pendingWrite = true;
      latchedWaddr = waddr.value();
      latchedWdata = wdata.value();
      latchedWstrb = wstrb.value();
    }

    if (ren0.toBool()) {
      pendingRead0 = true;
      latchedRaddr0 = raddr0.value();
      std::uint64_t v = (latchedRaddr0 < DepthEntries) ? mem_[static_cast<std::size_t>(latchedRaddr0)] : 0u;
      if (pendingWrite && (latchedWaddr == latchedRaddr0))
        v = applyStrb(v, latchedWdata, latchedWstrb);
      rdata0Next = Wire<DataWidth>(v);
    }

    if (ren1.toBool()) {
      pendingRead1 = true;
      latchedRaddr1 = raddr1.value();
      std::uint64_t v = (latchedRaddr1 < DepthEntries) ? mem_[static_cast<std::size_t>(latchedRaddr1)] : 0u;
      if (pendingWrite && (latchedWaddr == latchedRaddr1))
        v = applyStrb(v, latchedWdata, latchedWstrb);
      rdata1Next = Wire<DataWidth>(v);
    }
  }

  void tick_commit() {
    if (pendingWrite && (latchedWaddr < DepthEntries)) {
      std::size_t a = static_cast<std::size_t>(latchedWaddr);
      mem_[a] = applyStrb(mem_[a], latchedWdata, latchedWstrb);
    }
    if (pendingRead0)
      rdata0 = rdata0Next;
    if (pendingRead1)
      rdata1 = rdata1Next;
    pendingWrite = false;
    pendingRead0 = false;
    pendingRead1 = false;
  }

  void pokeEntry(std::size_t addr, std::uint64_t value) {
    if (addr < DepthEntries)
      mem_[addr] = value & Wire<DataWidth>::mask();
  }
  std::uint64_t peekEntry(std::size_t addr) const { return (addr < DepthEntries) ? mem_[addr] : 0u; }

public:
  Wire<1> &clk;
  Wire<1> &rst;

  Wire<1> &ren0;
  Wire<AddrWidth> &raddr0;
  Wire<DataWidth> &rdata0;

  Wire<1> &ren1;
  Wire<AddrWidth> &raddr1;
  Wire<DataWidth> &rdata1;

  Wire<1> &wvalid;
  Wire<AddrWidth> &waddr;
  Wire<DataWidth> &wdata;
  Wire<StrbWidth> &wstrb;

  bool clkPrev = false;
  bool pendingWrite = false;
  bool pendingRead0 = false;
  bool pendingRead1 = false;
  std::uint64_t latchedWaddr = 0;
  std::uint64_t latchedWdata = 0;
  std::uint64_t latchedWstrb = 0;
  std::uint64_t latchedRaddr0 = 0;
  std::uint64_t latchedRaddr1 = 0;
  Wire<DataWidth> rdata0Next{};
  Wire<DataWidth> rdata1Next{};

private:
  static constexpr std::uint64_t applyStrb(std::uint64_t oldV, std::uint64_t newV, std::uint64_t strb) {
    std::uint64_t v = oldV;
    for (unsigned i = 0; i < StrbWidth; i++) {
      if (!(strb & (std::uint64_t{1} << i)))
        continue;
      v &= ~(std::uint64_t{0xFF} << (8u * i));
      v |= ((newV >> (8u * i)) & 0xFFu) << (8u * i);
    }
    return v & Wire<DataWidth>::mask();
  }

  std::array<std::uint64_t, DepthEntries> mem_{};
};

} // namespace pyc::cpp

