"""UTC time helpers — timezone-aware replacements for datetime.utcnow()."""

from __future__ import annotations

from datetime import date, datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def utc_today() -> date:
    return utc_now().date()
