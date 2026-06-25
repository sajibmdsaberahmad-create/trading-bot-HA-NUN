#!/usr/bin/env python3
"""
core/scalper_micro_predict.py — Sub-second scalper read on freshest bars.

Merges live tick into the forming 1-min bar and projects 1–3 bar momentum
for spike entry, profit fade, and loss pressure — no Ollama wait.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from core.data import DataManager


def _forming_volume(dm: Optional["DataManager"]) -> int:
    if dm is None:
        return 0
    vol = 0
    acc = getattr(dm, "_fast_acc", None) or []
    vol += sum(int(t.get("size", 0)) for t in acc)
    ticks = list(getattr(dm, "_tick_buffer", []))
    if ticks:
        vol += sum(int(t.get("size", 0)) for t in ticks[-80:])
    return int(vol)


def bars_with_live_tick(
    df: pd.DataFrame,
    live_px: float,
    dm: Optional["DataManager"] = None,
) -> pd.DataFrame:
    """Update or append the forming minute bar with the latest tick price/volume."""
    if df is None or len(df) == 0 or live_px <= 0:
        return df
    out = df.copy()
    vol_add = _forming_volume(dm)
    idx = out.index[-1]
    row = out.iloc[-1].copy()
    row["close"] = float(live_px)
    row["high"] = max(float(row["high"]), live_px)
    row["low"] = min(float(row["low"]), live_px)
    if vol_add > 0:
        row["volume"] = int(max(float(row["volume"]), vol_add))
    out.iloc[-1] = row
    return out


def _ema(series: np.ndarray, span: int) -> float:
    if len(series) < 2:
        return float(series[-1]) if len(series) else 0.0
    alpha = 2.0 / (span + 1.0)
    v = float(series[0])
    for x in series[1:]:
        v = alpha * float(x) + (1.0 - alpha) * v
    return v


def _vwap(closes: np.ndarray, volumes: np.ndarray) -> float:
    v = volumes.sum()
    if v <= 0:
        return float(closes[-1])
    typical = closes  # already close proxy for scalper speed
    return float((typical * volumes).sum() / v)


def micro_forecast(
    df: pd.DataFrame,
    live_px: float,
    dm: Optional["DataManager"] = None,
) -> Dict[str, Any]:
    """
    Fast 1–3 bar forward read for scalper decisions.

    Returns spike_likelihood, fade_risk, loss_pressure, profit_run, dir,
    pred_1bar, pred_3bar, vol_accel, momentum.
    """
    empty = {
        "dir": 0,
        "momentum": 0.0,
        "vol_accel": 1.0,
        "spike_likelihood": 0.0,
        "fade_risk": 0.0,
        "loss_pressure": 0.0,
        "profit_run": 0.0,
        "pred_1bar": live_px,
        "pred_3bar": live_px,
    }
    if df is None or len(df) < 6 or live_px <= 0:
        return empty

    work = bars_with_live_tick(df, live_px, dm)
    closes = work["close"].values.astype(float)
    highs = work["high"].values.astype(float)
    lows = work["low"].values.astype(float)
    vols = work["volume"].values.astype(float)

    n = len(closes)
    ema3 = _ema(closes[-min(8, n):], 3)
    ema8 = _ema(closes[-min(12, n):], 8)
    slope = (closes[-1] - closes[-min(5, n)]) / max(closes[-min(5, n)], 1e-9)
    roc = (closes[-1] / max(closes[-min(3, n)], 1e-9)) - 1.0

    vol_tail = max(float(vols[-3:].mean()), 1.0)
    vol_base = max(float(vols[-min(20, n):-1].mean()), 1.0)
    vol_accel = vol_tail / vol_base

    vwap = _vwap(closes[-min(20, n):], vols[-min(20, n):])
    above_vwap = live_px >= vwap * 0.998

    recent_high = float(highs[-min(8, n):-1].max()) if n > 2 else float(highs[-1])
    breakout = live_px > recent_high * 1.0005

    mom = float(np.clip(slope * 40.0 + roc * 20.0, -1.0, 1.0))
    direction = 1 if mom > 0.08 else (-1 if mom < -0.08 else 0)

    pred_1 = live_px * (1.0 + slope * 0.6 + roc * 0.4)
    pred_3 = live_px * (1.0 + slope * 1.4 + roc * 0.9)

    spike_likelihood = 0.0
    spike_likelihood += min(0.45, max(0.0, (vol_accel - 1.0) * 0.35))
    spike_likelihood += 0.25 if breakout else 0.0
    spike_likelihood += 0.2 if above_vwap and mom > 0 else 0.0
    spike_likelihood += min(0.25, max(0.0, mom * 0.3))
    spike_likelihood = float(np.clip(spike_likelihood, 0.0, 1.0))

    extension = (live_px - ema8) / max(ema8, 1e-9)
    vol_fade = vol_accel < 0.85 and mom > 0.05
    fade_risk = float(np.clip(
        max(0.0, extension * 8.0) + (0.35 if vol_fade else 0.0) + (0.2 if live_px < ema3 else 0.0),
        0.0, 1.0,
    ))

    loss_pressure = float(np.clip(
        max(0.0, -mom * 0.55)
        + (0.3 if not above_vwap else 0.0)
        + (0.25 if live_px < float(lows[-min(5, n):].min()) * 1.001 else 0.0),
        0.0, 1.0,
    ))

    profit_run = float(np.clip(
        max(0.0, mom * 0.5) + (0.25 if breakout and vol_accel > 1.1 else 0.0),
        0.0, 1.0,
    ))

    return {
        "dir": direction,
        "momentum": round(mom, 4),
        "vol_accel": round(vol_accel, 3),
        "spike_likelihood": round(spike_likelihood, 3),
        "fade_risk": round(fade_risk, 3),
        "loss_pressure": round(loss_pressure, 3),
        "profit_run": round(profit_run, 3),
        "pred_1bar": round(pred_1, 4),
        "pred_3bar": round(pred_3, 4),
        "vwap": round(vwap, 4),
        "breakout": breakout,
    }
