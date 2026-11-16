#pragma once

#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <ostream>
#include <utility>
#include <vector>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// Byte-addressed memory with async read + sync write.
//
// - `DepthBytes` is in bytes.
// - `rdata` is assembled little-endian from successive bytes at `raddr`.
// - Write uses `wstrb` byte enables relative to `waddr`.
// - Addresses are low-bit indexed into host `size_t`; out-of-range bytes are 0.
template <unsigned AddrWidth, unsigned DataWidth, std::size_t DepthBytes>
class pyc_byte_mem {
public:
  static_assert(DataWidth > 0, "pyc_byte_mem requires DataWidth > 0");
  static_assert((DataWidth % 8) == 0, "pyc_byte_mem requires DataWidth divisible by 8");
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

  struct MemWatchEvent {
    enum class Kind : std::uint8_t { Read = 0, Write = 1 };
    Kind kind = Kind::Read;
    std::uint8_t port = 0;
    std::size_t addr = 0; // byte address
    Wire<DataWidth> data{};
    Wire<StrbWidth> strb{};
  };

  // Decision 0006: memory observability supports hash/watch/dump.
  //
  // Notes:
  // - For byte memories, `addr`/watch ranges are in bytes.
  // - Read events are recorded when `eval()` recomputes `rdata` (i.e. on an
  //   raddr change or after a write dirties data).
  // - Write events are recorded on the active clock edge when `wvalid` is
  //   asserted (data is the committed word at `waddr` after applying `wstrb`).
  void mem_watch(std::size_t lo, std::size_t hi) {
    if (lo > hi)
      std::swap(lo, hi);
    watch_enabled_ = true;
    watch_lo_ = lo;
    watch_hi_ = hi;
    watch_events_.clear();
  }
  void mem_watch_disable() {
    watch_enabled_ = false;
    watch_events_.clear();
  }
  bool mem_watch_enabled() const { return watch_enabled_; }
  void mem_watch_clear() { watch_events_.clear(); }
  const std::vector<MemWatchEvent> &mem_watch_events() const { return watch_events_; }

  std::uint64_t mem_hash(std::size_t lo = 0, std::size_t hi = (DepthBytes > 0 ? DepthBytes - 1 : 0)) const {
    if (DepthBytes == 0)
      return 0;
    if (lo > hi)
      std::swap(lo, hi);
    if (hi >= DepthBytes)
      hi = DepthBytes - 1;

    std::uint64_t h = 1469598103934665603ull;
    for (std::size_t i = lo; i <= hi; ++i) {
      h ^= static_cast<std::uint64_t>(mem_[i]);
      h *= 1099511628211ull;
    }
    return h;
  }

  void mem_dump(std::ostream &os, std::size_t lo = 0, std::size_t hi = (DepthBytes > 0 ? DepthBytes - 1 : 0)) const {
    if (DepthBytes == 0)
      return;
    if (lo > hi)
      std::swap(lo, hi);
    if (hi >= DepthBytes)
      hi = DepthBytes - 1;

    for (std::size_t i = lo; i <= hi; ++i) {
      os << "{\"addr\":" << i << ",\"byte\":\"0x" << std::hex << std::setw(2) << std::setfill('0')
         << static_cast<unsigned>(mem_[i]) << std::dec << "\"}\n";
    }
  }

  void eval() {
    std::size_t base = toIndex(raddr);
    if (evalValid && !dataDirty && base == lastEvalRaddr)
      return;

    Wire<DataWidth> v = Wire<DataWidth>(0);
    for (unsigned i = 0; i < StrbWidth; i++) {
      std::size_t ai = base + static_cast<std::size_t>(i);
      std::uint8_t b = (ai < DepthBytes) ? mem_[ai] : 0u;
      Wire<DataWidth> byteW = zext<DataWidth, 8>(Wire<8>(b));
      v = v | shl<DataWidth>(byteW, 8u * i);
    }
    rdata = v;
    lastEvalRaddr = base;
    evalValid = true;
    dataDirty = false;

    if (watch_enabled_) {
      const std::size_t lo = base;
      const std::size_t hi = base + static_cast<std::size_t>(StrbWidth ? (StrbWidth - 1) : 0);
      const bool hit = !(hi < watch_lo_ || lo > watch_hi_);
      if (hit) {
        MemWatchEvent ev;
        ev.kind = MemWatchEvent::Kind::Read;
        ev.port = 0;
        ev.addr = base;
        ev.data = v;
        ev.strb = Wire<StrbWidth>(0);
        watch_events_.push_back(ev);
      }
    }
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
      latchedAddr = toIndex(waddr);
      latchedData = wdata;
      latchedStrb = wstrb;
    }
  }

  // Preserves write/watch/eval side effects and reports only a visible rdata change.
  bool tick_commit() {
    const bool hadPendingWrite = pendingWrite;
    const Wire<DataWidth> oldRdata = rdata;
    if (pendingWrite) {
      std::size_t base = latchedAddr;
      for (unsigned i = 0; i < StrbWidth; i++) {
        if (!latchedStrb.bit(i))
          continue;
        std::size_t ai = base + static_cast<std::size_t>(i);
        if (ai >= DepthBytes)
          continue;
        Wire<8> byte = extract<8, DataWidth>(latchedData, 8u * i);
        mem_[ai] = static_cast<std::uint8_t>(byte.value() & 0xFFu);
      }
      dataDirty = true;

      if (watch_enabled_) {
        const std::size_t lo = base;
        const std::size_t hi = base + static_cast<std::size_t>(StrbWidth ? (StrbWidth - 1) : 0);
        const bool hit = !(hi < watch_lo_ || lo > watch_hi_);
        if (hit) {
          // Reconstruct committed word.
          Wire<DataWidth> v = Wire<DataWidth>(0);
          for (unsigned i = 0; i < StrbWidth; i++) {
            std::size_t ai = base + static_cast<std::size_t>(i);
            std::uint8_t b = (ai < DepthBytes) ? mem_[ai] : 0u;
            Wire<DataWidth> byteW = zext<DataWidth, 8>(Wire<8>(b));
            v = v | shl<DataWidth>(byteW, 8u * i);
          }
          MemWatchEvent ev;
          ev.kind = MemWatchEvent::Kind::Write;
          ev.port = 0;
          ev.addr = base;
          ev.data = v;
          ev.strb = latchedStrb;
          watch_events_.push_back(ev);
        }
      }
    }
    pendingWrite = false;
    eval();
    return hadPendingWrite && oldRdata != rdata;
  }

  // Convenience for testbenches.
  void pokeByte(std::size_t addr, std::uint8_t value) {
    if (addr < DepthBytes) {
      mem_[addr] = value;
      dataDirty = true;
    }
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
  std::size_t latchedAddr = 0;
  Wire<DataWidth> latchedData{};
  Wire<StrbWidth> latchedStrb{};
  bool evalValid = false;
  bool dataDirty = true;
  std::size_t lastEvalRaddr = 0;

  std::vector<std::uint8_t> mem_;

private:
  static constexpr std::size_t toIndex(Wire<AddrWidth> addr) {
    if constexpr (AddrWidth <= (sizeof(std::size_t) * 8u))
      return static_cast<std::size_t>(addr.value());
    constexpr unsigned hostBits = static_cast<unsigned>(sizeof(std::size_t) * 8u);
    constexpr unsigned useBits = (AddrWidth < hostBits) ? AddrWidth : hostBits;
    std::size_t out = 0;
    for (unsigned i = 0; i < useBits; i++) {
      if (addr.bit(i))
        out |= (std::size_t{1} << i);
    }
    return out;
  }

  bool watch_enabled_ = false;
  std::size_t watch_lo_ = 0;
  std::size_t watch_hi_ = 0;
  std::vector<MemWatchEvent> watch_events_{};
};

} // namespace pyc::cpp
