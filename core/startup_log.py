"""
Compact startup logging — one structured banner instead of dozens of INFO lines.
Set STARTUP_LOG_COMPACT=false for the full verbose boot trace.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig

_quiet_phase: bool = False


def startup_compact(cfg: Optional["BotConfig"] = None) -> bool:
    if cfg is not None:
        return getattr(cfg, "STARTUP_LOG_COMPACT", True)
    return True


def set_quiet_phase(active: bool) -> None:
    global _quiet_phase
    _quiet_phase = active


def in_quiet_phase() -> bool:
    return _quiet_phase


def sinfo(cfg: Optional["BotConfig"], msg: str, *, force: bool = False) -> None:
    """INFO when verbose boot; DEBUG when compact (unless force=True)."""
    if force or not startup_compact(cfg):
        log.info(msg)
    else:
        log.debug(msg)


def slog(msg: str, *, force: bool = False) -> None:
    """INFO during boot unless quiet phase or compact default."""
    if force or not _quiet_phase:
        log.info(msg)
    else:
        log.debug(msg)


def log_block(title: str, lines: List[str], *, width: int = 62) -> None:
    bar = "─" * width
    log.info(bar)
    log.info(f"  {title}")
    for line in lines:
        if line:
            log.info(f"  {line}")
    log.info(bar)


def engine_mode_label(cli_mode: str) -> str:
    """Human label for main.py --mode (scalper hull = scalp + swing life engine)."""
    m = (cli_mode or "").lower().strip()
    if m in ("scalper", "replay-live"):
        return "Life Engine · scalp + swing"
    if m == "trade":
        return "Legacy PPO trade"
    if m == "warmup":
        return "PPO warmup"
    if m == "evaluate":
        return "Backtest evaluate"
    if m.startswith("fusion"):
        return "Fusion multi-model"
    return m.replace("-", " ").title() or "unknown"


def horizons_line(cfg: Optional["BotConfig"] = None) -> str:
    cfg = cfg or __import__("core.config", fromlist=["BotConfig"]).BotConfig()
    parts = ["scalp (scanner)"]
    try:
        from core.trade_horizon import swing_ib_live_enabled, swing_shadow_enabled
        if swing_ib_live_enabled(cfg):
            parts.append("swing IB live")
        elif swing_shadow_enabled(cfg):
            parts.append("swing shadow")
    except Exception:
        if os.getenv("SWING_IB_LIVE", "true").lower() in ("1", "true", "yes"):
            parts.append("swing IB")
    return "Horizons: " + " · ".join(parts)


def capital_line(cfg: Optional["BotConfig"] = None, runner: Any = None) -> str:
    """Phase-aware capital — IB equity when known, not misleading INITIAL_CASH."""
    cfg = cfg or __import__("core.config", fromlist=["BotConfig"]).BotConfig()
    eq = 0.0
    if runner is not None:
        eq = float(getattr(runner, "account_equity", 0) or 0)
    if eq <= 0:
        try:
            from core.ib_truth import get_snapshot
            snap = get_snapshot()
            if snap.refreshed_at > 0:
                eq = float(snap.account.net_liquidation or 0)
        except Exception:
            pass
    try:
        from core.capital_phase import capital_phases_enabled, capital_phase, uses_war_sizing
        if capital_phases_enabled(cfg):
            war_usd = float(os.getenv("WAR_CAPITAL_USD", "1000"))
            phase = capital_phase(cfg, runner) if runner is not None else "?"
            sizing = "war pool" if (runner and uses_war_sizing(cfg, runner)) else "full IB"
            base = f"Capital: {sizing} · phase={phase}"
            if eq > 0:
                return f"{base} · IB NetLiq=${eq:,.0f} (war ~${war_usd:,.0f} @ RTH)"
            return f"{base} · war ~${war_usd:,.0f} @ RTH · full IB pre/post war"
    except Exception:
        pass
    if eq > 0:
        return f"Capital: IB NetLiq=${eq:,.0f}"
    return f"Capital: ${float(getattr(cfg, 'INITIAL_CASH', 0) or 0):,.0f} (config — IB sync at connect)"


def ib_connect_line(cfg: Optional["BotConfig"] = None) -> str:
    cfg = cfg or __import__("core.config", fromlist=["BotConfig"]).BotConfig()
    paper = "paper" if getattr(cfg, "PAPER_TRADING", True) else "LIVE"
    return f"IB Gateway: port={cfg.IB_PORT} client={cfg.IB_CLIENT_ID} ({paper})"


def log_launch_banner(cfg: Optional["BotConfig"], cli_mode: str) -> None:
    """Pre-connect launch summary — no duplicate product name, no fixed SPY ticker."""
    log_block(
        "HANOON LAUNCH",
        [
            f"Engine: {engine_mode_label(cli_mode)}",
            horizons_line(cfg),
            capital_line(cfg),
            ib_connect_line(cfg),
            "Universe: IB scanner (not a single fixed ticker)",
        ],
    )


def build_session_ready_lines(runner: Any) -> List[str]:
    """Post-IB-connect session summary for the life engine."""
    cfg = getattr(runner, "cfg", None)
    from core.data import tick_by_tick_type
    from core.market_hours import allowed_trading_sessions_label, can_trade_now, get_market_state

    account = "unknown"
    try:
        from core.ib_truth import get_snapshot
        snap = get_snapshot()
        if snap.refreshed_at > 0 and snap.account.account_code:
            account = snap.account.account_code
        elif snap.refreshed_at > 0:
            account = "IB"
    except Exception:
        pass
    if account == "unknown":
        try:
            acct_vals = runner.conn.ib.accountValues()
            account = acct_vals[0].account if acct_vals else "unknown"
        except Exception:
            pass

    mode = "PAPER" if getattr(cfg, "PAPER_TRADING", True) else "LIVE"
    market_state = get_market_state(cfg)
    can_trade, _ = can_trade_now(cfg)
    sessions = allowed_trading_sessions_label(cfg)

    paper_rt = bool(
        getattr(cfg, "PAPER_TRADING", False)
        and getattr(cfg, "PAPER_REALTIME_BARS_ONLY", False)
    )
    if paper_rt:
        md_mode = "5s bars (paper)"
    elif getattr(cfg, "USE_TICK_STREAM", True):
        md_mode = f"tick ({tick_by_tick_type(cfg)})"
    else:
        md_mode = "5s bars"

    defer = getattr(cfg, "SCAN_DEFER_IB_ON_STARTUP", False)
    from core.scanner_session import scanner_session_log_line
    scan_mode = f"deferred curated" if defer else scanner_session_log_line(cfg)

    council_on = getattr(cfg, "COUNCIL_ENABLED", False)
    council = (
        f"{getattr(cfg, 'COUNCIL_BACKEND', 'groq')}"
        if council_on else "off"
    )

    from core.fast_execution import max_realtime_bar_streams, tick_stream_count

    lines = [
        f"{mode} · account {account}",
        capital_line(cfg, runner),
        horizons_line(cfg),
        f"Market: {market_state} · tradable={'yes' if can_trade else 'no'} · {sessions}",
        f"Scanner: {scan_mode} · data: {md_mode} · council: {council}",
        f"PPO: {'loaded' if not getattr(runner, '_model_fresh', True) else 'fresh'} · "
        f"streams {tick_stream_count(cfg)} tick + {max_realtime_bar_streams(cfg)} bar",
    ]
    if hasattr(runner, "pilot"):
        vs = runner.pilot.get_veteran_status()
        lines.append(
            f"Pilot: {vs.get('level', '?')} · XP={vs.get('total_xp', 0)} · "
            f"conf={vs.get('confidence_threshold', 0):.0%}"
        )
    try:
        from core.ai_session_limits import format_limits_log, should_ai_define_limits
        if should_ai_define_limits(cfg):
            lines.append(format_limits_log(cfg, runner.account_equity))
    except Exception:
        pass
    return lines
