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
        "vix_level": None,
        "vix_regime": "unknown",
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