#pragma once

#include <cstdint>
#include <limits>

namespace pyc::cpp {

template <unsigned Width>
class Bits {
public:
  static_assert(Width > 0 && Width <= 64, "pyc::cpp::Bits supports widths 1..64 in the prototype");

  using storage_type = std::uint64_t;

  constexpr Bits() = default;
  constexpr explicit Bits(storage_type v) : v_(v & mask()) {}

  constexpr storage_type value() const { return v_; }
  constexpr bool toBool() const { return (v_ & 1u) != 0; }

  static constexpr storage_type mask() {
    if constexpr (Width == 64)
      return ~storage_type{0};
    return (storage_type{1} << Width) - 1;
  }

  friend constexpr Bits operator+(Bits a, Bits b) { return Bits(a.v_ + b.v_); }
  friend constexpr Bits operator-(Bits a, Bits b) { return Bits(a.v_ - b.v_); }
  friend constexpr Bits operator*(Bits a, Bits b) { return Bits(a.v_ * b.v_); }
  friend constexpr Bits operator&(Bits a, Bits b) { return Bits(a.v_ & b.v_); }
  friend constexpr Bits operator|(Bits a, Bits b) { return Bits(a.v_ | b.v_); }
  friend constexpr Bits operator^(Bits a, Bits b) { return Bits(a.v_ ^ b.v_); }
  friend constexpr Bits operator~(Bits a) { return Bits(~a.v_); }

  friend constexpr bool operator==(Bits a, Bits b) { return a.value() == b.value(); }
  friend constexpr bool operator!=(Bits a, Bits b) { return !(a == b); }

private:
  storage_type v_ = 0;
};

template <unsigned Width>
using Wire = Bits<Width>;

template <unsigned Width>
constexpr std::int64_t asSigned(Wire<Width> v) {
  static_assert(Width > 0 && Width <= 64, "asSigned supports widths 1..64");
  if constexpr (Width == 64) {
    return static_cast<std::int64_t>(v.value());
  } else {
    std::uint64_t x = v.value();
    std::uint64_t signBit = std::uint64_t{1} << (Width - 1);
    if (x & signBit)
      x |= (~std::uint64_t{0}) << Width;
    return static_cast<std::int64_t>(x);
  }
}

template <unsigned Width>
constexpr Wire<Width> shl(Wire<Width> v, unsigned amount) {
  static_assert(Width > 0 && Width <= 64, "shl supports widths 1..64");
  if (amount == 0)
    return v;
  if (amount >= Width)
    return Wire<Width>(0);
  return Wire<Width>(v.value() << amount);
}

template <unsigned Width>
constexpr Wire<Width> lshr(Wire<Width> v, unsigned amount) {
  static_assert(Width > 0 && Width <= 64, "lshr supports widths 1..64");
  if (amount == 0)
    return v;
  if (amount >= Width)
    return Wire<Width>(0);
  return Wire<Width>(v.value() >> amount);
}

template <unsigned Width>
constexpr Wire<Width> ashr(Wire<Width> v, unsigned amount) {
  static_assert(Width > 0 && Width <= 64, "ashr supports widths 1..64");
  if (amount == 0)
    return v;
  if (amount >= Width)
    return asSigned(v) < 0 ? Wire<Width>(Wire<Width>::mask()) : Wire<Width>(0);
  std::int64_t x = asSigned(v);
  std::int64_t y = x >> amount;
  return Wire<Width>(static_cast<std::uint64_t>(y));
}

template <unsigned Width>
constexpr Wire<Width> udiv(Wire<Width> a, Wire<Width> b) {
  static_assert(Width > 0 && Width <= 64, "udiv supports widths 1..64");
  std::uint64_t denom = b.value();
  if (denom == 0)
    return Wire<Width>(0);
  return Wire<Width>(a.value() / denom);
}

template <unsigned Width>
constexpr Wire<Width> urem(Wire<Width> a, Wire<Width> b) {
  static_assert(Width > 0 && Width <= 64, "urem supports widths 1..64");
  std::uint64_t denom = b.value();
  if (denom == 0)
    return Wire<Width>(0);
  return Wire<Width>(a.value() % denom);
}

template <unsigned Width>
constexpr Wire<Width> sdiv(Wire<Width> a, Wire<Width> b) {
  static_assert(Width > 0 && Width <= 64, "sdiv supports widths 1..64");
  std::int64_t denom = asSigned(b);
  if (denom == 0)
    return Wire<Width>(0);
  std::int64_t numer = asSigned(a);
  if constexpr (Width == 64) {
    if (numer == std::numeric_limits<std::int64_t>::min() && denom == -1)
      return Wire<Width>(static_cast<std::uint64_t>(numer));
  }
  std::int64_t q = numer / denom;
  return Wire<Width>(static_cast<std::uint64_t>(q));
}

template <unsigned Width>
constexpr Wire<Width> srem(Wire<Width> a, Wire<Width> b) {
  static_assert(Width > 0 && Width <= 64, "srem supports widths 1..64");
  std::int64_t denom = asSigned(b);
  if (denom == 0)
    return Wire<Width>(0);
  std::int64_t numer = asSigned(a);
  if constexpr (Width == 64) {
    if (numer == std::numeric_limits<std::int64_t>::min() && denom == -1)
      return Wire<Width>(0);
  }
  std::int64_t r = numer % denom;
  return Wire<Width>(static_cast<std::uint64_t>(r));
}

namespace detail {

template <unsigned... Ws>
struct SumWidth;

template <>
struct SumWidth<> {
  static constexpr unsigned value = 0;
};

template <unsigned W0, unsigned... Rest>
struct SumWidth<W0, Rest...> {
  static constexpr unsigned value = W0 + SumWidth<Rest...>::value;
};

} // namespace detail

template <unsigned OutWidth, unsigned InWidth>
constexpr Wire<OutWidth> trunc(Wire<InWidth> v) {
  static_assert(OutWidth > 0 && OutWidth <= 64, "trunc supports widths 1..64");
  static_assert(InWidth > 0 && InWidth <= 64, "trunc supports widths 1..64");
  static_assert(OutWidth <= InWidth, "trunc requires OutWidth <= InWidth");
  return Wire<OutWidth>(v.value());
}

template <unsigned OutWidth, unsigned InWidth>
constexpr Wire<OutWidth> zext(Wire<InWidth> v) {
  static_assert(OutWidth > 0 && OutWidth <= 64, "zext supports widths 1..64");
  static_assert(InWidth > 0 && InWidth <= 64, "zext supports widths 1..64");
  static_assert(OutWidth >= InWidth, "zext requires OutWidth >= InWidth");
  return Wire<OutWidth>(v.value());
}

template <unsigned OutWidth, unsigned InWidth>
constexpr Wire<OutWidth> sext(Wire<InWidth> v) {
  static_assert(OutWidth > 0 && OutWidth <= 64, "sext supports widths 1..64");
  static_assert(InWidth > 0 && InWidth <= 64, "sext supports widths 1..64");
  static_assert(OutWidth >= InWidth, "sext requires OutWidth >= InWidth");

  if constexpr (InWidth == 64) {
    return Wire<OutWidth>(v.value());
  } else {
    std::uint64_t x = v.value();
    std::uint64_t signBit = std::uint64_t{1} << (InWidth - 1);
    if (x & signBit)
      x |= (~std::uint64_t{0}) << InWidth;
    return Wire<OutWidth>(x);
  }
}

template <unsigned OutWidth, unsigned InWidth>
constexpr Wire<OutWidth> extract(Wire<InWidth> v, unsigned lsb) {
  static_assert(OutWidth > 0 && OutWidth <= 64, "extract supports widths 1..64");
  static_assert(InWidth > 0 && InWidth <= 64, "extract supports widths 1..64");
  return Wire<OutWidth>(v.value() >> lsb);
}

template <unsigned A>
constexpr Wire<A> concat(Wire<A> a) {
  return a;
}

template <unsigned A, unsigned B>
constexpr Wire<A + B> concat(Wire<A> a, Wire<B> b) {
  static_assert(A > 0 && B > 0, "concat inputs must be non-zero width");
  static_assert(A + B <= 64, "concat supports total widths 1..64 in the prototype");
  return Wire<A + B>((a.value() << B) | b.value());
}

template <unsigned A, unsigned B, unsigned C, unsigned... Rest>
constexpr Wire<A + B + C + detail::SumWidth<Rest...>::value> concat(Wire<A> a, Wire<B> b, Wire<C> c, Wire<Rest>... rest) {
  return concat(a, concat(b, c, rest...));
}

} // namespace pyc::cpp
