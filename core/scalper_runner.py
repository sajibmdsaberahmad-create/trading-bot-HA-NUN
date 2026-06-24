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
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Any
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.market_hours import get_market_state, market_status_line, now_et

import numpy as np
import pandas as pd
import requests

from core.config import BotConfig
from core.connector import IBConnector
from core.data import DataManager
from core.features_enhanced import FeatureEngineerEnhanced
from core.institutional import InstitutionalDetector, InstitutionalSignal
from core.scanner import StockScanner, ScanResult, PENNY_STOCK_UNIVERSE, CONTRACT_BLACKLIST
from core.risk import RiskManager, TradePlan, compute_atr, compute_momentum_score, safe_vwap
from core.broker import BrokerExecutor, BracketHandle
from core.env import TradingEnv
from core.agent import build_ppo_agent, predict_with_reasoning, initialize_enhanced_system
from core.experience_buffer import append as buffer_append
from core.market_context import summarize_market_context
from core.market_regime import MarketRegimeDetector
from core.self_improver import generate_self_improvement_plan
from core.consciousness import AIConsciousness
from core.pilot_experience import PilotExperienceSystem, pilot_experience_to_git
from core.pattern_memory_bank import PatternMemoryBank, pattern_memory_to_git
from core.notify import log, Notifier
from core.git_sync import init as git_sync_init, push_trade, push_daily_summary, push_model_release, sync_all_learning_artifacts, push_full_shutdown_sync, push_learning_checkpoint
from core.local_cleanup import cleanup_local_workspace
from core.async_utils import get_background_worker, AtomicFileWriter
from core.feature_drift import validate_features_at_startup
from core.train_subprocess import launch_training
from core.pilot_mode import (
    get_live_scan_universe, get_effective_confidence_threshold, get_deploy_usd,
    snapshot_features, send_dynamic_notification, observe_trade_everywhere,
    maybe_incremental_train, mtf_score_bonus, is_tradeable_ticker, generative_think,
    generative_position_decision,
)
from core.ai_commander import AICommander
from core.account_evaluator import AccountEvaluator


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
    vwap = safe_vwap(typical[-20:], volumes[-20:])
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
        self._position_stop: float = 0.0
        self._position_target: float = 0.0
        self._position_peak: float = 0.0
        self._hard_stop_floor: float = 0.0
        self._last_position_pulse: float = 0.0
        self._last_ai_position_manage: float = 0.0
        self._position_opened_at: float = 0.0
        self._pending_entry_ticker: Optional[str] = None
        self._pending_bracket_handle: Optional[BracketHandle] = None
        self._pending_entry_until: float = 0.0
        self._entry_cooldown_until: Dict[str, float] = {}
        self._short_warned: set = set()
        self._last_bg_watch: float = 0.0
        self._next_best_pick: Optional[ScanResult] = None
        self._next_best_score: float = 0.0
        self._spike_skip_until: Dict[str, float] = {}
        self._last_flat_pulse: float = 0.0
        self.top_pick: Optional[ScanResult] = None
        self._locked_targets: List[ScanResult] = []
        self._targets_locked_at: float = 0.0
        self._focus_target_index: int = 0
        self._last_focus_rotate: float = 0.0
        self._contract_blacklist: set = set(CONTRACT_BLACKLIST)
        self._last_scan_time: float = 0.0
        self._last_metrics_write: float = 0.0
        self._last_ai_narrative: float = 0.0
        self._scan_data_cache: Dict[str, pd.DataFrame] = {}  # Cache scanned data
        
        # Live stream monitors for locked targets (heartbeat in milliseconds)
        self._target_monitors: Dict[str, DataManager] = {}      # ticker -> DataManager
        self._target_last_bar_count: Dict[str, int] = {}        # ticker -> last seen bar count
        self._active_stream_ticker: Optional[str] = None        # Currently streaming ticker
        
        self.trade_journal: List[Dict] = []
        self.trades_today: int = 0
        self._current_day: Optional[str] = None
        self._last_daily_push_date: Optional[str] = None
        self._last_market_state: Optional[str] = None
        self._last_learning_push: float = 0.0
        self._weights_file = "models/scalper_weights.json"
        self._weights_mtime = 0.0

        # Experience buffer for unified learning
        self._xp_buffer_initialized = False
        
        # Pilot Experience and Pattern Memory systems
        self.pilot = PilotExperienceSystem(cfg)
        self.patterns = PatternMemoryBank(cfg)
        
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

        # Cognitive autopilot — autonomous decision layer with hard guardrails
        self.autopilot = None
        try:
            from core.cognitive_autopilot import CognitiveAutopilot
            self.autopilot = CognitiveAutopilot(cfg)
            self.autopilot.start()
            log.info("🤖 Cognitive Autopilot integrated into live trading loop")
        except Exception as exc:
            log.debug(f"Cognitive autopilot init skipped: {exc}")

        self.ai_commander = AICommander(
            self.cfg, self.autopilot, self.consciousness, self.model, self.ai_components,
        )
        self.notifier.attach_ai_brain(
            ai_commander=self.ai_commander,
            autopilot=self.autopilot,
            consciousness=self.consciousness,
            pilot=self.pilot,
        )
        self.account_evaluator = AccountEvaluator(self.cfg)
    
    def _notify_context(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Rich context for AI Telegram briefings."""
        ctx: Dict[str, Any] = {
            "nav": round(self.bot_nav, 2),
            "bot_cash": round(self.bot_cash, 2),
            "equity": round(self.account_equity, 2),
            "position": self.current_ticker,
            "shares": self.shares,
            "trades_today": self.trades_today,
            "win_rate": round(getattr(self.risk, "win_rate", 0) * 100, 1),
            "deployed_pct": round(
                (self.shares * self._latest_price()) / (self.account_equity + 1e-9) * 100, 2
            ) if self.shares > 0 else 0,
        }
        if self.top_pick:
            ctx["top_pick"] = self.top_pick.ticker
            ctx["top_score"] = self.top_pick.rank_score
        if self._locked_targets:
            ctx["locked"] = [t.ticker for t in self._locked_targets[:5]]
        if hasattr(self, "pilot"):
            try:
                ctx.update(self.pilot.get_veteran_status())
            except Exception:
                pass
        if extra:
            ctx.update(extra)
        return ctx

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

    def _run_account_eval(self, event: str, force: bool = False):
        """AI account snapshot, compare, log, and Telegram brief."""
        try:
            self.account_evaluator.evaluate(
                self, event, self.notifier, self.ai_commander,
                self.autopilot, self.consciousness, self.pilot, force=force,
            )
        except Exception as exc:
            log.debug(f"Account evaluation skipped: {exc}")
    
    def _latest_price(self) -> float:
        try:
            # Priority 1: Active stream ticker (post-entry live monitoring)
            if self._active_stream_ticker and self._active_stream_ticker in self._target_monitors:
                dm = self._target_monitors[self._active_stream_ticker]
                px = dm.get_latest_price()
                if px and px > 0:
                    return px
            # Priority 2: Cached scan bars for current ticker
            ticker = self.current_ticker or getattr(self.cfg, "TICKER", "")
            if ticker:
                df = self._scan_data_cache.get(ticker)
                if df is not None and len(df) > 0:
                    px = float(df["close"].iloc[-1])
                    if px > 0:
                        return px
            # Priority 3: Main data stream
            return self.data.get_latest_price() or 0.0
        except Exception:
            return 0.0
    
    def _live_price_for(self, ticker: str, fallback: float) -> float:
        """Best available price: live tick stream, then cache, then fallback."""
        dm = self._target_monitors.get(ticker)
        if dm:
            live = dm.get_latest_price()
            if live and live > 0:
                return float(live)
        df = self._scan_data_cache.get(ticker)
        if df is not None and len(df) > 0:
            px = float(df["close"].iloc[-1])
            if px > 0:
                return px
        return float(fallback)

    def _entry_parent_price(self, ticker: str, current_px: float) -> Optional[float]:
        """Deprecated — use _smart_entry_plan()."""
        bid, ask = self._get_bid_ask(ticker)
        limit_px, _ = self.broker.decide_smart_entry(current_px, bid, ask, 1, 0)
        return limit_px

    def _get_bid_ask(self, ticker: str) -> Tuple[Optional[float], Optional[float]]:
        """Snapshot bid/ask from IB for smart limit entries."""
        try:
            saved = self.cfg.TICKER
            self.cfg.TICKER = ticker
            contract = self.conn.get_contract()
            self.cfg.TICKER = saved
            ticks = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(0.4)
            bid = float(ticks.bid) if ticks.bid and ticks.bid > 0 else None
            ask = float(ticks.ask) if ticks.ask and ticks.ask > 0 else None
            self.ib.cancelMktData(contract)
            return bid, ask
        except Exception as exc:
            log.debug(f"Bid/ask snapshot {ticker}: {exc}")
            return None, None

    def _liquidity_cap_shares(self, shares: int, price: float, df) -> int:
        """Shrink size on penny/thin books — avoids IB error 2161 disruptive-order caps."""
        if shares < 1 or price <= 0:
            return 0
        penny_thr = float(getattr(self.cfg, "PENNY_PRICE_THRESHOLD", 1.0))
        avg_vol = float(df["volume"].tail(20).mean()) if df is not None and len(df) else 0
        recent_vol = float(df["volume"].iloc[-1]) if df is not None and len(df) else 0
        vol_ref = max(recent_vol, avg_vol, 1.0)
        max_pct = float(getattr(self.cfg, "LIQUIDITY_MAX_VOL_PCT", 0.08))
        max_by_vol = max(1, int(vol_ref * max_pct))

        capped = min(shares, max_by_vol)
        if price < penny_thr:
            penny_max = int(getattr(self.cfg, "PENNY_MAX_SHARES", 1200))
            penny_deploy = float(getattr(self.cfg, "PENNY_MAX_DEPLOY_USD", 350.0))
            capped = min(capped, penny_max, max(1, int(penny_deploy / price)))

        if capped < shares:
            log.info(
                f"  🧠 Liquidity sizing: {shares:,} → {capped:,} sh "
                f"(vol≈{vol_ref:,.0f}, ${price:.4f})"
            )
        return max(1, capped)

    def _clamp_entry_shares(self, shares: int, price: float) -> int:
        deploy_usd = min(
            get_deploy_usd(self.cfg, self.pilot),
            float(getattr(self.cfg, "MAX_TRADE_SIZE_USD", 1000.0)),
        )
        if price <= 0:
            return 0
        return max(1, min(int(shares), int(deploy_usd / price)))

    def _sync_position_from_ib(self):
        """Keep local shares in sync with IB (detect bracket fills/exits)."""
        if not self.current_ticker:
            return
        try:
            found = False
            ib_shares = 0.0
            for p in self.ib.positions():
                sym = getattr(p.contract, "symbol", "")
                if sym == self.current_ticker:
                    ib_shares = float(p.position)
                    found = True
                    break
            if found:
                if ib_shares < 0:
                    sym = self.current_ticker or ""
                    if sym not in self._short_warned:
                        self._short_warned.add(sym)
                        log.warning(
                            f"IB short position {ib_shares:.0f} {sym} "
                            f"— long-only scalper ignoring (orphan paper debris)"
                        )
                elif ib_shares > 0:
                    # Never inflate local size above what we opened this session
                    opened = getattr(self, "_position_opened_at", 0.0)
                    if opened and ib_shares > self.shares + 1:
                        log.debug(
                            f"IB position {ib_shares:.0f} > local {self.shares:.0f} "
                            f"— keeping local count"
                        )
                    else:
                        self.shares = ib_shares
            elif self.shares > 0:
                # Grace period after entry — IB may not show position until parent fills
                opened_at = getattr(self, "_position_opened_at", 0.0)
                if opened_at and (time.time() - opened_at) < 60.0:
                    return
                self.shares = 0.0
        except Exception as exc:
            log.debug(f"Position sync: {exc}")

    def _credit_exit_proceeds(self, quantity: float, exit_px: float):
        """Return sale proceeds to bot cash and refresh NAV."""
        proceeds = float(quantity) * exit_px * (1 - self.cfg.TRANSACTION_COST_PCT)
        self.bot_cash += proceeds
        self.bot_nav = self.bot_cash

    def _detect_exit(self, current_px: float):
        """Detect if position was closed (by bracket or manually) and notify."""
        if self._prev_shares > 0 and self.shares == 0:
            opened_at = getattr(self, "_position_opened_at", 0.0)
            if opened_at and (time.time() - opened_at) < 60.0:
                return
            self._credit_exit_proceeds(self._prev_shares, current_px)
            pnl = (current_px - self._entry_price) * self._prev_shares
            pnl_pct = ((current_px / self._entry_price) - 1) * 100 if self._entry_price else 0
            result = "win" if pnl > 0 else "loss"
            log.info(f"📕 EXIT: {self.current_ticker} @ ${current_px:.2f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | {result.upper()}")
            exit_ctx = {
                "ticker": self.current_ticker,
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "result": result,
                "pilot_level": self.pilot.state.level if hasattr(self, "pilot") else "Cadet",
            }
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                send_dynamic_notification(
                    self.notifier, self.autopilot, "trade_closed",
                    self._notify_context(exit_ctx),
                    f"📕 EXIT {self.current_ticker} | P&L ${pnl:+.2f} ({pnl_pct:+.1f}%) | {result.upper()}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            else:
                self.notifier.info(
                    f"📕 HANOON EXIT\n"
                    f"Ticker: {self.current_ticker}\n"
                    f"Exit: ${current_px:.2f}\n"
                    f"Entry: ${self._entry_price:.2f}\n"
                    f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                    f"Result: {result.upper()}"
                )
            trade_rec = {
                "ticker": self.current_ticker,
                "entry": self._entry_price,
                "exit": current_px,
                "shares": self._prev_shares,
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "result": result,
            }
            self.trade_journal.append(trade_rec)
            if self.ai_commander:
                self.ai_commander.record_trade(trade_rec)
            try:
                self.account_evaluator.evaluate(
                    self, "trade_closed", ai_commander=self.ai_commander,
                )
            except Exception:
                pass
            observe_trade_everywhere(
                trade_rec, self.autopilot, self.consciousness, self.pilot,
            )
            if getattr(self.cfg, "LEARNING_PUSH_ON_TRADE", True):
                try:
                    push_learning_checkpoint(f"trade_closed_{trade_rec.get('ticker', '?')}")
                except Exception:
                    pass
            try:
                buffer_append({
                    "source": "live_trade",
                    "ticker": self.current_ticker,
                    "action": "SELL",
                    "exit_price": current_px,
                    "entry_price": self._entry_price,
                    "pnl_usd": round(pnl, 2),
                    "win": 1 if pnl > 0 else 0,
                    "confidence": getattr(self, "_last_ai_confidence", 0.5),
                    "features": snapshot_features(self._feature_buffer, self.cfg),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass
            
            # Remove from active positions
            if hasattr(self, '_active_positions'):
                self._active_positions = [p for p in self._active_positions if p["ticker"] != self.current_ticker]
            
            self.current_ticker = None
            self.bracket_handle = None
            self._position_opened_at = 0.0
            # Queue next opportunity if background watch found one
            if getattr(self, "_next_best_pick", None) and self._next_best_score >= 25:
                self.top_pick = self._next_best_pick
            self._position_stop = 0.0
            self._position_target = 0.0
            self._position_peak = 0.0
            self._hard_stop_floor = 0.0
            # Clean up live stream for closed position
            if self._active_stream_ticker:
                self._stop_target_stream(self._active_stream_ticker)
                self._active_stream_ticker = None
            
            # Train from exit event
            try:
                self._daily_self_train()
            except Exception:
                pass
            try:
                from core.hybrid_distiller import maybe_run_hybrid_distillation
                maybe_run_hybrid_distillation(self.cfg)
            except Exception as exc:
                log.debug(f"Hybrid distill check: {exc}")

            # Update pilot experience
            try:
                pnl_usd = round(pnl, 2)
                pnl_pct = round(pnl_pct, 2) / 100
                self.pilot.complete_flight(current_px, pnl_usd, pnl_pct, "exit")
                if pnl > 0:
                    self.pilot.record_pattern_match("win", True, pnl_usd)
                else:
                    self.pilot.record_pattern_match("loss", False, pnl_usd)
            except Exception:
                pass

            # Sync learning artifacts
            try:
                pilot_experience_to_git(self.pilot)
                if getattr(self.cfg, "LEARNING_PUSH_ON_TRADE", True):
                    push_learning_checkpoint(f"trade_exit_{self.current_ticker}")
            except Exception:
                pass

            try:
                maybe_incremental_train(
                    self.cfg, self.trades_today, self.consciousness, self.autopilot,
                )
            except Exception:
                pass
        self._prev_shares = self.shares

    def _ensure_position_stream(self, ticker: str):
        """Dedicated tick stream on open position — never stop monitoring after entry."""
        if not ticker:
            return
        self._active_stream_ticker = ticker
        self._start_target_stream(ticker)
    
    def _clear_pending_entry(self, ticker: Optional[str] = None, cooldown_sec: float = 45.0):
        """Reset pending bracket state; optional per-ticker cooldown."""
        if ticker:
            self._entry_cooldown_until[ticker] = time.time() + cooldown_sec
            self._spike_skip_until[ticker] = time.time() + cooldown_sec
        self._pending_entry_ticker = None
        self._pending_bracket_handle = None
        self._pending_entry_until = 0.0

    def _exit_position(self, current_px: float, reason: str):
        """Manually exit position (early exit, AI exit, etc.)."""
        if self.shares <= 0:
            return
        ticker = self.current_ticker
        quantity = int(self.shares)
        entry_price = self._entry_price
        try:
            self.broker.flatten_position(quantity, handle=self.bracket_handle, urgent=True, symbol=ticker)
            self.ib.sleep(1)
            pnl = (current_px - entry_price) * quantity
            log.info(f"⚡ EARLY EXIT: {ticker} @ ${current_px:.2f} | Reason: {reason} | P&L: ${pnl:+.2f}")
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                send_dynamic_notification(
                    self.notifier, self.autopilot, "early_exit",
                    self._notify_context({
                        "ticker": ticker, "price": current_px, "pnl_usd": round(pnl, 2),
                        "reason": reason, "entry": entry_price,
                    }),
                    f"⚡ EARLY EXIT\n{ticker} @ ${current_px:.2f}\nReason: {reason}\nP&L: ${pnl:+.2f}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            else:
                self.notifier.info(f"⚡ EARLY EXIT\n{ticker} @ ${current_px:.2f}\nReason: {reason}\nP&L: ${pnl:+.2f}")
            self._credit_exit_proceeds(quantity, current_px)
            self.shares = 0.0
            self._prev_shares = 0.0
            self.bracket_handle = None
            self.current_ticker = None
            self._position_stop = 0.0
            self._position_target = 0.0
            self._position_peak = 0.0
            self._hard_stop_floor = 0.0
            if self.risk.plan:
                self.risk.record_trade_result(pnl)
                self.risk.close_position()
            if hasattr(self, '_active_positions'):
                self._active_positions = [p for p in self._active_positions if p["ticker"] != ticker]
            self._position_opened_at = 0.0
            self._clear_pending_entry(ticker, cooldown_sec=30.0)
            if getattr(self, "_next_best_pick", None) and self._next_best_score >= 25:
                self.top_pick = self._next_best_pick
            if self._active_stream_ticker:
                self._stop_target_stream(self._active_stream_ticker)
                self._active_stream_ticker = None
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
                if isinstance(r, dict):
                    scan_data.append({
                        "ticker": r.get("ticker", "?"),
                        "price": r.get("price", 0),
                        "score": round(float(r.get("total_score", 0)), 1),
                        "reason": str(r.get("reasons", ""))[:30],
                    })
                else:
                    scan_data.append({
                        "ticker": r.ticker, "price": r.price,
                        "score": round(r.rank_score, 1), "reason": r.reason[:30],
                    })
            metrics = {
                "mode": "HANOON",
                "account_equity": round(self.account_equity, 2),
                "available_cash": round(self.available_cash or 0, 2),
                "position_value": round(self.shares * self._latest_price(), 2),
                "nav": round(self.bot_nav, 2),
                "deployed_pct": round(
                    (self.shares * self._latest_price()) / (self.account_equity + 1e-9) * 100, 1
                ),
                "current_ticker": self.current_ticker or "NONE",
                "position": f"{self.shares:.0f} {self.current_ticker}" if self.shares > 0 else "NONE",
                "win_rate": round(win_rate, 1),
                "trades_today": self.trades_today,
                "top_pick": self.top_pick.ticker if self.top_pick else None,
                "top_score": self.top_pick.rank_score if self.top_pick else 0,
                "next_best": (
                    self._next_best_pick.ticker
                    if getattr(self, "_next_best_pick", None) else None
                ),
                "next_best_score": round(getattr(self, "_next_best_score", 0.0), 1),
                "scan_results": scan_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                if self.shares <= 0 and now - getattr(self, "_last_ai_narrative", 0) > 30.0:
                    self._last_ai_narrative = now
                    metrics["ai_narrative"] = self.ai_commander.account_narrative(metrics)
            with open("live_metrics.json", "w") as f:
                json.dump(metrics, f, indent=2)
        except Exception as exc:
            log.debug(f"Could not write live_metrics.json: {exc}")
    
    def run(self):
        self._register_shutdown_signals()
        # Full initialization report (pushed to git and Telegram)
        report_path = self._write_init_report()
        log.info("HANOON — SINGLE FOCUS SCALPER")
        acct = self.conn.ib.accountValues()
        log.info(f"Account: {acct[0].account if acct else 'unknown'} | Universe: live IB scanner (no static list)")
        if getattr(self.cfg, "OLLAMA_ENABLED", False):
            from core.memory_guard import memory_status, is_low_ram_machine
            mem = memory_status(self.cfg)
            log.info(
                f"🧠 Generative thinking: ON | model={self.cfg.OLLAMA_MODEL} @ {self.cfg.OLLAMA_HOST} | "
                f"budget={getattr(self.cfg, 'OLLAMA_MEMORY_BUDGET_MB', 2560)}MB | "
                f"warm={not getattr(self.cfg, 'OLLAMA_UNLOAD_AFTER_CALL', False)} | "
                f"RAM free={mem['available_ram_mb']}MB"
            )
            if is_low_ram_machine() and "llama3" in self.cfg.OLLAMA_MODEL.lower():
                log.warning(
                    "⚠️ llama3 uses ~4.7GB RAM — on 8GB Mac use OLLAMA_MODEL=qwen2.5:3b"
                )
        else:
            log.warning("🧠 Generative thinking: OFF — set OLLAMA_ENABLED=true and run Ollama locally")
        if getattr(self.cfg, "AI_FULL_CONTROL", True):
            log.info("🧠 AI FULL CONTROL: all decisions, logs, journals, notifications via AI brain")
        log.info(f"Max per trade: ${self.cfg.MAX_TRADE_SIZE_USD:,.0f} | Risk/trade: ${self.cfg.risk_amount_usd(self.account_equity):.2f}")
        if getattr(self.cfg, "PILOT_MODE_ENABLED", True) and hasattr(self, "pilot"):
            vs = self.pilot.get_veteran_status()
            log.info(
                f"✈️ PILOT MODE: {vs['level']} | XP={vs['total_xp']} | "
                f"flights={vs['flights_completed']} | conf_gate={vs['confidence_threshold']:.0%}"
            )
        try:
            from core.hybrid_distiller import distillation_status
            ds = distillation_status(self.cfg)
            log.info(
                f"🎓 Hybrid distill: phase={ds['phase']} | "
                f"closed_trades={ds['closed_trades']}/{ds['full_trades']} | "
                f"fast_path={'on' if ds['fast_path'] else 'off'}"
            )
        except Exception:
            pass
        if getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            log.info(
                f"📞 Live AI hotline ON — Ollama+PPO parallel | "
                f"max_age={getattr(self.cfg, 'LIVE_AI_MAX_AGE_SEC', 4)}s | "
                f"prefetch top {getattr(self.cfg, 'LIVE_AI_PREFETCH_TOP_N', 3)}"
            )
        log.info(f"Init report: {report_path}")

        self._refresh_account_balance()
        if self._ib_starting_balance:
            log.info(f"IB Starting Balance: ${self._ib_starting_balance:,.2f}")

        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, "startup",
                self._notify_context({"ib_balance": self._ib_starting_balance}),
                "🚀 HANOON STARTED",
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info("🚀 HANOON STARTED")

        # Clear orphaned bracket orders from previous sessions before trading
        try:
            self.broker.cancel_stale_open_orders()
            n = self.broker.flatten_orphan_short_positions()
            if n:
                log.info(f"🧹 Covered {n} orphan short position(s) on paper account")
        except Exception:
            pass

        # Check market status quietly
        log.info(f"🕐 {market_status_line(self.cfg)}")
        market_state = get_market_state(self.cfg)
        self._last_market_state = market_state
        if getattr(self.cfg, "AI_ACCOUNT_EVAL_ON_STARTUP", True):
            self._run_account_eval("session_startup", force=True)
        if market_state != "open":
            log.info(f"📊 Market state: {market_state.upper()} — extended-hours rules apply")

        # Block startup scan until IB connection is confirmed live
        if self.conn.is_connected():
            self._last_scan_time = time.time()
            self._scan_and_rank()
        else:
            log.warning("IB Gateway not connected at startup — skipping initial scan until connection is live")
        
        try:
            while True:
                in_position = self.shares > 0
                loop_sec = float(getattr(self.cfg, "POSITION_LOOP_SEC", 0.25)) if in_position else float(
                    getattr(self.cfg, "FLAT_LOOP_SEC", 0.25)
                )
                self.ib.sleep(loop_sec)
                if self.shares > 0:
                    self._sync_position_from_ib()
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
                    ai_exit_interval = float(getattr(self.cfg, "AI_EXIT_CHECK_SEC", 5.0))
                    if now - getattr(self, "_last_ai_exit_check", 0) >= ai_exit_interval:
                        self._last_ai_exit_check = now
                        try:
                            should_exit, ai_conf, ai_reason = self._ai_gate_exit(current_px)
                            if should_exit:
                                log.info(f"  🧠 AI EARLY EXIT: confidence={ai_conf:.0%} — {ai_reason[:80]}")
                                self._exit_position(current_px, "ai_early_exit")
                        except Exception:
                            pass
                
                # Check market state
                market_state = get_market_state(self.cfg)
                if self._last_market_state != market_state:
                    try:
                        self.account_evaluator.on_market_transition(
                            self, self._last_market_state or market_state, market_state,
                            self.notifier, self.ai_commander, self.autopilot,
                            self.consciousness, self.pilot,
                        )
                    except Exception as exc:
                        log.debug(f"Market transition eval: {exc}")
                    self._last_market_state = market_state
                can_trade = (
                    market_state == "open" or
                    (market_state == "pre_market" and self.cfg.ALLOW_PRE_MARKET_TRADING) or
                    (market_state == "after_hours" and self.cfg.ALLOW_AFTER_HOURS_TRADING)
                )
                
                if can_trade:
                    if self.conn.is_connected():
                        now = time.time()
                        time_since_scan = now - self._last_scan_time
                        
                        # FOCUS MODE: when targets are locked, do NOT rescan the full
                        # universe (87s blocking scan kills tick monitoring + entries).
                        # Only rescan after 30 min with no entry, or when flat with no targets.
                        have_targets = len(self._locked_targets) > 0
                        in_position = self.shares > 0

                        if in_position:
                            need_rescan = False
                        elif have_targets:
                            # Committed lock — never rescan universe while targets are set
                            need_rescan = False
                        else:
                            need_rescan = time_since_scan > 300
                        
                        if need_rescan:
                            self._scan_and_rank()
                            self._last_scan_time = time.time()
                            try:
                                if self.scan_results:
                                    best = self.scan_results[0]
                                    if isinstance(best, dict):
                                        bt, bs = best.get("ticker"), best.get("total_score", 0)
                                    else:
                                        bt, bs = best.ticker, best.rank_score
                                    buffer_append({
                                        "source": "scan_complete",
                                        "ticker": bt,
                                        "action": "SCAN_COMPLETE",
                                        "scan_score": bs,
                                        "confidence": 0.5,
                                        "features": [],
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    })
                            except Exception:
                                pass
                        
                        # Silent background watch on other locked targets while in position
                        if in_position and self._locked_targets:
                            if now - getattr(self, "_last_bg_watch", 0) >= float(
                                getattr(self.cfg, "BACKGROUND_WATCH_SEC", 45.0)
                            ):
                                self._last_bg_watch = now
                                self._silent_background_watch()

                        # SINGLE-FOCUS: millisecond heartbeat on locked targets every 2s
                        if self._locked_targets and self.shares == 0:
                            if now - getattr(self, '_last_fast_monitor', 0) > 1.0:
                                self._last_fast_monitor = now
                                self._fast_monitor_locked()
                            prefetch_iv = float(getattr(self.cfg, "LIVE_AI_PREFETCH_SEC", 1.0))
                            if now - getattr(self, "_last_ai_prefetch", 0) >= prefetch_iv:
                                self._last_ai_prefetch = now
                                try:
                                    self._prefetch_live_ai_hotline()
                                except Exception:
                                    pass
                            self._log_flat_heartbeat()
                        
                        # LIVE POSITION: sub-second monitoring + AI trail (never idle after entry)
                        if self.shares > 0:
                            current_px = self._latest_price()
                            if current_px > 0:
                                self._live_position_monitor(current_px)
                            elif int(now) % 10 == 0:
                                log.debug(
                                    f"No live price for {self.current_ticker} "
                                    f"(shares={self.shares:.0f}) — using cache on next tick"
                                )
                else:
                    if int(time.time()) % 60 == 0:
                        log.info(f"⏸ MARKET CLOSED ({market_state}) — training instead")
                    if time.time() - self._last_scan_time > 300:
                        self._last_scan_time = time.time()
                        self._train_off_hours()
                
                self._refresh_account_balance()
                self._write_live_metrics()
                self._maybe_daily_push()
                if getattr(self.cfg, "LEARNING_SYNC_INTERVAL_SEC", 1800) > 0:
                    sync_iv = float(getattr(self.cfg, "LEARNING_SYNC_INTERVAL_SEC", 1800))
                    if now - getattr(self, "_last_learning_push", 0) >= sync_iv:
                        self._last_learning_push = now
                        try:
                            push_learning_checkpoint("periodic")
                        except Exception:
                            pass
                
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self._shutdown()

    def _register_shutdown_signals(self):
        import signal
        def _handler(signum, _frame):
            log.info(f"Signal {signum} received — graceful shutdown...")
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
    
    def _scan_one(self, ticker: str, fast: bool = False) -> Optional[Dict]:
        """
        Scan one ticker. fast=True: 30min 1m bars only (HFT scan pass).
        Full pass adds MTF + AI scoring on refine phase.
        """
        if ticker in self._contract_blacklist:
            return None
        cfg_ticker = self.cfg.TICKER
        try:
            self.cfg.TICKER = ticker
            dm = DataManager(self.conn, self.cfg)

            duration = getattr(self.cfg, "SCAN_BAR_DURATION", "1800 S") if fast else "1 D"
            hist_1m = dm.fetch_historical(duration=duration, bar_size="1 min", use_rth=False, quiet=fast)

            df_5m = df_15m = None
            use_mtf = getattr(self.cfg, "USE_MULTI_TIMEFRAME_SCAN", True) and not fast
            if use_mtf:
                try:
                    df_5m = dm.fetch_historical(duration="1 D", bar_size="5 mins", use_rth=False, quiet=True)
                    df_15m = dm.fetch_historical(duration="1 D", bar_size="15 mins", use_rth=False, quiet=True)
                except Exception:
                    pass

            score = None
            if hist_1m is not None and len(hist_1m) >= 20:
                score = self._score_ticker(ticker, hist_1m)
                if score and score.get("total_score", 0) > 0 and use_mtf:
                    mtf_bonus, mtf_note = mtf_score_bonus(hist_1m, df_5m, df_15m)
                    score["total_score"] = round(score["total_score"] + mtf_bonus, 1)
                    if mtf_note:
                        score["reasons"] = f"{score.get('reasons', '')} | {mtf_note}".strip(" |")
                if score and score.get("total_score", 0) > 0 and not fast and not getattr(self.cfg, "AI_FULL_CONTROL", True):
                    ai_adjusted = self._ai_score_ticker(ticker, hist_1m, score["total_score"])
                    score["total_score"] = round(ai_adjusted, 1)
                    score["ai_score"] = round(ai_adjusted, 1)
                if score and score.get("total_score", 0) > 0:
                    self._scan_data_cache[ticker] = hist_1m

            if score and score.get("total_score", 0) > 0:
                log.debug(f"  ✅ {ticker}: score={score['total_score']:.1f} | {score.get('reasons', '')[:60]}")
            else:
                reason = score.get('reasons', 'no_data') if score else 'no_data'
                log.debug(f"  ❌ {ticker}: {reason}")

            return score if score and score.get("total_score", 0) > 0 else None
        except Exception as exc:
            msg = str(exc)
            if "Could not qualify" in msg or "No security definition" in msg:
                self._contract_blacklist.add(ticker)
                log.debug(f"  ⏭ {ticker}: blacklisted (no IB contract)")
            else:
                log.info(f"  ❌ {ticker}: SCAN ERROR — {exc}")
            return None
        finally:
            self.cfg.TICKER = cfg_ticker

    def _refine_scan_candidates(self, candidates: List[Dict]) -> List[Dict]:
        """Phase-2: MTF + AI refine on top candidates only (fast)."""
        refined = []
        for r in candidates:
            ticker = r["ticker"]
            full = self._scan_one(ticker, fast=False)
            if full:
                refined.append(full)
            else:
                refined.append(r)
        return refined
    
    def _scan_and_rank(self):
        t0 = time.perf_counter()
        screen_list = get_live_scan_universe(self.scanner, self.conn, self.cfg)
        if not screen_list:
            log.warning("⏸ Scan skipped — live IB scanner returned no tickers (no static fallback)")
            return
        fast = getattr(self.cfg, "FAST_SCAN_ENABLED", True)
        mode = "FAST" if fast else "FULL"
        log.info(f"🔍 HANOON SCAN START ({mode}): {len(screen_list)} tickers (live IB only)")
        results: List[Dict] = []
        
        scan_count = 0
        early_exit_n = int(getattr(self.cfg, "SCAN_EARLY_EXIT_QUALIFIED", 18))
        total = len(screen_list)
        for ticker in screen_list:
            scan_count += 1
            if scan_count == 1 or scan_count % 10 == 0 or scan_count == total:
                log.info(f"📊 Scan progress: {scan_count}/{total} tickers ({len(results)} qualified)")
            r = self._scan_one(ticker, fast=fast)
            if r:
                results.append(r)
            if fast and len(results) >= early_exit_n and scan_count >= 15:
                log.info(f"⚡ Early scan exit: {len(results)} qualified in {scan_count} tickers")
                break
        
        if fast and results:
            results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
            top_n = int(getattr(self.cfg, "SCAN_REFINE_TOP_N", 12))
            refine_pool = results[:top_n]
            log.info(f"🔬 Refining top {len(refine_pool)} with MTF + AI...")
            results = self._refine_scan_candidates(refine_pool) + results[top_n:]
        
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(f"Scan: {len(results)}/{scan_count} qualified in {elapsed_ms:.0f}ms")
        
        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            results = self.ai_commander.rank_scan_results(results)
        
        # Debug: log score distribution
        if results:
            scores = [r["total_score"] for r in results[:5]]
            log.debug(f"Score distribution: top5={scores}")
        
        # Lock only names that pass MTF+AI quality bar — no random low-score fills
        min_lock_score = float(getattr(self.cfg, "MIN_LOCK_SCORE", 30.0))
        min_candidates = int(getattr(self.cfg, "MIN_LOCK_CANDIDATES", 2))
        qualified = [r for r in results if r.get("total_score", 0) >= min_lock_score]

        if len(qualified) < min_candidates:
            top_hint = ""
            if results:
                t0 = results[0]
                top_hint = f" | best={t0['ticker']}@{t0.get('total_score', 0):.0f}"
            log.info(
                f"🔍 Lock skipped — {len(qualified)}/{min_candidates} names above "
                f"score {min_lock_score:.0f}{top_hint} (waiting for quality setups)"
            )
            self.top_pick = None
            self._locked_targets = []
            return
        
        self.scan_results = qualified[:5]

        # Lock top 1-5 by score; any liquid US stock (exclude OTC/pink only)
        max_price = getattr(self.cfg, "PENNY_STOCK_MAX_PRICE", 500.0)
        pool = [r for r in qualified if r["price"] <= max_price and is_tradeable_ticker(r["ticker"])]
        pool.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        locked = pool[:self.cfg.MAX_LOCKED_TARGETS]
        if not locked and qualified:
            locked = sorted(qualified, key=lambda x: x.get("total_score", 0), reverse=True)[:3]

        penny_results = locked
        
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
            self._targets_locked_at = time.time()
            self._focus_target_index = 0
            self._last_focus_rotate = 0.0
            names = ", ".join([p.ticker for p in self._locked_targets])
            log.info(f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names} | Scan: {elapsed_ms:.0f}ms")
            log.info(
                f"🔒 COMMITTED LOCK: scores≥{min_lock_score:.0f} | "
                f"no universe rescan until session clears"
            )
            self._generative_review_locks(penny_results)
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                send_dynamic_notification(
                    self.notifier, self.autopilot, "targets_locked",
                    self._notify_context({
                        "targets": names,
                        "top_score": self.top_pick.rank_score if self.top_pick else 0,
                        "scan_ms": elapsed_ms,
                    }),
                    f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names}\nTop score: {self.top_pick.rank_score:.0f}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            else:
                self.notifier.info(f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names}\nTop score: {self.top_pick.rank_score:.0f}")
            
            # Single tick stream on focus target (IB limits concurrent tick-by-tick)
            self._ensure_focus_stream()
            self._attempt_scan_bootstrap_entry()
            
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
    
    def _generative_review_locks(self, picks: List[Dict]):
        """AI ranks and comments on locked targets (human-style final watchlist)."""
        if not picks or not getattr(self.cfg, "GENERATIVE_THINKING_ENABLED", True):
            return
        summary = [
            f"{r['ticker']}@${r['price']:.2f} score={r.get('total_score', 0):.0f} ({r.get('reasons', '')[:40]})"
            for r in picks[:5]
        ]
        prompt = (
            "You are an expert momentum scalper with veteran intuition. "
            "I locked these stocks from the LIVE screener.\n"
            "Use computational scores AND gut feel — rank by what feels most tradeable NOW.\n"
            + "\n".join(summary) + "\n"
            'Reply JSON: {"ranked":["T1","T2",...],"gut_pick":"best gut feel ticker",'
            '"intuition":"why your gut agrees","commentary":"2-3 lines"}'
        )
        thought = generative_think(self.cfg, self.autopilot, prompt)
        if thought:
            log.info(f"🧠 AI watchlist: {thought[:400]}")
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                send_dynamic_notification(
                    self.notifier, self.autopilot, "system_status",
                    self._notify_context({
                        "locked": [r["ticker"] for r in picks],
                        "ai_review": thought[:200],
                    }),
                    f"🎯 AI LOCKED: {', '.join(r['ticker'] for r in picks)}\n{thought[:300]}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )

    def _focused_ticker(self) -> Optional[str]:
        """Pinned to top_pick — no rotation across locked names."""
        if getattr(self.cfg, "FOCUS_PIN_TOP_PICK", True) and self.top_pick:
            return self.top_pick.ticker
        if not self._locked_targets:
            return None
        idx = self._focus_target_index % len(self._locked_targets)
        return self._locked_targets[idx].ticker

    def _ensure_focus_stream(self, quiet: bool = False):
        """IB allows limited tick-by-tick streams — rotate one live stream across locked targets."""
        ticker = self._focused_ticker()
        if not ticker:
            return
        for t in list(self._target_monitors.keys()):
            if t != ticker:
                self._stop_target_stream(t)
        if ticker not in self._target_monitors:
            self._start_target_stream(ticker, quiet=quiet)

    def _start_target_stream(self, ticker: str, quiet: bool = False):
        """Start live tick stream for a locked target — millisecond heartbeat."""
        if ticker in self._target_monitors:
            return
        try:
            cfg = BotConfig(TICKER=ticker)
            dm = DataManager(self.conn, cfg)
            cached = self._scan_data_cache.get(ticker)
            if cached is not None and len(cached) > 0:
                dm.seed_buffer_from_dataframe(cached, n_bars=60)
            dm.start_tick_stream()
            self._target_monitors[ticker] = dm
            self._target_last_bar_count[ticker] = len(cached) if cached is not None else 0
            msg = f"  📡 LIVE STREAM started for {ticker} (focus, {self._target_last_bar_count[ticker]} bars)"
            (log.debug if quiet else log.info)(msg)
        except Exception as exc:
            log.warning(f"  Stream start failed for {ticker}: {exc}")

    def _log_flat_heartbeat(self):
        """One-line alive pulse while flat — confirms watch loop without stream spam."""
        if self.shares > 0 or not self._locked_targets:
            return
        now = time.time()
        pulse_sec = float(getattr(self.cfg, "FLAT_PULSE_SEC", 15.0))
        if now - self._last_flat_pulse < pulse_sec:
            return
        self._last_flat_pulse = now
        focus = self._focused_ticker() or "?"
        locked = ",".join(t.ticker for t in self._locked_targets[:5])
        nxt = self._next_best_pick.ticker if self._next_best_pick else "-"
        log.info(f"💓 WATCHING: focus={focus} | locked=[{locked}] | next_best={nxt}")

    def _detect_tick_volume_burst(self, dm: DataManager, df: pd.DataFrame) -> Tuple[bool, float]:
        """Detect volume burst from live tick prints vs recent 1min average."""
        ticks = list(getattr(dm, "_tick_buffer", []))
        if len(ticks) < 5:
            return False, 1.0
        recent_vol = sum(int(t.get("size", 0)) for t in ticks[-100:])
        avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 20 else 1.0
        if avg_vol <= 0:
            return False, 1.0
        ratio = recent_vol / avg_vol
        return ratio >= self.cfg.VOLUME_SPIKE_MIN_RATIO, ratio
    
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
    
    def _attempt_scan_bootstrap_entry(self):
        """Enter on scanner-confirmed momentum right after lock (don't wait for a new tick spike)."""
        if self.shares > 0 or not self._locked_targets:
            return
        pick = self._locked_targets[0]
        min_lock = float(getattr(self.cfg, "MIN_LOCK_SCORE", 30.0))
        if pick.rank_score < min_lock:
            return
        df = self._scan_data_cache.get(pick.ticker)
        if df is None or len(df) < 20:
            return
        is_spike, spike_ratio = self._detect_volume_spike(df)
        vol_ratio = float(df["volume"].tail(3).mean()) / (float(df["volume"].tail(20).mean()) + 1e-9)
        if not is_spike and vol_ratio >= 1.15:
            is_spike, spike_ratio = True, vol_ratio
        if not is_spike:
            return
        self.top_pick = pick
        log.info(f"📊 SCAN MOMENTUM: {pick.ticker} score={pick.rank_score:.0f} vol={spike_ratio:.1f}x")
        self._attempt_entry()

    def _refresh_locked_bars(self, quiet: bool = False):
        """Refresh 1min bars for locked targets so volume/uptrend checks stay current."""
        for target in self._locked_targets:
            ticker = target.ticker
            cfg_ticker = self.cfg.TICKER
            try:
                self.cfg.TICKER = ticker
                dm = DataManager(self.conn, self.cfg)
                fresh = dm.fetch_historical(
                    duration="1800 S", bar_size="1 min", use_rth=False, quiet=quiet,
                )
                if fresh is not None and len(fresh) >= 20:
                    self._scan_data_cache[ticker] = fresh
            except Exception:
                pass
            finally:
                self.cfg.TICKER = cfg_ticker

    def _silent_background_watch(self):
        """Rank other locked targets for next entry — no log noise while holding."""
        if self.shares <= 0 or len(self._locked_targets) < 2:
            return
        holding = self.current_ticker
        best: Optional[ScanResult] = None
        best_opp = 0.0
        cfg_ticker = self.cfg.TICKER
        try:
            for target in self._locked_targets:
                if target.ticker == holding:
                    continue
                ticker = target.ticker
                try:
                    self.cfg.TICKER = ticker
                    dm = DataManager(self.conn, self.cfg)
                    fresh = dm.fetch_historical(
                        duration="1800 S", bar_size="1 min", use_rth=False, quiet=True,
                    )
                    if fresh is None or len(fresh) < 20:
                        continue
                    self._scan_data_cache[ticker] = fresh
                    px = float(fresh["close"].iloc[-1])
                    if not _only_uptrend(fresh.tail(60), px):
                        continue
                    is_spike, vol = self._detect_volume_spike(fresh.tail(60))
                    opp = float(target.rank_score) * (vol if is_spike else 0.6)
                    if is_spike:
                        opp *= 1.4
                    if opp > best_opp:
                        best_opp = opp
                        best = target
                except Exception:
                    pass
            if best and best_opp > 0:
                self._next_best_pick = best
                self._next_best_score = best_opp
        finally:
            self.cfg.TICKER = cfg_ticker

    def _prefetch_live_ai_hotline(self):
        """Keep Ollama hotline ringing on locked watchlist — never blocks IB loop."""
        if not self.ai_commander or not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        targets = self._locked_targets or []
        top_n = int(getattr(self.cfg, "LIVE_AI_PREFETCH_TOP_N", 3))
        for target in targets[:top_n]:
            ticker = target.ticker if hasattr(target, "ticker") else target.get("ticker")
            if not ticker:
                continue
            df = self._scan_data_cache.get(ticker)
            if df is None or len(df) < 20:
                continue
            try:
                live_px = float(df["close"].iloc[-1])
                dm = self._target_monitors.get(ticker)
                if dm:
                    lp = dm.get_latest_price()
                    if lp and lp > 0:
                        live_px = float(lp)
                _, spike = self._detect_volume_spike(df)
                scan = target.rank_score if hasattr(target, "rank_score") else float(target.get("total_score", 0))
                bid, ask = self._get_bid_ask(ticker)
                spread = (ask - bid) / live_px if bid and ask and live_px > 0 else 0.0
                self.ai_commander.prefetch_entry_decision(
                    ticker, live_px, spike, scan,
                    market_ctx={
                        "bid": bid, "ask": ask, "spread_pct": spread,
                        "avg_volume": float(df["volume"].tail(20).mean()),
                        "recent_volume": float(df["volume"].iloc[-1]),
                    },
                )
            except Exception as exc:
                log.debug(f"Prefetch {ticker}: {exc}")

    def _fast_monitor_locked(self):
        """
        SINGLE-FOCUS execution: use cached 1min scan data + live tick price.
        Does NOT wait for 30+ minutes of live bar accumulation.
        """
        if not self._locked_targets or self.shares > 0:
            return
        if self._pending_entry_ticker and time.time() < self._pending_entry_until:
            return

        now = time.time()
        refresh_sec = float(getattr(self.cfg, "LOCK_BAR_REFRESH_SEC", 180.0))
        if now - getattr(self, '_last_bar_refresh', 0) > refresh_sec:
            self._last_bar_refresh = now
            self._refresh_locked_bars(quiet=True)

        # Pin live tick stream to top_pick — no rotation
        if getattr(self.cfg, "FOCUS_PIN_TOP_PICK", True) and self.top_pick:
            self._ensure_focus_stream(quiet=True)

        best_spike: Optional[Tuple[ScanResult, float, float, pd.DataFrame]] = None
        best_priority = 0.0

        for target in self._locked_targets[:self.cfg.MAX_LOCKED_TARGETS]:
            ticker = target.ticker
            df = self._scan_data_cache.get(ticker)
            if df is None or len(df) < 20:
                continue

            dm = self._target_monitors.get(ticker)
            live_px = dm.get_latest_price() if dm else None
            if not live_px or live_px <= 0:
                live_px = float(df["close"].iloc[-1])

            work_df = df.tail(60).copy()

            if not _only_uptrend(work_df, live_px):
                continue

            is_spike, spike_ratio = self._detect_volume_spike(work_df)
            min_spike = float(getattr(self.cfg, "LOCKED_SPIKE_MIN_RATIO", 1.15))
            if not is_spike and spike_ratio >= min_spike:
                is_spike, spike_ratio = True, spike_ratio

            focused = self._focused_ticker()
            if dm and ticker == focused:
                burst, burst_ratio = self._detect_tick_volume_burst(dm, work_df)
                if burst:
                    is_spike, spike_ratio = True, burst_ratio

            # Momentum breakout: price clearing recent high with elevated volume
            if not is_spike and len(work_df) >= 6:
                high5 = float(work_df["high"].tail(5).max())
                vol_ratio = float(work_df["volume"].tail(3).mean()) / (
                    float(work_df["volume"].tail(20).mean()) + 1e-9
                )
                if live_px > high5 * 1.001 and vol_ratio >= self.cfg.VOLUME_SPIKE_MIN_RATIO:
                    is_spike, spike_ratio = True, vol_ratio

            if not is_spike and target.rank_score >= 20:
                vol_ratio = float(work_df["volume"].tail(3).mean()) / (
                    float(work_df["volume"].tail(20).mean()) + 1e-9
                )
                if vol_ratio >= 1.15:
                    is_spike, spike_ratio = True, vol_ratio

            if not is_spike:
                continue

            priority = float(target.rank_score) * float(spike_ratio)
            if priority > best_priority:
                best_priority = priority
                best_spike = (target, live_px, spike_ratio, work_df)

        if best_spike is None:
            return

        target, live_px, spike_ratio, work_df = best_spike
        ticker = target.ticker
        if time.time() < self._spike_skip_until.get(ticker, 0):
            return
        if time.time() < self._entry_cooldown_until.get(ticker, 0):
            return

        self._scan_data_cache[ticker] = work_df
        self.top_pick = target
        log.info(f"⚡ SPIKE: {ticker} @ ${live_px:.2f} | vol={spike_ratio:.1f}x | score={target.rank_score:.0f} | attempting entry...")
        result = self._attempt_entry()
        if self.shares > 0:
            self._active_stream_ticker = ticker
            self._ensure_position_stream(ticker)
            return
        if result == 'permanent_skip':
            self._locked_targets = [t for t in self._locked_targets if t.ticker != ticker]
            self._stop_target_stream(ticker)
            if not self._locked_targets:
                self._last_scan_time = 0
                log.info("🔓 All locked targets cleared — will rescan universe")
            elif self.top_pick and self.top_pick.ticker == ticker:
                self.top_pick = self._locked_targets[0]
                self._ensure_focus_stream(quiet=True)
    
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
        threshold = getattr(self.cfg, "VOLUME_SPIKE_MIN_RATIO", 1.25)
        return spike_ratio >= threshold, spike_ratio
    
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
    
    def _live_position_monitor(self, current_px: float):
        """Continuous post-entry tracking: pulse log, AI manage, trail, exit."""
        if self.shares <= 0 or self._entry_price <= 0:
            return

        if current_px > self._position_peak:
            self._position_peak = current_px

        now = time.time()
        pulse_sec = float(getattr(self.cfg, "POSITION_PULSE_SEC", 5.0))
        if now - self._last_position_pulse >= pulse_sec:
            self._last_position_pulse = now
            pnl = (current_px - self._entry_price) * self.shares
            pnl_pct = ((current_px / self._entry_price) - 1) * 100
            pulse_ctx = {
                "ticker": self.current_ticker,
                "price": current_px,
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "stop": self._position_stop,
                "target": self._position_target,
                "peak": self._position_peak,
            }
            if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                self.ai_commander.ai_log("LIVE_PULSE", pulse_ctx)
            else:
                log.info(
                    f"📡 LIVE {self.current_ticker}: ${current_px:.4f} | "
                    f"P&L ${pnl:+.2f} ({pnl_pct:+.2f}%) | "
                    f"Stop ${self._position_stop:.4f} | TP ${self._position_target:.4f} | "
                    f"Peak ${self._position_peak:.4f}"
                )

        ai_sec = float(getattr(self.cfg, "AI_POSITION_MANAGE_SEC", 10.0))
        min_hold = float(getattr(self.cfg, "MIN_POSITION_HOLD_SEC", 45.0))
        opened = getattr(self, "_position_opened_at", 0.0)
        if now - self._last_ai_position_manage >= ai_sec:
            self._last_ai_position_manage = now
            if not opened or (now - opened) >= min_hold:
                self._ai_manage_position(current_px)

        self._update_trailing_stops(current_px)

        # Hard stop breach — always exit, bypasses min-hold
        stop_level = self._position_stop if self._position_stop > 0 else self._hard_stop_floor
        if stop_level > 0 and current_px <= stop_level:
            log.info(f"  🛑 STOP BREACH: ${current_px:.4f} <= ${stop_level:.4f}")
            self._exit_position(current_px, "stop_breach")
            self._active_stream_ticker = None
            return

        should_exit, exit_reason = self._should_exit_early(
            current_px, self._entry_price,
            (current_px - self._entry_price) * self.shares,
            50.0,
        )
        if should_exit:
            log.info(f"  ⚡ LIVE EXIT: {exit_reason}")
            self._exit_position(current_px, exit_reason)
            self._active_stream_ticker = None

    def _ai_manage_position(self, current_px: float):
        """Ollama + PPO full thinking on open position — dynamic stop/TP."""
        if self.shares <= 0 or not self.bracket_handle:
            return

        entry = self._entry_price
        pnl_usd = (current_px - entry) * self.shares
        pnl_pct = ((current_px / entry) - 1) * 100 if entry else 0

        vol_ratio = 1.0
        regime = "unknown"
        fast_df = None
        if self._active_stream_ticker and self._active_stream_ticker in self._target_monitors:
            fast_df = self._target_monitors[self._active_stream_ticker].get_bar_dataframe()
        if fast_df is None:
            fast_df = self._scan_data_cache.get(self.current_ticker or "")
        if fast_df is not None and len(fast_df) >= 10:
            _, vol_ratio = self._detect_volume_spike(fast_df)
            try:
                rr = self.regime_detector.classify(fast_df)
                if rr is not None:
                    raw = getattr(rr, "regime", "unknown")
                    regime = getattr(raw, "value", str(raw))
            except Exception:
                pass

        pos_ctx = {
            "ticker": self.current_ticker,
            "entry": entry,
            "price": current_px,
            "peak": self._position_peak,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop": self._position_stop,
            "target": self._position_target,
            "hard_floor": self._hard_stop_floor,
            "vol_ratio": round(vol_ratio, 2),
            "regime": str(regime),
        }
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            decision = self.ai_commander.decide_position_manage(pos_ctx)
        else:
            decision = generative_position_decision(self.cfg, self.autopilot, pos_ctx)

        action = decision.get("action", "HOLD")
        reason = decision.get("reason", "")
        new_stop = float(decision.get("stop", self._position_stop) or self._position_stop)
        new_target = float(decision.get("target", self._position_target) or self._position_target)

        widen_max = float(getattr(self.cfg, "VOLATILITY_STOP_WIDEN_MAX_PCT", 0.025))
        noise_floor = max(self._hard_stop_floor, entry * (1 - widen_max))

        if action == "EXIT":
            log.info(f"  🧠 AI EXIT: {reason}")
            self._exit_position(current_px, f"ai_position: {reason}")
            return

        if action == "WIDEN_STOP":
            new_stop = max(noise_floor, min(new_stop, self._position_stop))
            if new_stop < self._position_stop - 0.0001:
                self._apply_stop_update(new_stop, f"AI widen (noise cushion): {reason}")
        elif action == "TIGHTEN_STOP":
            new_stop = max(self._position_stop, min(new_stop, current_px * 0.999))
            if new_stop > self._position_stop + 0.0001:
                self._apply_stop_update(new_stop, f"AI tighten: {reason}")
        elif action == "RAISE_TP":
            new_target = max(self._position_target, new_target)
            if new_target > self._position_target + 0.0001:
                self._apply_target_update(new_target, f"AI raise TP: {reason}")

        # PPO reinforcement on every manage cycle
        try:
            if self.model and fast_df is not None and len(self._feature_buffer) >= self.cfg.WINDOW_SIZE:
                ai_exit, ai_conf, ai_reason = self._ai_gate_exit(current_px)
                if ai_exit and ai_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                    log.info(f"  🧠 PPO EXIT: conf={ai_conf:.0%} — {ai_reason[:80]}")
                    self._exit_position(current_px, f"ppo_exit: {ai_reason[:60]}")
        except Exception:
            pass

    def _apply_stop_update(self, new_stop: float, reason: str):
        if not self.bracket_handle or new_stop <= 0:
            return
        new_stop = round(new_stop, 4)
        if new_stop <= self._hard_stop_floor:
            new_stop = self._hard_stop_floor
        try:
            self.broker.update_stop_price(self.bracket_handle, new_stop)
            self._position_stop = new_stop
            if self.risk.plan:
                self.risk.plan.current_stop_price = new_stop
            log.info(f"  🛡️ STOP → ${new_stop:.4f} | {reason}")
        except Exception as exc:
            log.debug(f"Stop update failed: {exc}")

    def _apply_target_update(self, new_target: float, reason: str):
        if not self.bracket_handle or new_target <= 0:
            return
        new_target = round(new_target, 4)
        try:
            self.broker.update_target_price(self.bracket_handle, new_target)
            self._position_target = new_target
            if self.risk.plan:
                self.risk.plan.take_profit_price = new_target
            log.info(f"  🎯 TP → ${new_target:.4f} | {reason}")
        except Exception as exc:
            log.debug(f"Target update failed: {exc}")

    def _should_exit_early(self, current_px: float, entry_px: float, 
                           unrealized_pnl: float, risk_usd: float) -> Tuple[bool, str]:
        """
        Exit when profit gives back from peak, AI says exit, or slippage risk high.
        """
        if self.shares <= 0 or entry_px <= 0:
            return False, "no position"

        min_hold = float(getattr(self.cfg, "MIN_POSITION_HOLD_SEC", 45.0))
        opened = getattr(self, "_position_opened_at", 0.0)
        if opened and (time.time() - opened) < min_hold:
            return False, "hold (min hold)"
        
        pnl_pct = (current_px / entry_px) - 1
        peak_pct = (self._position_peak / entry_px) - 1 if self._position_peak > 0 else pnl_pct

        # Lock profit: exit if gave back >40% of peak gain
        if peak_pct > 0.015:
            giveback = peak_pct - pnl_pct
            if giveback > peak_pct * 0.4 and pnl_pct > 0.003:
                return True, f"profit_lock: peak +{peak_pct:.2%} now +{pnl_pct:.2%}"

        try:
            ai_exit, ai_conf, ai_reason = self._ai_gate_exit(current_px)
            if ai_exit and ai_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                return True, f"AI_exit: conf={ai_conf:.2f} | {ai_reason[:80]}"
        except Exception:
            pass
        
        try:
            fast_df = None
            if self._active_stream_ticker and self._active_stream_ticker in self._target_monitors:
                fast_df = self._target_monitors[self._active_stream_ticker].get_bar_dataframe()
            if fast_df is None and hasattr(self.data, 'get_bar_dataframe'):
                fast_df = self.data.get_bar_dataframe()
            if fast_df is not None and len(fast_df) >= 10:
                slippage = self._predict_slippage(fast_df, current_px)
                if slippage > 0.75 and pnl_pct > 0.005:
                    return True, f"slippage_risk: {slippage:.0%}"
                is_spike, _ = self._detect_volume_spike(fast_df)
                if not is_spike and pnl_pct > 0.012:
                    return True, f"wave_end: profit {pnl_pct:.2%} volume fading"
        except Exception:
            pass
        
        if unrealized_pnl > 0 and unrealized_pnl < 2.0 and risk_usd > 35 and pnl_pct < 0.008:
            return True, f"low_profit_high_risk: ${unrealized_pnl:.2f}"
        
        return False, "hold"
    
    def _update_trailing_stops(self, current_px: float):
        """Ratchet stop up on every tick; widen TP when momentum extends."""
        if self.shares <= 0 or self._entry_price <= 0 or not self.bracket_handle:
            return
        
        entry = self._entry_price
        pnl_pct = (current_px / entry) - 1
        peak_pct = (self._position_peak / entry) - 1 if self._position_peak > entry else pnl_pct

        if peak_pct <= 0:
            return

        # Trail ratio: tighter when AI wants exit, looser when momentum strong
        trail_ratio = 0.45
        try:
            ai_exit, ai_conf, _ = self._ai_gate_exit(current_px)
            if ai_exit and ai_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                trail_ratio = 0.25
            elif pnl_pct > 0.02:
                trail_ratio = 0.55
        except Exception:
            pass

        trail_stop = current_px - (entry * peak_pct * trail_ratio)
        trail_stop = max(trail_stop, self._hard_stop_floor, self._position_stop)

        if trail_stop > self._position_stop + 0.0001:
            self._apply_stop_update(trail_stop, f"trail locked +{peak_pct:.2%}")

        # Extend TP when price clears target with volume
        if current_px >= self._position_target * 0.98:
            extension = (current_px - entry) * 0.35
            new_tp = round(current_px + extension, 4)
            if new_tp > self._position_target + 0.0001:
                self._apply_target_update(new_tp, "momentum TP extension")
    
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
            vwap_hist = np.array([
                safe_vwap(typical[max(0, i - 19):i + 1], volumes[max(0, i - 19):i + 1])
                for i in range(19, len(typical))
            ])
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
        rule_result = {
            "ticker": ticker, "price": current_px, "volume": int(volumes[-1]),
            "avg_volume": int(vol_avg20), "rel_vol": round(vol_ratio, 2),
            "total_score": round(score, 1), "reasons": " | ".join(reasons[:3]) if reasons else "balanced",
            "ai_score": None,
        }
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            return self.ai_commander.score_ticker(ticker, df, hints=rule_result)
        return rule_result
    
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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

        if self.shares > 0:
            return 'waiting'
        now = time.time()
        if self._pending_entry_ticker and now < self._pending_entry_until:
            return 'waiting'
        if now < self._entry_cooldown_until.get(ticker, 0):
            return 'waiting'
        
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
            
            current_px = self._live_price_for(ticker, float(df_fast["close"].iloc[-1]))
            avg_volume = float(df_fast["volume"].tail(20).mean())
            bid, ask = self._get_bid_ask(ticker)
            spread_pct = (ask - bid) / current_px if bid and ask and current_px > 0 else 0.0
            market_ctx = {
                "bid": bid, "ask": ask, "spread_pct": spread_pct,
                "avg_volume": avg_volume,
                "recent_volume": float(df_fast["volume"].iloc[-1]),
            }

            if not _only_uptrend(df_fast, current_px):
                log.debug(f"Entry skip {ticker}: not uptrend")
                return 'waiting'
            
            is_spike, spike_ratio = self._detect_volume_spike(df_fast)
            vol_ratio = float(df_fast["volume"].tail(3).mean()) / (
                float(df_fast["volume"].tail(20).mean()) + 1e-9
            )
            if not is_spike and vol_ratio >= 1.15:
                is_spike, spike_ratio = True, vol_ratio
            if not is_spike:
                log.debug(f"Entry skip {ticker}: no volume spike (ratio={spike_ratio:.2f})")
                return 'waiting'
            
            self._ai_update_buffers(df_fast, current_px)
            scan_score = self.top_pick.rank_score if self.top_pick else 0.0
            self._last_spike_ratio = spike_ratio
            self._last_scan_score = scan_score
            self._last_market_ctx = market_ctx

            if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                obs = None
                if len(self._feature_buffer) >= self.cfg.WINDOW_SIZE:
                    window = np.array(list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:], dtype=np.float32).flatten()
                    total = self.bot_cash + self.shares * current_px
                    c_rat = self.bot_cash / (total + 1e-9)
                    p_rat = (self.shares * current_px) / (total + 1e-9) if self.shares > 0 else 0.0
                    obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
                bar_df = pd.DataFrame(self._bar_df_buffer) if self._bar_df_buffer else None
                ai_dec = self.ai_commander.decide_entry(
                    ticker, df_fast, current_px, spike_ratio, scan_score,
                    account={"equity": self.account_equity, "cash": self.bot_cash, "nav": self.bot_nav},
                    obs=obs, bar_df=bar_df, pilot=self.pilot, market_ctx=market_ctx,
                )
                if not ai_dec.get("enter"):
                    reason = (ai_dec.get('reason') or '')[:80]
                    log.info(f"  🧠 AI skip {ticker}: {reason}")
                    self._spike_skip_until[ticker] = time.time() + float(
                        getattr(self.cfg, "SPIKE_SKIP_SEC", 30.0)
                    )
                    return 'waiting'
                shares = int(ai_dec["shares"])
                stop_usd = float(ai_dec.get("risk_usd", 50.0))
                self._last_ai_confidence = float(ai_dec.get("confidence", 0.5))
            else:
                inst = self.institutional.scan()
                override, reason = self.institutional.should_override_buy()
                if override:
                    log.debug(f"Entry skip {ticker}: institutional override — {reason}")
                    return 'waiting'
                if self.autopilot:
                    allowed, cog_reason, _ = self.autopilot.should_trade(
                        self._build_ai_context(df_fast, current_px)
                    )
                    if not allowed:
                        log.debug(f"Entry skip {ticker}: cognitive — {cog_reason}")
                        return 'waiting'
                if self.cfg.USE_ENHANCED_AI and self.model is not None:
                    should_enter, ai_conf, ai_reason = self._ai_gate_entry(
                        ticker, current_px, spike_ratio=spike_ratio, scan_score=scan_score,
                    )
                    if not should_enter:
                        log.info(f"  🧠 AI gate skip {ticker}: conf={ai_conf:.0%} — {(ai_reason or '')[:80]}")
                        return 'waiting'
                deploy_usd = get_deploy_usd(self.cfg, self.pilot)
                shares = int(deploy_usd / current_px)
                if shares < 1:
                    log.debug(f"Entry skip {ticker}: shares={shares} < 1")
                    return 'waiting'
                stop_usd = 50.0
                stop_dist = stop_usd / shares
                stop_dist = max(stop_dist, current_px * self.cfg.SCALP_MIN_STOP_PCT)
                tp_dist = stop_dist * 2.5
                tp_dist = min(tp_dist, current_px * 0.05)
                ai_dec = {
                    "shares": shares,
                    "stop": round(current_px - stop_dist, 4),
                    "target": round(current_px + tp_dist, 4),
                    "risk_usd": stop_usd,
                }

            shares = int(ai_dec["shares"])
            if shares < 1:
                return 'waiting'

            current_px = self._live_price_for(ticker, current_px)
            shares = self._liquidity_cap_shares(shares, current_px, df_fast)
            shares = self._clamp_entry_shares(shares, current_px)
            if shares < 1:
                return 'waiting'

            spread_pct = (ask - bid) / current_px if bid and ask and current_px > 0 else 0.0
            max_spread = float(getattr(self.cfg, "MAX_ENTRY_SPREAD_PCT", 0.05))
            if spread_pct > max_spread:
                log.info(f"  ⏭ Skip {ticker}: spread {spread_pct:.1%} > {max_spread:.0%} (IB 2161 risk)")
                self._clear_pending_entry(ticker, cooldown_sec=60.0)
                return 'waiting'

            fail_cd = float(getattr(self.cfg, "ENTRY_FAILURE_COOLDOWN_SEC", 30.0))
            fill_wait = float(getattr(self.cfg, "ENTRY_FILL_WAIT_SEC", 1.0))
            max_wait = float(getattr(self.cfg, "ENTRY_FILL_MAX_WAIT_SEC", 30.0))
            fill_polls = max(5, int(max_wait / fill_wait))

            # One bracket per symbol — cancel any resting orders before submit
            n_cancelled = self.broker.cancel_open_orders_for_symbol(ticker)
            if n_cancelled:
                log.info(f"  🧹 Cleared {n_cancelled} stale {ticker} order(s) before entry")
            self._pending_entry_ticker = ticker
            self._pending_entry_until = now + 180.0

            # Start pilot flight tracking
            regime_result = self.regime_detector.classify(df_fast) if hasattr(self.regime_detector, 'classify') else None
            vix_level = 0.0
            try:
                ctx = summarize_market_context()
                vix_level = float(ctx.get('vix_level', 0.0))
            except Exception:
                pass
            self.pilot.start_flight(ticker, current_px, regime_result, 0.5, vix_level=vix_level)

            plan = TradePlan(
                side="LONG", entry_price=current_px, shares=float(shares),
                initial_stop_price=float(ai_dec["stop"]),
                take_profit_price=float(ai_dec["target"]),
                risk_usd=float(ai_dec.get("risk_usd", 50.0)),
                atr_at_entry=compute_atr(df_fast, period=5),
            )

            filled_shares = 0.0
            fill_px = current_px
            min_fill_ratio = float(getattr(self.cfg, "MIN_ENTRY_FILL_RATIO", 0.85))
            entry_parent_px = None
            entry_mode = "market"
            parent_trade = None
            last_ib_error = None

            for attempt in range(2):
                if attempt > 0:
                    cap = (last_ib_error or {}).get("price_cap")
                    if cap and cap > 0:
                        entry_parent_px = cap
                        entry_mode = "limit_ib_cap"
                    shares = max(1, shares // 2)
                    plan = TradePlan(
                        side="LONG", entry_price=current_px, shares=float(shares),
                        initial_stop_price=float(ai_dec["stop"]),
                        take_profit_price=float(ai_dec["target"]),
                        risk_usd=float(ai_dec.get("risk_usd", 50.0)),
                        atr_at_entry=plan.atr_at_entry,
                    )
                    log.info(f"  🔄 IB2161 retry: {shares} sh limit @ ${entry_parent_px:.4f}")
                else:
                    entry_parent_px, entry_mode = self.broker.decide_smart_entry(
                        current_px, bid, ask, shares, avg_volume,
                    )

                self.bracket_handle = self.broker.place_bracket_buy(
                    quantity=shares, limit_or_market_price=entry_parent_px,
                    stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                )
                self._pending_bracket_handle = self.bracket_handle
                mode_label = "MARKET" if entry_parent_px is None else f"LIMIT@${entry_parent_px:.4f}"
                log.info(f"  📥 Entry mode: {entry_mode} ({mode_label}) | {shares} sh @ ~${current_px:.4f}")

                filled_shares = 0.0
                parent_trade = getattr(self.bracket_handle, "parent_trade", None)
                parent_id = self.bracket_handle.parent_order_id
                cancelled = False
                for _ in range(fill_polls):
                    self.ib.sleep(fill_wait)
                    parent_trade = getattr(self.bracket_handle, "parent_trade", None)
                    parent_status = (
                        parent_trade.orderStatus.status
                        if parent_trade and parent_trade.orderStatus else "Unknown"
                    )
                    ierr = self.conn.pop_order_error(parent_id)
                    if ierr:
                        last_ib_error = ierr
                    if ierr and ierr.get("code") == 2161:
                        log.warning(
                            f"  IB 2161 regulatory cap on {ticker} — "
                            f"will retry smaller limit"
                        )
                    if parent_status in ("Cancelled", "Inactive", "ApiCancelled"):
                        cancelled = True
                        if (
                            attempt == 0
                            and getattr(self.cfg, "ENTRY_RETRY_ON_IB2161", True)
                            and (ierr or {}).get("code") == 2161
                        ):
                            self.broker.cancel_open_orders_for_symbol(ticker)
                            break
                        log.warning(f"Entry order rejected by IB ({parent_status}) — not opening position")
                        self.bracket_handle = None
                        self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
                        return 'waiting'
                    filled = float(parent_trade.orderStatus.filled) if parent_trade and parent_trade.orderStatus else 0.0
                    if filled > 0:
                        filled_shares = filled
                        avg = float(parent_trade.orderStatus.avgFillPrice or current_px)
                        if avg > 0:
                            fill_px = avg
                        if parent_status == "Filled" or filled >= shares * min_fill_ratio:
                            cancelled = False
                            break
                    if filled_shares < 1:
                        for p in self.ib.positions():
                            if getattr(p.contract, "symbol", "") == ticker and float(p.position) > 0:
                                pos_shares = float(p.position)
                                if pos_shares >= shares * min_fill_ratio:
                                    filled_shares = pos_shares
                                    avg_cost = float(getattr(p, "avgCost", 0) or 0)
                                    fill_px = avg_cost if avg_cost > 0 else current_px
                                    cancelled = False
                                    break

                if filled_shares >= shares * min_fill_ratio:
                    break
                if cancelled and attempt == 0 and getattr(self.cfg, "ENTRY_RETRY_ON_IB2161", True):
                    self.broker.cancel_open_orders_for_symbol(ticker)
                    self.bracket_handle = None
                    continue
                break

            if filled_shares < shares * min_fill_ratio:
                parent_status = (
                    parent_trade.orderStatus.status
                    if parent_trade and parent_trade.orderStatus else "Unknown"
                )
                if filled_shares >= 1:
                    log.warning(
                        f"Partial fill {int(filled_shares)}/{shares} below "
                        f"{min_fill_ratio:.0%} — flattening and skipping entry"
                    )
                    self.broker.flatten_position(
                        int(filled_shares), handle=self.bracket_handle,
                        urgent=True, symbol=ticker,
                    )
                    self.ib.sleep(0.5)
                elif parent_status in ("Submitted", "PreSubmitted", "PendingSubmit"):
                    log.info(f"Entry order pending for {ticker} ({parent_status}) — waiting for IB fill")
                else:
                    log.info(f"Entry not filled for {ticker} (status={parent_status})")
                self.broker.cancel_open_orders_for_symbol(ticker)
                self.bracket_handle = None
                self._pending_bracket_handle = None
                self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
                return 'waiting'

            shares = int(filled_shares)
            current_px = fill_px
            self._clear_pending_entry()

            cost = shares * current_px * (1 + self.cfg.TRANSACTION_COST_PCT)
            self.bot_cash -= cost
            self.shares = float(shares)
            self.bot_nav = self.bot_cash + self.shares * current_px
            self._entry_price = current_px
            self._prev_shares = self.shares
            self._position_opened_at = time.time()
            self._position_stop = plan.initial_stop_price
            self._position_target = plan.take_profit_price
            self._position_peak = current_px
            self._hard_stop_floor = plan.initial_stop_price
            self._last_position_pulse = 0.0
            self._last_ai_position_manage = 0.0
            self._ensure_position_stream(ticker)
            
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
            self.current_ticker = ticker
            log.info(f"🎯 ENTRY: {shares}x {ticker} @ ${current_px:.2f} | Stop ${plan.initial_stop_price:.2f} | TP ${plan.take_profit_price:.2f} | Deployed: ${cost:,.0f}")
            entry_ctx = {
                "ticker": ticker, "shares": shares, "entry": current_px,
                "stop": plan.initial_stop_price, "target": plan.take_profit_price,
                "pilot_level": self.pilot.state.level,
                "deployed": cost,
            }
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                send_dynamic_notification(
                    self.notifier, self.autopilot, "trade_opened",
                    self._notify_context(entry_ctx),
                    f"🎯 ENTRY {shares}x {ticker} @ ${current_px:.2f} | Stop ${plan.initial_stop_price:.2f} | TP ${plan.take_profit_price:.2f}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            else:
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

            try:
                buffer_append({
                    "source": "live_entry",
                    "ticker": ticker,
                    "action": "BUY",
                    "entry_price": current_px,
                    "shares": shares,
                    "stop": plan.initial_stop_price,
                    "target": plan.take_profit_price,
                    "confidence": getattr(self, "_last_ai_confidence", 0.5),
                    "features": snapshot_features(self._feature_buffer, self.cfg),
                    "spike_ratio": float(getattr(self, "_last_spike_ratio", 1.0)),
                    "scan_score": float(getattr(self, "_last_scan_score", 0)),
                    "spread_pct": float(getattr(self, "_last_market_ctx", {}).get("spread_pct", 0)),
                    "vol_ratio": float(
                        getattr(self, "_last_market_ctx", {}).get("recent_volume", 0)
                        / (getattr(self, "_last_market_ctx", {}).get("avg_volume", 1) + 1e-9)
                    ),
                    "cash_ratio": self.bot_cash / (self.bot_cash + self.shares * current_px + 1e-9),
                    "pos_ratio": (self.shares * current_px) / (self.bot_cash + self.shares * current_px + 1e-9) if self.shares > 0 else 0.0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass
            
            return 'entered'
        except Exception as exc:
            log.error(f"Entry error on {ticker}: {exc}")
            return 'waiting'
    
    def _build_ai_context(self, df: pd.DataFrame, current_px: float) -> Dict:
        """Build market context dict for cognitive autopilot decisions."""
        regime_label = "unknown"
        trend_strength = 0.5
        volatility = 0.5
        try:
            rr = self.regime_detector.classify(df)
            if rr is not None:
                raw_regime = getattr(rr, "regime", "unknown")
                regime_label = getattr(raw_regime, "value", str(raw_regime))
                trend_strength = abs(float(getattr(rr, "trend_strength", 0.0) or 0.0))
                vol_pct = float(getattr(rr, "volatility_percentile", 50.0) or 50.0)
                volatility = vol_pct / 100.0 if vol_pct > 1.0 else vol_pct
        except Exception:
            pass
        active = getattr(self, "_active_positions", [])
        return {
            "regime": str(regime_label).lower().replace("marketregime.", ""),
            "volatility": volatility,
            "trend_strength": max(trend_strength, 0.1),
            "desired_positions": len(active) + 1,
            "price": current_px,
        }

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
    
    def _ai_gate_entry(self, ticker: str, current_px: float,
                      spike_ratio: float = 1.0, scan_score: float = 0.0) -> Tuple[bool, float, str]:
        """
        Use full enhanced AI pipeline to decide if entry is justified.
        Strong technical setups (volume spike + scan score) can override uncertain AI.
        
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
                for_entry=True,
            )
            
            threshold = get_effective_confidence_threshold(self.cfg, self.pilot)
            should_enter = (action == 1 and confidence >= threshold)

            # Technical momentum override — also active under AI full control via ai_commander
            if not should_enter and action != 2:
                if spike_ratio >= 1.5 and scan_score >= 35:
                    should_enter = True
                    confidence = max(confidence, 0.55)
                    reasoning = (
                        f"Technical override: spike={spike_ratio:.1f}x score={scan_score:.0f} | "
                        f"{reasoning or 'momentum confirm'}"
                    )
                elif action == 1 and confidence >= threshold * 0.85 and spike_ratio >= 1.3:
                    should_enter = True
                    reasoning = f"Moderate AI+vol: conf={confidence:.0%} spike={spike_ratio:.1f}x"

            self._last_ai_confidence = confidence
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
                    self.consciousness.observe_scan({"source": "off_hours", "tickers": "live_ib"})
                    session = self.consciousness.continuous_train()
                    reflection = self.consciousness.reflect()
                    log.info(f"🧠 Consciousness reflection: {reflection[:200]}")
            except Exception as exc:
                log.debug(f"Consciousness training failed: {exc}")

            # Ollama meta-optimizer: AI proposes guarded param tweaks from performance
            try:
                if (
                    getattr(self.cfg, "OLLAMA_META_OPTIMIZER_ENABLED", True)
                    and self.autopilot
                    and getattr(self.autopilot, "core", None)
                    and getattr(self.autopilot.core, "ollama", None)
                ):
                    report = {
                        "win_rate": getattr(self.risk, "win_rate", 0.0),
                        "trades_today": self.trades_today,
                        "nav": self.bot_nav,
                        "pilot": self.pilot.get_veteran_status() if hasattr(self, "pilot") else {},
                    }
                    self.autopilot.core.ollama.meta_optimize(report, self.cfg)
                    log.info("🧬 Ollama meta-optimizer ran (guardrailed param proposals)")
            except Exception as exc:
                log.debug(f"Meta-optimizer: {exc}")
            
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
                sync_all_learning_artifacts(f"off_hours_{version}")
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
                sim_scores = [
                    (r.get("total_score", 0) if isinstance(r, dict) else r.rank_score)
                    for r in self.scan_results[:10]
                ]
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
                sync_all_learning_artifacts(f"weights_{version}")
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                max_score = max(
                    (r.get("total_score", 0) if isinstance(r, dict) else r.rank_score)
                    for r in self.scan_results[:3]
                )
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
            current_et = now_et()
            today_str = current_et.strftime("%Y-%m-%d")
            market_close_hour_et = 16
            if current_et.hour >= market_close_hour_et and self._last_daily_push_date != today_str:
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
                if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                    send_dynamic_notification(
                        self.notifier, self.autopilot, "daily_summary",
                        self._notify_context({"stmt": stmt, "guidelines": guidelines[:500]}),
                        f"📊 HANOON DAILY COMPLETE\n{stmt}\n\n{guidelines}",
                        ai_commander=self.ai_commander,
                        consciousness=self.consciousness,
                        pilot=self.pilot,
                    )
                else:
                    self.notifier.info(f"📊 HANOON DAILY COMPLETE\n{stmt}\n\n{guidelines}")
        except Exception as exc:
            log.debug(f"Daily push skipped: {exc}")
    
    def _write_init_report(self) -> str:
        """Write full initialization report and push to git."""
        try:
            from datetime import datetime
            import json
            os.makedirs("models/daily_reports", exist_ok=True)
            report_path = f"models/daily_reports/init_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            report = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "market_status": get_market_state(self.cfg),
            }
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            # Push to git (async, non-blocking)
            try:
                self._worker.submit_git_commit(
                    files=[report_path],
                    message=f"report: hanoon init {datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
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
            report_path = f"models/daily_reports/close_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            report = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                    message=f"report: hanoon close {datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                    push=False
                )
            except Exception:
                pass
            return report_path
        except Exception as exc:
            log.debug(f"Close report failed: {exc}")
            return "N/A"
    
    def _shutdown(self):
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        self._run_account_eval("session_shutdown", force=True)

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
        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, "session_close",
                self._notify_context({
                    "pnl": pnl, "pnl_pct": pnl_pct, "ib_change": ib_change,
                    "trades_today": self.trades_today, "report": str(report_path),
                }),
                summary,
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info(summary)

        try:
            push_full_shutdown_sync(self.bot_nav, pnl_pct, report_path or "")
        except Exception as exc:
            log.error(f"Shutdown git sync failed: {exc}")
            try:
                push_daily_summary(self.bot_nav, self.account_equity)
            except Exception:
                pass

        try:
            cleanup_local_workspace(aggressive=True)
        except Exception as exc:
            log.debug(f"Local cleanup: {exc}")

        if self.autopilot:
            try:
                self.autopilot.stop()
            except Exception:
                pass
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