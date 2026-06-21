#!/usr/bin/env python3
"""
core/features_enhanced.py — Extended feature engineering with 18 features.

Adds 4 new features on top of the original 14 in core/features.py:
   14. price_acceleration — Rate of change of momentum (2nd derivative of price)
   15. volume_momentum — Rate of change of volume relative to price
   16. market_microstructure — Tick-level imbalance signal (buying/selling pressure)
   17. cross_asset_correlation — Correlation between recent returns (regime change detector)

All features remain bounded/normalised to prevent gradient explosion.
Total: 18 features (up from 14).
"""

import numpy as np
import pandas as pd


class FeatureEngineerEnhanced:
    """
    Extended feature engineer that adds 4 advanced features.
    
    Use in place of FeatureEngineer when cfg.N_FEATURES == 18.
    Backward compatible — wraps the original 14 features internally.
    """
    
    MAX_LOOKBACK = 40
    N_FEATURES = 18  # 14 original + 4 new
    
    @staticmethod
    def compute(df: pd.DataFrame) -> np.ndarray:
        """
        Compute all 18 features from OHLCV data.
        
        Args:
            df: DataFrame with [open, high, low, close, volume], min 40 rows.
            
        Returns:
            np.ndarray of shape (n_valid_rows, 18), dtype float32.
        """
        if len(df) < FeatureEngineerEnhanced.MAX_LOOKBACK + 1:
            return np.empty((0, FeatureEngineerEnhanced.N_FEATURES), dtype=np.float32)
        
        # ── Compute original 14 features (reuse logic from features.py) ──
        f = pd.DataFrame(index=df.index)
        
        # 0: Log return
        f["log_return"] = np.log(df["close"] / df["close"].shift(1))
        
        # 1: Realised volatility
        f["volatility_10"] = f["log_return"].rolling(10).std()
        
        # 2: RSI-14 normalized [0, 1]
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        f["rsi_14"] = (100.0 - 100.0 / (1.0 + rs)) / 100.0
        
        # 3: MACD histogram
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        f["macd_signal"] = (macd_hist / (df["close"] + 1e-9)).clip(-0.02, 0.02)
        
        # 4: Bollinger %B
        bb_mid = df["close"].rolling(20).mean()
        bb_std = df["close"].rolling(20).std()
        bb_upper = bb_mid + 2.0 * bb_std
        bb_lower = bb_mid - 2.0 * bb_std
        f["bb_pct"] = ((df["close"] - bb_lower) / (bb_upper - bb_lower + 1e-9)).clip(0.0, 1.0)
        
        # 5: Volume Z-score
        vol_roll = df["volume"].rolling(30)
        f["volume_z"] = ((df["volume"] - vol_roll.mean()) / (vol_roll.std() + 1e-9)).clip(-3.0, 3.0)
        
        # 6: Volume acceleration
        f["volume_accel"] = f["volume_z"].diff().clip(-3.0, 3.0)
        
        # 7: 5-bar price momentum
        f["price_momentum_5"] = np.log(df["close"] / df["close"].shift(5)).clip(-0.10, 0.10)
        
        # 8: VWAP deviation
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        cum_vol = df["volume"].cumsum()
        cum_tpv = (typical * df["volume"]).cumsum()
        vwap = cum_tpv / (cum_vol + 1e-9)
        f["vwap_deviation"] = ((df["close"] - vwap) / (vwap + 1e-9)).clip(-0.05, 0.05)
        
        # 9: ATR normalized
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift(1)).abs()
        low_close = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        f["atr_norm"] = (atr14 / (df["close"] + 1e-9)).clip(0.0, 0.05)
        
        # 10: OBV Z-score
        direction = np.sign(df["close"].diff()).fillna(0)
        obv = (direction * df["volume"]).cumsum()
        obv_roll = obv.rolling(30)
        f["obv_z"] = ((obv - obv_roll.mean()) / (obv_roll.std() + 1e-9)).clip(-3.0, 3.0)
        
        # 11: Trend strength (ADX-style)
        up_move = df["high"].diff()
        down_move = -df["low"].diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr14_safe = atr14.replace(0, np.nan)
        plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(14).mean() / (atr14_safe + 1e-9)
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).mean() / (atr14_safe + 1e-9)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9) * 100
        f["trend_strength"] = (dx.rolling(14).mean() / 100.0).clip(0.0, 1.0)
        
        # 12: Mean-reversion Z-score
        ema9 = df["close"].ewm(span=9, adjust=False).mean()
        dist = df["close"] - ema9
        dist_std = dist.rolling(20).std()
        f["mean_reversion_z"] = (dist / (dist_std + 1e-9)).clip(-3.0, 3.0)
        
        # 13: Realised-vol ratio
        vol_short = f["log_return"].rolling(5).std()
        vol_long = f["log_return"].rolling(20).std()
        f["realized_vol_ratio"] = (vol_short / (vol_long + 1e-9)).clip(0.0, 3.0)
        
        # ════════════════════════════════════════════════════════════════
        # NEW FEATURES 14-17
        # ════════════════════════════════════════════════════════════════
        
        # ── 14: Price acceleration (2nd derivative of log price) ──────────
        # Measures whether momentum is accelerating or decelerating.
        # Positive = momentum building, Negative = momentum fading.
        log_ret_3 = np.log(df["close"] / df["close"].shift(3))
        log_ret_6 = np.log(df["close"].shift(3) / df["close"].shift(6))
        accel = (log_ret_3 - log_ret_6)  # Change in momentum over 3 bars
        f["price_acceleration"] = accel.clip(-0.05, 0.05) * 20  # Scale for visibility
        
        # ── 15: Volume momentum (VWAP × volume convergence) ──────────────
        # Divergence between price direction and volume confirms/rejects moves.
        # High volume on up-moves = buying pressure. High vol on down-moves = selling.
        price_dir = np.sign(df["close"].diff()).fillna(0)
        vol_change = df["volume"].pct_change().fillna(0)
        # Volume momentum: positive when volume expands in direction of price
        vol_mom = price_dir * vol_change
        f["volume_momentum"] = vol_mom.clip(-1.0, 1.0)
        
        # ── 16: Microstructure / tick imbalance proxy ──────────────────────
        # Estimates buying vs selling pressure from OHLC data.
        # Uses the relationship between close position in the range:
        # Close near high = buying pressure, near low = selling pressure.
        # More sophisticated: combine with volume-weighted close location.
        hl_range = (df["high"] - df["low"]).replace(0, np.nan)
        close_position = (df["close"] - df["low"]) / (hl_range + 1e-9)
        # Volume-weighted close position (higher vol = more conviction)
        vol_weight = df["volume"] / (df["volume"].rolling(20).mean() + 1e-9)
        imbalance = (close_position - 0.5) * 2 * vol_weight.clip(0, 3)
        f["microstructure_imbalance"] = imbalance.clip(-1.0, 1.0)
        
        # ── 17: Cross-bar correlation / regime coherence signal ────────────
        # Rolling correlation between consecutive returns.
        # High positive corr = trending, near zero = random walk, 
        # negative = mean-reverting.
        # This tells the model what KIND of market it's in (regime hint).
        ret_1 = f["log_return"].shift(0)
        ret_2 = f["log_return"].shift(1)
        # Rolling window correlation
        corr_window = 20
        rolling_corr = ret_1.rolling(corr_window).corr(ret_2).fillna(0)
        f["return_correlation"] = rolling_corr.clip(-1.0, 1.0)
        
        # Drop NaN rows from lookback period
        f = f.dropna()
        
        return f.values.astype(np.float32)
    
    @staticmethod
    def feature_names() -> list:
        """Return human-readable names for all 18 features."""
        return [
            "log_return",           # 0
            "volatility_10",        # 1
            "rsi_14",               # 2
            "macd_signal",          # 3
            "bb_pct",               # 4
            "volume_z",             # 5
            "volume_accel",         # 6
            "price_momentum_5",     # 7
            "vwap_deviation",       # 8
            "atr_norm",             # 9
            "obv_z",                # 10
            "trend_strength",       # 11
            "mean_reversion_z",     # 12
            "realized_vol_ratio",   # 13
            "price_acceleration",   # 14  NEW
            "volume_momentum",      # 15  NEW
            "microstructure_imbalance",  # 16  NEW
            "return_correlation",   # 17  NEW
        ]