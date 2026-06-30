#!/usr/bin/env python3
"""
core/replay_market_hub.py — Multi-ticker synchronized fake-live market clock.

Advances one market timestamp per ScalperRunner main-loop tick (via ib.sleep),
identical cadence to live HANOON.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.config import BotConfig
from core.notify import log
from core.replay_clock import set_replay_time
from core.replay_data import load_replay_intraday, resolve_replay_dir


def list_intraday_tickers(root: Optional[Path] = None) -> List[str]:
    root = root or resolve_replay_dir()
    if root is None:
        return []
    intraday = root / "intraday"
    if not intraday.is_dir():
        return []
    out: List[str] = []
    for p in sorted(intraday.glob("*_1min.csv")):
        out.append(p.stem.replace("_1min", "").replace("_1MIN", "").upper())
    return out


class ReplayMarketHub:
    def __init__(
        self,
        cfg: BotConfig,
        *,
        root: Optional[Path] = None,
        tickers: Optional[List[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ):
        self.cfg = cfg
        self.root = root or resolve_replay_dir()
        self.tickers = tickers or list_intraday_tickers(self.root)
        self.start = start or os.getenv("REPLAY_START", "").strip() or None
        self.end = end or os.getenv("REPLAY_END", "").strip() or None
        self.realtime_pace = os.getenv("REPLAY_REALTIME_PACE", "false").lower() in (
            "1", "true", "yes",
        )
        self.dilation_ms = int(os.getenv("REPLAY_TIME_DILATION_MS", "50"))
        self._data: Dict[str, pd.DataFrame] = {}
        self._timeline: List[Tuple[pd.Timestamp, Dict[str, pd.Series]]] = []
        self._feeders: Dict[str, Any] = {}
        self._current_time: Optional[pd.Timestamp] = None
        self._idx = 0
        self._finished = False
        self._stopped = False
        self._feeding = False
        self._prev_ts: Optional[pd.Timestamp] = None
        self._lock = threading.Lock()
        self._load_all()

    @property
    def current_time(self) -> Optional[pd.Timestamp]:
        return self._current_time

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def steps_walked(self) -> int:
        return self._idx

    @property
    def timeline_complete(self) -> bool:
        return self._finished and not self._stopped

    def _load_all(self) -> None:
        if self.root is None:
            raise FileNotFoundError("REPLAY_DATA_DIR not set")
        frames: Dict[str, pd.DataFrame] = {}
        for t in self.tickers:
            try:
                df = load_replay_intraday(t, root=self.root, start=self.start, end=self.end)
                frames[t] = df
                log.info(f"  📂 Replay loaded {t}: {len(df):,} bars")
            except Exception as exc:
                log.warning(f"  ⏭ Skip {t}: {exc}")
        if not frames:
            raise FileNotFoundError("No intraday replay data loaded")
        self._data = frames
        self.tickers = sorted(frames.keys())
        by_ts: Dict[pd.Timestamp, Dict[str, pd.Series]] = defaultdict(dict)
        for ticker, df in frames.items():
            for ts, row in df.iterrows():
                by_ts[pd.Timestamp(ts)][ticker] = row
        self._timeline = sorted(by_ts.items(), key=lambda x: x[0])
        if not self._timeline:
            raise FileNotFoundError(
                "No fresh replay bars — all CSV data already trained. "
                "Re-download: python scripts/download_ib_replay_data.py --days 60"
            )
        try:
            from core.replay_consumption import farm_unconsumed_stats
            unc = farm_unconsumed_stats(self.root)
            log.info(
                f"  Fresh replay bars: {unc.get('unconsumed_bars', 0):,} "
                f"across {unc.get('tickers', 0)} tickers (already-trained bars skipped)"
            )
        except Exception:
            pass
        log.info(
            f"Replay hub: {len(self.tickers)} tickers | "
            f"{len(self._timeline):,} time steps | "
            f"{self._timeline[0][0]} → {self._timeline[-1][0]}"
        )

    def history_before(self, ticker: str, ts: Optional[pd.Timestamp]) -> pd.DataFrame:
        df = self._data.get(ticker.upper())
        if df is None or df.empty:
            return pd.DataFrame()
        if ts is None:
            return df.iloc[:0]
        return df[df.index <= ts]

    def warmup_bars(self, ticker: str, n_bars: int) -> Optional[pd.DataFrame]:
        """History ending at current replay clock — not dataset start (lookahead-safe)."""
        ts = self._current_time
        if ts is not None:
            hist = self.history_before(ticker, ts)
            if hist is not None and len(hist) >= 6:
                return hist.tail(max(n_bars, 30))
        df = self._data.get(ticker.upper())
        if df is None or len(df) < 6:
            return None
        return df.iloc[: max(n_bars, 30)]

    def register_stream(self, ticker: str, dm: Any) -> None:
        with self._lock:
            self._feeders[ticker.upper()] = dm

    def begin_feeding(self) -> None:
        self._feeding = True
        log.info(
            f"Replay hub: stream unlocked — {len(self._feeders)} feeds | "
            f"{len(self._timeline)} steps synced to ScalperRunner loop"
        )

    def advance_step(self) -> bool:
        """Push next synchronized market timestamp — called once per main-loop ib.sleep."""
        if not self._feeding or self._finished:
            return False
        if self._idx >= len(self._timeline):
            self._finished = True
            log.info("Replay hub: timeline complete")
            return False

        ts, group = self._timeline[self._idx]
        self._sleep_pace(self._prev_ts, ts)
        set_replay_time(ts.to_pydatetime())
        self._current_time = ts

        with self._lock:
            feeders = dict(self._feeders)
        for ticker, row in group.items():
            dm = feeders.get(ticker)
            if dm is not None and hasattr(dm, "push_replay_bar"):
                try:
                    dm.push_replay_bar(ts, row)
                except Exception as exc:
                    log.debug(f"Replay push {ticker}: {exc}")

        self._idx += 1
        self._prev_ts = ts
        if self._idx <= 3 or self._idx % 100 == 0:
            ts_et = ts.tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M ET")
            log.info(
                f"  ⏱ REPLAY MARKET {ts_et} | {len(group)} tickers | "
                f"step {self._idx}/{len(self._timeline)}"
            )
        if self._idx >= len(self._timeline):
            self._finished = True
            log.info("Replay hub: timeline complete")
        return True

    def _sleep_pace(self, prev_ts: Optional[pd.Timestamp], cur_ts: pd.Timestamp) -> None:
        import time
        wait = 0.0
        if self.realtime_pace and prev_ts is not None:
            delta = (cur_ts - prev_ts).total_seconds()
            if 0 < delta <= 3600:
                wait = delta
        elif self.dilation_ms > 0:
            wait = self.dilation_ms / 1000.0
        if wait >= 5.0:
            log.info(
                f"  ⏳ Next replay step in {wait:.0f}s "
                f"({cur_ts.tz_convert('America/New_York').strftime('%H:%M ET')}) …"
            )
        if wait > 0:
            time.sleep(wait)

    def current_bar(self, ticker: str) -> Optional[pd.Series]:
        """Latest synchronized bar for ticker at current replay timestamp."""
        ts = self._current_time
        if ts is None:
            return None
        group = dict(self._timeline[self._idx - 1][1]) if self._idx > 0 else {}
        row = group.get(ticker.upper())
        if row is not None:
            return row
        df = self._data.get(ticker.upper())
        if df is None or df.empty:
            return None
        sub = df[df.index <= ts]
        if sub.empty:
            return None
        return sub.iloc[-1]

    def stop(self) -> None:
        """Session ending — do not mark timeline finished (consumption uses _idx only)."""
        self._stopped = True
