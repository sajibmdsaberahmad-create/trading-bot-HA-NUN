#!/usr/bin/env python3
"""
core/account_view.py — Single source for display equity and session P&L.

IB NetLiquidation is economic truth when fill sync is on; war account sizes entries;
bot_nav is internal bookkeeping only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from core.fill_tracker import require_ib_fill_sync

if TYPE_CHECKING:
    from core.config import BotConfig
    from core.scalper_runner import ScalperRunner


def day_pnl_ib(runner: "ScalperRunner") -> Tuple[float, float]:
    """Session P&L from IB NetLiquidation change."""
    ib_start = float(getattr(runner, "_ib_starting_balance", 0) or runner.account_equity)
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
    return {
        "equity": round(display_equity(runner, cfg), 2),
        "sizing_equity": round(sizing_equity(runner, cfg), 2),
        "ib_equity": round(float(getattr(runner, "account_equity", 0) or 0), 2),
        "bot_nav": round(float(getattr(runner, "bot_nav", 0) or 0), 2),
        "day_pnl": round(pnl_usd, 2),
        "day_pnl_pct": round(pnl_pct, 2),
        "ib_change": round(ib_chg, 2),
        "ib_change_pct": round(ib_pct, 2),
        "cash": round(
            float(getattr(runner, "available_cash", 0) or getattr(runner, "bot_cash", 0) or 0),
            2,
        ),
    }
