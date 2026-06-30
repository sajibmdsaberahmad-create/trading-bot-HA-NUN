#!/usr/bin/env python3
"""
core/account_view.py — Display equity and session P&L from IB Truth.

All bots/AI/Telegram read through here. IB Gateway is economic truth when
REQUIRE_IB_FILL_SYNC=true (default). War virtual $1k pool sizes entries only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from core.fill_tracker import require_ib_fill_sync
from core.ib_truth import day_pnl_from_snapshot, get_snapshot, ib_truth_enabled

if TYPE_CHECKING:
    from core.config import BotConfig
    from core.scalper_runner import ScalperRunner


def day_pnl_ib(runner: "ScalperRunner") -> Tuple[float, float]:
    """Session P&L from IB Truth (RTH FIFO fills, else NetLiq delta since RTH open)."""
    rth_start = float(getattr(runner, "_rth_starting_balance", 0) or 0)
    ib_start = rth_start or float(
        getattr(runner, "_ib_starting_balance", 0) or runner.account_equity
    )
    if ib_truth_enabled(getattr(runner, "cfg", None)):
        snap = get_snapshot()
        if snap.refreshed_at > 0:
            return day_pnl_from_snapshot(snap, ib_start)
    equity = float(getattr(runner, "account_equity", 0) or 0)
    change = equity - ib_start
    pct = (change / ib_start * 100.0) if ib_start > 0 else 0.0
    return change, pct


def display_equity(runner: "ScalperRunner", cfg: Optional["BotConfig"] = None) -> float:
    """Equity shown in risk/Telegram — IB when sync on, else bot_nav."""
    cfg = cfg or getattr(runner, "cfg", None)
    if require_ib_fill_sync(cfg):
        eq = float(getattr(runner, "account_equity", 0) or 0)
        if eq > 0:
            return eq
    nav = float(getattr(runner, "bot_nav", 0) or 0)
    if nav > 0:
        return nav
    return float(getattr(runner, "bot_cash", 0) or 0)


def sizing_equity(runner: "ScalperRunner", cfg: Optional["BotConfig"] = None) -> float:
    """Capital used for position sizing (war ledger when enabled)."""
    cfg = cfg or getattr(runner, "cfg", None)
    try:
        from core.war_account import war_account_enabled, war_effective_equity
        if war_account_enabled(cfg):
            eq = war_effective_equity(cfg)
            if eq > 0:
                return float(eq)
    except Exception:
        pass
    return display_equity(runner, cfg)


def day_pnl(
    runner: "ScalperRunner",
    cfg: Optional["BotConfig"] = None,
) -> Tuple[float, float]:
    """Session P&L (usd, pct) — IB change when fill sync on."""
    cfg = cfg or getattr(runner, "cfg", None)
    if require_ib_fill_sync(cfg):
        return day_pnl_ib(runner)
    baseline = float(getattr(cfg, "INITIAL_CASH", 1000) if cfg else 1000)
    nav = float(getattr(runner, "bot_nav", 0) or baseline)
    pnl = nav - baseline
    pct = (pnl / baseline * 100.0) if baseline > 0 else 0.0
    return pnl, pct


def account_summary(runner: "ScalperRunner") -> Dict[str, Any]:
    """Compact dict for notifications and commander context."""
    cfg = getattr(runner, "cfg", None)
    pnl_usd, pnl_pct = day_pnl(runner, cfg)
    ib_chg, ib_pct = day_pnl_ib(runner)
    snap = get_snapshot()
    ib_realized = snap.account.realized_pnl if snap.refreshed_at > 0 else 0.0
    ib_unrealized = snap.account.unrealized_pnl if snap.refreshed_at > 0 else 0.0
    return {
        "equity": round(display_equity(runner, cfg), 2),
        "sizing_equity": round(sizing_equity(runner, cfg), 2),
        "ib_equity": round(float(getattr(runner, "account_equity", 0) or 0), 2),
        "bot_nav": round(float(getattr(runner, "bot_nav", 0) or 0), 2),
        "day_pnl": round(pnl_usd, 2),
        "day_pnl_pct": round(pnl_pct, 2),
        "ib_change": round(ib_chg, 2),
        "ib_change_pct": round(ib_pct, 2),
        "ib_realized_pnl": round(ib_realized, 2),
        "ib_unrealized_pnl": round(ib_unrealized, 2),
        "ib_fifo_session_pnl": round(snap.session_pnl_fifo, 2) if snap.refreshed_at > 0 else 0.0,
        "ib_session_pnl": round(snap.session_pnl_ib, 2) if snap.refreshed_at > 0 else 0.0,
        "ib_open_orders": len(snap.open_orders) if snap.refreshed_at > 0 else 0,
        "cash": round(
            float(getattr(runner, "available_cash", 0) or getattr(runner, "bot_cash", 0) or 0),
            2,
        ),
        "ib_truth": snap.refreshed_at > 0,
    }
