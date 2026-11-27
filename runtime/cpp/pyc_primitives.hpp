#pragma once

#include <cstdint>

#include "pyc_bits.hpp"
#include "pyc_clock.hpp"
#include "pyc_component.hpp"

#include "pyc_delay_line.hpp"
#include "pyc_fifo.hpp"
#include "pyc_reg.hpp"

namespace pyc::cpp {

// "Module-like" primitives that mirror the Verilog templates in
// `runtime/verilog/` (same names, same port names).
//
// These are intended as stable building blocks for generated C++ cycle-accurate
// models.

template <unsigned Width>
struct pyc_add {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a + b; }
};

template <unsigned Width>
struct pyc_mux {
  Wire<1> &sel;
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = sel.toBool() ? a : b; }
};

template <unsigned Width>
struct pyc_and {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a & b; }
};

template <unsigned Width>
struct pyc_or {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a | b; }
};

template <unsigned Width>
struct pyc_xor {
  Wire<Width> &a;
  Wire<Width> &b;
  Wire<Width> &y;

  void eval() { y = a ^ b; }
};

template <unsigned Width>
struct pyc_not {
  Wire<Width> &a;
  Wire<Width> &y;

  void eval() { y = ~a; }
};

} // namespace pyc::cpp
