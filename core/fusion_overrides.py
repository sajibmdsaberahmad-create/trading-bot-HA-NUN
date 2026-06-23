#!/usr/bin/env python3
"""
core/fusion_overrides.py — Hard-coded circuit breakers for the fusion engine.

PURPOSE
═══════════════════════════════════════════════════════════════════════════
When market conditions become extreme (flash crash, volatility spike,
liquidity crisis), accuracy-tracked weights can be WRONG — the models
are trained on historical data that doesn't include the current regime.

This module provides structural overrides that BYPASS the accuracy tracker
and FORCE specific model weights based on market microstructure:

Examples:
  - High VIX spike → Ensemble (VolBreakout) dominates, DL models muted
  - Low liquidity widens → Reduce all position sizes, favor MeanReversion
  - Flash crash detection → Immediate HOLD, no new entries
  - Earnings / news blackout → Lock to HOLD regardless of signals

USAGE
    from core.fusion_overrides import FusionOverrides, OverrideSignal
    
    overrides = FusionOverrides()
    signal = overrides.evaluate(regime_result, bar_df, current_volatility)
    
    if signal.override_active:
        forced_weights = signal.forced_weights
        forced_action = signal.forced_action
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd


class OverrideLevel(Enum):
    """Severity of override."""
    NONE = 0
    CAUTION = 1
    HARD_OVERRIDE = 2
    FULL_HALT = 3


@dataclass
class OverrideSignal:
    """
    Result of override evaluation.
    
    If override_active is True, the fusion engine should IGNORE
    accuracy-tracked weights and use forced_weights / forced_action instead.
    """
    override_active: bool = False
    level: OverrideLevel = OverrideLevel.NONE
    reason: str = ""
    forced_weights: Optional[Dict[str, float]] = None
    forced_action: Optional[int] = None  # 0=HOLD, 1=BUY, 2=SELL
    risk_multiplier: float = 1.0
    position_scale: float = 1.0
    
    def __bool__(self):
        return self.override_active


class FusionOverrides:
    """
    Circuit breaker system for multi-model fusion engine.
    
    Rules are evaluated in order of severity (most restrictive first):
    1. FULL_HALT: Stop all trading (flash crash, circuit breaker)
    2. HARD_OVERRIDE: Force specific model weights/action
    3. CAUTION: Reduce position size, bias toward defensive models
    """
    
    # Thresholds
    FLASH_CRASH_PCT: float = -0.05       # 5% drop in 1 min = flash crash
    VOL_SPIKE_VIX: float = 30.0          # VIX > 30 = high volatility
    VOL_SPIKE_ATR: float = 0.03          # ATR > 3% of price
    LIQUIDITY_DRY_SPREAD_PCT: float = 0.01  # Spread > 1% = illiquid
    EARNINGS_BLACKOUT_MINUTES: int = 5   # Minutes around earnings
    
    def __init__(self):
        self._last_bar_time: Optional[pd.Timestamp] = None
        self._override_history: list = []
    
    def evaluate(self, regime_result: Any = None, bar_df: Optional[pd.DataFrame] = None,
                 atr_series: Optional[pd.Series] = None) -> OverrideSignal:
        """
        Evaluate all override rules and return the most restrictive active signal.
        
        Args:
            regime_result: Market regime classification
            bar_df: Recent OHLCV data
            atr_series: ATR values
            
        Returns:
            OverrideSignal with override details
        """
        signals = []
        
        # Rule 1: Flash crash detection
        if bar_df is not None and len(bar_df) >= 2:
            signals.append(self._check_flash_crash(bar_df))
        
        # Rule 2: Volatility spike
        if atr_series is not None and len(atr_series) > 0:
            signals.append(self._check_vol_spike(atr_series, bar_df))
        
        # Rule 3: Regime-based overrides
        if regime_result is not None:
            signals.append(self._check_regime_override(regime_result))
        
        # Rule 4: Liquidity dry-up
        if bar_df is not None and len(bar_df) >= 5:
            signals.append(self._check_liquidity(bar_df))
        
        # Take the most restrictive active signal
        active_signals = [s for s in signals if s.override_active]
        if not active_signals:
            return OverrideSignal()
        
        # Sort by level (most restrictive first)
        active_signals.sort(key=lambda s: s.level.value, reverse=True)
        worst = active_signals[0]
        
        self._override_history.append({
            "timestamp": pd.Timestamp.utcnow().isoformat(),
            "level": worst.level.name,
            "reason": worst.reason,
        })
        
        return worst
    
    def _check_flash_crash(self, bar_df: pd.DataFrame) -> OverrideSignal:
        """Detect 1-bar flash crashes (>=5% drop)."""
        if len(bar_df) < 2:
            return OverrideSignal()
        
        last_close = float(bar_df["close"].iloc[-1])
        prev_close = float(bar_df["close"].iloc[-2])
        pct_change = (last_close / prev_close - 1) if prev_close > 0 else 0
        
        if pct_change <= self.FLASH_CRASH_PCT:
            return OverrideSignal(
                override_active=True,
                level=OverrideLevel.FULL_HALT,
                reason=f"FLASH CRASH: {pct_change:.1%} drop in 1 bar. Halting all entries.",
                forced_weights={"ensemble": 0.0, "ppo": 0.0, "transformer": 0.0, "lstm": 0.0},
                forced_action=0,  # HOLD
                risk_multiplier=0.0,
                position_scale=0.0,
            )
        return OverrideSignal()
    
    def _check_vol_spike(self, atr_series: pd.Series, bar_df: Optional[pd.DataFrame]) -> OverrideSignal:
        """Detect volatility spikes."""
        if bar_df is None or len(bar_df) < 2:
            return OverrideSignal()
        
        current_price = float(bar_df["close"].iloc[-1])
        current_atr = float(atr_series.iloc[-1]) if len(atr_series) > 0 else 0
        atr_pct = current_atr / current_price if current_price > 0 else 0
        
        if atr_pct >= self.VOL_SPIKE_ATR:
            # High vol: mute DL models (lag), favor ensemble breakout/volatility
            return OverrideSignal(
                override_active=True,
                level=OverrideLevel.HARD_OVERRIDE,
                reason=f"VOL SPIKE: ATR={atr_pct:.1%} >= {self.VOL_SPIKE_ATR:.1%}. Muting DL models.",
                forced_weights={
                    "ensemble": 0.70,
                    "ppo": 0.15,
                    "transformer": 0.10,
                    "lstm": 0.05,
                },
                forced_action=None,  # Let ensemble decide
                risk_multiplier=0.5,  # Halve position size
                position_scale=0.5,
            )
        return OverrideSignal()
    
    def _check_regime_override(self, regime_result: Any) -> OverrideSignal:
        """Override based on classified market regime."""
        try:
            regime = regime_result.regime.value if hasattr(regime_result, 'regime') else "unknown"
        except Exception:
            return OverrideSignal()
        
        # Handle both agent_enhanced.MarketRegime and hmrs.MarketRegime value formats
        regime_upper = regime.upper()
        
        if "HIGH_VOL" in regime_upper or "VOLATILITY" in regime_upper:
            return OverrideSignal(
                override_active=True,
                level=OverrideLevel.HARD_OVERRIDE,
                reason=f"{regime} regime: defensive posture, favor ensemble.",
                forced_weights={
                    "ensemble": 0.65,
                    "ppo": 0.20,
                    "transformer": 0.10,
                    "lstm": 0.05,
                },
                forced_action=None,
                risk_multiplier=0.6,
                position_scale=0.6,
            )
        elif regime_upper == "LOW_VOLATILITY" or "LOW" in regime_upper or "CALM" in regime_upper:
            return OverrideSignal(
                override_active=True,
                level=OverrideLevel.CAUTION,
                reason=f"{regime} regime: can increase size slightly but watch for breakout.",
                forced_weights={
                    "ensemble": 0.40,
                    "ppo": 0.30,
                    "transformer": 0.20,
                    "lstm": 0.10,
                },
                forced_action=None,
                risk_multiplier=1.2,
                position_scale=1.2,
            )
        elif "TREND" in regime_upper and "DOWN" not in regime_upper:
            return OverrideSignal(
                override_active=True,
                level=OverrideLevel.CAUTION,
                reason=f"{regime} regime: favor momentum models.",
                forced_weights={
                    "ensemble": 0.30,
                    "ppo": 0.35,
                    "transformer": 0.25,
                    "lstm": 0.10,
                },
                forced_action=None,
                risk_multiplier=1.0,
                position_scale=1.0,
            )
        
        return OverrideSignal()
    
    def _check_liquidity(self, bar_df: pd.DataFrame) -> OverrideSignal:
        """Check for liquidity dry-up."""
        if len(bar_df) < 3:
            return OverrideSignal()
        
        volumes = bar_df["volume"].values[-5:]
        avg_vol = np.mean(volumes[:-1])
        current_vol = volumes[-1]
        
        # Volume dry-up: current < 30% of average
        if avg_vol > 0 and current_vol < avg_vol * 0.3:
            return OverrideSignal(
                override_active=True,
                level=OverrideLevel.CAUTION,
                reason=f"LIQUIDITY DRY-UP: volume {current_vol/avg_vol:.0%} of avg. Reducing exposure.",
                forced_weights={
                    "ensemble": 0.50,
                    "ppo": 0.30,
                    "transformer": 0.15,
                    "lstm": 0.05,
                },
                forced_action=None,
                risk_multiplier=0.5,
                position_scale=0.5,
            )
        
        return OverrideSignal()
    
    def get_override_history(self, last_n: int = 20) -> list:
        """Get recent override history for logging."""
        return self._override_history[-last_n:]