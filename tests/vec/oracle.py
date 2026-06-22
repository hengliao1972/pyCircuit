from __future__ import annotations


def mask(width: int) -> int:
    return (1 << int(width)) - 1


def u(value: int, width: int) -> int:
    return int(value) & mask(width)


def s(value: int, width: int) -> int:
    value = u(value, width)
    sign = 1 << (int(width) - 1)
    return value - (1 << int(width)) if value & sign else value


def zext(value: int, in_width: int, out_width: int) -> int:
    _ = in_width
    return u(value, out_width)


def sext(value: int, in_width: int, out_width: int) -> int:
    return u(s(value, in_width), out_width)


def trunc(value: int, width: int) -> int:
    return u(value, width)


def lshr(value: int, width: int, amount: int) -> int:
    return u(value, width) >> int(amount)


def ashr(value: int, width: int, amount: int) -> int:
    return u(s(value, width) >> int(amount), width)


def reduce_sum(values: list[int], in_width: int, out_width: int, *, signed: bool = False) -> int:
    if signed:
        total = sum(s(v, in_width) for v in values)
    else:
        total = sum(u(v, in_width) for v in values)
    return u(total, out_width)


def vector_binop(op: str, a: list[int], b: list[int], width: int, *, signed: bool = False) -> list[int]:
    out: list[int] = []
    for av, bv in zip(a, b):
        au = u(av, width)
        bu = u(bv, width)
        if op == "add":
            out.append(u(au + bu, width))
        elif op == "sub":
            out.append(u(au - bu, width))
        elif op == "mul":
            out.append(u(au * bu, width))
        elif op == "and":
            out.append(au & bu)
        elif op == "or":
            out.append(au | bu)
        elif op == "xor":
            out.append(au ^ bu)
        elif op == "eq":
            out.append(1 if au == bu else 0)
        elif op == "lt":
            if signed:
                out.append(1 if s(av, width) < s(bv, width) else 0)
            else:
                out.append(1 if au < bu else 0)
        elif op == "div":
            lhs = s(av, width) if signed else au
            rhs = s(bv, width) if signed else bu
            if rhs == 0:
                raise ZeroDivisionError("oracle div by zero")
            q = int(lhs / rhs) if signed else lhs // rhs
            out.append(u(q, width))
        elif op == "rem":
            lhs = s(av, width) if signed else au
            rhs = s(bv, width) if signed else bu
            if rhs == 0:
                raise ZeroDivisionError("oracle rem by zero")
            r = lhs - int(lhs / rhs) * rhs if signed else lhs % rhs
            out.append(u(r, width))
        else:
            raise ValueError(f"unsupported op: {op}")
    return out
