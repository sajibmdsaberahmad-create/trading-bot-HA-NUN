"""Device profiles — Halim runs featherweight → powerful by tier."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

PROFILES: Dict[str, Dict[str, Any]] = {
    "minimal": {
        "ram_gb_max": 4,
        "reflex_only": True,
        "lm_enabled": False,
        "lm_max_params_b": 0,
        "quant": None,
        "description": "Reflex students only — any low-RAM host",
    },
    "m2_8gb": {
        "ram_gb_max": 10,
        "reflex_only": True,
        "lm_enabled": False,
        "lm_max_params_b": 0,
        "quant": "4bit",
        "description": "PPO + proxy + dataset collection (current Mac)",
    },
    "m2_16gb": {
        "ram_gb_max": 20,
        "reflex_only": False,
        "lm_enabled": True,
        "lm_max_params_b": 3,
        "quant": "4bit",
        "description": "Local 1–3B Halim LM via MLX/llama.cpp",
    },
    "m2_32gb_plus": {
        "ram_gb_max": 64,
        "reflex_only": False,
        "lm_enabled": True,
        "lm_max_params_b": 8,
        "quant": "8bit",
        "description": "7–8B Halim LM on-device",
    },
    "gpu_cloud": {
        "ram_gb_max": 999,
        "reflex_only": False,
        "lm_enabled": True,
        "lm_max_params_b": 70,
        "quant": "bf16",
        "description": "Episodic train/eval — weights synced to git",
    },
}


def detect_profile() -> str:
    explicit = os.getenv("HALIM_DEVICE") or os.getenv("OWNED_BRAIN_DEVICE", "")
    if explicit in PROFILES:
        return explicit
    try:
        import psutil
        gb = psutil.virtual_memory().total / (1024 ** 3)
        if gb <= 10:
            return "m2_8gb"
        if gb <= 20:
            return "m2_16gb"
        return "m2_32gb_plus"
    except Exception:
        return "m2_8gb"


def profile_spec(name: Optional[str] = None) -> Dict[str, Any]:
    name = name or detect_profile()
    spec = dict(PROFILES.get(name, PROFILES["m2_8gb"]))
    spec["profile"] = name
    return spec
