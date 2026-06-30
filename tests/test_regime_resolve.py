#!/usr/bin/env python3
"""Regime classification — short bars and spike fallback."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.market_regime import MarketRegime, MarketRegimeDetector, resolve_regime
from core.trade_telemetry import regime_tag


def _synthetic_bars(n: int, *, trend: float = 0.002, vol: float = 1000.0) -> pd.DataFrame:
    px = 10.0
    rows = []
    for i in range(n):
        px *= 1.0 + trend
        rows.append({"open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": vol * (1 + i * 0.1)})
    return pd.DataFrame(rows)


def test_short_bars_not_unknown():
    det = MarketRegimeDetector()
    df = _synthetic_bars(12, trend=0.003)
    result = det.classify(df)
    assert result.regime != MarketRegime.UNKNOWN
    assert result.confidence > 0.3


def test_resolve_regime_uses_spike_when_no_bars():
    det = MarketRegimeDetector()
    _, tag = resolve_regime(det, None, spike_ratio=3.5, vol_ratio=2.0)
    assert tag == "high_vol_spike"
    assert "unknown" not in tag


def test_regime_tag_maps_bear_trend():
    det = MarketRegimeDetector()
    df = _synthetic_bars(15, trend=-0.004)
    result = det.classify(df)
    tag = regime_tag(result, spike_ratio=1.0, vol_ratio=1.0)
    assert tag in ("bear_trend", "breakdown", "high_vol_spike", "choppy_consolidation")
    assert tag != "unknown"
