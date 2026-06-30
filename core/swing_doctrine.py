#!/usr/bin/env python3
"""
core/swing_doctrine.py — Green entry/exit doctrine for swing (multi-day), maturity-scaled.

Same principles as scalp green doctrine (uptrend, green, AI prediction, book profit,
slippage-aware early exit, multi-bar ride) but:
- Timeframe: 1h / 4h / 1d bars; hold measured in days not minutes
- Strictness ramps with brain_maturity + swing IB trip count (slow learning)
- newborn→infant: advisory only; adult + enough swing trips: full mandatory gates
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

import pandas as pd

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig

_STAGE_ORDER = (
    "newborn", "infant", "toddler", "child", "teen", "young_adult", "adult",
)


def swing_doctrine_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    return os.getenv("SWING_DOCTRINE_ENABLED", "true").lower() in ("1", "true", "yes")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def swing_ib_trip_count() -> int:
    try:
        from core.swing_learning import read_swing_trips
        return len(read_swing_trips())
    except Exception:
        return 0


def swing_maturity_level(cfg: Optional["BotConfig"] = None) -> float:
    """
    0.0 = newborn swing learner (advisory) → 1.0 = adult + proven IB swing history.
    """
    cfg = cfg or __import__("core.config", fromlist=["BotConfig"]).BotConfig()
    try:
        from core.brain_maturity import compute_stage
        stage = compute_stage(cfg)
        idx = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else 0
        brain_frac = idx / max(len(_STAGE_ORDER) - 1, 1)
    except Exception:
        brain_frac = 0.0
    trips = swing_ib_trip_count()
    trip_target = int(_env_float("SWING_DOCTRINE_TRIP_MATURE", 24))
    trip_frac = min(1.0, trips / max(trip_target, 1))
    w_brain = _env_float("SWING_DOCTRINE_BRAIN_WEIGHT", 0.55)
    w_trips = 1.0 - w_brain
    return round(min(1.0, brain_frac * w_brain + trip_frac * w_trips), 3)


def swing_maturity_profile(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """Thresholds that tighten as swing brain + IB trips mature."""
    level = swing_maturity_level(cfg)
    full_at = _env_float("SWING_DOCTRINE_FULL_AT", 0.72)
    partial_at = _env_float("SWING_DOCTRINE_PARTIAL_AT", 0.35)
    mode = "advisory"
    if level >= full_at:
        mode = "mandatory"
    elif level >= partial_at:
        mode = "partial"
    return {
        "maturity_level": level,
        "mode": mode,
        "brain_stage": _safe_brain_stage(cfg),
        "swing_trips": swing_ib_trip_count(),
        "entry_min_composite": round(0.38 + level * 0.28, 3),
        "exit_slippage_profit": round(0.48 + level * 0.18, 3),
        "exit_slippage_loss": round(0.40 + level * 0.16, 3),
        "max_ride_days": int(_env_float("SWING_MULTIBAR_MAX_DAYS", 12) * (0.5 + level * 0.5)),
        "min_profit_run": round(0.22 + level * 0.22, 3),
    }


def _safe_brain_stage(cfg: Optional["BotConfig"]) -> str:
    try:
        from core.brain_maturity import compute_stage
        return compute_stage(cfg)
    except Exception:
        return "unknown"


def build_swing_micro(
    runner: Any,
    sym: str,
    *,
    tech: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Micro-style forecast from swing timeframes (1h primary, 4h/1d context)."""
    from core.swing_intel import _fetch_bars
    from core.scalper_micro_predict import micro_forecast

    sym = sym.upper()
    tech = tech or {}
    bars_1h = _fetch_bars(runner, sym, "1 hour", "10 D")
    live_px = 0.0
    micro: Dict[str, Any] = {
        "dir": 1 if tech.get("bias") == "long" else 0,
        "momentum": float(tech.get("strength", 0) or 0),
        "profit_run": 0.0,
        "fade_risk": 0.0,
        "loss_pressure": 0.0,
        "pred_1bar": 0.0,
        "pred_3bar": 0.0,
    }
    if bars_1h is not None and len(bars_1h) >= 6:
        live_px = float(bars_1h["close"].iloc[-1])
        fc = micro_forecast(bars_1h, live_px)
        micro.update(fc)
        micro["live_px"] = live_px
    bars_4h = _fetch_bars(runner, sym, "4 hours", "1 M")
    if bars_4h is not None and len(bars_4h) >= 4 and live_px > 0:
        c4 = bars_4h["close"].values.astype(float)
        slope_4h = (c4[-1] - c4[-min(3, len(c4))]) / max(c4[-min(3, len(c4))], 1e-9)
        micro["pred_3bar"] = max(
            float(micro.get("pred_3bar") or live_px),
            live_px * (1.0 + slope_4h * 0.8),
        )
        if slope_4h > 0.01:
            micro["profit_run"] = min(1.0, float(micro.get("profit_run", 0)) + 0.15)
    tfs = (tech.get("timeframes") or {})
    if tfs.get("1d", {}).get("bias") == "long":
        micro["profit_run"] = min(1.0, float(micro.get("profit_run", 0)) + 0.1)
    if tfs.get("1d", {}).get("bias") == "short":
        micro["fade_risk"] = min(1.0, float(micro.get("fade_risk", 0)) + 0.2)
    return micro


def assess_swing_entry(
    cfg: Optional["BotConfig"],
    runner: Any,
    sym: str,
    *,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Green swing entry — maturity-scaled; full alignment required only when mature."""
    from core.green_trade_doctrine import assess_green_entry, unified_doctrine_enabled
    from core.swing_intel import _fetch_bars

    if not swing_doctrine_enabled(cfg) or not unified_doctrine_enabled(cfg):
        return {"enter_ok": True, "mode": "disabled", "maturity": swing_maturity_profile(cfg)}

    profile = swing_maturity_profile(cfg)
    analysis = analysis or {}
    tech = analysis.get("technical") or {}
    verdict = analysis.get("verdict") or analysis
    bars_1h = _fetch_bars(runner, sym.upper(), "1 hour", "10 D")
    live_px = float(bars_1h["close"].iloc[-1]) if bars_1h is not None and len(bars_1h) else 0.0
    micro = build_swing_micro(runner, sym, tech=tech)

    ge = assess_green_entry(
        cfg,
        ticker=sym,
        df=bars_1h if bars_1h is not None else pd.DataFrame(),
        current_px=live_px,
        micro=micro,
        scan_score=float(verdict.get("score", 0) or 0),
        decision={
            "enter": verdict.get("enter"),
            "confidence": verdict.get("confidence"),
            "halim_enter": verdict.get("bias") == "long",
        },
    )
    composite = float(ge.get("composite_score", 0) or 0)
    min_comp = profile["entry_min_composite"]
    mode = profile["mode"]

    enter_ok = True
    block_reason = ""
    if mode == "mandatory":
        enter_ok = bool(ge.get("enter_ok"))
        if not enter_ok:
            block_reason = f"swing_green:need alignment score={composite:.2f}"
    elif mode == "partial":
        enter_ok = composite >= min_comp and bool(ge.get("uptrend"))
        if not enter_ok:
            block_reason = f"swing_green:partial need score>={min_comp:.2f} uptrend"
    else:
        if not ge.get("enter_ok"):
            log.debug(
                f"  SWING advisory {sym}: green score={composite:.2f} "
                f"(maturity={profile['maturity_level']:.2f})"
            )

    return {
        "enter_ok": enter_ok,
        "mode": mode,
        "maturity": profile,
        "green_entry": ge,
        "micro": micro,
        "block_reason": block_reason,
        "composite_score": composite,
    }


def require_swing_green_entry(
    cfg: Optional["BotConfig"],
    runner: Any,
    sym: str,
    analysis: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    a = assess_swing_entry(cfg, runner, sym, analysis=analysis)
    if a.get("enter_ok"):
        return None
    return str(a.get("block_reason") or "swing_doctrine_block")


def assess_swing_exit(
    cfg: Optional["BotConfig"],
    runner: Any,
    sym: str,
    slot: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Multi-day ride vs book profit / cut loss — uses assess_dynamic_exit with day scale.
    """
    from core.green_trade_doctrine import assess_dynamic_exit
    from core.swing_intel import _fetch_bars

    profile = swing_maturity_profile(cfg)
    entry_px = float(slot.get("entry_fill_px") or slot.get("entry_price") or 0)
    mark = float(slot.get("mark_px") or runner._live_price_for(sym, entry_px) or 0)
    if entry_px <= 0 or mark <= 0:
        return {"should_exit": False, "action": "hold", "reason": ""}

    opened = float(slot.get("opened_at") or 0)
    hold_days = (time.time() - opened) / 86400.0 if opened else 0.0
    pnl_pct = (mark / entry_px) - 1.0
    peak_px = float(slot.get("peak_px") or mark)
    if mark > peak_px:
        peak_px = mark
        slot["peak_px"] = peak_px
    peak_pct = (peak_px / entry_px) - 1.0 if entry_px else 0.0

    tech = {}
    try:
        from core.swing_intel import analyze_swing_technical
        tech = analyze_swing_technical(runner, sym)
    except Exception:
        pass
    micro = build_swing_micro(runner, sym, tech=tech)
    bars_1h = _fetch_bars(runner, sym.upper(), "1 hour", "10 D")

    # Scale slippage thresholds by maturity (looser when learning)
    level = profile["maturity_level"]
    slip_profit = profile["exit_slippage_profit"]
    slip_loss = profile["exit_slippage_loss"]

    os.environ.setdefault("_SWING_DOCTRINE_SLIP_PROFIT", str(slip_profit))
    os.environ.setdefault("_SWING_DOCTRINE_SLIP_LOSS", str(slip_loss))

    dx = assess_dynamic_exit(
        cfg,
        ticker=sym,
        current_px=mark,
        entry_px=entry_px,
        pnl_pct=pnl_pct,
        peak_pct=peak_pct,
        micro=micro,
        df=bars_1h,
        bars_held=int(hold_days),
    )

    max_days = profile["max_ride_days"]
    if dx.get("action") == "ride_multibar" and hold_days >= max_days:
        dx = {
            **dx,
            "should_exit": True,
            "action": "exit_profit",
            "reason": f"swing_exit:max_ride_days {hold_days:.1f}/{max_days}",
        }

    if level < _env_float("SWING_DOCTRINE_PARTIAL_AT", 0.35):
        if dx.get("should_exit") and pnl_pct > 0 and "slippage" not in str(dx.get("reason", "")):
            dx = {**dx, "should_exit": False, "action": "advisory", "reason": ""}

    dx["maturity"] = profile
    dx["hold_days"] = round(hold_days, 2)
    dx["symbol"] = sym.upper()
    return dx


def apply_swing_entry_doctrine(
    cfg: Optional["BotConfig"],
    runner: Any,
    sym: str,
    analysis: Dict[str, Any],
) -> bool:
    """Returns True if entry allowed after doctrine."""
    block = require_swing_green_entry(cfg, runner, sym, analysis=analysis)
    if block:
        log.info(f"  📈 SWING veto {sym}: {block[:100]}")
        return False
    return True
