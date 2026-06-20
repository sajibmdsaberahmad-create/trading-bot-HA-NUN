#!/usr/bin/env python3
"""
core/features.py — Converts raw OHLCV bars into the normalised feature
matrix the PPO agent reads.

Carries over all 11 original features and adds 3 new ones aimed at
giving the model more PREDICTIVE signal about where price is likely to
go next, rather than only descriptive signal about where it has been:

  11. trend_strength  — ADX-style directional strength (is there a
                         trend worth riding, or is the market choppy?)
  12. mean_reversion_z — distance from a fast EMA in standard-deviation
                         units (helps the model time entries instead of
                         chasing)
  13. realized_vol_ratio — short-vol / long-vol ratio (volatility
                         expansion is a classic precursor to a breakout
                         move — used by the predictive stop/target engine
                         too, see core/risk.py)

All features are bounded/normalised — unbounded raw values cause
gradient explosions and prevent the network from converging.
"""

import numpy as np
import pandas as pd


class FeatureEngineer:
    MAX_LOOKBACK = 40  # max rolling window used; first N rows will be NaN
    N_FEATURES = 14

    @staticmethod
    def compute(df: pd.DataFrame) -> np.ndarray:
        """
        Args:
            df: DataFrame with [open, high, low, close, volume].
                Minimum rows: MAX_LOOKBACK + 1.

        Returns:
            np.ndarray of shape (n_valid_rows, 14), dtype float32.
        """
        if len(df) < FeatureEngineer.MAX_LOOKBACK + 1:
            return np.empty((0, FeatureEngineer.N_FEATURES), dtype=np.float32)

        f = pd.DataFrame(index=df.index)

        # ── 0: Log return ──────────────────────────────────────────────────
        f["log_return"] = np.log(df["close"] / df["close"].shift(1))

        # ── 1: Realised volatility — rolling 10-bar std ────────────────────
        f["volatility_10"] = f["log_return"].rolling(10).std()

        # ── 2: RSI-14, normalised [0, 1] ────────────────────────────────────
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        f["rsi_14"] = (100.0 - 100.0 / (1.0 + rs)) / 100.0

        # ── 3: MACD histogram (12/26/9 EMA) ─────────────────────────────────
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        f["macd_signal"] = (macd_hist / (df["close"] + 1e-9)).clip(-0.02, 0.02)

        # ── 4: Bollinger Band %B ─────────────────────────────────────────────
        bb_mid = df["close"].rolling(20).mean()
        bb_std = df["close"].rolling(20).std()
        bb_upper = bb_mid + 2.0 * bb_std
        bb_lower = bb_mid - 2.0 * bb_std
        f["bb_pct"] = ((df["close"] - bb_lower) / (bb_upper - bb_lower + 1e-9)).clip(0.0, 1.0)

        # ── 5: Volume Z-score (30-bar rolling, +/-3 sigma clip) ──────────────
        vol_roll = df["volume"].rolling(30)
        f["volume_z"] = ((df["volume"] - vol_roll.mean()) / (vol_roll.std() + 1e-9)).clip(-3.0, 3.0)

        # ── 6: Volume acceleration ────────────────────────────────────────────
        f["volume_accel"] = f["volume_z"].diff().clip(-3.0, 3.0)

        # ── 7: 5-bar price momentum ────────────────────────────────────────────
        f["price_momentum_5"] = np.log(df["close"] / df["close"].shift(5)).clip(-0.10, 0.10)

        # ── 8: VWAP deviation ───────────────────────────────────────────────────
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        cum_vol = df["volume"].cumsum()
        cum_tpv = (typical * df["volume"]).cumsum()
        vwap = cum_tpv / (cum_vol + 1e-9)
        f["vwap_deviation"] = ((df["close"] - vwap) / (vwap + 1e-9)).clip(-0.05, 0.05)

        # ── 9: ATR (14-bar) normalised by price ──────────────────────────────
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift(1)).abs()
        low_close = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        f["atr_norm"] = (atr14 / (df["close"] + 1e-9)).clip(0.0, 0.05)

        # ── 10: On-Balance Volume Z-score ────────────────────────────────────
        direction = np.sign(df["close"].diff()).fillna(0)
        obv = (direction * df["volume"]).cumsum()
        obv_roll = obv.rolling(30)
        f["obv_z"] = ((obv - obv_roll.mean()) / (obv_roll.std() + 1e-9)).clip(-3.0, 3.0)

        # ── 11: Trend strength (simplified ADX-style directional index) ─────
        up_move = df["high"].diff()
        down_move = -df["low"].diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr14_safe = atr14.replace(0, np.nan)
        plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(14).mean() / (atr14_safe + 1e-9)
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).mean() / (atr14_safe + 1e-9)
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9) * 100
        f["trend_strength"] = (dx.rolling(14).mean() / 100.0).clip(0.0, 1.0)

        # ── 12: Mean-reversion Z-score (distance from fast EMA in std units) ──
        ema9 = df["close"].ewm(span=9, adjust=False).mean()
        dist = df["close"] - ema9
        dist_std = dist.rolling(20).std()
        f["mean_reversion_z"] = (dist / (dist_std + 1e-9)).clip(-3.0, 3.0)

        # ── 13: Realised-vol ratio (short/long) — volatility expansion signal ─
        vol_short = f["log_return"].rolling(5).std()
        vol_long = f["log_return"].rolling(20).std()
        f["realized_vol_ratio"] = (vol_short / (vol_long + 1e-9)).clip(0.0, 3.0)

        f = f.dropna()
        return f.values.astype(np.float32)
