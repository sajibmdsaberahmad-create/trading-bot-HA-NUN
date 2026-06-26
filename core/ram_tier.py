#!/usr/bin/env python3
"""
core/ram_tier.py — Auto-tune HANOON for installed RAM.

Cloud council (Groq/Gemini) — no local LLM RAM budget.
Tiers adjust council wait, chart vision, and training intensity.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.memory_guard import total_ram_mb

# Env var names that block auto-tune when explicitly set
_TIER_ENV_KEYS: Dict[str, str] = {
    "GROQ_MODEL": "GROQ_MODEL",
    "GEMINI_MODEL": "GEMINI_MODEL",
    "GEMINI_VISION_MODEL": "GEMINI_VISION_MODEL",
    "COUNCIL_TIMEOUT_SEC": "COUNCIL_TIMEOUT_SEC",
    "COUNCIL_MAX_TOKENS": "COUNCIL_MAX_TOKENS",
    "COUNCIL_MIN_CALL_INTERVAL_SEC": "COUNCIL_MIN_CALL_INTERVAL_SEC",
    "OLLAMA_META_OPTIMIZER_ENABLED": "OLLAMA_META_OPTIMIZER_ENABLED",
    "LIVE_CHART_VISION_ENABLED": "LIVE_CHART_VISION_ENABLED",
    "LIVE_CHART_VISION_MIN_SCORE": "LIVE_CHART_VISION_MIN_SCORE",
    "LIVE_AI_PREFETCH_TOP_N": "LIVE_AI_PREFETCH_TOP_N",
    "LIVE_AI_PREFETCH_SEC": "LIVE_AI_PREFETCH_SEC",
    "LIVE_AI_MAX_AGE_SEC": "LIVE_AI_MAX_AGE_SEC",
    "LIVE_AI_MIN_RING_SEC": "LIVE_AI_MIN_RING_SEC",
    "ENTRY_OLLAMA_WAIT_SEC": "ENTRY_OLLAMA_WAIT_SEC",
    "AI_COUNCIL_MAX_WAIT_SEC": "AI_COUNCIL_MAX_WAIT_SEC",
    "COUNCIL_SCANNER_FAST_SEC": "COUNCIL_SCANNER_FAST_SEC",
    "OFF_HOURS_HEAVY_TRAINING": "OFF_HOURS_HEAVY_TRAINING",
    "TRAINING_MEMORY_LIMIT_MB": "TRAINING_MEMORY_LIMIT_MB",
    "LIVE_CHART_VISION_OPPORTUNISTIC": "LIVE_CHART_VISION_OPPORTUNISTIC",
    "CAPITAL_DISCIPLINE": "CAPITAL_DISCIPLINE",
    "TREAT_PAPER_AS_LIVE": "TREAT_PAPER_AS_LIVE",
    "AI_SPIKE_FAST_ENTRY": "AI_SPIKE_FAST_ENTRY",
    "PPO_LEAD_WHILE_COUNCIL_PENDING": "PPO_LEAD_WHILE_COUNCIL_PENDING",
    "CONFIDENCE_THRESHOLD": "CONFIDENCE_THRESHOLD",
    "MIN_PROFIT_PROBABILITY": "MIN_PROFIT_PROBABILITY",
    "ENTRY_QUALITY_BLEND_WEIGHT": "ENTRY_QUALITY_BLEND_WEIGHT",
    "ENTRY_QUALITY_HARDNESS": "ENTRY_QUALITY_HARDNESS",
    "CAPITAL_MIN_ENTRY_SCAN_SCORE": "CAPITAL_MIN_ENTRY_SCAN_SCORE",
    "CAPITAL_MIN_ENTRY_SPIKE_RATIO": "CAPITAL_MIN_ENTRY_SPIKE_RATIO",
    "CAPITAL_ENTRY_COOLDOWN_SEC": "CAPITAL_ENTRY_COOLDOWN_SEC",
    "MAX_ENTRIES_PER_HOUR": "MAX_ENTRIES_PER_HOUR",
}

TIER_PROFILES: Dict[str, Dict[str, Any]] = {
    "compact": {
        "GROQ_MODEL": "llama-3.1-8b-instant",
        "COUNCIL_MAX_TOKENS": 256,
        "COUNCIL_TIMEOUT_SEC": 10,
        "COUNCIL_MIN_CALL_INTERVAL_SEC": 0.8,
        "OLLAMA_META_OPTIMIZER_ENABLED": False,
        "CAPITAL_DISCIPLINE": True,
        "TREAT_PAPER_AS_LIVE": True,
        "AI_SPIKE_FAST_ENTRY": False,
        "PPO_LEAD_WHILE_COUNCIL_PENDING": False,
        "CONFIDENCE_THRESHOLD": 0.65,
        "MIN_PROFIT_PROBABILITY": 0.62,
        "ENTRY_QUALITY_BLEND_WEIGHT": 0.55,
        "ENTRY_QUALITY_HARDNESS": 0.45,
        "CAPITAL_MIN_ENTRY_SCAN_SCORE": 55,
        "CAPITAL_MIN_ENTRY_SPIKE_RATIO": 1.25,
        "CAPITAL_STRONG_SPIKE_FAST": True,
        "CAPITAL_PPO_LEAD_STRONG_SPIKE": True,
        "CAPITAL_STRONG_SPIKE_SCORE": 78,
        "CAPITAL_STRONG_SPIKE_RATIO": 1.35,
        "CAPITAL_STRONG_PROFIT_PROB_FLOOR": 0.48,
        "CAPITAL_STRONG_MIN_PPO_CONF": 0.50,
        "SPIKE_FAST_REQUIRES_QUALITY": True,
        "CAPITAL_ENTRY_COOLDOWN_SEC": 0,
        "MAX_ENTRIES_PER_HOUR": 0,
        "AI_PROFIT_FULL_POWER": True,
        "PROFIT_HUNT_MECHANICAL_BYPASS_COUNCIL": False,
        "LIVE_CHART_VISION_ENABLED": False,
        "LIVE_CHART_VISION_MIN_SCORE": 80.0,
        "LIVE_AI_PREFETCH_TOP_N": 0,
        "LIVE_AI_PREFETCH_SEC": 5.0,
        "LIVE_AI_PREFETCH_ENABLED": False,
        "LIVE_AI_MAX_AGE_SEC": 6.0,
        "LIVE_AI_MIN_RING_SEC": 3.0,
        "COUNCIL_NANNY_MODE": True,
        "COUNCIL_LEARNING_RING_ENABLED": False,
        "COUNCIL_NANNY_MIN_RING_SEC": 3.0,
        "COUNCIL_NANNY_RESERVE_PCT": 0.25,
        "ENTRY_OLLAMA_WAIT_SEC": 8.0,
        "AI_COUNCIL_MAX_WAIT_SEC": 10.0,
        "COUNCIL_SCANNER_FAST_SEC": 4.0,
        "OFF_HOURS_HEAVY_TRAINING": False,
        "TRAINING_MEMORY_LIMIT_MB": 2048,
        "GEMINI_VISION_MODEL": "gemini-2.5-flash",
        "LIVE_CHART_VISION_OPPORTUNISTIC": True,
        "LIVE_CHART_VISION_MIN_SCORE": 92.0,
    },
    "balanced": {
        "GROQ_MODEL": "llama-3.3-70b-versatile",
        "COUNCIL_MAX_TOKENS": 320,
        "COUNCIL_TIMEOUT_SEC": 12,
        "OLLAMA_META_OPTIMIZER_ENABLED": False,
        "LIVE_CHART_VISION_ENABLED": False,
        "LIVE_CHART_VISION_MIN_SCORE": 72.0,
        "LIVE_AI_PREFETCH_TOP_N": 3,
        "LIVE_AI_PREFETCH_SEC": 1.2,
        "LIVE_AI_MAX_AGE_SEC": 5.0,
        "LIVE_AI_MIN_RING_SEC": 1.0,
        "ENTRY_OLLAMA_WAIT_SEC": 8.0,
        "AI_COUNCIL_MAX_WAIT_SEC": 10.0,
        "COUNCIL_SCANNER_FAST_SEC": 6.0,
        "OFF_HOURS_HEAVY_TRAINING": True,
        "TRAINING_MEMORY_LIMIT_MB": 3072,
        "GEMINI_VISION_MODEL": "gemini-2.5-flash",
        "LIVE_CHART_VISION_OPPORTUNISTIC": True,
        "LIVE_CHART_VISION_MIN_SCORE": 78.0,
    },
    "standard": {
        "GROQ_MODEL": "llama-3.3-70b-versatile",
        "COUNCIL_MAX_TOKENS": 384,
        "COUNCIL_TIMEOUT_SEC": 12,
        "OLLAMA_META_OPTIMIZER_ENABLED": True,
        "LIVE_CHART_VISION_ENABLED": True,
        "LIVE_CHART_VISION_MIN_SCORE": 65.0,
        "LIVE_AI_PREFETCH_TOP_N": 4,
        "LIVE_AI_PREFETCH_SEC": 1.0,
        "LIVE_AI_MAX_AGE_SEC": 4.0,
        "LIVE_AI_MIN_RING_SEC": 0.8,
        "ENTRY_OLLAMA_WAIT_SEC": 10.0,
        "AI_COUNCIL_MAX_WAIT_SEC": 12.0,
        "COUNCIL_SCANNER_FAST_SEC": 8.0,
        "OFF_HOURS_HEAVY_TRAINING": True,
        "TRAINING_MEMORY_LIMIT_MB": 4096,
        "GEMINI_VISION_MODEL": "gemini-2.5-flash",
        "LIVE_CHART_VISION_OPPORTUNISTIC": False,
    },
    "performance": {
        "GROQ_MODEL": "llama-3.3-70b-versatile",
        "COUNCIL_MAX_TOKENS": 384,
        "COUNCIL_TIMEOUT_SEC": 15,
        "OLLAMA_META_OPTIMIZER_ENABLED": True,
        "LIVE_CHART_VISION_ENABLED": True,
        "LIVE_CHART_VISION_MIN_SCORE": 60.0,
        "LIVE_AI_PREFETCH_TOP_N": 5,
        "LIVE_AI_PREFETCH_SEC": 0.8,
        "LIVE_AI_MAX_AGE_SEC": 4.0,
        "LIVE_AI_MIN_RING_SEC": 0.8,
        "ENTRY_OLLAMA_WAIT_SEC": 12.0,
        "AI_COUNCIL_MAX_WAIT_SEC": 15.0,
        "COUNCIL_SCANNER_FAST_SEC": 10.0,
        "OFF_HOURS_HEAVY_TRAINING": True,
        "TRAINING_MEMORY_LIMIT_MB": 6144,
        "GEMINI_VISION_MODEL": "gemini-2.5-flash",
        "LIVE_CHART_VISION_OPPORTUNISTIC": False,
    },
}

TIER_LABELS = {
    "compact": "≤8GB compact",
    "balanced": "≤12GB balanced",
    "standard": "≤24GB standard",
    "performance": ">24GB performance",
}


def detect_ram_tier(total_mb: Optional[int] = None) -> str:
    ram = int(total_mb if total_mb is not None else total_ram_mb())
    if ram <= 8192:
        return "compact"
    if ram <= 12288:
        return "balanced"
    if ram <= 24576:
        return "standard"
    return "performance"


def _env_overrides_key(attr: str) -> bool:
    env_name = _TIER_ENV_KEYS.get(attr, attr)
    return os.getenv(env_name) is not None


def apply_ram_tier_to_config(cfg) -> str:
    """
    Apply tier profile to cfg. Skips keys the user set in .env.
    Returns tier name.
    """
    forced = (getattr(cfg, "RAM_TIER_FORCE", "") or os.getenv("RAM_TIER_FORCE", "") or "").strip().lower()
    if forced in TIER_PROFILES:
        tier = forced
    else:
        tier = detect_ram_tier()

    auto = getattr(cfg, "RAM_AUTO_TUNE", True)
    if os.getenv("RAM_AUTO_TUNE", "").lower() in ("0", "false", "no"):
        auto = False

    if not auto:
        cfg.RAM_TIER = tier
        cfg.RAM_TIER_LABEL = TIER_LABELS.get(tier, tier)
        return tier

    profile = TIER_PROFILES[tier]
    applied = []
    for attr, value in profile.items():
        if _env_overrides_key(attr):
            continue
        setattr(cfg, attr, value)
        applied.append(attr)

    cfg.RAM_TIER = tier
    cfg.RAM_TIER_LABEL = TIER_LABELS.get(tier, tier)
    cfg._ram_tier_applied = applied  # noqa: SLF001 — debug
    if not os.getenv("META_OPTIMIZER_MODEL"):
        cfg.META_OPTIMIZER_MODEL = getattr(cfg, "GROQ_MODEL", profile.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    return tier


def ram_tier_summary(cfg) -> Dict[str, Any]:
    tier = getattr(cfg, "RAM_TIER", detect_ram_tier())
    return {
        "tier": tier,
        "label": TIER_LABELS.get(tier, tier),
        "total_ram_mb": total_ram_mb(),
        "auto_tune": getattr(cfg, "RAM_AUTO_TUNE", True),
        "ollama_model": getattr(cfg, "GROQ_MODEL", "?"),
        "council_backend": getattr(cfg, "COUNCIL_BACKEND", "groq"),
        "chart_vision": getattr(cfg, "LIVE_CHART_VISION_ENABLED", False)
        or getattr(cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False),
        "vision_model": getattr(cfg, "GEMINI_VISION_MODEL", "?"),
        "vision_opportunistic": getattr(cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False),
        "heavy_training": getattr(cfg, "OFF_HOURS_HEAVY_TRAINING", False),
        "council_wait_sec": getattr(cfg, "AI_COUNCIL_MAX_WAIT_SEC", 15),
        "prefetch_top_n": getattr(cfg, "LIVE_AI_PREFETCH_TOP_N", 3),
    }
