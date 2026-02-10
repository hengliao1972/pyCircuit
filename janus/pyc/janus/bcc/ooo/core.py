from __future__ import annotations

"""
Janus BCC OOO core (pyCircuit).

This module exists as the stable import path for the OOO core builder:

  from janus.bcc.ooo.core import build_bcc_ooo

The implementation currently lives in `linxcore.py` (historical name).
"""

from .linxcore import BccOooExports, build_bcc_ooo

__all__ = ["BccOooExports", "build_bcc_ooo"]
