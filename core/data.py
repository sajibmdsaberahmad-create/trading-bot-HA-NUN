#!/usr/bin/env python3
"""
core/data.py — Market data: historical fetch + live tick stream + bar aggregation.

THREE LAYERS OF DATA, EACH WITH A DIFFERENT JOB
┌──────────────────┬───────────────────────────────────────────────────────┐
│ Tick stream       │ reqTickByTickData("AllLast") — every trade print    │
│ (fastest)         │ print from the exchange, arrives whenever a trade    │
│                   │ happens (no fixed interval — that's how real         │
│                   │ exchanges work; there is no "millisecond bar").      │
│                   │ Used for: stop-loss / take-profit / trailing checks. │
│                   │ The bot reacts to a stop breach on the very next     │
│                   │ tick, not on the next 1-minute bar close.            │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Fast bars (5s)    │ Built by aggregating ticks. Used for: short-horizon  │
│                   │ volatility (ATR) calculations that feed the          │
│                   │ predictive stop/target engine.                       │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Decision bars     │ 1-minute bars, built from the 5s bars. Used for:     │
│ (1 min)           │ PPO agent's observation window (this is what it      │
│                   │ was trained on — keep this stable).                  │
└──────────────────┴───────────────────────────────────────────────────────┘

If your IB market data subscription does not support tick-by-tick (some
basic/delayed subscriptions don't), set USE_TICK_STREAM=False in config
and the bot automatically falls back to 5-second real-time bars, which
covers the vast majority of intrabar stop monitoring needs.
"""

from collections import deque
from typing import Deque, Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import ib_insync as ibi
except ImportError:
    raise SystemExit("ERROR: ib_insync not installed. Fix: pip install ib_insync")

from core.config import BotConfig
from core.connector import IBConnector
from core.notify import log


_VALID_TICK_BY_TICK_TYPES = frozenset({"Last", "AllLast", "BidAsk", "MidPoint"})


def tick_by_tick_type(cfg: BotConfig) -> str:
    """IB tickType string — case-sensitive; AllLast includes odd-lot/off-exchange."""
    raw = str(getattr(cfg, "TICK_BY_TICK_TYPE", "AllLast") or "AllLast")
    return raw if raw in _VALID_TICK_BY_TICK_TYPES else "AllLast"


def _utc_timestamp(dt) -> pd.Timestamp:
    """Normalize IB/datetime timestamps to UTC without double tz assignment."""
    if dt is None:
        return pd.Timestamp.utcnow()
    ts = pd.Timestamp(dt)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def coalesce_bars(*sources: Optional[pd.DataFrame], min_len: int = 1) -> Optional[pd.DataFrame]:
    """Return the first non-empty DataFrame — never use `or` on DataFrames."""
    for df in sources:
        if df is not None and len(df) >= min_len:
            return df
    return None


class DataManager:
    """Pulls historical data and manages the live tick/bar buffers."""

    def __init__(self, connector: IBConnector, cfg: BotConfig):
        self.conn = connector
        self.cfg  = cfg
        self.ib   = connector.ib

        self._contract = None  # DataManager's own contract cache

        # Tick buffer — every individual trade print
        self._tick_buffer: Deque[Dict] = deque(maxlen=cfg.TICK_BUFFER_MAXLEN)
        self.last_tick_price: Optional[float] = None
        self.last_tick_time:  Optional[pd.Timestamp] = None

        # Fast (5-sec) bar buffer, built from ticks
        self._fast_bar_buffer: Deque[Dict] = deque(maxlen=2_000)
        self._fast_acc: List[Dict] = []
        self._current_fast_bucket: Optional[pd.Timestamp] = None

        # Decision (1-min) bar buffer, built from fast bars
        self._bar_buffer: Deque[Dict] = deque(maxlen=1_000)
        self._5sec_acc: List[Dict] = []
        self._current_minute: Optional[pd.Timestamp] = None

        self._tick_handle = None
        self._realtime_handle = None

        # Callbacks other components can subscribe to for tick-level reaction
        self._tick_callbacks = []

    def on_tick(self, callback):
        """Register a function(price: float, ts: pd.Timestamp) called on every tick."""
        self._tick_callbacks.append(callback)

    def _get_contract(self):
        """Qualify and cache the contract for this DataManager's cfg.TICKER."""
        if self._contract is None or getattr(self._contract, 'symbol', None) != self.cfg.TICKER:
            raw = ibi.Stock(self.cfg.TICKER, self.cfg.EXCHANGE, self.cfg.CURRENCY)
            qualified = self.ib.qualifyContracts(raw)
            if not qualified:
                raise RuntimeError(
                    f"Could not qualify contract for '{self.cfg.TICKER}' "
                    f"(exchange={self.cfg.EXCHANGE}, currency={self.cfg.CURRENCY}). "
                    "Check ticker, data subscription, and IB Gateway login."
                )
            self._contract = qualified[0]
        return self._contract

    # ── Historical data ──────────────────────────────────────────────────────

    def fetch_historical(self, duration: Optional[str] = None,
                          bar_size: Optional[str] = None,
                          use_rth: bool = True,
                          quiet: bool = False) -> pd.DataFrame:
        duration = duration or self.cfg.HISTORY_DURATION
        bar_size = bar_size or self.cfg.HISTORY_BAR_SIZE

        if quiet:
            log.debug(f"Fetching {duration} of {bar_size} bars for {self.cfg.TICKER} …")
        else:
            log.info(f"Fetching {duration} of {bar_size} bars for {self.cfg.TICKER} …")
        contract = self._get_contract()

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=use_rth,
            formatDate=1,
            keepUpToDate=False,
        )

        if not bars:
            raise RuntimeError(
                "IB returned no historical data. Possible causes:\n"
                "  - Market data subscription does not cover this ticker\n"
                "  - IB Gateway is not fully logged in\n"
                "  - Outside data availability window\n"
                "  - Paper account may need a market data subscription"
            )

        df = ibi.util.df(bars)
        df = df.rename(columns={"date": "datetime"}).set_index("datetime")
        df.index = pd.to_datetime(df.index, utc=True)
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df = df.sort_index().dropna()
        df = df[df["close"] > 0]

        fetch_log = log.debug if quiet else log.info
        fetch_log(
            f"Fetched {len(df):,} bars "
            f"[{df.index[0].strftime('%Y-%m-%d')} -> {df.index[-1].strftime('%Y-%m-%d')}]"
        )
        return df

    # ── Live tick stream ─────────────────────────────────────────────────────

    def start_tick_stream(self, realtime_only: bool = False):
        """
        Subscribe to live market data for a locked watch target.
        realtime_only=True uses 5-second bars (lighter — run one per locked ticker).
        Default tries tick-by-tick first, then falls back to 5-second bars.
        """
        if realtime_only:
            try:
                self._start_realtime_bars_fallback()
            except Exception as exc:
                log.warning(f"Real-time bar stream failed: {exc}")
            return
        try:
            if self.cfg.USE_TICK_STREAM:
                try:
                    contract = self._get_contract()
                    tbt = tick_by_tick_type(self.cfg)
                    ticker = self.ib.reqTickByTickData(contract, tbt, 0, False)
                    ticker.updateEvent += self._on_tick
                    self._tick_handle = ticker
                    self.conn.register_stream_manager(self.cfg.TICKER, self)
                    log.info(
                        f"Tick-by-tick stream started ({tbt}) — "
                        f"live trade prints for {self.cfg.TICKER}, sub-second."
                    )
                    return
                except Exception as exc:
                    log.warning(
                        f"Tick-by-tick stream unavailable ({exc}). "
                        "Falling back to 5-second real-time bars."
                    )

            self._start_realtime_bars_fallback()
        except Exception as exc:
            log.warning(f"Stream start failed entirely: {exc}")

    def _start_realtime_bars_fallback(self):
        contract = self._get_contract()
        use_rth = False
        try:
            from core.rth_session import realtime_bars_use_rth
            use_rth = realtime_bars_use_rth(self.cfg)
        except Exception:
            use_rth = True
        rt_bars = self.ib.reqRealTimeBars(
            contract, barSize=5, whatToShow="TRADES", useRTH=use_rth,
        )
        rt_bars.updateEvent += self._on_realtime_bar_fallback
        self._realtime_handle = rt_bars
        self.conn.register_stream_manager(self.cfg.TICKER, self)
        log.info("Real-time 5-second bar stream started (tick stream fallback mode).")

    def fallback_to_realtime_bars(self) -> None:
        """IB 10189/10190 — cancel dead tick-by-tick sub and start 5s bars immediately."""
        if self._realtime_handle is not None:
            return
        try:
            if self._tick_handle is not None and self._contract is not None:
                self.ib.cancelTickByTickData(
                    self._contract, tick_by_tick_type(self.cfg),
                )
        except Exception:
            pass
        self._tick_handle = None
        try:
            self._start_realtime_bars_fallback()
            log.info(
                f"  📡 {self.cfg.TICKER}: tick-by-tick denied — switched to 5s realtime bars"
            )
        except Exception as exc:
            log.warning(f"Realtime bar fallback failed for {self.cfg.TICKER}: {exc}")

    def stop_tick_stream(self):
        self.conn.unregister_stream_manager(self.cfg.TICKER)
        try:
            if self._tick_handle is not None and self._contract is not None:
                self.ib.cancelTickByTickData(
                    self._contract, tick_by_tick_type(self.cfg),
                )
            if self._realtime_handle is not None:
                self.ib.cancelRealTimeBars(self._realtime_handle)
        except Exception:
            pass
        self._tick_handle = None
        self._realtime_handle = None
        log.debug("Live data stream cancelled.")

    def _on_tick(self, ticker):
        self.conn.touch()
        if not ticker.tickByTicks:
            return
        for t in ticker.tickByTicks:
            price = float(t.price)
            size  = int(t.size)
            ts    = _utc_timestamp(t.time)
            if price <= 0:
                continue

            self.last_tick_price = price
            self.last_tick_time  = ts
            self._tick_buffer.append({"price": price, "size": size, "time": ts})

            self._accumulate_fast_bar(price, size, ts)

            for cb in self._tick_callbacks:
                try:
                    cb(price, ts)
                except Exception as exc:
                    log.warning(f"Tick callback error: {exc}")

    def _on_realtime_bar_fallback(self, bars, has_new_bar: bool):
        """Used only when tick-by-tick isn't available. Treats each 5s bar close as a tick."""
        self.conn.touch()
        if not has_new_bar or not bars:
            return
        last = bars[-1]
        price = float(last.close)
        ts = _utc_timestamp(last.time)

        self.last_tick_price = price
        self.last_tick_time = ts

        self._fast_bar_buffer.append({
            "datetime": ts, "open": float(last.open_), "high": float(last.high),
            "low": float(last.low), "close": price, "volume": int(last.volume),
        })
        self._maybe_flush_minute_from_fast(ts)

        for cb in self._tick_callbacks:
            try:
                cb(price, ts)
            except Exception as exc:
                log.warning(f"Tick callback error: {exc}")

    # ── Fast bar (5s) aggregation from ticks ─────────────────────────────────

    def _accumulate_fast_bar(self, price: float, size: int, ts: pd.Timestamp):
        bucket = ts.floor(f"{self.cfg.FAST_BAR_SECONDS}s")

        if self._current_fast_bucket is not None and bucket != self._current_fast_bucket:
            self._flush_fast_bar()
            self._fast_acc = []

        self._current_fast_bucket = bucket
        self._fast_acc.append({"price": price, "size": size, "time": ts})

    def _flush_fast_bar(self):
        if not self._fast_acc:
            return
        prices = [t["price"] for t in self._fast_acc]
        vol = sum(t["size"] for t in self._fast_acc)
        bar = {
            "datetime": self._current_fast_bucket,
            "open": prices[0], "high": max(prices), "low": min(prices),
            "close": prices[-1], "volume": vol,
        }
        self._fast_bar_buffer.append(bar)
        self._maybe_flush_minute_from_fast(self._current_fast_bucket)

    def _maybe_flush_minute_from_fast(self, ts: pd.Timestamp):
        minute = ts.floor("1min")
        if self._current_minute is not None and minute != self._current_minute:
            self._flush_minute_bar()
            self._5sec_acc = []
        self._current_minute = minute
        if self._fast_bar_buffer:
            self._5sec_acc.append(dict(self._fast_bar_buffer[-1]))

    def _flush_minute_bar(self):
        acc = self._5sec_acc
        if not acc:
            return
        minute_bar = {
            "datetime": self._current_minute,
            "open": acc[0]["open"],
            "high": max(b["high"] for b in acc),
            "low": min(b["low"] for b in acc),
            "close": acc[-1]["close"],
            "volume": sum(b["volume"] for b in acc),
        }
        self._bar_buffer.append(minute_bar)

    # ── Buffer access ─────────────────────────────────────────────────────────

    def seed_buffer_from_historical(self, n_bars: int = 300):
        """Pre-fill the 1-min bar buffer so the bot can trade immediately."""
        log.info(f"Seeding bar buffer with {n_bars} recent historical bars …")
        try:
            recent = self.fetch_historical(duration="5 D", bar_size=self.cfg.DECISION_BAR)
            recent = recent.tail(n_bars)
            for ts, row in recent.iterrows():
                self._bar_buffer.append({
                    "datetime": ts, "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    "volume": int(row["volume"]),
                })
            log.info(f"Buffer seeded: {len(self._bar_buffer)} bars ready")
        except Exception as exc:
            log.warning(f"Could not seed buffer from historical: {exc}")
            log.warning("Bot will wait for live bars to accumulate.")

    def seed_buffer_from_dataframe(self, df: pd.DataFrame, n_bars: int = 60):
        """Pre-fill the 1-min bar buffer from an existing OHLCV dataframe."""
        if df is None or len(df) == 0:
            return
        tail = df.tail(n_bars)
        for ts, row in tail.iterrows():
            self._bar_buffer.append({
                "datetime": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            })
        log.debug(f"Buffer seeded from dataframe: {len(self._bar_buffer)} bars")

    def _minute_bars_from_fast(self) -> Optional[pd.DataFrame]:
        """Build 1m OHLCV from accumulated 5s stream bars (before minute buffer flushes)."""
        fast = list(self._fast_bar_buffer)
        if len(fast) < 2:
            return None
        df5 = pd.DataFrame(fast)
        df5["datetime"] = pd.to_datetime(df5["datetime"], utc=True)
        df5 = df5.set_index("datetime").sort_index()
        ohlcv = df5.resample("1min").agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
        }).dropna(subset=["close"])
        if ohlcv.empty:
            return None
        live_px = self.get_latest_price()
        if live_px and live_px > 0:
            ohlcv.iloc[-1, ohlcv.columns.get_loc("close")] = float(live_px)
        return ohlcv

    def get_bar_dataframe(self, min_bars: int = 20) -> Optional[pd.DataFrame]:
        """1-minute decision bars, for the PPO agent."""
        if len(self._bar_buffer) < min_bars:
            return None
        df = pd.DataFrame(list(self._bar_buffer))
        return df.set_index("datetime").sort_index()

    def get_live_decision_bars(self, min_bars: int = 6) -> Optional[pd.DataFrame]:
        """
        Freshest 1-min bars for scalper decisions — includes forming minute
        updated with live tick price and accumulated volume.
        """
        rows = list(self._bar_buffer)
        df: Optional[pd.DataFrame] = None
        if rows:
            df = pd.DataFrame(rows)
            df = df.set_index(pd.to_datetime(df["datetime"], utc=True)).sort_index()
            df = df[["open", "high", "low", "close", "volume"]]

        fast_df = self._minute_bars_from_fast()
        if fast_df is not None and len(fast_df) > 0:
            if df is not None and len(df) > 0:
                df = pd.concat([df, fast_df])
                df = df[~df.index.duplicated(keep="last")].sort_index()
            else:
                df = fast_df

        if df is None or df.empty:
            if self.last_tick_price is None or self.last_tick_price <= 0:
                return None
            now = pd.Timestamp.utcnow().floor("1min")
            df = pd.DataFrame(
                [{
                    "open": float(self.last_tick_price),
                    "high": float(self.last_tick_price),
                    "low": float(self.last_tick_price),
                    "close": float(self.last_tick_price),
                    "volume": 0,
                }],
                index=[now],
            )

        live_px = self.get_latest_price()
        if live_px and live_px > 0:
            try:
                from core.scalper_micro_predict import bars_with_live_tick
                df = bars_with_live_tick(df, float(live_px), self)
            except Exception:
                pass
        if len(df) < min_bars:
            return df if len(df) >= max(1, min_bars // 2) else None
        return df

    def get_fast_bar_dataframe(self, n: int = 60) -> Optional[pd.DataFrame]:
        """Recent 5-second bars, for ATR / volatility used in stop sizing."""
        if len(self._fast_bar_buffer) < 5:
            return None
        df = pd.DataFrame(list(self._fast_bar_buffer)[-n:])
        return df.set_index("datetime").sort_index()

    def get_latest_price(self) -> Optional[float]:
        """The single freshest known price — from the tick stream if available."""
        if self.last_tick_price is not None:
            return self.last_tick_price
        if self._bar_buffer:
            return float(self._bar_buffer[-1]["close"])
        return None
