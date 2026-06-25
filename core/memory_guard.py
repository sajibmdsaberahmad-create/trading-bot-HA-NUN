#!/usr/bin/env python3
"""
core/memory_guard.py — RAM pressure detection for 8GB Macs.

Gates Ollama / heavy AI work so the trading bot does not starve the OS.
"""

from __future__ import annotations

import os
import logging
import shutil
from typing import Optional

logger = logging.getLogger("MEMORY_GUARD")

_cached_total_mb: Optional[int] = None


def total_ram_mb() -> int:
    global _cached_total_mb
    if _cached_total_mb is not None:
        return _cached_total_mb
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        _cached_total_mb = int(pages * page_size // (1024 * 1024))
    except Exception:
        _cached_total_mb = 8192
    return _cached_total_mb


def available_ram_mb() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().available // (1024 * 1024))
    except Exception:
        return max(512, total_ram_mb() // 2)


def is_low_ram_machine(threshold_mb: int = 10_240) -> bool:
    return total_ram_mb() <= threshold_mb


def is_memory_pressured(min_free_mb: int = 2048) -> bool:
    return available_ram_mb() < min_free_mb


def recommended_ollama_model(memory_budget_mb: int = 2560) -> str:
    """Pick model that fits the reserved Ollama RAM budget."""
    from core.ram_tier import TIER_PROFILES, detect_ram_tier

    tier = detect_ram_tier()
    return str(TIER_PROFILES.get(tier, {}).get("OLLAMA_MODEL", "qwen2.5:1.5b"))


def should_allow_ollama_decide(cfg) -> tuple[bool, str]:
    """Relaxed RAM gate for live entry/exit/position decisions (model kept warm)."""
    if not getattr(cfg, "OLLAMA_ENABLED", False):
        return False, "ollama_disabled"
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
        return False, "generative_disabled"
    min_free = int(getattr(cfg, "OLLAMA_DECISION_MIN_FREE_RAM_MB", 768))
    avail = available_ram_mb()
    if avail < min_free:
        return False, f"decision_ram_{avail}mb"
    return True, "ok"


def should_allow_ollama_notify(cfg) -> tuple[bool, str]:
    """Relaxed RAM gate for short Telegram compose calls (model often already loaded)."""
    if not getattr(cfg, "OLLAMA_ENABLED", False):
        return False, "ollama_disabled"
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
        return False, "generative_disabled"
    min_free = int(getattr(cfg, "OLLAMA_NOTIFY_MIN_FREE_RAM_MB", 512))
    avail = available_ram_mb()
    if avail < min_free:
        return False, f"notify_ram_{avail}mb"
    return True, "ok"


def should_allow_chart_vision(cfg) -> tuple[bool, str]:
    """Quantized llava — always if enabled; opportunistic on 8GB when RAM + score OK."""
    always = getattr(cfg, "LIVE_CHART_VISION_ENABLED", False)
    opportunistic = getattr(cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False)
    if not always and not opportunistic:
        return False, "disabled"
    if not getattr(cfg, "OLLAMA_ENABLED", False):
        return False, "ollama_disabled"
    avail = available_ram_mb()
    min_free = int(getattr(cfg, "LIVE_CHART_VISION_MIN_FREE_RAM_MB", 1300))
    if avail < min_free:
        return False, f"need_{min_free}mb_have_{avail}mb"
    return True, "ok"


def unload_heavy_ollama_models() -> None:
    """Free RAM — stop vision + oversized text models (disk copies remain)."""
    if not shutil.which("ollama"):
        return
    import subprocess

    for model in (
        "llava", "llava:7b", "llama3", "llama3.2:3b", "qwen2.5:3b",
        "phi3:mini", "moondream",
    ):
        try:
            subprocess.run(
                ["ollama", "stop", model],
                capture_output=True,
                timeout=8,
            )
        except Exception:
            pass


def should_allow_ollama(cfg) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    if not getattr(cfg, "OLLAMA_ENABLED", False):
        return False, "ollama_disabled"
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
        return False, "generative_disabled"
    budget = int(getattr(cfg, "OLLAMA_MEMORY_BUDGET_MB", 2560))
    min_free = int(getattr(cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024))
    avail = available_ram_mb()
    if avail < min_free:
        return False, f"critical_ram_{avail}mb"
    if avail < budget + min_free and getattr(cfg, "OLLAMA_UNLOAD_AFTER_CALL", False):
        return False, f"budget_wait_{avail}mb_need_{budget + min_free}mb"
    return True, "ok"


def memory_status(cfg=None) -> dict:
    budget = int(getattr(cfg, "OLLAMA_MEMORY_BUDGET_MB", 2560)) if cfg else 2560
    from core.ram_tier import detect_ram_tier, ram_tier_summary

    tier = getattr(cfg, "RAM_TIER", detect_ram_tier()) if cfg else detect_ram_tier()
    out = {
        "total_ram_mb": total_ram_mb(),
        "available_ram_mb": available_ram_mb(),
        "ollama_budget_mb": budget,
        "ram_tier": tier,
        "ram_tier_label": getattr(cfg, "RAM_TIER_LABEL", "") if cfg else "",
        "low_ram": is_low_ram_machine(),
        "pressured": is_memory_pressured(int(getattr(cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024)) if cfg else 1024),
        "recommended_model": recommended_ollama_model(budget),
    }
    if cfg:
        out["tier_profile"] = ram_tier_summary(cfg)
    return out
