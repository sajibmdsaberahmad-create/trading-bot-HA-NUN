#!/usr/bin/env python3
"""
core/fill_reconciler.py — Non-blocking IB fill reconciliation for P&L + notifications.

Captures position snapshots on exit, then each main-loop tick does an instant cache
lookup (execDetails → FillExecutionCache). No ib.sleep, no throttle, no blocking.
Trading continues at full speed; notify/learn fire the moment IB confirms a fill.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.fill_tracker import (
    bracket_exit_fill,
    build_round_trip_record,
    ib_fill_strict,
    position_avg_cost,
    read_order_fill_instant,
    recent_execution_fill,
    require_ib_fill_sync,
    round_trip_pnl,
)
from core.notify import log

if TYPE_CHECKING:
    from core.broker import BracketHandle


@dataclass
class FillRecord:
    symbol: str
    side: str
    price: float
    qty: float
    ts: float
    exec_id: str = ""
    commission: float = 0.0


class FillExecutionCache:
    """Live IB execution cache — updated from execDetails on the IB thread."""

    def __init__(self, ib):
        self._ib = ib
        self._records: List[FillRecord] = []
        self._max_records = 500
        try:
            ib.execDetailsEvent += self._on_exec
        except Exception:
            pass

    def _on_exec(self, trade, fill) -> None:
        try:
            contract = getattr(fill, "contract", None) or getattr(trade, "contract", None)
            sym = (getattr(contract, "symbol", "") or "").upper()
            ex = getattr(fill, "execution", None)
            if not sym or ex is None:
                return
            px = float(getattr(ex, "price", 0) or 0)
            qty = float(getattr(ex, "shares", 0) or 0)
            if px <= 0 or qty <= 0:
                return
            side = str(getattr(ex, "side", "") or "").upper()
            ts_raw = getattr(ex, "time", None)
            ts = time.time()
            if ts_raw is not None and hasattr(ts_raw, "timestamp"):
                try:
                    ts = float(ts_raw.timestamp())
                except Exception:
                    pass
            rec = FillRecord(
                symbol=sym,
                side=side,
                price=px,
                qty=qty,
                ts=ts,
                exec_id=str(getattr(ex, "execId", "") or ""),
                commission=_execution_commission(fill),
            )
            self._records.append(rec)
            if len(self._records) > self._max_records:
                self._records = self._records[-self._max_records :]
        except Exception:
            pass

    def latest(self, symbol: str, side: str, since_ts: float = 0.0) -> Optional[FillRecord]:
        sym = (symbol or "").upper()
        side_u = (side or "").upper()
        best: Optional[FillRecord] = None
        for rec in reversed(self._records):
            if rec.symbol != sym or rec.side != side_u:
                continue
            if since_ts and rec.ts < since_ts - 1.0:
                continue
            if best is None or rec.ts >= best.ts:
                best = rec
        return best

    def seed_from_ib_fills(self) -> None:
        """One-shot hydrate from ib.fills() (no reqExecutions)."""
        try:
            for fill in self._ib.fills():
                self._on_exec(None, fill)
        except Exception:
            pass


@dataclass
class PendingClose:
    ticker: str
    reason: str
    quote_exit_px: float
    slot: Dict[str, Any]
    shares: float
    opened_at: float
    started_at: float = field(default_factory=time.time)
    event: str = "trade_closed"
    flatten_trade: Any = None
    bracket: Any = None
    credited: bool = False
    credit_qty: float = 0.0
    ib_baseline_shares: float = 0.0
    retry_attempted: bool = False
    stuck_retries: int = 0


def _execution_commission(fill) -> float:
    try:
        report = getattr(fill, "commissionReport", None)
        if report is not None:
            comm = float(getattr(report, "commission", 0) or 0)
            if comm != 0:
                return abs(comm)
    except Exception:
        pass
    return 0.0


def _sane_fill(px: float, ref: float) -> bool:
    if px <= 0:
        return False
    if ref <= 0:
        return True
    ratio = px / ref
    return 0.02 <= ratio <= 50.0


def resolve_exit_from_ib(
    ib,
    cache: Optional[FillExecutionCache],
    *,
    symbol: str,
    flatten_trade=None,
    bracket: Optional["BracketHandle"] = None,
    quote_px: float,
    since_ts: float,
    entry_fill: float,
) -> tuple[float, bool]:
    """
    Instant exit fill lookup — no ib.sleep loops.
    Returns (exit_fill, confirmed) where confirmed=True when sourced from IB execution.
    """
    sym = (symbol or "").upper()

    px, qty = read_order_fill_instant(flatten_trade, quote_px)
    if px > 0 and qty > 0 and _sane_fill(px, entry_fill or quote_px):
        return px, True

    px, qty = bracket_exit_fill(bracket, quote_px)
    if px > 0 and qty > 0 and _sane_fill(px, entry_fill or quote_px):
        return px, True

    if cache is not None:
        hit = cache.latest(sym, "SLD", since_ts=since_ts or (time.time() - 600))
        if hit and _sane_fill(hit.price, entry_fill or quote_px):
            return hit.price, True

    px, _ = recent_execution_fill(
        ib, sym, "SLD", since_ts=since_ts or (time.time() - 300), max_wait=0.0,
    )
    if px > 0 and _sane_fill(px, entry_fill or quote_px):
        return px, True

    return float(quote_px or 0), False


def resolve_entry_from_ib(
    ib,
    cache: Optional[FillExecutionCache],
    *,
    symbol: str,
    slot_entry_fill: float,
    slot_entry_quote: float,
    opened_at: float,
) -> tuple[float, bool]:
    """
    Best IB entry fill for round-trip P&L — execution cache, then stored slot, then avgCost.
    Returns (entry_fill, confirmed).
    """
    sym = (symbol or "").upper()
    ref = float(slot_entry_fill or slot_entry_quote or 0)
    since = max(0.0, float(opened_at or 0) - 1.0)

    if cache is not None:
        hit = cache.latest(sym, "BOT", since_ts=since)
        if hit and _sane_fill(hit.price, ref or hit.price):
            return hit.price, True

    if slot_entry_fill > 0 and _sane_fill(slot_entry_fill, ref or slot_entry_fill):
        return float(slot_entry_fill), True

    px, qty = recent_execution_fill(
        ib, sym, "BOT", since_ts=since, max_wait=0.0,
    )
    if px > 0 and qty > 0 and _sane_fill(px, ref or px):
        return px, True

    avg = position_avg_cost(ib, sym)
    if avg > 0 and _sane_fill(avg, ref or avg):
        return avg, True

    if slot_entry_quote > 0:
        return float(slot_entry_quote), False
    return 0.0, False


def _round_trip_commission(
    cache: Optional[FillExecutionCache],
    symbol: str,
    since_ts: float,
) -> float:
    if cache is None:
        return 0.0
    sym = (symbol or "").upper()
    total = 0.0
    for rec in cache._records:
        if rec.symbol != sym:
            continue
        if since_ts and rec.ts < since_ts - 1.0:
            continue
        if rec.commission > 0:
            total += rec.commission
    return round(total, 4)


def build_close_record(
    pending: PendingClose,
    ib,
    cache: Optional[FillExecutionCache],
    *,
    force: bool = False,
    cfg=None,
) -> Optional[Dict[str, Any]]:
    """Build trade record when IB fill is confirmed, or force at deadline."""
    slot = pending.slot or {}
    entry_quote = float(slot.get("entry_price") or 0)
    opened_at = float(pending.opened_at or slot.get("opened_at") or 0)
    slot_entry = float(slot.get("entry_fill_px") or entry_quote)
    entry_fill, entry_confirmed = resolve_entry_from_ib(
        ib,
        cache,
        symbol=pending.ticker,
        slot_entry_fill=slot_entry,
        slot_entry_quote=entry_quote,
        opened_at=opened_at,
    )
    shares = float(pending.shares or slot.get("shares") or 0)
    if shares <= 0:
        return None

    exit_fill, exit_confirmed = resolve_exit_from_ib(
        ib,
        cache,
        symbol=pending.ticker,
        flatten_trade=pending.flatten_trade,
        bracket=pending.bracket,
        quote_px=pending.quote_exit_px,
        since_ts=max(0.0, float(pending.started_at or 0) - 1.0),
        entry_fill=entry_fill,
    )

    confirmed = exit_confirmed
    if require_ib_fill_sync(cfg) and not entry_confirmed and entry_fill <= 0:
        if not force:
            return None

    if not confirmed and not force:
        return None
    if not confirmed and force and ib_fill_strict(cfg):
        return None
    if not _sane_fill(exit_fill, entry_fill) and entry_fill > 0:
        if force and entry_fill > 0 and not ib_fill_strict(cfg):
            exit_fill = entry_fill
            confirmed = False
        else:
            return None

    commission = _round_trip_commission(cache, pending.ticker, opened_at)
    rec = build_round_trip_record(
        ticker=pending.ticker,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        quote_entry=entry_quote,
        quote_exit=pending.quote_exit_px,
        shares=shares,
        exit_reason=pending.reason,
        limit_px=slot.get("limit_px"),
        entry_mode=str(slot.get("entry_mode", "")),
        regime=str(slot.get("regime", "")),
        hold_sec=max(0.0, time.time() - pending.opened_at) if pending.opened_at else 0.0,
        peak_px=float(slot.get("peak") or 0),
        stop_px=float(slot.get("stop") or 0),
        target_px=float(slot.get("target") or 0),
    )
    if commission > 0:
        pnl_usd, pnl_pct = round_trip_pnl(entry_fill, exit_fill, shares, commission=commission)
        rec["pnl_usd"] = round(pnl_usd, 2)
        rec["pnl_pct"] = round(pnl_pct, 2)
        rec["result"] = "win" if pnl_usd > 0 else "loss"
        rec["ib_commission_usd"] = commission
    rec["fill_confirmed"] = confirmed and (entry_confirmed or entry_fill > 0)
    rec["entry_fill_confirmed"] = entry_confirmed
    rec["exit_fill_confirmed"] = exit_confirmed
    rec["reconcile_event"] = pending.event
    return rec


def finalize_flat_position_close(
    pending: PendingClose,
    ib,
    cache: Optional[FillExecutionCache],
    *,
    cfg=None,
) -> Optional[Dict[str, Any]]:
    """
    IB shows flat position but normal reconcile missed the fill.
    Last-resort salvage so pending_closes do not leak forever.
    """
    from core.fill_tracker import ib_position_shares

    sym = (pending.ticker or "").upper()
    baseline = float(pending.ib_baseline_shares or pending.shares or 0)
    if baseline <= 0.5:
        return None
    if ib_position_shares(ib, sym) > 0.5:
        return None

    slot = pending.slot or {}
    entry_quote = float(slot.get("entry_price") or 0)
    opened_at = float(pending.opened_at or slot.get("opened_at") or 0)
    slot_entry = float(slot.get("entry_fill_px") or entry_quote)
    shares = float(pending.shares or slot.get("shares") or 0)
    if shares <= 0:
        return None

    entry_fill, entry_confirmed = resolve_entry_from_ib(
        ib,
        cache,
        symbol=sym,
        slot_entry_fill=slot_entry,
        slot_entry_quote=entry_quote,
        opened_at=opened_at,
    )
    since = max(0.0, float(pending.started_at or 0) - 1.0)
    exit_fill, exit_confirmed = resolve_exit_from_ib(
        ib,
        cache,
        symbol=sym,
        flatten_trade=pending.flatten_trade,
        bracket=pending.bracket,
        quote_px=pending.quote_exit_px,
        since_ts=since,
        entry_fill=entry_fill,
    )
    if not exit_confirmed or exit_fill <= 0:
        if cache is not None:
            hit = cache.latest(sym, "SLD", since_ts=since)
            if hit and _sane_fill(hit.price, entry_fill or pending.quote_exit_px):
                exit_fill, exit_confirmed = hit.price, True
    if not exit_confirmed or exit_fill <= 0:
        if ib_fill_strict(cfg):
            return None
        exit_fill = float(pending.quote_exit_px or entry_fill or 0)
        exit_confirmed = False
    if exit_fill <= 0:
        return None

    commission = _round_trip_commission(cache, sym, opened_at)
    rec = build_round_trip_record(
        ticker=sym,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        quote_entry=entry_quote,
        quote_exit=pending.quote_exit_px,
        shares=shares,
        exit_reason=pending.reason,
        limit_px=slot.get("limit_px"),
        entry_mode=str(slot.get("entry_mode", "")),
        regime=str(slot.get("regime", "")),
        hold_sec=max(0.0, time.time() - pending.opened_at) if pending.opened_at else 0.0,
        peak_px=float(slot.get("peak") or 0),
        stop_px=float(slot.get("stop") or 0),
        target_px=float(slot.get("target") or 0),
    )
    if commission > 0:
        pnl_usd, pnl_pct = round_trip_pnl(entry_fill, exit_fill, shares, commission=commission)
        rec["pnl_usd"] = round(pnl_usd, 2)
        rec["pnl_pct"] = round(pnl_pct, 2)
        rec["result"] = "win" if pnl_usd > 0 else "loss"
        rec["ib_commission_usd"] = commission
    rec["fill_confirmed"] = exit_confirmed and (entry_confirmed or entry_fill > 0)
    rec["entry_fill_confirmed"] = entry_confirmed
    rec["exit_fill_confirmed"] = exit_confirmed
    rec["reconcile_event"] = pending.event
    rec["reconcile_source"] = "position_flat"
    return rec


def snapshot_slot(slot: Dict[str, Any]) -> Dict[str, Any]:
    """Deep copy position slot for async reconciliation."""
    return dict(slot) if slot else {}
