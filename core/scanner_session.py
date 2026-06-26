#!/usr/bin/env python3
"""
core/scanner_session.py — IB market scanner profiles by US session.

RTH scan codes (MOST_ACTIVE, TOP_PERC_GAIN) return 0 rows after 16:00 ET unless
the subscription uses extended-hours filters / session-appropriate scan codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.config import BotConfig
from core.market_hours import get_market_state
from core.universe_filter import PROFIT_HUNT_SCAN_CODES

try:
    from ib_insync.contract import TagValue
except ImportError:
    TagValue = None  # type: ignore


# Regular session — IB updates these live 09:30–16:00 ET
RTH_SCAN_CODES = (
    "MOST_ACTIVE",
    "TOP_PERC_GAIN",
    "HOT_BY_VOLUME",
    "HOT_BY_PRICE",
    "TOP_VOLUME",
)

# Pre-market — TOP_OPEN_PERC_GAIN is designed for the open / pre-RTH window
PRE_MARKET_SCAN_CODES = (
    "TOP_OPEN_PERC_GAIN",
    "TOP_PERC_GAIN",
    "HOT_BY_VOLUME",
    "MOST_ACTIVE",
)

# After 16:00 ET — RTH-only codes stall with 0 rows; use AH-friendly codes + filters
AFTER_HOURS_SCAN_CODES = (
    "TOP_PERC_GAIN",
    "HOT_BY_VOLUME",
    "MOST_ACTIVE",
    "TOP_VOLUME",
)


@dataclass(frozen=True)
class ScannerProfile:
    """One IB scanner run configuration for the current ET session."""

    session: str
    scan_codes: Tuple[str, ...]
    filter_options: Tuple  # TagValue tuples
    per_code_sec: float
    label: str
    use_extended_filters: bool


def _tag(name: str, value: str):
    if TagValue is None:
        return (name, value)
    return TagValue(name, value)


def ib_scanner_profile(cfg: Optional[BotConfig] = None) -> ScannerProfile:
    """Build scanner codes/timeouts/filters for the current US market session."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    base_per = float(getattr(cfg, "IB_SCANNER_PER_CODE_SEC", 18))
    ext_per = float(getattr(cfg, "IB_SCANNER_EXTENDED_PER_CODE_SEC", 8))
    min_vol = int(getattr(cfg, "IB_SCANNER_MIN_VOLUME", 50_000))
    use_filters = bool(getattr(cfg, "IB_SCANNER_EXTENDED_FILTERS", True))

    if state == "open":
        codes = tuple(c for c in RTH_SCAN_CODES if c in PROFIT_HUNT_SCAN_CODES)
        return ScannerProfile(
            session=state,
            scan_codes=codes or RTH_SCAN_CODES,
            filter_options=(),
            per_code_sec=base_per,
            label="RTH live",
            use_extended_filters=False,
        )

    if state == "pre_market":
        codes = tuple(c for c in PRE_MARKET_SCAN_CODES if c in PROFIT_HUNT_SCAN_CODES)
        filters: Tuple = ()
        if use_filters and TagValue is not None:
            filters = (_tag("volumeAbove", str(min_vol)),)
        return ScannerProfile(
            session=state,
            scan_codes=codes or PRE_MARKET_SCAN_CODES,
            filter_options=filters,
            per_code_sec=ext_per,
            label="pre-market extended",
            use_extended_filters=use_filters,
        )

    if state == "after_hours":
        codes = tuple(c for c in AFTER_HOURS_SCAN_CODES if c in PROFIT_HUNT_SCAN_CODES)
        filters = ()
        if use_filters and TagValue is not None:
            # afterHoursChangePerc* gates AH movers; volumeAbove avoids dead names
            filters = (
                _tag("volumeAbove", str(min_vol)),
                _tag("afterHoursChangePercAbove", "0.1"),
            )
        return ScannerProfile(
            session=state,
            scan_codes=codes or AFTER_HOURS_SCAN_CODES,
            filter_options=filters,
            per_code_sec=ext_per,
            label="after-hours extended",
            use_extended_filters=use_filters,
        )

    return ScannerProfile(
        session=state,
        scan_codes=(),
        filter_options=(),
        per_code_sec=0.0,
        label=f"{state} (scanner off)",
        use_extended_filters=False,
    )


def should_run_ib_scanner(cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    """
    Whether to call IB reqScannerSubscription now.

    Scanning can run outside RTH even when ALLOW_AFTER_HOURS_TRADING=false.
    Overnight/weekend/holiday: skip live scanner (use curated fallback).
    """
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    if state in ("open", "pre_market", "after_hours"):
        if state == "after_hours" and not getattr(cfg, "IB_SCANNER_OUTSIDE_RTH", True):
            return False, "after_hours scanner disabled (IB_SCANNER_OUTSIDE_RTH=false)"
        if state == "pre_market" and not getattr(cfg, "IB_SCANNER_OUTSIDE_RTH", True):
            return False, "pre_market scanner disabled (IB_SCANNER_OUTSIDE_RTH=false)"
        return True, state
    return False, f"{state} — IB scanner skipped (no live RTH/AH session)"


def scanner_session_log_line(cfg: Optional[BotConfig] = None) -> str:
    """One-line scanner mode for startup banner."""
    cfg = cfg or BotConfig()
    ok, reason = should_run_ib_scanner(cfg)
    if not ok:
        return f"scanner: off ({reason})"
    prof = ib_scanner_profile(cfg)
    filt = " + AH filters" if prof.filter_options else ""
    return f"scanner: {prof.label}{filt} [{','.join(prof.scan_codes[:3])}…]"
