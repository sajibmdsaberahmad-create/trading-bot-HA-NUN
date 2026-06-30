"""Shared scalper entry filters (uptrend gate, etc.)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.risk import compute_atr, safe_vwap


def only_uptrend(df: pd.DataFrame, current_px: float, min_bars: int = 20) -> bool:
    """
    USER METHODOLOGY: Uptrend filter — must be loose enough to catch
    institutional algo waves early, not late.
    """
    if len(df) < min_bars:
        return False
    n = min(len(df), 20)
    closes = df["close"].values[-n:]
    volumes = df["volume"].values[-n:]
    sma20 = np.mean(closes)

    if current_px <= sma20 * 0.99:
        return False

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vwap = safe_vwap(typical[-20:], volumes[-20:])
    if current_px <= vwap * 0.99:
        return False

    rising = sum(1 for i in range(-8, 0) if i > -len(closes) and closes[i] >= closes[i - 1])
    if rising < 2:
        return False

    atr = compute_atr(df, period=10)
    if atr <= 0 or atr > current_px * 0.10:
        return False

    return True
