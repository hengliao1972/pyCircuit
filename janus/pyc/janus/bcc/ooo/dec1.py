from __future__ import annotations

from pycircuit import Circuit, Wire

from janus.bcc.decode import DecodeBundle, decode_bundle_8B


def decode_f4_bundle(m: Circuit, f4_window: Wire) -> DecodeBundle:
    """D1 decode wrapper for F4 bundles (64b fetch window)."""

    return decode_bundle_8B(m, f4_window)
