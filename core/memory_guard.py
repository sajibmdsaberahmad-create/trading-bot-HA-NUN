#!/usr/bin/env python3
"""
core/memory_guard.py — RAM pressure detection (PPO / OS headroom).

Cloud council (Groq/Gemini) does not use local RAM — gates are API-key based.
"""

from __future__ import annotations

import os
import logging
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


def _council_on(cfg) -> bool:
    enabled = getattr(cfg, "COUNCIL_ENABLED", False)
    if not enabled:
        return False
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
        return False
    groq = (getattr(cfg, "GROQ_API_KEY", "") or "").strip()
    gemini = (
        getattr(cfg, "GEMINI_API_KEY", "")
        or getattr(cfg, "GOOGLE_API_KEY", "")
        or ""
    ).strip()
    return bool(groq or gemini)


def recommended_council_model(cfg=None) -> str:
    if cfg is None:
        from core.config import BotConfig
        cfg = BotConfig()
    return getattr(cfg, "GROQ_MODEL", "llama-3.3-70b-versatile")


def should_allow_chart_vision(cfg) -> tuple[bool, str]:
    always = getattr(cfg, "LIVE_CHART_VISION_ENABLED", False)
    opportunistic = getattr(cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False)
    if not always and not opportunistic:
        return False, "disabled"
    gemini = (
        getattr(cfg, "GEMINI_API_KEY", "")
        or getattr(cfg, "GOOGLE_API_KEY", "")
        or ""
    ).strip()
    if not gemini:
        return False, "no_gemini_key"
    return True, "ok"




def memory_status(cfg=None) -> dict:
    from core.ram_tier import detect_ram_tier, ram_tier_summary

    tier = getattr(cfg, "RAM_TIER", detect_ram_tier()) if cfg else detect_ram_tier()
    out = {
        "total_ram_mb": total_ram_mb(),
        "available_ram_mb": available_ram_mb(),
        "council_backend": getattr(cfg, "COUNCIL_BACKEND", "groq") if cfg else "groq",
        "council_model": recommended_council_model(cfg) if cfg else "llama-3.3-70b-versatile",
        "ram_tier": tier,
        "ram_tier_label": getattr(cfg, "RAM_TIER_LABEL", "") if cfg else "",
        "low_ram": is_low_ram_machine(),
        "pressured": is_memory_pressured(1024),
        "recommended_model": recommended_council_model(cfg) if cfg else "llama-3.3-70b-versatile",
    }
    if cfg:
        out["tier_profile"] = ram_tier_summary(cfg)
    return out
