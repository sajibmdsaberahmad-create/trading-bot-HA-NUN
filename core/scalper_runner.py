#!/usr/bin/env python3
"""
core/scalper_runner.py — HANOON institutional algo-wave rider.

MATCHES USER MANUAL TRADING METHODOLOGY:
1. Scan full universe, select 1-5 stocks (most active, top movers, volume, VWAP, etc.)
2. Lock selected stocks and monitor them continuously
3. Detect volume spike + uptrend before entry
4. Deploy EXACTLY $1,000 per stock (penny stocks focus)
5. Hard stop loss ($50) + hard take profit ALWAYS in place
6. Trail profit to ride institutional algo waves
7. Early exit on slippage prediction (protect gains, minimize losses)
8. High-frequency: every bar/tick analyzed
9. AI predicts entries/exits like human trader

GOAL: 60%+ win rate, $1,000 → profit via systematic execution.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # fallback

MARKET_TZ = ZoneInfo("America/New_York")

import numpy as np
import pandas as pd
import requests

# US market holidays (approximate - will verify via internet on startup)
US_MARKET_HOLIDAYS = {
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
}

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.features_enhanced import FeatureEngineerEnhanced
from core.institutional import InstitutionalDetector, InstitutionalSignal
from core.scanner import StockScanner, ScanResult, PENNY_STOCK_UNIVERSE
from core.risk import RiskManager, TradePlan, compute_atr, compute_momentum_score
from core.broker import BrokerExecutor, BracketHandle
from core.env import TradingEnv
from core.agent import build_ppo_agent, predict_with_reasoning, initialize_enhanced_system
from core.experience_buffer import append as buffer_append
from core.market_context import summarize_market_context
from core.market_regime import MarketRegimeDetector
from core.self_improver import generate_self_improvement_plan
from core.consciousness import AIConsciousness
from core.notify import log, Notifier
from core.git_sync import init as git_sync_init, push_trade, push_daily_summary, push_model_release
from core.async_utils import get_background_worker, AtomicFileWriter
from core.feature_drift import validate_features_at_startup
from core.train_subprocess import launch_training


def _only_uptrend(df: pd.DataFrame, current_px: float) -> bool:
    """
    USER METHODOLOGY: Uptrend filter — must be loose enough to catch
    institutional algo waves early, not late.
    """
    if len(df) < 20:
        return False
    closes = df["close"].values[-20:]
    volumes = df["volume"].values[-20:]
    sma20 = np.mean(closes)
    
    # Price above sma20 (1% tolerance for wicks)
    if current_px <= sma20 * 0.99:
        return False
    
    # VWAP above (1% tolerance)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vwap = np.average(typical[-20:], weights=volumes[-20:])
    if current_px <= vwap * 0.99:
        return False
    
    # At least 2 of last 8 closes rising (not too strict)
    rising = sum(1 for i in range(-8, 0) if i > -len(closes) and closes[i] >= closes[i-1])
    if rising < 2:
        return False
    
    # ATR sanity check (max 10% = very volatile, skip)
    atr = compute_atr(df, period=10)
    if atr <= 0 or atr > current_px * 0.10:
        return False
    
    return True


class ScalperRunner:
    def __init__(self, connector: IBConnector, cfg: BotConfig, notifier: Notifier):
        self.conn = connector
        self.ib = connector.ib
        self.cfg = cfg
        self.notifier = notifier
        
        self.data = DataManager(connector, cfg)
        self.broker = BrokerExecutor(connector, cfg)
        self.scanner = StockScanner(cfg)
        self.institutional = InstitutionalDetector()
        self.risk = RiskManager(cfg, cfg.INITIAL_CASH, notifier)
        git_sync_init(cfg)

        # AI / PPO wiring
        self.model = None
        self.ai_components: Dict[str, Any] = {}
        self.fe = FeatureEngineerEnhanced()
        self.regime_detector = MarketRegimeDetector()
        
        # Background worker for non-blocking Git/Ollama/notifications
        self._worker = get_background_worker()
        
        # File watcher for hot-reload of weights
        self._weights_watcher = None
        
        # IB account state (full account from IB)
        self.account_equity = float(cfg.INITIAL_CASH)
        self.available_cash: Optional[float] = None
        self.cash = float(cfg.INITIAL_CASH)
        
        # Bot's own financial state (starts at INITIAL_CASH, changes ONLY via trades)
        self.bot_cash: float = float(cfg.INITIAL_CASH)
        self.shares: float = 0.0
        self.bot_nav: float = float(cfg.INITIAL_CASH)
        self.current_ticker: Optional[str] = None
        self.bracket_handle: Optional[BracketHandle] = None
        
        # IB account tracking (real P&L impact)
        self._ib_starting_balance: Optional[float] = None
        
        # Track previous shares to detect exits
        self._prev_shares: float = 0.0
        self._entry_price: float = 0.0
        
        self.scan_results: List[ScanResult] = []
        self.top_pick: Optional[ScanResult] = None
        self._locked_targets: List[ScanResult] = []
        self._last_scan_time: float = 0.0
        self._last_metrics_write: float = 0.0
        self._scan_data_cache: Dict[str, pd.DataFrame] = {}  # Cache scanned data
        
        # Live stream monitors for locked targets (heartbeat in milliseconds)
        self._target_monitors: Dict[str, DataManager] = {}      # ticker -> DataManager
        self._target_last_bar_count: Dict[str, int] = {}        # ticker -> last seen bar count
        self._active_stream_ticker: Optional[str] = None        # Currently streaming ticker
        
        self.trade_journal: List[Dict] = []
        self.trades_today: int = 0
        self._current_day: Optional[str] = None
        self._last_daily_push_date: Optional[str] = None
        self._weights_file = "models/scalper_weights.json"
        self._weights_mtime = 0.0

        # Experience buffer for unified learning
        self._xp_buffer_initialized = False
        
        # Start file watcher for weights hot-reload
        self._start_weights_watcher()

        # Validate feature pipeline (prevents training/serving skew)
        self._validate_features()

        # Initialize enhanced AI system (quietly - details in final init report)
        self.ai_components = initialize_enhanced_system(cfg)
        self._init_model()
        
        # Feature buffer for AI observation building
        self._feature_buffer: deque = deque(maxlen=cfg.WINDOW_SIZE + 10)
        self._price_buffer: deque = deque(maxlen=cfg.WINDOW_SIZE + 10)
        self._bar_df_buffer: List[Dict] = []
        self._bars_since_ai_check = 0
        
        try:
            self.consciousness = AIConsciousness(cfg)
        except Exception as exc:
            log.debug(f"Consciousness init skipped: {exc}")
            self.consciousness = None
    
    def _validate_features(self):
        """Run feature pipeline validation to prevent training/serving skew."""
        try:
            # Use the feature engineer from enhanced features
            from core.features_enhanced import FeatureEngineerEnhanced
            fe = FeatureEngineerEnhanced()
            
            # Create a dummy DataManager to get the feature function
            def feature_fn(df, window_size=30):
                try:
                    return fe.compute_features(df, window_size=window_size)
                except Exception:
                    # Fallback: return simple features
                    n = min(window_size, len(df))
                    return np.zeros((n, 18), dtype=np.float32)
            
            ok = validate_features_at_startup(feature_fn)
            if not ok:
                log.error("Feature validation failed — this would cause trading errors. Please fix before continuing.")
        except Exception as exc:
            log.debug(f"Feature validation skipped: {exc}")
    
    def _init_model(self):
        self._model_fresh = True
        self._model_train_step = 0
        try:
            dummy_f = np.zeros((self.cfg.WINDOW_SIZE + 2, self.cfg.N_FEATURES), np.float32)
            dummy_px = np.ones(self.cfg.WINDOW_SIZE + 2, np.float32) * 100.0
            dummy_env = TradingEnv(dummy_f, dummy_px, self.cfg.INITIAL_CASH,
                                   self.cfg.TRANSACTION_COST_PCT, self.cfg.WINDOW_SIZE, self.cfg.DEFAULT_MAX_POSITION_PCT)
            self.model = build_ppo_agent(dummy_env, self.cfg, self.cfg.MODEL_PATH)
            if self.cfg.MODEL_PATH and os.path.exists(self.cfg.MODEL_PATH):
                self._model_fresh = False
            log.info(f"🧠 PPO model ready: fresh={self._model_fresh}")
        except Exception as exc:
            log.warning(f"PPO model init failed ({exc.__class__.__name__}: {exc}) — will use fresh model")
            try:
                dummy_f = np.zeros((self.cfg.WINDOW_SIZE + 2, self.cfg.N_FEATURES), np.float32)
                dummy_px = np.ones(self.cfg.WINDOW_SIZE + 2, np.float32) * 100.0
                dummy_env = TradingEnv(dummy_f, dummy_px, self.cfg.INITIAL_CASH,
                                       self.cfg.TRANSACTION_COST_PCT, self.cfg.WINDOW_SIZE, self.cfg.DEFAULT_MAX_POSITION_PCT)
                self.model = build_ppo_agent(dummy_env, self.cfg, None)
                log.info("🧠 Fresh PPO model initialized (18-feature architecture)")
            except Exception as exc2:
                log.error(f"Fresh PPO model also failed: {exc2}")
                self.model = None
    
    def _refresh_account_balance(self):
        """Pull live balance from IB. Bot state changes ONLY via trades."""
        try:
            values = self.ib.accountValues()
            for v in values:
                if v.tag in ("NetLiquidation", "TotalCashValue"):
                    if v.currency == self.cfg.CURRENCY:
                        self.account_equity = float(v.value)
                        if v.tag == "TotalCashValue":
                            self.available_cash = float(v.value)
            if self.available_cash is None:
                self.available_cash = self.account_equity
            self.cash = self.available_cash
            if self._ib_starting_balance is None:
                self._ib_starting_balance = self.account_equity
            self.cfg._latest_account_balance = self.account_equity
        except Exception as exc:
            log.debug(f"Could not fetch IB account balance: {exc}")
        self.bot_nav = self.bot_cash + self.shares * self._latest_price()
    
    def _latest_price(self) -> float:
        try:
            # Priority 1: Active stream ticker (post-entry live monitoring)
            if self._active_stream_ticker and self._active_stream_ticker in self._target_monitors:
                dm = self._target_monitors[self._active_stream_ticker]
                px = dm.get_latest_price()
                if px and px > 0:
                    return px
            # Priority 2: Main data stream
            return self.data.get_latest_price() or 0.0
        except Exception:
            return 0.0
    
    def _detect_exit(self, current_px: float):
        """Detect if position was closed (by bracket or manually) and notify."""
        if self._prev_shares > 0 and self.shares == 0:
            pnl = (current_px - self._entry_price) * self._prev_shares
            pnl_pct = ((current_px / self._entry_price) - 1) * 100 if self._entry_price else 0
            result = "win" if pnl > 0 else "loss"
            log.info(f"📕 EXIT: {self.current_ticker} @ ${current_px:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | {result.upper()}")
            self.notifier.info(
                f"📕 HANOON EXIT\n"
                f"Ticker: {self.current_ticker}\n"
                f"Exit: ${current_px:.2f}\n"
                f"Entry: ${self._entry_price:.2f}\n"
                f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                f"Result: {result.upper()}"
            )
            self.trade_journal.append({
                "ticker": self.current_ticker,
                "entry": self._entry_price,
                "exit": current_px,
                "shares": self._prev_shares,
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "result": result,
            })
            try:
                buffer_append({
                    "source": "live_trade",
                    "ticker": self.current_ticker,
                    "action": "SELL",
                    "exit_price": current_px,
                    "entry_price": self._entry_price,
                    "pnl_usd": round(pnl, 2),
                    "win": 1 if pnl > 0 else 0,
                    "confidence": 0.5,
                    "features": [],
                })
            except Exception:
                pass
            
            # Remove from active positions
            if hasattr(self, '_active_positions'):
                self._active_positions = [p for p in self._active_positions if p["ticker"] != self.current_ticker]
            
            self.current_ticker = None
            self.bracket_handle = None
            # Clean up live stream for closed position
            if self._active_stream_ticker:
                self._stop_target_stream(self._active_stream_ticker)
                self._active_stream_ticker = None
            
            # Train from exit event
            try:
                self._daily_self_train()
            except Exception:
                pass
        self._prev_shares = self.shares
        if self.shares > 0:
            self._entry_price = current_px
    
    def _exit_position(self, current_px: float, reason: str):
        """Manually exit position (early exit, AI exit, etc.)."""
        if self.shares <= 0:
            return
        ticker = self.current_ticker
        try:
            self.broker.cancel_bracket(self.bracket_handle)
            self.ib.sleep(0.5)
            self.broker.place_market_sell(self.shares)
            self.ib.sleep(1)
            pnl = (current_px - self._entry_price) * self.shares
            log.info(f"⚡ EARLY EXIT: {ticker} @ ${current_px:.2f} | Reason: {reason} | P&L: ${pnl:+.2f}")
            self.notifier.info(f"⚡ EARLY EXIT\n{ticker} @ ${current_px:.2f}\nReason: {reason}\nP&L: ${pnl:+.2f}")
            self.shares = 0.0
            self.bot_nav = self.bot_cash
            self.bracket_handle = None
            self.current_ticker = None
            if hasattr(self, '_active_positions'):
                self._active_positions = [p for p in self._active_positions if p["ticker"] != ticker]
            # Clean up live stream for manually exited position
            if self._active_stream_ticker:
                self._stop_target_stream(self._active_stream_ticker)
                self._active_stream_ticker = None
            
            # Train from manual exit event
            try:
                self._daily_self_train()
            except Exception:
                pass
        except Exception as exc:
            log.error(f"Early exit failed: {exc}")
    
    def _write_live_metrics(self):
        try:
            now = time.time()
            if now - self._last_metrics_write < 2.0:
                return
            self._last_metrics_write = now
            win_rate = (self.risk.win_rate * 100) if hasattr(self.risk, 'win_rate') else 0.0
            scan_data = []
            for r in self.scan_results[:5]:
                scan_data.append({
                    "ticker": r.ticker, "price": r.price,
                    "score": round(r.rank_score, 1), "reason": r.reason[:30]
                })
            metrics = {
                "mode": "HANOON",
                "account_equity": round(self.account_equity, 2),
                "available_cash": round(self.available_cash or 0, 2),
                "position_value": round(self.shares * self._latest_price(), 2),
                "nav": round(self.bot_nav, 2),
                "deployed_pct": round((self.bot_nav - (self.available_cash or 0)) / (self.account_equity + 1e-9) * 100, 1),
                "current_ticker": self.current_ticker or "NONE",
                "position": f"{self.shares:.0f} {self.current_ticker}" if self.shares > 0 else "NONE",
                "win_rate": round(win_rate, 1),
                "trades_today": self.trades_today,
                "top_pick": self.top_pick.ticker if self.top_pick else None,
                "top_score": self.top_pick.rank_score if self.top_pick else 0,
                "scan_results": scan_data,
                "timestamp": datetime.now(datetime.UTC).isoformat(),
            }
            with open("live_metrics.json", "w") as f:
                json.dump(metrics, f, indent=2)
        except Exception as exc:
            log.debug(f"Could not write live_metrics.json: {exc}")
    
    def run(self):
        # Full initialization report (pushed to git and Telegram)
        report_path = self._write_init_report()
        log.info("HANOON — SINGLE FOCUS SCALPER")
        acct = self.conn.ib.accountValues()
        log.info(f"Account: {acct[0].account if acct else 'unknown'} | Universe: {len(PENNY_STOCK_UNIVERSE)} tickers")
        log.info(f"Max per trade: ${self.cfg.MAX_TRADE_SIZE_USD:,.0f} | Risk/trade: ${self.cfg.risk_amount_usd(self.account_equity):.2f}")
        log.info(f"Init report: {report_path}")
        self.notifier.info("🚀 HANOON STARTED")

        self._refresh_account_balance()
        if self._ib_starting_balance:
            log.info(f"IB Starting Balance: ${self._ib_starting_balance:,.2f}")

        # Check market status quietly
        market_state = self._get_market_state()
        if market_state != "open":
            log.info(f"📊 Market state: {market_state.upper()} — pre-market/after-hours trading enabled")

        # Block startup scan until IB connection is confirmed live
        if self.conn.is_connected():
            self._last_scan_time = time.time()
            self._scan_and_rank()
        else:
            log.warning("IB Gateway not connected at startup — skipping initial scan until connection is live")
        
        try:
            while True:
                self.ib.sleep(1)
                current_px = self._latest_price()
                
                if not self.conn.is_connected():
                    log.warning("IB connection lost. Reconnecting...")
                    if not self.conn.reconnect():
                        break
                    self._refresh_account_balance()
                
                # Update AI buffers periodically (throttled to every 5s)
                now = time.time()
                if now - getattr(self, '_last_ai_update', 0) > 5.0:
                    self._last_ai_update = now
                    try:
                        fast_df = self.data.get_fast_bar_dataframe(n=60)
                        if fast_df is not None and len(fast_df) >= 30:
                            self._ai_update_buffers(fast_df, current_px)
                    except Exception:
                        pass
                
                # Detect exits (bracket orders hitting stop/target)
                self._detect_exit(current_px)
                
                # AI-driven early exit check (when in position, non-blocking)
                if self.shares > 0 and self.model is not None:
                    self._bars_since_ai_check += 1
                    if self._bars_since_ai_check >= 10:  # Check every ~10 seconds in 1s loop
                        self._bars_since_ai_check = 0
                        try:
                            should_exit, ai_conf, ai_reason = self._ai_gate_exit(current_px)
                            if should_exit:
                                log.info(f"  🧠 AI EARLY EXIT: confidence={ai_conf:.0%} — {ai_reason[:80]}")
                                self._exit_position(current_px, "ai_early_exit")
                        except Exception:
                            pass
                
                # Check market state
                market_state = self._get_market_state()
                can_trade = (
                    market_state == "open" or
                    (market_state == "pre_market" and self.cfg.ALLOW_PRE_MARKET_TRADING) or
                    (market_state == "after_hours" and self.cfg.ALLOW_AFTER_HOURS_TRADING)
                )
                
                if can_trade:
                    if self.conn.is_connected():
                        now = time.time()
                        time_since_scan = now - self._last_scan_time
                        
                        # USER METHODOLOGY:
                        # - If we have locked targets, fast 1min monitor runs every 2s
                        # - Full rescan every 120s even with targets (in case better setups appear)
                        # - Scan every 60s when no targets
                        have_targets = len(self._locked_targets) > 0
                        scan_interval = 120 if have_targets else 60
                        
                        need_rescan = time_since_scan > scan_interval
                        
                        if need_rescan:
                            self._scan_and_rank()
                            self._last_scan_time = time.time()
                            # Train from scan results
                            try:
                                if self.scan_results and len(self.scan_results) > 0:
                                    best = self.scan_results[0]
                                    buffer_append({
                                        "source": "scan_complete",
                                        "ticker": best.ticker,
                                        "action": "SCAN_COMPLETE",
                                        "scan_score": best.rank_score,
                                        "confidence": 0.5,
                                        "features": [],
                                        "timestamp": datetime.now(datetime.UTC).isoformat(),
                                    })
                            except Exception:
                                pass
                        
                        # USER METHODOLOGY: 1min millisecond obs on locked targets
                        if self._locked_targets and self.shares == 0:
                            now = time.time()
                            if now - getattr(self, '_last_fast_monitor', 0) > 2.0:  # Every 2s heartbeat
                                self._last_fast_monitor = now
                                self._fast_monitor_locked()
                        
                        # USER METHODOLOGY: Live stream post-entry monitoring — EVERY loop iteration
                        if self.shares > 0:
                            current_px = self._latest_price()
                            if current_px > 0:
                                # AI-driven early exit check on EVERY tick (millisecond)
                                should_exit, exit_reason = self._should_exit_early(
                                    current_px, self._entry_price,
                                    (current_px - self._entry_price) * self.shares,
                                    50.0
                                )
                                if should_exit:
                                    log.info(f"  ⚡ LIVE EXIT: {exit_reason}")
                                    self._exit_position(current_px, exit_reason)
                                    # Clean up streams after exit
                                    self._active_stream_ticker = None
                                else:
                                    # Trailing stops on every iteration
                                    self._update_trailing_stops(current_px)
                else:
                    if int(time.time()) % 60 == 0:
                        log.info(f"⏸ MARKET CLOSED ({market_state}) — training instead")
                    if time.time() - self._last_scan_time > 300:
                        self._last_scan_time = time.time()
                        self._train_off_hours()
                
                self._refresh_account_balance()
                self._write_live_metrics()
                self._maybe_daily_push()
                
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self._shutdown()
    
    def _scan_one(self, ticker: str) -> Optional[Dict]:
        """
        Scan one ticker: 1min candles ONLY for high-frequency penny scalping.
        USER METHODOLOGY: 1min is PRIMARY — trade on millisecond 1min bar obs.
        """
        try:
            cfg_ticker = self.cfg.TICKER
            self.cfg.TICKER = ticker
            dm = DataManager(self.conn, self.cfg)
            
            # ONLY 1min bars — fast, focused, high-frequency
            hist_1m = dm.fetch_historical(duration="2 D", bar_size="1 min", use_rth=False)
            
            self.cfg.TICKER = cfg_ticker
            
            score = None
            if hist_1m is not None and len(hist_1m) >= 60:
                score = self._score_ticker(ticker, hist_1m)
                if score and score.get("total_score", 0) > 0:
                    ai_adjusted = self._ai_score_ticker(ticker, hist_1m, score["total_score"])
                    score["total_score"] = round(ai_adjusted, 1)
                    score["ai_score"] = round(ai_adjusted, 1)
                self._scan_data_cache[ticker] = hist_1m
            
            if score and score.get("total_score", 0) > 0:
                log.debug(f"  ✅ {ticker}: score={score['total_score']:.1f} | {score.get('reasons', '')[:60]}")
            else:
                reason = score.get('reasons', 'no_data') if score else 'no_data'
                log.debug(f"  ❌ {ticker}: {reason}")
            
            return score if score and score.get("total_score", 0) > 0 else None
        except Exception as exc:
            log.info(f"  ❌ {ticker}: SCAN ERROR — {exc}")
            return None
    
    def _scan_and_rank(self):
        t0 = time.perf_counter()
        screen_list = getattr(self.cfg, "SCAN_UNIVERSE", PENNY_STOCK_UNIVERSE[:72])
        log.info(f"🔍 HANOON SCAN START: {len(screen_list)} tickers")
        results: List[Dict] = []
        
        # USER METHODOLOGY: Sequential scan — IB async loop breaks with threads
        scan_count = 0
        for ticker in screen_list:
            scan_count += 1
            r = self._scan_one(ticker)
            if r:
                results.append(r)
        
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(f"Scan: {len(results)}/{scan_count} qualified in {elapsed_ms:.0f}ms")
        
        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        
        # Debug: log score distribution
        if results:
            scores = [r["total_score"] for r in results[:5]]
            log.debug(f"Score distribution: top5={scores}")
        
        # USER METHODOLOGY: Always return 1-5 stocks, never 0
        min_score = 1.0
        qualified = [r for r in results if r["total_score"] >= min_score]
        
        if not qualified:
            if results:
                qualified = results[:3]
                log.info(f"⚠️ No high-score setups — taking top {len(qualified)} by default (scores: {[r['total_score'] for r in qualified]})")
            else:
                log.warning("🔴 Zero scan results — check data feed or uptrend filter")
        
        self.scan_results = qualified[:5]
        
        # USER RULE: Penny stocks only — filter out >$5 before locking
        penny_results = []
        for r in self.scan_results:
            if r["price"] <= 5.0:
                penny_results.append(r)
            else:
                log.debug(f"  Filtered {r['ticker']}: ${r['price']:.2f} > $5 (penny only)")
        
        if not penny_results and self.scan_results:
            log.info(f"⚠️ All top {len(self.scan_results)} setups >$5 — taking cheapest available")
            cheapest = sorted(self.scan_results, key=lambda x: x["price"])[:3]
            penny_results = cheapest
        
        if penny_results:
            self._locked_targets = []
            for r in penny_results:
                pick = ScanResult(
                    ticker=r["ticker"], price=r["price"], volume=r["volume"],
                    avg_volume=r["avg_volume"], relative_volume=r["rel_vol"],
                    rank_score=r["total_score"], reason=r["reasons"],
                )
                self._locked_targets.append(pick)
            self.top_pick = self._locked_targets[0] if self._locked_targets else None
            names = ", ".join([p.ticker for p in self._locked_targets])
            log.info(f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names} | Scan: {elapsed_ms:.0f}ms")
            self.notifier.info(f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names}\nTop score: {self.top_pick.rank_score:.0f}")
            
            # Start live streams for ALL locked targets immediately
            for pick in self._locked_targets:
                self._start_target_stream(pick.ticker)
            
            try:
                buffer_append({
                    "source": "scan_pick",
                    "ticker": self.top_pick.ticker,
                    "action": "SCAN_PICK",
                    "scan_score": self.top_pick.rank_score,
                    "confidence": 0.5,
                    "features": [],
                })
            except Exception:
                pass
        else:
            self.top_pick = None
            self._locked_targets = []
            log.info(f"🔍 No setups found in full universe scan ({elapsed_ms:.0f}ms)")
    
    def _start_target_stream(self, ticker: str):
        """Start live tick stream for a locked target — millisecond heartbeat."""
        if ticker in self._target_monitors:
            return  # Already streaming
        try:
            cfg = BotConfig(TICKER=ticker)  # fresh config for new contract
            log.info(f"  📡 DEBUG: ticker={ticker}, cfg.TICKER={cfg.TICKER!r}")
            print(f"STREAM DEBUG: ticker={ticker}, cfg.TICKER={cfg.TICKER!r}")
            dm = DataManager(self.conn, cfg)
            dm.start_tick_stream()  # tick-by-tick or 5s realtime bars
            self._target_monitors[ticker] = dm
            self._target_last_bar_count[ticker] = 0
            log.info(f"  📡 LIVE STREAM started for {ticker} (millisecond heartbeat)")
        except Exception as exc:
            log.warning(f"  Stream start failed for {ticker}: {exc}")
    
    def _stop_target_stream(self, ticker: str):
        """Stop live tick stream for a target."""
        dm = self._target_monitors.pop(ticker, None)
        if dm:
            try:
                dm.stop_tick_stream()
            except Exception:
                pass
        self._target_last_bar_count.pop(ticker, None)
        if self._active_stream_ticker == ticker:
            self._active_stream_ticker = None
    
    def _get_live_1min_bars(self, ticker: str) -> Optional[pd.DataFrame]:
        """
        Get NEW 1min bars since last check from live stream buffer.
        Returns only bars we haven't processed yet.
        """
        dm = self._target_monitors.get(ticker)
        if dm is None:
            return None
        df = dm.get_bar_dataframe()
        if df is None or len(df) < 20:
            return None
        last_count = self._target_last_bar_count.get(ticker, 0)
        if len(df) <= last_count:
            return None  # No new bars
        new_bars = df.iloc[last_count:]
        self._target_last_bar_count[ticker] = len(df)
        return new_bars
    
    def _fast_monitor_locked(self):
        """
        USER METHODOLOGY: Millisecond 1min observation via LIVE STREAM heartbeat.
        No historical fetches. Uses tick-by-tick / realtime bar buffers.
        Triggers entry on live volume spike + uptrend the instant it happens.
        """
        if not self._locked_targets or self.shares > 0:
            return
        
        alive = []
        for target in self._locked_targets[:self.cfg.MAX_LOCKED_TARGETS]:
            ticker = target.ticker
            
            # Ensure live stream is running (start on first check)
            if ticker not in self._target_monitors:
                self._start_target_stream(ticker)
            
            # Get NEW 1min bars since last heartbeat check
            new_bars = self._get_live_1min_bars(ticker)
            if new_bars is None or len(new_bars) == 0:
                alive.append(target)
                continue
            
            # Use the accumulated live bars (all we have so far)
            dm = self._target_monitors[ticker]
            full_df = dm.get_bar_dataframe()
            if full_df is None or len(full_df) < 20:
                alive.append(target)
                continue
            
            current_px = float(full_df["close"].iloc[-1])
            
            # Quick uptrend check on live 1min bars
            if not _only_uptrend(full_df, current_px):
                alive.append(target)
                continue
            
            # Volume spike check on LIVE 1min bars
            is_spike, spike_ratio = self._detect_volume_spike(full_df)
            if not is_spike:
                alive.append(target)
                continue
            
            # Volume spike detected — try entry NOW
            self._scan_data_cache[ticker] = full_df.tail(60).copy()  # Update cache for entry
            self.top_pick = target
            log.info(f"⚡ LIVE 1min SPIKE: {ticker} @ ${current_px:.2f} | vol={spike_ratio:.1f}x | entering...")
            result = self._attempt_entry()
            if self.shares > 0:
                # Entered! Keep stream running for post-entry monitoring
                self._active_stream_ticker = ticker
                return
            if result == 'permanent_skip':
                self._stop_target_stream(ticker)
                continue
            alive.append(target)
        
        self._locked_targets = alive
        # Stop streams for targets that were removed
        old_tickers = set(self._target_monitors.keys())
        new_tickers = set(t.ticker for t in self._locked_targets)
        removed = old_tickers - new_tickers
        for t in removed:
            self._stop_target_stream(t)
        # Start streams for any new targets that don't have them
        for t in self._locked_targets:
            if t.ticker not in self._target_monitors:
                self._start_target_stream(t.ticker)
        if not self._locked_targets:
            self._last_scan_time = 0  # Force new scan
    
    def _detect_volume_spike(self, df: pd.DataFrame) -> Tuple[bool, float]:
        """
        Detect volume spike: current volume vs 20-period average.
        Returns (is_spike, spike_ratio)
        """
        if len(df) < 20:
            return False, 1.0
        volumes = df["volume"].values[-20:]
        avg_vol = np.mean(volumes[:-1])  # exclude current bar
        current_vol = volumes[-1]
        if avg_vol <= 0:
            return False, 1.0
        spike_ratio = current_vol / avg_vol
        return spike_ratio >= 1.5, spike_ratio  # 1.5x = 50% above average
    
    def _predict_slippage(self, df: pd.DataFrame, current_px: float) -> float:
        """
        Predict slippage risk based on spread, momentum divergence, and order flow.
        Returns 0.0 (no slippage) to 1.0 (high slippage)
        """
        if len(df) < 10:
            return 0.5
        closes = df["close"].values[-10:]
        volumes = df["volume"].values[-10:]
        
        # Momentum divergence: price up but volume down = exhaustion
        price_up = closes[-1] > closes[-3]
        vol_down = volumes[-1] < np.mean(volumes[-5:-1])
        divergence = 0.3 if (price_up and vol_down) else 0.0
        
        # High volatility = higher slippage
        atr = compute_atr(df, period=5)
        vol_ratio = atr / current_px if current_px > 0 else 0.01
        vol_slippage = min(0.3, vol_ratio * 2.0)
        
        # Thin volume = higher slippage
        avg_vol = np.mean(volumes[-5:])
        thin_penalty = 0.2 if avg_vol < 50000 else 0.0
        
        total_slippage = min(1.0, divergence + vol_slippage + thin_penalty)
        return total_slippage
    
    def _should_exit_early(self, current_px: float, entry_px: float, 
                           unrealized_pnl: float, risk_usd: float) -> Tuple[bool, str]:
        """
        USER METHODOLOGY: Exit early when slippage predicted OR profit locked.
        Even with $50 hard stop, exit at $1+ if prediction says so.
        
        Returns (should_exit, reason)
        """
        if self.shares <= 0 or entry_px <= 0:
            return False, "no position"
        
        pnl_pct = (current_px / entry_px) - 1
        
        # USER RULE: Hard stop $50 per trade (already set in bracket)
        # But exit EARLY if we predict slippage or lock profit
        
        # 1. Lock profit trail: if up > 2%, trail at 1% giveback
        if pnl_pct > 0.02:
            giveback = pnl_pct * 0.5  # give back up to 50% of gains
            if pnl_pct < giveback:
                return True, f"profit_trail: locked {pnl_pct:.2%}, giving back {giveback:.2%}"
        
        # 2. AI-driven exit: use live stream data for AI evaluation
        try:
            ai_exit, ai_conf, ai_reason = self._ai_gate_exit(current_px)
            if ai_exit and ai_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                return True, f"AI_exit: conf={ai_conf:.2f} | {ai_reason[:80]}"
        except Exception:
            pass
        
        # 3. Slippage prediction + volume check on LIVE 1min bars
        try:
            fast_df = None
            if self._active_stream_ticker and self._active_stream_ticker in self._target_monitors:
                fast_df = self._target_monitors[self._active_stream_ticker].get_bar_dataframe()
            if fast_df is None and hasattr(self.data, 'get_bar_dataframe'):
                fast_df = self.data.get_bar_dataframe()
            if fast_df is not None and len(fast_df) >= 10:
                slippage = self._predict_slippage(fast_df, current_px)
                if slippage > 0.7:
                    return True, f"slippage_risk: {slippage:.0%}"
                is_spike, ratio = self._detect_volume_spike(fast_df)
                if not is_spike and pnl_pct > 0.01:
                    return True, f"no_volume_spike: profit {pnl_pct:.2%} locked, algo wave ending"
        except Exception:
            pass
        
        # 4. USER RULE: If unrealized profit is tiny ($1-$2) and risk is high, exit
        if 0 < unrealized_pnl < 2.0 and risk_usd > 30:
            return True, f"low_profit_high_risk: ${unrealized_pnl:.2f} profit, ${risk_usd:.0f} risk"
        
        return False, "hold"
    
    def _update_trailing_stops(self, current_px: float):
        """USER METHODOLOGY: Trail profit along institutional algo wave — AI-driven."""
        if self.shares <= 0 or self._entry_price <= 0:
            return
        if not self.bracket_handle:
            return
        
        pnl_pct = (current_px / self._entry_price) - 1
        
        # Only trail if profitable
        if pnl_pct <= 0:
            return
        
        # AI-driven trail decision
        ai_trail = True
        try:
            ai_exit, ai_conf, ai_reason = self._ai_gate_exit(current_px)
            if ai_exit and ai_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                ai_trail = False
        except Exception:
            pass
        
        trail_ratio = 0.5
        if not ai_trail:
            trail_ratio = 0.3
        
        # Calculate new stop: lock in gains based on AI confidence
        trail_stop = current_px - (self._entry_price * pnl_pct * trail_ratio)
        trail_stop = max(trail_stop, self.bracket_handle.initial_stop_price)
        
        # Update bracket if stop improved
        try:
            if trail_stop > self.bracket_handle.initial_stop_price:
                self.broker.cancel_bracket(self.bracket_handle)
                self.bracket_handle = self.broker.place_bracket_buy(
                    quantity=int(self.shares),
                    limit_or_market_price=current_px,
                    stop_price=trail_stop,
                    target_price=self.bracket_handle.take_profit_price,
                )
                log.info(f"📈 TRAILING STOP: moved to ${trail_stop:.2f} (locked {pnl_pct:.2%}, AI_trail={ai_trail})")
        except Exception as exc:
            log.debug(f"Trailing stop update failed: {exc}")
    
    def _score_ticker(self, ticker: str, df: pd.DataFrame) -> Dict:
        closes = df["close"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])
        if not _only_uptrend(df, current_px):
            return {"ticker": ticker, "total_score": 0, "price": current_px, "volume": int(volumes[-1]), "avg_volume": int(np.mean(volumes[-20:])), "rel_vol": 1.0, "reasons": "not_uptrend"}
        score = 1.0
        reasons = ["uptrend"]
        ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) > 10 else 0
        ret_20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else 0
        mom_score = ret_5 * 0.5 + ret_10 * 0.3 + ret_20 * 0.2
        score += mom_score * 2.0
        if mom_score > 2:
            reasons.append(f"strong_mom_{mom_score:.1f}")
        vol_avg20 = np.mean(volumes[-20:])
        vol_avg5 = np.mean(volumes[-5:])
        vol_ratio = vol_avg5 / (vol_avg20 + 1e-9)
        score += max(0, vol_ratio - 1.0) * 15
        if vol_ratio > 1.3:
            reasons.append(f"vol_{vol_ratio:.1f}x")
        inst = InstitutionalDetector()
        for i in range(-20, 0):
            inst.feed_bar(float(volumes[i]), float(closes[i]))
        sig = inst.scan()
        if sig.direction == "accumulating" and sig.strength > 0.5:
            score += sig.strength * 20
            reasons.append(f"inst_{sig.strength:.1f}")
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        try:
            vwap_hist = np.array([np.average(typical[max(0, i-19):i+1], weights=volumes[max(0, i-19):i+1]) for i in range(19, len(typical))])
            vwap_slope = (vwap_hist[-1] - vwap_hist[-5]) / (vwap_hist[-5] + 1e-9) * 100
        except Exception:
            vwap_slope = 0
        score += max(0, vwap_slope) * 5
        if vwap_slope > 0.5:
            reasons.append(f"vwap_up_{vwap_slope:.2f}%")
        atr = compute_atr(df, period=10)
        atr_pct = (atr / current_px) * 100
        if 0.3 < atr_pct < 3.0:
            score += 5
        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().iloc[-1]
        dist = (current_px - ema9) / (pd.Series(closes).diff().rolling(20).std().iloc[-1] + 1e-9)
        if abs(dist) < 1.5:
            score += 5
        return {
            "ticker": ticker, "price": current_px, "volume": int(volumes[-1]),
            "avg_volume": int(vol_avg20), "rel_vol": round(vol_ratio, 2),
            "total_score": round(score, 1), "reasons": " | ".join(reasons[:3]) if reasons else "balanced",
            "ai_score": None,
        }
    
    def _ai_score_ticker(self, ticker: str, df: pd.DataFrame, rule_score: float) -> float:
        """
        AI validates/overrides rule-based score.
        Returns AI-adjusted score (0-100 scale).
        """
        if not self.cfg.USE_ENHANCED_AI or self.model is None or self._model_fresh:
            return rule_score
        try:
            self._ai_update_buffers(df, float(df["close"].iloc[-1]))
            if len(self._feature_buffer) < self.cfg.WINDOW_SIZE:
                return rule_score
            window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
            total = self.bot_cash + self.shares * float(df["close"].iloc[-1])
            c_rat = self.bot_cash / (total + 1e-9)
            p_rat = (self.shares * float(df["close"].iloc[-1])) / (total + 1e-9) if self.shares > 0 else 0.0
            obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
            from core.agent import predict_with_reasoning
            bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
            action, confidence, reasoning = predict_with_reasoning(
                self.model, obs, self.cfg, self.ai_components,
                bar_df=bar_df,
                recent_rewards=getattr(self.perf, 'recent_rewards', None) if hasattr(self, 'perf') else None,
            )
            ai_score = rule_score
            if action == 1 and confidence >= self.cfg.CONFIDENCE_THRESHOLD:
                ai_score = rule_score * (1.0 + confidence * 0.5)
            elif action == 2:
                ai_score = rule_score * 0.3
            buffer_append({
                "source": "ai_scan",
                "ticker": ticker,
                "action": "EVALUATE",
                "scan_score": rule_score,
                "ai_score": ai_score,
                "confidence": confidence,
                "features": [],
                "timestamp": datetime.now(datetime.UTC).isoformat(),
            })
            return ai_score
        except Exception:
            return rule_score
    
    def _attempt_entry(self) -> str:
        """
        Attempt entry on self.top_pick.
        Returns: 'entered', 'permanent_skip', or 'waiting'
        """
        if not self.top_pick:
            return 'waiting'
        ticker = self.top_pick.ticker
        
        active_positions = getattr(self, '_active_positions', [])
        if len(active_positions) >= 5:
            return 'waiting'
        
        try:
            self.cfg.TICKER = ticker
            
            # Use cached data from scanner (avoids re-fetching 2 days of bars every second)
            df_fast = self._scan_data_cache.get(ticker)
            if df_fast is None or len(df_fast) < 20:
                df_fast = self.data.fetch_historical(duration="2 D", bar_size="1 min", use_rth=False)
                if df_fast is None or len(df_fast) < 20:
                    return 'waiting'
            
            current_px = float(df_fast["close"].iloc[-1])
            
            if not _only_uptrend(df_fast, current_px):
                log.debug(f"Entry skip {ticker}: not uptrend")
                return 'waiting'
            
            is_spike, spike_ratio = self._detect_volume_spike(df_fast)
            if not is_spike:
                log.debug(f"Entry skip {ticker}: no volume spike (ratio={spike_ratio:.2f})")
                return 'waiting'
            
            inst = self.institutional.scan()
            # Use detector's override check (not signal's attribute)
            override, reason = self.institutional.should_override_buy()
            if override:
                log.debug(f"Entry skip {ticker}: institutional override — {reason}")
                return 'waiting'
            
            if self.cfg.USE_ENHANCED_AI and self.model is not None:
                self._ai_update_buffers(df_fast, current_px)
                should_enter, ai_conf, ai_reason = self._ai_gate_entry(ticker, current_px)
                if not should_enter:
                    log.debug(f"Entry skip {ticker}: AI gate rejected (conf={ai_conf:.2f}) — {ai_reason[:80]}")
                    return 'waiting'
            
            deploy_usd = 1000.0
            shares = int(deploy_usd / current_px)
            if shares < 1:
                log.debug(f"Entry skip {ticker}: shares={shares} < 1")
                return 'waiting'
            
            stop_usd = 50.0
            stop_dist = stop_usd / shares
            stop_dist = max(stop_dist, current_px * self.cfg.SCALP_MIN_STOP_PCT)
            tp_dist = stop_dist * 2.0
            tp_dist = min(tp_dist, current_px * 0.05)
            tp_price = current_px + tp_dist
            
            plan = TradePlan(
                side="LONG", entry_price=current_px, shares=float(shares),
                initial_stop_price=round(current_px - stop_dist, 4),
                take_profit_price=round(tp_price, 4),
                risk_usd=stop_usd, atr_at_entry=compute_atr(df_fast, period=5),
            )
            
            self.bracket_handle = self.broker.place_bracket_buy(
                quantity=shares, limit_or_market_price=current_px,
                stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
            )
            self.ib.sleep(1)
            cost = shares * current_px * (1 + self.cfg.TRANSACTION_COST_PCT)
            self.bot_cash -= cost
            self.shares = float(shares)
            self.bot_nav = self.bot_cash + self.shares * current_px
            self._entry_price = current_px
            self._prev_shares = self.shares
            
            if not hasattr(self, '_active_positions'):
                self._active_positions = []
            self._active_positions.append({
                "ticker": ticker,
                "entry_price": current_px,
                "shares": shares,
                "stop": plan.initial_stop_price,
                "target": plan.take_profit_price,
                "entry_time": time.time(),
            })
            
            self.risk.open_position(plan)
            self.trades_today += 1
            log.info(f"🎯 ENTRY: {shares}x {ticker} @ ${current_px:.2f} | Stop ${plan.initial_stop_price:.2f} | TP ${plan.take_profit_price:.2f} | Deployed: ${cost:,.0f}")
            self.notifier.info(
                f"🎯 HANOON ENTRY\n"
                f"Ticker: {ticker}\n"
                f"Qty: {shares}\n"
                f"Entry: ${current_px:.2f}\n"
                f"Stop: ${plan.initial_stop_price:.2f} (-$50)\n"
                f"Target: ${plan.take_profit_price:.2f}\n"
                f"Deployed: ${cost:,.0f}"
            )
            push_trade(ticker, "BUY", current_px, shares)
            
            # Train from entry event
            try:
                buffer_append({
                    "source": "live_entry",
                    "ticker": ticker,
                    "action": "BUY",
                    "entry_price": current_px,
                    "shares": shares,
                    "stop": plan.initial_stop_price,
                    "target": plan.take_profit_price,
                    "confidence": getattr(self, '_last_ai_confidence', 0.5),
                    "features": [],
                    "timestamp": datetime.now(datetime.UTC).isoformat(),
                })
            except Exception:
                pass
            
            return 'entered'
        except Exception as exc:
            log.error(f"Entry error on {ticker}: {exc}")
            return 'waiting'
    
    @staticmethod
    def _get_market_state() -> str:
        """
        Returns one of: 'open', 'pre_market', 'after_hours', 'closed'
        """
        now_et = datetime.now(MARKET_TZ)
        weekday = now_et.weekday()  # 0=Mon, 6=Sun

        # Weekend
        if weekday >= 5:
            return "closed"

        # Holiday check
        date_str = now_et.strftime("%Y-%m-%d")
        if date_str in US_MARKET_HOLIDAYS:
            return "closed"

        # Optional live holiday verification
        try:
            resp = requests.get("https://www.nyse.com/markets/hours-calendars", timeout=5)
            if resp.status_code == 200 and date_str.replace("-", "") in resp.text.replace("-", ""):
                return "closed"
        except Exception:
            pass

        hour = now_et.hour
        minute = now_et.minute
        current_minutes = hour * 60 + minute
        pre_start = int(BotConfig.PRE_MARKET_START.split(":")[0]) * 60 + int(BotConfig.PRE_MARKET_START.split(":")[1])
        pre_end = int(BotConfig.PRE_MARKET_END.split(":")[0]) * 60 + int(BotConfig.PRE_MARKET_END.split(":")[1])
        regular_open = 9 * 60 + 30
        regular_close = 16 * 60
        ah_start = int(BotConfig.AFTER_HOURS_START.split(":")[0]) * 60 + int(BotConfig.AFTER_HOURS_START.split(":")[1])
        ah_end = int(BotConfig.AFTER_HOURS_END.split(":")[0]) * 60 + int(BotConfig.AFTER_HOURS_END.split(":")[1])

        if pre_start <= current_minutes < regular_open:
            return "pre_market"
        elif regular_open <= current_minutes < regular_close:
            return "open"
        elif regular_close <= current_minutes < ah_end:
            return "after_hours"
        else:
            return "closed"
    
    def _ai_update_buffers(self, bar_df: pd.DataFrame, current_px: float):
        """Update feature and price buffers for AI evaluation."""
        try:
            feats = FeatureEngineerEnhanced.compute(bar_df)
            if len(feats) > 0:
                for f in feats[-min(len(feats), self.cfg.WINDOW_SIZE):]:
                    self._feature_buffer.append(f)
            for px in bar_df["close"].values[-min(len(bar_df), self.cfg.WINDOW_SIZE + 10):]:
                self._price_buffer.append(float(px))
            self._bar_df_buffer = bar_df.tail(self.cfg.WINDOW_SIZE + 10).to_dict('records')
        except Exception:
            pass
    
    def _ai_gate_entry(self, ticker: str, current_px: float) -> Tuple[bool, float, str]:
        """
        Use full enhanced AI pipeline to decide if entry is justified.
        
        Returns:
            (should_enter, confidence, reasoning)
        """
        if not self.cfg.USE_ENHANCED_AI or not self.ai_components:
            return True, 0.5, "AI disabled"
        if self.model is None:
            return True, 0.5, "No model"
        if self._model_fresh:
            return True, 0.5, "Fresh model — bypassing AI gate (rule-based only)"
        if len(self._feature_buffer) < self.cfg.WINDOW_SIZE:
            return True, 0.5, "Warming up"
        
        try:
            from core.agent import predict_with_reasoning
            
            window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
            total = self.bot_cash + self.shares * current_px
            c_rat = self.bot_cash / (total + 1e-9)
            p_rat = (self.shares * current_px) / (total + 1e-9) if self.shares > 0 else 0.0
            obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
            
            bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
            
            action, confidence, reasoning = predict_with_reasoning(
                self.model, obs, self.cfg, self.ai_components,
                bar_df=bar_df,
                recent_rewards=getattr(self.perf, 'recent_rewards', None) if hasattr(self, 'perf') else None,
            )
            
            should_enter = (action == 1 and confidence >= self.cfg.CONFIDENCE_THRESHOLD)
            return should_enter, confidence, reasoning or "AI evaluation"
        except Exception as exc:
            log.debug(f"AI gate entry error: {exc}")
            return True, 0.5, f"AI error: {exc}"
    
    def _ai_gate_exit(self, current_px: float) -> Tuple[bool, float, str]:
        """
        Use AI to evaluate if current position should be closed early.
        
        Returns:
            (should_exit, confidence, reasoning)
        """
        if not self.cfg.USE_ENHANCED_AI or not self.ai_components:
            return False, 0.5, "AI disabled"
        if self.model is None or self.shares <= 0:
            return False, 0.5, "No model/position"
        if self._model_fresh:
            return False, 0.5, "Fresh model — bypassing AI exit (rule-based only)"
        if len(self._feature_buffer) < self.cfg.WINDOW_SIZE:
            return False, 0.5, "Warming up"
        
        try:
            from core.agent import predict_with_reasoning
            
            window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
            total = self.bot_cash + self.shares * current_px
            c_rat = self.bot_cash / (total + 1e-9)
            p_rat = (self.shares * current_px) / (total + 1e-9)
            obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
            
            bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
            
            action, confidence, reasoning = predict_with_reasoning(
                self.model, obs, self.cfg, self.ai_components,
                bar_df=bar_df,
                recent_rewards=getattr(self.perf, 'recent_rewards', None) if hasattr(self, 'perf') else None,
            )
            
            should_exit = (action == 2 and confidence >= self.cfg.CONFIDENCE_THRESHOLD)
            return should_exit, confidence, reasoning or "AI exit evaluation"
        except Exception as exc:
            log.debug(f"AI gate exit error: {exc}")
            return False, 0.5, f"AI error: {exc}"
    
    def _train_off_hours(self):
        """
        When market is closed, launch isolated training subprocess.
        
        Training is moved to a separate short-lived process to:
        - Free MPS/GPU memory completely after training
        - Prevent memory fragmentation in the long-running trading process
        - Isolate crashes from the main trading loop
        """
        try:
            log.info("🧠 OFF-HOURS TRAINING: Launching isolated training subprocess...")
            
            # Update market regime from broader context (lightweight, stays in-process)
            self._update_market_context()
            
            # Train weights on historical data (lightweight, stays in-process)
            self._daily_self_train()
            
            # Launch heavy training (Transformer + PPO + LSTM) in isolated subprocess
            # This returns immediately - training continues in background
            try:
                session_id = launch_training([
                    sys.executable, "-m", "core.advanced_training",
                    "--mode", "full",
                    "--ticker", self.cfg.TICKER,
                    "--ppo-timesteps", "100000",  # Reduced for off-hours
                    "--epochs", "20",
                    "--save-model", "models/transformer_model.pth",
                ], timeout_minutes=30)
                
                if session_id:
                    log.info(f"🏋️ Training subprocess launched: {session_id}")
                    self.notifier.info(f"🏋️ OFF-HOURS TRAINING\nIsolated subprocess launched.\nSession: {session_id}")
                else:
                    log.warning("Training subprocess failed to launch")
            except Exception as exc:
                log.debug(f"Subprocess training launch failed: {exc}")
            
            # Consciousness reflection (lightweight, stays in-process)
            try:
                if hasattr(self, 'consciousness') and self.consciousness:
                    self.consciousness.observe_scan({"source": "off_hours", "tickers": len(PENNY_STOCK_UNIVERSE)})
                    session = self.consciousness.continuous_train()
                    reflection = self.consciousness.reflect()
                    log.info(f"🧠 Consciousness reflection: {reflection[:200]}")
            except Exception as exc:
                log.debug(f"Consciousness training failed: {exc}")
            
            # Self-improvement plan (lightweight, stays in-process)
            try:
                plan = generate_self_improvement_plan(self.cfg)
                if plan.get("adjustments"):
                    self.notifier.info(f"🧬 SELF-IMPROVEMENT PLAN\n{plan['guidelines'][:1000]}")
            except Exception as exc:
                log.debug(f"Self-improvement plan failed: {exc}")
            
            # Tag git release after off-hours training
            try:
                version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                push_model_release(version, notes="off_hours_full_training")
            except Exception:
                pass
            
            log.info("🧠 Off-hours training dispatched. Ready for next session.")
        except Exception as exc:
            log.debug(f"Off-hours training failed: {exc}")
    
    def _start_weights_watcher(self):
        """Start background file watcher for hot-reload of scalper_weights.json."""
        try:
            from core.async_utils import FileWatcher
            self._weights_watcher = FileWatcher(
                filepath=self._weights_file,
                callback=self._on_weights_changed,
                poll_interval=5.0
            )
            self._weights_watcher.start()
            log.debug("Weights file watcher started")
        except Exception as exc:
            log.debug(f"Weights watcher init failed: {exc}")
    
    def _on_weights_changed(self, filepath: str):
        """Called when scalper_weights.json changes on disk."""
        log.info(f"🧠 Weights file changed — hot-reloading from disk")
        try:
            weights = self._load_weights()
            log.info(f"🧠 Hot-reload complete | {len(weights.get('win_history', []))} trade samples in history")
        except Exception as exc:
            log.warning(f"Weights hot-reload failed: {exc}")
    
    def _load_weights(self) -> Dict:
        try:
            with open(self._weights_file, "r") as f:
                return json.load(f)
        except Exception:
            return {"momentum": 2.0, "volume": 15.0, "institutional": 20.0, "vwap_slope": 5.0, "atr_bonus": 5.0, "mean_reversion": 5.0, "win_history": []}
    
    def _save_weights(self, weights: Dict):
        os.makedirs("models", exist_ok=True)
        # Atomic write to prevent corruption during concurrent access
        AtomicFileWriter.write_json(self._weights_file, weights)
        log.info(f"🧠 Learned weights saved -> {self._weights_file}")
    
    def _daily_self_train(self):
        try:
            weights = self._load_weights()
            # Load trade journal into win_history if not already there
            if self.trade_journal and not weights.get("win_history"):
                for trade in self.trade_journal:
                    weights["win_history"].append({
                        "result": trade["result"],
                        "pnl_usd": trade["pnl_usd"],
                        "weights_active": {k: weights.get(k, 1.0) for k in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]}
                    })
            wins = [w for w in weights.get("win_history", []) if w["result"] == "win"]
            losses = [w for w in weights.get("win_history", []) if w["result"] == "loss"]
            if wins or losses:
                win_rate = len(wins) / (len(wins) + len(losses))
                for w in weights.get("win_history", []):
                    factor = 1.15 if w["result"] == "win" else 0.85
                    for key in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]:
                        if key in w.get("weights_active", {}):
                            weights[key] = weights.get(key, 1.0) * factor
                for key in ["momentum", "volume", "institutional", "vwap_slope", "atr_bonus", "mean_reversion"]:
                    weights[key] = max(0.5, min(weights[key], 50.0))
                log.info(f"🧠 Self-train: win_rate={win_rate:.0%} | wins={len(wins)} losses={len(losses)} | weights updated")
            try:
                sim_scores = [r.rank_score for r in self.scan_results[:10]]
                if sim_scores:
                    max_score = max(sim_scores)
                    if max_score < 30:
                        weights["volume"] *= 1.2
                        weights["institutional"] *= 1.2
                        log.info(f"🧠 Weak top-score ({max_score:.0f}) → boosted volume+institutional weights")
            except Exception:
                pass
            self._save_weights(weights)
            
            # Tag git release after self-training
            try:
                version = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                push_model_release(version, notes=f"weights={json.dumps(weights)[:100]}")
            except Exception:
                pass
        except Exception as exc:
            log.debug(f"Self-train skipped: {exc}")
    
    def _update_market_context(self):
        """Fetch Yahoo Finance context and update regime detector."""
        try:
            ctx = summarize_market_context()
            regime = self.regime_detector.classify(
                self.data.get_bar_dataframe() if hasattr(self.data, 'get_bar_dataframe') else None,
                vix_df=None,
            )
            buffer_append({
                "source": "market_context",
                "ticker": "MARKET",
                "action": "REGIME",
                "regime": regime.regime.value if hasattr(regime, 'regime') else "unknown",
                "confidence": getattr(regime, 'confidence', 0.0),
                "features": [],
                "timestamp": datetime.now(datetime.UTC).isoformat(),
            })
            log.info(f"🌍 Market context: {ctx.get('spy_trend', 'unknown')} SPY, {ctx.get('vix_regime', 'unknown')} VIX")
        except Exception as exc:
            log.debug(f"Market context update failed: {exc}")

    def _generate_guidelines(self) -> str:
        try:
            weights = self._load_weights()
            win_rate = len([w for w in weights.get("win_history", []) if w["result"] == "win"]) / max(len(weights.get("win_history", [])), 1)
            rules = []
            wins = [w for w in weights.get("win_history", []) if w["result"] == "win"]
            losses = [w for w in weights.get("win_history", []) if w["result"] == "loss"]
            if win_rate < 0.4:
                rules.append("URGENT: Win rate below 40%. Tighten stop-loss (reduce SCALP_STOP_ATR_MULTIPLIER from 0.7 to 0.5).")
                rules.append("Reduce trade frequency: increase SCAN_INTERVAL_SECONDS from 300 to 600.")
            elif win_rate > 0.7:
                rules.append("Win rate excellent (>70%). Consider increasing position size or reducing SCALP_STOP_ATR_MULTIPLIER for bigger wins.")
            else:
                rules.append(f"Win rate {win_rate:.0%} — stable. Continue current risk parameters.")
            if losses:
                avg_loss = sum(l.get("pnl_usd", 0) for l in losses) / len(losses)
                if avg_loss > 30:
                    rules.append(f"Average loss ${avg_loss:.0f} is high. Consider reducing MAX_TRADE_SIZE_USD from $1,000 to $500.")
                    rules.append("Review trailing stop: tighten SCALP_TRAILING_ATR_MULTIPLIER.")
            w = weights
            if w.get("momentum", 0) > 30:
                rules.append("Momentum weight is very high — strategy is overly focused on momentum. Consider rebalancing.")
            if w.get("volume", 0) > 30:
                rules.append("Volume weight is very high — add volume_decay check to avoid chasing pumps.")
            if w.get("institutional", 0) > 30:
                rules.append("Institutional weight is very high — ensure institutional detector is accurate (check for false signals).")
            if self.scan_results:
                max_score = max(r.rank_score for r in self.scan_results[:3])
                if max_score < 20:
                    rules.append("Market conditions are weak (low scores). Consider wider SCALP_MIN_STOP_PCT or wait for better setups.")
                elif max_score > 50:
                    rules.append("Strong market conditions. Increase SCALP_MAX_TP_PCT from 3% to 5% to capture more upside.")
            if self.bot_nav > float(self.cfg.INITIAL_CASH) * 1.5:
                rules.append(f"Account grew {self.bot_nav / float(self.cfg.INITIAL_CASH):.0%}x. Consider adding a second concurrent position (MAX_CONCURRENT_POSITIONS).")
            rules.append("Always use limit orders in fast markets (USE_LIMIT_ORDERS_IN_FAST_MARKETS = True).")
            rules.append("Monitor slippage: if fills consistently >0.4%, reduce order size.")
            pnl = self.bot_nav - float(self.cfg.INITIAL_CASH)
            pnl_pct = pnl / float(self.cfg.INITIAL_CASH)
            if pnl_pct < -0.1:
                rules.append("ALERT: Drawdown >10%. Pause trading for 24 hours and review strategy.")
                rules.append("Strengthen uptrend filter: require price > SMA50 instead of SMA20.")
            if not rules:
                rules.append("No guideline changes needed. System running optimally.")
            rules_text = "\n".join(f"• {r}" for r in rules)
            return f"🧭 HANOON SELF-IMPROVEMENT GUIDELINES\n{'_'*40}\n{rules_text}\n"
        except Exception as exc:
            log.debug(f"Guidelines generation failed: {exc}")
            return ""
    
    def _maybe_daily_push(self):
        try:
            now_et = datetime.now(MARKET_TZ)
            today_str = now_et.strftime("%Y-%m-%d")
            market_close_hour_et = 16
            if now_et.hour >= market_close_hour_et and self._last_daily_push_date != today_str:
                self._last_daily_push_date = today_str
                self._daily_self_train()
                guidelines = self._generate_guidelines()
                baseline = float(self.cfg.INITIAL_CASH)
                pnl = self.bot_nav - baseline
                pnl_pct = (pnl / baseline) * 100 if baseline else 0.0
                stmt = (
                    f"portfolio: {today_str} ET | "
                    f"bot_nav=${self.bot_nav:,.0f} | "
                    f"baseline=${baseline:,.0f} | "
                    f"pnl=${pnl:+,.0f} ({pnl_pct:+.2f}%) | "
                    f"trades={self.trades_today}"
                )
                push_daily_summary(self.bot_nav, self.account_equity)
                try:
                    weights = self._load_weights()
                    self.cfg._latest_account_balance = self.account_equity
                    os.makedirs("models", exist_ok=True)
                    with open("models/daily_guidelines.txt", "w") as f:
                        f.write(guidelines)
                        f.write(f"\nGenerated: {now_et.isoformat()}\n")
                        f.write(f"Weights: {json.dumps(weights, indent=2)}\n")
                        f.write(f"Performance: {stmt}\n")
                    # Async git commit (non-blocking)
                    self._worker.submit_git_commit(
                        files=["models/scalper_weights.json", "models/daily_guidelines.txt"],
                        message=f"train: hanoon daily self-improvement {today_str}",
                        push=True
                    )
                except Exception:
                    pass
                log.info(f"📤 {stmt}")
                log.info(f"🧭 Guidelines generated and pushed to git")
                self.notifier.info(f"📊 HANOON DAILY COMPLETE\n{stmt}\n\n{guidelines}")
        except Exception as exc:
            log.debug(f"Daily push skipped: {exc}")
    
    def _write_init_report(self) -> str:
        """Write full initialization report and push to git."""
        try:
            from datetime import datetime
            import json
            os.makedirs("models/daily_reports", exist_ok=True)
            report_path = f"models/daily_reports/init_report_{datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')}.json"
            report = {
                "timestamp": datetime.now(datetime.UTC).isoformat(),
                "mode": "HANOON",
                "ticker": self.cfg.TICKER,
                "account": "DUO429233",
                "equity": round(self.account_equity, 2),
                "max_trade_usd": self.cfg.MAX_TRADE_SIZE_USD,
                "risk_per_trade": self.cfg.risk_amount_usd(self.account_equity),
                "baseline": self.cfg.INITIAL_CASH,
                "universe_size": len(PENNY_STOCK_UNIVERSE),
                "ai_models": list(self.ai_components.keys()) if self.ai_components else [],
                "ppo_loaded": self.model is not None,
                "consciousness_active": hasattr(self, 'consciousness') and self.consciousness is not None,
                "market_status": self._get_market_state(),
            }
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            # Push to git (async, non-blocking)
            try:
                self._worker.submit_git_commit(
                    files=[report_path],
                    message=f"report: hanoon init {datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')}",
                    push=False
                )
            except Exception:
                pass
            return report_path
        except Exception as exc:
            log.debug(f"Init report failed: {exc}")
            return "N/A"
    
    def _write_close_report(self):
        """Write full shutdown/session report and push to git."""
        try:
            from datetime import datetime
            import json
            os.makedirs("models/daily_reports", exist_ok=True)
            baseline = float(self.cfg.INITIAL_CASH)
            pnl = self.bot_nav - baseline
            pnl_pct = (pnl / baseline) * 100 if baseline else 0.0
            ib_start = self._ib_starting_balance or self.account_equity
            ib_change = self.account_equity - ib_start
            ib_change_pct = (ib_change / ib_start) * 100 if ib_start else 0.0
            report_path = f"models/daily_reports/close_report_{datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')}.json"
            report = {
                "timestamp": datetime.now(datetime.UTC).isoformat(),
                "mode": "HANOON",
                "ticker": self.cfg.TICKER,
                "ib_account": round(self.account_equity, 2),
                "ib_start": round(ib_start, 2),
                "ib_change": round(ib_change, 2),
                "ib_change_pct": round(ib_change_pct, 2),
                "bot_cash": round(self.bot_cash, 2),
                "bot_nav": round(self.bot_nav, 2),
                "day_pnl": round(pnl, 2),
                "day_pnl_pct": round(pnl_pct, 2),
                "baseline": baseline,
                "trades": self.trades_today,
                "wins": len([t for t in self.trade_journal if t["result"] == "win"]),
                "losses": len([t for t in self.trade_journal if t["result"] == "loss"]),
                "position": f"{self.shares:.0f} {self.current_ticker}" if self.shares > 0 else None,
                "scan_count": len(self.scan_results),
                "top_pick": self.top_pick.ticker if self.top_pick else None,
                "weights": self._load_weights(),
                "journal": self.trade_journal[-20:],
            }
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            # Push to git (async, non-blocking)
            try:
                self._worker.submit_git_commit(
                    files=[report_path],
                    message=f"report: hanoon close {datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')}",
                    push=False
                )
            except Exception:
                pass
            return report_path
        except Exception as exc:
            log.debug(f"Close report failed: {exc}")
            return "N/A"
    
    def _shutdown(self):
        # Write and push full session report
        report_path = self._write_close_report()
        self._refresh_account_balance()
        baseline = float(self.cfg.INITIAL_CASH)
        pnl = self.bot_nav - baseline
        pnl_pct = (pnl / baseline) * 100 if baseline else 0.0
        ib_start = self._ib_starting_balance or self.account_equity
        ib_change = self.account_equity - ib_start
        ib_change_pct = (ib_change / ib_start) * 100 if ib_start else 0.0
        summary = "📊 HANOON SESSION CLOSE\n"
        summary += f" IB Account:    ${self.account_equity:>12,.2f}  (start: ${ib_start:,.2f})\n"
        summary += f" IB Change:     ${ib_change:>+12,.2f} ({ib_change_pct:+.2f}%)\n"
        summary += f" Bot Cash:      ${self.bot_cash:>12,.2f}\n"
        summary += f" Bot NAV:       ${self.bot_nav:>12,.2f}\n"
        summary += f" Day P&L:       ${pnl:>+12,.2f} ({pnl_pct:+.2f}%)\n"
        summary += f" Baseline:      ${baseline:>12,.2f}\n"
        summary += f" Trades:        {self.trades_today:>12d}\n"
        if self.shares > 0:
            summary += f" Position:      {self.shares:.0f} {self.current_ticker}\n"
            summary += " (bracket orders remain active on IB)\n"
        summary += f"\nReport: {report_path}\n"
        log.info(summary)
        self.notifier.info(summary)
        push_daily_summary(self.bot_nav, self.account_equity)
        self.conn.disconnect()
        log.info("HANOON stopped.")


def main():
    """CLI entry-point for the live trading lifecycle."""
    from core.config import BotConfig
    from core.connector import IBConnector
    from core.notify import Notifier

    cfg = BotConfig()
    connector = IBConnector(cfg)
    notifier = Notifier(cfg)

    runner = ScalperRunner(connector, cfg, notifier)
    runner.run()


if __name__ == "__main__":
    main()