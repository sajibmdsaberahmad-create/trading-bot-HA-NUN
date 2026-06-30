#!/usr/bin/env python3
"""
core/replay_live_runner.py — Fake-live training using historical bars (NOT backtest).

Separate from ScalperRunner / START.command — live trading is never touched.
Uses the same PPO model + feature pipeline; simulates fills via shadow circuit.
Streams 1-min bars at real-time pace (or configurable dilation).

Env:
  REPLAY_LIVE=true
  REPLAY_DATA_DIR=/path/to/data/replay
  REPLAY_REALTIME_PACE=true     # sleep actual bar interval (1 min bar ≈ 60s)
  REPLAY_TIME_DILATION_MS=0     # fixed ms/bar if REALTIME_PACE=false
  REPLAY_MODEL_PATH=models/ppo_trader.zip   # optional separate model copy
  REPLAY_BLOCK_IB=true          # never connect IB for orders (default true)
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.agent import build_ppo_agent, initialize_enhanced_system, predict_with_reasoning
from core.config import BotConfig
from core.env import TradingEnv
from core.features_enhanced import FeatureEngineerEnhanced
from core.market_hours import can_trade_now, get_market_state, market_status_line
from core.notify import Notifier, log
from core.ppo_entry_learning import on_entry_fill, set_ppo_model
from core.replay_bar_feeder import ReplayBarFeeder
from core.replay_clock import activate, deactivate, set_replay_time
from core.replay_data import iter_replay_bars, load_replay_intraday, resolve_replay_dir
from core.risk import RiskManager, TradePlan, compute_atr
from core.shadow_mode import ShadowCircuitBreaker


@dataclass
class ReplayLiveState:
    ticker: str
    cash: float
    shares: float = 0.0
    entry_price: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    journal: List[Dict[str, Any]] = field(default_factory=list)


class ReplayLiveRunner:
    """Standalone fake-live session — does not import or modify ScalperRunner."""

    def __init__(self, cfg: BotConfig, notifier: Optional[Notifier] = None):
        self.cfg = cfg
        self.notifier = notifier or Notifier(cfg)
        self.ticker = (os.getenv("REPLAY_TICKER") or cfg.TICKER).upper()
        self.root = resolve_replay_dir()
        if self.root is None:
            raise FileNotFoundError(
                "REPLAY_DATA_DIR not set. Run scripts/download_ib_replay_data.py first."
            )
        self.feeder = ReplayBarFeeder(self.ticker)
        self.risk = RiskManager(cfg, cfg.INITIAL_CASH, self.notifier)
        self.shadow = ShadowCircuitBreaker(cfg)
        self.shadow.in_shadow = True  # always simulate — never IB orders
        self.state = ReplayLiveState(ticker=self.ticker, cash=float(cfg.INITIAL_CASH))
        self.model = None
        self.ai_components: Dict[str, Any] = {}
        self._feature_buffer: Deque = deque(maxlen=cfg.WINDOW_SIZE + 10)
        self._bar_df_buffer: List[Dict] = []
        self.realtime_pace = os.getenv("REPLAY_REALTIME_PACE", "true").lower() in (
            "1", "true", "yes",
        )
        self.dilation_ms = int(os.getenv("REPLAY_TIME_DILATION_MS", "0"))
        self._model_path = self._resolve_model_path()
        self._start = os.getenv("REPLAY_START", "")
        self._end = os.getenv("REPLAY_END", "")

    def _resolve_model_path(self) -> str:
        repo = Path(__file__).resolve().parents[1]
        raw = os.getenv("REPLAY_MODEL_PATH", self.cfg.MODEL_PATH)
        candidates = []
        if raw:
            p = Path(raw)
            candidates.append(p if p.is_absolute() else repo / p)
            candidates.append(p)
        for name in ("models/ppo_trader_replay.zip", "ppo_trader.zip", self.cfg.MODEL_PATH):
            candidates.append(repo / name)
            candidates.append(Path(name))
        seen: set = set()
        for c in candidates:
            s = str(c)
            if s in seen:
                continue
            seen.add(s)
            if Path(c).is_file():
                return str(Path(c).resolve())
        return str((repo / self.cfg.MODEL_PATH).resolve())

    def _init_model(self) -> None:
        # Replay inference should stay on CPU (faster for MLP, avoids GPU warning)
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        self.ai_components = initialize_enhanced_system(self.cfg) or {}
        dummy_f = np.zeros((self.cfg.WINDOW_SIZE + 2, self.cfg.N_FEATURES), np.float32)
        dummy_px = np.ones(self.cfg.WINDOW_SIZE + 2, np.float32) * 100.0
        dummy_env = TradingEnv(
            dummy_f, dummy_px, self.cfg.INITIAL_CASH,
            self.cfg.TRANSACTION_COST_PCT, self.cfg.WINDOW_SIZE,
            self.cfg.DEFAULT_MAX_POSITION_PCT,
        )
        path = self._model_path if os.path.exists(self._model_path or "") else None
        self.model = build_ppo_agent(dummy_env, self.cfg, path)
        set_ppo_model(self.model)
        log.info(f"🧠 Replay-live PPO loaded: {path or 'fresh'} (live model untouched)")

    def _sleep_until_next_bar(self, prev_ts: Optional[pd.Timestamp], cur_ts: pd.Timestamp) -> None:
        wait_sec = 0.0
        if self.realtime_pace and prev_ts is not None:
            delta = (cur_ts - prev_ts).total_seconds()
            if 0 < delta <= 3600:
                wait_sec = delta
        elif self.dilation_ms > 0:
            wait_sec = self.dilation_ms / 1000.0

        if wait_sec <= 0:
            return

        if wait_sec >= 5.0:
            log.info(
                f"  ⏳ Next bar in {wait_sec:.0f}s "
                f"({cur_ts.tz_convert('America/New_York').strftime('%Y-%m-%d %H:%M ET')}) …"
            )
        remaining = wait_sec
        while remaining > 0:
            chunk = min(remaining, 10.0 if wait_sec >= 30.0 else remaining)
            time.sleep(chunk)
            remaining -= chunk
            if wait_sec >= 30.0 and remaining > 0:
                log.info(f"  ⏳ … {remaining:.0f}s until next bar")

    def _update_buffers(self, bar_df: pd.DataFrame) -> None:
        try:
            feats = FeatureEngineerEnhanced.compute(bar_df)
            if len(feats) > 0:
                for f in feats[-min(len(feats), self.cfg.WINDOW_SIZE):]:
                    self._feature_buffer.append(f)
            self._bar_df_buffer = bar_df.tail(self.cfg.WINDOW_SIZE + 10).to_dict("records")
        except Exception:
            pass

    def _obs(self, px: float) -> Optional[np.ndarray]:
        if len(self._feature_buffer) < self.cfg.WINDOW_SIZE:
            return None
        window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
        nav = self.state.cash + self.state.shares * px
        c_rat = self.state.cash / (nav + 1e-9)
        p_rat = (self.state.shares * px) / (nav + 1e-9) if self.state.shares > 0 else 0.0
        return np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)

    def _ppo_decision(self, px: float, for_entry: bool) -> Tuple[int, float, str]:
        obs = self._obs(px)
        if obs is None or self.model is None:
            return 0, 0.5, "warming up"
        bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
        action, conf, reasoning = predict_with_reasoning(
            self.model, obs, self.cfg, self.ai_components,
            bar_df=bar_df, for_entry=for_entry,
        )
        return int(action), float(conf), reasoning or ""

    def _try_entry(self, px: float, bar_df: pd.DataFrame) -> None:
        if self.state.shares > 0:
            return
        can, _ = can_trade_now(self.cfg)
        if not can:
            return
        action, conf, reasoning = self._ppo_decision(px, for_entry=True)
        threshold = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if action != 1 or conf < threshold:
            return
        atr = compute_atr(bar_df, period=14)
        plan = self.risk.compute_trade_plan(
            equity=self.state.cash,
            cash=self.state.cash,
            entry_price=px,
            atr=atr,
        )
        if plan is None or plan.shares < 1:
            return
        cost = plan.shares * px * (1 + self.cfg.TRANSACTION_COST_PCT)
        if cost > self.state.cash:
            return
        self.state.shares = float(plan.shares)
        self.state.entry_price = px
        self.state.stop = float(plan.initial_stop_price)
        self.state.target = float(plan.take_profit_price)
        self.state.cash -= cost
        self.shadow.open_shadow_trade(
            self.ticker, px, self.state.stop, self.state.target, int(plan.shares),
        )
        log.info(
            f"  📈 REPLAY ENTRY {self.ticker} {plan.shares}sh @ ${px:.2f} "
            f"stop=${self.state.stop:.2f} tgt=${self.state.target:.2f} | "
            f"PPO conf={conf:.0%} | {reasoning[:80]}"
        )
        try:
            obs = self._obs(px)
            feats = list(self._feature_buffer)[-1] if self._feature_buffer else None
            on_entry_fill(
                self.cfg,
                ticker=self.ticker,
                entry_price=px,
                shares=int(plan.shares),
                features=feats.tolist() if hasattr(feats, "tolist") else feats,
                spike_ratio=1.0,
                scan_score=50.0,
                model=self.model,
                obs=obs,
            )
        except Exception:
            pass

    def _check_exit(self, bar: Dict[str, float]) -> None:
        if self.state.shares <= 0:
            return
        low = float(bar["low"])
        high = float(bar["high"])
        px = float(bar["close"])
        exit_px = None
        reason = ""
        if low <= self.state.stop:
            exit_px, reason = self.state.stop, "stop"
        elif high >= self.state.target:
            exit_px, reason = self.state.target, "target"
        else:
            action, conf, _ = self._ppo_decision(px, for_entry=False)
            if action == 2 and conf >= float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)):
                exit_px, reason = px, "ppo_exit"

        if exit_px is None:
            return
        proceeds = self.state.shares * exit_px * (1 - self.cfg.TRANSACTION_COST_PCT)
        pnl = proceeds - self.state.shares * self.state.entry_price
        self.state.cash += proceeds
        self.state.trades += 1
        if pnl >= 0:
            self.state.wins += 1
        else:
            self.state.losses += 1
        self.state.pnl += pnl
        log.info(
            f"  📉 REPLAY EXIT {self.ticker} @ ${exit_px:.2f} ({reason}) "
            f"P&L=${pnl:+.2f} | NAV=${self.state.cash:.2f}"
        )
        self.state.journal.append({
            "ticker": self.ticker,
            "entry": self.state.entry_price,
            "exit": exit_px,
            "pnl": pnl,
            "reason": reason,
            "source": "replay_live",
        })
        self.state.shares = 0.0
        self.state.entry_price = 0.0
        if self.ticker in self.shadow.shadow_open:
            self.shadow.update_shadow_price(self.ticker, exit_px)

    def run(self) -> Dict[str, Any]:
        activate()
        try:
            return self._run_loop()
        finally:
            deactivate()

    def _run_loop(self) -> Dict[str, Any]:
        self._init_model()
        start = self._start or None
        end = self._end or None
        df = load_replay_intraday(self.ticker, root=self.root, start=start, end=end)
        warmup = min(self.cfg.WINDOW_SIZE + 5, len(df) // 4)
        seed_df = df.iloc[:warmup]
        stream_df = df.iloc[warmup:]
        self.feeder.seed_from_dataframe(seed_df, n_bars=warmup)

        pace = "real-time (~60s/bar)" if self.realtime_pace else f"{self.dilation_ms}ms/bar"
        est_sec = 0.0
        if self.realtime_pace:
            # Rough: ~390 bars per RTH day × 60s; full set ≈ many hours
            est_sec = len(stream_df) * 60.0 * 0.65  # ~65% are 1-min consecutive RTH bars
        elif self.dilation_ms > 0:
            est_sec = len(stream_df) * (self.dilation_ms / 1000.0)
        est_human = ""
        if est_sec >= 3600:
            est_human = f"~{est_sec / 3600:.1f} hours"
        elif est_sec >= 60:
            est_human = f"~{est_sec / 60:.0f} min"
        elif est_sec > 0:
            est_human = f"~{est_sec:.0f} sec"

        log.info("=" * 70)
        log.info("  REPLAY-LIVE (fake market stream — NOT backtest, NOT live IB orders)")
        log.info(f"  Ticker:  {self.ticker}")
        log.info(f"  Bars:    {len(stream_df):,} (+ {warmup} warmup)")
        log.info(f"  Range:   {df.index[0]} → {df.index[-1]}")
        log.info(f"  Pace:    {pace}" + (f" | ETA {est_human}" if est_human else ""))
        if self.realtime_pace and len(stream_df) > 500:
            log.info(
                "  Tip: full dataset at real-time takes days. "
                "Use REPLAY_REALTIME_PACE=false REPLAY_TIME_DILATION_MS=50 for fast training, "
                "or REPLAY_START/REPLAY_END for one day at real-time."
            )
        log.info(f"  Model:   {self._model_path}")
        log.info(f"  Data:    {self.root}")
        log.info("=" * 70)
        log.info("  ▶ Stream starting — first status in a few seconds …")

        prev_ts: Optional[pd.Timestamp] = None
        bar_count = 0
        last_log = time.time()
        rows = list(stream_df.iterrows())

        for i, (ts, row) in enumerate(rows):
            ts = pd.Timestamp(ts)
            set_replay_time(ts.to_pydatetime())

            bar = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            }
            self.feeder.push_bar(ts, row, source="replay_live")
            bar_df = self.feeder.get_live_decision_bars(min_bars=6)
            if bar_df is None or len(bar_df) < 6:
                prev_ts = ts
                continue
            self._update_buffers(bar_df)
            px = float(bar["close"])
            self._check_exit(bar)
            self._try_entry(px, bar_df)

            bar_count += 1
            now = time.time()
            ts_et = ts.tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M ET")
            mkt = get_market_state(self.cfg)
            nav = self.state.cash + self.state.shares * px

            if bar_count == 1 or bar_count <= 3 or now - last_log >= 15.0:
                log.info(
                    f"  ⏱ {ts_et} | {mkt.upper()} | ${px:.2f} | "
                    f"NAV=${nav:.2f} trades={self.state.trades} | "
                    f"bar {bar_count}/{len(stream_df)}"
                )
                last_log = now

            if i + 1 < len(rows):
                next_ts = pd.Timestamp(rows[i + 1][0])
                self._sleep_until_next_bar(ts, next_ts)
            prev_ts = ts

        nav = self.state.cash + self.state.shares * float(stream_df.iloc[-1]["close"])
        summary = {
            "ticker": self.ticker,
            "bars": bar_count,
            "trades": self.state.trades,
            "wins": self.state.wins,
            "losses": self.state.losses,
            "pnl": round(self.state.pnl, 2),
            "final_nav": round(nav, 2),
            "return_pct": round((nav / self.cfg.INITIAL_CASH - 1) * 100, 2),
        }
        log.info("=" * 70)
        log.info("  REPLAY-LIVE SESSION COMPLETE")
        for k, v in summary.items():
            log.info(f"  {k}: {v}")
        log.info("=" * 70)
        return summary


def run_replay_live(cfg: BotConfig) -> Dict[str, Any]:
    """Entry point from main.py --mode replay-live."""
    os.environ["REPLAY_LIVE"] = "true"
    if os.getenv("REPLAY_BLOCK_IB", "true").lower() in ("1", "true", "yes"):
        os.environ.setdefault("SHADOW_CIRCUIT_ENABLED", "true")
    notifier = Notifier(cfg)
    runner = ReplayLiveRunner(cfg, notifier)
    return runner.run()
