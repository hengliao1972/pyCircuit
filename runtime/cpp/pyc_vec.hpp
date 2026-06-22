#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <type_traits>

#include "pyc_bits.hpp"

namespace pyc::cpp {

// Multi-dimensional hardware vector. Vectors lower to nested Vec values
// (e.g. vector<4x8xi32> -> Vec<Vec<Wire<32>, 8>, 4>); leaves are Wire<W>.
// All arithmetic/bitwise ops below are element-wise and recurse through the
// nesting; scalar operands broadcast to every leaf.
//
// TODO: keep the Verilog emitter's element-wise vector semantics in sync with
// the operator set implemented here.
template <typename T, std::size_t N>
struct Vec {
  std::array<T, N> v{};

  constexpr T &operator[](std::size_t i) { return v[i]; }
  constexpr const T &operator[](std::size_t i) const { return v[i]; }

  static constexpr std::size_t size() { return N; }

  constexpr auto begin() { return v.begin(); }
  constexpr auto end() { return v.end(); }
  constexpr auto begin() const { return v.begin(); }
  constexpr auto end() const { return v.end(); }

  constexpr std::uint64_t value() const {
    std::uint64_t h = 0xcbf29ce484222325ull;
    for (std::size_t i = 0; i < N; ++i) {
      h ^= static_cast<std::uint64_t>(v[i].value());
      h *= 0x100000001b3ull;
    }
    return h;
  }
};

// Trait: detect Vec<T, N> so scalar broadcast overloads can exclude vectors.
template <typename>
struct is_vec : std::false_type {};
template <typename T, std::size_t N>
struct is_vec<Vec<T, N>> : std::true_type {};
template <typename T>
inline constexpr bool is_vec_v = is_vec<std::remove_cv_t<std::remove_reference_t<T>>>::value;

// Element-wise binary/unary operators (Vec op Vec, Vec op scalar, scalar op Vec).
// The scalar overloads are SFINAE-excluded for Vec operands so they do not
// collide with the Vec/Vec form.
#define PYC_VEC_BINOP(OP)                                                              \
  template <typename T, std::size_t N>                                                 \
  constexpr Vec<T, N> operator OP(const Vec<T, N> &a, const Vec<T, N> &b) {             \
    Vec<T, N> out{};                                                                   \
    for (std::size_t i = 0; i < N; ++i)                                                \
      out[i] = a[i] OP b[i];                                                           \
    return out;                                                                        \
  }                                                                                    \
  template <typename T, std::size_t N, typename S,                                     \
            typename = std::enable_if_t<!is_vec_v<S>>>                                  \
  constexpr Vec<T, N> operator OP(const Vec<T, N> &a, const S &s) {                     \
    Vec<T, N> out{};                                                                   \
    for (std::size_t i = 0; i < N; ++i)                                                \
      out[i] = a[i] OP s;                                                               \
    return out;                                                                        \
  }                                                                                    \
  template <typename T, std::size_t N, typename S,                                     \
            typename = std::enable_if_t<!is_vec_v<S>>>                                  \
  constexpr Vec<T, N> operator OP(const S &s, const Vec<T, N> &a) {                     \
    Vec<T, N> out{};                                                                   \
    for (std::size_t i = 0; i < N; ++i)                                                \
      out[i] = s OP a[i];                                                              \
    return out;                                                                        \
  }

PYC_VEC_BINOP(+)
PYC_VEC_BINOP(-)
PYC_VEC_BINOP(*)
PYC_VEC_BINOP(&)
PYC_VEC_BINOP(|)
PYC_VEC_BINOP(^)
#undef PYC_VEC_BINOP

template <typename T, std::size_t N>
constexpr Vec<T, N> operator~(const Vec<T, N> &a) {
  Vec<T, N> out{};
  for (std::size_t i = 0; i < N; ++i)
    out[i] = ~a[i];
  return out;
}

// Whole-vector equality (used by change detection / generic comparisons).
template <typename T, std::size_t N>
constexpr bool operator==(const Vec<T, N> &a, const Vec<T, N> &b) {
  for (std::size_t i = 0; i < N; ++i)
    if (!(a[i] == b[i]))
      return false;
  return true;
}
template <typename T, std::size_t N>
constexpr bool operator!=(const Vec<T, N> &a, const Vec<T, N> &b) {
  return !(a == b);
}

// Width-templated element-wise free functions. The C++ emitter lowers vector
// ops with the scalar element width, so e.g. a vector udiv emits udiv<32>(va, vb).
// Each overload recurses until it reaches the Wire<W> leaf functions in pyc_bits.
#define PYC_VEC_WIDTH_BINFN(FN)                                                        \
  template <unsigned W, typename T, std::size_t N>                                     \
  constexpr Vec<T, N> FN(const Vec<T, N> &a, const Vec<T, N> &b) {                      \
    Vec<T, N> out{};                                                                   \
    for (std::size_t i = 0; i < N; ++i)                                                \
      out[i] = FN<W>(a[i], b[i]);                                                       \
    return out;                                                                        \
  }                                                                                    \
  template <unsigned W, typename T, std::size_t N, typename S,                         \
            typename = std::enable_if_t<!is_vec_v<S>>>                                  \
  constexpr auto FN(const Vec<T, N> &a, const S &s) {                                   \
    using Elem = decltype(FN<W>(a[0], s));                                               \
    Vec<Elem, N> out{};                                                                 \
    for (std::size_t i = 0; i < N; ++i)                                                  \
      out[i] = FN<W>(a[i], s);                                                          \
    return out;                                                                         \
  }                                                                                    \
  template <unsigned W, typename S, typename T, std::size_t N,                         \
            typename = std::enable_if_t<!is_vec_v<S>>>                                  \
  constexpr auto FN(const S &s, const Vec<T, N> &a) {                                   \
    using Elem = decltype(FN<W>(s, a[0]));                                               \
    Vec<Elem, N> out{};                                                                 \
    for (std::size_t i = 0; i < N; ++i)                                                  \
      out[i] = FN<W>(s, a[i]);                                                          \
    return out;                                                                         \
  }

PYC_VEC_WIDTH_BINFN(udiv)
PYC_VEC_WIDTH_BINFN(urem)
PYC_VEC_WIDTH_BINFN(sdiv)
PYC_VEC_WIDTH_BINFN(srem)
PYC_VEC_WIDTH_BINFN(eq)
PYC_VEC_WIDTH_BINFN(ult)
#undef PYC_VEC_WIDTH_BINFN

// Element-wise shifts by a scalar amount (static or dynamic lowering both pass
// an unsigned amount that broadcasts to every lane).
#define PYC_VEC_SHIFT(FN)                                                              \
  template <unsigned W, typename T, std::size_t N>                                     \
  constexpr Vec<T, N> FN(const Vec<T, N> &a, unsigned amount) {                         \
    Vec<T, N> out{};                                                                   \
    for (std::size_t i = 0; i < N; ++i)                                                \
      out[i] = FN<W>(a[i], amount);                                                     \
    return out;                                                                        \
  }

PYC_VEC_SHIFT(shl)
PYC_VEC_SHIFT(lshr)
PYC_VEC_SHIFT(ashr)
#undef PYC_VEC_SHIFT

#define PYC_VEC_WIDTH_UNFN(FN)                                                         \
  template <unsigned OutW, unsigned InW, typename T, std::size_t N>                    \
  constexpr auto FN(const Vec<T, N> &a) {                                               \
    using Elem = decltype(FN<OutW, InW>(a[0]));                                         \
    Vec<Elem, N> out{};                                                                 \
    for (std::size_t i = 0; i < N; ++i)                                                 \
      out[i] = FN<OutW, InW>(a[i]);                                                     \
    return out;                                                                         \
  }

PYC_VEC_WIDTH_UNFN(trunc)
PYC_VEC_WIDTH_UNFN(zext)
PYC_VEC_WIDTH_UNFN(sext)
#undef PYC_VEC_WIDTH_UNFN

// slt needs special bool→Wire<1> conversion; kept manual (not macro-generated).
template <unsigned W, typename T, std::size_t N>
constexpr auto slt(const Vec<T, N> &a, const Vec<T, N> &b) {
  using Lane = decltype(slt<W>(a[0], b[0]));
  using Elem = std::conditional_t<std::is_same_v<Lane, bool>, Wire<1>, Lane>;
  Vec<Elem, N> out{};
  for (std::size_t i = 0; i < N; ++i) {
    auto lane = slt<W>(a[i], b[i]);
    if constexpr (std::is_same_v<decltype(lane), bool>)
      out[i] = Wire<1>(lane ? 1u : 0u);
    else
      out[i] = lane;
  }
  return out;
}

template <unsigned W, typename T, std::size_t N, typename S,
          typename = std::enable_if_t<!is_vec_v<S>>>
constexpr auto slt(const Vec<T, N> &a, const S &s) {
  using Lane = decltype(slt<W>(a[0], s));
  using Elem = std::conditional_t<std::is_same_v<Lane, bool>, Wire<1>, Lane>;
  Vec<Elem, N> out{};
  for (std::size_t i = 0; i < N; ++i) {
    auto lane = slt<W>(a[i], s);
    if constexpr (std::is_same_v<decltype(lane), bool>)
      out[i] = Wire<1>(lane ? 1u : 0u);
    else
      out[i] = lane;
  }
  return out;
}

template <unsigned W, typename S, typename T, std::size_t N,
          typename = std::enable_if_t<!is_vec_v<S>>>
constexpr auto slt(const S &s, const Vec<T, N> &a) {
  using Lane = decltype(slt<W>(s, a[0]));
  using Elem = std::conditional_t<std::is_same_v<Lane, bool>, Wire<1>, Lane>;
  Vec<Elem, N> out{};
  for (std::size_t i = 0; i < N; ++i) {
    auto lane = slt<W>(s, a[i]);
    if constexpr (std::is_same_v<decltype(lane), bool>)
      out[i] = Wire<1>(lane ? 1u : 0u);
    else
      out[i] = lane;
  }
  return out;
}

template <unsigned W, typename T, std::size_t N>
constexpr Vec<T, N> mux(Wire<1> sel, const Vec<T, N> &a, const Vec<T, N> &b) {
  Vec<T, N> out{};
  for (std::size_t i = 0; i < N; ++i)
    out[i] = mux<W>(sel, a[i], b[i]);
  return out;
}

template <unsigned W, typename S, typename T, std::size_t N>
constexpr Vec<T, N> mux(const Vec<S, N> &sel, const Vec<T, N> &a, const Vec<T, N> &b) {
  Vec<T, N> out{};
  for (std::size_t i = 0; i < N; ++i)
    out[i] = mux<W>(sel[i], a[i], b[i]);
  return out;
}

// Mixed scalar/vector arms: scalar implicitly broadcasts to every lane.
template <unsigned W, typename T, std::size_t N>
constexpr Vec<T, N> mux(Wire<1> sel, const Vec<T, N> &a, Wire<W> b) {
  Vec<T, N> out{};
  for (std::size_t i = 0; i < N; ++i)
    out[i] = mux<W>(sel, a[i], b);
  return out;
}

template <unsigned W, typename T, std::size_t N>
constexpr Vec<T, N> mux(Wire<1> sel, Wire<W> a, const Vec<T, N> &b) {
  Vec<T, N> out{};
  for (std::size_t i = 0; i < N; ++i)
    out[i] = mux<W>(sel, a, b[i]);
  return out;
}

template <unsigned W, typename S, typename T, std::size_t N>
constexpr Vec<T, N> mux(const Vec<S, N> &sel, const Vec<T, N> &a, Wire<W> b) {
  Vec<T, N> out{};
  for (std::size_t i = 0; i < N; ++i)
    out[i] = mux<W>(sel[i], a[i], b);
  return out;
}

template <unsigned W, typename S, typename T, std::size_t N>
constexpr Vec<T, N> mux(const Vec<S, N> &sel, Wire<W> a, const Vec<T, N> &b) {
  Vec<T, N> out{};
  for (std::size_t i = 0; i < N; ++i)
    out[i] = mux<W>(sel[i], a, b[i]);
  return out;
}

template <typename T, std::size_t VecN, std::size_t PackedN>
inline void appendPackedWireWords(std::array<std::uint64_t, PackedN> &dst, std::size_t &offset,
                                  const Vec<T, VecN> &v) {
  for (std::size_t i = 0; i < VecN; ++i)
    appendPackedWireWords(dst, offset, v[i]);
}

} // namespace pyc::cpp
