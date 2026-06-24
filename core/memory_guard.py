#!/usr/bin/env python3
"""
core/memory_guard.py — RAM pressure detection for 8GB Macs.

Gates Ollama / heavy AI work so the trading bot does not starve the OS.
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


def recommended_ollama_model(memory_budget_mb: int = 2560) -> str:
    """Pick model that fits the reserved Ollama RAM budget."""
    ram = total_ram_mb()
    if memory_budget_mb >= 2300 and ram <= 10_240:
        return "qwen2.5:3b"      # ~2GB GQA — best JSON/instruction fit for 2.5GB budget
    if memory_budget_mb >= 1200:
        return "qwen2.5:1.5b"   # ~1GB GQA fallback
    if ram <= 8_192:
        return "qwen2.5:0.5b"
    if ram <= 16_384:
        return "phi3:mini"
    return "llama3"


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


def should_allow_ollama(cfg) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    if not getattr(cfg, "OLLAMA_ENABLED", False):
        return False, "ollama_disabled"
    if not getattr(cfg, "GENERATIVE_THINKING_ENABLED", True):
        return False, "generative_disabled"
    # Reserve OLLAMA_MEMORY_BUDGET_MB for the model; only block when OS+bot would starve
    budget = int(getattr(cfg, "OLLAMA_MEMORY_BUDGET_MB", 2560))
    min_free = int(getattr(cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024))
    avail = available_ram_mb()
    # Hard floor: never call if critically low
    if avail < min_free:
        return False, f"critical_ram_{avail}mb"
    # Soft check: need budget + min_free headroom (approximate)
    if avail < budget + min_free and getattr(cfg, "OLLAMA_UNLOAD_AFTER_CALL", False):
        return False, f"budget_wait_{avail}mb_need_{budget + min_free}mb"
    return True, "ok"


def memory_status(cfg=None) -> dict:
    budget = int(getattr(cfg, "OLLAMA_MEMORY_BUDGET_MB", 2560)) if cfg else 2560
    return {
        "total_ram_mb": total_ram_mb(),
        "available_ram_mb": available_ram_mb(),
        "ollama_budget_mb": budget,
        "low_ram": is_low_ram_machine(),
        "pressured": is_memory_pressured(int(getattr(cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024)) if cfg else 1024),
        "recommended_model": recommended_ollama_model(budget),
    }
