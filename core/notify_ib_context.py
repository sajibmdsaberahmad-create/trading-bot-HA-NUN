#!/usr/bin/env python3
"""
core/notify_ib_context.py — Telegram notification context from IB Truth only.

All economic fields (equity, session P&L, fills, position marks) for outbound
alerts come from IB Gateway snapshot — never bot_nav, stream ticks, or composer
local accumulators.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from core.account_view import account_summary, day_pnl
from core.ib_truth import get_snapshot, ib_truth_enabled, refresh

if TYPE_CHECKING:
    from core.config import BotConfig
    from core.scalper_runner import ScalperRunner


def _refresh_snapshot(runner: "ScalperRunner", cfg: Optional["BotConfig"] = None) -> None:
    cfg = cfg or getattr(runner, "cfg", None)
    if not ib_truth_enabled(cfg):
        return
    try:
        ib = getattr(runner, "ib", None)
        if ib is not None:
            refresh(ib, cfg, force=False)
    except Exception:
        pass


def ib_telegram_account(runner: "ScalperRunner", cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """Account + session block — IB Truth snapshot only."""
    cfg = cfg or getattr(runner, "cfg", None)
    _refresh_snapshot(runner, cfg)
    acct = account_summary(runner)
    snap = get_snapshot()
    round_trips = len(snap.round_trips) if snap.refreshed_at > 0 else 0
    ib_eq = float(acct.get("ib_equity") or acct.get("equity") or 0)
    session_pnl = float(acct.get("ib_fifo_session_pnl", acct.get("day_pnl", 0)) or 0)
    deployed_pct = 0.0
    if snap.refreshed_at > 0 and ib_eq > 0:
        mv = sum(
            abs(p.qty) * (p.market_price or p.avg_cost)
            for p in snap.long_positions().values()
        )
        deployed_pct = round(mv / ib_eq * 100, 2)

    open_syms = list(snap.long_positions().keys()) if snap.refreshed_at > 0 else []
    primary = open_syms[0] if len(open_syms) == 1 else (open_syms[0] if open_syms else None)
    primary_qty = 0.0
    if primary and snap.refreshed_at > 0:
        primary_qty = float(snap.long_positions()[primary].qty)

    return {
        "pnl_source": "ib_truth",
        "ib_truth": snap.refreshed_at > 0,
        "nav": ib_eq,
        "equity": ib_eq,
        "ib_account": ib_eq,
        "ib_equity": ib_eq,
        "day_pnl": session_pnl,
        "ib_change": float(acct.get("ib_change", session_pnl) or 0),
        "ib_fifo_session_pnl": session_pnl,
        "session_pnl": session_pnl,
        "ib_realized_pnl": float(acct.get("ib_realized_pnl", 0) or 0),
        "ib_unrealized_pnl": float(acct.get("ib_unrealized_pnl", 0) or 0),
        "ib_round_trips": round_trips,
        "session_trades": round_trips,
        "trades_today": round_trips,
        "deployed_pct": deployed_pct,
        "position": primary,
        "shares": primary_qty,
        "open_positions": len(open_syms),
        "session_scope": snap.session_scope if snap.refreshed_at > 0 else "",
    }


def _reconcile_trade_fields(
    extra: Dict[str, Any],
    *,
    event_type: str = "",
) -> Dict[str, Any]:
    """Align per-trade fields with IB position / FIFO when snapshot is fresh."""
    snap = get_snapshot()
    if snap.refreshed_at <= 0:
        return extra
    ticker = str(extra.get("ticker") or "").upper()
    out = dict(extra)

    if event_type == "trade_opened" and ticker:
        pos = snap.long_positions().get(ticker)
        if pos and pos.qty > 0:
            fill_px = float(pos.avg_cost or 0)
            sh = int(pos.qty)
            mkt = float(pos.market_price or fill_px)
            out["shares"] = sh
            out["entry"] = round(fill_px, 4)
            out["price"] = round(fill_px, 4)
            out["deployed"] = round(sh * mkt, 2)
            out["ib_mark"] = round(mkt, 4)
            out["pnl_source"] = "ib_fill"

    if event_type in ("trade_closed", "early_exit", "profit_hunt"):
        if out.get("fill_confirmed") or out.get("exit_fill_confirmed"):
            out["pnl_source"] = "ib_fill"
        if ticker:
            trip_pnl = snap.ticker_pnl_fifo.get(ticker)
            if trip_pnl is not None and out.get("pnl_usd") is None:
                out["pnl_usd"] = float(trip_pnl)
        out["session_pnl"] = float(snap.session_pnl_fifo)
        out["ib_fifo_session_pnl"] = float(snap.session_pnl_fifo)
        out["session_trades"] = len(snap.round_trips)

    return out


def telegram_notify_context(
    runner: "ScalperRunner",
    cfg: Optional["BotConfig"] = None,
    extra: Optional[Dict[str, Any]] = None,
    *,
    event_type: str = "",
) -> Dict[str, Any]:
    """
    Full Telegram alert context — IB account block + optional trade extras.
    Local bot_nav / bot_cash / stream prices are never included.
    """
    cfg = cfg or getattr(runner, "cfg", None)
    ctx = ib_telegram_account(runner, cfg)
    try:
        from core.rth_session import rth_reply_context
        ctx.update(rth_reply_context(cfg))
    except Exception:
        pass
    if hasattr(runner, "pilot"):
        try:
            ctx.update(runner.pilot.get_veteran_status())
        except Exception:
            pass
    if getattr(runner, "top_pick", None):
        ctx["top_pick"] = runner.top_pick.ticker
        ctx["top_score"] = runner.top_pick.rank_score
    locked = getattr(runner, "_locked_targets", None) or []
    if locked:
        ctx["locked"] = [t.ticker for t in locked[:5]]
    try:
        from core.war_account import war_account_context
        ctx.update(war_account_context(cfg))
    except Exception:
        pass
    if extra:
        merged = _reconcile_trade_fields(extra, event_type=event_type)
        ctx.update(merged)
        # Re-apply IB session totals after trade merge (never local trade counters)
        snap = get_snapshot()
        if snap.refreshed_at > 0:
            ctx["session_pnl"] = float(snap.session_pnl_fifo)
            ctx["ib_fifo_session_pnl"] = float(snap.session_pnl_fifo)
            ctx["session_trades"] = len(snap.round_trips)
            ctx["trades_today"] = len(snap.round_trips)
    ctx["_runner"] = runner
    ctx["data_source"] = "ib_truth"
    return ctx


def merge_ib_telegram_context(
    runner: "ScalperRunner",
    cfg: Optional["BotConfig"] = None,
    ctx: Optional[Dict[str, Any]] = None,
    *,
    event_type: str = "",
) -> Dict[str, Any]:
    """
    Overlay IB economics onto an existing partial context (copilot / commander replies).
    Strips local bot_nav / bot_cash fiction before merge.
    """
    incoming = dict(ctx or {})
    for key in ("bot_nav", "bot_cash"):
        incoming.pop(key, None)
    base = ib_telegram_account(runner, cfg)
    incoming.update(base)
    if event_type:
        incoming.update(_reconcile_trade_fields(incoming, event_type=event_type))
        snap = get_snapshot()
        if snap.refreshed_at > 0:
            incoming["session_pnl"] = float(snap.session_pnl_fifo)
            incoming["ib_fifo_session_pnl"] = float(snap.session_pnl_fifo)
            incoming["trades_today"] = len(snap.round_trips)
    incoming["_runner"] = runner
    incoming["data_source"] = "ib_truth"
    return incoming
