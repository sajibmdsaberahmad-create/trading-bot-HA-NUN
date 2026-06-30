#!/usr/bin/env python3
"""
core/war_ib_sync.py — IB Gateway as source of truth for war ledger positions + PnL.

Fetches positions, fills, portfolio marks, and account values from IB (never local fiction).
War pool stays WAR_CAPITAL_USD ($1k default) — full IB paper NAV is monitor-only context.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config import BotConfig
from core.fill_tracker import normalize_ib_avg_cost, position_entry_price, round_trip_pnl
from core.notify import log

_REPO = Path(__file__).resolve().parents[1]
LEDGER_PATH = _REPO / "models" / "war_account_ledger.jsonl"


@dataclass
class IBExecution:
    symbol: str
    side: str
    price: float
    qty: float
    ts: float
    commission: float = 0.0
    exec_id: str = ""


@dataclass
class IBPosition:
    symbol: str
    qty: float
    avg_cost: float
    market_price: float = 0.0
    unrealized_pnl: float = 0.0
    multiplier: float = 1.0


@dataclass
class IBAccountSnapshot:
    net_liquidation: float = 0.0
    total_cash: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    gross_position_value: float = 0.0
    currency: str = "USD"


@dataclass
class RoundTrip:
    symbol: str
    entry_px: float
    exit_px: float
    shares: float
    entry_ts: float
    exit_ts: float
    commission: float = 0.0
    pnl_usd: float = 0.0


def war_ib_sync_enabled(cfg: Optional[BotConfig] = None) -> bool:
    return os.getenv("WAR_IB_SYNC", "true").lower() in ("1", "true", "yes")


def _execution_ts(ex) -> float:
    ts_raw = getattr(ex, "time", None)
    if ts_raw is None:
        return time.time()
    try:
        if hasattr(ts_raw, "timestamp"):
            return float(ts_raw.timestamp())
        return float(ts_raw)
    except Exception:
        return time.time()


def _fill_commission(fill) -> float:
    try:
        report = getattr(fill, "commissionReport", None)
        if report is not None:
            comm = float(getattr(report, "commission", 0) or 0)
            if comm != 0:
                return abs(comm)
    except Exception:
        pass
    return 0.0


def fetch_ib_executions(ib, *, since_ts: float = 0.0) -> List[IBExecution]:
    """All IB fills after since_ts — chronological."""
    out: List[IBExecution] = []
    try:
        for fill in ib.fills():
            contract = getattr(fill, "contract", None)
            sym = (getattr(contract, "symbol", "") or "").upper()
            ex = getattr(fill, "execution", None)
            if not sym or ex is None:
                continue
            px = float(getattr(ex, "price", 0) or 0)
            qty = float(getattr(ex, "shares", 0) or 0)
            if px <= 0 or qty <= 0:
                continue
            ts = _execution_ts(ex)
            if since_ts and ts < since_ts - 1.0:
                continue
            out.append(
                IBExecution(
                    symbol=sym,
                    side=str(getattr(ex, "side", "") or "").upper(),
                    price=px,
                    qty=qty,
                    ts=ts,
                    commission=_fill_commission(fill),
                    exec_id=str(getattr(ex, "execId", "") or ""),
                )
            )
    except Exception as exc:
        log.debug(f"fetch_ib_executions: {exc}")
    out.sort(key=lambda e: e.ts)
    return out


def fetch_ib_positions(ib) -> List[IBPosition]:
    """IB positions + portfolio marks when available."""
    portfolio_by_sym: Dict[str, Any] = {}
    try:
        for item in ib.portfolio():
            sym = (getattr(item.contract, "symbol", "") or "").upper()
            if sym:
                portfolio_by_sym[sym] = item
    except Exception:
        pass

    out: List[IBPosition] = []
    try:
        for p in ib.positions():
            sym = (getattr(p.contract, "symbol", "") or "").upper()
            if not sym:
                continue
            qty = float(getattr(p, "position", 0) or 0)
            if qty == 0:
                continue
            raw_avg = float(getattr(p, "avgCost", 0) or 0)
            mult = float(getattr(p.contract, "multiplier", 1) or 1)
            port = portfolio_by_sym.get(sym)
            mkt = float(getattr(port, "marketPrice", 0) or 0) if port else 0.0
            unreal = float(getattr(port, "unrealizedPNL", 0) or 0) if port else 0.0
            avg = normalize_ib_avg_cost(raw_avg, market_px=mkt, multiplier=mult)
            if avg <= 0 and mkt > 0:
                avg = mkt
            out.append(
                IBPosition(
                    symbol=sym,
                    qty=qty,
                    avg_cost=avg,
                    market_price=mkt,
                    unrealized_pnl=unreal,
                    multiplier=max(mult, 1.0),
                )
            )
    except Exception as exc:
        log.debug(f"fetch_ib_positions: {exc}")
    return out


def fetch_ib_account_snapshot(ib, currency: str = "USD") -> IBAccountSnapshot:
    snap = IBAccountSnapshot(currency=currency)
    try:
        for v in ib.accountValues():
            if getattr(v, "currency", "") != currency:
                continue
            tag = str(getattr(v, "tag", "") or "")
            val = float(getattr(v, "value", 0) or 0)
            if tag == "NetLiquidation":
                snap.net_liquidation = val
            elif tag == "TotalCashValue":
                snap.total_cash = val
            elif tag == "RealizedPnL":
                snap.realized_pnl = val
            elif tag == "UnrealizedPnL":
                snap.unrealized_pnl = val
            elif tag == "GrossPositionValue":
                snap.gross_position_value = val
    except Exception as exc:
        log.debug(f"fetch_ib_account_snapshot: {exc}")
    return snap


def session_start_ts_et() -> float:
    """Midnight US/Eastern today as unix ts."""
    try:
        from core.market_hours import now_et
        et = now_et()
        start = et.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp()
    except Exception:
        today = datetime.now(timezone.utc).date()
        return datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()


def fifo_round_trips(executions: List[IBExecution]) -> List[RoundTrip]:
    """FIFO match BOT/SLD per symbol — IB-grounded round trips."""
    by_sym: Dict[str, List[IBExecution]] = {}
    for ex in executions:
        by_sym.setdefault(ex.symbol, []).append(ex)

    trips: List[RoundTrip] = []
    for sym, rows in by_sym.items():
        lots: List[Tuple[float, float, float, float]] = []  # qty, px, ts, comm
        for ex in rows:
            if ex.side == "BOT":
                lots.append((ex.qty, ex.price, ex.ts, ex.commission))
            elif ex.side == "SLD":
                remaining = ex.qty
                exit_comm = ex.commission
                while remaining > 0.0001 and lots:
                    lot_qty, lot_px, lot_ts, lot_comm = lots[0]
                    matched = min(remaining, lot_qty)
                    comm = lot_comm * (matched / lot_qty) + exit_comm * (matched / ex.qty)
                    pnl, _ = round_trip_pnl(lot_px, ex.price, matched, commission=comm)
                    trips.append(
                        RoundTrip(
                            symbol=sym,
                            entry_px=lot_px,
                            exit_px=ex.price,
                            shares=matched,
                            entry_ts=lot_ts,
                            exit_ts=ex.ts,
                            commission=comm,
                            pnl_usd=pnl,
                        )
                    )
                    remaining -= matched
                    lot_qty -= matched
                    if lot_qty <= 0.0001:
                        lots.pop(0)
                    else:
                        lots[0] = (lot_qty, lot_px, lot_ts, lot_comm * (lot_qty / (lot_qty + matched)))
    trips.sort(key=lambda t: t.exit_ts)
    return trips


def read_war_ledger(*, since_ts: float = 0.0) -> List[Dict[str, Any]]:
    if not LEDGER_PATH.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if since_ts and float(row.get("ts", 0) or 0) < since_ts:
                continue
            rows.append(row)
    except Exception:
        pass
    return rows


def _max_war_notional(nav: float, cfg: Optional[BotConfig] = None) -> float:
    from core.war_account import _env_float
    pct = _env_float("WAR_IB_RECOVER_MAX_NAV_PCT", 0.90)
    return max(50.0, nav * pct)


def _align_operating_capital(state: Dict[str, Any], cfg: BotConfig) -> bool:
    """Snap war pool to WAR_CAPITAL_USD ($1k) when config differs from persisted state."""
    from core.war_account import _reconcile_war_cash_from_positions, operating_capital_usd

    cap = operating_capital_usd(cfg)
    old = float(state.get("operating_capital", cap) or cap)
    if abs(old - cap) < 0.5:
        return False
    session_pnl = float(state.get("session_pnl_war", 0) or 0)
    state["operating_capital"] = cap
    state["nav"] = round(cap + session_pnl, 2)
    _reconcile_war_cash_from_positions(state, cfg)
    log.info(
        f"⚔️ War capital aligned ${old:,.0f} → ${cap:,.0f} "
        f"(nav=${float(state.get('nav', 0)):,.0f} deployed=${float(state.get('deployed_usd', 0)):,.0f})"
    )
    return True


def sync_war_positions_from_ib(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Rewrite open_wars from IB long positions — prices/shares from IB only.
    Oversized vs war NAV → monitor-only (dropped from open_wars).
    """
    from core.war_account import (
        _commission_usd,
        _normalize_open_positions,
        _reconcile_war_cash_from_positions,
        load_state,
        operating_capital_usd,
    )

    cfg = cfg or BotConfig()
    state = state if state is not None else load_state(cfg)
    _normalize_open_positions(state)

    positions = fetch_ib_positions(ib)
    ib_long = {p.symbol: p for p in positions if p.qty > 0}
    nav = float(state.get("nav", operating_capital_usd(cfg)) or operating_capital_usd(cfg))
    max_notional = _max_war_notional(nav, cfg)

    wars: Dict[str, Any] = {}
    monitor_only: List[str] = []
    adopted: List[str] = []
    dropped: List[str] = []

    for sym, pos in ib_long.items():
        sh = int(abs(pos.qty))
        if sh <= 0:
            continue
        entry = position_entry_price(ib, sym, market_px=pos.market_price or pos.avg_cost)
        if entry <= 0:
            entry = pos.avg_cost
        notional = entry * sh
        if notional > max_notional:
            monitor_only.append(sym)
            continue
        comm = _commission_usd(cfg, notional)
        prev = (state.get("open_wars") or {}).get(sym) or {}
        wars[sym] = {
            "ticker": sym,
            "shares": sh,
            "entry": entry,
            "ib_fill": entry,
            "comm": comm,
            "ts": float(prev.get("ts") or time.time()),
            "pipeline": "ib_sync",
            "promotion": prev.get("promotion", ""),
            "synced": True,
        }
        if sym not in (state.get("open_wars") or {}):
            adopted.append(sym)

    for sym in list((state.get("open_wars") or {}).keys()):
        if sym not in wars:
            dropped.append(sym)

    state["open_wars"] = wars
    state["open_war"] = next(iter(wars.values()), None) if wars else None
    _reconcile_war_cash_from_positions(state, cfg)

    return {
        "ib_positions": len(ib_long),
        "war_slots": len(wars),
        "adopted": adopted,
        "dropped": dropped,
        "monitor_only": monitor_only,
    }


def sync_session_pnl_from_ib(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    state: Optional[Dict[str, Any]] = None,
    since_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Replay today's IB FIFO round trips into session_pnl_war + ticker_session_pnl."""
    from core.war_account import load_state

    cfg = cfg or BotConfig()
    state = state if state is not None else load_state(cfg)
    since = since_ts if since_ts is not None else session_start_ts_et()
    execs = fetch_ib_executions(ib, since_ts=since)
    trips = fifo_round_trips(execs)

    war_syms = set((state.get("open_wars") or {}).keys())
    ticker_pnl: Dict[str, float] = {}
    total = 0.0
    for trip in trips:
        ticker_pnl[trip.symbol] = round(ticker_pnl.get(trip.symbol, 0) + trip.pnl_usd, 2)
        total += trip.pnl_usd
        war_syms.add(trip.symbol)

    state["session_pnl_war"] = round(total, 2)
    state["ticker_session_pnl"] = ticker_pnl
    cap = float(state.get("operating_capital", 0) or 0)
    if cap > 0:
        state["nav"] = round(cap + total, 2)
        from core.war_account import _reconcile_war_cash_from_positions
        _reconcile_war_cash_from_positions(state, cfg)

    return {
        "since_ts": since,
        "executions": len(execs),
        "round_trips": len(trips),
        "session_pnl_war": round(total, 2),
        "tickers": ticker_pnl,
    }


def build_reconcile_report(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    since_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Compare war ledger vs IB — ghost exits, PnL drift, missing positions."""
    from core.war_account import LEDGER_PATH as WAR_LEDGER, STATE_PATH, load_state, operating_capital_usd

    cfg = cfg or BotConfig()
    state = load_state(cfg)
    since = since_ts if since_ts is not None else session_start_ts_et()

    account = fetch_ib_account_snapshot(ib, currency=getattr(cfg, "CURRENCY", "USD"))
    positions = fetch_ib_positions(ib)
    execs = fetch_ib_executions(ib, since_ts=since)
    trips = fifo_round_trips(execs)
    ledger = read_war_ledger(since_ts=since)

    ib_trip_pnl: Dict[str, float] = {}
    for t in trips:
        ib_trip_pnl[t.symbol] = round(ib_trip_pnl.get(t.symbol, 0) + t.pnl_usd, 2)

    war_exit_pnl: Dict[str, float] = {}
    war_entries: Dict[str, int] = {}
    ghost_exits: List[Dict[str, Any]] = []
    open_at_exit: Dict[str, bool] = {}

    for row in ledger:
        ev = row.get("event", "")
        sym = str(row.get("ticker", "") or "").upper()
        if ev in ("war_entry", "war_ib_recover"):
            war_entries[sym] = war_entries.get(sym, 0) + 1
            open_at_exit[sym] = True
        elif ev == "war_exit":
            pnl = float(row.get("net_pnl", 0) or 0)
            war_exit_pnl[sym] = round(war_exit_pnl.get(sym, 0) + pnl, 2)
            if not open_at_exit.get(sym):
                ghost_exits.append({
                    "ticker": sym,
                    "net_pnl": pnl,
                    "ts": row.get("ts"),
                    "ib_fill": row.get("ib_fill"),
                })
            open_at_exit[sym] = False

    pnl_drift: List[Dict[str, Any]] = []
    for sym in sorted(set(ib_trip_pnl) | set(war_exit_pnl)):
        ib_p = ib_trip_pnl.get(sym, 0)
        war_p = war_exit_pnl.get(sym, 0)
        delta = round(war_p - ib_p, 2)
        if abs(delta) > 5.0:
            pnl_drift.append({"ticker": sym, "war_pnl": war_p, "ib_pnl": ib_p, "delta": delta})

    ib_long = {p.symbol: p for p in positions if p.qty > 0}
    war_open = set((state.get("open_wars") or {}).keys())
    ib_only = [s for s in ib_long if s not in war_open]
    war_only = [s for s in war_open if s not in ib_long]

    outliers = [
        g for g in ghost_exits
        if abs(float(g.get("net_pnl", 0) or 0)) > 100
    ]

    return {
        "war_state_path": str(STATE_PATH),
        "war_ledger_path": str(WAR_LEDGER),
        "operating_capital_cfg": operating_capital_usd(cfg),
        "war_nav": float(state.get("nav", 0)),
        "war_session_pnl": float(state.get("session_pnl_war", 0)),
        "ib_account": {
            "net_liquidation": account.net_liquidation,
            "total_cash": account.total_cash,
            "realized_pnl": account.realized_pnl,
            "unrealized_pnl": account.unrealized_pnl,
        },
        "ib_positions": [
            {"symbol": p.symbol, "qty": p.qty, "avg_cost": p.avg_cost, "unrealized_pnl": p.unrealized_pnl}
            for p in positions
        ],
        "ib_round_trips_today": len(trips),
        "ib_session_pnl_fifo": round(sum(t.pnl_usd for t in trips), 2),
        "ghost_exits": ghost_exits,
        "pnl_outliers": outliers,
        "pnl_drift": pnl_drift,
        "ib_positions_not_in_war": ib_only,
        "war_slots_not_in_ib": war_only,
        "ok": not ghost_exits and not pnl_drift and not war_only,
    }


def sync_war_from_ib(
    ib,
    cfg: Optional[BotConfig] = None,
    *,
    apply: bool = False,
) -> Dict[str, Any]:
    """
    Full war sync from IB: align $1k cap, positions, session PnL.
    apply=True writes state + ledger event.
    """
    from core.war_account import _append_ledger, load_state, save_state, war_account_enabled

    cfg = cfg or BotConfig()
    if not war_account_enabled(cfg):
        return {"ok": False, "reason": "war_disabled"}

    state = load_state(cfg)
    cap_changed = _align_operating_capital(state, cfg)
    pos_result = sync_war_positions_from_ib(ib, cfg, state=state)
    pnl_result = sync_session_pnl_from_ib(ib, cfg, state=state)
    report = build_reconcile_report(ib, cfg)

    result = {
        "ok": True,
        "apply": apply,
        "capital_aligned": cap_changed,
        "positions": pos_result,
        "session_pnl": pnl_result,
        "reconcile": report,
    }

    if apply:
        save_state(state)
        _append_ledger({
            "event": "war_ib_sync",
            "capital_aligned": cap_changed,
            "war_slots": pos_result.get("war_slots", 0),
            "session_pnl_war": pnl_result.get("session_pnl_war", 0),
            "monitor_only": pos_result.get("monitor_only", []),
            "dropped": pos_result.get("dropped", []),
            "ts": time.time(),
        })
        log.info(
            f"⚔️ War IB sync applied — nav=${float(state.get('nav', 0)):,.0f} "
            f"session_pnl=${float(state.get('session_pnl_war', 0)):+.2f} "
            f"slots={pos_result.get('war_slots', 0)} "
            f"monitor_only={pos_result.get('monitor_only', [])}"
        )

    return result


def format_reconcile_report(report: Dict[str, Any]) -> str:
    """Human-readable reconcile summary for CLI."""
    lines = [
        "═══ War vs IB Reconcile ═══",
        f"  War NAV (virtual):     ${report.get('war_nav', 0):,.2f}",
        f"  War session PnL:       ${report.get('war_session_pnl', 0):+,.2f}",
        f"  Config operating cap:  ${report.get('operating_capital_cfg', 0):,.0f}",
        "",
        "── IB Account (ground truth) ──",
    ]
    acct = report.get("ib_account") or {}
    lines.append(f"  NetLiquidation:        ${acct.get('net_liquidation', 0):,.2f}")
    lines.append(f"  RealizedPnL (IB tag):  ${acct.get('realized_pnl', 0):+,.2f}")
    lines.append(f"  UnrealizedPnL:         ${acct.get('unrealized_pnl', 0):+,.2f}")
    lines.append(f"  IB FIFO session PnL:   ${report.get('ib_session_pnl_fifo', 0):+,.2f}")
    lines.append("")
    lines.append("── Positions ──")
    for p in report.get("ib_positions") or []:
        lines.append(
            f"  {p['symbol']:6s} qty={p['qty']:>6} avg=${p['avg_cost']:.4f} "
            f"unreal=${p.get('unrealized_pnl', 0):+,.2f}"
        )
    if report.get("ib_positions_not_in_war"):
        lines.append(f"  IB-only (monitor):     {report['ib_positions_not_in_war']}")
    if report.get("war_slots_not_in_ib"):
        lines.append(f"  War-only (stale):      {report['war_slots_not_in_ib']}")

    if report.get("ghost_exits"):
        lines.append("")
        lines.append(f"── Ghost exits ({len(report['ghost_exits'])}) ──")
        for g in report["ghost_exits"][:20]:
            lines.append(f"  {g['ticker']} net=${g.get('net_pnl', 0):+,.2f}")

    if report.get("pnl_drift"):
        lines.append("")
        lines.append("── PnL drift (war vs IB FIFO) ──")
        for d in report["pnl_drift"]:
            lines.append(
                f"  {d['ticker']} war=${d['war_pnl']:+,.2f} ib=${d['ib_pnl']:+,.2f} "
                f"Δ=${d['delta']:+,.2f}"
            )

    if report.get("pnl_outliers"):
        lines.append("")
        lines.append(f"── Outliers (|ghost| > $100): {len(report['pnl_outliers'])} ──")

    lines.append("")
    lines.append(f"  Status: {'OK' if report.get('ok') else 'DRIFT DETECTED'}")
    return "\n".join(lines)
