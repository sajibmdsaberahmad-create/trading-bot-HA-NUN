#!/usr/bin/env python3
"""
core/position_intel.py — Open positions + account risk for commander / Telegram.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.scalper_runner import ScalperRunner


def _ib_long_positions(runner: "ScalperRunner") -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
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
    """Merge bot slot state with IB account positions."""
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

        entry = float(ib.get("avg_cost") or slot.get("entry_fill_px") or slot.get("entry_price") or 0)
        px = runner._live_price_for(ticker, entry)
        if px <= 0:
            px = entry

        market_value = shares * px
        unrealized = (px - entry) * shares if entry > 0 else 0.0
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

    equity = float(getattr(runner, "account_equity", 0) or 0)
    cash = float(getattr(runner, "available_cash", 0) or getattr(runner, "bot_cash", 0) or 0)
    ib_chg, _ = (0.0, 0.0)
    try:
        ib_chg, _ = runner._day_pnl_ib()
    except Exception:
        pass

    return {
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "nav": round(getattr(runner, "bot_nav", 0) or equity, 2),
        "ib_day_pnl": round(ib_chg, 2),
        "position_count": len(positions),
        "total_market_value": round(total_value, 2),
        "total_unrealized_pnl": round(total_unrealized, 2),
        "total_stop_risk_usd": round(total_risk_usd, 2),
        "deployed_pct": round(total_value / equity * 100, 2) if equity > 0 else 0.0,
        "positions": positions,
    }


def collect_risk(runner: "ScalperRunner") -> Dict[str, Any]:
    intel = collect_positions(runner)
    risk = getattr(runner, "risk", None)
    equity = intel["equity"]

    daily_start = float(getattr(risk, "start_of_day_equity", equity) or equity) if risk else equity
    weekly_start = float(getattr(risk, "start_of_week_equity", equity) or equity) if risk else equity

    halted = False
    halt_reason = ""
    consecutive = 0
    if risk:
        try:
            halted = risk.is_halted()
            halt_reason = getattr(risk, "_halt_reason", "") or ""
            consecutive = int(getattr(risk, "_consecutive_losses", 0) or 0)
        except Exception:
            pass

    cfg = runner.cfg
    max_positions = 1
    try:
        from core.pilot_mode import effective_max_concurrent_positions
        max_positions = effective_max_concurrent_positions(cfg)
    except Exception:
        max_positions = int(getattr(cfg, "MAX_CONCURRENT_POSITIONS", 1) or 1)

    return {
        **intel,
        "halted": halted,
        "halt_reason": halt_reason,
        "consecutive_losses": consecutive,
        "daily_start_equity": round(daily_start, 2),
        "daily_pnl": round(equity - daily_start, 2),
        "weekly_start_equity": round(weekly_start, 2),
        "weekly_pnl": round(equity - weekly_start, 2),
        "max_positions": max_positions,
        "slots_used": intel["position_count"],
        "win_rate_pct": round(getattr(risk, "win_rate", 0) * 100, 1) if risk else 0.0,
        "trades_today": int(getattr(runner, "trades_today", 0) or 0),
    }


def format_positions_report(intel: Dict[str, Any], *, max_positions: int = 12) -> str:
    lines = [
        "📊 OPEN POSITIONS",
        f"IB ${intel.get('equity', 0):,.2f} · Day P&L ${intel.get('ib_day_pnl', 0):+,.2f} · "
        f"Cash ${intel.get('cash', 0):,.2f}",
        f"Deployed ${intel.get('total_market_value', 0):,.0f} "
        f"({intel.get('deployed_pct', 0):.1f}%) · "
        f"Unrealized ${intel.get('total_unrealized_pnl', 0):+,.2f}",
        f"Stop risk (booked): ${intel.get('total_stop_risk_usd', 0):,.0f} · "
        f"{intel.get('position_count', 0)} position(s)",
        "",
    ]

    for p in intel.get("positions", [])[:max_positions]:
        tag = "🤖" if p.get("bot_managed") else "📎"
        if p.get("ib_only"):
            tag = "IB"
        stop_s = f"stop ${p['stop']:.2f}" if p.get("stop") else "no stop"
        tgt_s = f"tgt ${p['target']:.2f}" if p.get("target") else ""
        lines.append(
            f"{tag} {p['ticker']} {p['shares']:,}sh @ ${p['entry']:.2f} → ${p['price']:.2f} "
            f"({p.get('unrealized_pct', 0):+.1f}% · ${p.get('unrealized_pnl', 0):+,.0f})"
        )
        extra = " · ".join(x for x in (stop_s, tgt_s) if x)
        if extra:
            lines.append(f"   {extra}")

    extra_n = intel.get("position_count", 0) - max_positions
    if extra_n > 0:
        lines.append(f"… +{extra_n} more (use /system for full dump)")

    if not intel.get("positions"):
        lines.append("Flat — no open long positions.")

    return "\n".join(lines)


def format_risk_report(risk: Dict[str, Any]) -> str:
    status = "⛔ HALTED" if risk.get("halted") else "✅ ACTIVE"
    lines = [
        "🛡 ACCOUNT RISK",
        f"Status: {status}",
        f"Equity ${risk.get('equity', 0):,.2f} · Daily P&L ${risk.get('daily_pnl', 0):+,.2f} · "
        f"Weekly ${risk.get('weekly_pnl', 0):+,.2f}",
        f"Deployed {risk.get('deployed_pct', 0):.1f}% · "
        f"Unrealized ${risk.get('total_unrealized_pnl', 0):+,.2f} · "
        f"Stop risk ${risk.get('total_stop_risk_usd', 0):,.0f}",
        f"Slots {risk.get('slots_used', 0)}/{risk.get('max_positions', 1)} · "
        f"Trades today {risk.get('trades_today', 0)} · "
        f"Consecutive losses {risk.get('consecutive_losses', 0)}",
    ]
    if risk.get("halted") and risk.get("halt_reason"):
        lines.append(f"Reason: {risk['halt_reason'][:200]}")
    return "\n".join(lines)
