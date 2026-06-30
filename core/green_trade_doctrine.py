#!/usr/bin/env python3
"""
core/green_trade_doctrine.py — Unified green entry / green exit for ALL capital phases.

War ($1k), pre-war full balance, and post-war full balance use the SAME tactics:
PPO + Halim + entry_quality + uptrend + green bar + micro prediction.
Only sizing differs (capital_phase / war ledger).

Static mechanical gates are advisors; dynamic AI scores decide. Hard blocks:
uptrend + green entry candle + positive prediction when GREEN_DOCTRINE_MANDATORY=true.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

import pandas as pd

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig


def unified_doctrine_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    return os.getenv("GREEN_DOCTRINE_UNIFIED", "true").lower() in ("1", "true", "yes")


def green_entry_mandatory(cfg: Optional["BotConfig"] = None) -> bool:
    if not unified_doctrine_enabled(cfg):
        return False
    return os.getenv("GREEN_DOCTRINE_ENTRY", "true").lower() in ("1", "true", "yes")


def green_exit_mandatory(cfg: Optional["BotConfig"] = None) -> bool:
    if not unified_doctrine_enabled(cfg):
        return False
    return os.getenv("GREEN_DOCTRINE_EXIT", "true").lower() in ("1", "true", "yes")


def same_tactics_all_phases(cfg: Optional["BotConfig"] = None) -> bool:
    """Quality/confidence/profit-prob gates apply in war AND full-balance phases."""
    return unified_doctrine_enabled(cfg)


def _is_green_bar(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 2:
        return False
    try:
        last = df.iloc[-1]
        o = float(last.get("open", last.get("close", 0)) or 0)
        c = float(last.get("close", 0) or 0)
        prev_c = float(df.iloc[-2].get("close", 0) or 0)
        if c <= 0:
            return False
        return c >= o and c >= prev_c
    except Exception:
        return False


def _prediction_up(micro: Optional[Dict[str, Any]], live_px: float) -> bool:
    micro = micro or {}
    pred = float(micro.get("pred_1bar") or micro.get("pred_1") or 0)
    if pred > 0 and live_px > 0:
        return pred >= live_px * 0.9995
    return float(micro.get("dir", 0) or 0) > 0 or float(micro.get("momentum", 0) or 0) > 0.02


def _dynamic_min_confidence(cfg: Optional["BotConfig"], decision: Optional[Dict] = None) -> float:
    cfg = cfg or __import__("core.config", fromlist=["BotConfig"]).BotConfig()
    base = float(getattr(cfg, "CAPITAL_MIN_CONFIDENCE", 0.65))
    try:
        from core.war_entry_gates import war_min_entry_confidence
        if same_tactics_all_phases(cfg):
            base = max(base, war_min_entry_confidence(cfg))
    except Exception:
        pass
    try:
        from core.capital_discipline import effective_min_confidence
        base = max(base, effective_min_confidence(cfg))
    except Exception:
        pass
    if decision:
        # Slightly lower bar when PPO+Halim both agree enter
        if decision.get("halim_enter") and int(decision.get("ppo_action", 0) or 0) == 1:
            base = max(0.52, base - 0.04)
    return base


def _dynamic_min_profit_prob(cfg: Optional["BotConfig"], ticker: str = "") -> float:
    cfg = cfg or __import__("core.config", fromlist=["BotConfig"]).BotConfig()
    try:
        from core.capital_discipline import effective_min_profit_probability
        return effective_min_profit_probability(cfg, scan_score=0, spike_ratio=0)
    except Exception:
        pass
    try:
        from core.war_entry_gates import war_min_profit_probability
        if same_tactics_all_phases(cfg):
            return war_min_profit_probability(cfg)
    except Exception:
        pass
    return float(getattr(cfg, "CAPITAL_MIN_PROFIT_PROBABILITY", 0.62))


def assess_green_entry(
    cfg: Optional["BotConfig"],
    *,
    ticker: str,
    df: pd.DataFrame,
    current_px: float,
    micro: Optional[Dict[str, Any]] = None,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Dynamic green-entry assessment — combines mechanical + AI signals.
    Returns enter_ok, composite_score, reasons, and component flags.
    """
    from core.scalper_filters import only_uptrend
    from core.entry_quality import assess_entry_quality

    decision = decision or {}
    micro = micro or {}
    quality = assess_entry_quality(
        cfg,
        micro,
        spike_ratio=spike_ratio,
        scan_score=scan_score,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        live_px=current_px,
        ticker=ticker,
    )
    uptrend = only_uptrend(df.tail(60) if df is not None and len(df) > 0 else df, current_px)
    green_bar = _is_green_bar(df)
    pred_up = _prediction_up(micro, current_px)

    halim_enter = bool(decision.get("halim_enter") or decision.get("enter"))
    conf = float(decision.get("confidence", ppo_conf) or ppo_conf)
    profit_p = float(quality.get("profit_probability", 0) or 0)
    ppo_buy = int(ppo_action or decision.get("ppo_action", 0) or 0) == 1

    min_conf = _dynamic_min_confidence(cfg, decision)
    min_pp = _dynamic_min_profit_prob(cfg, ticker)

    ai_vote = (ppo_buy and ppo_conf >= min_conf * 0.92) or (halim_enter and conf >= min_conf)
    score = (
        (0.25 if uptrend else 0.0)
        + (0.20 if green_bar else 0.0)
        + (0.20 if pred_up else 0.0)
        + (0.15 if ppo_buy else 0.0)
        + (0.10 if halim_enter else 0.0)
        + profit_p * 0.30
        + min(ppo_conf, 1.0) * 0.10
    )
    score = round(min(1.0, score), 3)

    enter_ok = (
        uptrend
        and green_bar
        and pred_up
        and profit_p >= min_pp
        and ai_vote
        and conf >= min_conf
    )

    reasons = []
    if uptrend:
        reasons.append("uptrend")
    if green_bar:
        reasons.append("green_bar")
    if pred_up:
        reasons.append("pred_up")
    if ppo_buy:
        reasons.append("ppo_buy")
    if halim_enter:
        reasons.append("halim_enter")
    reasons.append(f"pp={profit_p:.2f}")
    reasons.append(f"conf={conf:.2f}")

    return {
        "enter_ok": enter_ok,
        "composite_score": score,
        "uptrend": uptrend,
        "green_bar": green_bar,
        "prediction_up": pred_up,
        "profit_probability": profit_p,
        "min_profit_probability": min_pp,
        "confidence": conf,
        "min_confidence": min_conf,
        "ai_vote": ai_vote,
        "quality": quality,
        "reasons": reasons,
    }


def require_green_entry(
    cfg: Optional["BotConfig"],
    *,
    ticker: str,
    df: pd.DataFrame,
    current_px: float,
    micro: Optional[Dict[str, Any]] = None,
    spike_ratio: float = 1.0,
    scan_score: float = 0.0,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    decision: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Hard block reason if green entry doctrine fails; None if OK or disabled."""
    if not green_entry_mandatory(cfg):
        return None
    a = assess_green_entry(
        cfg,
        ticker=ticker,
        df=df,
        current_px=current_px,
        micro=micro,
        spike_ratio=spike_ratio,
        scan_score=scan_score,
        ppo_action=ppo_action,
        ppo_conf=ppo_conf,
        decision=decision,
    )
    if a.get("enter_ok"):
        return None
    missing = []
    if not a.get("uptrend"):
        missing.append("uptrend")
    if not a.get("green_bar"):
        missing.append("green_bar")
    if not a.get("prediction_up"):
        missing.append("pred_up")
    if float(a.get("profit_probability", 0) or 0) < float(a.get("min_profit_probability", 1)):
        missing.append("profit_prob")
    if not a.get("ai_vote"):
        missing.append("ai_vote")
    return (
        f"green_entry:need {'+'.join(missing) or 'alignment'} "
        f"score={a.get('composite_score', 0):.2f} "
        f"pp={a.get('profit_probability', 0):.2f}"
    )


def apply_unified_pipeline_gates(
    cfg: Optional["BotConfig"],
    *,
    ticker: str = "",
    pipeline: str = "",
    decision: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """War-style pipeline/confidence gates on ALL phases when unified doctrine on."""
    if not same_tactics_all_phases(cfg):
        return None
    try:
        from core.war_entry_gates import war_entry_veto
        veto = war_entry_veto(
            cfg,
            pipeline=pipeline,
            ticker=ticker,
            decision=decision or {},
        )
        return veto
    except Exception as exc:
        log.debug(f"unified pipeline gates: {exc}")
    return None


def assess_green_exit(
    cfg: Optional["BotConfig"],
    *,
    ticker: str,
    pnl_pct: float,
    peak_pct: float,
    micro: Optional[Dict[str, Any]] = None,
    ppo_action: int = 0,
    ppo_conf: float = 0.5,
    ai_exit: bool = False,
    ai_stalled: bool = False,
) -> Dict[str, Any]:
    """
    Dynamic green exit — take profit while green when AI/PPO fade or stall.
    """
    from core.green_profit_lock import evaluate_green_lock

    micro = micro or {}
    pred_down = float(micro.get("dir", 0) or 0) < 0 or (
        float(micro.get("pred_1bar", 0) or 0) > 0
        and float(micro.get("pred_1bar", 0)) < float(micro.get("live_px", 0) or 1e9)
    )
    fade = float(micro.get("fade_risk", 0) or 0)
    giveback = max(0.0, peak_pct - pnl_pct)

    should_exit = False
    reason = ""
    if pnl_pct <= 0:
        return {"should_exit": False, "reason": "", "pnl_pct": pnl_pct}

    if green_exit_mandatory(cfg):
        if ai_exit or (ppo_action == 2 and ppo_conf >= 0.55):
            should_exit = True
            reason = "green_exit:ai_ppo_sell"
        elif ai_stalled and pnl_pct > 0:
            should_exit = True
            reason = "green_exit:ai_stall"
        elif pred_down and fade > 0.45 and pnl_pct >= 0.002:
            should_exit = True
            reason = "green_exit:pred_fade"
        elif peak_pct >= 0.004 and giveback >= peak_pct * 0.35:
            should_exit = True
            reason = "green_exit:giveback"

    lock, lock_reason = evaluate_green_lock(
        cfg,
        pnl_pct=pnl_pct,
        peak_pct=peak_pct,
        ai_stalled=ai_stalled,
        giveback_from_peak=giveback,
        was_green=peak_pct > 0,
    )
    if lock and not should_exit:
        should_exit = True
        reason = lock_reason

    return {
        "should_exit": should_exit,
        "reason": reason,
        "pnl_pct": pnl_pct,
        "peak_pct": peak_pct,
        "giveback": giveback,
    }


def doctrine_account_tags(cfg: Optional["BotConfig"], runner: Any = None) -> Dict[str, Any]:
    """Inject into commander account / verdict rows."""
    try:
        from core.capital_phase import capital_phase_context, uses_war_sizing
        phase = capital_phase_context(cfg, runner)
    except Exception:
        phase = {}
    return {
        "green_doctrine_unified": unified_doctrine_enabled(cfg),
        "green_entry_mandatory": green_entry_mandatory(cfg),
        "green_exit_mandatory": green_exit_mandatory(cfg),
        "same_tactics_all_phases": same_tactics_all_phases(cfg),
        "sizing_war_only": uses_war_sizing(cfg, runner),
        **phase,
    }
