#!/usr/bin/env python3
"""
core/entry_quality.py — Profit probability + fakeout read for council/PPO.

All weights and thresholds are cfg params (AI-learnable within param_bounds).
Code never hard-vetoes unless ENTRY_QUALITY_HARD_BLOCK or hardness ≥ 0.5.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.config import BotConfig


def _eq_weights(cfg: BotConfig) -> Dict[str, float]:
    return {
        "profit_run": float(getattr(cfg, "EQ_WEIGHT_PROFIT_RUN", 0.22)),
        "spike_lik": float(getattr(cfg, "EQ_WEIGHT_SPIKE_LIK", 0.18)),
        "scan": float(getattr(cfg, "EQ_WEIGHT_SCAN", 0.16)),
        "ppo": float(getattr(cfg, "EQ_WEIGHT_PPO", 0.14)),
        "vol_spike": float(getattr(cfg, "EQ_WEIGHT_VOL_SPIKE", 0.12)),
        "mom": float(getattr(cfg, "EQ_WEIGHT_MOM", 0.10)),
        "penalty_fade": float(getattr(cfg, "EQ_PENALTY_FADE", 0.28)),
        "penalty_loss": float(getattr(cfg, "EQ_PENALTY_LOSS", 0.22)),
    }


def repeat_loser_prob_bump(cfg: BotConfig, ticker: str = "") -> float:
    """Raise min profit_probability for tickers with session losses (no blanket ban)."""
    if os.getenv("REPEAT_LOSER_PROB_BUMP", "true").lower() not in ("1", "true", "yes"):
        return 0.0
    t = str(ticker or "").upper()
    if not t:
        return 0.0
    try:
        from core.live_trade_guard import session_loss_count
        losses = session_loss_count(t)
    except Exception:
        return 0.0
    if losses >= 4:
        return float(os.getenv("REPEAT_LOSER_PROB_BUMP_4", "0.18"))
    if losses >= 3:
        return float(os.getenv("REPEAT_LOSER_PROB_BUMP_3", "0.12"))
    if losses >= 2:
        return float(os.getenv("REPEAT_LOSER_PROB_BUMP_2", "0.08"))
    if losses >= 1:
        return float(os.getenv("REPEAT_LOSER_PROB_BUMP_1", "0.04"))
    return 0.0


def assess_entry_quality(
    cfg: BotConfig,
    micro: Optional[Dict[str, Any]],
    *,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    live_px: float = 0.0,
    ticker: str = "",
) -> Dict[str, Any]:
    """
    Estimate odds of a profitable long scalp and classify setup type.

    Returns profit_probability, fakeout_risk, setup_type, enter_ok (recommendation), reason.
    """
    micro = micro or {}
    w = _eq_weights(cfg)
    sl = float(micro.get("spike_likelihood", 0))
    fade = float(micro.get("fade_risk", 0))
    loss_p = float(micro.get("loss_pressure", 0))
    profit_run = float(micro.get("profit_run", 0))
    mom = float(micro.get("momentum", 0))
    va = float(micro.get("vol_accel", 1.0))
    pred_1 = float(micro.get("pred_1bar") or live_px or 0)
    breakout = bool(micro.get("breakout", False))

    score_norm = min(max(scan_score / 100.0, 0.0), 1.0)
    spike_norm = min(max((spike_ratio - 1.0) / 1.5, 0.0), 1.0)
    ppo_up = ppo_conf if ppo_action == 1 else max(0.0, ppo_conf - 0.15)

    pred_up = 0.0
    if live_px > 0 and pred_1 > live_px:
        pred_up = min((pred_1 / live_px - 1.0) * 50.0, 0.35)

    profit_probability = (
        w["profit_run"] * profit_run
        + w["spike_lik"] * sl
        + w["scan"] * score_norm
        + w["ppo"] * ppo_up
        + w["vol_spike"] * spike_norm
        + w["mom"] * max(0.0, mom)
        + pred_up
        - w["penalty_fade"] * fade
        - w["penalty_loss"] * loss_p
    )
    micro_weak = sl < 0.08 and profit_run < 0.08 and abs(mom) < 0.05
    if micro_weak and scan_score >= 55 and spike_ratio >= 1.25:
        cold_boost = min(0.30, score_norm * 0.24 + spike_norm * 0.20 + ppo_up * 0.08)
        profit_probability += cold_boost
    try:
        from core.sniper_execution import sniper_active, sniper_cold_micro_vol_confirms
        if sniper_active(cfg) and sniper_cold_micro_vol_confirms(
            spike_ratio, scan_score, micro, live_px=live_px, cfg=cfg,
        ):
            profit_probability += min(
                0.28,
                score_norm * 0.20 + spike_norm * 0.22 + ppo_up * 0.14,
            )
    except Exception:
        pass
    profit_probability = float(max(0.0, min(1.0, profit_probability)))

    fakeout_risk = float(max(0.0, min(1.0, fade * 0.55 + sl * 0.25 + (0.2 if va > 1.8 and mom < 0.05 else 0.0))))

    min_fade = float(getattr(cfg, "FAKEOUT_FADE_MIN_FADE", 0.40))
    min_sl = float(getattr(cfg, "FAKEOUT_FADE_MIN_SL", 0.35))
    fakeout_fade_play = (
        fade >= min_fade
        and sl >= min_sl
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
    try:
        from core.capital_discipline import effective_min_profit_probability
        min_prob = effective_min_profit_probability(
            cfg, scan_score=scan_score, spike_ratio=spike_ratio,
        )
    except Exception:
        pass
    loss_bump = repeat_loser_prob_bump(cfg, ticker)
    if loss_bump > 0:
        min_prob = min(0.92, min_prob + loss_bump)
    min_fakeout = float(getattr(cfg, "MIN_FAKEOUT_FADE_PROB", 0.50))
    max_fakeout_risk = float(getattr(cfg, "MAX_FAKEOUT_RISK_ENTER", 0.62))
    fakeout_block = float(getattr(cfg, "LIKELY_FAKEOUT_BLOCK_LEVEL", 0.0))

    enter_ok = True
    reason = f"profit_prob={profit_probability:.0%} setup={setup_type}"

    if setup_type == "fakeout_fade":
        enter_ok = profit_probability >= min_fakeout
        reason = (
            f"fakeout fade-play prob={profit_probability:.0%} "
            f"(target {min_fakeout:.0%}) fade={fade:.0%}"
        )
    elif setup_type == "likely_fakeout":
        if fakeout_block >= 0.5:
            enter_ok = False
            reason = f"likely fakeout (block_level={fakeout_block:.0%}) fade={fade:.0%}"
        else:
            enter_ok = (
                profit_probability >= min_prob * 0.85
                and fakeout_risk <= max_fakeout_risk
            )
            reason = (
                f"likely fakeout advisory prob={profit_probability:.0%} "
                f"fakeout_risk={fakeout_risk:.0%}"
            )
    else:
        enter_ok = profit_probability >= min_prob and fakeout_risk <= max_fakeout_risk
        reason = (
            f"profit_prob={profit_probability:.0%} (target {min_prob:.0%}) "
            f"fakeout_risk={fakeout_risk:.0%} (max {max_fakeout_risk:.0%})"
        )
    if loss_bump > 0 and not enter_ok:
        reason = f"{reason} | repeat_loser+{loss_bump:.0%}"

    return _pack(
        profit_probability, fakeout_risk, setup_type, enter_ok, reason,
        fakeout_fade_play, micro, live_px=live_px,
    )


def _pack(
    profit_probability: float,
    fakeout_risk: float,
    setup_type: str,
    enter_ok: bool,
    reason: str,
    fakeout_fade_play: bool,
    micro: Dict[str, Any],
    *,
    live_px: float = 0.0,
) -> Dict[str, Any]:
    pred_1bar = micro.get("pred_1bar")
    if pred_1bar is None and live_px > 0:
        pred_1bar = live_px
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
        "pred_1bar": pred_1bar,
    }


def regime_blocks_entry(cfg: BotConfig, regime: str) -> bool:
    """Block new entries in choppy/low-vol regimes when enabled."""
    caution, _ = regime_entry_caution(cfg, regime)
    if not caution:
        return False
    try:
        from core.smart_stack import mechanical_gates_advisory_only
        if mechanical_gates_advisory_only(cfg):
            return False
    except Exception:
        pass
    return True


def regime_entry_caution(cfg: BotConfig, regime: str) -> tuple[bool, str]:
    """Signal-only: would regime filter caution? Ignores advisory mode."""
    if not getattr(cfg, "REGIME_ENTRY_BLOCK", False):
        return False, ""
    label = (regime or "").strip().lower()
    if not label or label == "unknown":
        return False, ""
    blocked = getattr(cfg, "REGIME_ENTRY_BLOCK_LIST", None)
    if not blocked:
        raw = os.getenv("REGIME_ENTRY_BLOCK_LIST", "ranging,low_volatility")
        blocked = [x.strip().lower() for x in raw.split(",") if x.strip()]
    else:
        blocked = [str(x).strip().lower() for x in blocked]
    if label in blocked:
        return True, f"regime={regime}"
    return False, ""


def mtf_trend_aligned(df_5m: Any, df_15m: Any) -> tuple[bool, str]:
    """Require price above 20-bar SMA on 5m and 15m (human-style trend filter)."""
    import pandas as pd

    for label, df in (("5m", df_5m), ("15m", df_15m)):
        if df is None:
            continue
        if not isinstance(df, pd.DataFrame) or len(df) < 20:
            continue
        closes = df["close"].values
        sma = float(closes[-20:].mean())
        if float(closes[-1]) <= sma:
            return False, f"{label}_below_sma20"
    return True, "mtf_aligned"


def mtf_cache_ttl_sec(cfg: Optional[BotConfig] = None) -> float:
    try:
        return float(os.getenv("MTF_BAR_CACHE_SEC", "60"))
    except (TypeError, ValueError):
        return 60.0


def mtf_fetch_skipped(
    cfg: BotConfig,
    *,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> bool:
    """Skip expensive IB 5m/15m fetches when MTF cannot block this spike."""
    if not getattr(cfg, "MTF_ENTRY_BLOCK", False):
        return True
    try:
        from core.sniper_execution import (
            sniper_active,
            sniper_vol_flash,
            is_sniper_strong_spike,
        )
        if sniper_active(cfg) and (
            sniper_vol_flash(cfg, scan_score, spike_ratio)
            or is_sniper_strong_spike(cfg, scan_score, spike_ratio)
        ):
            return True
    except Exception:
        pass
    return False


def mtf_blocks_entry(
    cfg: BotConfig,
    df_5m: Any,
    df_15m: Any,
    *,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> bool:
    """Block spike entries when higher timeframes are not in uptrend."""
    caution, _ = mtf_entry_caution(
        cfg, df_5m, df_15m, scan_score=scan_score, spike_ratio=spike_ratio,
    )
    if not caution:
        return False
    try:
        from core.smart_stack import mechanical_gates_advisory_only
        if mechanical_gates_advisory_only(cfg):
            return False
    except Exception:
        pass
    return True


def mtf_entry_caution(
    cfg: BotConfig,
    df_5m: Any,
    df_15m: Any,
    *,
    scan_score: float = 0.0,
    spike_ratio: float = 0.0,
) -> tuple[bool, str]:
    """Signal-only: would MTF filter caution? Ignores advisory mode."""
    if not getattr(cfg, "MTF_ENTRY_BLOCK", False):
        return False, ""
    try:
        from core.sniper_execution import (
            sniper_active,
            sniper_vol_flash,
            is_sniper_strong_spike,
        )
        if sniper_active(cfg) and (
            sniper_vol_flash(cfg, scan_score, spike_ratio)
            or is_sniper_strong_spike(cfg, scan_score, spike_ratio)
        ):
            return False, ""
    except Exception:
        pass
    ok, detail = mtf_trend_aligned(df_5m, df_15m)
    if not ok:
        return True, detail or "5m/15m not aligned"
    return False, ""


def profit_prob_blocks_entry(
    cfg: BotConfig,
    quality: Optional[Dict[str, Any]],
) -> bool:
    """Hard block when calculative profit_probability is red (Smart Stack default)."""
    try:
        from core.smart_stack import strict_profit_prob_enabled
        if not strict_profit_prob_enabled(cfg):
            return False
    except Exception:
        return False
    if not quality:
        return True
    return not bool(quality.get("enter_ok", True))


def apply_profit_prob_veto(
    cfg: BotConfig,
    decision: Dict[str, Any],
    quality: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Last-line veto — stamp profit fields and block red calculative quality."""
    out = dict(decision)
    if quality:
        out["profit_probability"] = float(quality.get("profit_probability", 0) or 0)
        out["fakeout_risk"] = float(quality.get("fakeout_risk", 0) or 0)
        out["quality_enter"] = bool(quality.get("enter_ok", True))
        out["quality_conf"] = float(quality.get("profit_probability", 0) or 0)
        out["quality_reason"] = str(quality.get("reason", ""))[:200]
    if not profit_prob_blocks_entry(cfg, quality):
        return out
    out["enter"] = False
    out["pending"] = False
    reason = str((quality or {}).get("reason", "profit_probability below threshold"))[:160]
    out["reason"] = f"profit_prob veto: {reason}"[:200]
    pipe = str(out.get("pipeline", "") or "entry")
    if "profit_prob" not in pipe:
        out["pipeline"] = f"{pipe}+profit_prob_veto" if pipe else "profit_prob:veto"
    return out


def apply_ai_sure_veto(
    cfg: BotConfig,
    decision: Dict[str, Any],
    quality: Optional[Dict[str, Any]],
    *,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    scan_score: float = 0.0,
    spike_ratio: float = 1.0,
    ticker: str = "",
) -> Dict[str, Any]:
    """Block blind spike / fast-path entries when AI-sure mode is on."""
    try:
        from core.smart_stack import (
            ai_sure_entry_enabled,
            dynamic_entry_surety,
            fast_entry_pipeline_blocked,
        )
        if not ai_sure_entry_enabled(cfg):
            return decision
    except Exception:
        return decision

    out = dict(decision)
    if not out.get("enter"):
        return out

    pipe = str(out.get("pipeline", "") or "")
    if fast_entry_pipeline_blocked(pipe):
        out["enter"] = False
        out["pending"] = False
        out["reason"] = f"AI-sure: blocked fast path ({pipe})"[:200]
        out["pipeline"] = f"{pipe}+ai_sure_veto" if pipe else "ai_sure:fast_block"
        return out

    if "ai_sure_quality_timeout" in pipe and enter_ok and profit_prob >= min_prob:
        return out

    sure = dynamic_entry_surety(
        cfg, scan_score=scan_score, spike_ratio=spike_ratio, ticker=ticker,
    )
    profit_prob = float(
        (quality or {}).get("profit_probability")
        or out.get("profit_probability")
        or 0
    )
    enter_ok = bool((quality or {}).get("enter_ok", True))
    min_conf = float(sure.get("min_conf", 0.65))
    min_prob = float(sure.get("min_prob", 0.62))
    halim_enter = out.get("halim_enter")
    council_ok = "council:" in pipe and bool(out.get("council_agreement"))

    if ppo_action != 1 or float(ppo_conf or 0) < min_conf * 0.92:
        out["enter"] = False
        out["pending"] = False
        out["reason"] = (
            f"AI-sure: PPO {ppo_conf:.0%} below {min_conf:.0%} floor"
        )[:200]
        out["pipeline"] = f"{pipe}+ai_sure_veto" if pipe else "ai_sure:ppo"
        return out

    if not enter_ok or profit_prob < min_prob:
        out["enter"] = False
        out["pending"] = False
        out["reason"] = (
            f"AI-sure: prob={profit_prob:.0%} need {min_prob:.0%} enter_ok={enter_ok}"
        )[:200]
        out["pipeline"] = f"{pipe}+ai_sure_veto" if pipe else "ai_sure:prob"
        return out

    allowed_halim = (
        pipe.startswith("halim:ai_sure")
        or pipe.startswith("halim:local_lead")
        or "ai_sure_quality_timeout" in pipe
    )
    if allowed_halim or council_ok:
        return out

    out["enter"] = False
    out["pending"] = bool(out.get("pending"))
    out["reason"] = (
        f"AI-sure: need Halim lead or council alignment (pipe={pipe})"
    )[:200]
    out["pipeline"] = f"{pipe}+ai_sure_veto" if pipe else "ai_sure:alignment"
    return out


def quality_blocks_entry(cfg: BotConfig, quality: Dict[str, Any]) -> bool:
    """True when hardness/block enabled, or strict profit prob is red."""
    if profit_prob_blocks_entry(cfg, quality):
        return True
    if getattr(cfg, "ENTRY_QUALITY_HARD_BLOCK", False):
        return not bool(quality.get("enter_ok", True))
    hardness = float(getattr(cfg, "ENTRY_QUALITY_HARDNESS", 0.0))
    if hardness >= 0.5 and not bool(quality.get("enter_ok", True)):
        return True
    if getattr(cfg, "ENTRY_QUALITY_GATE", False) and not bool(quality.get("enter_ok", True)):
        return True
    return False


def apply_ai_entry_quality(
    cfg: BotConfig,
    decision: Dict[str, Any],
    quality: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Blend quality signals into council decision; veto only when hardness enabled."""
    if not quality:
        return decision

    prob = float(quality.get("profit_probability", 0.5))
    fake = float(quality.get("fakeout_risk", 0))
    decision["profit_probability"] = prob
    decision["fakeout_risk"] = fake
    decision["setup_type"] = quality.get("setup_type")
    decision["quality_recommendation"] = bool(quality.get("enter_ok", True))

    if quality_blocks_entry(cfg, quality):
        decision["enter"] = False
        decision["reason"] = f"quality veto: {quality.get('reason', '')}"[:200]
        decision["pipeline"] = f"quality:{quality.get('setup_type', 'skip')}"
        return decision

    blend_w = float(getattr(cfg, "ENTRY_QUALITY_BLEND_WEIGHT", 0.35))
    try:
        from core.capital_discipline import effective_entry_quality_blend
        blend_w = effective_entry_quality_blend(cfg)
    except Exception:
        pass
    if blend_w <= 0.01:
        return decision

    conf = float(decision.get("confidence", 0.5))
    min_prob = float(getattr(cfg, "MIN_PROFIT_PROBABILITY", 0.42))
    try:
        from core.capital_discipline import effective_min_profit_probability
        min_prob = effective_min_profit_probability(cfg)
    except Exception:
        pass
    ollama_prob = decision.get("ollama_profit_probability")
    if ollama_prob is not None:
        prob = float(ollama_prob)

    if prob >= min_prob:
        decision["confidence"] = min(1.0, conf + blend_w * (prob - conf))
    else:
        gap = min_prob - prob
        decision["confidence"] = max(0.0, conf - blend_w * gap)
        if fake > float(getattr(cfg, "MAX_FAKEOUT_RISK_ENTER", 0.62)):
            decision["confidence"] = min(decision["confidence"], conf * 0.9)

    return decision
