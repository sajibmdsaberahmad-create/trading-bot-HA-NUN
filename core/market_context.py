#!/usr/bin/env python3
"""
core/market_context.py — Broader market context from Yahoo Finance.

Fetches SPY, QQQ, and VIX data to give the AI regime detector
and self-improver a view beyond the single ticker.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from core.notify import log
from typing import Dict

logger = logging.getLogger("MARKET_CONTEXT")

def _try_import_yfinance():
    try:
        import yfinance as yf
        return yf
    except Exception:
        return None


def get_spy_qqq_vix(days: int = 30) -> dict:
    """Return latest snapshot for SPY, QQQ, VIX."""
    yf = _try_import_yfinance()
    if yf is None:
        return {"spy": None, "qqq": None, "vix": None}
    try:
        spy = yf.Ticker("SPY").history(period=f"{days}d", interval="1d")
        qqq = yf.Ticker("QQQ").history(period=f"{days}d", interval="1d")
        vix = yf.Ticker("^VIX").history(period=f"{days}d", interval="1d")
        return {
            "spy": spy,
            "qqq": qqq,
            "vix": vix,
        }
    except Exception as exc:
        logger.debug(f"Yahoo Finance fetch failed: {exc}")
        return {"spy": None, "qqq": None, "vix": None}


def summarize_market_context() -> dict:
    data = get_spy_qqq_vix()
    summary = {
        "spy_trend": "unknown",
        "qqq_trend": "unknown",
        "vix_level": 0.0,
        "vix_regime": "low",
        "timestamp": datetime.utcnow().isoformat(),
    }
    for key in ("spy", "qqq"):
        df = data.get(key)
        if df is not None and not df.empty and "close" in df.columns:
            closes = df["close"].values
            if len(closes) >= 5:
                slope = (closes[-1] - closes[-5]) / (closes[-5] + 1e-9)
                summary[f"{key}_trend"] = "up" if slope > 0.005 else "down" if slope < -0.005 else "flat"
    vix = data.get("vix")
    if vix is not None and not vix.empty and "close" in vix.columns:
        level = float(vix["close"].iloc[-1])
        summary["vix_level"] = round(level, 2)
        summary["vix_regime"] = "high" if level > 30 else "elevated" if level > 20 else "low"
    return summary


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
        "timestamp": datetime.utcnow().isoformat(),
    }
    
    if not ib_connector or not hasattr(ib_connector, 'ib'):
        return snapshot
    
    try:
        # Fetch live market data for key indices
        try:
            from ib_insync import Stock
            spy = Stock('SPY', 'ARCA')
            ib_connector.ib.qualifyContracts(spy)
            spy_ticker = ib_connector.ib.reqMktData(spy, '', False, False)
            if spy_ticker and hasattr(spy_ticker, 'last'):
                snapshot["spy_price"] = float(spy_ticker.last)
        except Exception:
            pass
        
        try:
            from ib_insync import Stock
            qqq = Stock('QQQ', 'NASDAQ')
            ib_connector.ib.qualifyContracts(qqq)
            qqq_ticker = ib_connector.ib.reqMktData(qqq, '', False, False)
            if qqq_ticker and hasattr(qqq_ticker, 'last'):
                snapshot["qqq_price"] = float(qqq_ticker.last)
        except Exception:
            pass
        
        # VIX from CBOE - try IB first, then Yahoo
        try:
            from ib_insync import Index
            vix = Index('VIX', 'CBOE')
            ib_connector.ib.qualifyContracts(vix)
            vix_ticker = ib_connector.ib.reqMktData(vix, '', False, False)
            if vix_ticker and hasattr(vix_ticker, 'last'):
                snapshot["vix_level"] = max(0.0, float(vix_ticker.last))
        except Exception:
            # Fallback to Yahoo Finance
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