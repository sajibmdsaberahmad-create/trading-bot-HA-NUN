#!/usr/bin/env python3
"""
core/green_profit_lock.py — Mechanical green-profit fallback when AI stalls.

AI gets first shot at riding/trailing/exiting. If council is pending, timed out,
or never responds while the position is green, this layer quick-scalps the profit
so trades do not bleed back to red.
"""

from __future__ import annotations

from typing import Optional, Tuple

from core.config import BotConfig


def green_profit_lock_enabled(cfg: Optional[BotConfig] = None) -> bool:
    cfg = cfg or BotConfig()
    return bool(getattr(cfg, "GREEN_PROFIT_LOCK_ENABLED", True))


def is_green_lock_reason(reason: str) -> bool:
    r = (reason or "").lower()
    return "green_profit_lock" in r or r.startswith("green_lock:")


def min_green_pnl_pct(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    return float(getattr(cfg, "GREEN_PROFIT_LOCK_MIN_PNL_PCT", 0.0025))


def quick_scalp_pnl_pct(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    return float(getattr(cfg, "GREEN_PROFIT_LOCK_QUICK_SCALP_PCT", 0.0035))


def ai_wait_sec(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    return float(getattr(cfg, "GREEN_PROFIT_LOCK_AI_WAIT_SEC", 4.0))


def giveback_fallback_pct(cfg: Optional[BotConfig] = None) -> float:
    cfg = cfg or BotConfig()
    return float(getattr(cfg, "GREEN_PROFIT_LOCK_GIVEBACK_PCT", 0.22))


def fade_floor_pct(cfg: Optional[BotConfig] = None) -> float:
    """Lock green if profit fades back toward breakeven after a real run."""
    cfg = cfg or BotConfig()
    return float(getattr(cfg, "GREEN_PROFIT_LOCK_FADE_FLOOR_PCT", 0.0015))


def evaluate_green_lock(
    cfg: Optional[BotConfig],
    *,
    pnl_pct: float,
    peak_pct: float,
    ai_stalled: bool,
    giveback_from_peak: float = 0.0,
    was_green: bool = False,
) -> Tuple[bool, str]:
    """
    Returns (should_lock, reason). Only acts while pnl_pct > 0.
    """
    if not green_profit_lock_enabled(cfg):
        return False, ""
    if pnl_pct <= 0:
        return False, ""

    min_pnl = min_green_pnl_pct(cfg)
    quick = quick_scalp_pnl_pct(cfg)
    gb_frac = giveback_fallback_pct(cfg)
    floor = fade_floor_pct(cfg)

    if ai_stalled and pnl_pct >= quick:
        return True, (
            f"green_profit_lock:ai_stall quick_scalp +{pnl_pct:.2%} "
            f"(peak +{peak_pct:.2%})"
        )

    if peak_pct >= min_pnl and giveback_from_peak > 0 and peak_pct > 0:
        if giveback_from_peak >= peak_pct * gb_frac and pnl_pct >= floor:
            return True, (
                f"green_profit_lock:giveback +{pnl_pct:.2%} "
                f"(peak +{peak_pct:.2%} gave back {giveback_from_peak:.2%})"
            )

    if was_green and peak_pct >= quick and pnl_pct < floor and pnl_pct > 0:
        return True, (
            f"green_profit_lock:fade_floor +{pnl_pct:.2%} "
            f"(peak was +{peak_pct:.2%})"
        )

    if ai_stalled and pnl_pct >= min_pnl:
        return True, f"green_profit_lock:ai_stall_min_green +{pnl_pct:.2%}"

    return False, ""


def mechanical_green_fallback(
    cfg: Optional[BotConfig],
    reason: str,
    pnl_pct: float,
    *,
    ai_stalled: bool = False,
) -> bool:
    """True when mechanical exit should bypass council (AI failed or green lock)."""
    if pnl_pct <= 0:
        return False
    if is_green_lock_reason(reason):
        return True
    if not green_profit_lock_enabled(cfg):
        return False
    if ai_stalled:
        return True
    return False
