#!/usr/bin/env python3
"""
core/ollama_models.py — Text LLM selection for RAM tiers (8GB Mac friendly).

Picks the best installed Ollama text model that fits free RAM. For HANOON council
(JSON decisions + market context), Qwen 2.5 3B is preferred on 8GB when it fits;
smaller models are used only under RAM pressure.
"""

from __future__ import annotations

import os
from typing import Optional

from core.config import BotConfig
from core.notify import log

# Approximate resident size after load (MB) — used for fit checks, not exact.
MODEL_EST_MB: dict[str, int] = {
    "qwen2.5:0.5b": 450,
    "qwen2.5:1.5b": 1100,
    "qwen2.5:3b": 2000,
    "phi3:mini": 2100,
    "phi4-mini": 2600,
    "phi4:mini": 2600,
    "gemma3:4b": 3000,
    "gemma3:4b-it-qat": 2800,
    "llama3": 4700,
    "llama3:8b": 4700,
}

TEXT_MODEL_BY_TIER: dict[str, str] = {
    "compact": "qwen2.5:3b",
    "balanced": "qwen2.5:3b",
    "standard": "qwen2.5:3b",
    "performance": "llama3",
}

# Council quality first on 8GB; lighter models only when RAM is tight.
TEXT_FALLBACK_CHAIN: tuple[str, ...] = (
    "qwen2.5:3b",
    "phi4-mini",
    "phi3:mini",
    "gemma3:4b",
    "qwen2.5:1.5b",
    "qwen2.5:0.5b",
)

PRESSURE_FALLBACK = "qwen2.5:0.5b"
PRESSURE_FALLBACK_MINIMAL = "qwen2.5:0.5b"

# Models that should not be default text LLMs on compact RAM.
HEAVY_TEXT_WARNINGS: tuple[str, ...] = ("llava", "llama3", "mistral", "mixtral")


def _list_models(cfg: BotConfig) -> list[str]:
    from core.ollama_vision import _list_models as _vision_list

    return _vision_list(cfg)


def is_text_model_present(cfg: BotConfig, model: str) -> bool:
    target = (model or "").strip()
    if not target:
        return False
    base = target.split(":")[0]
    installed = _list_models(cfg)
    return any(
        n == target or n == base or n.startswith(f"{base}:")
        for n in installed
    )


def _model_est_mb(name: str) -> int:
    key = (name or "").strip().lower()
    if key in MODEL_EST_MB:
        return MODEL_EST_MB[key]
    base = key.split(":")[0]
    return MODEL_EST_MB.get(base, 2200)


def _fits_ram(cfg: BotConfig, model: str, available_mb: Optional[int] = None) -> bool:
    from core.memory_guard import available_ram_mb

    avail = available_mb if available_mb is not None else available_ram_mb()
    budget = int(getattr(cfg, "OLLAMA_MEMORY_BUDGET_MB", 2048) or 2048)
    reserve = int(getattr(cfg, "OLLAMA_OS_RESERVE_MB", 1500) or 1500)
    est = _model_est_mb(model)
    headroom = max(512, avail - reserve)
    return est <= min(budget, headroom)


def installed_model_tag(cfg: BotConfig, model: str) -> Optional[str]:
    """Exact Ollama tag for API calls (e.g. llava → llava:latest)."""
    target = (model or "").strip()
    if not target:
        return None
    installed = _list_models(cfg)
    full_tags = [n for n in installed if ":" in n]
    if target in full_tags:
        return target
    base = target.split(":")[0]
    for tag in full_tags:
        if tag.split(":")[0] == base:
            return tag
    if target in installed:
        return target
    return None


def ensure_text_model(cfg: BotConfig) -> str:
    """Return an installed text model tag — never a missing name (avoids HTTP 404)."""
    dynamic = getattr(cfg, "OLLAMA_DYNAMIC_MODEL", True)
    if os.getenv("OLLAMA_DYNAMIC_MODEL", "").lower() in ("0", "false", "no"):
        dynamic = False
    raw = active_text_model(cfg) if dynamic else (getattr(cfg, "OLLAMA_MODEL", "") or "")
    tag = installed_model_tag(cfg, raw)
    if tag:
        if tag != getattr(cfg, "OLLAMA_MODEL", ""):
            cfg.OLLAMA_MODEL = tag
        return tag
    for candidate in TEXT_FALLBACK_CHAIN:
        tag = installed_model_tag(cfg, candidate)
        if tag:
            log.warning(
                f"🧠 Ollama model {raw or '?'} not installed — using {tag} "
                f"(run: ollama pull {candidate})"
            )
            cfg.OLLAMA_MODEL = tag
            return tag
    return raw or "qwen2.5:3b"


def resolve_text_model(cfg: BotConfig, *, available_mb: Optional[int] = None) -> str:
    """
    Env override → tier default → first installed model in fallback chain that fits RAM.
    """
    explicit = (getattr(cfg, "OLLAMA_MODEL", "") or os.getenv("OLLAMA_MODEL", "") or "").strip()
    dynamic = getattr(cfg, "OLLAMA_DYNAMIC_MODEL", True)
    if os.getenv("OLLAMA_DYNAMIC_MODEL", "").lower() in ("0", "false", "no"):
        dynamic = False

    if explicit and not dynamic:
        return explicit

    from core.ram_tier import detect_ram_tier

    tier = getattr(cfg, "RAM_TIER", "") or detect_ram_tier()
    tier_model = TEXT_MODEL_BY_TIER.get(tier, "qwen2.5:1.5b")

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if tier_model not in candidates:
        candidates.append(tier_model)
    for c in TEXT_FALLBACK_CHAIN:
        if c not in candidates:
            candidates.append(c)

    for candidate in candidates:
        if is_text_model_present(cfg, candidate) and _fits_ram(cfg, candidate, available_mb):
            return candidate

    for candidate in TEXT_FALLBACK_CHAIN:
        if is_text_model_present(cfg, candidate):
            tag = installed_model_tag(cfg, candidate)
            return tag or candidate

    tag = installed_model_tag(cfg, explicit or tier_model)
    return tag or explicit or tier_model


def active_text_model(cfg: BotConfig) -> str:
    """Runtime model — may downgrade under memory pressure."""
    from core.memory_guard import available_ram_mb, is_memory_pressured

    avail = available_ram_mb()
    pressure_mb = int(getattr(cfg, "OLLAMA_PRESSURE_FREE_MB", 1800) or 1800)
    severe_mb = int(getattr(cfg, "OLLAMA_SEVERE_PRESSURE_FREE_MB", 1200) or 1200)
    if is_memory_pressured(severe_mb) and is_text_model_present(cfg, PRESSURE_FALLBACK_MINIMAL):
        return PRESSURE_FALLBACK_MINIMAL
    if is_memory_pressured(pressure_mb) and is_text_model_present(cfg, PRESSURE_FALLBACK):
        return PRESSURE_FALLBACK
    return resolve_text_model(cfg, available_mb=avail)


def sync_text_model(cfg: BotConfig) -> str:
    """Write resolved model back to cfg when dynamic selection is enabled."""
    dynamic = getattr(cfg, "OLLAMA_DYNAMIC_MODEL", True)
    if os.getenv("OLLAMA_DYNAMIC_MODEL", "").lower() in ("0", "false", "no"):
        dynamic = False
    if not dynamic:
        return ensure_text_model(cfg)
    chosen = ensure_text_model(cfg)
    prev = getattr(cfg, "OLLAMA_MODEL", "")
    if chosen != prev:
        log.info(f"🧠 Ollama text model: {prev or '?'} → {chosen}")
    cfg.OLLAMA_MODEL = chosen
    return chosen


def text_model_startup_warnings(cfg: BotConfig) -> list[str]:
    """Actionable warnings for mis-sized local models."""
    warnings: list[str] = []
    from core.ram_tier import detect_ram_tier

    tier = getattr(cfg, "RAM_TIER", "") or detect_ram_tier()
    installed = _list_models(cfg)
    resolved = resolve_text_model(cfg)

    if tier == "compact":
        if any("llava:latest" in n or n == "llava" for n in installed):
            warnings.append(
                "llava:latest (~4.7GB) is too heavy for 8GB — run: "
                "ollama pull llava-phi3:3.8b && ollama rm llava:latest"
            )
        if "llama3" in (getattr(cfg, "OLLAMA_MODEL", "") or "").lower():
            warnings.append("llama3 is too heavy for 8GB — using dynamic fallback instead")

    for heavy in HEAVY_TEXT_WARNINGS:
        if tier == "compact" and heavy in resolved.lower() and heavy != "phi3":
            warnings.append(f"Text model {resolved} may swap on 8GB — prefer phi3:mini or qwen2.5:1.5b")

    if tier == "compact" and not is_text_model_present(cfg, resolved):
        tag = installed_model_tag(cfg, resolved)
        if not tag:
            warnings.append(
                f"Text model {resolved} not installed (Ollama 404) — run: "
                "ollama pull qwen2.5:3b"
            )

    return warnings


def recommended_pulls_for_tier(tier: str) -> list[str]:
    if tier == "compact":
        return ["phi4-mini", "phi3:mini", "qwen2.5:1.5b", "qwen2.5:0.5b", "llava-phi3:3.8b"]
    if tier == "balanced":
        return ["qwen2.5:3b", "llava:7b-v1.6-mistral-q4_K_M"]
    return ["qwen2.5:3b", "llama3"]
