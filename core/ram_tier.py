#!/usr/bin/env python3
"""
core/ram_tier.py — Auto-tune HANOON for installed RAM.

Tiers (by total physical RAM):
  compact     ≤ 8 GB   — fast council, small Ollama, no live llava, no heavy training
  balanced    ≤ 12 GB  — qwen 3b, light off-hours training
  standard    ≤ 24 GB  — live chart vision, full council wait, 4 prefetch
  performance  > 24 GB  — llama3, llava, heavy training, meta-optimizer

Set RAM_AUTO_TUNE=false to disable. Per-key .env overrides always win.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.memory_guard import total_ram_mb

# Env var names that block auto-tune when explicitly set
_TIER_ENV_KEYS: Dict[str, str] = {
    "OLLAMA_MODEL": "OLLAMA_MODEL",
    "OLLAMA_MEMORY_BUDGET_MB": "OLLAMA_MEMORY_BUDGET_MB",
    "OLLAMA_TIMEOUT": "OLLAMA_TIMEOUT",
    "OLLAMA_MAX_TOKENS": "OLLAMA_MAX_TOKENS",
    "OLLAMA_NUM_CTX": "OLLAMA_NUM_CTX",
    "OLLAMA_KEEP_ALIVE": "OLLAMA_KEEP_ALIVE",
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
    "OLLAMA_VISION_MODEL": "OLLAMA_VISION_MODEL",
    "LIVE_CHART_VISION_OPPORTUNISTIC": "LIVE_CHART_VISION_OPPORTUNISTIC",
    "OLLAMA_VISION_UNLOAD_AFTER_CALL": "OLLAMA_VISION_UNLOAD_AFTER_CALL",
    "OLLAMA_VISION_SWAP_TEXT_MODEL": "OLLAMA_VISION_SWAP_TEXT_MODEL",
}

TIER_PROFILES: Dict[str, Dict[str, Any]] = {
    "compact": {
        "OLLAMA_MODEL": "qwen2.5:3b",
        "OLLAMA_DYNAMIC_MODEL": True,
        "OLLAMA_MEMORY_BUDGET_MB": 2560,
        "OLLAMA_TIMEOUT": 12,
        "OLLAMA_MAX_TOKENS": 192,
        "OLLAMA_NUM_CTX": 1536,
        "OLLAMA_KEEP_ALIVE": 300,
        "OLLAMA_META_OPTIMIZER_ENABLED": False,
        "LIVE_CHART_VISION_ENABLED": False,
        "LIVE_CHART_VISION_MIN_SCORE": 80.0,
        "LIVE_AI_PREFETCH_TOP_N": 1,
        "LIVE_AI_PREFETCH_SEC": 1.5,
        "LIVE_AI_MAX_AGE_SEC": 6.0,
        "LIVE_AI_MIN_RING_SEC": 1.2,
        "ENTRY_OLLAMA_WAIT_SEC": 5.0,
        "AI_COUNCIL_MAX_WAIT_SEC": 6.0,
        "COUNCIL_SCANNER_FAST_SEC": 4.0,
        "OFF_HOURS_HEAVY_TRAINING": False,
        "TRAINING_MEMORY_LIMIT_MB": 2048,
        "OLLAMA_VISION_MODEL": "llava-phi3:3.8b",
        "LIVE_CHART_VISION_OPPORTUNISTIC": True,
        "LIVE_CHART_VISION_MIN_SCORE": 92.0,
        "OLLAMA_VISION_UNLOAD_AFTER_CALL": True,
        "OLLAMA_VISION_SWAP_TEXT_MODEL": False,
    },
    "balanced": {
        "OLLAMA_MODEL": "qwen2.5:3b",
        "OLLAMA_MEMORY_BUDGET_MB": 2560,
        "OLLAMA_TIMEOUT": 15,
        "OLLAMA_MAX_TOKENS": 256,
        "OLLAMA_NUM_CTX": 2048,
        "OLLAMA_KEEP_ALIVE": 450,
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
        "OLLAMA_VISION_MODEL": "llava:7b-v1.6-mistral-q4_K_M",
        "LIVE_CHART_VISION_OPPORTUNISTIC": True,
        "LIVE_CHART_VISION_MIN_SCORE": 78.0,
        "OLLAMA_VISION_UNLOAD_AFTER_CALL": True,
        "OLLAMA_VISION_SWAP_TEXT_MODEL": True,
    },
    "standard": {
        "OLLAMA_MODEL": "qwen2.5:3b",
        "OLLAMA_MEMORY_BUDGET_MB": 3072,
        "OLLAMA_TIMEOUT": 18,
        "OLLAMA_MAX_TOKENS": 320,
        "OLLAMA_NUM_CTX": 3072,
        "OLLAMA_KEEP_ALIVE": 600,
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
        "OLLAMA_VISION_MODEL": "llava:7b-v1.6-mistral-q4_K_M",
        "LIVE_CHART_VISION_OPPORTUNISTIC": False,
        "OLLAMA_VISION_UNLOAD_AFTER_CALL": False,
        "OLLAMA_VISION_SWAP_TEXT_MODEL": False,
    },
    "performance": {
        "OLLAMA_MODEL": "llama3",
        "OLLAMA_MEMORY_BUDGET_MB": 4096,
        "OLLAMA_TIMEOUT": 20,
        "OLLAMA_MAX_TOKENS": 384,
        "OLLAMA_NUM_CTX": 4096,
        "OLLAMA_KEEP_ALIVE": 600,
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
        "OLLAMA_VISION_MODEL": "llava:13b-v1.6-vicuna-q4_K_M",
        "LIVE_CHART_VISION_OPPORTUNISTIC": False,
        "OLLAMA_VISION_UNLOAD_AFTER_CALL": False,
        "OLLAMA_VISION_SWAP_TEXT_MODEL": False,
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
        cfg.META_OPTIMIZER_MODEL = getattr(cfg, "OLLAMA_MODEL", profile.get("OLLAMA_MODEL", "qwen2.5:3b"))
    if getattr(cfg, "OLLAMA_DYNAMIC_MODEL", True) and not _env_overrides_key("OLLAMA_MODEL"):
        try:
            from core.ollama_models import sync_text_model

            sync_text_model(cfg)
        except Exception:
            pass
    return tier


def ram_tier_summary(cfg) -> Dict[str, Any]:
    tier = getattr(cfg, "RAM_TIER", detect_ram_tier())
    return {
        "tier": tier,
        "label": TIER_LABELS.get(tier, tier),
        "total_ram_mb": total_ram_mb(),
        "auto_tune": getattr(cfg, "RAM_AUTO_TUNE", True),
        "ollama_model": getattr(cfg, "OLLAMA_MODEL", "?"),
        "chart_vision": getattr(cfg, "LIVE_CHART_VISION_ENABLED", False)
        or getattr(cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False),
        "vision_model": getattr(cfg, "OLLAMA_VISION_MODEL", "?"),
        "vision_opportunistic": getattr(cfg, "LIVE_CHART_VISION_OPPORTUNISTIC", False),
        "heavy_training": getattr(cfg, "OFF_HOURS_HEAVY_TRAINING", False),
        "council_wait_sec": getattr(cfg, "AI_COUNCIL_MAX_WAIT_SEC", 15),
        "prefetch_top_n": getattr(cfg, "LIVE_AI_PREFETCH_TOP_N", 3),
    }
