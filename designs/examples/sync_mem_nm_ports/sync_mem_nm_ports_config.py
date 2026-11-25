from __future__ import annotations

DEFAULT_PARAMS = {
    "depth": 8,
    "data_width": 32,
    "addr_width": 3,
}

TB_PRESETS = {
    "smoke": {"timeout": 64, "finish": 10},
    "nightly": {"timeout": 256, "finish": 12},
}

SIM_TIER = "normal"
