#!/usr/bin/env python3
"""
core/replay_clock.py — Virtual US/Eastern clock for replay-live sessions.

When set, market_hours.now_et() returns replay time instead of wall clock.
Live trading is unaffected when no replay time is active.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

MARKET_TZ = ZoneInfo("America/New_York")

_active: bool = False
_virtual: Optional[datetime] = None


def activate() -> None:
    global _active
    _active = True


def deactivate() -> None:
    global _active, _virtual
    _active = False
    _virtual = None


def is_active() -> bool:
    return _active and _virtual is not None


def set_replay_time(dt: datetime) -> None:
    """Set current simulated ET (naive or tz-aware)."""
    global _virtual
    if dt.tzinfo is None:
        _virtual = dt.replace(tzinfo=MARKET_TZ)
    else:
        _virtual = dt.astimezone(MARKET_TZ)


def replay_now_et() -> Optional[datetime]:
    """Return virtual ET if replay clock active, else None."""
    if _active and _virtual is not None:
        return _virtual
    return None


def get_now_et() -> datetime:
    """Used by market_hours when replay is running."""
    t = replay_now_et()
    if t is not None:
        return t
    return datetime.now(MARKET_TZ)
