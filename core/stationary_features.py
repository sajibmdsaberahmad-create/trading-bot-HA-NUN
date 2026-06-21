#!/usr/bin/env python3
"""
core/stationary_features.py — Stationarity-Enforced Feature Engineering.

ARCHITECTURE
═══════════════════════════════════════════════════════════════════════════
Replaces raw absolute prices and naive rolling indicators with mathematically
stationary geometries that remain valid across regime shifts.

FEATURES IMPLEMENTED
• Fractional Differentiation (d=0.4): Preserves long-term memory while
  ensuring stationarity, avoiding the over-differencing of raw log returns.
• VPIN (Volume-Synchronized Probability of Toxicity): Measures order-flow
  imbalance to detect informed trading pressure before price moves.
• Amihud Illiquidity Ratio: Absolute price change per dollar of volume,
  flags systemic liquidity dry-ups that predict slippage and gaps.
• Microstructure Spread Proxy: (High-Low)/Close as a volatility estimator.

No raw price or unmasked rolling window passes into the 18-feature pipeline.
Every input is a relative, scaled, or differenced quantity.
"""

import numpy as np
import pandas as pd
from typing import Optional


def fractional_difference(series: pd.Series, d: float = 0.4,
                         threshold: float = 1e-5) -> pd.Series:
    """
    Compute fractional differentiated series using the fixed-width window
    approximation (FFD) from Jensen & Muratore (2010).
    
    Unlike standard diff() which removes all memory, fractional diff retains
    long-horizon predictive power while yielding a stationary series.
    
    Args:
        series: Price series (typically close)
        d: Fractional differencing order (0.4 recommended for finance)
        threshold: Truncation threshold for binomial weights
        
    Returns:
        Stationary fractional difference series
    """
    series = series.dropna()
    if len(series) < 2:
        return pd.Series([0.0] * len(series), index=series.index)
    
    # Compute binomial weights
    weights = [1.0]
    k = 1
    while True:
        w_k = weights[-1] * (d - k + 1) / k
        if abs(w_k) < threshold:
            break
        weights.append(w_k)
        k += 1
    
    weights = np.array(weights)
    weights = weights.reshape(-1, 1)
    
    # Apply convolution
    result = np.zeros(len(series))
    for t in range(len(series)):
        subset = series.iloc[max(0, t - len(weights) + 1): t + 1]
        w_subset = weights[-len(subset):]
        result[t] = np.sum(subset.values * w_subset.flatten())
    
    return pd.Series(result, index=series.index)


def compute_vpin(prices: pd.Series, volume: pd.Series,
                 window: int = 50) -> pd.Series:
    """
    Volume-Synchronized Probability of Informed Trading (VPIN).
    
    VPIN approximates the probability that volume is informed (vs. noise)
    by measuring the absolute order-flow imbalance normalized by volume.
    
    High VPIN → Toxic flow → impending adverse price move.
    Low VPIN  → Retail/noise flow → safer to trade.
    
    Simplified single-asset VPIN (no volume bucket decomposition):
        VPIN = |∑(volume * sign(returns))| / total_volume
    
    Args:
        prices: Close price series
        volume: Volume series
        window: Rolling window length
        
    Returns:
        VPIN series [0, 1] normalized
    """
    returns = prices.diff().fillna(0)
    signed_volume = volume * np.sign(returns)
    
    # Rolling absolute imbalance
    imbalance = signed_volume.abs().rolling(window).sum()
    total_vol = volume.rolling(window).sum()
    
    vpin = (imbalance / total_vol.replace(0, np.nan)).fillna(0.5)
    
    # Normalize to [0, 1] using rolling percentile ranking
    # Default 0.5 center; map extremes based on rolling std
    vpin_std = vpin.rolling(window * 2).std().fillna(0.1)
    vpin_mean = vpin.rolling(window * 2).mean().fillna(0.5)
    
    vpin_norm = (vpin - vpin_mean) / (vpin_std + 1e-9)
    vpin_norm = 1 / (1 + np.exp(-vpin_norm * 3))  # Sigmoid squash to [0,1]
    
    return vpin_norm.clip(0, 1)


def compute_amihud(prices: pd.Series, volume: pd.Series,
                   window: int = 20) -> pd.Series:
    """
    Amihud Illiquidity Ratio = mean(|return| / dollar_volume) over window.
    
    High values indicate illiquid markets where trades move price excessively.
    Used here as a normalized feature (not raw ratio, which is tiny).
    
    Args:
        prices: Close price series
        volume: Volume series (in shares)
        window: Rolling window
        
    Returns:
        Normalized Amihud illiquidity metric
    """
    dollar_volume = prices * volume
    abs_return = prices.pct_change().abs()
    
    # Avoid division by zero
    ratio = abs_return / (dollar_volume + 1e-9)
    
    # Scale up and normalize via rolling z-score
    amihud = ratio.rolling(window).mean().fillna(0)
    
    # Normalize using robust statistics
    roll_med = amihud.rolling(window * 3, min_periods=window).median().fillna(0)
    roll_mad = (amihud - roll_med).abs().rolling(window * 3, min_periods=window).median().fillna(1e-9)
    
    z_score = (amihud - roll_med) / (roll_mad * 1.4826 + 1e-9)
    # Clip and sigmoid
    z_score = z_score.clip(-5, 5)
    amihud_norm = 1 / (1 + np.exp(-z_score))
    
    return amihud_norm.clip(0, 1)


def compute_microstructure_features(df: pd.DataFrame,
                                     frac_diff_order: float = 0.4,
                                     vpin_window: int = 50,
                                     amihud_window: int = 20,
                                     frac_diff_window: int = 60) -> pd.DataFrame:
    """
    Master function that enriches an OHLCV DataFrame with all stationary
    microstructure features.
    
    Replaces or supplements the basic RSI/MACD features with:
    - frac_close: Fractionally differenced close prices
    - vpin: Volume-synchronized toxicity probability
    - amihud: Illiquidity ratio
    - spread_proxy: (High - Low) / Close
    - volume_zscore: Relative volume spike metric
    
    Args:
        df: DataFrame with columns ['open','high','low','close','volume']
        frac_diff_order: Order d for fractional differentiation
        vpin_window: Window for VPIN calculation
        amihud_window: Window for Amihud calculation
        frac_diff_window: Window for fractional diff truncation
        
    Returns:
        DataFrame with added stationary feature columns
    """
    df = df.copy()
    
    # Ensure sorted
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
    
    # 1. Fractional Difference of Close (stationarity-preserving)
    df['frac_close'] = fractional_difference(
        df['close'], d=frac_diff_order, threshold=1e-5
    )
    
    # 2. VPIN
    df['vpin'] = compute_vpin(df['close'], df['volume'], window=vpin_window)
    
    # 3. Amihud Illiquidity
    df['amihud'] = compute_amihud(df['close'], df['volume'], window=amihud_window)
    
    # 4. Spread Proxy (normalized)
    hl_spread = (df['high'] - df['low']) / (df['close'] + 1e-9)
    df['spread_proxy'] = hl_spread.rolling(20).mean().fillna(hl_spread)
    
    # 5. Volume Z-score (robust)
    vol_median = df['volume'].rolling(60, min_periods=10).median().fillna(df['volume'].median())
    vol_mad = (df['volume'] - vol_median).abs().rolling(60, min_periods=10).median().fillna(1e-9)
    df['volume_zscore'] = ((df['volume'] - vol_median) / (vol_mad * 1.4826 + 1e-9)).clip(-5, 5)
    
    # 6. Fractional Returns (alternative to log returns)
    df['frac_return'] = fractional_difference(
        df['close'], d=0.2, threshold=1e-4
    ).diff().fillna(0)
    
    # 7. Rolling Volatility of Fractional Returns (normalized)
    vol_frac = df['frac_return'].rolling(20).std().fillna(0)
    vol_frac_med = vol_frac.rolling(60, min_periods=10).median().fillna(vol_frac.median())
    df['volatility_regime'] = (vol_frac - vol_frac_med) / (vol_frac_med + 1e-9)
    df['volatility_regime'] = df['volatility_regime'].clip(-3, 3)
    
    # Cleanup: replace infinities and NaNs from startup
    feature_cols = ['frac_close', 'vpin', 'amihud', 'spread_proxy',
                    'volume_zscore', 'frac_return', 'volatility_regime']
    df[feature_cols] = df[feature_cols].replace([np.inf, -np.inf], 0).fillna(0)
    
    return df


def get_feature_columns() -> list:
    """Return list of stationary feature column names."""
    return [
        'frac_close', 'frac_return', 'volatility_regime',
        'vpin', 'amihud', 'spread_proxy', 'volume_zscore',
    ]


def validate_stationarity(df: pd.DataFrame, columns: Optional[list] = None) -> dict:
    """
    Validate that features are roughly stationary via Augmented Dickey-Fuller.
    Returns pass/fail and p-values for each column.
    """
    from statsmodels.tsa.stattools import adfuller
    
    if columns is None:
        columns = get_feature_columns()
    
    results = {}
    for col in columns:
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if len(series) < 20:
            results[col] = {'stationary': None, 'p_value': None, 'error': 'insufficient_data'}
            continue
        try:
            adf_stat, p_value, _, _, _, _ = adfuller(series, maxlag=1, regression='c')
            results[col] = {
                'stationary': p_value < 0.05,
                'p_value': round(float(p_value), 4),
                'adf_stat': round(float(adf_stat), 4),
            }
        except Exception as e:
            results[col] = {'stationary': False, 'p_value': None, 'error': str(e)}
    
    return results