#!/usr/bin/env python3
"""
core/rth_session.py — RTH open detection, opening-window alertness, live-session tuning.

The bot may run since pre-market; this module ensures an immediate, aggressive
shift when regular hours begin (09:30 ET) without restart.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from core.config import BotConfig
from core.market_hours import MARKET_TZ, get_market_state, now_et

RTH_OPEN_HHMM = (9, 30)
RTH_CLOSE_HHMM = (16, 0)


def _today_rth_open(cfg: Optional[BotConfig] = None) -> datetime:
    now = now_et()
    h, m = RTH_OPEN_HHMM
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def _today_rth_close(cfg: Optional[BotConfig] = None) -> datetime:
    now = now_et()
    h, m = RTH_CLOSE_HHMM
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def seconds_until_rth_open(cfg: Optional[BotConfig] = None) -> Optional[float]:
    """Seconds until 09:30 ET today; negative if RTH already started; None if not a market day."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    if state in ("closed",):
        return None
    now = now_et()
    open_dt = _today_rth_open(cfg)
    return (open_dt - now).total_seconds()


def seconds_since_rth_open(cfg: Optional[BotConfig] = None) -> Optional[float]:
    """Seconds since 09:30 ET today; None if before open or not a market day."""
    cfg = cfg or BotConfig()
    if get_market_state(cfg) not in ("open", "after_hours"):
        return None
    now = now_et()
    open_dt = _today_rth_open(cfg)
    if now < open_dt:
        return None
    return (now - open_dt).total_seconds()


def is_rth(cfg: Optional[BotConfig] = None) -> bool:
    return get_market_state(cfg or BotConfig()) == "open"


def is_rth_opening_window(cfg: Optional[BotConfig] = None) -> bool:
    """First N minutes after the bell — elevated noise, stricter entry filters."""
    cfg = cfg or BotConfig()
    if not is_rth(cfg):
        return False
    secs = seconds_since_rth_open(cfg)
    if secs is None:
        return False
    window_min = float(getattr(cfg, "RTH_OPENING_WINDOW_MIN", 30))
    return secs < window_min * 60.0


def is_pre_rth_countdown(cfg: Optional[BotConfig] = None) -> bool:
    """Within N seconds before 09:30 — wake main loop for instant open handling."""
    cfg = cfg or BotConfig()
    if get_market_state(cfg) != "pre_market":
        return False
    secs = seconds_until_rth_open(cfg)
    if secs is None:
        return False
    lead = float(getattr(cfg, "RTH_OPEN_COUNTDOWN_SEC", 120))
    return 0 < secs <= lead


def rth_tier(cfg: Optional[BotConfig] = None) -> str:
    """Session tier for monitoring and AI context."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    if state == "open":
        return "rth_opening" if is_rth_opening_window(cfg) else "rth"
    return state


def rth_monitor_interval_sec(cfg: BotConfig) -> float:
    """Faster loops during RTH; fastest in opening window."""
    if is_rth_opening_window(cfg):
        return float(getattr(cfg, "RTH_OPENING_MONITOR_SEC", 0.05))
    if is_rth(cfg):
        return float(getattr(cfg, "RTH_MONITOR_SEC", 0.08))
    if is_pre_rth_countdown(cfg):
        return float(getattr(cfg, "RTH_PREOPEN_MONITOR_SEC", 0.12))
    from core.fast_execution import ai_fast_execution
    if ai_fast_execution(cfg):
        base = float(getattr(cfg, "FAST_MONITOR_SEC", 0.15))
        if bool(getattr(cfg, "PROFIT_LOCK_ULTRA_FAST", True)):
            return min(base, 0.10)
        return base
    return float(getattr(cfg, "FAST_MONITOR_SEC", 1.0))


def rth_main_loop_sec(cfg: BotConfig, **kwargs) -> Optional[float]:
    """Override main-loop sleep when near or in RTH alert tiers."""
    if is_rth_opening_window(cfg):
        return float(getattr(cfg, "RTH_OPENING_LOOP_SEC", 0.05))
    if is_rth(cfg):
        in_pos = bool(kwargs.get("in_position"))
        if in_pos:
            return float(getattr(cfg, "RTH_POSITION_LOOP_SEC", 0.08))
        return float(getattr(cfg, "RTH_FLAT_LOOP_SEC", 0.08))
    if is_pre_rth_countdown(cfg):
        return float(getattr(cfg, "RTH_PREOPEN_LOOP_SEC", 0.15))
    return None


def opening_entry_adjustments(cfg: BotConfig) -> Dict[str, float]:
    """Stricter mechanical gates during opening noise."""
    if not is_rth_opening_window(cfg):
        return {}
    return {
        "min_spike_mult": float(getattr(cfg, "RTH_OPENING_SPIKE_MULT", 1.12)),
        "min_score_add": float(getattr(cfg, "RTH_OPENING_SCORE_ADD", 6)),
        "min_conf_add": float(getattr(cfg, "RTH_OPENING_CONF_ADD", 0.04)),
    }


def apply_opening_entry_adjustments(
    cfg: BotConfig,
    *,
    scan_score: float,
    spike_ratio: float,
    min_score: float,
    min_spike: float,
) -> Tuple[float, float, str]:
    """Return adjusted thresholds and note for logs."""
    adj = opening_entry_adjustments(cfg)
    if not adj:
        return min_score, min_spike, ""
    ms = min_score + adj.get("min_score_add", 0)
    sp = min_spike * adj.get("min_spike_mult", 1.0)
    note = (
        f"RTH opening window (+{adj.get('min_score_add', 0):.0f} score, "
        f"×{adj.get('min_spike_mult', 1):.2f} spike)"
    )
    return ms, sp, note


def realtime_bars_use_rth(cfg: BotConfig) -> bool:
    """IB reqRealTimeBars useRTH — paper often needs useRTH=False for live 5s bars."""
    if getattr(cfg, "PAPER_TRADING", False):
        return bool(getattr(cfg, "PAPER_REALTIME_BARS_USE_RTH", False))
    if not bool(getattr(cfg, "REALTIME_BARS_USE_RTH_WHEN_OPEN", True)):
        return False
    return is_rth(cfg)


def historical_prefetch_allowed(cfg: BotConfig) -> bool:
    """Whether HMDS historical warm is appropriate now."""
    if not getattr(cfg, "SCALPER_LIVE_BARS_FIRST", True):
        return True
    if bool(getattr(cfg, "SKIP_HMDS_OUTSIDE_RTH", True)) and not is_rth(cfg):
        return False
    if getattr(cfg, "PAPER_TRADING", False) and not getattr(
        cfg, "PAPER_USE_HISTORICAL_BARS", False,
    ):
        return False
    return True


def is_transient_md_failure(
    cfg: BotConfig,
    *,
    code: int,
    pattern: str,
    state: Optional[str] = None,
) -> bool:
    """HMDS 162 outside RTH is flaky — do not drop locks or permanent-blacklist."""
    if not bool(getattr(cfg, "MD_SOFT_FAIL_OUTSIDE_RTH", True)):
        return False
    state = state or get_market_state(cfg)
    if state == "open":
        return False
    if code == 162 and pattern == "no_historical_data":
        return True
    return False


def rth_status_line(cfg: Optional[BotConfig] = None) -> str:
    cfg = cfg or BotConfig()
    tier = rth_tier(cfg)
    if tier == "rth_opening":
        secs = seconds_since_rth_open(cfg) or 0
        return f"RTH OPENING ALERT — {secs / 60:.0f}m since bell (noise filter ON)"
    if tier == "rth":
        return "RTH ALERT — full regular session (live-money mode)"
    if is_pre_rth_countdown(cfg):
        secs = seconds_until_rth_open(cfg) or 0
        return f"PRE-OPEN COUNTDOWN — RTH in {secs:.0f}s"
    return ""


def ai_session_context_block(cfg: BotConfig) -> str:
    """Prompt block so Ollama/council knows current session tier."""
    tier = rth_tier(cfg)
    lines = [f"US session tier: {tier} | ET {now_et().strftime('%H:%M')}"]
    if tier == "rth_opening":
        lines.append(
            "OPENING WINDOW: high noise, fakeouts common — require stronger spike+score; "
            "stay super alert on exits and spreads."
        )
    elif tier == "rth":
        lines.append(
            "REGULAR HOURS: trade as live capital — fast monitor, full streams, IB scanner live."
        )
    elif tier == "pre_market":
        lines.append(
            "PRE-MARKET: thin liquidity — prefer stream bars; HMDS may fail until 09:30."
        )
    return " | ".join(lines)
