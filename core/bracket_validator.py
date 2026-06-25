#!/usr/bin/env python3
"""
core/bracket_validator.py — Deterministic bracket math between AI council and broker.

Division of labor (hybrid architecture):
  • Ollama / council → enter/skip, exit/hold, regime, sentiment (NO decimal prices)
  • PPO → pattern timing and directional signals
  • This module → ATR stops, TP, shares, R:R validation (hard rules)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np

from core.config import BotConfig
from core.notify import log
from core.pilot_mode import get_trade_risk_usd


@dataclass
class ValidatedBracket:
    ok: bool
    entry: float
    stop: float
    target: float
    shares: int
    risk_usd: float
    reward_risk: float
    stop_dist: float
    target_dist: float
    reason: str = ""
    source: str = "atr_math"


def _rr_tolerance(cfg: BotConfig) -> float:
    """Allow micro-rounding / ATR tick undershoot without rejecting viable brackets."""
    return float(getattr(cfg, "MIN_REWARD_RISK_TOLERANCE", 0.02))


def _min_rr(cfg: BotConfig) -> float:
    return float(
        getattr(cfg, "MIN_REWARD_RISK_RATIO", None)
        or getattr(cfg, "SCALP_MIN_RR", 1.5)
    )


def _max_rr(cfg: BotConfig) -> float:
    return float(getattr(cfg, "MAX_REWARD_RISK_RATIO", 10.0))


def validate_long_bracket(
    cfg: BotConfig,
    entry: float,
    stop: float,
    target: float,
    shares: int,
) -> Tuple[bool, str, float, float]:
    """
    Hard guardrails before any IB order.
    LONG: stop < entry < target, risk > 0, R:R in [min, max].
  Returns (ok, reason, risk_usd, reward_risk).
    """
    if entry <= 0 or shares < 1:
        return False, f"invalid entry/shares entry={entry} shares={shares}", 0.0, 0.0
    if stop >= entry:
        return False, (
            f"INVERTED STOP: stop ${stop:.4f} >= entry ${entry:.4f} — abort"
        ), 0.0, 0.0
    if target <= entry:
        return False, (
            f"INVERTED TARGET: target ${target:.4f} <= entry ${entry:.4f} — abort"
        ), 0.0, 0.0
    risk_usd = (entry - stop) * shares
    if risk_usd <= 0:
        return False, f"non-positive risk ${risk_usd:.2f}", risk_usd, 0.0
    reward_usd = (target - entry) * shares
    if reward_usd <= 0:
        return False, f"non-positive reward ${reward_usd:.2f}", risk_usd, 0.0
    rr = reward_usd / risk_usd
    min_rr = _min_rr(cfg)
    max_rr = _max_rr(cfg)
    tol = _rr_tolerance(cfg)
    if rr < min_rr - tol:
        return False, f"R:R {rr:.2f} below minimum {min_rr:.2f}", risk_usd, rr
    if rr > max_rr:
        return False, (
            f"R:R {rr:.1f} above maximum {max_rr:.1f} — likely bad AI math"
        ), risk_usd, rr
    return True, "ok", round(risk_usd, 2), round(rr, 2)


def compute_atr_bracket(
    cfg: BotConfig,
    entry: float,
    atr: float,
    *,
    equity: float = 0.0,
    cash: float = 0.0,
    deploy_cap: float = 0.0,
    shares_hint: int = 0,
    momentum_score: float = 0.0,
    is_penny: bool = False,
    avg_vol: float = 0.0,
    use_fixed_risk: Optional[bool] = None,
    max_risk_usd: Optional[float] = None,
) -> ValidatedBracket:
    """Pure math bracket — never reads LLM price fields."""
    if entry <= 0:
        return ValidatedBracket(
            ok=False, entry=entry, stop=0, target=0, shares=0,
            risk_usd=0, reward_risk=0, stop_dist=0, target_dist=0,
            reason="entry price <= 0",
        )
    if atr <= 0:
        atr = entry * float(getattr(cfg, "SCALP_MIN_STOP_PCT", 0.004))

    use_fixed = (
        bool(getattr(cfg, "USE_FIXED_RISK_CAP", False))
        if use_fixed_risk is None else use_fixed_risk
    )
    max_risk = max_risk_usd if max_risk_usd is not None else get_trade_risk_usd(cfg, equity)

    min_dist = entry * float(getattr(cfg, "SCALP_MIN_STOP_PCT", 0.004))
    max_dist = entry * float(getattr(cfg, "SCALP_MAX_STOP_PCT", 0.015))

    atr_mult = float(
        getattr(cfg, "SCALP_STOP_ATR_MULTIPLIER", None)
        or getattr(cfg, "STOP_ATR_MULTIPLIER", 1.5)
    )
    stop_dist = float(np.clip(atr * atr_mult, min_dist, max_dist))

    if use_fixed and max_risk > 0:
        shares_from_cap = max(1, int(deploy_cap / entry)) if deploy_cap > 0 else 0
        shares_from_hint = max(0, int(shares_hint))
        shares = max(shares_from_cap, shares_from_hint, 1)
        stop_dist_raw = max_risk / shares
        stop_dist = float(np.clip(stop_dist_raw, min_dist, max_dist))
    else:
        if shares_hint >= 1:
            shares = int(shares_hint)
        elif deploy_cap > 0:
            shares = max(1, int(deploy_cap / entry))
        elif cash > 0:
            shares = max(1, int((cash * float(getattr(cfg, "DEFAULT_MAX_POSITION_PCT", 0.95))) / entry))
        else:
            shares = max(1, int(max_risk / stop_dist)) if stop_dist > 0 else 1

    if is_penny:
        penny_deploy = float(getattr(cfg, "PENNY_MAX_DEPLOY_USD", 350.0))
        shares = min(shares, max(1, int(penny_deploy / entry)))
        shares = min(shares, int(getattr(cfg, "PENNY_MAX_SHARES", 1200)))
    if avg_vol > 0:
        vol_cap = max(1, int(avg_vol * float(getattr(cfg, "LIQUIDITY_MAX_VOL_PCT", 0.08))))
        shares = min(shares, vol_cap)

    stop = round(entry - stop_dist, 4)
    stop = max(stop, entry * 0.995 - max_dist)
    stop = min(stop, entry - min_dist * 0.5)
    stop = max(stop, 0.0001)

    tp_mult = float(
        getattr(cfg, "SCALP_TP_ATR_MULTIPLIER", None)
        or getattr(cfg, "TAKE_PROFIT_ATR_MULTIPLIER", 2.5)
    )
    tp_dist = atr * tp_mult * (1.0 + 0.25 * max(0.0, momentum_score))
    tp_dist = max(tp_dist, stop_dist * _min_rr(cfg))
    max_tp_dist = entry * float(getattr(cfg, "SCALP_MAX_TP_PCT", 0.03))
    tp_dist = min(tp_dist, max_tp_dist)
    target = round(entry + tp_dist, 4)
    min_target = entry + stop_dist * _min_rr(cfg)
    target = max(target, round(min_target, 4))

    ok, reason, risk_usd, rr = validate_long_bracket(cfg, entry, stop, target, shares)
    if not ok and "below minimum" in reason:
        max_tp = entry + max_tp_dist
        bumped = round(min_target + 0.0001, 4)
        if bumped <= max_tp + 1e-9:
            target = bumped
            ok, reason, risk_usd, rr = validate_long_bracket(cfg, entry, stop, target, shares)
    if not ok:
        stop_dist = float(np.clip(atr * atr_mult, min_dist, max_dist))
        stop = round(entry - stop_dist, 4)
        min_target = entry + stop_dist * _min_rr(cfg)
        target = round(min_target + 0.0001, 4)
        ok, reason, risk_usd, rr = validate_long_bracket(cfg, entry, stop, target, shares)

    return ValidatedBracket(
        ok=ok,
        entry=round(entry, 4),
        stop=stop,
        target=target,
        shares=int(shares),
        risk_usd=risk_usd if ok else 0.0,
        reward_risk=rr if ok else 0.0,
        stop_dist=round(entry - stop, 4),
        target_dist=round(target - entry, 4),
        reason=reason if ok else reason,
        source="atr_math",
    )


@dataclass
class FillAdaptResult:
    ok: bool
    abort: bool
    stop: float
    target: float
    risk_usd: float
    reward_risk: float
    reason: str
    slippage_pct: float = 0.0
    slippage_usd: float = 0.0
    adjusted: bool = False
    planned_entry: float = 0.0
    fill_entry: float = 0.0


def _max_fill_slippage_pct(cfg: BotConfig) -> float:
    return float(getattr(cfg, "MAX_ENTRY_FILL_SLIPPAGE_PCT", 0.012))


def _max_fill_slippage_atr(cfg: BotConfig) -> float:
    return float(getattr(cfg, "MAX_ENTRY_FILL_SLIPPAGE_ATR", 0.5))


def adapt_bracket_to_fill(
    cfg: BotConfig,
    planned_entry: float,
    fill_px: float,
    stop: float,
    target: float,
    shares: int,
    atr: float,
) -> FillAdaptResult:
    """
    Post-fill Risk Officer — re-validate bracket at actual fill, re-anchor if slipped,
    or abort if chase slippage blew past ATR/percent caps.
    """
    base = FillAdaptResult(
        ok=False, abort=False, stop=stop, target=target,
        risk_usd=0.0, reward_risk=0.0, reason="",
        planned_entry=planned_entry, fill_entry=fill_px,
    )
    if fill_px <= 0 or shares < 1:
        base.abort = True
        base.reason = "invalid fill"
        return base

    slip_usd = fill_px - planned_entry
    slip_pct = slip_usd / planned_entry if planned_entry > 0 else 0.0
    base.slippage_pct = round(slip_pct, 6)
    base.slippage_usd = round(slip_usd, 4)

    if planned_entry > 0:
        if slip_pct > _max_fill_slippage_pct(cfg):
            base.abort = True
            base.reason = (
                f"fill slippage {slip_pct:.2%} > max {_max_fill_slippage_pct(cfg):.2%} "
                f"(planned ${planned_entry:.4f} fill ${fill_px:.4f})"
            )
            return base
        if atr > 0 and slip_usd > atr * _max_fill_slippage_atr(cfg):
            # Skip ATR abort when ATR is unrealistically tiny vs price (bad bar data)
            atr_floor = fill_px * float(getattr(cfg, "POST_FILL_MIN_ATR_PCT", 0.002))
            if atr >= atr_floor:
                base.abort = True
                base.reason = (
                    f"fill slipped ${slip_usd:.4f} > {_max_fill_slippage_atr(cfg):.1f}×ATR "
                    f"(${atr:.4f}) — spike chase abort"
                )
                return base

    ok, reason, risk_usd, rr = validate_long_bracket(cfg, fill_px, stop, target, shares)
    if ok:
        base.ok = True
        base.risk_usd = risk_usd
        base.reward_risk = rr
        base.reason = "fill bracket valid"
        return base

    if not getattr(cfg, "POST_FILL_REANCHOR_ENABLED", True):
        base.abort = True
        base.reason = f"post-fill invalid bracket: {reason}"
        return base

    log.warning(
        f"  ⚠️ POST-FILL re-anchor: {reason} | planned ${planned_entry:.4f} "
        f"fill ${fill_px:.4f} stop ${stop:.4f} tp ${target:.4f}"
    )
    reb = compute_atr_bracket(
        cfg, fill_px, atr if atr > 0 else fill_px * 0.005,
        shares_hint=shares,
        use_fixed_risk=bool(getattr(cfg, "USE_FIXED_RISK_CAP", False)),
    )
    if reb.ok:
        base.ok = True
        base.adjusted = True
        base.stop = reb.stop
        base.target = reb.target
        base.risk_usd = reb.risk_usd
        base.reward_risk = reb.reward_risk
        base.reason = f"re-anchored to fill @ ${fill_px:.4f} (was: {reason})"
        return base

    base.abort = True
    base.reason = f"post-fill re-anchor failed: {reb.reason}"
    return base


def validate_decision_bracket(
    cfg: BotConfig,
    decision: Dict[str, Any],
    *,
    fallback_entry: float = 0.0,
) -> Tuple[bool, Dict[str, Any], str]:
    """
    Final gate before broker submit. Rejects inverted stops and absurd R:R.
    Returns (ok, decision_copy, error_reason).
    """
    entry = float(decision.get("entry", decision.get("price", fallback_entry)) or fallback_entry)
    stop = float(decision.get("stop", 0) or 0)
    target = float(decision.get("target", 0) or 0)
    shares = int(decision.get("shares", 0) or 0)
    ok, reason, risk_usd, rr = validate_long_bracket(cfg, entry, stop, target, shares)
    if not ok:
        log.warning(
            f"  🛑 BRACKET REJECTED {decision.get('ticker', '?')}: {reason} | "
            f"entry={entry:.4f} stop={stop:.4f} target={target:.4f} sh={shares}"
        )
        return False, decision, reason
    out = dict(decision)
    out["risk_usd"] = risk_usd
    out["reward_risk"] = rr
    return True, out, ""


def adjust_managed_stop(
    cfg: BotConfig,
    action: str,
    entry: float,
    current_px: float,
    current_stop: float,
    atr: float,
) -> Optional[float]:
    """Map council action to ATR-based stop — ignore LLM price literals."""
    if entry <= 0 or current_px <= 0:
        return None
    action = str(action).upper()
    min_dist = current_px * float(getattr(cfg, "SCALP_MIN_STOP_PCT", 0.004))
    max_dist = current_px * float(getattr(cfg, "SCALP_MAX_STOP_PCT", 0.015))
    atr = atr if atr > 0 else current_px * 0.005
    trail_dist = float(np.clip(atr * float(getattr(cfg, "TRAILING_STOP_ATR_MULTIPLIER", 1.2)), min_dist, max_dist))

    if action == "TIGHTEN_STOP":
        new_stop = max(current_stop, current_px - trail_dist)
        new_stop = min(new_stop, current_px * 0.999)
        if new_stop > current_stop + 0.0001:
            return round(new_stop, 4)
    elif action == "WIDEN_STOP":
        widen_max = float(getattr(cfg, "VOLATILITY_STOP_WIDEN_MAX_PCT", 0.025))
        floor = entry * (1 - widen_max)
        new_stop = min(current_stop, current_px - trail_dist * 1.5)
        new_stop = max(new_stop, floor)
        if new_stop < current_stop - 0.0001:
            return round(new_stop, 4)
    return None


def adjust_managed_target(
    cfg: BotConfig,
    action: str,
    entry: float,
    current_px: float,
    current_target: float,
    atr: float,
) -> Optional[float]:
    """ATR extension for profit-take — ignore LLM target literals."""
    if action != "RAISE_TP" or entry <= 0 or current_px <= 0:
        return None
    atr = atr if atr > 0 else current_px * 0.005
    ext = atr * float(getattr(cfg, "TAKE_PROFIT_ATR_MULTIPLIER", 2.5)) * 0.4
    new_tp = round(max(current_target, current_px + ext), 4)
    max_tp = current_px + current_px * float(getattr(cfg, "SCALP_MAX_TP_PCT", 0.03))
    new_tp = min(new_tp, max_tp)
    if new_tp > current_target + 0.0001:
        return new_tp
    return None
