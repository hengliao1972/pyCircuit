#pragma once

#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <ostream>

#include "pyc_bits.hpp"
#include "pyc_primitives.hpp"
#include "pyc_vec.hpp"

namespace pyc::cpp {
namespace detail {

struct StreamStateGuard {
  explicit StreamStateGuard(std::ostream &os) : os_(os), flags_(os.flags()), fill_(os.fill()) {}
  ~StreamStateGuard() {
    os_.flags(flags_);
    os_.fill(fill_);
  }

private:
  std::ostream &os_;
  std::ios::fmtflags flags_;
  char fill_;
};

template <unsigned Width>
inline void printBits(std::ostream &os, Bits<Width> v) {
  constexpr unsigned digits = (Width + 3) / 4;
  StreamStateGuard guard(os);
  os << Width << "'h" << std::hex << std::setfill('0');
  constexpr unsigned words = Bits<Width>::kWords;
  for (int wi = static_cast<int>(words) - 1; wi >= 0; --wi) {
    unsigned widx = static_cast<unsigned>(wi);
    unsigned wd = 16;
    if (widx == (words - 1)) {
      unsigned rem = digits % 16;
      if (rem != 0)
        wd = rem;
    }
    os << std::setw(wd) << v.word(widx);
  }
}

} // namespace detail

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const Bits<Width> &v) {
  detail::printBits<Width>(os, v);
  return os;
}

template <typename T, std::size_t N>
inline std::ostream &operator<<(std::ostream &os, const Vec<T, N> &v) {
  os << "{";
  for (std::size_t i = 0; i < N; i++) {
    if (i)
      os << ", ";
    os << v[i];
  }
  os << "}";
  return os;
}

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const pyc_add<Width> &m) {
  os << "pyc_add<" << Width << ">{a=" << m.a << " b=" << m.b << " y=" << m.y << "}";
  return os;
}

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const pyc_mux<Width> &m) {
  os << "pyc_mux<" << Width << ">{sel=" << m.sel << " a=" << m.a << " b=" << m.b << " y=" << m.y << "}";
  return os;
}

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const pyc_and<Width> &m) {
  os << "pyc_and<" << Width << ">{a=" << m.a << " b=" << m.b << " y=" << m.y << "}";
  return os;
}

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const pyc_or<Width> &m) {
  os << "pyc_or<" << Width << ">{a=" << m.a << " b=" << m.b << " y=" << m.y << "}";
  return os;
}

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const pyc_xor<Width> &m) {
  os << "pyc_xor<" << Width << ">{a=" << m.a << " b=" << m.b << " y=" << m.y << "}";
  return os;
}

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const pyc_not<Width> &m) {
  os << "pyc_not<" << Width << ">{a=" << m.a << " y=" << m.y << "}";
  return os;
}

template <unsigned Width>
inline std::ostream &operator<<(std::ostream &os, const pycReg<Width> &m) {
  os << "pycReg<" << Width << ">{en=" << m.en << " d=" << m.d << " init=" << m.init << " q=" << m.q << "}";
  return os;
}

template <unsigned Width, unsigned Depth>
inline std::ostream &operator<<(std::ostream &os, const pycFifo<Width, Depth> &m) {
  os << "pycFifo<" << Width << "," << Depth << ">{";
  os << "in_valid=" << m.in_valid << " in_ready=" << m.in_ready << " in_data=" << m.in_data;
  os << " out_valid=" << m.out_valid << " out_ready=" << m.out_ready << " out_data=" << m.out_data;
  os << " count=" << m.debug_count() << " rd=" << m.debug_rd_ptr() << " wr=" << m.debug_wr_ptr();
  os << "}";
  return os;
}

} // namespace pyc::cpp
