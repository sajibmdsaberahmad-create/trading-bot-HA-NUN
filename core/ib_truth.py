#!/usr/bin/env python3
"""
core/ib_truth.py — IB Gateway as the single source of truth for the entire bot.

All programs (scalper, war ledger, AI/Halim context, Telegram, coach, risk)
read positions, fills, account values, and session P&L from here — never
from local fiction, manual ledger entries, or stale bot_nav bookkeeping.

Enable: REQUIRE_IB_FILL_SYNC=true (default). War virtual $1k pool still sizes
entries but positions/PnL are grounded in IB executions.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from core.fill_tracker import normalize_ib_avg_cost, position_entry_price, round_trip_pnl, require_ib_fill_sync
from core.notify import log

if TYPE_CHECKING:
    from core.config import BotConfig


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


@dataclass
class IBTruthSnapshot:
    """One coherent IB pull — every consumer reads this, not local ledgers."""
    account: IBAccountSnapshot = field(default_factory=IBAccountSnapshot)
    positions: List[IBPosition] = field(default_factory=list)
    executions: List[IBExecution] = field(default_factory=list)
    round_trips: List[RoundTrip] = field(default_factory=list)
    session_pnl_fifo: float = 0.0
    ticker_pnl_fifo: Dict[str, float] = field(default_factory=dict)
    refreshed_at: float = 0.0
    session_since_ts: float = 0.0
    session_scope: str = "rth"

    def long_positions(self) -> Dict[str, IBPosition]:
        return {p.symbol: p for p in self.positions if p.qty > 0}

    def short_positions(self) -> Dict[str, IBPosition]:
        return {p.symbol: p for p in self.positions if p.qty < 0}

    def position_qty(self, symbol: str) -> float:
        sym = (symbol or "").upper()
        for p in self.positions:
            if p.symbol == sym:
                return p.qty
        return 0.0


_store_lock = threading.RLock()
_snapshot: Optional[IBTruthSnapshot] = None
_last_refresh: float = 0.0


def ib_truth_enabled(cfg: Optional["BotConfig"] = None) -> bool:
    """Master switch — same as REQUIRE_IB_FILL_SYNC."""
    return require_ib_fill_sync(cfg)


def session_start_ts_et(cfg: Optional["BotConfig"] = None) -> float:
    """Deprecated alias — use rth_session.ib_truth_session_start_ts."""
    from core.rth_session import ib_truth_session_start_ts
    return ib_truth_session_start_ts(cfg)


def _rth_fills_only(cfg: Optional["BotConfig"] = None) -> bool:
    return os.getenv("IB_TRUTH_RTH_FILLS_ONLY", "true").lower() in ("1", "true", "yes")


def filter_rth_executions(
    executions: List[IBExecution],
    cfg: Optional["BotConfig"] = None,
) -> List[IBExecution]:
    """Keep only fills inside 09:30–16:00 ET when IB_TRUTH_RTH_FILLS_ONLY=true."""
    if not _rth_fills_only(cfg):
        return executions
    from core.rth_session import execution_in_rth_window
    return [ex for ex in executions if execution_in_rth_window(ex.ts, cfg)]


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
        log.debug(f"ib_truth executions: {exc}")
    out.sort(key=lambda e: e.ts)
    return out


def fetch_ib_positions(ib) -> List[IBPosition]:
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
        log.debug(f"ib_truth positions: {exc}")
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
        log.debug(f"ib_truth account: {exc}")
    return snap


def fifo_round_trips(executions: List[IBExecution]) -> List[RoundTrip]:
    by_sym: Dict[str, List[IBExecution]] = {}
    for ex in executions:
        by_sym.setdefault(ex.symbol, []).append(ex)

    trips: List[RoundTrip] = []
    for sym, rows in by_sym.items():
        lots: List[Tuple[float, float, float, float]] = []
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


def build_snapshot(
    ib,
    cfg: Optional["BotConfig"] = None,
    *,
    since_ts: Optional[float] = None,
) -> IBTruthSnapshot:
    """Pull full IB truth in one pass."""
    from core.config import BotConfig

    cfg = cfg or BotConfig()
    currency = str(getattr(cfg, "CURRENCY", "USD") or "USD")
    from core.rth_session import ib_truth_session_start_ts
    since = since_ts if since_ts is not None else ib_truth_session_start_ts(cfg)
    rth_session = os.getenv("IB_TRUTH_RTH_SESSION", "true").lower() not in ("0", "false", "no")

    account = fetch_ib_account_snapshot(ib, currency=currency)
    positions = fetch_ib_positions(ib)
    raw_execs = fetch_ib_executions(ib, since_ts=since)
    executions = filter_rth_executions(raw_execs, cfg)
    trips = fifo_round_trips(executions)

    ticker_pnl: Dict[str, float] = {}
    total = 0.0
    for trip in trips:
        ticker_pnl[trip.symbol] = round(ticker_pnl.get(trip.symbol, 0) + trip.pnl_usd, 2)
        total += trip.pnl_usd

    return IBTruthSnapshot(
        account=account,
        positions=positions,
        executions=executions,
        round_trips=trips,
        session_pnl_fifo=round(total, 2),
        ticker_pnl_fifo=ticker_pnl,
        refreshed_at=time.time(),
        session_since_ts=since,
        session_scope="rth" if rth_session else "calendar",
    )


def refresh(
    ib,
    cfg: Optional["BotConfig"] = None,
    *,
    force: bool = False,
    ttl_sec: float = 2.0,
    since_ts: Optional[float] = None,
) -> IBTruthSnapshot:
    """Refresh cached IB truth (throttled). Returns latest snapshot."""
    global _snapshot, _last_refresh

    if not ib_truth_enabled(cfg):
        return get_snapshot()

    now = time.time()
    with _store_lock:
        if not force and _snapshot and (now - _last_refresh) < ttl_sec:
            return _snapshot

    snap = build_snapshot(ib, cfg, since_ts=since_ts)
    with _store_lock:
        _snapshot = snap
        _last_refresh = now
    return snap


def get_snapshot() -> IBTruthSnapshot:
    with _store_lock:
        return _snapshot or IBTruthSnapshot()


def apply_to_runner(runner, snap: Optional[IBTruthSnapshot] = None) -> None:
    """Push IB truth into runner fields — replaces local bot_nav fiction."""
    snap = snap or get_snapshot()
    if snap.account.net_liquidation <= 0 and not snap.positions:
        return
    acct = snap.account
    if acct.net_liquidation > 0:
        runner.account_equity = acct.net_liquidation
        runner.bot_nav = acct.net_liquidation
    if acct.total_cash > 0:
        runner.available_cash = acct.total_cash
        runner.cash = acct.total_cash
        if ib_truth_enabled(getattr(runner, "cfg", None)):
            runner.bot_cash = acct.total_cash


def day_pnl_from_snapshot(
    snap: IBTruthSnapshot,
    ib_start: float,
) -> Tuple[float, float]:
    """Session P&L (usd, pct) — FIFO fills first, else NetLiq delta."""
    if snap.session_pnl_fifo != 0 or snap.round_trips:
        pct = (snap.session_pnl_fifo / ib_start * 100.0) if ib_start > 0 else 0.0
        return snap.session_pnl_fifo, pct
    equity = snap.account.net_liquidation
    change = equity - ib_start
    pct = (change / ib_start * 100.0) if ib_start > 0 else 0.0
    return change, pct


def position_entry_from_truth(
    ib,
    symbol: str,
    snap: Optional[IBTruthSnapshot] = None,
) -> float:
    """Entry price for symbol — IB position avgCost normalized."""
    sym = (symbol or "").upper()
    snap = snap or get_snapshot()
    pos = snap.long_positions().get(sym)
    if pos and pos.avg_cost > 0:
        return pos.avg_cost
    mkt = pos.market_price if pos else 0.0
    return position_entry_price(ib, sym, market_px=mkt)


def format_snapshot_summary(snap: IBTruthSnapshot) -> str:
    lines = [
        "═══ IB Truth (source of truth) ═══",
        f"  NetLiq:        ${snap.account.net_liquidation:,.2f}",
        f"  Cash:          ${snap.account.total_cash:,.2f}",
        f"  RealizedPnL:   ${snap.account.realized_pnl:+,.2f}",
        f"  UnrealizedPnL: ${snap.account.unrealized_pnl:+,.2f}",
        f"  FIFO session:  ${snap.session_pnl_fifo:+,.2f} ({len(snap.round_trips)} trips, {snap.session_scope})",
        "",
        "── Positions ──",
    ]
    for p in snap.positions:
        side = "LONG" if p.qty > 0 else "SHORT"
        lines.append(
            f"  {p.symbol:6s} {side:5s} {abs(p.qty):>6.0f}sh "
            f"avg=${p.avg_cost:.4f} unreal=${p.unrealized_pnl:+,.2f}"
        )
    if not snap.positions:
        lines.append("  (flat)")
    return "\n".join(lines)
