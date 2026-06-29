#!/usr/bin/env python3
"""
core/market_context.py — Broader market context from Yahoo Finance.

Cached SPY/QQQ/VIX snapshot for council, Halim, and copilot prompts.
Advisory only — never blocks entries. Refreshed on a timer (not per entry).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from core.notify import log

logger = logging.getLogger("MARKET_CONTEXT")

# Inverse / bear ETFs — macro hints only (no hard veto).
INVERSE_ETFS = frozenset({
    "TZA", "SOXS", "SQQQ", "SPXS", "SPXU", "SDOW", "SDS", "QID", "PSQ",
    "UVXY", "VIXY", "SVXY", "LABD", "FAZ", "ERY", "DUST", "JDST",
})

_CACHE: Dict[str, object] = {
    "ctx": None,
    "fetched_at": 0.0,
    "lock": threading.Lock(),
}
_STATE_PATH = Path(__file__).resolve().parent.parent / "models" / "macro_context.json"


def _try_import_yfinance():
    try:
        import yfinance as yf
        return yf
    except Exception:
        return None


def _refresh_interval_sec() -> float:
    return float(os.getenv("MACRO_CONTEXT_REFRESH_SEC", "600"))


def macro_context_enabled() -> bool:
    return os.getenv("MACRO_CONTEXT_ENABLED", "true").lower() in ("1", "true", "yes")


def _fast_snapshot(yf, symbol: str) -> Dict[str, float]:
    out = {"price": 0.0, "change_pct": 0.0}
    try:
        t = yf.Ticker(symbol)
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            price = fi.get("lastPrice") or fi.get("regularMarketPrice") or fi.get("last_price")
            chg = fi.get("regularMarketChangePercent") or fi.get("regular_market_change_percent")
            if price:
                out["price"] = float(price)
            if chg is not None:
                out["change_pct"] = float(chg)
            if out["price"] or out["change_pct"]:
                return out
    except Exception:
        pass
    try:
        hist = yf.Ticker(symbol).history(period="5d", interval="1d")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].values
            out["price"] = float(closes[-1])
            if len(closes) >= 2:
                out["change_pct"] = float((closes[-1] / closes[-2] - 1.0) * 100.0)
    except Exception:
        pass
    return out


def _risk_tone(spy_pct: float, qqq_pct: float, vix: float) -> str:
    if vix >= 30:
        return "high_fear"
    if vix >= 22 and spy_pct < -0.3:
        return "risk_off"
    if spy_pct >= 0.4 and qqq_pct >= 0.4 and vix < 18:
        return "risk_on"
    if spy_pct <= -0.4 and qqq_pct <= -0.4:
        return "risk_off"
    if abs(spy_pct) < 0.15 and abs(qqq_pct) < 0.15:
        return "neutral"
    if spy_pct > 0 and qqq_pct > 0:
        return "mild_risk_on"
    if spy_pct < 0 and qqq_pct < 0:
        return "mild_risk_off"
    return "mixed"


def _trend_label(pct: float) -> str:
    if pct > 0.25:
        return "up"
    if pct < -0.25:
        return "down"
    return "flat"


def _fetch_macro_context() -> Dict:
    """Pull fresh Yahoo snapshot (blocking — call from refresh/tick only)."""
    summary: Dict = {
        "spy_trend": "unknown",
        "qqq_trend": "unknown",
        "spy_pct": 0.0,
        "qqq_pct": 0.0,
        "spy_price": 0.0,
        "qqq_price": 0.0,
        "vix_level": 0.0,
        "vix_regime": "low",
        "risk_tone": "neutral",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "yahoo",
    }
    yf = _try_import_yfinance()
    if yf is None:
        summary["source"] = "unavailable"
        return summary
    try:
        spy = _fast_snapshot(yf, "SPY")
        qqq = _fast_snapshot(yf, "QQQ")
        vix = _fast_snapshot(yf, "^VIX")
        summary["spy_pct"] = round(spy["change_pct"], 2)
        summary["qqq_pct"] = round(qqq["change_pct"], 2)
        summary["spy_price"] = round(spy["price"], 2)
        summary["qqq_price"] = round(qqq["price"], 2)
        summary["spy_trend"] = _trend_label(spy["change_pct"])
        summary["qqq_trend"] = _trend_label(qqq["change_pct"])
        summary["vix_level"] = round(vix["price"], 2)
        summary["vix_regime"] = (
            "high" if summary["vix_level"] > 30
            else "elevated" if summary["vix_level"] > 20
            else "low"
        )
        summary["risk_tone"] = _risk_tone(
            summary["spy_pct"], summary["qqq_pct"], summary["vix_level"],
        )
    except Exception as exc:
        logger.debug(f"Yahoo macro fetch failed: {exc}")
        summary["source"] = "error"
    return summary


def _save_state(ctx: Dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(ctx, indent=2))
    except Exception:
        pass


def _load_state() -> Optional[Dict]:
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text())
    except Exception:
        pass
    return None


def refresh_macro_context(*, force: bool = False) -> Dict:
    """Refresh cache if stale or forced. Returns latest macro dict."""
    if not macro_context_enabled():
        return get_macro_context()
    now = time.time()
    interval = _refresh_interval_sec()
    with _CACHE["lock"]:
        cached = _CACHE.get("ctx")
        fetched_at = float(_CACHE.get("fetched_at") or 0.0)
        if cached and not force and (now - fetched_at) < interval:
            return dict(cached)
    ctx = _fetch_macro_context()
    with _CACHE["lock"]:
        _CACHE["ctx"] = ctx
        _CACHE["fetched_at"] = now
    _save_state(ctx)
    return ctx


def get_macro_context() -> Dict:
    """Cached macro — never blocks on Yahoo if cache/state exists."""
    if not macro_context_enabled():
        return {}
    with _CACHE["lock"]:
        cached = _CACHE.get("ctx")
        if cached:
            return dict(cached)
    disk = _load_state()
    if disk:
        with _CACHE["lock"]:
            _CACHE["ctx"] = disk
            _CACHE["fetched_at"] = float(disk.get("_fetched_at", 0.0) or 0.0)
        return dict(disk)
    return refresh_macro_context(force=True)


def tick_macro_context_if_due() -> Optional[Dict]:
    """Main-loop hook: refresh when due; log on meaningful change."""
    if not macro_context_enabled():
        return None
    now = time.time()
    with _CACHE["lock"]:
        fetched_at = float(_CACHE.get("fetched_at") or 0.0)
    if (now - fetched_at) < _refresh_interval_sec():
        return None
    prev = dict(get_macro_context())
    ctx = refresh_macro_context(force=True)
    ctx["_fetched_at"] = now
    _save_state(ctx)
    tone = ctx.get("risk_tone", "neutral")
    if (
        not prev
        or prev.get("risk_tone") != tone
        or abs(float(prev.get("vix_level", 0)) - float(ctx.get("vix_level", 0))) >= 1.5
    ):
        log.info(
            f"🌍 MACRO: SPY {ctx.get('spy_pct', 0):+.2f}% | "
            f"QQQ {ctx.get('qqq_pct', 0):+.2f}% | "
            f"VIX {ctx.get('vix_level', 0):.1f} ({ctx.get('vix_regime', '?')}) | "
            f"{tone}"
        )
    return ctx


def macro_context_line() -> str:
    """One-line macro block for council/Halim prompts (advisory, no veto)."""
    ctx = get_macro_context()
    if not ctx or ctx.get("source") == "unavailable":
        return ""
    tone = str(ctx.get("risk_tone", "neutral"))
    hints = {
        "risk_on": "broad market up — favor momentum longs; inverse ETFs need extra edge",
        "mild_risk_on": "market green — don't fight tape on inverse names without strong setup",
        "risk_off": "market weak — long scalps need conviction; inverse names still chop-prone",
        "mild_risk_off": "market red — stay selective; quality over quantity",
        "high_fear": "VIX elevated — tighter stops, smaller size mentally; still enter A+ setups",
        "neutral": "macro flat — trade the ticker tape, not the index",
        "mixed": "mixed macro — rely on ticker spike/PPO quality",
    }
    hint = hints.get(tone, hints["mixed"])
    return (
        f"MACRO (advisory, never veto): SPY {ctx.get('spy_pct', 0):+.2f}% "
        f"QQQ {ctx.get('qqq_pct', 0):+.2f}% VIX {ctx.get('vix_level', 0):.1f} "
        f"({ctx.get('vix_regime', '?')}) | {tone} — {hint}"
    )


def macro_ticker_hint(ticker: str) -> str:
    """Per-ticker macro note for inverse ETFs — context only."""
    sym = (ticker or "").upper()
    if sym not in INVERSE_ETFS:
        return ""
    ctx = get_macro_context()
    if not ctx:
        return ""
    tone = str(ctx.get("risk_tone", ""))
    if tone in ("risk_on", "mild_risk_on"):
        return (
            f"{sym} is inverse/bear — tape is risk-on (SPY {ctx.get('spy_pct', 0):+.2f}%). "
            "Enter only on exceptional spike; macro does NOT block entries."
        )
    if tone == "high_fear":
        return f"{sym} inverse — VIX {ctx.get('vix_level', 0):.1f}; chop risk high even on red days."
    return ""


# --- Legacy API (kept for callers) ---

def get_spy_qqq_vix(days: int = 30) -> dict:
    """Return latest snapshot for SPY, QQQ, VIX (daily history)."""
    yf = _try_import_yfinance()
    if yf is None:
        return {"spy": None, "qqq": None, "vix": None}
    try:
        spy = yf.Ticker("SPY").history(period=f"{days}d", interval="1d")
        qqq = yf.Ticker("QQQ").history(period=f"{days}d", interval="1d")
        vix = yf.Ticker("^VIX").history(period=f"{days}d", interval="1d")
        return {"spy": spy, "qqq": qqq, "vix": vix}
    except Exception as exc:
        logger.debug(f"Yahoo Finance fetch failed: {exc}")
        return {"spy": None, "qqq": None, "vix": None}


def summarize_market_context() -> dict:
    """Cached macro — avoids Yahoo on every entry path."""
    ctx = get_macro_context()
    if ctx:
        return ctx
    return {
        "spy_trend": "unknown",
        "qqq_trend": "unknown",
        "vix_level": 0.0,
        "vix_regime": "low",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_ib_market_snapshot(ib_connector) -> Dict:
    """
    Fetch real-time market context from IB Gateway.
    Uses IB contract details for SPY, QQQ, VIX, and sector ETFs.
    """
    snapshot = {
        "spy_price": None,
        "qqq_price": None,
        "vix_level": 0.0,
        "sector_etfs": {},
        "market_status": "unknown",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if not ib_connector or not hasattr(ib_connector, "ib"):
        return snapshot

    try:
        try:
            from ib_insync import Stock
            spy = Stock("SPY", "ARCA")
            ib_connector.ib.qualifyContracts(spy)
            spy_ticker = ib_connector.ib.reqMktData(spy, "", False, False)
            if spy_ticker and hasattr(spy_ticker, "last"):
                snapshot["spy_price"] = float(spy_ticker.last)
        except Exception:
            pass

        try:
            from ib_insync import Stock
            qqq = Stock("QQQ", "NASDAQ")
            ib_connector.ib.qualifyContracts(qqq)
            qqq_ticker = ib_connector.ib.reqMktData(qqq, "", False, False)
            if qqq_ticker and hasattr(qqq_ticker, "last"):
                snapshot["qqq_price"] = float(qqq_ticker.last)
        except Exception:
            pass

        try:
            from ib_insync import Index
            vix = Index("VIX", "CBOE")
            ib_connector.ib.qualifyContracts(vix)
            vix_ticker = ib_connector.ib.reqMktData(vix, "", False, False)
            if vix_ticker and hasattr(vix_ticker, "last"):
                snapshot["vix_level"] = max(0.0, float(vix_ticker.last))
        except Exception:
            yf = _try_import_yfinance()
            if yf:
                try:
                    vix_hist = yf.Ticker("^VIX").history(period="1d")
                    if not vix_hist.empty and "close" in vix_hist.columns:
                        snapshot["vix_level"] = float(vix_hist["close"].iloc[-1])
                except Exception:
                    pass

        snapshot["market_status"] = "open"
    except Exception as exc:
        logger.debug(f"IB market snapshot error: {exc}")

    return snapshot
