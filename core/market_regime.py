#!/usr/bin/env python3
"""
core/market_regime.py — Advanced market regime classification.

Detects more than just bull/bear:
- bull_trend, bear_trend, sideways, high_volatility, low_volatility
- accumulation, distribution, breakout, breakdown, gap_up, gap_down
- regime transitions and persistence

Uses Yahoo Finance via yfinance for broader market context (SPY, QQQ, VIX).
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("MARKET_REGIME")

MIN_FULL_BARS = 50
MIN_SHORT_BARS = 5


class MarketRegime(Enum):
    UNKNOWN = "unknown"
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    SIDEWAYS = "sideways"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    GAP_UP = "gap_up"
    GAP_DOWN = "gap_down"

@dataclass
class RegimeResult:
    regime: MarketRegime
    confidence: float
    trend_strength: float
    volatility_percentile: float
    momentum: float
    volume_regime: str
    recommendation: str

class MarketRegimeDetector:
    """
    Detects current market regime using multiple signals:
    - Trend direction and strength (SMA, EMA, slope)
    - Volatility regime (VIX or ATR-based)
    - Volume regime (rising/declining volume)
    - Momentum (RSI, MACD, rate of change)
    - Gap and breakout detection
    """

    def __init__(self):
        self._current_regime: Optional[RegimeResult] = None

    def _classify_short(self, df: pd.DataFrame) -> RegimeResult:
        """Regime from 5–49 bars — live stream / spike path (no 50-bar wait)."""
        closes = df["close"].astype(float).values
        n = len(closes)
        current_px = float(closes[-1])
        slope = (closes[-1] - closes[0]) / (closes[0] + 1e-9)
        lookback = min(5, n - 1)
        roc_5 = (closes[-1] / closes[-1 - lookback] - 1) * 100 if lookback > 0 else 0.0

        tr = np.abs(np.diff(closes))
        atr = float(np.mean(tr[-min(14, len(tr)):])) if len(tr) else 0.0
        volatility_pct = (atr / (current_px + 1e-9)) * 100

        volumes = (
            df["volume"].astype(float).values
            if "volume" in df.columns else np.ones(n, dtype=float)
        )
        vol_short = float(np.mean(volumes[-min(5, n):]))
        vol_long = float(np.mean(volumes[-min(20, n):]))
        vol_ratio = vol_short / (vol_long + 1e-9)

        gap_pct = 0.0
        if "open" in df.columns and n >= 2:
            gap_pct = (
                (float(df["open"].iloc[-1]) - closes[-2])
                / (closes[-2] + 1e-9) * 100
            )

        regime = MarketRegime.SIDEWAYS
        confidence = 0.4

        if abs(gap_pct) > 1.5:
            regime = MarketRegime.GAP_UP if gap_pct > 0 else MarketRegime.GAP_DOWN
            confidence = min(0.85, 0.5 + abs(gap_pct) / 10.0)
        elif volatility_pct > 3.0 or vol_ratio > 2.0:
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = min(0.8, 0.45 + vol_ratio * 0.12)
        elif slope > 0.01 and roc_5 > 0.25:
            regime = MarketRegime.BULL_TREND
            confidence = min(0.75, 0.45 + abs(slope) * 10.0)
        elif slope < -0.01 and roc_5 < -0.25:
            regime = MarketRegime.BEAR_TREND
            confidence = min(0.75, 0.45 + abs(slope) * 10.0)
        elif abs(slope) < 0.008 and volatility_pct < 1.5:
            regime = MarketRegime.SIDEWAYS
            confidence = 0.55
        elif vol_ratio > 1.35 and slope > 0.003:
            regime = MarketRegime.BREAKOUT
            confidence = 0.58
        elif vol_ratio > 1.35 and slope < -0.003:
            regime = MarketRegime.BREAKDOWN
            confidence = 0.58
        elif roc_5 > 0.12:
            regime = MarketRegime.BULL_TREND
            confidence = 0.42
        elif roc_5 < -0.12:
            regime = MarketRegime.BEAR_TREND
            confidence = 0.42
        elif volatility_pct > 2.0:
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = 0.45

        volume_regime = (
            "rising" if vol_ratio > 1.2
            else "declining" if vol_ratio < 0.8
            else "normal"
        )
        return RegimeResult(
            regime=regime,
            confidence=round(confidence, 2),
            trend_strength=round(abs(slope), 3),
            volatility_percentile=round(volatility_pct, 2),
            momentum=round(roc_5, 2),
            volume_regime=volume_regime,
            recommendation=self._recommend(regime, confidence, volatility_pct),
        )

    def classify(self, df: pd.DataFrame, vix_df: Optional[pd.DataFrame] = None) -> RegimeResult:
        if df is None or len(df) < MIN_SHORT_BARS:
            return RegimeResult(
                regime=MarketRegime.UNKNOWN, confidence=0.0,
                trend_strength=0.0, volatility_percentile=50.0,
                momentum=0.0, volume_regime="normal",
                recommendation="Insufficient data"
            )

        if len(df) < MIN_FULL_BARS:
            result = self._classify_short(df)
            self._current_regime = result
            return result

        closes = df["close"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])

        # Trend
        sma20 = np.mean(closes[-20:])
        sma50 = np.mean(closes[-50:])
        ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
        slope_20 = (closes[-1] - closes[-20]) / (closes[-20] + 1e-9)

        # Volatility
        tr = np.abs(np.diff(closes))
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
        volatility_pct = (atr / (current_px + 1e-9)) * 100

        # Momentum
        roc_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        roc_20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else 0

        # Volume regime
        vol_avg20 = np.mean(volumes[-20:])
        vol_avg5 = np.mean(volumes[-5:])
        vol_ratio = vol_avg5 / (vol_avg20 + 1e-9)

        # Gap detection
        gap_pct = 0.0
        if len(closes) >= 2:
            prev_close = closes[-2]
            open_price = float(df["open"].iloc[-1]) if "open" in df.columns else current_px
            gap_pct = (open_price - prev_close) / (prev_close + 1e-9) * 100

        # Breakout/breakdown detection
        resistance = np.max(closes[-20:-1])
        support = np.min(closes[-20:-1])
        breakout = current_px > resistance * 1.01
        breakdown = current_px < support * 0.99

        # Regime scoring
        regime_scores = {}
        regime_scores[MarketRegime.BULL_TREND] = max(0, slope_20 * 10) + max(0, roc_5) + (5 if current_px > sma20 > sma50 else 0)
        regime_scores[MarketRegime.BEAR_TREND] = max(0, -slope_20 * 10) + max(0, -roc_5) + (5 if current_px < sma20 < sma50 else 0)
        regime_scores[MarketRegime.SIDEWAYS] = (5 if abs(slope_20) < 0.02 else 0)
        regime_scores[MarketRegime.HIGH_VOLATILITY] = max(0, volatility_pct - 2) * 2
        regime_scores[MarketRegime.LOW_VOLATILITY] = max(0, 1 - volatility_pct) * 2
        regime_scores[MarketRegime.ACCUMULATION] = (5 if current_px > sma20 and vol_ratio > 1.2 and slope_20 > 0 else 0)
        regime_scores[MarketRegime.DISTRIBUTION] = (5 if current_px < sma20 and vol_ratio > 1.2 and slope_20 < 0 else 0)
        regime_scores[MarketRegime.BREAKOUT] = (20 if breakout else 0)
        regime_scores[MarketRegime.BREAKDOWN] = (20 if breakdown else 0)
        regime_scores[MarketRegime.GAP_UP] = (10 if gap_pct > 1.0 else 0)
        regime_scores[MarketRegime.GAP_DOWN] = (10 if gap_pct < -1.0 else 0)

        best_regime = max(regime_scores, key=regime_scores.get)
        best_score = regime_scores[best_regime]
        confidence = min(best_score / 20.0, 1.0)

        # VIX adjustment
        if vix_df is not None and len(vix_df) > 0:
            vix = float(vix_df["close"].iloc[-1])
            if vix > 30:
                confidence = max(confidence, 0.8)
                if best_regime not in (MarketRegime.HIGH_VOLATILITY,):
                    best_regime = MarketRegime.HIGH_VOLATILITY

        recommendation = self._recommend(best_regime, confidence, volatility_pct)

        result = RegimeResult(
            regime=best_regime,
            confidence=round(confidence, 2),
            trend_strength=round(abs(slope_20), 3),
            volatility_percentile=round(volatility_pct, 2),
            momentum=round(roc_5, 2),
            volume_regime="rising" if vol_ratio > 1.2 else "declining" if vol_ratio < 0.8 else "normal",
            recommendation=recommendation,
        )
        self._current_regime = result
        return result

    def _recommend(self, regime: MarketRegime, confidence: float, vol_pct: float) -> str:
        if confidence < 0.3:
            return "Low confidence — wait for clearer signals"
        recs = {
            MarketRegime.BULL_TREND: "Favor long setups, use wider stops",
            MarketRegime.BEAR_TREND: "Favor shorts or stay flat",
            MarketRegime.SIDEWAYS: "Mean reversion plays, tighten targets",
            MarketRegime.HIGH_VOLATILITY: "Reduce size, widen stops, avoid chasing",
            MarketRegime.LOW_VOLATILITY: "Normal sizing, watch for breakout",
            MarketRegime.ACCUMULATION: "Institutional buying likely — favor longs",
            MarketRegime.DISTRIBUTION: "Institutional selling likely — favor shorts or avoid",
            MarketRegime.BREAKOUT: "Momentum play — enter on pullback",
            MarketRegime.BREAKDOWN: "Avoid longs, potential short",
            MarketRegime.GAP_UP: "Watch for exhaustion; avoid chasing",
            MarketRegime.GAP_DOWN: "Potential bounce play if oversold",
        }
        return recs.get(regime, "Neutral — follow primary trend")

    @property
    def current(self) -> Optional[RegimeResult]:
        return self._current_regime


def resolve_regime(
    detector: MarketRegimeDetector,
    df: Optional[pd.DataFrame],
    *,
    spike_ratio: float = 1.0,
    vol_ratio: float = 1.0,
) -> tuple[RegimeResult, str]:
    """
    Classify bars and return (result, telemetry tag).
    Tag uses spike/vol heuristics when bars are thin — never bare 'unknown' on spikes.
    """
    from core.trade_telemetry import regime_tag

    if df is not None and len(df) >= MIN_SHORT_BARS:
        try:
            result = detector.classify(df)
        except Exception:
            result = RegimeResult(
                regime=MarketRegime.UNKNOWN, confidence=0.0,
                trend_strength=0.0, volatility_percentile=50.0,
                momentum=0.0, volume_regime="normal",
                recommendation="classify error",
            )
    else:
        result = RegimeResult(
            regime=MarketRegime.UNKNOWN, confidence=0.0,
            trend_strength=0.0, volatility_percentile=50.0,
            momentum=0.0, volume_regime="normal",
            recommendation="Insufficient data",
        )
    tag = regime_tag(result, spike_ratio=spike_ratio, vol_ratio=vol_ratio)
    return result, tag


def regime_from_macro(ctx: dict) -> RegimeResult:
    """SPY/VIX snapshot → regime when no ticker bar history (consciousness, copilot)."""
    spy_pct = float(ctx.get("spy_pct", 0) or 0)
    vix = float(ctx.get("vix_level", 0) or ctx.get("vix", 0) or 0)
    if vix >= 28:
        regime = MarketRegime.HIGH_VOLATILITY
        conf = 0.75
    elif spy_pct >= 0.45:
        regime = MarketRegime.BULL_TREND
        conf = 0.6
    elif spy_pct <= -0.45:
        regime = MarketRegime.BEAR_TREND
        conf = 0.6
    elif abs(spy_pct) < 0.12:
        regime = MarketRegime.SIDEWAYS
        conf = 0.5
    elif spy_pct > 0:
        regime = MarketRegime.BULL_TREND
        conf = 0.45
    else:
        regime = MarketRegime.BEAR_TREND
        conf = 0.45
    return RegimeResult(
        regime=regime,
        confidence=conf,
        trend_strength=round(abs(spy_pct) / 2.0, 3),
        volatility_percentile=min(99.0, max(5.0, vix * 2.5)),
        momentum=spy_pct,
        volume_regime="normal",
        recommendation="Macro SPY/VIX inference",
    )