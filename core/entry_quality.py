#!/usr/bin/env python3
"""
core/entry_quality.py — Profit probability + fakeout read before any spike entry.

Heavy on micro + PPO context; Ollama council still deliberates async for learning.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.config import BotConfig


def assess_entry_quality(
    cfg: BotConfig,
    micro: Optional[Dict[str, Any]],
    *,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    live_px: float = 0.0,
) -> Dict[str, Any]:
    """
    Estimate odds of a profitable long scalp and classify setup type.

    Returns profit_probability, fakeout_risk, setup_type, enter_ok, reason.
    """
    micro = micro or {}
    sl = float(micro.get("spike_likelihood", 0))
    fade = float(micro.get("fade_risk", 0))
    loss_p = float(micro.get("loss_pressure", 0))
    profit_run = float(micro.get("profit_run", 0))
    mom = float(micro.get("momentum", 0))
    va = float(micro.get("vol_accel", 1.0))
    pred_1 = float(micro.get("pred_1bar", live_px or 0))
    breakout = bool(micro.get("breakout", False))

    score_norm = min(max(scan_score / 100.0, 0.0), 1.0)
    spike_norm = min(max((spike_ratio - 1.0) / 1.5, 0.0), 1.0)
    ppo_up = ppo_conf if ppo_action == 1 else max(0.0, ppo_conf - 0.15)

    pred_up = 0.0
    if live_px > 0 and pred_1 > live_px:
        pred_up = min((pred_1 / live_px - 1.0) * 50.0, 0.35)

    profit_probability = (
        0.22 * profit_run
        + 0.18 * sl
        + 0.16 * score_norm
        + 0.14 * ppo_up
        + 0.12 * spike_norm
        + 0.10 * max(0.0, mom)
        + pred_up
        - 0.28 * fade
        - 0.22 * loss_p
    )
    profit_probability = float(max(0.0, min(1.0, profit_probability)))

    fakeout_risk = float(max(0.0, min(1.0, fade * 0.55 + sl * 0.25 + (0.2 if va > 1.8 and mom < 0.05 else 0.0))))

    # Fakeout fade-play: volume spike exhausting, price extended, micro projects bounce
    fakeout_fade_play = (
        fade >= 0.40
        and sl >= 0.35
        and spike_ratio >= 1.15
        and live_px > 0
        and pred_1 >= live_px * 1.0008
        and mom >= -0.05
        and profit_run >= 0.15
    )

    if fakeout_fade_play and getattr(cfg, "ALLOW_FAKEOUT_ENTRIES", True):
        setup_type = "fakeout_fade"
    elif breakout and profit_run >= 0.35 and fade < 0.45:
        setup_type = "momentum_breakout"
    elif sl >= 0.45 and fade < 0.50:
        setup_type = "volume_spike"
    elif fade >= 0.55 and sl >= 0.40:
        setup_type = "likely_fakeout"
    else:
        setup_type = "mixed"

    min_prob = float(getattr(cfg, "MIN_PROFIT_PROBABILITY", 0.42))
    min_fakeout = float(getattr(cfg, "MIN_FAKEOUT_FADE_PROB", 0.50))
    max_fakeout_risk = float(getattr(cfg, "MAX_FAKEOUT_RISK_ENTER", 0.62))
    gate = getattr(cfg, "ENTRY_QUALITY_GATE", True)

    enter_ok = True
    reason = f"profit_prob={profit_probability:.0%} setup={setup_type}"

    if not gate:
        return _pack(
            profit_probability, fakeout_risk, setup_type, True, reason,
            fakeout_fade_play, micro,
        )

    if setup_type == "fakeout_fade":
        enter_ok = profit_probability >= min_fakeout
        reason = (
            f"fakeout fade-play prob={profit_probability:.0%} "
            f"(need {min_fakeout:.0%}) fade={fade:.0%}"
        )
    elif setup_type == "likely_fakeout":
        enter_ok = False
        reason = f"likely fakeout — fade={fade:.0%} spike without follow-through"
    else:
        enter_ok = profit_probability >= min_prob and fakeout_risk <= max_fakeout_risk
        reason = (
            f"profit_prob={profit_probability:.0%} (need {min_prob:.0%}) "
            f"fakeout_risk={fakeout_risk:.0%}"
        )

    return _pack(
        profit_probability, fakeout_risk, setup_type, enter_ok, reason,
        fakeout_fade_play, micro,
    )


def _pack(
    profit_probability: float,
    fakeout_risk: float,
    setup_type: str,
    enter_ok: bool,
    reason: str,
    fakeout_fade_play: bool,
    micro: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "profit_probability": round(profit_probability, 3),
        "fakeout_risk": round(fakeout_risk, 3),
        "setup_type": setup_type,
        "fakeout_fade_play": fakeout_fade_play,
        "enter_ok": enter_ok,
        "reason": reason,
        "spike_likelihood": micro.get("spike_likelihood", 0),
        "fade_risk": micro.get("fade_risk", 0),
        "profit_run": micro.get("profit_run", 0),
        "pred_1bar": micro.get("pred_1bar"),
    }


def quality_blocks_entry(cfg: BotConfig, quality: Dict[str, Any]) -> bool:
    if not getattr(cfg, "ENTRY_QUALITY_GATE", True):
        return False
    return not bool(quality.get("enter_ok", True))
