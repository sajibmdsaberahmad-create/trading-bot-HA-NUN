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
    Returns one of: 'open', 'pre_market', 'after_hours', 'closed'
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
    return "closed"


def is_regular_session(cfg: Optional[BotConfig] = None) -> bool:
    return get_market_state(cfg) == "open"


def should_use_extended_hours_orders(cfg: Optional[BotConfig] = None) -> bool:
    """True only when IB should receive outsideRth on orders."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    if state == "pre_market":
        return bool(getattr(cfg, "ALLOW_PRE_MARKET_TRADING", False))
    if state == "after_hours":
        return bool(getattr(cfg, "ALLOW_AFTER_HOURS_TRADING", False))
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
    return (
        f"US Market: {state.upper()} | "
        f"ET {now.strftime('%Y-%m-%d %H:%M:%S')} | "
        f"RTH 09:30–16:00 ET"
    )
