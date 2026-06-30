#!/usr/bin/env python3
"""
core/ib_macro.py — Macro context from IB (SPY/QQQ/VIX) — no Yahoo when connected.

One-shot reqTickers snapshot; does not leave streaming subscriptions open.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

from core.notify import log

if TYPE_CHECKING:
    from core.connector import IBConnector

_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}
_TTL = float(os.getenv("IB_MACRO_TTL_SEC", "120"))


def ib_macro_enabled() -> bool:
    return os.getenv("MACRO_FROM_IB", "true").lower() in ("1", "true", "yes")


def _risk_tone(spy_pct: float, qqq_pct: float, vix: float) -> str:
    if vix >= 30:
        return "high_fear"
    if vix >= 22 and spy_pct < -0.3:
        return "risk_off"
    if vix <= 16 and spy_pct > 0.3:
        return "risk_on"
    if spy_pct < -1.0:
        return "weak"
    if spy_pct > 1.0:
        return "strong"
    return "neutral"


def fetch_ib_macro_snapshot(connector: Optional["IBConnector"]) -> Dict[str, Any]:
    """SPY/QQQ/VIX from IB reqTickers — broker marks only."""
    out: Dict[str, Any] = {
        "source": "ib",
        "spy_price": 0.0,
        "spy_change_pct": 0.0,
        "qqq_price": 0.0,
        "qqq_change_pct": 0.0,
        "vix_level": 0.0,
        "risk_tone": "unknown",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if connector is None or not connector.is_connected():
        out["source"] = "none"
        return out
    try:
        from ib_insync import Index, Stock

        ib = connector.ib
        contracts = [
            Stock("SPY", "ARCA", "USD"),
            Stock("QQQ", "NASDAQ", "USD"),
            Index("VIX", "CBOE", "USD"),
        ]
        qualified = ib.qualifyContracts(*contracts)
        if not qualified:
            return out
        tickers = ib.reqTickers(*qualified)
        ib.sleep(0.35)
        by_sym: Dict[str, Any] = {}
        for t in tickers:
            sym = getattr(getattr(t, "contract", None), "symbol", "") or ""
            if sym:
                by_sym[sym] = t

        def _px(t) -> float:
            for attr in ("last", "close", "marketPrice"):
                v = float(getattr(t, attr, 0) or 0)
                if v > 0:
                    return v
            return 0.0

        def _chg_pct(t) -> float:
            last = _px(t)
            close = float(getattr(t, "close", 0) or 0)
            if last > 0 and close > 0:
                return round((last / close - 1.0) * 100.0, 3)
            return 0.0

        spy_t = by_sym.get("SPY")
        qqq_t = by_sym.get("QQQ")
        vix_t = by_sym.get("VIX")
        if spy_t:
            out["spy_price"] = round(_px(spy_t), 2)
            out["spy_change_pct"] = _chg_pct(spy_t)
        if qqq_t:
            out["qqq_price"] = round(_px(qqq_t), 2)
            out["qqq_change_pct"] = _chg_pct(qqq_t)
        if vix_t:
            out["vix_level"] = round(max(0.0, _px(vix_t)), 2)
        out["risk_tone"] = _risk_tone(
            out["spy_change_pct"], out["qqq_change_pct"], out["vix_level"],
        )
        for t in tickers:
            try:
                ib.cancelMktData(t.contract)
            except Exception:
                pass
    except Exception as exc:
        log.debug(f"ib_macro snapshot: {exc}")
        out["source"] = "error"
    return out


def get_ib_macro_context(connector: Optional["IBConnector"], *, force: bool = False) -> Dict[str, Any]:
    if not ib_macro_enabled():
        return {}
    now = time.time()
    if not force and _CACHE["data"] and now - _CACHE["ts"] < _TTL:
        return dict(_CACHE["data"])
    snap = fetch_ib_macro_snapshot(connector)
    if snap.get("source") == "ib" and snap.get("spy_price", 0) > 0:
        _CACHE["data"] = snap
        _CACHE["ts"] = now
    return snap
