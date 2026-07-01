#!/usr/bin/env python3
"""Swing bar fetch + DataFrame parsing tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from core.swing_bars import bars_len, bars_to_closes, fetch_swing_bars
from core.swing_intel import _atr_pct, analyze_swing_technical


def _uptrend_df(n: int = 30) -> pd.DataFrame:
    base = 100.0
    rows = []
    for i in range(n):
        c = base + i * 0.8
        rows.append({"open": c - 0.2, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1000})
    return pd.DataFrame(rows)


def test_bars_to_closes_dataframe():
    df = _uptrend_df(25)
    closes = bars_to_closes(df)
    assert len(closes) == 25
    assert closes[-1] > closes[0]


def test_atr_pct_from_dataframe():
    df = _uptrend_df(20)
    atr = _atr_pct(df, period=5)
    assert atr > 0


def test_analyze_swing_technical_with_dataframes():
    runner = MagicMock()
    df = _uptrend_df(30)

    def fake_fetch(_runner, _sym, _bar_size, _duration):
        return df

    with patch("core.swing_intel._fetch_bars", side_effect=fake_fetch):
        tech = analyze_swing_technical(runner, "SPY")
    assert tech["bias"] == "long"
    assert tech["strength"] > 0
    assert tech["atr_pct"] >= 0


def test_fetch_swing_bars_uses_target_monitor():
    df = _uptrend_df(15)
    dm = MagicMock()
    dm.fetch_historical.return_value = df
    runner = MagicMock()
    runner._target_monitors = {"SPY": dm}
    runner.ib = MagicMock()

    with patch("core.ib_sync.ib_blocking_calls_safe", return_value=True):
        out = fetch_swing_bars(runner, "SPY", "1 hour", "10 D")
    assert len(out) == 15
    dm.fetch_historical.assert_called_once()


def test_bars_len_empty():
    assert bars_len(None) == 0
    assert bars_len(pd.DataFrame()) == 0
