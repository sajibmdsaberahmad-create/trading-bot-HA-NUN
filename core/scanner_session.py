#!/usr/bin/env python3
"""
core/scanner_session.py — IB market scanner profiles by US session.

RTH codes (MOST_ACTIVE, TOP_PERC_GAIN) return 0 rows after 16:00 ET.
After-hours requires snapshot scan codes (MOST_ACTIVE_AVG_USD).
IB rejects extendedHours on reqScannerSubscription (error 10337) — use session codes only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from core.config import BotConfig
from core.market_hours import get_market_state
from core.universe_filter import PROFIT_HUNT_SCAN_CODES

try:
    from ib_insync.contract import TagValue
except ImportError:
    TagValue = None  # type: ignore


# Regular session — live updates 09:30–16:00 ET
RTH_SCAN_CODES = (
    "MOST_ACTIVE",
    "TOP_PERC_GAIN",
    "HOT_BY_VOLUME",
    "HOT_BY_PRICE",
    "TOP_VOLUME",
)

# Pre-market
PRE_MARKET_SCAN_CODES = (
    "TOP_OPEN_PERC_GAIN",
    "TOP_PERC_GAIN",
    "MOST_ACTIVE_USD",
    "HOT_BY_VOLUME",
)

# After 16:00 ET — IB snapshot scanners (close-based); RTH codes return 0 rows
AFTER_HOURS_SCAN_CODES = (
    "MOST_ACTIVE_AVG_USD",
    "MOST_ACTIVE_USD",
    "TOP_TRADE_COUNT",
    "HOT_BY_VOLUME",
)


@dataclass(frozen=True)
class ScannerProfile:
    """One IB scanner run configuration for the current ET session."""

    session: str
    scan_codes: Tuple[str, ...]
    filter_options: Tuple
    scan_options: Tuple
    scanner_setting_pairs: str
    per_code_sec: float
    label: str
    snapshot_mode: bool
    location_codes: Tuple[str, ...]


def _tag(name: str, value: str):
    if TagValue is None:
        return (name, value)
    return TagValue(name, value)


def ib_scanner_profile(cfg: Optional[BotConfig] = None) -> ScannerProfile:
    """Build scanner codes/timeouts/filters for the current US market session."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    base_per = float(getattr(cfg, "IB_SCANNER_PER_CODE_SEC", 18))
    ext_per = float(getattr(cfg, "IB_SCANNER_EXTENDED_PER_CODE_SEC", 12))
    min_vol = int(getattr(cfg, "IB_SCANNER_MIN_VOLUME", 50_000))
    major_only = ("STK.US.MAJOR", "STK.US")
    both_locs = ("STK.US.MAJOR", "STK.US")

    if state == "open":
        codes = tuple(c for c in RTH_SCAN_CODES if c in PROFIT_HUNT_SCAN_CODES)
        return ScannerProfile(
            session=state,
            scan_codes=codes or RTH_SCAN_CODES,
            filter_options=(),
            scan_options=(),
            scanner_setting_pairs="",
            per_code_sec=base_per,
            label="RTH live",
            snapshot_mode=False,
            location_codes=both_locs,
        )

    if state == "pre_market":
        codes = PRE_MARKET_SCAN_CODES
        filters: Tuple = ()
        if TagValue is not None:
            filters = (_tag("volumeAbove", str(min_vol)),)
        return ScannerProfile(
            session=state,
            scan_codes=codes,
            filter_options=filters,
            scan_options=ext_opts,
            scanner_setting_pairs=ext_pairs,
            per_code_sec=ext_per,
            label="pre-market",
            snapshot_mode=False,
            location_codes=both_locs,
        )

    if state == "after_hours":
        # Snapshot codes — do NOT use afterHoursChangePerc filters (often 0 rows)
        return ScannerProfile(
            session=state,
            scan_codes=AFTER_HOURS_SCAN_CODES,
            filter_options=(),
            scan_options=ext_opts,
            scanner_setting_pairs=ext_pairs,
            per_code_sec=ext_per,
            label="after-hours snapshot",
            snapshot_mode=True,
            location_codes=major_only,
        )

    return ScannerProfile(
        session=state,
        scan_codes=(),
        filter_options=(),
        scan_options=(),
        scanner_setting_pairs="",
        per_code_sec=0.0,
        label=f"{state} (off)",
        snapshot_mode=False,
        location_codes=(),
    )


def should_run_ib_scanner(cfg: Optional[BotConfig] = None) -> Tuple[bool, str]:
    """
    Whether to call IB reqScannerSubscription now.

    Scanning can run outside RTH even when ALLOW_AFTER_HOURS_TRADING=false.
    """
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    if state in ("open", "pre_market", "after_hours"):
        if not getattr(cfg, "IB_SCANNER_OUTSIDE_RTH", True):
            return False, f"{state} scanner disabled (IB_SCANNER_OUTSIDE_RTH=false)"
        return True, state
    return False, f"{state} — use curated universe"


def scanner_session_log_line(cfg: Optional[BotConfig] = None) -> str:
    """One-line scanner mode for startup banner."""
    cfg = cfg or BotConfig()
    ok, reason = should_run_ib_scanner(cfg)
    if not ok:
        return f"off ({reason})"
    prof = ib_scanner_profile(cfg)
    mode = "snapshot" if prof.snapshot_mode else "live"
    ext = " + extHours" if prof.scan_options else ""
    codes = ",".join(prof.scan_codes[:2])
    return f"{prof.label} ({mode}{ext}) [{codes}…]"
