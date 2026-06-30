#!/usr/bin/env python3
"""
core/fill_tracker.py — Retrieve and ledger IB entry/exit fills for learning.

Uses order avgFillPrice, bracket child fills, and execution reports before
falling back to quote prices so P&L and rewards reflect real fills.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.broker import BracketHandle

MODELS_DIR = Path("models")
FILL_LEDGER_PATH = MODELS_DIR / "fill_ledger.jsonl"
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_fill_ledger(record: Dict[str, Any]) -> None:
    """Persistent round-trip fill log for training reconciliation."""
    from core.trade_horizon import tag_record

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    record = tag_record(record)
    record.setdefault("timestamp", _now())
    line = json.dumps(record, default=str, separators=(",", ":"))
    with _lock:
        with open(FILL_LEDGER_PATH, "a") as f:
            f.write(line + "\n")


def poll_trade_fill(
    ib,
    trade,
    fallback_px: float,
    *,
    max_wait: float = 2.0,
    poll_interval: float = 0.15,
) -> Tuple[float, float]:
    """Wait for IB order fill; return (avg_fill_px, filled_qty). max_wait=0 → instant read only."""
    if trade is None:
        return float(fallback_px or 0), 0.0
    if max_wait <= 0:
        return read_order_fill_instant(trade, fallback_px)
    deadline = time.time() + max_wait
    last_px = float(fallback_px or 0)
    last_qty = 0.0
    while time.time() < deadline:
        try:
            ib.sleep(poll_interval)
            status = trade.orderStatus
            if status is None:
                continue
            filled = float(getattr(status, "filled", 0) or 0)
            avg = float(getattr(status, "avgFillPrice", 0) or 0)
            st = str(getattr(status, "status", "") or "")
            if avg > 0:
                last_px = avg
            if filled > 0:
                last_qty = filled
            if st == "Filled" and last_px > 0:
                return last_px, last_qty
            if st in ("Cancelled", "Inactive", "ApiCancelled") and last_px > 0 and last_qty > 0:
                return last_px, last_qty
        except Exception:
            break
    return last_px, last_qty


def read_order_fill_instant(trade, fallback_px: float = 0.0) -> Tuple[float, float]:
    """Read fill from orderStatus without sleeping — safe on hot path."""
    if trade is None:
        return float(fallback_px or 0), 0.0
    try:
        status = getattr(trade, "orderStatus", None)
        if status is None:
            return float(fallback_px or 0), 0.0
        filled = float(getattr(status, "filled", 0) or 0)
        avg = float(getattr(status, "avgFillPrice", 0) or 0)
        st = str(getattr(status, "status", "") or "")
        if avg > 0 and filled > 0:
            return avg, filled
        if st == "Filled" and avg > 0:
            return avg, filled
    except Exception:
        pass
    return float(fallback_px or 0), 0.0


def bracket_exit_fill(
    handle: Optional["BracketHandle"],
    fallback_px: float,
) -> Tuple[float, float]:
    """Read avg fill from a filled bracket stop or target child."""
    if handle is None:
        return 0.0, 0.0
    for child in (handle.stop_trade, handle.target_trade, handle.parent_trade):
        if child is None or child.orderStatus is None:
            continue
        st = str(child.orderStatus.status or "")
        if st != "Filled":
            continue
        avg = float(child.orderStatus.avgFillPrice or 0)
        qty = float(child.orderStatus.filled or 0)
        if avg > 0 and qty > 0:
            return avg, qty
    return 0.0, 0.0


def recent_execution_fill(
    ib,
    symbol: str,
    side: str,
    *,
    since_ts: float = 0.0,
    min_shares: float = 1.0,
    max_wait: float = 0.25,
) -> Tuple[float, float]:
    """Latest IB execution for symbol/side after since_ts. max_wait=0 skips reqExecutions."""
    sym = (symbol or "").upper()
    if not sym:
        return 0.0, 0.0
    side_u = side.upper()
    try:
        if max_wait > 0:
            ib.reqExecutions()
            ib.sleep(min(max_wait, 0.35))
        best_ts = since_ts
        best_px = 0.0
        best_qty = 0.0
        for fill in ib.fills():
            contract = getattr(fill, "contract", None)
            if getattr(contract, "symbol", "").upper() != sym:
                continue
            ex = getattr(fill, "execution", None)
            if ex is None:
                continue
            if str(getattr(ex, "side", "")).upper() != side_u:
                continue
            px = float(getattr(ex, "price", 0) or 0)
            qty = float(getattr(ex, "shares", 0) or 0)
            if px <= 0 or qty < min_shares:
                continue
            ts_raw = getattr(ex, "time", None)
            ts = 0.0
            if ts_raw is not None:
                try:
                    ts = ts_raw.timestamp() if hasattr(ts_raw, "timestamp") else float(ts_raw)
                except Exception:
                    ts = time.time()
            if ts >= since_ts and ts >= best_ts:
                best_ts = ts
                best_px = px
                best_qty = qty
        if best_px > 0:
            return best_px, best_qty
    except Exception as exc:
        log.debug(f"recent_execution_fill {sym}: {exc}")
    return 0.0, 0.0


def position_avg_cost(ib, symbol: str) -> float:
    """IB position avgCost as entry-fill fallback (raw — prefer position_entry_price)."""
    entry, _ = _ib_position_cost(ib, symbol)
    return entry


def _truth_long_position(symbol: str):
    """IB Truth position row when snapshot is fresh."""
    sym = (symbol or "").upper()
    if not sym:
        return None
    try:
        from core.ib_truth import get_snapshot, ib_truth_enabled
        if not ib_truth_enabled():
            return None
        snap = get_snapshot()
        if snap.refreshed_at <= 0:
            return None
        return snap.long_positions().get(sym)
    except Exception:
        return None


def _ib_position_cost(ib, symbol: str) -> Tuple[float, float]:
    """Return (raw_avg_cost, contract_multiplier) for a long position."""
    sym = (symbol or "").upper()
    if not sym:
        return 0.0, 1.0
    pos = _truth_long_position(sym)
    if pos is not None and pos.avg_cost > 0:
        return float(pos.avg_cost), max(float(pos.multiplier or 1), 1.0)
    try:
        from core.ib_truth import ib_truth_enabled
        if ib_truth_enabled():
            return 0.0, 1.0
    except Exception:
        pass
    try:
        for p in ib.positions():
            if getattr(p.contract, "symbol", "").upper() == sym:
                avg = float(getattr(p, "avgCost", 0) or 0)
                mult = float(getattr(p.contract, "multiplier", 1) or 1)
                if avg > 0:
                    return avg, max(mult, 1.0)
    except Exception:
        pass
    return 0.0, 1.0


def sanitize_quote_price(
    quote_px: float,
    *,
    ref_px: float = 0.0,
    pred_px: float = 0.0,
    symbol: str = "",
) -> float:
    """Reconcile live IB quotes vs bar history / micro prediction (10x paper drift)."""
    if quote_px <= 0:
        return 0.0
    refs = [r for r in (ref_px, pred_px) if r and r > 0]
    if not refs:
        return quote_px
    ref = sum(refs) / len(refs)
    fixed = normalize_ib_avg_cost(quote_px, market_px=ref)
    if abs(fixed - quote_px) / max(quote_px, 0.01) > 0.05 and symbol:
        log.warning(
            f"  🔧 Quote sanitized {symbol.upper()} ${quote_px:.4f} → ${fixed:.4f} "
            f"(ref ${ref:.4f})"
        )
    return fixed


def snapshot_market_price(
    ib,
    symbol: str,
    *,
    exchange: str = "SMART",
    currency: str = "USD",
    wait: float = 0.12,
) -> float:
    """One-shot IB quote for entry/avgCost sanity checks."""
    sym = (symbol or "").upper()
    if not sym:
        return 0.0
    try:
        from ib_insync import Stock

        qualified = ib.qualifyContracts(Stock(sym, exchange, currency))
        if not qualified:
            return 0.0
        contract = qualified[0]
        ticks = ib.reqMktData(contract, "", False, False)
        ib.sleep(wait)
        px = 0.0
        for attr in ("last", "close", "marketPrice"):
            raw = getattr(ticks, attr, None)
            if raw and float(raw) > 0:
                px = float(raw)
                break
        if px <= 0:
            bid = float(ticks.bid) if ticks.bid and ticks.bid > 0 else 0.0
            ask = float(ticks.ask) if ticks.ask and ticks.ask > 0 else 0.0
            if bid > 0 and ask > 0:
                px = (bid + ask) / 2
        ib.cancelMktData(contract)
        return px if px > 0 else 0.0
    except Exception as exc:
        log.debug(f"snapshot_market_price {sym}: {exc}")
        return 0.0


def normalize_ib_avg_cost(
    raw_avg: float,
    *,
    market_px: float = 0.0,
    multiplier: float = 1.0,
) -> float:
    """Reconcile IB avgCost with live quote — fixes 10x paper avgCost drift."""
    if raw_avg <= 0:
        return 0.0
    base = raw_avg / max(float(multiplier or 1), 1.0)
    if market_px <= 0:
        return base
    if 0.85 <= (base / market_px) <= 1.15:
        return base
    best = base
    best_err = abs(base / market_px - 1.0)
    for factor in (10.0, 0.1, 100.0, 0.01):
        for candidate in (base * factor, base / factor):
            if candidate <= 0:
                continue
            err = abs(candidate / market_px - 1.0)
            if err < best_err and 0.5 <= candidate / market_px <= 2.0:
                best, best_err = candidate, err
    if best_err > 0.35 and base / market_px > 2.0:
        log.warning(
            f"  🔧 IB avgCost ${raw_avg:.4f} vs market ${market_px:.4f} "
            f"— using market for entry"
        )
        return market_px
    if abs(best - base) > 0.0001:
        log.warning(
            f"  🔧 IB avgCost normalized ${raw_avg:.4f} → ${best:.4f} "
            f"(market ${market_px:.4f})"
        )
    return best


def position_entry_price(
    ib,
    symbol: str,
    *,
    market_px: float = 0.0,
) -> float:
    """Best entry price for an IB-held symbol — normalized avgCost vs market."""
    raw, mult = _ib_position_cost(ib, symbol)
    if raw <= 0:
        return 0.0
    mkt = float(market_px or 0)
    if mkt <= 0:
        mkt = snapshot_market_price(ib, symbol)
    return normalize_ib_avg_cost(raw, market_px=mkt, multiplier=mult)


def ib_position_shares(ib, symbol: str) -> float:
    """Long shares held at IB for symbol (0 if flat)."""
    sym = (symbol or "").upper()
    if not sym:
        return 0.0
    pos = _truth_long_position(sym)
    if pos is not None:
        return float(pos.qty) if pos.qty > 0 else 0.0
    try:
        from core.ib_truth import ib_truth_enabled
        if ib_truth_enabled():
            return 0.0
    except Exception:
        pass
    try:
        for p in ib.positions():
            if getattr(p.contract, "symbol", "").upper() == sym:
                pos = float(p.position)
                return pos if pos > 0 else 0.0
    except Exception:
        pass
    return 0.0


def require_ib_fill_sync(cfg=None) -> bool:
    import os
    env = os.getenv("REQUIRE_IB_FILL_SYNC", "true").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    if cfg is not None:
        return bool(getattr(cfg, "REQUIRE_IB_FILL_SYNC", True))
    return True


def ib_fill_strict(cfg=None) -> bool:
    """When true, never book P&L / cash from quote fallbacks — IB execution only."""
    import os
    if not require_ib_fill_sync(cfg):
        return False
    env = os.getenv("IB_FILL_STRICT", "true").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if cfg is not None:
        return bool(getattr(cfg, "IB_FILL_STRICT", True))
    return True


def confirm_entry_fill(
    ib,
    *,
    symbol: str,
    parent_trade=None,
    cache=None,
    order_shares: float,
    min_fill_ratio: float,
    ib_pos_baseline: float,
    started_at: float,
    quote_px: float,
) -> Tuple[float, float, bool, str]:
    """
    Confirm entry fill traceable to this order (not orphan IB holdings).
    Returns (filled_shares, fill_px, confirmed, source).
    """
    sym = (symbol or "").upper()
    min_qty = max(1.0, float(order_shares) * float(min_fill_ratio))

    px, qty = read_order_fill_instant(parent_trade, 0.0)
    if qty >= min_qty and px > 0 and _sane_fill_ratio(px, quote_px):
        return qty, px, True, "order_status"

    if cache is not None:
        hit = cache.latest(sym, "BOT", since_ts=max(0.0, started_at - 1.0))
        if hit and hit.qty >= min_qty and _sane_fill_ratio(hit.price, quote_px):
            return hit.qty, hit.price, True, "exec_cache"

    px, qty = recent_execution_fill(
        ib, sym, "BOT", since_ts=max(0.0, started_at - 1.0), max_wait=0.0,
    )
    if qty >= min_qty and px > 0 and _sane_fill_ratio(px, quote_px):
        return qty, px, True, "execution"

    current_pos = ib_position_shares(ib, sym)
    delta = current_pos - float(ib_pos_baseline or 0)
    max_delta = float(order_shares) * 1.25
    if min_qty <= delta <= max_delta:
        avg = position_avg_cost(ib, sym)
        if avg > 0 and _sane_fill_ratio(avg, quote_px):
            use_qty = min(delta, float(order_shares))
            return use_qty, avg, True, "position_delta"

    return 0.0, 0.0, False, ""


def resolve_entry_fill(
    ib,
    *,
    symbol: str,
    parent_trade=None,
    quote_px: float,
    max_wait: float = 0.5,
    cache=None,
) -> float:
    """Best available entry fill price."""
    px, qty = poll_trade_fill(ib, parent_trade, quote_px, max_wait=max_wait)
    if px > 0 and qty > 0 and _sane_fill_ratio(px, quote_px):
        return px
    sym = (symbol or "").upper()
    if cache is not None:
        hit = cache.latest(sym, "BOT", since_ts=time.time() - 600)
        if hit and _sane_fill_ratio(hit.price, quote_px):
            return hit.price
    avg = position_avg_cost(ib, sym)
    if avg > 0 and _sane_fill_ratio(avg, quote_px):
        return avg
    px, _ = recent_execution_fill(ib, sym, "BOT", since_ts=time.time() - 120.0, max_wait=0.0)
    if px > 0:
        return px
    return float(quote_px or 0)


def _sane_fill_ratio(fill_px: float, ref_px: float) -> bool:
    if fill_px <= 0 or ref_px <= 0:
        return fill_px > 0
    ratio = fill_px / ref_px
    return 0.02 <= ratio <= 50.0


def resolve_exit_fill(
    ib,
    *,
    symbol: str,
    bracket: Optional["BracketHandle"] = None,
    flatten_trade=None,
    quote_px: float,
    since_ts: float = 0.0,
    max_wait: float = 2.0,
    entry_fill: float = 0.0,
) -> float:
    """Best available exit fill price."""
    sym = (symbol or "").upper()
    px, qty = poll_trade_fill(ib, flatten_trade, quote_px, max_wait=max_wait)
    if px > 0 and qty > 0 and _sane_fill_ratio(px, entry_fill or quote_px):
        return px
    px, qty = bracket_exit_fill(bracket, quote_px)
    if px > 0 and qty > 0 and _sane_fill_ratio(px, entry_fill or quote_px):
        return px
    px, _ = recent_execution_fill(
        ib, sym, "SLD", since_ts=since_ts or (time.time() - 300.0), max_wait=0.0 if max_wait <= 0 else 0.25,
    )
    if px > 0 and _sane_fill_ratio(px, entry_fill or quote_px):
        return px
    return float(quote_px or 0)


def round_trip_pnl(
    entry_fill: float,
    exit_fill: float,
    shares: float,
    *,
    commission: float = 0.0,
) -> Tuple[float, float]:
    """Return (pnl_usd, pnl_pct) from IB fills, net of commission when provided."""
    if entry_fill <= 0 or shares <= 0:
        return 0.0, 0.0
    pnl = (exit_fill - entry_fill) * shares - float(commission or 0)
    pnl_pct = ((exit_fill / entry_fill) - 1) * 100
    return round(pnl, 4), round(pnl_pct, 4)


def slippage_vs_quote(fill_px: float, quote_px: float) -> float:
    if fill_px <= 0 or quote_px <= 0:
        return 0.0
    return round((fill_px - quote_px) / quote_px, 6)


def build_round_trip_record(
    *,
    ticker: str,
    entry_fill: float,
    exit_fill: float,
    quote_entry: float,
    quote_exit: float,
    shares: float,
    exit_reason: str = "",
    limit_px: Optional[float] = None,
    entry_mode: str = "",
    regime: str = "",
    hold_sec: float = 0.0,
    peak_px: float = 0.0,
    stop_px: float = 0.0,
    target_px: float = 0.0,
) -> Dict[str, Any]:
    pnl_usd, pnl_pct = round_trip_pnl(entry_fill, exit_fill, shares)
    entry_slip = slippage_vs_quote(entry_fill, limit_px) if limit_px else slippage_vs_quote(entry_fill, quote_entry)
    exit_slip = slippage_vs_quote(exit_fill, quote_exit)
    result = "win" if pnl_usd > 0 else "loss"
    peak_pct = 0.0
    if entry_fill > 0 and peak_px > 0:
        peak_pct = round(((peak_px / entry_fill) - 1) * 100, 3)
    return {
        "ticker": ticker,
        "entry": round(entry_fill, 4),
        "exit": round(exit_fill, 4),
        "entry_fill": round(entry_fill, 4),
        "exit_fill": round(exit_fill, 4),
        "quote_entry": round(quote_entry, 4),
        "quote_exit": round(quote_exit, 4),
        "shares": shares,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
        "peak_pct": peak_pct,
        "result": result,
        "exit_reason": exit_reason[:200],
        "entry_slippage_pct": entry_slip,
        "exit_slippage_pct": exit_slip,
        "limit_px": limit_px,
        "entry_mode": entry_mode,
        "regime": regime,
        "hold_sec": round(hold_sec, 1),
        "peak": round(peak_px, 4),
        "stop": round(stop_px, 4),
        "target": round(target_px, 4),
    }
