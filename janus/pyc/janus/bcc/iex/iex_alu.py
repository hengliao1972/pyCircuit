from __future__ import annotations

from janus.bcc.ooo.exec import exec_uop

MODULE = "IEX_ALU"
DESCRIPTION = "ALU lane execution (reuses bring-up exec_uop semantics)"
__all__ = ["exec_uop", "MODULE", "DESCRIPTION"]
