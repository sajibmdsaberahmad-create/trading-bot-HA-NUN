#!/usr/bin/env python3
"""
core/position_intel.py — Open positions + account risk for commander / Telegram.

All position counts, entry prices, and unrealized P&L come from IB Truth
(core/ib_truth.py) — not local slot fiction.
"""

from __future__ import annotations

from typing import Any, Dict, List, TYPE_CHECKING

from core.ib_truth import get_snapshot, ib_truth_enabled, position_entry_from_truth
from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner


def _ib_long_positions(runner: "ScalperRunner") -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    if ib_truth_enabled(getattr(runner, "cfg", None)):
        snap = get_snapshot()
        if snap.refreshed_at > 0:
            for sym, pos in snap.long_positions().items():
                out[sym] = {
                    "shares": pos.qty,
                    "avg_cost": pos.avg_cost,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "market_price": pos.market_price,
                }
            return out
    try:
        runner.ib.reqPositions()
        runner.ib.sleep(0.3)
        for p in runner.ib.positions():
            sym = (getattr(p.contract, "symbol", "") or "").upper()
            qty = float(p.position)
            if not sym or qty <= 0:
                continue
            out[sym] = {
                "shares": qty,
                "avg_cost": float(getattr(p, "avgCost", 0) or 0),
            }
    except Exception as exc:
        log.debug(f"position_intel IB snapshot: {exc}")
    return out


def collect_positions(runner: "ScalperRunner") -> Dict[str, Any]:
    """Merge bot slot state with IB Truth positions."""
    try:
        runner._sync_all_positions_from_ib()
    except Exception:
        pass

    slots = getattr(runner, "_position_slots", {}) or {}
    ib_map = _ib_long_positions(runner)
    tickers = sorted(set(slots.keys()) | set(ib_map.keys()))

    positions: List[Dict[str, Any]] = []
    total_value = 0.0
    total_unrealized = 0.0
    total_risk_usd = 0.0

    for ticker in tickers:
        slot = slots.get(ticker, {})
        ib = ib_map.get(ticker, {})
        ib_sh = float(ib.get("shares") or 0)
        slot_sh = float(slot.get("shares") or 0)
        session_sh = float(slot.get("session_shares", 0) or slot_sh)
        if ib_sh > 0:
            shares = ib_sh
            if session_sh > 0 and ticker in slots:
                shares = min(ib_sh, session_sh)
        else:
            shares = slot_sh if slot.get("ib_fill_confirmed") else 0.0
        if shares < 0.5:
            continue

        entry = float(
            ib.get("avg_cost")
            or position_entry_from_truth(runner.ib, ticker)
            or slot.get("entry_fill_px")
            or slot.get("entry_price")
            or 0
        )
        ib_unreal = float(ib.get("unrealized_pnl") or 0)
        mkt_px = float(ib.get("market_price") or 0)
        px = runner._live_price_for(ticker, mkt_px or entry)
        if px <= 0:
            px = mkt_px or entry

        market_value = shares * px
        unrealized = ib_unreal if ib_unreal != 0 else ((px - entry) * shares if entry > 0 else 0.0)
        stop = float(slot.get("stop") or 0)
        target = float(slot.get("target") or 0)
        peak = float(slot.get("peak") or px)
        hard_floor = float(slot.get("hard_floor") or 0)

        stop_risk = 0.0
        if stop > 0 and entry > 0:
            stop_risk = max(0.0, (entry - stop) * shares)

        positions.append({
            "ticker": ticker,
            "shares": int(shares),
            "entry": round(entry, 4),
            "price": round(px, 4),
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pct": round((px / entry - 1) * 100, 2) if entry > 0 else 0.0,
            "stop": round(stop, 4) if stop else None,
            "target": round(target, 4) if target else None,
            "peak": round(peak, 4) if peak else None,
            "hard_floor": round(hard_floor, 4) if hard_floor else None,
            "stop_risk_usd": round(stop_risk, 2),
            "bot_managed": ticker in slots,
            "ib_only": ticker not in slots and ticker in ib_map,
            "opened_at": slot.get("opened_at"),
        })
        total_value += market_value
        total_unrealized += unrealized
        total_risk_usd += stop_risk

    snap = get_snapshot()
    equity = float(snap.account.net_liquidation or getattr(runner, "account_equity", 0) or 0)
    cash = float(snap.account.total_cash or getattr(runner, "available_cash", 0) or getattr(runner, "bot_cash", 0) or 0)
    ib_chg = snap.session_pnl_fifo if snap.refreshed_at > 0 else 0.0
    if ib_chg == 0:
        try:
            from core.account_view import day_pnl_ib
            ib_chg, _ = day_pnl_ib(runner)
        except Exception:
            pass

    return {
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "nav": round(equity, 2),
        "ib_day_pnl": round(ib_chg, 2),
        "position_count": len(positions),
        "total_market_value": round(total_value, 2),
        "total_unrealized_pnl": round(total_unrealized, 2),
        "total_stop_risk_usd": round(total_risk_usd, 2),
        "deployed_pct": round(total_value / equity * 100, 2) if equity > 0 else 0.0,
        "positions": positions,
        "ib_truth": snap.refreshed_at > 0,
    }
