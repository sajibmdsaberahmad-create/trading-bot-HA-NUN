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


def rth_session_start_ts(cfg: Optional[BotConfig] = None) -> float:
    """09:30 ET today as unix timestamp — matches war RTH session reset."""
    return _today_rth_open(cfg).timestamp()


def rth_session_end_ts(cfg: Optional[BotConfig] = None) -> float:
    """16:00 ET today as unix timestamp."""
    return _today_rth_close(cfg).timestamp()


def execution_in_rth_window(ts: float, cfg: Optional[BotConfig] = None) -> bool:
    """True when IB execution time falls inside 09:30–16:00 ET on that calendar day."""
    if ts <= 0:
        return False
    dt = datetime.fromtimestamp(ts, tz=MARKET_TZ)
    open_dt = dt.replace(hour=RTH_OPEN_HHMM[0], minute=RTH_OPEN_HHMM[1], second=0, microsecond=0)
    close_dt = dt.replace(hour=RTH_CLOSE_HHMM[0], minute=RTH_CLOSE_HHMM[1], second=0, microsecond=0)
    return open_dt <= dt < close_dt


def ib_truth_session_start_ts(cfg: Optional[BotConfig] = None) -> float:
    """
    Session window for IB Truth FIFO PnL.
    Default: 09:30 ET (RTH bell) — aligned with war trip reset, not calendar midnight.
  """
    import os
    cfg = cfg or BotConfig()
    if os.getenv("IB_TRUTH_RTH_SESSION", "true").lower() in ("0", "false", "no"):
        from datetime import datetime, timezone
        try:
            now = now_et()
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start.timestamp()
        except Exception:
            today = datetime.now(timezone.utc).date()
            return datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()
    return rth_session_start_ts(cfg)


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
    message: str = "",
) -> bool:
    """HMDS 162 flakes (cancelled/timeout/inactive) — keep lock, use live streams."""
    if code == 162 and pattern == "no_historical_data":
        if is_hmds_transient_message(message):
            return bool(getattr(cfg, "MD_SOFT_FAIL_HMDS", True))
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


def rth_reply_context(cfg: Optional[BotConfig] = None) -> Dict[str, Any]:
    """Structured market context for Telegram/Halim — RTH-aware, not generic."""
    cfg = cfg or BotConfig()
    state = get_market_state(cfg)
    ctx: Dict[str, Any] = {
        "market_state": state,
        "rth_tier": rth_tier(cfg),
        "is_rth": is_rth(cfg),
        "time_et": now_et().strftime("%Y-%m-%d %H:%M %Z"),
        "session_window": "09:30-16:00 ET",
    }
    secs_open = seconds_since_rth_open(cfg)
    if secs_open is not None:
        ctx["minutes_since_rth_open"] = round(secs_open / 60.0, 1)
    secs_to = seconds_until_rth_open(cfg)
    if secs_to is not None and secs_to > 0:
        ctx["minutes_until_rth_open"] = round(secs_to / 60.0, 1)
    try:
        from core.ib_truth import get_snapshot
        snap = get_snapshot()
        if snap.refreshed_at > 0:
            ctx["session_scope"] = snap.session_scope
            ctx["ib_session_pnl"] = snap.session_pnl_ib
            ctx["ib_fifo_session_pnl"] = snap.session_pnl_fifo
            ctx["ib_realized_pnl"] = snap.account.realized_pnl
            ctx["ib_unrealized_pnl"] = snap.account.unrealized_pnl
            ctx["ib_open_orders"] = len(snap.open_orders)
    except Exception:
        pass
    if state == "open":
        ctx["market_note"] = (
            f"RTH live — PnL from IB RealizedPnL tag since 09:30 ET ({ctx.get('session_scope', 'rth')})."
        )
    elif state == "after_hours":
        ctx["market_note"] = (
            "After hours (RTH closed 16:00 ET). Report today's RTH session PnL only — no new entries."
        )
    elif state == "pre_market":
        ctx["market_note"] = "Pre-market — RTH opens 09:30 ET. Session PnL resets at the bell."
    else:
        ctx["market_note"] = "Market closed — cite last RTH session results, not live trading."
    return ctx
