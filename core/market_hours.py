#!/usr/bin/env python3
"""
core/market_hours.py — US market clock (always America/New_York).

Device locale/timezone does not matter: all session boundaries, logging
display, and IB extended-hours flags use US Eastern Time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

from core.config import BotConfig

MARKET_TZ = ZoneInfo("America/New_York")

US_MARKET_HOLIDAYS = {
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}


def now_et() -> datetime:
    """Current wall-clock time in US Eastern (DST-aware)."""
    return datetime.now(MARKET_TZ)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def get_market_state(cfg: Optional[BotConfig] = None) -> str:
    """
    Returns one of: 'open', 'pre_market', 'after_hours', 'overnight', 'closed'
    All boundaries are US Eastern Time.
    """
    cfg = cfg or BotConfig()
    now = now_et()
    weekday = now.weekday()

    if weekday >= 5:
        return "closed"

    date_str = now.strftime("%Y-%m-%d")
    if date_str in US_MARKET_HOLIDAYS:
        return "closed"

    current_minutes = now.hour * 60 + now.minute
    pre_start = _minutes(cfg.PRE_MARKET_START)
    regular_open = 9 * 60 + 30
    regular_close = 16 * 60
    ah_end = _minutes(cfg.AFTER_HOURS_END)

    if pre_start <= current_minutes < regular_open:
        return "pre_market"
    if regular_open <= current_minutes < regular_close:
        return "open"
    if regular_close <= current_minutes < ah_end:
        return "after_hours"
    # Weekday gap after after-hours until pre-market (e.g. 20:00–04:00 ET)
    if current_minutes >= ah_end or current_minutes < pre_start:
        return "overnight"
    return "closed"


def is_extended_session(state: str) -> bool:
    return state in ("pre_market", "after_hours", "overnight")


def _session_trading_allowed(cfg: BotConfig, state: str) -> bool:
    if state == "open":
        return True
    if state == "pre_market":
        return bool(getattr(cfg, "ALLOW_PRE_MARKET_TRADING", True))
    if state == "after_hours":
        return bool(getattr(cfg, "ALLOW_AFTER_HOURS_TRADING", True))
    if state == "overnight":
        return bool(getattr(cfg, "ALLOW_OVERNIGHT_TRADING", True))
    return False


def can_trade_now(cfg: Optional[BotConfig] = None) -> tuple[bool, str]:
    """True when the algo may scan, enter, exit, and modify IB orders."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    return _session_trading_allowed(cfg, state), state


def orders_allowed(cfg: Optional[BotConfig] = None) -> tuple[bool, str]:
    """Alias for can_trade_now — gate all IB order submission."""
    return can_trade_now(cfg)


def allowed_trading_sessions_label(cfg: Optional[BotConfig] = None) -> str:
    """Human-readable list of enabled sessions (default: pre-market + RTH only)."""
    cfg = cfg or BotConfig()
    parts: list[str] = []
    if getattr(cfg, "ALLOW_PRE_MARKET_TRADING", True):
        parts.append("pre-market")
    parts.append("regular hours")
    if getattr(cfg, "ALLOW_AFTER_HOURS_TRADING", False):
        parts.append("after-hours")
    if getattr(cfg, "ALLOW_OVERNIGHT_TRADING", False):
        parts.append("overnight")
    return " + ".join(parts)


def min_confidence_for_state(cfg: Optional[BotConfig] = None, state: Optional[str] = None) -> float:
    """Higher bar outside regular hours — skipped when AI learns from failures."""
    cfg = cfg or BotConfig()
    try:
        from core.ai_learning_policy import learn_dont_block
        if learn_dont_block(cfg):
            return float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.55))
    except Exception:
        pass
    state = state or get_market_state(cfg)
    if state == "open":
        return float(getattr(cfg, "CONFIDENCE_THRESHOLD", 0.55))
    if state == "pre_market":
        return float(getattr(cfg, "MIN_CONFIDENCE_PRE_MARKET", 0.70))
    if state == "after_hours":
        return float(getattr(cfg, "MIN_CONFIDENCE_AFTER_HOURS", 0.72))
    if state == "overnight":
        return float(getattr(cfg, "MIN_CONFIDENCE_OVERNIGHT", 0.78))
    return 1.0


def is_regular_session(cfg: Optional[BotConfig] = None) -> bool:
    return get_market_state(cfg) == "open"


def is_rth_open(cfg: Optional[BotConfig] = None) -> bool:
    """True during regular trading hours 09:30–16:00 ET."""
    return is_regular_session(cfg)


def should_use_extended_hours_orders(cfg: Optional[BotConfig] = None) -> bool:
    """True when IB orders need outsideRth (pre, after, overnight)."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    if state == "open":
        return False
    if is_extended_session(state):
        return _session_trading_allowed(cfg, state)
    return False


def format_et(dt: Optional[datetime] = None, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    dt = dt or now_et()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MARKET_TZ)
    else:
        dt = dt.astimezone(MARKET_TZ)
    return dt.strftime(fmt)


def market_status_line(cfg: Optional[BotConfig] = None) -> str:
    """One-line status for logs and startup banners."""
    cfg = cfg or BotConfig()
    now = now_et()
    state = get_market_state(cfg)
    allowed, _ = can_trade_now(cfg)
    sessions = allowed_trading_sessions_label(cfg)
    if allowed:
        mode = f"TRADABLE ({sessions})"
    elif state in ("after_hours", "overnight"):
        mode = f"DAY FINISHED — {state.upper()} (enabled: {sessions})"
    else:
        mode = f"NO SESSION — {state.upper()}"
    return (
        f"US Market: {mode} | "
        f"ET {now.strftime('%Y-%m-%d %H:%M:%S')} | "
        f"RTH 09:30–16:00 ET"
    )


def is_market_day(day_str: str) -> bool:
    """True when day_str (YYYY-MM-DD) is a weekday and not a US holiday."""
    try:
        dt = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=MARKET_TZ)
    except ValueError:
        return False
    if dt.weekday() >= 5:
        return False
    return day_str not in US_MARKET_HOLIDAYS


def previous_market_day(anchor=None) -> str:
    """Most recent US market day strictly before anchor (ET)."""
    from datetime import timedelta

    anchor = anchor or now_et()
    d = anchor.date()
    for _ in range(14):
        d = d - timedelta(days=1)
        ds = d.strftime("%Y-%m-%d")
        if is_market_day(ds):
            return ds
    return (anchor - timedelta(days=1)).strftime("%Y-%m-%d")


def learning_day_for_trigger(trigger: str, anchor=None) -> str:
    """
    Which ET calendar day to learn from.

    session_end → today (day that just finished)
    market_open / off_hours / pre_session → yesterday (last full market day)
    """
    anchor = anchor or now_et()
    today = anchor.strftime("%Y-%m-%d")
    if trigger in ("session_end", "market_close", "day_finished"):
        return today if is_market_day(today) else previous_market_day(anchor)
    return previous_market_day(anchor)
