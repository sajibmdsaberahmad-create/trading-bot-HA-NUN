#!/usr/bin/env python3
"""Swing intel scoring tests."""
from __future__ import annotations

from core.swing_intel import _rsi, _score_analysis, _trend_from_closes


def test_rsi_bounds():
    closes = [float(100 + i * 0.5) for i in range(20)]
    r = _rsi(closes)
    assert 0 <= r <= 100


def test_trend_uptrend():
    closes = [float(100 + i) for i in range(25)]
    t = _trend_from_closes(closes, "1h")
    assert t["bias"] == "long"
    assert t["strength"] > 0


def test_score_analysis_long():
    tech = {"bias": "long", "strength": 0.6, "atr_pct": 3.0, "timeframes": {"1h": {"reason": "1h_uptrend"}}}
    macro = {"swing_favorable": True, "risk_tone": "mild_risk_on"}
    web = {"web_sentiment": "bullish"}
    ib = {"fundamentals": {"pe": 20}, "news_headlines": ["headline"]}
    v = _score_analysis(tech, ib, macro, web)
    assert v["enter"] is True
    assert v["confidence"] > 0.3
