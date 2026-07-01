#!/usr/bin/env python3
"""
core/ppo_deploy_tiers.py — PPO conviction sizing tiers (normal / strong / lottery_bullet).

Lottery is a deploy multiplier from PPO conviction — not a duplicate gate stack.
War ledger still clamps final notional to settled cash.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.config import BotConfig


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def ppo_deploy_tiers_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return _env_bool("PPO_DEPLOY_TIERS_ENABLED", "false")


def classify_deploy_tier(
    cfg: Optional[BotConfig],
    *,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    profit_probability: float = 0.0,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    confidence: float = 0.0,
) -> str:
    """Return deploy_tier: none | normal | strong | lottery_bullet."""
    if int(ppo_action) != 1:
        return "none"
    conf = max(float(ppo_conf or 0), float(confidence or 0))
    prob = float(profit_probability or 0)
    spike = float(spike_ratio or 1.0)
    score = float(scan_score or 0)

    lot_conf = _env_float("PPO_LOTTERY_TIER_MIN_CONF", 0.74)
    lot_prob = _env_float("PPO_LOTTERY_TIER_MIN_PROB", 0.70)
    lot_spike = _env_float("PPO_LOTTERY_TIER_MIN_SPIKE", 1.75)
    lot_score = _env_float("PPO_LOTTERY_TIER_MIN_SCORE", 55)

    strong_conf = _env_float("PPO_STRONG_TIER_MIN_CONF", 0.62)
    strong_prob = _env_float("PPO_STRONG_TIER_MIN_PROB", 0.58)
    strong_spike = _env_float("PPO_STRONG_TIER_MIN_SPIKE", 1.35)

    if (
        conf >= lot_conf
        and (prob <= 0 or prob >= lot_prob)
        and spike >= lot_spike
        and score >= lot_score
    ):
        return "lottery_bullet"
    if conf >= strong_conf and (prob <= 0 or prob >= strong_prob) and spike >= strong_spike:
        return "strong"
    return "normal"


def tier_size_multiplier(tier: str) -> float:
    t = str(tier or "normal").lower()
    if t == "lottery_bullet":
        return _env_float("PPO_LOTTERY_TIER_SIZE_MULT", 2.0)
    if t == "strong":
        return _env_float("PPO_STRONG_TIER_SIZE_MULT", 1.35)
    return 1.0


def apply_deploy_tier_to_decision(
    cfg: Optional[BotConfig],
    decision: Dict[str, Any],
    entry_px: float,
    *,
    ppo_action: int = 0,
    ppo_conf: float = 0.0,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
) -> Dict[str, Any]:
    """Tag decision with deploy_tier and scale shares/deploy_usd (war cap applies later)."""
    if not ppo_deploy_tiers_enabled(cfg) or entry_px <= 0:
        return decision
    prob = float(
        decision.get("profit_probability")
        or decision.get("ollama_profit_probability")
        or 0.0
    )
    conf = float(decision.get("confidence", ppo_conf) or ppo_conf)
    tier = classify_deploy_tier(
        cfg,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        profit_probability=prob,
        spike_ratio=spike_ratio,
        scan_score=scan_score,
        confidence=conf,
    )
    if tier in ("none", "normal"):
        out = dict(decision)
        out["deploy_tier"] = tier if tier != "none" else "normal"
        return out

    mult = tier_size_multiplier(tier)
    out = dict(decision)
    out["deploy_tier"] = tier
    out["deploy_tier_mult"] = round(mult, 3)
    shares = int(out.get("shares") or 0)
    deploy = float(out.get("deploy_usd") or 0)
    if shares > 0:
        out["shares"] = max(1, int(shares * mult))
        out["deploy_usd"] = round(out["shares"] * entry_px, 2)
    elif deploy > 0:
        out["deploy_usd"] = round(deploy * mult, 2)
        out["shares"] = max(1, int(out["deploy_usd"] / entry_px))
    return out
