#!/usr/bin/env python3
"""
core/scalper_runner.py — HA-NUN single-focus institutional scalper.

THE RULES:
1. Scan ALL tickers, pick top 1 by probability score.
2. Deploy MAX $1,000 per trade (from live account balance).
3. Risk is FIXED at $50 max per trade — cannot be overridden.
4. Only trade UPTRENDS (price > SMA20, VWAP, rising closes).
5. Every tick is analyzed: price delta, velocity, tape rhythm.
6. Hard stop + trailing stop + hard TP + trailing profit.
7. Daily summary shows all trades + balance changes.
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
from core.git_sync import init as git_sync_init, push_trade, push_daily_summary
from core.async_utils import get_background_worker, AtomicFileWriter
from core.feature_drift import validate_features_at_startup
from core.train_subprocess import launch_training


def _only_uptrend(df: pd.DataFrame, current_px: float) -> bool:
    if len(df) < 20:
        return False
    closes = df["close"].values[-20:]
    sma20 = np.mean(closes)
    if current_px <= sma20 * 0.98:
        return False
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vwap = np.average(typical[-20:], weights=df["volume"].values[-20:])
    if current_px <= vwap * 0.985:
        return False
    # Require at least 2 of last 5 closes rising
    rising = sum(1 for i in range(-5, 0) if i > -len(closes) and closes[i] >= closes[i-1])
    if rising < 2:
        return False
    atr = compute_atr(df, period=10)
    if atr <= 0 or atr > current_px * 0.05:
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
            return self.data.get_latest_price() or 0.0
        except Exception:
            return 0.0
    
    def _detect_exit(self, current_px: float):
        """Detect if position was closed (by bracket or manually) and notify."""
        if self._prev_shares > 0 and self.shares == 0:
            # Position closed
            pnl = (current_px - self._entry_price) * self._prev_shares
            pnl_pct = ((current_px / self._entry_price) - 1) * 100 if self._entry_price else 0
            result = "win" if pnl > 0 else "loss"
            log.info(f"📕 EXIT: {self.current_ticker} @ ${current_px:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | {result.upper()}")
            self.notifier.info(
                f"📕 HA-NUN EXIT\n"
                f"Ticker: {self.current_ticker}\n"
                f"Exit: ${current_px:.2f}\n"
                f"Entry: ${self._entry_price:.2f}\n"
                f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                f"Result: {result.upper()}"
            )
            # Save to trade journal for self-training
            self.trade_journal.append({
                "ticker": self.current_ticker,
                "entry": self._entry_price,
                "exit": current_px,
                "shares": self._prev_shares,
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "result": result,
            })
            # Record exit outcome into experience buffer
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
        self._prev_shares = self.shares
        if self.shares > 0:
            self._entry_price = current_px
    
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
                "mode": "HA-NUN",
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
        log.info("HA-NUN — SINGLE FOCUS SCALPER")
        acct = self.conn.ib.accountValues()
        log.info(f"Account: {acct[0].account if acct else 'unknown'} | Universe: {len(PENNY_STOCK_UNIVERSE)} tickers")
        log.info(f"Max per trade: ${self.cfg.MAX_TRADE_SIZE_USD:,.0f} | Risk/trade: ${self.cfg.risk_amount_usd(self.account_equity):.2f}")
        log.info(f"Init report: {report_path}")
        self.notifier.info("🚀 HA-NUN STARTED")

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
                    # Scan and trade ONLY when IB is connected
                    if self.conn.is_connected():
                        now = time.time()
                        time_since_scan = now - self._last_scan_time
                        have_locked = len(self._locked_targets) > 0
                        
                        # Hard gate: if locked targets exist, ONLY rescan after SCAN_INTERVAL_SECONDS
                        # No exceptions, no early rescans
                        need_rescan = False
                        if have_locked:
                            # With locked targets, only rescan at full interval
                            if time_since_scan > self.cfg.SCAN_INTERVAL_SECONDS:
                                need_rescan = True
                                log.debug(f"Rescan triggered: interval elapsed ({time_since_scan:.0f}s > {self.cfg.SCAN_INTERVAL_SECONDS}s)")
                        else:
                            # No locked targets: scan more aggressively until we find candidates
                            if self.top_pick is None and self.shares == 0 and time_since_scan > self.cfg.SCAN_INTERVAL_SECONDS:
                                need_rescan = True
                                log.debug("Rescan triggered: no locked targets")
                        
                        if need_rescan:
                            self._last_scan_time = now
                            self._scan_and_rank()
                        elif self.top_pick is None and self.shares == 0 and have_locked:
                            # Heartbeat: cycle through locked targets for entry evaluation
                            cycle = int(now) % max(len(self._locked_targets), 1)
                            self.top_pick = self._locked_targets[cycle]
                        
                        # Apply confidence gating for pre-market/after-hours
                        if self.top_pick and self.shares == 0:
                            confidence = getattr(self.top_pick, 'rank_score', 0) / 100.0
                            min_conf = self.cfg.MIN_CONFIDENCE_PRE_MARKET if market_state != "open" else 0.0
                            
                            if confidence >= min_conf:
                                self._attempt_entry()
                            else:
                                log.info(f"⏸ SKIPPING {self.top_pick.ticker} — confidence {confidence:.0%} < {min_conf:.0%} threshold for {market_state}")
                                self.top_pick = None
                else:
                    # Market is closed: train instead of scan
                    if int(time.time()) % 60 == 0:  # log every minute
                        log.info(f"⏸ MARKET CLOSED ({market_state}) — training instead")
                    if time.time() - self._last_scan_time > 300:  # train every 5 min
                        self._last_scan_time = time.time()
                        self._train_off_hours()
                
                self._refresh_account_balance()
                self._write_live_metrics()
                self._maybe_daily_push()
                
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self._shutdown()
    
    def _scan_and_rank(self):
        t0 = time.perf_counter()
        # Use a smaller active screen to reduce IB load; not the full universe every scan
        screen_list = getattr(self.cfg, "SCAN_UNIVERSE", PENNY_STOCK_UNIVERSE[:40])
        log.info(f"🔍 HA-NUN SCAN: {len(screen_list)} tickers (screened subset)...")
        results: List[Dict] = []

        def _scan_one(ticker: str) -> Optional[Dict]:
            try:
                cfg_ticker = self.cfg.TICKER
                self.cfg.TICKER = ticker
                dm = DataManager(self.conn, self.cfg)
                hist = dm.fetch_historical(duration="1 D", bar_size="1 min", use_rth=False)
                if hist is None or len(hist) < 60:
                    return None
                score = self._score_ticker(ticker, hist)
                self.cfg.TICKER = cfg_ticker
                return score if score and score.get("total_score", 0) > 0 else None
            except Exception:
                return None

        # Sequential-ish with small worker count to avoid IB rate limits
        workers = min(len(screen_list), 6)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_scan_one, t): t for t in screen_list}
            for fut in as_completed(futures):
                r = fut.result()
                if r:
                    results.append(r)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        
        results.sort(key=lambda x: x["total_score"], reverse=True)
        self.scan_results = results[:10]
        
        if self.scan_results:
            # Keep top N setups as locked targets
            max_locked = getattr(self.cfg, "MAX_LOCKED_TARGETS", 5)
            self._locked_targets = []
            for r in self.scan_results[:max_locked]:
                best = r
                pick = ScanResult(
                    ticker=best["ticker"], price=best["price"], volume=best["volume"],
                    avg_volume=best["avg_volume"], relative_volume=best["rel_vol"],
                    rank_score=best["total_score"], reason=best["reasons"],
                )
                self._locked_targets.append(pick)
            # Current top_pick for immediate evaluation is the best of the locked list
            self.top_pick = self._locked_targets[0] if self._locked_targets else None
            names = ", ".join([p.ticker for p in self._locked_targets])
            log.info(f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names} | Scan: {elapsed_ms:.0f}ms")
            self.notifier.info(f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names}\nTop score: {self.top_pick.rank_score:.0f}")
            
            # Record scan pick into experience buffer
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
            # No new setups: DO NOT clear locked targets, just clear top_pick for re-evaluation
            self.top_pick = None
            if self._locked_targets:
                names = ", ".join([p.ticker for p in self._locked_targets])
                log.info(f"🔍 No new setups — keeping locked targets: {names} ({elapsed_ms:.0f}ms)")
            else:
                log.info(f"🔍 No setups — no locked targets yet ({elapsed_ms:.0f}ms)")
    
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
        vwap_hist = np.array([np.average(typical[max(0, i-19):i+1], weights=volumes[max(0, i-19):i+1]) for i in range(19, len(typical))])
        vwap_slope = (vwap_hist[-1] - vwap_hist[-5]) / (vwap_hist[-5] + 1e-9) * 100
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
        }
    
    def _attempt_entry(self):
        if not self.top_pick:
            return
        ticker = self.top_pick.ticker
        self.current_ticker = ticker
        try:
            self.cfg.TICKER = ticker
            df_fast = self.data.fetch_historical(duration="1 D", bar_size="1 min", use_rth=False)
            if df_fast is None or len(df_fast) < 20:
                return
            current_px = float(df_fast["close"].iloc[-1])
            if not _only_uptrend(df_fast, current_px):
                self.top_pick = None
                return
            inst = self.institutional.scan()
            override, reason = self.institutional.should_override_buy()
            if override:
                self.top_pick = None
                return
            
            # AI gate: require AI confidence >= threshold before entry
            if self.cfg.USE_ENHANCED_AI and self.model is not None:
                self._ai_update_buffers(df_fast, current_px)
                should_enter, ai_conf, ai_reason = self._ai_gate_entry(ticker, current_px)
                if not should_enter:
                    log.info(f"  🧠 AI BLOCKS ENTRY on {ticker}: confidence={ai_conf:.0%} — {ai_reason[:80]}")
                    self.top_pick = None
                    return
                log.info(f"  🧠 AI APPROVES ENTRY: {ticker} confidence={ai_conf:.0%}")
            
            fast_atr = compute_atr(df_fast, period=5)
            momentum = compute_momentum_score(df_fast, lookback=5)
            deploy_usd = min(self.cfg.MAX_TRADE_SIZE_USD, self.bot_cash * 0.95)
            risk_usd = self.cfg.risk_amount_usd(self.account_equity)
            stop_dist = max(fast_atr * self.cfg.SCALP_STOP_ATR_MULTIPLIER, current_px * self.cfg.SCALP_MIN_STOP_PCT)
            stop_dist = min(stop_dist, current_px * self.cfg.SCALP_MAX_STOP_PCT)
            shares_by_risk = int(risk_usd / stop_dist)
            shares_by_cash = int(deploy_usd / current_px)
            shares = min(shares_by_risk, shares_by_cash, self.cfg.MAX_SHARES_PER_TRADE)
            if shares < 1:
                return
            tp_dist = max(fast_atr * self.cfg.SCALP_TP_ATR_MULTIPLIER, stop_dist * self.cfg.SCALP_MIN_RR)
            tp_dist = min(tp_dist, current_px * self.cfg.SCALP_MAX_TP_PCT)
            tp_price = current_px + tp_dist
            plan = TradePlan(
                side="LONG", entry_price=current_px, shares=float(shares),
                initial_stop_price=round(current_px - stop_dist, 4),
                take_profit_price=round(tp_price, 4),
                risk_usd=round(shares * stop_dist, 2), atr_at_entry=fast_atr,
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
            self.risk.open_position(plan)
            self.trades_today += 1
            log.info(f"🎯 FOCUS ENTRY: {shares}x {ticker} @ ${current_px:.2f} | Stop -{stop_dist/current_px:.2%} | TP +{tp_dist/current_px:.2%} | Deployed: ${cost:,.0f} | Score: {self.top_pick.rank_score:.0f}")
            self.notifier.info(
                f"🎯 HA-NUN ENTRY\n"
                f"Ticker: {ticker}\n"
                f"Qty: {shares}\n"
                f"Entry: ${current_px:.2f}\n"
                f"Stop: ${plan.initial_stop_price:.2f}\n"
                f"Target: ${plan.take_profit_price:.2f}\n"
                f"Deployed: ${cost:,.0f}\n"
                f"Score: {self.top_pick.rank_score:.0f}"
            )
            push_trade(ticker, "BUY", current_px, shares)
            
            # Record entry into experience buffer for unified training
            try:
                buffer_append({
                    "source": "live_trade",
                    "ticker": ticker,
                    "action": "BUY",
                    "entry_price": current_px,
                    "stop_dist": stop_dist,
                    "tp_dist": tp_dist,
                    "confidence": 0.5,
                    "scan_score": self.top_pick.rank_score if self.top_pick else 0,
                    "features": [],
                })
            except Exception:
                pass
            
            self.top_pick = None
        except Exception as exc:
            log.error(f"Entry error on {ticker}: {exc}")
    
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
            return f"🧭 HA-NUN SELF-IMPROVEMENT GUIDELINES\n{'_'*40}\n{rules_text}\n"
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
                        message=f"train: ha-nun daily self-improvement {today_str}",
                        push=True
                    )
                except Exception:
                    pass
                log.info(f"📤 {stmt}")
                log.info(f"🧭 Guidelines generated and pushed to git")
                self.notifier.info(f"📊 HA-NUN DAILY COMPLETE\n{stmt}\n\n{guidelines}")
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
                "mode": "HA-NUN",
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
                    message=f"report: ha-nun init {datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')}",
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
                "mode": "HA-NUN",
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
                    message=f"report: ha-nun close {datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S')}",
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
        summary = "📊 HA-NUN SESSION CLOSE\n"
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
        log.info("HA-NUN stopped.")


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