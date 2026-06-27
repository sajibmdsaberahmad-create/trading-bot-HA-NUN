#!/usr/bin/env python3
"""
core/replay_bar_feeder.py — DataManager-compatible bar feed for replay-live.

Pushes historical 1-min bars one at a time; no IB connection required.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional

import pandas as pd

from core.notify import log


class ReplayBarFeeder:
    """Minimal DataManager surface for replay-live runner."""

    def __init__(self, ticker: str, max_buffer: int = 500):
        self.cfg_ticker = ticker.upper()
        self._bar_buffer: Deque[Dict] = deque(maxlen=max_buffer)
        self.last_tick_price: Optional[float] = None
        self._current_bar: Optional[Dict] = None

    def seed_from_dataframe(self, df: pd.DataFrame, n_bars: int = 60) -> None:
        if df is None or df.empty:
            return
        tail = df.tail(n_bars)
        for ts, row in tail.iterrows():
            self._bar_buffer.append(self._row_to_bar(ts, row))
        if self._bar_buffer:
            self.last_tick_price = float(self._bar_buffer[-1]["close"])
        log.info(f"Replay feeder seeded {self.cfg_ticker}: {len(self._bar_buffer)} bars")

    def push_bar(self, ts, row, *, source: str = "replay_live") -> None:
        bar = self._row_to_bar(ts, row)
        bar["source"] = source
        self._bar_buffer.append(bar)
        self._current_bar = bar
        self.last_tick_price = float(bar["close"])

    @staticmethod
    def _row_to_bar(ts, row) -> Dict:
        return {
            "datetime": pd.Timestamp(ts).tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        }

    def get_latest_price(self) -> Optional[float]:
        return self.last_tick_price

    def get_live_decision_bars(self, min_bars: int = 6) -> Optional[pd.DataFrame]:
        if len(self._bar_buffer) < 1:
            return None
        df = pd.DataFrame(list(self._bar_buffer))
        df = df.set_index(pd.to_datetime(df["datetime"], utc=True)).sort_index()
        df = df[["open", "high", "low", "close", "volume"]]
        if len(df) < min_bars:
            return df if len(df) >= max(1, min_bars // 2) else None
        return df

    def get_bar_dataframe(self, min_bars: int = 20) -> Optional[pd.DataFrame]:
        return self.get_live_decision_bars(min_bars=min_bars)

    @property
    def current_bar(self) -> Optional[Dict]:
        return self._current_bar
