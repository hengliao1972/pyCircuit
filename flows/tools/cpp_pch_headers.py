#!/usr/bin/env python3
"""Select device module hpp files for CMake precompiled headers."""

from __future__ import annotations

from pathlib import Path


def select_device_hpp_headers(headers: list[str]) -> list[str]:
    """Return absolute paths of module primary headers suitable for PCH.

    A primary header matches ``<module>/<module>.hpp`` (filename equals parent dir).
    """
    selected: list[str] = []
    seen: set[str] = set()
    for raw in headers:
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        if path.suffix.lower() != ".hpp":
            continue
        if path.stem != path.parent.name:
            continue
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        selected.append(resolved)
    return sorted(selected)
