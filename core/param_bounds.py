#!/usr/bin/env python3
"""
core/param_bounds.py — Parameter mutation ranges for AI self-improvement.

Live accounts: conservative learning bounds.
Paper + AI_PAPER_FREE_LEARNING: equity-scaled wide bounds — ride, learn, adapt.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Union

from core.paper_mode import account_equity, is_paper_free_learning

# Never AI-mutable (secrets, credentials)
ABSOLUTE_LOCK_PARAMS: FrozenSet[str] = frozenset({
    "PAPER_TRADING",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_VERIFY_SECRET",
    "GITHUB_TOKEN",
    "GITHUB_PAT",
    "IB_HOST",
    "IB_PORT",
})

# Live / small-account learning bounds
LIVE_PARAM_BOUNDS: Dict[str, Tuple[Union[int, float], Union[int, float]]] = {
    "MAX_RISK_PER_TRADE_USD": (15.0, 100.0),
    "RISK_PER_TRADE_PCT": (0.01, 0.10),
    "HARD_STOP_USD": (25.0, 100.0),
    "MAX_DAILY_LOSS_PCT": (0.01, 0.08),
    "MAX_WEEKLY_LOSS_PCT": (0.02, 0.15),
    "MIN_CASH_RESERVE_PCT": (0.02, 0.30),
    "MAX_TRADE_SIZE_USD": (200.0, 5000.0),
    "MAX_SHARES_PER_TRADE": (50, 3000),
    "PENNY_MAX_SHARES": (200, 1500),
    "PENNY_MAX_DEPLOY_USD": (150.0, 500.0),
    "MAX_CONCURRENT_POSITIONS": (1, 30),
    "AI_MAX_CONCURRENT_POSITIONS": (1, 50),
    "AI_MAX_LOCKED_TARGETS": (3, 40),
    "CONFIDENCE_THRESHOLD": (0.35, 0.90),
    "VOLUME_SPIKE_MIN_RATIO": (1.1, 3.5),
    "LOCKED_SPIKE_MIN_RATIO": (1.05, 2.5),
    "SCALP_MIN_RR": (1.2, 4.0),
    "SCALP_STOP_ATR_MULTIPLIER": (0.4, 2.0),
    "SCALP_TP_ATR_MULTIPLIER": (0.5, 4.0),
    "SCALP_MIN_STOP_PCT": (0.002, 0.02),
    "SCALP_MAX_STOP_PCT": (0.005, 0.03),
    "SCALP_MAX_TP_PCT": (0.01, 0.08),
    "SCALP_TRAILING_ATR_MULTIPLIER": (0.2, 2.5),
    "SCALP_PROFIT_GIVEBACK_PCT": (0.10, 0.60),
    "SCALP_TRAILING_ACTIVATE_PCT": (0.001, 0.01),
    "SCALP_PROFIT_ACTIVATE_PCT": (0.003, 0.02),
    "TRAILING_PROFIT_GIVEBACK_PCT": (0.15, 0.55),
    "SPIKE_TOP_MIN_GAIN_PCT": (0.002, 0.025),
    "SPIKE_TOP_MIN_VOL_RATIO": (1.05, 2.5),
    "PROFIT_HUNT_MIN_PNL_PCT": (0.001, 0.02),
    "EXTENDED_PROFIT_GIVEBACK_PCT": (0.10, 0.50),
    "SCAN_INTERVAL_SECONDS": (5, 180),
    "STAGNATION_EXIT_SEC": (45.0, 240.0),
    "MIN_POSITION_HOLD_SEC": (20.0, 180.0),
    "ENTRY_LIMIT_BUFFER_PCT": (0.001, 0.012),
    "PENNY_LIMIT_BUFFER_PCT": (0.003, 0.015),
    "MAX_ENTRY_SPREAD_PCT": (0.02, 0.08),
    "ENTRY_FILL_MAX_WAIT_SEC": (10.0, 45.0),
    "PPO_ENT_COEF": (0.0001, 0.05),
    "PPO_LR": (1e-5, 0.005),
}

# Strategy/judgment params — same wide technical ranges on paper
_PAPER_TECH_BOUNDS: Dict[str, Tuple[Union[int, float], Union[int, float]]] = {
    k: v for k, v in LIVE_PARAM_BOUNDS.items()
    if k not in {
        "MAX_RISK_PER_TRADE_USD", "RISK_PER_TRADE_PCT", "HARD_STOP_USD",
        "MAX_DAILY_LOSS_PCT", "MAX_WEEKLY_LOSS_PCT", "MIN_CASH_RESERVE_PCT",
        "MAX_TRADE_SIZE_USD", "MAX_SHARES_PER_TRADE", "PENNY_MAX_SHARES",
        "PENNY_MAX_DEPLOY_USD", "MAX_CONCURRENT_POSITIONS",
        "AI_MAX_CONCURRENT_POSITIONS", "AI_MAX_LOCKED_TARGETS",
    }
}

PARAM_ALIASES: Dict[str, str] = {
    "STOP_ATR_MULTIPLIER": "SCALP_STOP_ATR_MULTIPLIER",
    "TAKE_PROFIT_ATR_MULTIPLIER": "SCALP_TP_ATR_MULTIPLIER",
    "TP_ATR_MULTIPLIER": "SCALP_TP_ATR_MULTIPLIER",
}


def paper_param_bounds(cfg) -> Dict[str, Tuple[Union[int, float], Union[int, float]]]:
    """Equity-scaled bounds for $1M paper — AI learns from real sizing mistakes."""
    eq = max(account_equity(cfg), 10_000.0)
    return {
        **_PAPER_TECH_BOUNDS,
        "MAX_RISK_PER_TRADE_USD": (100.0, eq * 0.25),
        "RISK_PER_TRADE_PCT": (0.001, 0.25),
        "HARD_STOP_USD": (50.0, eq * 0.15),
        "MAX_DAILY_LOSS_PCT": (0.005, 0.50),
        "MAX_WEEKLY_LOSS_PCT": (0.01, 0.60),
        "MIN_CASH_RESERVE_PCT": (0.0, 0.50),
        "MAX_TRADE_SIZE_USD": (500.0, eq * 0.95),
        "MAX_SHARES_PER_TRADE": (1, 500_000),
        "PENNY_MAX_SHARES": (100, 50_000),
        "PENNY_MAX_DEPLOY_USD": (100.0, eq * 0.10),
        "MAX_CONCURRENT_POSITIONS": (1, 200),
        "AI_MAX_CONCURRENT_POSITIONS": (1, 500),
        "AI_MAX_LOCKED_TARGETS": (1, 200),
    }


def effective_param_bounds(cfg) -> Dict[str, Tuple[Union[int, float], Union[int, float]]]:
    if is_paper_free_learning(cfg):
        return paper_param_bounds(cfg)
    return dict(LIVE_PARAM_BOUNDS)


def normalize_param(name: str) -> str:
    key = (name or "").strip().upper()
    return PARAM_ALIASES.get(key, key)


def is_tunable(param: str, cfg=None) -> bool:
    p = normalize_param(param)
    bounds = effective_param_bounds(cfg) if cfg is not None else LIVE_PARAM_BOUNDS
    return p in bounds


def is_locked(param: str) -> bool:
    return normalize_param(param) in ABSOLUTE_LOCK_PARAMS


def bounds_for(param: str, cfg=None) -> Optional[Tuple]:
    p = normalize_param(param)
    bounds = effective_param_bounds(cfg) if cfg is not None else LIVE_PARAM_BOUNDS
    return bounds.get(p)


def clamp_param_value(
    param: str,
    value: Any,
    *,
    current: Any = None,
    cfg=None,
) -> Tuple[Any, bool, str]:
    p = normalize_param(param)
    if p in ABSOLUTE_LOCK_PARAMS:
        return value, False, f"locked: {p}"

    bounds = effective_param_bounds(cfg) if cfg is not None else LIVE_PARAM_BOUNDS
    if p not in bounds:
        return value, False, f"not in learning bounds: {p}"

    low, high = bounds[p]
    try:
        if isinstance(current, bool):
            return value, False, f"bool param not tunable: {p}"
        if isinstance(current, int) and not isinstance(current, bool):
            v = int(round(float(value)))
            clamped = int(max(low, min(high, v)))
        else:
            v = float(value)
            clamped = float(max(low, min(high, v)))
    except (TypeError, ValueError):
        return value, False, f"invalid value for {p}: {value}"

    if clamped != v:
        return clamped, True, f"clamped {v} → {clamped} (bounds [{low}, {high}])"
    return clamped, True, "ok"


def validate_mutation(param: str, value: Any, current: Any = None, cfg=None) -> Tuple[bool, str]:
    _, ok, msg = clamp_param_value(param, value, current=current, cfg=cfg)
    return ok, msg


def tunable_param_names(cfg=None) -> List[str]:
    return sorted(effective_param_bounds(cfg).keys())


def bounds_snapshot(cfg) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    mode = "paper_free" if is_paper_free_learning(cfg) else "live_bounded"
    eq = account_equity(cfg)
    for p, (low, high) in effective_param_bounds(cfg).items():
        if hasattr(cfg, p):
            out[p] = {
                "current": getattr(cfg, p),
                "min": low,
                "max": high,
            }
    return {"mode": mode, "equity": eq, "params": out}


def format_bounds_for_prompt(cfg=None, max_params: int = 40) -> str:
    bounds = effective_param_bounds(cfg)
    if cfg and is_paper_free_learning(cfg):
        eq = account_equity(cfg)
        lines = [f"PAPER FREE LEARNING — equity ${eq:,.0f}, wide bounds to learn from outcomes:"]
    else:
        lines = ["LIVE BOUNDED LEARNING:"]
    for i, (p, (lo, hi)) in enumerate(sorted(bounds.items())):
        if i >= max_params:
            lines.append(f"... +{len(bounds) - max_params} more")
            break
        lines.append(f"{p}: [{lo}, {hi}]")
    return "\n".join(lines)
