"""In-place lowering of Vector signals into scalar PYC ops.

The Python-facing vector API (Vector / Wire / Module.v_*) is unchanged.  This
module provides the lane registry that pycircuit.dsl.Module uses so that every
vector operation is expanded into per-lane scalar ops while the .pyc text is
emitted (the same expansion that compiler/mlir/lib/Transforms/VectorUnrollPass.cpp
performs, moved into the frontend).  Vector-typed ports are emitted as a single
packed integer bus (i{elem_width * lanes}) with lane 0 in the LSBs, matching the
backend packed-bus convention for vector<NxiW> ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .data import Bits, Data, Vector


@dataclass(frozen=True)
class LaneInfo:
    """Row-major lane registry for one vector-typed SSA tag.

    refs is the flat row-major lane list (same order as MLIR vector and the
    Verilog packed LSB-first convention): lane (i, j) of shape (A, B) sits at
    refs[i * B + j].
    """

    shape: tuple[int, ...]
    leaf: Data
    refs: tuple[str, ...]


def shape_of(ty: Data) -> tuple[int, ...]:
    if isinstance(ty, Vector):
        return tuple(int(d) for d in ty.shape())
    return ()


def leaf_of(ty: Data) -> Data:
    return ty.datatype() if isinstance(ty, Vector) else ty


def lane_count(shape: tuple[int, ...]) -> int:
    n = 1
    for d in shape:
        n *= int(d)
    return n


def flat_index(shape: tuple[int, ...], indices: tuple[int, ...]) -> int:
    flat = 0
    for d, i in zip(shape, indices):
        flat = flat * int(d) + int(i)
    return flat


def iter_indices(shape: tuple[int, ...]) -> Iterator[tuple[int, ...]]:
    """Yield every index tuple of shape in row-major order."""
    if not shape:
        yield ()
        return
    idx = [0] * len(shape)
    while True:
        yield tuple(idx)
        pos = len(shape) - 1
        while pos >= 0:
            idx[pos] += 1
            if idx[pos] < shape[pos]:
                break
            idx[pos] = 0
            pos -= 1
        if pos < 0:
            return


def lane_suffix(shape: tuple[int, ...], indices: tuple[int, ...]) -> str:
    """Canonical lane name suffix: rank 1 [i], rank 2 [i][j]."""
    _ = shape
    return "".join(f"[{int(i)}]" for i in indices)


def port_mlir_type(ty: Data) -> Data:
    """Type used for a packed port/SSA operand: Vector -> one integer bus."""
    if not isinstance(ty, Vector):
        return ty
    return Bits(int(leaf_of(ty).width) * lane_count(shape_of(ty)))
