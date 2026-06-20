#!/usr/bin/env python3
"""
core/trader.py — The live trading orchestrator.

TWO LOOPS RUNNING TOGETHER
═══════════════════════════════════════════════════════════════════════
1. TICK LOOP (fires on every market tick, sub-second when liquid):
   - Updates the freshest known price
   - If a position is open: evaluate_tick() checks hard stop, trailing
     stop, hard take-profit, trailing profit-taker
   - If the trailing logic moved the stop, the live IB stop order is
     re-priced immediately (core/broker.py update_stop_price)
   - This is what makes exits fast — a stop breach is acted on the
     moment a trade prints, not on the next 1-minute bar close.

2. DECISION LOOP (fires once per new 1-minute decision bar):
   - Feature engineering -> PPO prediction -> risk-validated action
   - New entries are sized and placed here (computing a fresh ATR-based
     stop/target plan each time)
   - Online fine-tuning, status logging, performance summaries

Both loops share the same RiskManager.plan object so state stays
consistent. The IB connection itself runs the same heartbeat under
both — `ib.sleep()` pumps every IB event (ticks AND bar closes)
through the same callbacks, so there's no separate thread to manage.
"""

import os
import json
import time
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.features import FeatureEngineer
from core.risk import RiskManager, compute_atr, compute_momentum_score
from core.broker import BrokerExecutor, BracketHandle
from core.agent import build_ppo_agent, OnlineLearningManager
from core.env import TradingEnv
from core.performance import PerformanceTracker
from core.notify import log, Notifier


class LiveTrader:
    ACTION_NAMES = {0: "HOLD", 1: "BUY ", 2: "SELL"}

    def __init__(self, connector: IBConnector, cfg: BotConfig, notifier: Notifier):
        self.conn = connector
        self.ib = connector.ib
        self.cfg = cfg
        self.notifier = notifier

        self.data = DataManager(connector, cfg)
        self.broker = BrokerExecutor(connector, cfg)

        # account_equity is refreshed from IB's actual account values when
        # available; falls back to local tracking in pure-paper-sim mode
        self.account_equity = float(cfg.INITIAL_CASH)
        self.cash = float(cfg.INITIAL_CASH)
        self.shares = 0.0
        self.nav = float(cfg.INITIAL_CASH)

        self.risk = RiskManager(cfg, self.account_equity, notifier)
        self.perf = PerformanceTracker(cfg.INITIAL_CASH, cfg.PERF_PATH)

        self.model = None
        self.online: Optional[OnlineLearningManager] = None
        self.bracket_handle: Optional[BracketHandle] = None

        self._bars_processed = 0
        self._last_bar_time: Optional[pd.Timestamp] = None
        self._current_day: Optional[date] = None
        self._current_week: Optional[int] = None
        self._last_summary_hour: Optional[int] = None
        self._last_metrics_write: float = 0.0

    def _write_live_metrics(self, current_px: float, action: int = -1):
        """Write current bot state to live_metrics.json for the dashboard."""
        try:
            now = time.time()
            if now - self._last_metrics_write < 2.0:
                return  # throttle writes to once per 2 seconds
            self._last_metrics_write = now

            trades = []
            try:
                if os.path.exists(self.cfg.PERF_PATH):
                    df = pd.read_csv(self.cfg.PERF_PATH)
                    trades = df.tail(20).to_dict(orient="records")
            except Exception:
                pass

            equity_curve = []
            try:
                if os.path.exists(self.cfg.PERF_PATH):
                    df = pd.read_csv(self.cfg.PERF_PATH)
                    if "portfolio_value" in df.columns:
                        equity_curve = df["portfolio_value"].dropna().tolist()
            except Exception:
                pass

            metrics = {
                "account_equity": round(self.account_equity, 2),
                "cash": round(self.cash, 2),
                "shares": self.shares,
                "open_pnl": round(self.nav - self.account_equity, 2) if self.nav else 0,
                "daily_pnl": round(self.perf.session_pnl if hasattr(self.perf, 'session_pnl') else 0, 2),
                "position": f"{self.shares:.0f} {self.cfg.TICKER}" if self.shares > 0 else "NONE",
                "current_price": round(current_px, 2) if current_px else 0,
                "nav": round(self.nav, 2),
                "last_action": LiveTrader.ACTION_NAMES.get(action, "N/A"),
                "win_rate": round(self.perf.win_rate * 100, 1) if hasattr(self.perf, 'win_rate') else 0,
                "bars_processed": self._bars_processed,
                "trades": trades[-20:],
                "equity_curve": equity_curve[-500:],
                "timestamp": datetime.utcnow().isoformat(),
            }
            with open("live_metrics.json", "w") as f:
                json.dump(metrics, f, indent=2)
        except Exception as exc:
            log.debug(f"Could not write live_metrics.json: {exc}")

    # ── Setup ─────────────────────────────────────────────────────────────────

    def setup(self):
        if not os.path.exists(self.cfg.MODEL_PATH):
            raise FileNotFoundError(
                f"No trained model found at '{self.cfg.MODEL_PATH}'.\n"
                "Run warm-up training first:\n"
                "  python main.py --mode warmup"
            )

        dummy_f = np.zeros((self.cfg.WINDOW_SIZE + 2, self.cfg.N_FEATURES), np.float32)
        dummy_px = np.ones(self.cfg.WINDOW_SIZE + 2, np.float32) * 100.0
        dummy_env = TradingEnv(dummy_f, dummy_px, self.cfg.INITIAL_CASH,
                                self.cfg.TRANSACTION_COST_PCT, self.cfg.WINDOW_SIZE)
        self.model = build_ppo_agent(dummy_env, self.cfg, self.cfg.MODEL_PATH)
        self.online = OnlineLearningManager(self.model, self.cfg)

        self._refresh_account_equity()

        self.data.seed_buffer_from_historical(n_bars=300)
        self.data.start_tick_stream()
        self.data.on_tick(self._on_tick)

        self.risk.new_day(self.account_equity)
        self._current_week = pd.Timestamp.utcnow().isocalendar()[1]

        log.info("Live trader setup complete. Listening for market data …")
        self.notifier.info(
            f"🤖 Bot started\nTicker: {self.cfg.TICKER}\n"
            f"Equity: ${self.account_equity:,.2f}\n"
            f"Mode: {'PAPER' if self.cfg.PAPER_TRADING else 'LIVE'}"
        )

    def _refresh_account_equity(self):
        """Pull live NetLiquidation from IB if available; else use local tracking."""
        try:
            values = self.ib.accountValues()
            for v in values:
                if v.tag == "NetLiquidation" and v.currency == self.cfg.CURRENCY:
                    self.account_equity = float(v.value)
                    return
        except Exception as exc:
            log.debug(f"Could not fetch IB account equity, using local tracking: {exc}")
        self.account_equity = self.nav

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        log.info("=" * 70)
        log.info("  LIVE TRADING")
        log.info(f"  Ticker:    {self.cfg.TICKER}")
        log.info(f"  Equity:    ${self.account_equity:,.2f}")
        log.info(f"  Model:     {self.cfg.MODEL_PATH}")
        log.info(f"  Risk/trade: ${self.cfg.risk_amount_usd(self.account_equity):.2f}")
        log.info(f"  Daily halt: {self.cfg.MAX_DAILY_LOSS_PCT:.0%} | Weekly halt: {self.cfg.MAX_WEEKLY_LOSS_PCT:.0%}")
        log.info("  Press Ctrl+C to stop cleanly")
        log.info("=" * 70)

        try:
            while True:
                self.ib.sleep(1)

                if not self.conn.is_connected():
                    log.warning("IB connection lost. Attempting reconnect …")
                    if not self.conn.reconnect():
                        break
                    self.data.start_tick_stream()

                self._maybe_send_daily_summary()

                bar_df = self.data.get_bar_dataframe()
                if bar_df is None or len(bar_df) == 0:
                    continue

                last_time = bar_df.index[-1]
                if last_time == self._last_bar_time:
                    continue

                self._last_bar_time = last_time
                self._check_new_day(last_time)
                self._check_new_week(last_time)
                self._process_decision_bar(bar_df)

        except KeyboardInterrupt:
            log.info("Keyboard interrupt — shutting down cleanly …")
        finally:
            self._shutdown()

    # ── Tick-level exit monitoring (fast loop) ───────────────────────────────

    def _on_tick(self, price: float, ts: pd.Timestamp):
        if self.risk.plan is None or self.shares <= 0:
            return

        prev_stop = self.risk.plan.current_stop_price
        should_exit, reason = self.risk.evaluate_tick(price)

        # If the trailing logic moved the stop, mirror it to IB immediately
        if self.risk.plan and self.risk.plan.current_stop_price != prev_stop:
            self.broker.update_stop_price(self.bracket_handle, self.risk.plan.current_stop_price)

        if should_exit:
            self._exit_position(price, reason)

    # ── Decision-bar processing (slow loop, ~1/min) ──────────────────────────

    def _process_decision_bar(self, bar_df: pd.DataFrame):
        self._bars_processed += 1

        features = FeatureEngineer.compute(bar_df)
        prices = bar_df["close"].values[-len(features):]

        if len(features) < self.cfg.WINDOW_SIZE + 2:
            needed = self.cfg.WINDOW_SIZE + 2
            log.debug(f"Warming up: {len(features)}/{needed} feature rows")
            return

        current_px = self.data.get_latest_price() or float(prices[-1])
        self._refresh_account_equity()
        self.nav = self.cash + self.shares * current_px

        log.info(f"Current Balance | Equity: ${self.account_equity:,.2f} | Cash: ${self.cash:,.2f} | NAV: ${self.nav:,.2f}")

        total = self.nav
        c_rat = self.cash / (total + 1e-9)
        p_rat = (self.shares * current_px) / (total + 1e-9)
        window = features[-self.cfg.WINDOW_SIZE:].flatten()
        obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)

        raw_action, _ = self.model.predict(obs, deterministic=True)
        action = int(raw_action)

        safe = self.risk.validate_action(action, self.account_equity, self.cash, self.shares,
                                           now_ts=bar_df.index[-1])
        if safe != action:
            log.warning(f"Risk override: {self.ACTION_NAMES[action]} -> {self.ACTION_NAMES[safe]}")
        action = safe

        if action == 1 and self.shares == 0:
            self._enter_position(bar_df, current_px)
        elif action == 2 and self.shares > 0:
            self._exit_position(current_px, "agent_signal")

        self.online.notify_new_bar(features, prices)

        if self._bars_processed % 5 == 0:
            ret = (self.nav / self.cfg.INITIAL_CASH - 1.0) * 100.0
            ts = self._last_bar_time.strftime("%H:%M") if self._last_bar_time else "??:??"
            log.info(
                f"Bar {self._bars_processed:5d} | {ts} | Price: ${current_px:>8.2f} | "
                f"Action: {self.ACTION_NAMES[action]} | NAV: ${self.nav:>10,.2f} ({ret:+.2f}%) | "
                f"Shares: {self.shares:.1f}"
            )

        # Write live metrics for the dashboard
        self._write_live_metrics(current_px, action)

        if self._bars_processed % 50 == 0:
            log.info("  PERF | " + self.perf.summary(self.nav))

    # ── Entry / exit execution ───────────────────────────────────────────────

    def _enter_position(self, bar_df: pd.DataFrame, current_px: float):
        fast_df = self.data.get_fast_bar_dataframe(n=60)
        atr_source = fast_df if fast_df is not None and len(fast_df) >= 15 else bar_df
        atr = compute_atr(atr_source, period=min(14, max(2, len(atr_source) - 1)))
        if atr <= 0:
            log.debug("Entry skipped: ATR not yet computable")
            return

        momentum = compute_momentum_score(bar_df, lookback=10)

        plan = self.risk.compute_trade_plan(
            equity=self.account_equity, cash=self.cash,
            entry_price=current_px, atr=atr, momentum_score=momentum,
        )
        if plan is None:
            return

        quantity = int(plan.shares)
        if quantity < 1:
            return

        bid, ask = self._get_bid_ask()
        limit_price, used_limit = self.broker.decide_entry_price(current_px, bid, ask)

        self.bracket_handle = self.broker.place_bracket_buy(
            quantity=quantity,
            limit_or_market_price=limit_price,
            stop_price=plan.initial_stop_price,
            target_price=plan.take_profit_price,
        )
        self.ib.sleep(1)

        cost = quantity * current_px * (1.0 + self.cfg.TRANSACTION_COST_PCT)
        self.cash -= cost
        self.shares += float(quantity)

        self.risk.open_position(plan)

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        self.perf.record_trade(timestamp, "BUY", current_px, quantity, cost, self.nav)

        log.info(f"ENTRY: {quantity} x {self.cfg.TICKER} @ ${current_px:.2f} | Cash left: ${self.cash:,.2f}")
        self.notifier.trade_opened(
            side="BUY", ticker=self.cfg.TICKER, qty=quantity, price=current_px,
            stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
            risk_usd=plan.risk_usd,
        )

    def _exit_position(self, price: float, reason: str):
        if self.shares <= 0:
            return
        quantity = int(self.shares)

        self.broker.flatten_position(quantity, handle=self.bracket_handle, urgent=True)
        self.ib.sleep(1)

        proceeds = quantity * price * (1.0 - self.cfg.TRANSACTION_COST_PCT)
        entry_price = self.risk.plan.entry_price if self.risk.plan else price
        pnl_usd = (price - entry_price) * quantity
        pnl_pct = (price / entry_price - 1.0) * 100.0 if entry_price else 0.0

        self.cash += proceeds
        self.shares = 0.0
        self.nav = self.cash

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        self.perf.record_trade(timestamp, "SELL", price, quantity, proceeds, self.nav, exit_reason=reason)
        self.risk.record_trade_result(pnl_usd)
        self.risk.close_position()
        self.bracket_handle = None

        log.info(f"EXIT ({reason}): {quantity} x {self.cfg.TICKER} @ ${price:.2f} | P&L: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)")

        if reason in ("hard_stop", "trailing_stop", "hard_take_profit", "trailing_profit"):
            self.notifier.stop_triggered(reason, self.cfg.TICKER, price, detail=f"P&L: ${pnl_usd:+.2f}")
        self.notifier.trade_closed(self.cfg.TICKER, quantity, price, pnl_usd, pnl_pct, reason)

    def _get_bid_ask(self):
        try:
            contract = self.conn.get_contract()
            tickers = self.ib.reqTickers(contract)
            if tickers:
                t = tickers[0]
                bid = float(t.bid) if t.bid and t.bid > 0 else None
                ask = float(t.ask) if t.ask and t.ask > 0 else None
                return bid, ask
        except Exception:
            pass
        return None, None

    # ── Day/week rollover & summaries ────────────────────────────────────────

    def _check_new_day(self, ts: pd.Timestamp):
        day = ts.date()
        if self._current_day is None:
            self._current_day = day
        elif day != self._current_day:
            self._current_day = day
            self._refresh_account_equity()
            self.risk.new_day(self.account_equity)
            log.info("  PERF | " + self.perf.summary(self.nav))

    def _check_new_week(self, ts: pd.Timestamp):
        week = ts.isocalendar()[1]
        if self._current_week is None:
            self._current_week = week
        elif week != self._current_week:
            self._current_week = week
            self._refresh_account_equity()
            self.risk.new_week(self.account_equity)

    def _maybe_send_daily_summary(self):
        now = pd.Timestamp.utcnow()
        if now.hour == self.cfg.DAILY_SUMMARY_HOUR_UTC and self._last_summary_hour != now.hour:
            self._last_summary_hour = now.hour
            self._refresh_account_equity()
            text = self.perf.daily_summary_text(self.nav, self.account_equity)
            self.notifier.daily_summary(text)

    def _shutdown(self):
        self.data.stop_tick_stream()

        if self.model:
            self.model.save(self.cfg.MODEL_PATH)
            log.info(f"Model saved -> {self.cfg.MODEL_PATH}")

        ret = (self.nav / self.cfg.INITIAL_CASH - 1.0) * 100.0
        log.info("=" * 70)
        log.info(f"  Session complete | Final NAV: ${self.nav:,.2f} ({ret:+.1f}%)")
        log.info("  PERF | " + self.perf.summary(self.nav))
        log.info(f"  Trade log saved -> {self.cfg.PERF_PATH}")
        log.info("=" * 70)

        if self.shares > 0:
            log.warning(
                f"NOTE: bot exiting with an OPEN position of {self.shares:.0f} shares. "
                "Its IB bracket stop/target orders remain live on IB's servers "
                "and will continue to protect the position even though the bot has stopped."
            )
            self.notifier.info(
                f"⚠️ Bot stopped with open position: {self.shares:.0f} {self.cfg.TICKER}\n"
                "Bracket stop/target orders remain active on IB."
            )
        else:
            self.notifier.info(f"🤖 Bot stopped. Final NAV: ${self.nav:,.2f} ({ret:+.1f}%)")

        self.conn.disconnect()
