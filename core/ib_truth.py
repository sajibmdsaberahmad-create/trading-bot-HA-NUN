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
    realized_pnl: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0
    con_id: int = 0
    multiplier: float = 1.0
    account: str = ""


@dataclass
class IBOpenOrder:
    symbol: str
    order_id: int
    action: str
    order_type: str
    qty: float
    status: str
    filled: float
    avg_fill: float
    remaining: float = 0.0
    lmt_price: float = 0.0
    aux_price: float = 0.0
    parent_id: int = 0
    tif: str = ""
    outside_rth: bool = False
    order_ref: str = ""


@dataclass
class IBAccountSnapshot:
    net_liquidation: float = 0.0
    total_cash: float = 0.0
    settled_cash: float = 0.0
    accrued_cash: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    gross_position_value: float = 0.0
    buying_power: float = 0.0
    available_funds: float = 0.0
    excess_liquidity: float = 0.0
    init_margin_req: float = 0.0
    maint_margin_req: float = 0.0
    equity_with_loan: float = 0.0
    prev_day_equity: float = 0.0
    day_trades_remaining: float = -1.0
    leverage: float = 0.0
    cushion: float = 0.0
    sma: float = 0.0
    regt_equity: float = 0.0
    currency: str = "USD"
    tags: Dict[str, float] = field(default_factory=dict)


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
    open_orders: List[IBOpenOrder] = field(default_factory=list)
    executions: List[IBExecution] = field(default_factory=list)
    round_trips: List[RoundTrip] = field(default_factory=list)
    session_pnl_ib: float = 0.0
    session_pnl_fifo: float = 0.0
    session_commissions: float = 0.0
    ticker_pnl_fifo: Dict[str, float] = field(default_factory=dict)
    ticker_pnl_ib: Dict[str, float] = field(default_factory=dict)
    refreshed_at: float = 0.0
    session_since_ts: float = 0.0
    session_scope: str = "rth"
    server_time: str = ""
    connected: bool = False

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
            real = float(getattr(port, "realizedPNL", 0) or 0) if port else 0.0
            mkt_val = float(getattr(port, "marketValue", 0) or 0) if port else 0.0
            cost_basis = float(getattr(port, "averageCost", 0) or 0) if port else 0.0
            if cost_basis <= 0 and port:
                cost_basis = float(getattr(port, "costBasis", 0) or 0)
            acct = str(getattr(port, "account", "") or "") if port else ""
            con_id = int(getattr(p.contract, "conId", 0) or 0)
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
                    realized_pnl=real,
                    market_value=mkt_val,
                    cost_basis=cost_basis,
                    con_id=con_id,
                    multiplier=max(mult, 1.0),
                    account=acct,
                )
            )
    except Exception as exc:
        log.debug(f"ib_truth positions: {exc}")
    return out


def fetch_ib_account_snapshot(ib, currency: str = "USD") -> IBAccountSnapshot:
    from core.ib_data_catalog import ACCOUNT_TAG_FIELD_MAP

    snap = IBAccountSnapshot(currency=currency)
    try:
        for v in ib.accountValues():
            if getattr(v, "currency", "") not in (currency, "BASE", ""):
                continue
            tag = str(getattr(v, "tag", "") or "")
            val = float(getattr(v, "value", 0) or 0)
            snap.tags[tag] = val
            attr = ACCOUNT_TAG_FIELD_MAP.get(tag)
            if attr:
                setattr(snap, attr, val)
    except Exception as exc:
        log.debug(f"ib_truth account: {exc}")
    return snap


def fetch_ib_server_time(ib) -> Tuple[str, bool]:
    """Gateway clock — also proves connection is alive."""
    try:
        dt = ib.reqCurrentTime()
        if dt is not None:
            return dt.isoformat(), True
    except Exception as exc:
        log.debug(f"ib_truth server_time: {exc}")
    return "", False


def fetch_ib_open_orders(ib) -> List[IBOpenOrder]:
    """Live IB orders — openTrades + trades (no local order book)."""
    out: List[IBOpenOrder] = []
    seen: set = set()
    try:
        try:
            ib.reqAllOpenOrders()
            ib.sleep(0.15)
        except Exception:
            pass
        sources = list(ib.openTrades()) + list(ib.trades())
        for t in sources:
            contract = getattr(t, "contract", None)
            order = getattr(t, "order", None)
            status = getattr(t, "orderStatus", None)
            if contract is None or order is None:
                continue
            sym = (getattr(contract, "symbol", "") or "").upper()
            oid = int(getattr(order, "orderId", 0) or 0)
            key = (oid, sym)
            if key in seen:
                continue
            seen.add(key)
            st = str(getattr(status, "status", "") or "") if status else ""
            if st in ("Cancelled", "Inactive", "ApiCancelled"):
                continue
            out.append(
                IBOpenOrder(
                    symbol=sym,
                    order_id=oid,
                    action=str(getattr(order, "action", "") or ""),
                    order_type=type(order).__name__,
                    qty=float(getattr(order, "totalQuantity", 0) or 0),
                    status=st,
                    filled=float(getattr(status, "filled", 0) or 0) if status else 0.0,
                    avg_fill=float(getattr(status, "avgFillPrice", 0) or 0) if status else 0.0,
                    remaining=float(getattr(status, "remaining", 0) or 0) if status else 0.0,
                    lmt_price=float(getattr(order, "lmtPrice", 0) or 0),
                    aux_price=float(getattr(order, "auxPrice", 0) or 0),
                    parent_id=int(getattr(order, "parentId", 0) or 0),
                    tif=str(getattr(order, "tif", "") or ""),
                    outside_rth=bool(getattr(order, "outsideRth", False)),
                    order_ref=str(getattr(order, "orderRef", "") or "")[:40],
                )
            )
    except Exception as exc:
        log.debug(f"ib_truth open_orders: {exc}")
    return out


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
    open_orders = fetch_ib_open_orders(ib)
    raw_execs = fetch_ib_executions(ib, since_ts=since)
    executions = filter_rth_executions(raw_execs, cfg)
    trips = fifo_round_trips(executions)
    server_time, connected = fetch_ib_server_time(ib)

    ticker_pnl: Dict[str, float] = {}
    total_fifo = 0.0
    for trip in trips:
        ticker_pnl[trip.symbol] = round(ticker_pnl.get(trip.symbol, 0) + trip.pnl_usd, 2)
        total_fifo += trip.pnl_usd
    total_comm = sum(ex.commission for ex in executions)

    ticker_pnl_ib: Dict[str, float] = {}
    for p in positions:
        if p.realized_pnl != 0:
            ticker_pnl_ib[p.symbol] = round(p.realized_pnl, 2)

    session_pnl_ib = float(account.realized_pnl)

    return IBTruthSnapshot(
        account=account,
        positions=positions,
        open_orders=open_orders,
        executions=executions,
        round_trips=trips,
        session_pnl_ib=round(session_pnl_ib, 2),
        session_pnl_fifo=round(total_fifo, 2),
        session_commissions=round(total_comm, 2),
        ticker_pnl_fifo=ticker_pnl,
        ticker_pnl_ib=ticker_pnl_ib,
        refreshed_at=time.time(),
        session_since_ts=since,
        session_scope="rth" if rth_session else "calendar",
        server_time=server_time,
        connected=connected,
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
    """Session P&L — IB account RealizedPnL when snapshot fresh, else FIFO, else NetLiq delta."""
    if snap.refreshed_at > 0:
        pnl = snap.session_pnl_ib
    elif snap.session_pnl_fifo != 0 or snap.round_trips:
        pnl = snap.session_pnl_fifo
    else:
        pnl = snap.account.net_liquidation - ib_start
    pct = (pnl / ib_start * 100.0) if ib_start > 0 else 0.0
    return round(pnl, 2), round(pct, 2)


def ib_truth_context(cfg: Optional["BotConfig"] = None) -> Dict[str, Any]:
    """Compact IB-native dict for Halim/Telegram/war — no local math."""
    snap = get_snapshot()
    if snap.refreshed_at <= 0:
        return {"ib_truth": False}
    acct = snap.account
    return {
        "ib_truth": True,
        "session_scope": snap.session_scope,
        "ib_server_time": snap.server_time,
        "ib_connected": snap.connected,
        "ib_net_liquidation": round(acct.net_liquidation, 2),
        "ib_cash": round(acct.total_cash, 2),
        "ib_settled_cash": round(acct.settled_cash, 2),
        "ib_realized_pnl": round(acct.realized_pnl, 2),
        "ib_unrealized_pnl": round(acct.unrealized_pnl, 2),
        "ib_session_pnl": snap.session_pnl_ib,
        "ib_fifo_session_pnl": snap.session_pnl_fifo,
        "ib_session_commissions": snap.session_commissions,
        "ib_buying_power": round(acct.buying_power, 2),
        "ib_available_funds": round(acct.available_funds, 2),
        "ib_excess_liquidity": round(acct.excess_liquidity, 2),
        "ib_init_margin": round(acct.init_margin_req, 2),
        "ib_maint_margin": round(acct.maint_margin_req, 2),
        "ib_day_trades_remaining": acct.day_trades_remaining,
        "ib_leverage": round(acct.leverage, 2),
        "ib_cushion": round(acct.cushion, 4),
        "ib_gross_position_value": round(acct.gross_position_value, 2),
        "ib_open_orders": len(snap.open_orders),
        "ib_position_count": len(snap.positions),
        "ib_executions_session": len(snap.executions),
        "ib_round_trips": len(snap.round_trips),
        "ib_positions": [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_cost": round(p.avg_cost, 4),
                "market_price": round(p.market_price, 4),
                "market_value": round(p.market_value, 2),
                "cost_basis": round(p.cost_basis, 2),
                "unrealized_pnl": round(p.unrealized_pnl, 2),
                "realized_pnl": round(p.realized_pnl, 2),
                "con_id": p.con_id,
            }
            for p in snap.positions
        ],
        "ib_open_orders_detail": [
            {
                "symbol": o.symbol,
                "action": o.action,
                "type": o.order_type,
                "status": o.status,
                "qty": o.qty,
                "filled": o.filled,
                "lmt": o.lmt_price,
                "stop": o.aux_price,
                "parent_id": o.parent_id,
            }
            for o in snap.open_orders[:20]
        ],
        "ib_open_order_symbols": [o.symbol for o in snap.open_orders[:12]],
    }


def ib_ai_context(
    cfg: Optional["BotConfig"] = None,
    connector: Any = None,
) -> Dict[str, Any]:
    """
    Full IB context for Halim/council/Telegram — account, positions, orders,
    session fills, macro (IB-first), and API catalog stats.
    """
    from core.ib_data_catalog import catalog_summary

    ctx = ib_truth_context(cfg)
    if not ctx.get("ib_truth"):
        return {"ib_truth": False, "catalog": catalog_summary()}

    snap = get_snapshot()
    ctx["ib_ticker_pnl_fifo"] = dict(snap.ticker_pnl_fifo)
    ctx["ib_ticker_pnl_ib"] = dict(snap.ticker_pnl_ib)
    ctx["catalog"] = catalog_summary()

    if connector is not None:
        try:
            from core.ib_macro import get_ib_macro_context
            macro = get_ib_macro_context(connector)
            if macro.get("spy_price", 0) > 0:
                ctx["macro"] = macro
                ctx["spy_price"] = macro.get("spy_price")
                ctx["spy_change_pct"] = macro.get("spy_change_pct")
                ctx["qqq_price"] = macro.get("qqq_price")
                ctx["vix_level"] = macro.get("vix_level")
                ctx["risk_tone"] = macro.get("risk_tone")
        except Exception as exc:
            log.debug(f"ib_ai_context macro: {exc}")

    try:
        from core.trade_horizon import horizon_context
        ctx.update(horizon_context(cfg))
    except Exception:
        pass

    return ctx


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
        f"  RealizedPnL:   ${snap.account.realized_pnl:+,.2f} (IB tag)",
        f"  UnrealizedPnL: ${snap.account.unrealized_pnl:+,.2f}",
        f"  Session PnL:   ${snap.session_pnl_ib:+,.2f} IB | ${snap.session_pnl_fifo:+,.2f} FIFO ({len(snap.round_trips)} trips, {snap.session_scope})",
        f"  Open orders:   {len(snap.open_orders)}",
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
