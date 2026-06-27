#!/usr/bin/env python3
"""ReplayDataManager — DataManager that reads from ReplayMarketHub instead of IB."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

from core.config import BotConfig
from core.data import DataManager, _utc_timestamp
from core.notify import log

if TYPE_CHECKING:
    from core.replay_market_hub import ReplayMarketHub


class ReplayDataManager(DataManager):
    """1-min bars + simulated ticks from replay hub (no IB HMDS or live streams)."""

    def __init__(
        self,
        connector: Any,
        cfg: BotConfig,
        hub: "ReplayMarketHub",
    ):
        super().__init__(connector, cfg)
        self.hub = hub
        self._stream_active = False

    def _get_contract(self):
        try:
            import ib_insync as ibi
            return ibi.Stock(self.cfg.TICKER, self.cfg.EXCHANGE, self.cfg.CURRENCY)
        except Exception:
            return None

    def fetch_historical(
        self,
        duration: Optional[str] = None,
        bar_size: Optional[str] = None,
        use_rth: bool = True,
        quiet: bool = False,
    ) -> pd.DataFrame:
        df = self.hub.history_before(self.cfg.TICKER, self.hub.current_time)
        if df is None or df.empty:
            raise RuntimeError(f"No replay history for {self.cfg.TICKER}")
        if not quiet:
            log.debug(f"Replay history {self.cfg.TICKER}: {len(df)} bars")
        return df.copy()

    def start_tick_stream(self, realtime_only: bool = False, quiet: bool = False):
        self.hub.register_stream(self.cfg.TICKER, self)
        self._stream_active = True
        msg = f"  📡 REPLAY STREAM {self.cfg.TICKER} (CSV fake-live)"
        (log.debug if quiet else log.info)(msg)

    def push_replay_bar(self, ts: pd.Timestamp, row: pd.Series) -> None:
        """Inject one 1-min bar and simulate tick prints for spike monitor."""
        bar = {
            "datetime": _utc_timestamp(ts),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        }
        self._bar_buffer.append(bar)
        px = float(bar["close"])
        self.last_tick_price = px
        self.last_tick_time = bar["datetime"]
        self._emit_tick(px, bar["datetime"], size=int(row["volume"]))

    def _emit_tick(self, price: float, ts: pd.Timestamp, size: int = 0) -> None:
        tick = {"price": price, "datetime": ts, "size": size}
        self._tick_buffer.append(tick)
        for cb in self._tick_callbacks:
            try:
                cb(price, ts)
            except Exception:
                pass
