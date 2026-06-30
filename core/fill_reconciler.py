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
    read_order_fill_instant,
    recent_execution_fill,
    require_ib_fill_sync,
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
    entry_fill = float(slot.get("entry_fill_px") or slot.get("entry_price") or 0)
    entry_quote = float(slot.get("entry_price") or entry_fill)
    shares = float(pending.shares or slot.get("shares") or 0)
    if shares <= 0:
        return None

    exit_fill, confirmed = resolve_exit_from_ib(
        ib,
        cache,
        symbol=pending.ticker,
        flatten_trade=pending.flatten_trade,
        bracket=pending.bracket,
        quote_px=pending.quote_exit_px,
        since_ts=pending.opened_at,
        entry_fill=entry_fill,
    )

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
    rec["fill_confirmed"] = confirmed
    rec["reconcile_event"] = pending.event
    return rec


def snapshot_slot(slot: Dict[str, Any]) -> Dict[str, Any]:
    """Deep copy position slot for async reconciliation."""
    return dict(slot) if slot else {}
