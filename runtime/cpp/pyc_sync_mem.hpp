#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <ostream>
#include <utility>
#include <vector>

#include "pyc_bits.hpp"

namespace pyc::cpp {
// write-first (transparent) read: a same-edge write to the read address is
// forwarded to the read output (port 1 wins conflicts), matching register
// file Mem semantics (Yosys RD_TRANSPARENCY_MASK=1). Write port 0 applies
// first; port 1 wins same-address same-cycle write conflicts.
template <unsigned AddrWidth, unsigned DataWidth, std::size_t DepthEntries>
class pyc_sync_mem_2w {
public:
  static_assert(DataWidth > 0, "pyc_sync_mem_2w requires DataWidth > 0");
  static_assert(DepthEntries > 0, "pyc_sync_mem_2w DepthEntries must be > 0");
  static constexpr unsigned StrbWidth = (DataWidth + 7) / 8;
  using change_mask_t = std::uint8_t;
  static constexpr change_mask_t kReadDataChanged = change_mask_t{1} << 0;

  pyc_sync_mem_2w(Wire<1> &clk,
                  Wire<1> &ren,
                  Wire<AddrWidth> &raddr,
                  Wire<DataWidth> &rdata,
                  Wire<1> &wvalid0,
                  Wire<AddrWidth> &waddr0,
                  Wire<DataWidth> &wdata0,
                  Wire<StrbWidth> &wstrb0,
                  Wire<1> &wvalid1,
                  Wire<AddrWidth> &waddr1,
                  Wire<DataWidth> &wdata1,
                  Wire<StrbWidth> &wstrb1)
      : clk(clk), ren(ren), raddr(raddr), rdata(rdata),
        wvalid0(wvalid0), waddr0(waddr0), wdata0(wdata0), wstrb0(wstrb0),
        wvalid1(wvalid1), waddr1(waddr1), wdata1(wdata1), wstrb1(wstrb1) {}

  std::uint64_t mem_hash(std::size_t lo = 0, std::size_t hi = (DepthEntries > 0 ? DepthEntries - 1 : 0)) const {
    if (DepthEntries == 0)
      return 0;
    if (lo > hi)
      std::swap(lo, hi);
    if (hi >= DepthEntries)
      hi = DepthEntries - 1;
    std::uint64_t h = 1469598103934665603ull;
    for (std::size_t i = lo; i <= hi; ++i)
      for (unsigned w = 0; w < Wire<DataWidth>::kWords; ++w) {
        h ^= mem_[i].word(w);
        h *= 1099511628211ull;
      }
    return h;
  }

  void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) && clkNow;
    clkPrev = clkNow;
    pendingWrite0 = false;
    pendingWrite1 = false;
    pendingRead = false;
    if (!posedge)
      return;

    if (wvalid0.toBool()) {
      pendingWrite0 = true;
      latchedWaddr0 = toIndex(waddr0);
      latchedWdata0 = wdata0;
      latchedWstrb0 = wstrb0;
    }
    if (wvalid1.toBool()) {
      pendingWrite1 = true;
      latchedWaddr1 = toIndex(waddr1);
      latchedWdata1 = wdata1;
      latchedWstrb1 = wstrb1;
    }

    if (ren.toBool()) {
      pendingRead = true;
      latchedRaddr = toIndex(raddr);
      Wire<DataWidth> v = Wire<DataWidth>(0);
      if (latchedRaddr < DepthEntries)
        v = mem_[latchedRaddr];
      // Write-first (transparent) read: a same-edge write to the read
      // address is forwarded (port 1 wins), matching register-file Mem
      // semantics (Yosys RD_TRANSPARENCY_MASK=1).
      if (latchedRaddr < DepthEntries) {
        if (pendingWrite0 && latchedWaddr0 == latchedRaddr)
          v = applyStrb(v, latchedWdata0, latchedWstrb0);
        if (pendingWrite1 && latchedWaddr1 == latchedRaddr)
          v = applyStrb(v, latchedWdata1, latchedWstrb1);
      }
      rdataNext = v;
    }
  }

  // The single bit reports a semantic change on the registered read port.
  change_mask_t tick_commit() {
    change_mask_t changed = 0;
    if (pendingWrite0 && (latchedWaddr0 < DepthEntries))
      mem_[latchedWaddr0] =
          applyStrb(mem_[latchedWaddr0], latchedWdata0, latchedWstrb0);
    if (pendingWrite1 && (latchedWaddr1 < DepthEntries))
      mem_[latchedWaddr1] =
          applyStrb(mem_[latchedWaddr1], latchedWdata1, latchedWstrb1);
    if (pendingRead) {
      if (rdata != rdataNext)
        changed |= kReadDataChanged;
      rdata = rdataNext;
    }
    pendingWrite0 = false;
    pendingWrite1 = false;
    pendingRead = false;
    return changed;
  }

  void pokeEntry(std::size_t addr, Wire<DataWidth> value) {
    if (addr < DepthEntries)
      mem_[addr] = value;
  }
  void pokeEntry(std::size_t addr, std::uint64_t value) { pokeEntry(addr, Wire<DataWidth>(value)); }
  Wire<DataWidth> peekEntryBits(std::size_t addr) const {
    return (addr < DepthEntries) ? mem_[addr] : Wire<DataWidth>(0);
  }
  std::uint64_t peekEntry(std::size_t addr) const { return peekEntryBits(addr).value(); }

public:
  Wire<1> &clk;

  Wire<1> &ren;
  Wire<AddrWidth> &raddr;
  Wire<DataWidth> &rdata;

  Wire<1> &wvalid0;
  Wire<AddrWidth> &waddr0;
  Wire<DataWidth> &wdata0;
  Wire<StrbWidth> &wstrb0;

  Wire<1> &wvalid1;
  Wire<AddrWidth> &waddr1;
  Wire<DataWidth> &wdata1;
  Wire<StrbWidth> &wstrb1;

  bool clkPrev = false;
  bool pendingWrite0 = false;
  bool pendingWrite1 = false;
  bool pendingRead = false;
  std::size_t latchedWaddr0 = 0;
  std::size_t latchedWaddr1 = 0;
  std::size_t latchedRaddr = 0;
  Wire<DataWidth> latchedWdata0{};
  Wire<StrbWidth> latchedWstrb0{};
  Wire<DataWidth> latchedWdata1{};
  Wire<StrbWidth> latchedWstrb1{};
  Wire<DataWidth> rdataNext{};

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

  static constexpr unsigned lastLaneBits() {
    unsigned rem = DataWidth % 8;
    return (rem == 0) ? 8 : rem;
  }

  static constexpr Wire<DataWidth> applyStrb(Wire<DataWidth> oldV, Wire<DataWidth> newV, Wire<StrbWidth> strb) {
    if constexpr (DataWidth <= 64) {
      std::uint64_t out = oldV.value();
      std::uint64_t src = newV.value();
      for (unsigned i = 0; i < StrbWidth; i++) {
        if (!strb.bit(i))
          continue;
        unsigned bitsInLane = (i == StrbWidth - 1) ? lastLaneBits() : 8u;
        std::uint64_t mask = ((1ull << bitsInLane) - 1ull) << (8u * i);
        out = (out & ~mask) | (src & mask);
      }
      return Wire<DataWidth>(out);
    }
    Wire<DataWidth> v = oldV;
    constexpr unsigned kLanesPerWord = 64u / 8u;
    constexpr unsigned kWords = Wire<DataWidth>::kWords;
    for (unsigned w = 0; w < kWords; ++w) {
      unsigned laneLo = w * kLanesPerWord;
      if (laneLo >= StrbWidth)
        break;
      unsigned laneHi = laneLo + kLanesPerWord;
      if (laneHi > StrbWidth)
        laneHi = StrbWidth;
      std::uint64_t m = 0;
      for (unsigned i = laneLo; i < laneHi; ++i) {
        if (!strb.bit(i))
          continue;
        unsigned bitsInLane = (i == StrbWidth - 1) ? lastLaneBits() : 8u;
        std::uint64_t laneMask =
            (bitsInLane >= 64) ? ~std::uint64_t{0} : ((1ull << bitsInLane) - 1ull);
        m |= laneMask << (8u * (i - laneLo));
      }
      if (m != 0)
        v.setWord(w, (v.word(w) & ~m) | (newV.word(w) & m));
    }
    return v;
  }

  std::array<Wire<DataWidth>, DepthEntries> mem_{};
};


// Synchronous NxM memory: N registered read ports, M write ports (write
// index order applies; higher index wins same-address conflicts).
// OldData=false (read_mode=forward): each read port forwards the last
// matching same-edge write. OldData=true (read_mode=old): read-during-write
// returns the pre-write data, like pyc_sync_mem / a plain SRAM.
template <unsigned AddrWidth, unsigned DataWidth, std::size_t DepthEntries,
          unsigned RPorts, unsigned WPorts, bool OldData = false>
class pyc_sync_mem_nm {
public:
  static_assert(DataWidth > 0, "pyc_sync_mem_nm requires DataWidth > 0");
  static_assert(DepthEntries > 0, "pyc_sync_mem_nm DepthEntries must be > 0");
  static_assert(RPorts > 0 && RPorts <= 64, "pyc_sync_mem_nm RPorts in 1..64");
  static_assert(WPorts > 0, "pyc_sync_mem_nm WPorts must be > 0");
  static constexpr unsigned StrbWidth = (DataWidth + 7) / 8;
  using change_mask_t = std::uint64_t;

  pyc_sync_mem_nm(Wire<1> &clk,
                  const Wire<1> *const *rens,
                  const Wire<AddrWidth> *const *raddrs,
                  Wire<DataWidth> *const *rdatas,
                  const Wire<1> *const *wvalids,
                  const Wire<AddrWidth> *const *waddrs,
                  const Wire<DataWidth> *const *wdatas,
                  const Wire<StrbWidth> *const *wstrbs)
      : clk(clk) {
    for (unsigned i = 0; i < RPorts; ++i) {
      ren[i] = rens[i];
      raddr[i] = raddrs[i];
      rdata[i] = rdatas[i];
    }
    for (unsigned k = 0; k < WPorts; ++k) {
      wvalid[k] = wvalids[k];
      waddr[k] = waddrs[k];
      wdata[k] = wdatas[k];
      wstrb[k] = wstrbs[k];
    }
  }

  std::uint64_t mem_hash(std::size_t lo = 0, std::size_t hi = (DepthEntries > 0 ? DepthEntries - 1 : 0)) const {
    if (lo > hi) std::swap(lo, hi);
    if (hi >= DepthEntries) hi = DepthEntries - 1;
    std::uint64_t h = 1469598103934665603ull;
    for (std::size_t i = lo; i <= hi; ++i)
      for (unsigned w = 0; w < Wire<DataWidth>::kWords; ++w) {
        h ^= mem_[i].word(w);
        h *= 1099511628211ull;
      }
    return h;
  }

  void tick_compute() {
    bool clkNow = clk.toBool();
    bool posedge = (!clkPrev) && clkNow;
    clkPrev = clkNow;
    for (unsigned k = 0; k < WPorts; ++k) {
      pendingWrite[k] = false;
      if (posedge && wvalid[k]->toBool()) {
        pendingWrite[k] = true;
        latchedWaddr[k] = toIndex(*waddr[k]);
        latchedWdata[k] = *wdata[k];
        latchedWstrb[k] = *wstrb[k];
      }
    }
    for (unsigned i = 0; i < RPorts; ++i) {
      pendingRead[i] = false;
      rdataNext[i] = Wire<DataWidth>(0);
      if (!posedge)
        continue;
      if (!ren[i]->toBool())
        continue;
      pendingRead[i] = true;
      std::size_t ra = toIndex(*raddr[i]);
      Wire<DataWidth> v = Wire<DataWidth>(0);
      if (ra < DepthEntries) {
        v = mem_[ra];
        if constexpr (!OldData) {
          // read_mode=forward: apply matching same-edge writes in index
          // order (higher write port wins). read_mode=old skips this and
          // returns the pre-write data.
          for (unsigned k = 0; k < WPorts; ++k)
            if (pendingWrite[k] && latchedWaddr[k] == ra)
              v = applyStrbNM(v, latchedWdata[k], latchedWstrb[k]);
        }
      }
      rdataNext[i] = v;
    }
  }

  // Bit i reports a semantic change on read port i.
  change_mask_t tick_commit() {
    change_mask_t changed = 0;
    for (unsigned k = 0; k < WPorts; ++k) {
      if (pendingWrite[k] && (latchedWaddr[k] < DepthEntries))
        mem_[latchedWaddr[k]] =
            applyStrbNM(mem_[latchedWaddr[k]], latchedWdata[k], latchedWstrb[k]);
      pendingWrite[k] = false;
    }
    for (unsigned i = 0; i < RPorts; ++i) {
      if (pendingRead[i]) {
        if (*rdata[i] != rdataNext[i])
          changed |= (change_mask_t{1} << i);
        *rdata[i] = rdataNext[i];
      }
      pendingRead[i] = false;
    }
    return changed;
  }

  void pokeEntry(std::size_t addr, std::uint64_t value) {
    if (addr < DepthEntries)
      mem_[addr] = Wire<DataWidth>(value);
  }
  std::uint64_t peekEntry(std::size_t addr) const {
    return (addr < DepthEntries) ? mem_[addr].value() : 0;
  }

public:
  Wire<1> &clk;
  const Wire<1> *ren[RPorts];
  const Wire<AddrWidth> *raddr[RPorts];
  Wire<DataWidth> *rdata[RPorts];
  const Wire<1> *wvalid[WPorts];
  const Wire<AddrWidth> *waddr[WPorts];
  const Wire<DataWidth> *wdata[WPorts];
  const Wire<StrbWidth> *wstrb[WPorts];

  bool clkPrev = false;
  bool pendingWrite[WPorts] = {};
  bool pendingRead[RPorts] = {};
  std::size_t latchedWaddr[WPorts] = {};
  Wire<DataWidth> latchedWdata[WPorts] = {};
  Wire<StrbWidth> latchedWstrb[WPorts] = {};
  Wire<DataWidth> rdataNext[RPorts] = {};

private:
  static constexpr std::size_t toIndex(Wire<AddrWidth> addr) {
    if constexpr (AddrWidth <= (sizeof(std::size_t) * 8u))
      return static_cast<std::size_t>(addr.value());
    constexpr unsigned hostBits = static_cast<unsigned>(sizeof(std::size_t) * 8u);
    constexpr unsigned useBits = (AddrWidth < hostBits) ? AddrWidth : hostBits;
    std::size_t out = 0;
    for (unsigned i = 0; i < useBits; i++)
      if (addr.bit(i))
        out |= (std::size_t{1} << i);
    return out;
  }

  static constexpr unsigned lastLaneBits() {
    unsigned rem = DataWidth % 8;
    return (rem == 0) ? 8 : rem;
  }

  static constexpr Wire<DataWidth> applyStrbNM(Wire<DataWidth> oldV, Wire<DataWidth> newV, Wire<StrbWidth> strb) {
    if constexpr (DataWidth <= 64) {
      std::uint64_t out = oldV.value();
      std::uint64_t src = newV.value();
      for (unsigned i = 0; i < StrbWidth; i++) {
        if (!strb.bit(i))
          continue;
        unsigned bitsInLane = (i == StrbWidth - 1) ? lastLaneBits() : 8u;
        std::uint64_t mask = ((1ull << bitsInLane) - 1ull) << (8u * i);
        out = (out & ~mask) | (src & mask);
      }
      return Wire<DataWidth>(out);
    }
    Wire<DataWidth> v = oldV;
    constexpr unsigned kLanesPerWord = 64u / 8u;
    constexpr unsigned kWords = Wire<DataWidth>::kWords;
    for (unsigned w = 0; w < kWords; ++w) {
      unsigned laneLo = w * kLanesPerWord;
      if (laneLo >= StrbWidth)
        break;
      unsigned laneHi = laneLo + kLanesPerWord;
      if (laneHi > StrbWidth)
        laneHi = StrbWidth;
      std::uint64_t m = 0;
      for (unsigned i = laneLo; i < laneHi; ++i) {
        if (!strb.bit(i))
          continue;
        unsigned bitsInLane = (i == StrbWidth - 1) ? lastLaneBits() : 8u;
        std::uint64_t laneMask =
            (bitsInLane >= 64) ? ~std::uint64_t{0} : ((1ull << bitsInLane) - 1ull);
        m |= laneMask << (8u * (i - laneLo));
      }
      if (m != 0)
        v.setWord(w, (v.word(w) & ~m) | (newV.word(w) & m));
    }
    return v;
  }

  std::array<Wire<DataWidth>, DepthEntries> mem_{};
};

} // namespace pyc::cpp
