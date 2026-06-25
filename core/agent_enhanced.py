#!/usr/bin/env python3
"""
core/agent_enhanced.py — Enhanced AI agent with deep reasoning capabilities.

The standard PPO agent is good, but this module adds:
1. MARKET REGIME CLASSIFIER — Detects trending/choppy/volatile/calm regimes
2. ENSEMBLE THINKING — Combines PPO with rule-based & statistical models
3. CONFIDENCE SCORING — Only acts when confidence exceeds threshold
4. CHAIN-OF-THOUGHT REASONING — Logs the "why" behind every decision
5. ONLINE ADAPTATION — Dynamically adjusts to market conditions
6. MULTI-TIMEFRAME ANALYSIS — Combines signals from 1m, 5m, 1d

All decisions still pass through guardrails (core/ai_guardrails.py) and
risk management (core/risk.py) — this adds intelligence, not risk override.
"""

import os
import json
import time
import math
from datetime import datetime
from typing import Optional, Tuple, Dict, List, Any, Callable
from dataclasses import dataclass, field
from collections import deque
from enum import Enum

import numpy as np
import pandas as pd
from collections import defaultdict

try:
    import gymnasium as gym
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
except ImportError:
    raise SystemExit("ERROR: gymnasium/stable-baselines3 not installed.")

from core.config import BotConfig
from core.env import TradingEnv
from core.notify import log


# ═════════════════════════════════════════════════════════════════════════════
# MARKET REGIME CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════

class MarketRegime(Enum):
    """Market regime classifications."""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    BREAKOUT = "breakout"
    UNKNOWN = "unknown"


@dataclass
class RegimeResult:
    """Result of market regime analysis."""
    regime: MarketRegime
    confidence: float                 # 0.0 to 1.0
    trend_strength: float             # 0 to 100
    volatility_percentile: float      # 0 to 100
    momentum: float                   # -1 to 1
    volume_regime: str                # "normal" | "elevated" | "low"
    recommendation: str               # Human-readable guidance
    stability: float = 0.5            # Regime stability score (0-1), computed by classifier


class MarketRegimeClassifier:
    """
    Classifies the current market regime using multiple indicators.
    
    Uses:
    - ADX for trend strength
    - ATR percentile for volatility regime
    - Moving average alignment for trend direction
    - Volume vs average for participation
    - Bollinger Band width for compression/expansion
    """
    
    def __init__(self, lookback_short: int = 10, lookback_long: int = 50):
        self.lookback_short = lookback_short
        self.lookback_long = lookback_long
        self._regime_history: deque = deque(maxlen=100)
    
    def classify(self, df: pd.DataFrame) -> RegimeResult:
        """
        Classify the current market regime from OHLCV data.
        
        Args:
            df: DataFrame with [open, high, low, close, volume], minimum 50 rows
            
        Returns:
            RegimeResult with classification and confidence
        """
        if len(df) < 50:
            return RegimeResult(
                regime=MarketRegime.UNKNOWN,
                confidence=0.0,
                trend_strength=0.0,
                volatility_percentile=50.0,
                momentum=0.0,
                volume_regime="normal",
                recommendation="Insufficient data",
            )
        
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])
        
        # ── 1. Trend Strength (simplified ADX) ────────────────────────────
        lookback_adx = min(20, len(df) - 1)
        up_move = np.diff(highs[-lookback_adx-1:])
        down_move = -np.diff(lows[-lookback_adx-1:])
        
        plus_dm = np.maximum(up_move, 0) * (up_move > down_move)
        minus_dm = np.maximum(down_move, 0) * (down_move > up_move)
        
        h_win = highs[-lookback_adx:]
        l_win = lows[-lookback_adx:]
        prev_close = closes[-lookback_adx - 1:-1]  # previous close aligned to each bar
        tr = np.maximum(
            h_win - l_win,
            np.maximum(
                np.abs(h_win - prev_close),
                np.abs(l_win - prev_close)
            )
        )
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
        
        plus_di = 100 * np.mean(plus_dm[-14:]) / (atr + 1e-9)
        minus_di = 100 * np.mean(minus_dm[-14:]) / (atr + 1e-9)
        dx = abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100
        adx = dx  # simplified ADX
        
        # ── 2. Trend Direction via MA alignment ───────────────────────────
        sma10 = np.mean(closes[-10:])
        sma20 = np.mean(closes[-20:])
        sma50 = np.mean(closes[-50:]) if len(closes) >= 50 else sma20
        
        # Price relative to MAs
        above_sma10 = current_px > sma10
        above_sma20 = current_px > sma20
        above_sma50 = current_px > sma50
        
        # MA slope (derivative)
        sma10_slope = (sma10 - np.mean(closes[-12:-2])) / (np.mean(closes[-12:-2]) + 1e-9)
        sma20_slope = (sma20 - np.mean(closes[-22:-2])) / (np.mean(closes[-22:-2]) + 1e-9)
        
        # MA alignment score (-3 to +3)
        alignment = sum([above_sma10, above_sma20, above_sma50])
        if sma10 > sma20 > sma50:
            upward_alignment = True
        elif sma10 < sma20 < sma50:
            upward_alignment = False
        else:
            upward_alignment = None  # mixed or ranging
        
        # ── 3. Momentum ──────────────────────────────────────────────────
        ret_short = np.log(closes[-1] / closes[-min(6, len(closes))]) if len(closes) >= 6 else 0
        ret_medium = np.log(closes[-1] / closes[-min(21, len(closes))]) if len(closes) >= 21 else 0
        momentum = float(np.clip((ret_short * 0.7 + ret_medium * 0.3) * 20, -1.0, 1.0))
        
        # ── 4. Volatility Regime ─────────────────────────────────────────
        # ATR percentile over window
        lookback_atr = min(100, len(df))
        atr_series = []
        for i in range(14, lookback_atr):
            tr_i = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            atr_series.append(tr_i)
        
        short_atr = np.mean(atr_series[-5:]) if len(atr_series) >= 5 else np.mean(atr_series) if atr_series else 0
        long_atr = np.mean(atr_series) if atr_series else 0
        
        vol_ratio = short_atr / (long_atr + 1e-9)
        
        # Bollinger Band width (compression/expansion)
        bb_mid = np.mean(closes[-20:])
        bb_std = np.std(closes[-20:])
        bb_width = bb_std / (bb_mid + 1e-9)
        
        # Long-term BB width for percentile
        bb_widths = []
        for i in range(20, len(closes)):
            bb_mid_i = np.mean(closes[i-20:i])
            bb_std_i = np.std(closes[i-20:i])
            bb_widths.append(bb_std_i / (bb_mid_i + 1e-9))
        
        if bb_widths:
            percentile = sum(1 for w in bb_widths if w < bb_width) / len(bb_widths) * 100
        else:
            percentile = 50.0
        
        vol_percentile = float(np.clip(percentile, 0, 100))
        
        # ── 5. Volume Analysis ──────────────────────────────────────────
        vol_avg20 = np.mean(volumes[-20:])
        vol_recent = np.mean(volumes[-5:])
        vol_ratio_v = vol_recent / (vol_avg20 + 1e-9)
        
        if vol_ratio_v > 1.5:
            volume_regime = "elevated"
        elif vol_ratio_v < 0.5:
            volume_regime = "low"
        else:
            volume_regime = "normal"
        
        # ── Classification Logic ──────────────────────────────────────────
        
        # Detect breakout: strong move with elevated volume
        if vol_ratio_v > 1.3 and abs(ret_short) > 0.015 and adx > 30:
            regime = MarketRegime.BREAKOUT
            confidence = min(1.0, adx / 60 * vol_ratio_v / 2)
            direction = "UP" if ret_short > 0 else "DOWN"
            recommendation = f"Breakout detected ({direction}). "
            if ret_short > 0:
                recommendation += "Strong momentum with volume confirmation. Look for entry on pullback."
            else:
                recommendation += "Strong selling pressure. Wait for stabilization."
        
        # Trending up: strong ADX + upward MA alignment + positive momentum
        elif adx > 25 and upward_alignment and momentum > 0.2:
            regime = MarketRegime.TRENDING_UP
            confidence = min(1.0, adx / 50)
            recommendation = "Uptrend with strong directional movement. "
            if momentum > 0.5:
                recommendation += "Momentum is strong. Consider trend-following entry."
            else:
                recommendation += "Trend intact but momentum cooling. Consider pullback entry."
        
        # Trending down: strong ADX + downward alignment + negative momentum
        elif adx > 25 and not upward_alignment and momentum < -0.2:
            regime = MarketRegime.TRENDING_DOWN
            confidence = min(1.0, adx / 50)
            recommendation = "Downtrend with strong directional movement. Avoid long entries."
        
        # High volatility: BB width expansion + elevated vol ratio
        elif vol_ratio > 1.3 and vol_percentile > 70:
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = min(1.0, vol_ratio / 2)
            recommendation = "High volatility regime. "
            if abs(momentum) < 0.3:
                recommendation += "Choppy with wide ranges. Use wider stops or reduce position size."
            else:
                recommendation += f"Directional volatility ({'up' if momentum > 0 else 'down'}). "
                recommendation += "Consider reduced position sizing."
        
        # Low volatility: BB width compression + low vol ratio
        elif vol_ratio < 0.7 and vol_percentile < 30:
            regime = MarketRegime.LOW_VOLATILITY
            confidence = min(1.0, (1 - vol_ratio) * 2)
            recommendation = "Low volatility regime. Market is quiet. "
            recommendation += "Be patient for expansion. Tight stops may get triggered by noise."
        
        # Ranging: weak ADX + mixed alignment + low momentum
        elif adx < 20 and upward_alignment is None and abs(momentum) < 0.3:
            regime = MarketRegime.RANGING
            confidence = min(1.0, (25 - adx) / 25)
            recommendation = "Ranging market with no clear direction. "
            recommendation += "Use mean-reversion strategy or wait for breakout."
        
        else:
            regime = MarketRegime.UNKNOWN
            confidence = 0.3
            recommendation = f"Mixed signals (ADX={adx:.0f}, momentum={momentum:.2f}). "
            recommendation += "Exercise caution."
        
        # Adjust confidence based on alignment clarity
        if regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            if alignment == 3:
                confidence = min(1.0, confidence * 1.2)
            elif alignment <= 1:
                confidence *= 0.7
        
        # Store history
        self._regime_history.append({
            "regime": regime.value,
            "confidence": confidence,
            "adx": adx,
            "momentum": momentum,
            "vol_ratio": vol_ratio,
            "timestamp": time.time(),
        })
        
        stability = self.regime_stability(n_last=10)
        
        return RegimeResult(
            regime=regime,
            confidence=float(np.clip(confidence, 0.0, 1.0)),
            trend_strength=float(np.clip(adx, 0, 100)),
            volatility_percentile=vol_percentile,
            momentum=momentum,
            volume_regime=volume_regime,
            recommendation=recommendation,
            stability=stability,
        )
    
    def regime_stability(self, n_last: int = 10) -> float:
        """
        How stable the regime has been (0 = flipping constantly, 1 = rock solid).
        
        Useful for knowing whether to trust the regime classification.
        """
        if len(self._regime_history) < n_last:
            return 0.5
        
        recent = list(self._regime_history)[-n_last:]
        regimes = [r["regime"] for r in recent]
        unique = set(regimes)
        
        # Stability score based on regime consistency
        if len(unique) == 1:
            return 1.0
        elif len(unique) <= 2:
            return 0.7
        elif len(unique) <= 3:
            return 0.4
        else:
            return 0.1


# ═════════════════════════════════════════════════════════════════════════════
# CONFIDENCE & REASONING ENGINE
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ReasoningChain:
    """Complete reasoning behind a single trading decision."""
    timestamp: float
    market_regime: str
    regime_confidence: float
    
    # PPO model output
    ppo_action: int
    ppo_action_name: str
    ppo_value_estimate: float  # Critic's value estimate
    ppo_probabilities: List[float]  # Action probabilities
    
    # Contextual factors
    trend_score: float           # -1 to 1
    volatility_score: float      # 0 to 1
    momentum_score: float        # -1 to 1
    mean_reversion_signal: float # -3 to 3
    volume_signal: float         # -1 to 1
    
    # Confidence
    composite_confidence: float  # 0 to 1
    decision_threshold: float    # Minimum confidence required
    
    # Guardrails
    passed_guardrails: bool
    guardrail_warnings: List[str]
    
    # Risk info
    risk_override: bool = False
    risk_override_reason: str = ""
    
    # Final decision
    final_action: int = 0
    final_action_name: str = "HOLD"
    
    def summary(self) -> str:
        """Human-readable summary of the reasoning chain."""
        lines = [
            f"🧠 REASONING for {self.final_action_name}",
            f"   Market: {self.market_regime} (conf: {self.regime_confidence:.0%})",
            f"   PPO wants: {self.ppo_action_name} (v={self.ppo_value_estimate:.3f}, probs={[f'{p:.1%}' for p in self.ppo_probabilities]})",
            f"   Trend: {self.trend_score:+.2f} | Vol: {self.volatility_score:.2f} | Mom: {self.momentum_score:+.2f}",
            f"   MRZ: {self.mean_reversion_signal:+.1f} | VolSignal: {self.volume_signal:+.1f}",
            f"   Confidence: {self.composite_confidence:.0%} (threshold: {self.decision_threshold:.0%})",
        ]
        if self.guardrail_warnings:
            lines.append(f"   ⚠️  Guardrails: {'; '.join(self.guardrail_warnings)}")
        if self.risk_override:
            lines.append(f"   🔒 Risk override: {self.risk_override_reason}")
        return "\n".join(lines)


class ConfidenceScorer:
    """
    Multi-factor confidence scoring for agent decisions.
    
    Combines:
    - PPO value estimate (how good the model thinks the state is)
    - Action probability spread (clear vs. uncertain pick)
    - Regime clarity (stable regimes = higher confidence)
    - Feature coherence (features within normal ranges)
    - Recent performance (how well the agent has been doing)
    """
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._recent_rewards: deque = deque(maxlen=50)
        self._recent_actions: deque = deque(maxlen=50)
        self._baseline_value: Optional[float] = None
    
    def score(self, 
              ppo_probs: np.ndarray,
              ppo_value: float,
              regime_result: RegimeResult,
              features: Optional[np.ndarray] = None,
              last_n_rewards: Optional[List[float]] = None,
              ) -> float:
        """
        Compute composite confidence score for a decision.
        
        Returns: 0.0 (low confidence) to 1.0 (maximum confidence)
        """
        factors = []
        weights = []
        
        # Factor 1: Action probability clarity (how sure the model is)
        if ppo_probs is not None and len(ppo_probs) >= 3:
            sorted_probs = sorted(ppo_probs, reverse=True)
            best_prob = sorted_probs[0]
            second_prob = sorted_probs[1]
            
            # Clarity: gap between best and second choice
            prob_gap = best_prob - second_prob
            clarity_score = float(np.clip(prob_gap * 3, 0, 1))
            factors.append(clarity_score)
            weights.append(0.25)
        
        # Factor 2: Regime confidence
        regime_conf = regime_result.confidence * (0.5 + 0.5 * regime_result.trend_strength / 50)
        factors.append(min(1.0, regime_conf))
        weights.append(0.20)
        
        # Factor 3: Value estimate baseline
        if self._baseline_value is None:
            self._baseline_value = ppo_value
        else:
            # Running EMA update
            self._baseline_value = 0.95 * self._baseline_value + 0.05 * ppo_value
        
        if self._baseline_value is not None and abs(self._baseline_value) > 1e-9:
            # Value relative to baseline — higher is better
            value_ratio = ppo_value / abs(self._baseline_value)
            value_score = float(np.clip((value_ratio + 0.5) / 3, 0, 1))
            factors.append(value_score)
            weights.append(0.15)
        
        # Factor 4: Recent win rate (last 20)
        if last_n_rewards is not None and len(last_n_rewards) >= 5:
            recent_wins = sum(1 for r in last_n_rewards[-20:] if r > 0)
            win_rate = recent_wins / len(last_n_rewards[-20:])
            # Win rate mapped to confidence: 50% = 0.5, 80% = 0.9
            performance_score = float(np.clip(win_rate * 1.2 - 0.1, 0, 1))
            factors.append(performance_score)
            weights.append(0.20)
        
        # Factor 5: Feature health (are features within normal ranges?)
        if features is not None and np.std(features) > 0:
            # Check for extreme z-scores in feature distribution
            z_scores = np.abs((features - np.mean(features)) / (np.std(features) + 1e-9))
            extreme_ratio = np.mean(z_scores > 3.0)
            feature_health = 1.0 - float(extreme_ratio)
            factors.append(feature_health)
            weights.append(0.10)
        
        # Factor 6: Regime stability
        stability = getattr(regime_result, 'stability', 0.5)
        if stability is None:
            stability = 0.5
        factors.append(float(stability))
        weights.append(0.10)
        
        # Weighted average
        if sum(weights) > 0:
            confidence = sum(f * w for f, w in zip(factors, weights)) / sum(weights)
        else:
            confidence = 0.5
        
        return float(np.clip(confidence, 0.0, 1.0))


# ═════════════════════════════════════════════════════════════════════════════
# ENSEMBLE DECISION MAKER
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ModelVote:
    """Vote from a single model in the ensemble."""
    action: int
    confidence: float
    model_name: str
    reasoning: str


class EnsembleTrader:
    """
    Combines multiple decision models into a single action.
    
    Models:
    1. PPO (primary) — learned policy
    2. Trend Follower — follows strong trends
    3. Mean Reversion — fades extreme moves
    4. Volatility Breakout — trades vol expansion
    5. Volume Confirmation — requires volume support
    
    Voting: Weighted majority with confidence thresholds.
    """
    
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self._vote_history: deque = deque(maxlen=100)
    
    def get_votes(self, 
                   ppo_action: int,
                   ppo_probs: np.ndarray,
                   ppo_value: float,
                   regime: RegimeResult,
                   features_df: Optional[pd.DataFrame] = None,
                   ) -> List[ModelVote]:
        """
        Get votes from all ensemble members.
        
        Returns list of (action, confidence, name, reasoning) tuples.
        """
        votes: List[ModelVote] = []
        
        # ── Model 1: PPO (learned policy) ──────────────────────────────
        ppo_conf = max(ppo_probs) if ppo_probs is not None else 0.5
        votes.append(ModelVote(
            action=ppo_action,
            confidence=float(ppo_conf),
            model_name="PPO",
            reasoning=f"Learned policy: action={ppo_action}, value={ppo_value:.3f}, probs={[f'{p:.2f}' for p in ppo_probs]}",
        ))
        
        # ── Model 2: Trend Follower ────────────────────────────────────
        if features_df is not None and len(features_df) >= 20:
            tf_action, tf_conf, tf_reason = self._trend_follower_vote(features_df, regime)
            votes.append(ModelVote(
                action=tf_action, confidence=tf_conf,
                model_name="TrendFollow", reasoning=tf_reason,
            ))
        
        # ── Model 3: Mean Reversion ────────────────────────────────────
        if features_df is not None and len(features_df) >= 20:
            mr_action, mr_conf, mr_reason = self._mean_reversion_vote(features_df, regime)
            votes.append(ModelVote(
                action=mr_action, confidence=mr_conf,
                model_name="MeanRev", reasoning=mr_reason,
            ))
        
        # ── Model 4: Volatility Breakout ───────────────────────────────
        if features_df is not None and len(features_df) >= 20:
            vb_action, vb_conf, vb_reason = self._vol_breakout_vote(features_df, regime)
            votes.append(ModelVote(
                action=vb_action, confidence=vb_conf,
                model_name="VolBreak", reasoning=vb_reason,
            ))
        
        return votes
    
    def _trend_follower_vote(self, df: pd.DataFrame, regime: RegimeResult) -> Tuple[int, float, str]:
        """
        Trend-following strategy.
        BUY in strong uptrends, SELL (or stay out) in strong downtrends.
        """
        closes = df["close"].values
        current_px = float(closes[-1])
        
        # SMA alignment
        sma10 = np.mean(closes[-10:])
        sma20 = np.mean(closes[-20:])
        sma50 = np.mean(closes[-50:]) if len(closes) >= 50 else sma20
        
        # Trend score
        above_count = sum([current_px > sma10, current_px > sma20, current_px > sma50])
        trend_direction = 1 if sma10 > sma20 > sma50 else (-1 if sma10 < sma20 < sma50 else 0)
        
        # Momentum confirmation
        ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
        
        if regime.regime in (MarketRegime.TRENDING_UP, MarketRegime.BREAKOUT) and trend_direction > 0:
            if above_count >= 2 and ret_5 > 0:
                confidence = min(0.9, regime.trend_strength / 60 + abs(ret_5) / 5)
                return 1, float(confidence), f"Uptrend confirmed (above {above_count}/3 MAs, +{ret_5:.2f}%)"
        
        return 0, 0.5, "No trend signal"
    
    def _mean_reversion_vote(self, df: pd.DataFrame, regime: RegimeResult) -> Tuple[int, float, str]:
        """
        Mean-reversion strategy.
        BUY when oversold, SELL (HOLD) when overbought.
        Only works in ranging/low volatility regimes.
        """
        closes = df["close"].values
        current_px = float(closes[-1])
        
        # RSI
        delta = np.diff(closes[-15:])
        gains = delta[delta > 0].mean() if len(delta[delta > 0]) > 0 else 0
        losses = -delta[delta < 0].mean() if len(delta[delta < 0]) > 0 else 1e-9
        rs = gains / losses
        rsi = 100 - 100 / (1 + rs)
        
        # Z-score distance from EMA
        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().iloc[-1]
        z_dist = (current_px - ema9) / (np.std(closes[-20:]) + 1e-9)
        
        # Mean reversion works best in ranging/low vol regimes
        if regime.regime in (MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY):
            if rsi < 30 and z_dist < -1.5:
                confidence = min(0.8, (30 - rsi) / 30 * 0.8)
                return 1, float(confidence), f"Oversold (RSI={rsi:.0f}, z={z_dist:.1f})"
            elif rsi > 70 and z_dist > 1.5:
                confidence = min(0.8, (rsi - 70) / 30 * 0.7)
                # Overbought -> HOLD (don't short in long-only strategy)
                return 0, float(confidence), f"Overbought (RSI={rsi:.0f}, z={z_dist:.1f})"
        
        # In trending regimes, mean reversion is dangerous — lower confidence
        if regime.regime in (MarketRegime.TRENDING_UP, MarketRegime.BREAKOUT):
            if rsi < 35:  # Pullback in uptrend
                confidence = 0.4
                return 1, confidence, f"Pullback in uptrend (RSI={rsi:.0f}, limited MR opportunity)"
        
        return 0, 0.3, "No MR signal"
    
    def _vol_breakout_vote(self, df: pd.DataFrame, regime: RegimeResult) -> Tuple[int, float, str]:
        """
        Volatility breakout strategy.
        BUY when volatility expands with direction.
        """
        closes = df["close"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])
        
        # Volume spike
        vol_avg = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        vol_recent = np.mean(volumes[-3:]) if len(volumes) >= 3 else volumes[-1]
        vol_ratio = vol_recent / (vol_avg + 1e-9)
        
        # Price range expansion
        recent_range = (np.max(closes[-5:]) - np.min(closes[-5:])) / (closes[-5] + 1e-9)
        medium_range = (np.max(closes[-20:]) - np.min(closes[-20:])) / (closes[-20] + 1e-9) if len(closes) >= 20 else recent_range
        range_ratio = recent_range / (medium_range + 1e-9)
        
        # Breakout detection: vol expansion + price moving in one direction
        price_direction = 1 if closes[-1] > closes[-3] > closes[-5] else (-1 if closes[-1] < closes[-3] < closes[-5] else 0)
        
        if vol_ratio > 1.3 and range_ratio > 1.2 and price_direction != 0:
            confidence = min(0.85, (vol_ratio - 1) * 0.5 + (range_ratio - 1) * 0.5)
            if price_direction > 0:
                return 1, float(confidence), f"Bullish breakout (vol={vol_ratio:.1f}x, range={range_ratio:.1f}x)"
        
        return 0, 0.3, "No breakout signal"
    
    def ensemble_decision(self, votes: List[ModelVote], min_confidence: float = 0.5,
                          for_entry: bool = False) -> Tuple[int, float, str]:
        """
        Combine ensemble votes into a single decision.
        
        Uses weighted majority: each model gets vote weight = its confidence.
        BUY if weighted-BUY > weighted-HOLD + threshold
        """
        # Weighted vote counting
        weights = {0: 0.0, 1: 0.0, 2: 0.0}  # HOLD, BUY, SELL
        model_details = []
        
        for vote in votes:
            action = vote.action
            if for_entry and action == 2:
                action = 0  # flat account — SELL votes are not actionable for entry
            weights[action] += vote.confidence
            model_details.append(f"{vote.model_name}: {vote.action} ({vote.confidence:.0%})")
        
        total_weight = sum(weights.values())
        
        if total_weight > 0:
            buy_ratio = weights[1] / total_weight
            sell_ratio = weights[2] / total_weight
            hold_ratio = weights[0] / total_weight
        else:
            return 0, 0.0, "No votes"
        
        # Decision logic
        if for_entry:
            if buy_ratio >= max(0.28, min_confidence * 0.55) and buy_ratio > sell_ratio:
                action = 1
                confidence = buy_ratio
            else:
                action = 0
                confidence = max(hold_ratio, buy_ratio)
        elif buy_ratio > 0.5 and buy_ratio > hold_ratio + 0.15:
            action = 1
            confidence = buy_ratio
        elif sell_ratio > 0.5 and sell_ratio > hold_ratio + 0.15:
            action = 2
            confidence = sell_ratio
        else:
            action = 0
            confidence = hold_ratio
        
        # Override: minimum confidence threshold
        if confidence < min_confidence:
            action = 0
            reasoning = f"Confidence {confidence:.0%} < threshold {min_confidence:.0%} -> HOLD"
        else:
            reasoning = f"Ensemble: {' | '.join(model_details)}"
        
        self._vote_history.append({
            "action": action,
            "confidence": confidence,
            "votes": [(v.model_name, v.action, v.confidence) for v in votes],
            "timestamp": time.time(),
        })
        
        return action, confidence, reasoning


# ═════════════════════════════════════════════════════════════════════════════
# ENHANCED LEARNING MANAGER
# ═════════════════════════════════════════════════════════════════════════════

class AdaptiveLearner:
    """
    Enhanced online learning that adapts training parameters to market regime.
    
    Key differences from basic OnlineLearningManager:
    - Regime-dependent learning rates (learn faster in new regimes)
    - Adaptive batch sizes based on volatility
    - Automatic model backup before each update
    - Performance tracking per regime
    - Early stopping if performance degrades
    """
    
    def __init__(self, model: PPO, cfg: BotConfig):
        self.model = model
        self.cfg = cfg
        
        self._update_count = 0
        self._last_update_ts: float = 0.0
        self._per_regime_perf: Dict[str, List[float]] = defaultdict(list)
        self._pre_update_score: Optional[float] = None
        self._recent_rewards: deque = deque(maxlen=100)
        
        # Minimum time between updates (seconds)
        self._min_update_interval = 30.0
        
        # Backup directory
        self._backup_dir = "models/backups"
        os.makedirs(self._backup_dir, exist_ok=True)
    
    def should_update(self, regime: RegimeResult, bars_since_last: int) -> Tuple[bool, str]:
        """
        Determine if we should do an online update based on current conditions.
        
        Returns: (should_update, reason)
        """
        now = time.time()
        
        # Minimum time between updates
        if now - self._last_update_ts < self._min_update_interval:
            return False, "Too soon since last update"
        
        # Minimum data requirement
        if bars_since_last < 10:
            return False, "Not enough new bars"
        
        reasons = []
        
        # Update in new regimes (high learning opportunity)
        if regime.confidence > 0.6 and regime.regime != MarketRegime.UNKNOWN:
            reasons.append(f"Clear regime: {regime.regime.value}")
        
        # Update during moderate volatility (best learning signal)
        if 0.3 < regime.volatility_percentile < 80:
            reasons.append(f"Good volatility regime (pct={regime.volatility_percentile:.0f})")
        
        # Update after consecutive bars (standard schedule)
        if bars_since_last >= self.cfg.FINE_TUNE_EVERY_BARS:
            reasons.append(f"Scheduled update ({bars_since_last} bars)")
        
        # Maximum cap on bars between updates
        if bars_since_last >= self.cfg.FINE_TUNE_EVERY_BARS * 2:
            reasons.append(f"Overdue update ({bars_since_last} bars)")
            return True, "; ".join(reasons)
        
        if len(reasons) >= 1:
            return True, "; ".join(reasons)
        
        return False, "No update trigger"
    
    def get_adaptive_params(self, regime: RegimeResult) -> Dict[str, Any]:
        """
        Get regime-adaptive training parameters.
        
        Returns dict with possible overrides for:
        - learning_rate
        - batch_size
        - n_epochs
        - clip_range
        """
        params = {}
        
        # In high volatility: smaller learning rate (avoid overreacting to noise)
        if regime.regime == MarketRegime.HIGH_VOLATILITY:
            params['learning_rate'] = self.cfg.PPO_LR * 0.5
            params['n_epochs'] = max(3, self.cfg.PPO_N_EPOCHS - 3)
        
        # In trending regimes: normal learning, slightly more epochs
        elif regime.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            params['batch_size'] = min(512, self.cfg.PPO_BATCH_SIZE * 2)
            params['learning_rate'] = self.cfg.PPO_LR * 1.2
        
        # In ranging regimes: conservative updates
        elif regime.regime == MarketRegime.RANGING:
            params['clip_range'] = max(0.1, self.cfg.PPO_CLIP_RANGE * 0.8)
            params['n_epochs'] = max(3, self.cfg.PPO_N_EPOCHS - 2)
        
        # In low volatility: slightly more aggressive learning
        elif regime.regime == MarketRegime.LOW_VOLATILITY:
            params['learning_rate'] = self.cfg.PPO_LR * 1.3
            params['batch_size'] = min(512, self.cfg.PPO_BATCH_SIZE * 1.5)
        
        # In breakout regimes: fast adaptation
        elif regime.regime == MarketRegime.BREAKOUT:
            params['learning_rate'] = self.cfg.PPO_LR * 1.5
            params['n_epochs'] = self.cfg.PPO_N_EPOCHS + 3
        
        return params
    
    def backup_model(self) -> str:
        """Create a timestamped backup of the current model before updating."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self._backup_dir, f"ppo_{timestamp}.zip")
        
        try:
            self.model.save(backup_path)
            log.info(f"Model backed up -> {backup_path}")
            return backup_path
        except Exception as exc:
            log.warning(f"Model backup failed: {exc}")
            return ""
    
    def update(self, features: np.ndarray, prices: np.ndarray,
                regime: RegimeResult, reward: float = 0.0) -> Tuple[bool, str]:
        """
        Perform an adaptive online update.
        
        Returns: (success, message)
        """
        # Backup current model
        backup_path = self.backup_model()
        
        # Get adaptive parameters
        params = self.get_adaptive_params(regime)
        
        try:
            # Build environment
            env = TradingEnv(
                features, prices,
                self.cfg.INITIAL_CASH,
                self.cfg.TRANSACTION_COST_PCT,
                self.cfg.WINDOW_SIZE,
                self.cfg.DEFAULT_MAX_POSITION_PCT,
            )
            vec_env = DummyVecEnv([lambda: env])
            self.model.set_env(vec_env)
            
            # Apply parameter overrides if model supports it
            if 'learning_rate' in params:
                # PPO uses a schedule; we can reset the optimizer with new LR
                self.model.learning_rate = params['learning_rate']
            
            # Determine training steps (scale by volatility)
            base_steps = self.cfg.FINE_TUNE_STEPS
            if regime.volatility_percentile > 70:
                scale = int(base_steps * 0.7)  # Less steps in high vol (noisy)
            elif regime.volatility_percentile < 20:
                scale = int(base_steps * 1.5)  # More steps in low vol (cleaner)
            else:
                scale = base_steps
            
            total_steps = max(256, scale)
            
            log.info(f"🧠 Adaptive update #{self._update_count + 1} | "
                     f"regime={regime.regime.value} | "
                     f"steps={total_steps} | "
                     f"params={params}")
            
            # Train
            self.model.learn(
                total_timesteps=total_steps,
                reset_num_timesteps=False,
                progress_bar=False,
            )
            
            # Save
            self.model.save(self.cfg.MODEL_PATH)
            
            self._update_count += 1
            self._last_update_ts = time.time()
            
            # Track per-regime performance
            if reward != 0:
                self._per_regime_perf[regime.regime.value].append(reward)
            
            log.info(f"✅ Adaptive update #{self._update_count} complete -> {self.cfg.MODEL_PATH}")
            return True, f"Update #{self._update_count} ({total_steps} steps in {regime.regime.value})"
            
        except Exception as exc:
            log.error(f"Adaptive update failed: {exc}")
            
            # Restore from backup
            if backup_path and os.path.exists(backup_path):
                try:
                    self.model = PPO.load(backup_path)
                    log.info(f"Model restored from backup: {backup_path}")
                    return False, f"Update failed, restored from backup: {exc}"
                except Exception:
                    pass
            
            return False, f"Update failed: {exc}"
    
    def performance_summary(self) -> Dict:
        """Get learning performance summary per regime."""
        summary = {}
        for regime, rewards in self._per_regime_perf.items():
            if rewards:
                summary[regime] = {
                    'count': len(rewards),
                    'avg_reward': float(np.mean(rewards)),
                    'min_reward': float(np.min(rewards)),
                    'max_reward': float(np.max(rewards)),
                }
        return summary


# ═════════════════════════════════════════════════════════════════════════════
# ENHANCED PPO BUILDER (integrates everything)
# ═════════════════════════════════════════════════════════════════════════════

def build_enhanced_agent(cfg: BotConfig, model_path: Optional[str] = None,
                          verbose: int = 1) -> Tuple[PPO, Dict[str, Any]]:
    """
    Build an enhanced PPO agent with deeper architecture and device optimization.
    
    Network architecture scales with available compute:
    - CPU: (512, 256, 128) — standard
    - GPU: (1024, 512, 256) — deeper for GPU acceleration
    - Apple MPS: (1024, 768, 512) — M-series Neural Engine
    
    Returns: (model, components_dict)
        components_dict contains: regime_classifier, confidence_scorer, etc.
    """
    import torch
    
    # Check device and scale architecture
    if torch.backends.mps.is_available():
        device_str = "mps"
        net_arch = (1024, 768, 512)
        log.info("🍎 Apple MPS detected — using deep network (1024, 768, 512)")
    elif torch.cuda.is_available():
        device_str = "cuda"
        net_arch = (1024, 512, 256)
        log.info(f"🎮 CUDA detected ({torch.cuda.get_device_name(0)}) — using deep network (1024, 512, 256)")
    else:
        device_str = "cpu"
        net_arch = list(cfg.PPO_NET_ARCH)
        log.info(f"💻 CPU detected — using standard network {net_arch}")
    
    # Override with config if explicitly set
    if cfg.PPO_NET_ARCH and len(cfg.PPO_NET_ARCH) > 0:
        net_arch = list(cfg.PPO_NET_ARCH)
    
    # Build dummy env to get observation space
    dummy_f = np.zeros((cfg.WINDOW_SIZE + 2, cfg.N_FEATURES), np.float32)
    dummy_px = np.ones(cfg.WINDOW_SIZE + 2, np.float32) * 100.0
    dummy_env = TradingEnv(dummy_f, dummy_px, cfg.INITIAL_CASH,
                            cfg.TRANSACTION_COST_PCT, cfg.WINDOW_SIZE,
                            cfg.DEFAULT_MAX_POSITION_PCT)
    vec_env = DummyVecEnv([lambda: dummy_env])
    
    # Build model
    if model_path and os.path.exists(model_path):
        log.info(f"Loading existing model from {model_path} …")
        model = PPO.load(model_path, env=vec_env, device=device_str)
        model.set_env(vec_env)
    else:
        log.info("Building new enhanced PPO agent …")
        policy_kwargs = {
            "net_arch": net_arch,
            "activation_fn": torch.nn.Tanh,  # Stable for financial data
        }
        
        # Use the config values, but allow device-specific overrides
        n_steps = cfg.PPO_N_STEPS
        if device_str == "mps":
            n_steps = min(n_steps, 4096)  # MPS works well with moderate batches
        
        model = PPO(
            policy="MlpPolicy",
            env=vec_env,
            n_steps=n_steps,
            batch_size=cfg.PPO_BATCH_SIZE,
            n_epochs=cfg.PPO_N_EPOCHS,
            clip_range=cfg.PPO_CLIP_RANGE,
            max_grad_norm=cfg.PPO_MAX_GRAD_NORM,
            learning_rate=cfg.PPO_LR,
            gamma=cfg.PPO_GAMMA,
            gae_lambda=cfg.PPO_GAE_LAM,
            ent_coef=cfg.PPO_ENT_COEF,
            vf_coef=cfg.PPO_VF_COEF,
            policy_kwargs=policy_kwargs,
            verbose=verbose,
            device=device_str,
        )
    
    device_name = str(model.device)
    log.info(f"PPO agent ready | device: {device_name} | net: {net_arch} | obs: {dummy_env.observation_space.shape}")
    
    # Build enhanced components
    components = {
        "regime_classifier": MarketRegimeClassifier(),
        "confidence_scorer": ConfidenceScorer(cfg),
        "ensemble": EnsembleTrader(cfg),
        "adaptive_learner": AdaptiveLearner(model, cfg),
        "device": device_str,
        "net_arch": net_arch,
    }
    
    return model, components


# ── Convenience re-exports ────────────────────────────────────────────────

def compute_thinking_confidence(model: PPO, obs: np.ndarray) -> Tuple[int, float, np.ndarray]:
    """
    Get PPO action with probability distribution (for confidence scoring).
    
    Returns: (action, value_estimate, action_probabilities)
    """
    import torch

    obs = np.asarray(obs, dtype=np.float32).flatten()
    obs_space = int(model.observation_space.shape[0])
    if obs.shape[0] != obs_space:
        if obs.shape[0] == obs_space - 2:
            obs = np.concatenate([obs, np.array([0.5, 0.5], dtype=np.float32)])
        elif obs.shape[0] > obs_space:
            obs = obs[:obs_space]
        else:
            padded = np.zeros(obs_space, dtype=np.float32)
            padded[: obs.shape[0]] = obs
            obs = padded

    # Convert observation to tensor
    if not isinstance(obs, torch.Tensor):
        obs_tensor = torch.as_tensor(obs, dtype=torch.float32, device=model.device)
    else:
        obs_tensor = obs

    # SB3 policy networks expect a batched (2-D) observation. A raw 1-D obs
    # makes downstream ops index a nonexistent batch dim and raises
    # "Dimension out of range". model.predict() batches internally; this
    # manual inference path must do the same.
    if obs_tensor.ndim == 1:
        obs_tensor = obs_tensor.unsqueeze(0)

    # Use SB3's public policy API, which correctly handles feature extraction
    # and the MLP extractor (manually reconstructing the forward pass is brittle
    # across SB3 versions and feature-extractor sharing modes).
    with torch.no_grad():
        distribution = model.policy.get_distribution(obs_tensor)
        values = model.policy.predict_values(obs_tensor)

        # Get action probabilities
        action_logits = distribution.distribution.logits
        probabilities = torch.softmax(action_logits, dim=-1)

        # Sample action
        action = distribution.get_actions()

        value_estimate = float(values.cpu().numpy().flatten()[0])
        action_val = int(action.cpu().numpy().flatten()[0])
        probs = probabilities.cpu().numpy().flatten()

    return action_val, value_estimate, probs


