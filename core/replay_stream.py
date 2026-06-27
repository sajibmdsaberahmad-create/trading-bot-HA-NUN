#!/usr/bin/env python3
"""
core/replay_stream.py — Time-dilated async-style bar stream from REPLAY_DATA_DIR.

Yields daily bars one at a time with optional real-world delay (time dilation)
so PPO / backtest_engine can mimic a live event loop without IB Gateway.
"""

from __future__ import annotations

import os
import time
from typing import Dict, Generator, Iterator, Optional

from core.replay_data import iter_replay_bars, load_replay_csv, resolve_replay_dir


class TimeDilatedReplayStream:
    """
    Streams daily bars from local CSV with optional pause between events.

    Args:
        ticker: Symbol (e.g. ABBV, SOFI)
        time_dilation_ms: Real milliseconds between bar events (0 = no wait)
        start / end: Optional date filter YYYY-MM-DD
    """

    def __init__(
        self,
        ticker: str,
        time_dilation_ms: int = 0,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        root=None,
    ):
        self.ticker = ticker.upper().strip()
        self.delay_sec = max(0.0, float(time_dilation_ms) / 1000.0)
        self.start = start
        self.end = end
        self.root = root or resolve_replay_dir()
        if self.root is None:
            raise ValueError(
                "REPLAY_DATA_DIR not set and data/replay not found. "
                "Run: python scripts/download_replay_data.py"
            )
        self._df = load_replay_csv(
            self.ticker, root=self.root, start=start, end=end,
        )
        self._index = 0
        self._total = len(self._df)

    @property
    def total_bars(self) -> int:
        return self._total

    def stream(self) -> Generator[Dict, None, None]:
        """Yield bars sequentially; sleep between bars if time_dilation_ms > 0."""
        for bar in iter_replay_bars(
            self.ticker,
            root=self.root,
            start=self.start,
            end=self.end,
        ):
            yield bar
            if self.delay_sec > 0:
                time.sleep(self.delay_sec)

    def __iter__(self) -> Iterator[Dict]:
        return self.stream()


def replay_time_dilation_ms() -> int:
    return int(os.getenv("REPLAY_TIME_DILATION_MS", "0"))


def replay_stream_enabled() -> bool:
    return os.getenv("REPLAY_STREAM", "false").lower() in ("1", "true", "yes")
