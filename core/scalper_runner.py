#!/usr/bin/env python3
"""
core/scalper_runner.py — HANOON institutional algo-wave rider.

MATCHES USER MANUAL TRADING METHODOLOGY:
PRIMARY MISSION: FULL-TIME PROFIT HUNTING — AIs work all session to make money;
algo + council have full freedom to extract profit within hard risk guardrails.
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
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Any
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.market_hours import get_market_state, market_status_line, now_et, can_trade_now, is_extended_session, allowed_trading_sessions_label
from core.rth_session import (
    is_rth,
    rth_status_line,
    rth_tier,
    ai_session_context_block,
)

import numpy as np
import pandas as pd
import requests

from core.config import BotConfig
from core.ai_learning_policy import (
    learn_dont_block,
    failure_cooldown_sec,
    should_permanent_blacklist,
    is_ib_structural_reject,
    record_failure_for_learning,
)
from core.profit_hunting import (
    evaluate_spike_top_exit,
    evaluate_wave_end_on_spike_fade,
    check_missed_profit_hunt,
    record_profit_hunt_learning,
    teach_profit_hunt_lesson,
    track_profit_hunt_event,
    is_mechanical_profit_exit,
    mechanical_bypass_council,
    profit_exit_bypasses_council,
    profit_exit_bypasses_hold,
)
from core.market_data_learning import (
    is_market_data_blocked,
    filter_tradeable_tickers,
    record_fetch_failure,
    prompt_block as market_data_prompt_block,
    clear_transient_md_blocks,
)
from core.deferred_council_learning import deferred_learning_enabled
from core.capital_discipline import (
    allows_ppo_lead_while_pending,
    startup_log_line,
    passes_pre_entry_gate,
    check_entry_rate_limit,
    entry_cooldown_after_skip,
    capital_discipline_enabled,
    max_entries_per_hour,
)
from core.fast_execution import (
    ai_fast_execution,
    min_bars_for_ticker,
    stream_ticker_list,
    warm_ticker_list,
    should_spike_fast_entry,
    prefetch_per_loop,
    warm_budget_sec,
    prioritize_locked_targets,
    stream_priority_count,
    warm_priority_count,
    fast_monitor_interval,
    priority_tick_streams,
    max_spike_attempts_per_cycle,
    monitor_ticker_list,
    is_priority_ticker,
    focus_rotation_enabled,
    tick_stream_count,
    max_realtime_bar_streams,
    assign_stream_modes,
    main_loop_sec,
    council_max_wait_sec,
    entry_fill_poll_sec,
    ai_exit_check_sec,
    tick_spike_debounce_sec,
    tick_spike_monitor_enabled,
    background_watch_sec,
    spike_entry_cooldown_sec,
    entry_pending_block_sec,
    apply_micro_spike_boost,
    should_micro_fast_entry,
    skip_historical_prefetch,
)
from core.connector import IBConnector
from core.data import DataManager, coalesce_bars
from core.features_enhanced import FeatureEngineerEnhanced
from core.institutional import InstitutionalDetector, InstitutionalSignal
from core.scanner import StockScanner, ScanResult, ScannerHit, PENNY_STOCK_UNIVERSE, CONTRACT_BLACKLIST
from core.risk import RiskManager, TradePlan, compute_atr, compute_momentum_score, safe_vwap
from core.broker import BrokerExecutor, BracketHandle, parse_ib_order_block
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
from core.git_sync import init as git_sync_init, push_trade, push_daily_summary, push_model_release, sync_all_learning_artifacts, push_full_shutdown_sync, push_learning_checkpoint_async
from core.local_cleanup import cleanup_local_workspace, run_periodic_cleanup
from core.async_utils import get_background_worker, AtomicFileWriter
from core.feature_drift import validate_features_at_startup
from core.train_subprocess import launch_training
from core.pilot_mode import (
    get_live_scan_universe, get_effective_confidence_threshold, get_deploy_usd,
    get_ai_deploy_budget, get_trade_risk_usd,
    is_ai_unlimited, is_ai_council_mode, effective_max_locked_targets, effective_max_concurrent_positions,
    effective_min_lock_score, effective_min_lock_candidates,
    effective_min_cash_reserve_pct, effective_max_shares_per_trade,
    effective_prefetch_top_n, effective_min_position_hold_sec, effective_min_hold_for_exit,
    snapshot_features, send_dynamic_notification, observe_trade_everywhere,
    maybe_incremental_train, mtf_score_bonus, is_tradeable_ticker, generative_think,
    generative_position_decision, ai_full_capital_access,
)
from core.ai_commander import AICommander
from core.bracket_validator import validate_decision_bracket, adapt_bracket_to_fill
from core.shadow_mode import ShadowCircuitBreaker
from core.trade_telemetry import (
    log_bracket_reject, log_entry_execution, log_exit_postmortem,
    log_post_fill_adapt, log_regime_atr_outcome, log_round_trip_fills, regime_tag,
)
from core.fill_tracker import (
    append_fill_ledger, build_round_trip_record, resolve_entry_fill,
    resolve_exit_fill,
)
from core.fill_reconciler import (
    PendingClose, build_close_record, snapshot_slot,
)
from core.reward_shaping import reward_from_bracket_reject, reward_from_trade
from core.account_evaluator import AccountEvaluator
from core.ai_session_limits import (
    bootstrap_ai_session_limits, format_limits_log, maybe_refresh_session_limits,
    should_ai_define_limits,
)
from core.telegram_listener import TelegramCommandListener
from core.commander_learning import load_commander_guidance, run_commander_learning_cycle
from core.ai_guardrails import build_ppo_observation
from core.ai_runtime_observer import get_runtime_observer
from core.scalper_micro_predict import bars_with_live_tick, micro_forecast


def _only_uptrend(df: pd.DataFrame, current_px: float, min_bars: int = 20) -> bool:
    """
    USER METHODOLOGY: Uptrend filter — must be loose enough to catch
    institutional algo waves early, not late.
    """
    if len(df) < min_bars:
        return False
    n = min(len(df), 20)
    closes = df["close"].values[-n:]
    volumes = df["volume"].values[-n:]
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
        try:
            from core.commander_learning import reload_persisted_params
            n = reload_persisted_params(cfg)
            if n:
                log.info(f"🧬 Restored {n} persisted learning param(s) from prior sessions")
            from core.sniper_execution import cap_sniper_confidence_threshold
            if cap_sniper_confidence_threshold(cfg):
                log.info(
                    f"🎯 Sniper cap: CONFIDENCE_THRESHOLD → "
                    f"{float(getattr(cfg, 'CONFIDENCE_THRESHOLD', 0.55)):.2f}"
                )
        except Exception as exc:
            log.debug(f"Persisted param reload: {exc}")
        self.conn.register_market_data_error_handler(self._on_market_data_failure)
        self.conn.register_tick_limit_handler(self._on_tick_stream_limit)
        self.conn.register_session_reclaim_handler(self._on_ib_session_reclaim)
        self.conn.register_connectivity_handler(self._on_ib_connectivity)
        self._tick_limit_denied: set = set()
        self._ib_connectivity_paused: bool = False

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
        self._position_slots: Dict[str, Dict[str, Any]] = {}
        self._bracket_by_ticker: Dict[str, BracketHandle] = {}
        
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
        self._last_pulse_price: float = 0.0
        self._last_price_change_at: float = 0.0
        self._last_price_snapshot_at: float = 0.0
        self._last_pulse_fingerprint: str = ""
        self._last_stagnation_decision: Dict[str, Any] = {}
        self._pending_entry_ticker: Optional[str] = None
        self._pending_brackets_by_ticker: Dict[str, BracketHandle] = {}
        self._pending_entry_until: float = 0.0
        self._entry_poll_states: Dict[str, Dict[str, Any]] = {}
        self._ai_councils: Dict[str, Dict[str, Any]] = {}
        self._entry_cooldown_until: Dict[str, float] = {}
        self._short_warned: set = set()
        self._last_bg_watch: float = 0.0
        self._next_best_pick: Optional[ScanResult] = None
        self._next_best_score: float = 0.0
        self._spike_skip_until: Dict[str, float] = {}
        self._spike_attempt_until: Dict[str, float] = {}
        self._lock_spike_touch_at: Dict[str, float] = {}
        self._last_soft_rotate: float = 0.0
        self._last_merge_scan: float = 0.0
        self._soft_merge_due: bool = False
        self._mtf_bar_cache: Dict[str, Tuple[float, Any, Any]] = {}
        self._profit_hunt_spike_peak: float = 0.0
        self._profit_hunt_spike_at: float = 0.0
        self._profit_hunt_spike_ctx: Dict[str, Any] = {}
        self._profit_hunt_missed_logged: bool = False
        self._profit_ride_started_at: float = 0.0
        self._was_in_profit: bool = False
        self._last_flat_pulse: float = 0.0
        self.top_pick: Optional[ScanResult] = None
        self._locked_targets: List[ScanResult] = []
        self._targets_locked_at: float = 0.0
        self._focus_target_index: int = 0
        self._last_focus_rotate: float = 0.0
        self._last_entry_attempt_at: float = 0.0
        self._contract_blacklist: set = (
            set() if learn_dont_block(cfg) else set(CONTRACT_BLACKLIST)
        )
        self._ib_structural_reject_count: int = 0
        self._last_scan_time: float = 0.0
        self._last_metrics_write: float = 0.0
        self._last_ai_narrative: float = 0.0
        self._loss_learning_in_flight: bool = False
        self._scan_data_cache: Dict[str, pd.DataFrame] = {}
        self._scan_cache_max_tickers = int(os.getenv("SCAN_CACHE_MAX_TICKERS", "14"))
        self._scan_cache_max_bars = int(os.getenv("SCAN_CACHE_MAX_BARS", "150"))  # Cache scanned data
        self._bar_prefetch_queue: List[str] = []
        self._tick_spike_pending: Dict[str, Dict[str, Any]] = {}
        self._tick_spike_last_at: Dict[str, float] = {}
        self._tick_exit_last_at: Dict[str, float] = {}
        
        # Live stream monitors for locked targets (heartbeat in milliseconds)
        self._target_monitors: Dict[str, DataManager] = {}      # ticker -> DataManager
        self._stream_modes: Dict[str, str] = {}                 # ticker -> tick | realtime
        self._target_last_bar_count: Dict[str, int] = {}        # ticker -> last seen bar count
        self._active_stream_ticker: Optional[str] = None        # Currently streaming ticker
        self._risk_plans: Dict[str, TradePlan] = {}             # ticker -> active risk plan
        
        self.trade_journal: List[Dict] = []
        self._trade_journal_max = int(os.getenv("TRADE_JOURNAL_MAX", "500"))
        self.trades_today: int = 0
        self._current_day: Optional[str] = None
        self._last_daily_push_date: Optional[str] = None
        self._last_market_state: Optional[str] = None
        self._last_market_closed_log: float = 0.0
        self._day_session_ended: bool = False
        self._rth_open_day: Optional[str] = None
        self._entries_this_hour: int = 0
        self._smart_gate_context: Dict[str, Dict[str, Any]] = {}
        self._hour_window_start: float = time.time()
        self._last_quality_watch_log: float = 0.0
        self._pending_closes: Dict[str, PendingClose] = {}
        self._pending_lottery_meta: Dict[str, Dict[str, Any]] = {}
        self._deferred_exits: Dict[str, Dict[str, Any]] = {}
        self._last_off_hours_train: float = 0.0
        self._last_learning_push: float = 0.0
        self._weights_file = "models/scalper_weights.json"
        self._weights_mtime = 0.0

        # Experience buffer for unified learning
        self._xp_buffer_initialized = False
        self.shadow_circuit = ShadowCircuitBreaker(cfg)
        self._last_micro_forecast: Dict[str, Any] = {}
        self._bar_warm_due = False
        self._bar_warm_idx = 0
        self._stream_repair: Dict[str, str] = {}
        self._md_suspended: bool = False
        self._bootstrap_entry_due = False
        self._lock_review_due = False
        self._lock_review_picks: List[Dict] = []
        self._shutdown_requested_flag = False
        self._last_council_backlog_log: float = 0.0
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
        except Exception as exc:
            log.debug(f"Cognitive autopilot init skipped: {exc}")

        self.ai_commander = AICommander(
            self.cfg, self.autopilot, self.consciousness, self.model, self.ai_components,
        )
        if self.model is not None:
            self.ai_commander.bind_ppo_model(self.model)
        self.notifier.attach_ai_brain(
            ai_commander=self.ai_commander,
            autopilot=self.autopilot,
            consciousness=self.consciousness,
            pilot=self.pilot,
        )
        self.account_evaluator = AccountEvaluator(self.cfg)
        self.runtime_observer = get_runtime_observer(self.cfg)
        self.runtime_observer.attach(self)
        self._telegram_listener: Optional[TelegramCommandListener] = None
        self._init_telegram_listener()
    
    def _init_telegram_listener(self) -> None:
        """Start inbound Telegram copilot (verify-any-account)."""
        if not getattr(self.cfg, "TELEGRAM_LISTEN_ENABLED", True):
            return
        try:
            ensure_vision_model(self.cfg, background=True)
            def _think(prompt: str) -> str:
                if self.ai_commander:
                    return self.ai_commander.compose_telegram(prompt)
                return generative_think(self.cfg, self.autopilot, prompt)

            def _vision(prompt: str, image_bytes: bytes) -> str:
                ollama = None
                if self.autopilot and getattr(self.autopilot, "core", None):
                    ollama = getattr(self.autopilot.core, "ollama", None)
                if ollama and hasattr(ollama, "analyze_image"):
                    text = ollama.analyze_image(prompt, image_bytes)
                    if text:
                        return text
                from core.council_brain import CouncilBrain
                brain = CouncilBrain(self.cfg)
                return brain.analyze_image(prompt, image_bytes) or "Vision model unavailable."

            self._telegram_listener = TelegramCommandListener(
                self.cfg,
                runner=self,
                ai_commander=self.ai_commander,
                think_fn=_think,
                vision_fn=_vision,
            )
            self._telegram_listener.start()
            try:
                from core.telegram_broadcast import broadcast_ops, register_listener
                register_listener(self._telegram_listener)
                from core.ai_telegram import register_global_composer
                if self.notifier._ai_composer:
                    register_global_composer(self.notifier._ai_composer)
            except Exception:
                pass
        except Exception as exc:
            log.debug(f"Telegram listener init skipped: {exc}")
    
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
        """Run feature pipeline validation (deferred so IB connect is not blocked)."""
        def _run():
            try:
                from core.features_enhanced import FeatureEngineerEnhanced
                fe = FeatureEngineerEnhanced()

                def feature_fn(df, window_size=30):
                    try:
                        return fe.compute_features(df, window_size=window_size)
                    except Exception:
                        n = min(window_size, len(df))
                        return np.zeros((n, 18), dtype=np.float32)

                ok = validate_features_at_startup(feature_fn)
                if not ok:
                    log.error(
                        "Feature validation failed — this would cause trading errors. "
                        "Please fix before continuing."
                    )
            except Exception as exc:
                log.debug(f"Feature validation skipped: {exc}")

        if getattr(self.cfg, "DEFER_FEATURE_VALIDATION", True):
            threading.Thread(target=_run, daemon=True, name="feature-validate").start()
        else:
            _run()
    
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
            from core.startup_log import sinfo
            sinfo(self.cfg, f"🧠 PPO model ready: fresh={self._model_fresh}")
        except Exception as exc:
            log.warning(f"PPO model init failed ({exc.__class__.__name__}: {exc}) — will use fresh model")
            try:
                dummy_f = np.zeros((self.cfg.WINDOW_SIZE + 2, self.cfg.N_FEATURES), np.float32)
                dummy_px = np.ones(self.cfg.WINDOW_SIZE + 2, np.float32) * 100.0
                dummy_env = TradingEnv(dummy_f, dummy_px, self.cfg.INITIAL_CASH,
                                       self.cfg.TRANSACTION_COST_PCT, self.cfg.WINDOW_SIZE, self.cfg.DEFAULT_MAX_POSITION_PCT)
                self.model = build_ppo_agent(dummy_env, self.cfg, None)
                sinfo(self.cfg, "🧠 Fresh PPO model initialized (18-feature architecture)")
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
            if self._ib_starting_balance is None and self.account_equity > 0:
                self._ib_starting_balance = self.account_equity
                self.cfg.INITIAL_CASH = self.account_equity
                if ai_full_capital_access(self.cfg):
                    self.bot_cash = float(self.available_cash or self.account_equity)
                    self.bot_nav = self.account_equity
            self.cfg._latest_account_balance = self.account_equity
        except Exception as exc:
            log.debug(f"Could not fetch IB account balance: {exc}")
        self.bot_nav = self.bot_cash + self.shares * self._latest_price()

    def _deployable_cash(self) -> float:
        """Cash for new entries — war settled cash when war account enabled."""
        try:
            from core.war_account import war_account_enabled, war_settled_cash
            if war_account_enabled(self.cfg):
                return max(0.0, war_settled_cash(self.cfg))
        except Exception:
            pass
        ib_cash = float(self.available_cash if self.available_cash is not None else self.account_equity)
        if ai_full_capital_access(self.cfg):
            return max(0.0, ib_cash)
        return max(0.0, float(self.bot_cash))

    def _war_account_equity(self) -> float:
        try:
            from core.war_account import war_account_enabled, war_effective_equity
            if war_account_enabled(self.cfg):
                eq = war_effective_equity(self.cfg)
                if eq > 0:
                    return eq
        except Exception:
            pass
        return float(self.account_equity)

    def _resolve_live_bars(
        self,
        ticker: str,
        min_bars: Optional[int] = None,
    ) -> Tuple[Optional[pd.DataFrame], float, Optional[DataManager], Dict[str, Any]]:
        """
        Freshest 1-min bars (live stream + forming tick bar) and micro forecast.
        Used for spike monitor, entry, profit exit, and loss exit.
        """
        min_b = min_bars if min_bars is not None else self._min_bars_for(ticker)
        dm = self._target_monitors.get(ticker)
        df: Optional[pd.DataFrame] = None
        live_px = 0.0

        if dm and getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
            df = dm.get_live_decision_bars(min_bars=min_b)
            live_px = float(dm.get_latest_price() or 0)
            if (df is None or len(df) < min_b) and live_px > 0:
                partial = dm.get_live_decision_bars(min_bars=1)
                if partial is not None and len(partial) > 0:
                    df = partial

        if df is None or len(df) < min_b:
            cached = self._scan_data_cache.get(ticker)
            if cached is not None and len(cached) >= max(3, min_b // 2):
                df = cached
                if live_px <= 0:
                    live_px = float(df["close"].iloc[-1])
                if live_px > 0 and getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True):
                    df = bars_with_live_tick(df, live_px, dm)

        if df is not None and len(df) >= 3:
            if live_px <= 0:
                live_px = float(df["close"].iloc[-1])
            elif getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True):
                df = bars_with_live_tick(df, live_px, dm)
            self._store_scan_cache(ticker, df)

        forecast: Dict[str, Any] = {}
        if (
            getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True)
            and df is not None
            and len(df) >= 6
            and live_px > 0
        ):
            forecast = micro_forecast(df, live_px, dm)
            self._last_micro_forecast[ticker] = forecast

        return df, live_px, dm, forecast

    def _stream_has_price(self, ticker: str) -> bool:
        """True when live stream or cache already has a usable price for ticker."""
        dm = self._target_monitors.get(ticker)
        if dm:
            px = dm.get_latest_price()
            if px and px > 0:
                return True
        cached = self._scan_data_cache.get(ticker)
        if cached is not None and len(cached) > 0:
            px = float(cached["close"].iloc[-1])
            return px > 0
        return False

    def _heal_stale_stream_prices(self, now: float) -> None:
        """IB snapshot when 5s streams are up but prices never arrived."""
        if not self._locked_targets:
            return
        heal_iv = float(getattr(self.cfg, "STREAM_PRICE_HEAL_SEC", 20.0))
        if now - getattr(self, "_last_stream_heal", 0) < heal_iv:
            return
        need_heal = [
            t.ticker for t in self._locked_targets[: self._max_locked()]
            if t.ticker in self._target_monitors and not self._stream_has_price(t.ticker)
        ]
        if not need_heal:
            return
        healed = 0
        for ticker in need_heal[:4]:
            px = self._force_price_snapshot(ticker)
            if px <= 0:
                continue
            healed += 1
            dm = self._target_monitors.get(ticker)
            if dm:
                dm.last_tick_price = float(px)
            for target in self._locked_targets:
                if target.ticker == ticker and target.price <= 0:
                    target.price = float(px)
            cached = self._scan_data_cache.get(ticker)
            if cached is None or len(cached) == 0:
                now_ts = pd.Timestamp.utcnow().floor("1min")
                self._store_scan_cache(ticker, pd.DataFrame(
                    [{
                        "open": px, "high": px, "low": px, "close": px, "volume": 0,
                    }],
                    index=[now_ts],
                ))
        if healed:
            self._last_stream_heal = now
            log.info(f"  💉 Stream heal: IB snapshot priced {healed} ticker(s)")

    def _bars_from_stream(self, ticker: str, need: int) -> Optional[pd.DataFrame]:
        """Use live tick/5s stream bars — no IB HMDS historical request."""
        dm = self._target_monitors.get(ticker)
        if dm is None:
            return None
        df = dm.get_live_decision_bars(min_bars=need)
        if df is None or len(df) == 0:
            df = dm.get_live_decision_bars(min_bars=1)
        if df is None or len(df) == 0:
            return None
        min_ok = max(3, need // 2)
        soft_hmds = bool(getattr(self.cfg, "MD_SOFT_FAIL_HMDS", True))
        has_live_px = bool(dm.get_latest_price() and dm.get_latest_price() > 0)
        if len(df) < min_ok and not (soft_hmds and has_live_px):
            return None
        self._store_scan_cache(ticker, df)
        if df["close"].iloc[-1] > 0:
            for target in self._locked_targets:
                if target.ticker == ticker and target.price <= 0:
                    target.price = float(df["close"].iloc[-1])
        return df

    def _on_locked_stream_tick(self, ticker: str, price: float, _ts: Any) -> None:
        """Tick callback — queue spike entry or fast profit/loss exit (debounced)."""
        if price <= 0 or not tick_spike_monitor_enabled(self.cfg):
            return
        now = time.time()
        debounce = tick_spike_debounce_sec(self.cfg)
        if now - self._tick_spike_last_at.get(ticker, 0) < debounce:
            return
        self._tick_spike_last_at[ticker] = now

        if ticker in self._held_tickers():
            if now - self._tick_exit_last_at.get(ticker, 0) < debounce:
                return
            self._tick_exit_last_at[ticker] = now
            self._service_tick_position_exit(ticker, float(price))
            return

        if ticker == self._pending_entry_ticker:
            return
        if not any(t.ticker == ticker for t in self._locked_targets):
            return
        if self._open_position_count() >= self._max_concurrent():
            return
        if now < self._spike_attempt_until.get(ticker, 0):
            return

        min_bars = self._min_bars_for(ticker)
        df, live_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=min_bars)
        if df is None or len(df) < max(3, min_bars // 2):
            return

        is_spike, ratio = self._detect_volume_spike(df, min_period=min(20, max(6, min_bars)))
        is_spike, ratio = apply_micro_spike_boost(
            is_spike, ratio, forecast, cfg=self.cfg,
        )
        if not is_spike and dm:
            burst, br = self._detect_tick_volume_burst(dm, df)
            if burst:
                is_spike, ratio = True, br
        if not is_spike:
            return

        target = next((t for t in self._locked_targets if t.ticker == ticker), None)
        if target is None:
            return
        self._tick_spike_pending[ticker] = {
            "target": target,
            "ratio": ratio,
            "px": float(price or live_px),
            "forecast": forecast,
            "at": now,
        }

    def _service_tick_spike_queue(self) -> None:
        """Drain tick-triggered spike entries (runs on main loop thread)."""
        if not self._tick_spike_pending or self.risk.is_halted():
            return
        now = time.time()
        for ticker, pkt in sorted(
            self._tick_spike_pending.items(),
            key=lambda x: float(x[1].get("ratio", 0)),
            reverse=True,
        ):
            if now - float(pkt.get("at", 0)) > 3.0:
                self._tick_spike_pending.pop(ticker, None)
                continue
            if ticker in self._held_tickers():
                self._tick_spike_pending.pop(ticker, None)
                continue
            if self._pending_entry_ticker and self._pending_entry_ticker != ticker:
                continue
            if now < self._spike_attempt_until.get(ticker, 0):
                self._tick_spike_pending.pop(ticker, None)
                continue

            target = pkt["target"]
            ratio = float(pkt.get("ratio", 1.0))
            px = float(pkt.get("px", 0))
            fc = pkt.get("forecast") or {}
            self._tick_spike_pending.pop(ticker, None)
            self.top_pick = target
            self._last_entry_attempt_at = now
            self._spike_attempt_until[ticker] = now + spike_entry_cooldown_sec(self.cfg)
            log.info(
                f"⚡ TICK SPIKE: {ticker} @ ${px:.2f} | vol={ratio:.1f}x | "
                f"micro={fc.get('spike_likelihood', 0):.0%} pred→${(fc.get('pred_1bar') or px):.2f}"
            )
            result = self._attempt_entry()
            if result in ("entered", "waiting") or ticker in self._held_tickers():
                return
            break

    def _service_tick_position_exit(self, ticker: str, price: float) -> None:
        """Sub-second micro profit/loss exit on tick (mechanical, no council wait)."""
        can_trade, _ = can_trade_now(self.cfg)
        if not can_trade:
            return
        if not getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True):
            return
        if not self._load_position_context(ticker):
            return
        if price > self._position_peak:
            self._position_peak = price
            if self.risk.plan:
                self.risk.plan.peak_price = max(self.risk.plan.peak_price, price)

        entry_px = self._entry_price
        if entry_px <= 0 or self.shares <= 0:
            return
        pnl_pct = (price / entry_px) - 1.0
        min_hold = effective_min_hold_for_exit(self.cfg, pnl_pct)
        opened = getattr(self, "_position_opened_at", 0.0)
        if min_hold > 0 and opened and (time.time() - opened) < min_hold:
            return

        _, _, _, forecast = self._resolve_live_bars(ticker, min_bars=6)
        fade_thr = float(getattr(self.cfg, "MICRO_FADE_EXIT", 0.55))
        loss_thr = float(getattr(self.cfg, "MICRO_LOSS_EXIT", 0.58))

        if (
            pnl_pct > float(getattr(self.cfg, "IN_PROFIT_MANAGE_PNL_PCT", 0.003))
            and forecast.get("fade_risk", 0) >= fade_thr
            and forecast.get("dir", 0) <= 0
        ):
            if self._execute_mechanical_profit_exit(
                price, f"tick_micro_fade:{forecast.get('fade_risk', 0):.2f}", defer=True,
            ):
                self._save_position_context(ticker)
                return

        if pnl_pct < -0.002 and forecast.get("loss_pressure", 0) >= loss_thr and forecast.get("dir", 0) < 0:
            log.info(
                f"  ⚡ TICK LOSS EXIT {ticker}: ${price:.4f} | "
                f"pressure={forecast.get('loss_pressure', 0):.2f}"
            )
            self._exit_position(price, "tick_micro_loss", ticker=ticker, defer=True)
            self._save_position_context(ticker)

    def _any_position_in_profit(self, threshold_pct: float = 0.003) -> bool:
        for t, slot in self._position_slots.items():
            entry = float(slot.get("entry_price", 0) or 0)
            if entry <= 0:
                continue
            px = self._live_price_for(t, entry)
            if px > entry * (1.0 + threshold_pct):
                return True
        return False

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
            self.ib.sleep(0.12)
            bid = float(ticks.bid) if ticks.bid and ticks.bid > 0 else None
            ask = float(ticks.ask) if ticks.ask and ticks.ask > 0 else None
            self.ib.cancelMktData(contract)
            return bid, ask
        except Exception as exc:
            log.debug(f"Bid/ask snapshot {ticker}: {exc}")
            return None, None

    def _force_price_snapshot(self, ticker: str) -> float:
        """IB market snapshot when tick stream appears frozen."""
        try:
            saved = self.cfg.TICKER
            self.cfg.TICKER = ticker
            contract = self.conn.get_contract()
            self.cfg.TICKER = saved
            ticks = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(0.12)
            for attr in ("last", "close", "marketPrice"):
                raw = getattr(ticks, attr, None)
                if raw and float(raw) > 0:
                    px = float(raw)
                    break
            else:
                bid = float(ticks.bid) if ticks.bid and ticks.bid > 0 else 0.0
                ask = float(ticks.ask) if ticks.ask and ticks.ask > 0 else 0.0
                px = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
            self.ib.cancelMktData(contract)
            if px > 0:
                dm = self._target_monitors.get(ticker)
                if dm is not None:
                    dm.last_tick_price = px
                log.info(f"  📡 Price snapshot refresh {ticker}: ${px:.4f}")
                return px
        except Exception as exc:
            log.debug(f"Price snapshot {ticker}: {exc}")
        return 0.0

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

    def _max_concurrent(self) -> int:
        return effective_max_concurrent_positions(self.cfg)

    def _max_locked(self) -> int:
        return effective_max_locked_targets(self.cfg)

    def _open_position_count(self) -> int:
        return len(getattr(self, "_position_slots", {}))

    def _held_tickers(self) -> set:
        return set(getattr(self, "_position_slots", {}).keys())

    def _in_any_position(self) -> bool:
        return self._open_position_count() > 0

    def _refresh_aggregate_position_state(self):
        slots = getattr(self, "_position_slots", {})
        if not slots:
            self.shares = 0.0
            if not self._pending_entry_ticker:
                self.current_ticker = None
            return
        self.shares = sum(float(s.get("shares", 0)) for s in slots.values())
        focus = self._focused_ticker()
        if focus and focus in slots:
            self.current_ticker = focus
        elif self.current_ticker not in slots:
            self.current_ticker = next(iter(slots))

    def _save_position_context(self, ticker: str):
        slots = getattr(self, "_position_slots", {})
        if ticker not in slots:
            return
        slots[ticker].update({
            "shares": self.shares,
            "entry_price": self._entry_price,
            "stop": self._position_stop,
            "target": self._position_target,
            "peak": self._position_peak,
            "hard_floor": self._hard_stop_floor,
            "opened_at": self._position_opened_at,
            "prev_shares": self._prev_shares,
            "last_pulse_price": self._last_pulse_price,
            "last_price_change_at": self._last_price_change_at,
            "last_price_snapshot_at": self._last_price_snapshot_at,
            "last_pulse_fingerprint": self._last_pulse_fingerprint,
            "last_position_pulse": self._last_position_pulse,
            "last_ai_position_manage": self._last_ai_position_manage,
            "last_stagnation_decision": dict(self._last_stagnation_decision),
        })

    def _load_position_context(self, ticker: str) -> bool:
        s = self._position_slots.get(ticker)
        if not s:
            return False
        self._repair_slot_entry_price(ticker)
        s = self._position_slots.get(ticker)
        self.current_ticker = ticker
        self.shares = float(s.get("shares", 0))
        self._entry_price = float(s.get("entry_price", 0))
        self._position_stop = float(s.get("stop", 0))
        self._position_target = float(s.get("target", 0))
        self._position_peak = float(s.get("peak", 0))
        self._hard_stop_floor = float(s.get("hard_floor", 0))
        self._position_opened_at = float(s.get("opened_at", 0))
        self._prev_shares = float(s.get("prev_shares", self.shares))
        self._last_pulse_price = float(s.get("last_pulse_price", 0))
        self._last_price_change_at = float(s.get("last_price_change_at", 0))
        self._last_price_snapshot_at = float(s.get("last_price_snapshot_at", 0))
        self._last_pulse_fingerprint = str(s.get("last_pulse_fingerprint", ""))
        self._last_position_pulse = float(s.get("last_position_pulse", 0))
        self._last_ai_position_manage = float(s.get("last_ai_position_manage", 0))
        self._last_stagnation_decision = dict(s.get("last_stagnation_decision", {}))
        self.bracket_handle = self._bracket_by_ticker.get(ticker)
        plan = self._risk_plans.get(ticker)
        if plan is not None:
            self.risk.open_position(plan)
        return True

    def _repair_slot_entry_price(self, ticker: str) -> None:
        """Fix cross-ticker contamination — refresh entry from IB if price is implausible."""
        slot = self._position_slots.get(ticker)
        if not slot:
            return
        entry = float(slot.get("entry_price", 0) or 0)
        if entry <= 0:
            return
        live = self._live_price_for(ticker, entry)
        if live <= 0:
            return
        ratio = entry / live
        if 0.85 <= ratio <= 1.15:
            return
        try:
            from core.fill_tracker import position_avg_cost
            avg = position_avg_cost(self.ib, ticker)
        except Exception:
            avg = 0.0
        if avg > 0 and 0.85 <= (avg / live) <= 1.15:
            log.warning(
                f"  🔧 Entry price repair {ticker}: ${entry:.4f} → ${avg:.4f} "
                f"(live ${live:.4f})"
            )
            slot["entry_price"] = avg
            slot["entry_fill_px"] = avg

    def _request_deferred_exit(self, ticker: str, price: float, reason: str) -> None:
        """Queue exit for main loop — safe when called from IB tick callbacks."""
        ticker = (ticker or "").upper()
        if not ticker or ticker not in self._position_slots:
            return
        if ticker in self._pending_closes or ticker in self._deferred_exits:
            return
        self._deferred_exits[ticker] = {
            "price": float(price),
            "reason": str(reason),
            "requested_at": time.time(),
        }

    def _service_deferred_exits(self) -> None:
        if not self._deferred_exits:
            return
        can_trade, _ = can_trade_now(self.cfg)
        if not can_trade:
            return
        for ticker, req in list(self._deferred_exits.items()):
            if ticker in self._pending_closes:
                self._deferred_exits.pop(ticker, None)
                continue
            if ticker not in self._position_slots:
                self._deferred_exits.pop(ticker, None)
                continue
            self._deferred_exits.pop(ticker, None)
            self._exit_position(
                float(req.get("price", 0)),
                str(req.get("reason", "deferred_exit")),
                ticker=ticker,
            )

    def _dm_for_ticker(self, ticker: str) -> Optional[DataManager]:
        """Live bar stream for a held ticker (position stream, not scan focus)."""
        dm = self._target_monitors.get(ticker or "")
        if dm is None and self._active_stream_ticker:
            dm = self._target_monitors.get(self._active_stream_ticker)
        return dm

    def _resolve_mtf_bars(
        self,
        ticker: str,
        scan_score: float,
        spike_ratio: float,
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """Cached 5m/15m bars for MTF gate — avoids per-spike IB round-trips."""
        from core.entry_quality import mtf_cache_ttl_sec, mtf_fetch_skipped

        if mtf_fetch_skipped(self.cfg, scan_score=scan_score, spike_ratio=spike_ratio):
            return None, None

        ttl = mtf_cache_ttl_sec(self.cfg)
        now = time.time()
        cached = self._mtf_bar_cache.get(ticker)
        if cached and now - float(cached[0]) < ttl:
            return cached[1], cached[2]

        df_5m = df_15m = None
        ticker_dm = self._dm_for_ticker(ticker)
        if ticker_dm is not None:
            try:
                df_5m = ticker_dm.fetch_historical(
                    duration="1 D", bar_size="5 mins", use_rth=False, quiet=True,
                )
                df_15m = ticker_dm.fetch_historical(
                    duration="1 D", bar_size="15 mins", use_rth=False, quiet=True,
                )
            except Exception:
                pass
        self._mtf_bar_cache[ticker] = (now, df_5m, df_15m)
        return df_5m, df_15m

    def _recalc_bot_nav(self):
        total_pos = 0.0
        for t, s in self._position_slots.items():
            px = self._live_price_for(t, float(s.get("entry_price", 0)))
            total_pos += float(s.get("shares", 0)) * px
        self.bot_nav = self.bot_cash + total_pos

    def _account_context_for_ai(self) -> Dict[str, Any]:
        deployed = 0.0
        for t, s in self._position_slots.items():
            px = self._live_price_for(t, float(s.get("entry_price", 0)))
            deployed += float(s.get("shares", 0)) * px
        equity = self._war_account_equity()
        cash = self._deployable_cash()
        ctx = {
            "equity": equity,
            "cash": cash,
            "nav": equity,
            "open_positions": self._open_position_count(),
            "max_positions": self._max_concurrent(),
            "deployed_usd": deployed,
            "held_tickers": list(self._held_tickers()),
            "consecutive_losses": int(getattr(self.risk, "_consecutive_losses", 0) or 0),
        }
        try:
            from core.war_account import war_account_context
            ctx.update(war_account_context(self.cfg))
        except Exception:
            pass
        ticker = getattr(self.top_pick, "ticker", "") if self.top_pick else ""
        if ticker:
            gate = self._smart_gate_context.get(ticker.upper())
            if gate:
                ctx["smart_gate_context"] = gate
        return ctx

    def _sync_all_positions_from_ib(self):
        if not getattr(self.cfg, "USE_MULTI_POSITION", True):
            self._sync_position_from_ib()
            return
        if not self._position_slots:
            return
        try:
            ib_map: Dict[str, float] = {}
            for p in self.ib.positions():
                sym = getattr(p.contract, "symbol", "")
                pos = float(p.position)
                if pos > 0:
                    ib_map[sym] = pos
                elif pos < 0:
                    if sym not in self._short_warned:
                        self._short_warned.add(sym)
                        log.warning(
                            f"IB short position {pos:.0f} {sym} "
                            f"— long-only scalper ignoring (orphan paper debris)"
                        )
            for ticker, slot in list(self._position_slots.items()):
                if ticker in ib_map:
                    slot["shares"] = ib_map[ticker]
                else:
                    opened = float(slot.get("opened_at", 0))
                    if opened and (time.time() - opened) < 60.0:
                        continue
                    slot["shares"] = 0.0
            self._refresh_aggregate_position_state()
        except Exception as exc:
            log.debug(f"Multi position sync: {exc}")

    def _monitor_all_open_positions(self):
        for ticker in list(self._position_slots.keys()):
            try:
                if not self._load_position_context(ticker):
                    continue
                px = self._live_price_for(ticker, self._entry_price)
                if px > 0:
                    self._live_position_monitor(px)
                self._save_position_context(ticker)
            except Exception as exc:
                log.error(f"Position monitor failed for {ticker}: {exc}")
        self._refresh_aggregate_position_state()

    def _detect_all_exits(self):
        if not getattr(self.cfg, "USE_MULTI_POSITION", True):
            self._detect_exit(self._latest_price())
            return
        for ticker in list(self._position_slots.keys()):
            if not self._load_position_context(ticker):
                continue
            px = self._live_price_for(ticker, self._entry_price)
            self._detect_exit(px)
            self._save_position_context(ticker)
        self._refresh_aggregate_position_state()

    def _position_risk_budget(self) -> float:
        """Risk budget for exit heuristics — AI stop distance or fixed $50 cap."""
        if self._position_stop > 0 and self._entry_price > 0 and self.shares > 0:
            stop_risk = (self._entry_price - self._position_stop) * self.shares
            if stop_risk > 0:
                return stop_risk
        if getattr(self.cfg, "USE_FIXED_RISK_CAP", False):
            return float(getattr(self.cfg, "HARD_STOP_USD", 50.0))
        return get_trade_risk_usd(self.cfg, self.account_equity)

    def _clamp_entry_shares(self, shares: int, price: float) -> int:
        max_shares = effective_max_shares_per_trade(self.cfg)
        shares = min(int(shares), max_shares)
        if getattr(self.cfg, "PAPER_TRADING", False):
            shares = min(shares, int(getattr(self.cfg, "PAPER_MAX_ENTRY_SHARES", 5000)))
        if price <= 0:
            return 0
        if not getattr(self.cfg, "USE_FIXED_DEPLOY_CAP", False):
            reserve_pct = effective_min_cash_reserve_pct(self.cfg)
            cash_cap = self._deployable_cash() * (1.0 - reserve_pct)
            cash_shares = int(cash_cap / price) if price > 0 else shares
            return max(1, min(shares, cash_shares))
        deploy_usd = min(
            get_deploy_usd(self.cfg, self.pilot),
            float(getattr(self.cfg, "MAX_TRADE_SIZE_USD", 1000.0)),
        )
        return max(1, min(shares, int(deploy_usd / price)))

    def _entry_price_mode(
        self,
        current_px: float,
        bid: float,
        ask: float,
        shares: int,
        avg_volume: float,
    ) -> Tuple[Optional[float], str]:
        """Paper uses MARKET entries by default — limits often sit in PendingSubmit."""
        if (
            getattr(self.cfg, "PAPER_TRADING", False)
            and getattr(self.cfg, "PAPER_MARKET_ENTRIES", True)
        ):
            return None, "paper_market"
        return self.broker.decide_smart_entry(current_px, bid, ask, shares, avg_volume)

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
        """Detect if position was closed (by bracket or manually) — reconcile IB fill async."""
        if self._prev_shares > 0 and self.shares == 0:
            opened_at = getattr(self, "_position_opened_at", 0.0)
            if opened_at and (time.time() - opened_at) < 60.0:
                return
            closed_ticker = (self.current_ticker or "").upper()
            if not closed_ticker:
                self._prev_shares = self.shares
                return
            bracket = self._bracket_by_ticker.get(closed_ticker) or self.bracket_handle
            self._enqueue_pending_close(
                closed_ticker,
                "bracket_exit",
                current_px,
                event="trade_closed",
                bracket=bracket,
                shares=self._prev_shares,
            )
            self._clear_closed_position_state(closed_ticker)
        self._prev_shares = self.shares

    def _ensure_position_stream(self, ticker: str):
        """Dedicated tick stream on open position — never stop monitoring after entry."""
        if not ticker:
            return
        self._active_stream_ticker = ticker
        self._ensure_target_stream(ticker, mode="tick")
    
    def _clear_pending_entry(self, ticker: Optional[str] = None, cooldown_sec: float = 45.0):
        """Reset pending bracket state; optional per-ticker cooldown."""
        if ticker:
            self._entry_cooldown_until[ticker] = time.time() + cooldown_sec
            self._spike_skip_until[ticker] = time.time() + cooldown_sec
            self._pending_brackets_by_ticker.pop(ticker, None)
            self._entry_poll_states.pop(ticker, None)
            if self._pending_entry_ticker == ticker:
                self._pending_entry_ticker = (
                    next(iter(self._entry_poll_states), None)
                )
        else:
            self._pending_brackets_by_ticker.clear()
            self._entry_poll_states.clear()
            self._pending_entry_ticker = None
        if not self._entry_poll_states:
            self._pending_entry_ticker = None
            self._pending_entry_until = 0.0

    def _bracket_for_entry_fill(self, ticker: str) -> Optional[BracketHandle]:
        """Bracket for a specific pending/fresh fill — never another ticker's handle."""
        st = self._entry_poll_states.get(ticker) or {}
        if st.get("bracket"):
            return st["bracket"]
        return self._pending_brackets_by_ticker.get(ticker)

    def _suspend_off_hours_market_data(self, market_state: str) -> None:
        """Release IB market data streams when session is not tradable."""
        if not getattr(self.cfg, "OFF_HOURS_SUSPEND_MARKET_DATA", True):
            return
        if self._md_suspended:
            return
        self._md_suspended = True
        self.conn.set_market_data_active(False)
        self.conn.clear_pending_session_reclaim()
        n = len(self._target_monitors)
        if n:
            log.info(
                f"⏸ Off-hours ({market_state}) — releasing {n} market data stream(s)"
            )
            self._stop_all_target_streams()

    def _resume_tradable_market_data(self) -> None:
        """Re-open streams when pre-market/RTH returns."""
        if not self._md_suspended:
            return
        self._md_suspended = False
        self.conn.set_market_data_active(True)
        if self._locked_targets:
            log.info("📡 Session tradable — re-subscribing market data streams")
            self._queue_locked_stream_repairs()
            self._ensure_locked_streams(quiet=False)

    def _halt_trading_for_closed_market(self, market_state: str) -> None:
        """Cancel in-flight entry orders when session is not tradable."""
        if not (
            self._pending_entry_ticker
            or self._entry_poll_states
            or self._pending_brackets_by_ticker
        ):
            return
        tickers = list(self._entry_poll_states.keys()) or list(self._pending_brackets_by_ticker.keys())
        if self._pending_entry_ticker and self._pending_entry_ticker not in tickers:
            tickers.append(self._pending_entry_ticker)
        for ticker in tickers:
            try:
                self.broker.cancel_open_orders_for_symbol(ticker)
            except Exception:
                pass
        self.bracket_handle = None
        self._clear_pending_entry(None, cooldown_sec=120.0)
        log.info(f"⏸ Pending entry halted — market {market_state}")

    def _on_day_session_end(self, market_state: str) -> None:
        """RTH/pre-market window ended — stop all trading for the day."""
        if getattr(self, "_day_session_ended", False):
            return
        self._day_session_ended = True
        sessions = allowed_trading_sessions_label(self.cfg)
        log.info(
            f"🏁 DAY SESSION FINISHED ({market_state}) — enabled sessions: {sessions}. "
            f"No new orders until next pre-market. Open brackets remain on IB."
        )
        self._halt_trading_for_closed_market(market_state)
        self._suspend_off_hours_market_data(market_state)
        self._deferred_exits.clear()
        if getattr(self.cfg, "DAILY_IB_LEARNING_ON_SESSION_END", True):
            try:
                from core.daily_ib_learning import schedule_daily_ib_learning
                schedule_daily_ib_learning(
                    self.cfg, self,
                    trigger="session_end",
                    connector=self.conn,
                )
            except Exception as exc:
                log.debug(f"Session-end IB learning schedule: {exc}")
        try:
            from core.slow_coach import schedule_post_session_coach
            from core.market_hours import now_et
            schedule_post_session_coach(
                self.cfg, self, day=now_et().strftime("%Y-%m-%d"),
            )
        except Exception as exc:
            log.debug(f"Session-end coach lane: {exc}")

    def _on_rth_open(self, old_state: str) -> None:
        """
        Bell at 09:30 ET — shift to live RTH mode when transitioning from pre-market.
        Mid-day startup (old_state=startup) only clears flaky MD blocks — no teardown.
        """
        today = now_et().strftime("%Y-%m-%d")
        if self._rth_open_day == today:
            return
        self._rth_open_day = today
        self._day_session_ended = False

        is_startup = old_state == "startup"
        status = rth_status_line(self.cfg)
        from core.startup_log import sinfo
        if is_startup:
            sinfo(self.cfg, f"🔔 RTH OPEN ({old_state} → open) | {status}")
        else:
            log.info(f"🔔 RTH OPEN ({old_state} → open) | {status}")
            log.info(f"  🧠 {ai_session_context_block(self.cfg)}")

        cleared = clear_transient_md_blocks(self.cfg)
        if cleared:
            for t in cleared:
                self._contract_blacklist.discard(t.upper())
                self._contract_blacklist.discard(t)
        try:
            from core.market_data_learning import clear_hmds_transient_blocks
            clear_hmds_transient_blocks()
        except Exception:
            pass
        try:
            from core.market_context import refresh_macro_context
            ctx = refresh_macro_context(force=True)
            log.info(
                f"🌍 RTH macro: SPY {ctx.get('spy_pct', 0):+.2f}% | "
                f"QQQ {ctx.get('qqq_pct', 0):+.2f}% | "
                f"VIX {ctx.get('vix_level', 0):.1f} ({ctx.get('risk_tone', '?')})"
            )
        except Exception:
            pass

        if is_startup:
            sinfo(
                self.cfg,
                "📡 Mid-session start — streams kept (no 9:30 teardown)",
            )
            teach_profit_hunt_lesson(
                self.autopilot, self.consciousness,
                "RTH session live — profit hunt on stream bars while cache warms.",
            )
            return

        teach_profit_hunt_lesson(
            self.autopilot, self.consciousness,
            "RTH open — super alert: opening noise, ride real volume, protect capital.",
        )
        self._observe_runtime(
            "rth_open",
            old_state=old_state,
            tier=rth_tier(self.cfg),
            cleared_md=cleared[:20],
        )

        if getattr(self.cfg, "RTH_OPEN_STREAM_REFRESH", True):
            for ticker in list(self._target_monitors.keys()):
                self._stop_target_stream(ticker)
            self._scan_data_cache.clear()
            self._bar_warm_due = True
            self._bar_warm_idx = 0
            self._queue_locked_stream_repairs()

        if getattr(self.cfg, "RTH_OPEN_FORCE_RESCAN", True):
            self._last_scan_time = 0.0
            self._needs_initial_scan = True
            log.info("  🔍 RTH open — forcing live IB universe rescan")

        try:
            from core.ai_session_limits import maybe_refresh_session_limits
            maybe_refresh_session_limits(self, min_interval_sec=0.0)
        except Exception:
            pass

        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            try:
                send_dynamic_notification(
                    self.notifier, self.autopilot, "rth_open",
                    self._notify_context({"tier": rth_tier(self.cfg), "old_state": old_state}),
                    f"🔔 RTH OPEN — super alert mode\n{status}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            except Exception:
                pass

        if not is_startup:
            try:
                from core.halim_companion import companion_session_ping
                companion_session_ping(self, self.cfg, trigger="rth_open")
            except Exception as exc:
                log.debug(f"Halim companion RTH ping: {exc}")

    def _queue_locked_stream_repairs(self) -> None:
        """Schedule stream (re)starts on main loop — safe outside IB callbacks."""
        if not self._locked_targets:
            return
        wanted = stream_ticker_list(self._locked_targets, self.cfg)
        held = set(self._held_tickers())
        modes = assign_stream_modes(
            wanted, self.cfg, held=held, tick_denied=self._tick_limit_denied,
        )
        for ticker, mode in modes.items():
            if mode == "skip":
                continue
            self._stream_repair[ticker.upper()] = mode

    def _ai_skip_ticker_permanent(self, ticker: str, reason: str) -> str:
        """IB / venue failure — learn + rotate focus (no permanent block in learn mode)."""
        cd = failure_cooldown_sec(self.cfg)
        spike = float(getattr(self, "_last_spike_ratio", 1.0))
        record_failure_for_learning(
            self.cfg,
            ticker=ticker,
            reason=reason,
            event="ib_failure",
            spike_ratio=spike,
            extra={"pipeline": "entry_reject"},
        )
        if should_permanent_blacklist(self.cfg, reason):
            self._contract_blacklist.add(ticker)
        if is_ib_structural_reject(reason):
            self._ib_structural_reject_count += 1
            if self._ib_structural_reject_count >= 3:
                self._last_scan_time = 0
                log.warning(
                    "🔄 IB blocked 3+ symbols (closing-only / permission) — forcing universe rescan"
                )
                self._ib_structural_reject_count = 0
        self._stop_target_stream(ticker)
        self.broker.cancel_open_orders_for_symbol(ticker)
        self.bracket_handle = None
        self._clear_pending_entry(ticker, cooldown_sec=cd)
        self._locked_targets = [t for t in self._locked_targets if t.ticker != ticker]
        if not learn_dont_block(self.cfg):
            self._locked_targets = [
                t for t in self._locked_targets if t.ticker not in self._contract_blacklist
            ]
        remaining = list(self._locked_targets)
        next_pick = None
        if remaining and getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            try:
                next_ticker = self.ai_commander.pick_next_target(
                    [t.ticker for t in remaining],
                    {t.ticker: t.rank_score for t in remaining},
                    skipped_ticker=ticker,
                    reason=reason,
                )
                next_pick = next((t for t in remaining if t.ticker == next_ticker), None)
            except Exception:
                next_pick = None
        if not next_pick and remaining:
            next_pick = max(remaining, key=lambda t: t.rank_score)
        self.top_pick = next_pick
        if next_pick:
            log.info(
                f"  🧠 AI learned from {ticker} ({reason[:60]}) → focus {next_pick.ticker}"
            )
            self._ensure_focus_stream(quiet=True)
        else:
            self.top_pick = None
            if learn_dont_block(self.cfg):
                self._last_scan_time = 0
                log.info(f"  📚 {ticker}: {reason[:80]} — recorded for learning, rescanning")
            else:
                self._last_scan_time = 0
                log.info(f"  🚫 {ticker} blocked ({reason}) — no tradeable locks left, rescanning")
        if hasattr(self, "consciousness") and self.consciousness:
            try:
                self.consciousness.observe_trade({
                    "ticker": ticker, "action": "LEARN_FAILURE", "reason": reason, "pnl": 0.0,
                })
            except Exception:
                pass
        self._observe_runtime(
            "ib_failure",
            ticker=ticker,
            reason=reason,
            spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
            market_state=get_market_state(self.cfg),
        )
        return "learn_skip" if learn_dont_block(self.cfg) else "permanent_skip"

    def _on_market_data_failure(
        self,
        ticker: str,
        code: int,
        message: str,
        entry: Dict[str, Any],
    ) -> None:
        """IB 162/420/etc. — learn, rotate; soft-fail outside RTH for flaky HMDS."""
        if not ticker:
            return
        pattern = str(entry.get("pattern", "md_failure"))
        reason = f"MD {code} {pattern}: {message[:100]}"
        if entry.get("transient"):
            log.info(
                f"  ⏳ MD transient {ticker}: HMDS flake — keep lock, live streams only "
                f"({reason[:80]})"
            )
            self._observe_runtime(
                "market_data_failure",
                ticker=ticker,
                reason=reason,
                ib_code=code,
                pattern=pattern,
                transient=True,
                market_state=get_market_state(self.cfg),
            )
            self._queue_locked_stream_repairs()
            return

        log.info(f"  🚫 MD skip {ticker}: {reason[:120]}")
        prim = str(entry.get("primary_exchange", "") or "").upper()
        if prim in ("PINK", "OTC", "OTCBB", "ARCAEDGE", "GREY", "GRAY") or pattern in (
            "no_md_permission", "otc_limited",
        ):
            self._contract_blacklist.add(ticker)
        elif pattern == "no_historical_data" and is_rth(self.cfg):
            self._contract_blacklist.add(ticker)
        self._stop_target_stream(ticker)
        self._scan_data_cache.pop(ticker, None)
        self._locked_targets = [t for t in self._locked_targets if t.ticker != ticker]
        if self.top_pick and self.top_pick.ticker == ticker:
            remaining = self._locked_targets
            self.top_pick = remaining[0] if remaining else None
        teach_profit_hunt_lesson(
            self.autopilot, self.consciousness,
            f"No clean data on {ticker} ({pattern}) — profit hunt elsewhere.",
        )
        self._observe_runtime(
            "market_data_failure",
            ticker=ticker,
            reason=reason,
            ib_code=code,
            pattern=pattern,
            failures=int(entry.get("failures", 1)),
            market_state=get_market_state(self.cfg),
        )
        if getattr(self.cfg, "LEARNING_PUSH_ON_TRADE", True):
            try:
                push_learning_checkpoint_async(f"md_failure:{ticker}")
            except Exception:
                pass
        remaining = list(self._locked_targets)
        if remaining:
            try:
                if self.ai_commander:
                    next_t = self.ai_commander.pick_next_target(
                        [t.ticker for t in remaining],
                        {t.ticker: t.rank_score for t in remaining},
                        skipped_ticker=ticker,
                        reason=reason,
                    )
                    self.top_pick = next((t for t in remaining if t.ticker == next_t), remaining[0])
                else:
                    self.top_pick = max(remaining, key=lambda t: t.rank_score)
            except Exception:
                self.top_pick = max(remaining, key=lambda t: t.rank_score)
            self._queue_locked_stream_repairs()

    def _observe_runtime(self, event: str, **context: Any) -> None:
        try:
            self.runtime_observer.observe(event, **context)
        except Exception as exc:
            log.debug(f"Runtime observe {event}: {exc}")

    def _service_loss_streak_learning(self) -> None:
        """Run AI review on loss-streak halt — resume when confident, not after 60 min."""
        risk = self.risk
        if not getattr(risk, "needs_learning_session", False):
            return
        if self._loss_learning_in_flight:
            return
        self._loss_learning_in_flight = True
        risk.begin_learning_session()
        log.info("🧠 LOSS STREAK: starting review of recent losses…")

        def _worker():
            try:
                from core.loss_streak_learning import run_loss_streak_learning
                result = run_loss_streak_learning(self.cfg, self)
                ok = risk.complete_learning_session(
                    str(result.get("summary", "")),
                    float(result.get("confidence", 0.55)),
                )
                if not ok:
                    risk.force_end_learning_halt("learning confidence below threshold")
            except Exception as exc:
                log.warning(f"Loss streak learning failed: {exc}")
                risk.force_end_learning_halt(f"learning error: {exc}")
            finally:
                self._loss_learning_in_flight = False

        threading.Thread(target=_worker, name="loss-streak-learn", daemon=True).start()

    def _build_trade_close_record(
        self,
        ticker: str,
        quote_exit_px: float,
        reason: str = "",
        *,
        flatten_trade=None,
        bracket: Optional[BracketHandle] = None,
    ) -> Dict[str, Any]:
        """Resolve IB entry/exit fills and build a round-trip trade record."""
        slot = dict(self._position_slots.get(ticker, {}))
        entry_quote = float(slot.get("entry_price") or self._entry_price or 0)
        entry_fill = float(slot.get("entry_fill_px") or entry_quote)
        shares = float(slot.get("shares") or self._prev_shares or self.shares or 0)
        opened_at = float(slot.get("opened_at") or getattr(self, "_position_opened_at", 0))
        exit_fill = resolve_exit_fill(
            self.ib,
            symbol=ticker,
            bracket=bracket or self._bracket_by_ticker.get(ticker) or self.bracket_handle,
            flatten_trade=flatten_trade,
            quote_px=quote_exit_px,
            since_ts=opened_at,
            max_wait=0.0,
            entry_fill=entry_fill,
        )
        return build_round_trip_record(
            ticker=ticker,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            quote_entry=entry_quote,
            quote_exit=quote_exit_px,
            shares=shares,
            exit_reason=reason,
            limit_px=slot.get("limit_px"),
            entry_mode=str(slot.get("entry_mode", "")),
            regime=str(slot.get("regime") or getattr(self, "_last_entry_regime", "")),
            hold_sec=max(0.0, time.time() - opened_at) if opened_at else 0.0,
            peak_px=float(slot.get("peak") or self._position_peak or 0),
            stop_px=float(slot.get("stop") or self._position_stop or 0),
            target_px=float(slot.get("target") or self._position_target or 0),
        )

    def _fill_cache(self):
        return getattr(self.conn, "fill_cache", None)

    def _enqueue_pending_close(
        self,
        ticker: str,
        reason: str,
        quote_exit_px: float,
        *,
        event: str = "trade_closed",
        flatten_trade=None,
        bracket=None,
        slot: Optional[Dict] = None,
        shares: Optional[float] = None,
    ) -> None:
        """Queue IB fill reconciliation — notifications fire after confirmed fill."""
        ticker = (ticker or "").upper()
        if not ticker:
            return
        key = f"{ticker}:{time.time():.3f}"
        snap = snapshot_slot(slot or self._position_slots.get(ticker, {}))
        if not snap.get("entry_fill_px") and self._entry_price > 0 and self.current_ticker == ticker:
            snap.setdefault("entry_fill_px", self._entry_price)
            snap.setdefault("entry_price", self._entry_price)
        qty = float(shares if shares is not None else snap.get("shares") or self._prev_shares or self.shares or 0)
        opened = float(snap.get("opened_at") or getattr(self, "_position_opened_at", 0))
        self._pending_closes[key] = PendingClose(
            ticker=ticker,
            reason=reason,
            quote_exit_px=quote_exit_px,
            slot=snap,
            shares=qty,
            opened_at=opened,
            event=event,
            flatten_trade=flatten_trade,
            bracket=bracket,
        )

    def _service_pending_closes(self) -> None:
        """Instant IB cache lookup each tick — zero sleep, zero throttle; notify when fill lands."""
        if not self._pending_closes:
            return
        cache = self._fill_cache()
        fallback_sec = float(getattr(self.cfg, "FILL_RECONCILE_FALLBACK_SEC", 8.0))
        now = time.time()

        for key, pending in list(self._pending_closes.items()):
            force = (now - pending.started_at) >= fallback_sec
            trade_rec = build_close_record(pending, self.ib, cache, force=force)
            if trade_rec is None:
                continue
            self._pending_closes.pop(key, None)
            self._finalize_closed_trade(trade_rec, pending)

    def _finalize_closed_trade(self, trade_rec: Dict[str, Any], pending: PendingClose) -> None:
        """Notify and learn using IB-confirmed fills."""
        ticker = pending.ticker
        exit_fill = float(trade_rec.get("exit_fill") or trade_rec.get("exit", 0))
        pnl = float(trade_rec.get("pnl_usd", 0))
        pnl_pct = float(trade_rec.get("pnl_pct", 0))
        result = trade_rec.get("result", "loss")
        confirmed = bool(trade_rec.get("fill_confirmed"))
        qty = float(trade_rec.get("shares") or pending.shares or 0)

        if not pending.credited and qty > 0 and exit_fill > 0:
            self._credit_exit_proceeds(qty, exit_fill)
            pending.credited = True

        tag = "IB fill" if confirmed else "est. fill"
        log.info(
            f"📕 EXIT {ticker} ({tag}): ${exit_fill:.4f} "
            f"(quote ${pending.quote_exit_px:.4f}) | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | "
            f"{result.upper()} | {pending.reason[:60]}"
        )

        exit_ctx = {
            "ticker": ticker,
            "pnl_usd": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "result": result,
            "entry_fill": trade_rec.get("entry_fill"),
            "exit_fill": exit_fill,
            "fill_confirmed": confirmed,
            "pilot_level": self.pilot.state.level if hasattr(self, "pilot") else "Cadet",
        }
        notify_event = "early_exit" if pending.event == "early_exit" else "trade_closed"
        fallback = (
            f"📕 EXIT {ticker} | P&L ${pnl:+.2f} ({pnl_pct:+.1f}%) | {result.upper()}\n"
            f"Entry ${trade_rec.get('entry_fill', 0):.4f} → Exit ${exit_fill:.4f} ({tag})"
        )
        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, notify_event,
                self._notify_context(exit_ctx),
                fallback,
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info(
                f"📕 HANOON EXIT\nTicker: {ticker}\n"
                f"Exit fill: ${exit_fill:.4f}\n"
                f"Entry fill: ${trade_rec.get('entry_fill', 0):.4f}\n"
                f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                f"Result: {result.upper()}"
            )

        self._apply_trade_close_learning(trade_rec, ticker)
        try:
            from core.lottery_bank import on_trade_closed
            on_trade_closed(
                self.cfg, self.notifier, trade_rec,
                slot=pending.slot if pending else {},
            )
        except Exception as exc:
            log.debug(f"Lottery bank close: {exc}")
        if pending.event == "early_exit" and is_mechanical_profit_exit(pending.reason):
            record_profit_hunt_learning(
                self.cfg,
                event=pending.reason.split(":")[0],
                ticker=ticker,
                context={"reason": pending.reason, **getattr(self, "_profit_hunt_spike_ctx", {})},
                pnl_usd=pnl,
                won=pnl > 0,
            )
        self.risk.record_trade_result(pnl)
        if self.risk.needs_learning_session:
            self._service_loss_streak_learning()
        try:
            self.shadow_circuit.on_live_trade_closed(pnl, self.account_equity)
        except Exception:
            pass

        try:
            pnl_usd = round(pnl, 2)
            self.pilot.complete_flight(exit_fill, pnl_usd, round(pnl_pct, 2) / 100, pending.reason[:80])
            if pnl > 0:
                self.pilot.record_pattern_match("win", True, pnl_usd)
            else:
                self.pilot.record_pattern_match("loss", False, pnl_usd)
        except Exception:
            pass

        try:
            pilot_experience_to_git(self.pilot)
            if getattr(self.cfg, "LEARNING_PUSH_ON_TRADE", True):
                push_learning_checkpoint_async(f"trade_exit_{ticker}")
        except Exception:
            pass
        try:
            from core.learning_coordinator import schedule_post_close_learning
            schedule_post_close_learning(self.cfg, self)
        except Exception as exc:
            log.debug(f"Post-close learning schedule: {exc}")
        self._attempt_hot_swap_entry()

    def _clear_closed_position_state(self, ticker: str) -> None:
        """Drop local position tracking after exit (IB brackets may still rest)."""
        if hasattr(self, "_active_positions"):
            self._active_positions = [
                p for p in self._active_positions if p.get("ticker") != ticker
            ]
        if ticker:
            self._position_slots.pop(ticker, None)
            self._bracket_by_ticker.pop(ticker, None)
            self._risk_plans.pop(ticker, None)
        if self.current_ticker == ticker:
            self.current_ticker = None
        self.bracket_handle = None
        self._position_opened_at = 0.0
        self._position_stop = 0.0
        self._position_target = 0.0
        self._position_peak = 0.0
        self._hard_stop_floor = 0.0
        if self._active_stream_ticker == ticker:
            self._stop_target_stream(self._active_stream_ticker)
            self._active_stream_ticker = None
        if getattr(self, "_next_best_pick", None) and self._next_best_score >= 25:
            self.top_pick = self._next_best_pick
        self._refresh_aggregate_position_state()

    def _apply_trade_close_learning(self, trade_rec: Dict[str, Any], ticker: str) -> None:
        """Feed round-trip fills into every learning / telemetry hook."""
        pnl = float(trade_rec.get("pnl_usd", 0))
        pnl_pct = float(trade_rec.get("pnl_pct", 0))
        result = trade_rec.get("result", "loss")
        exit_fill = float(trade_rec.get("exit_fill") or trade_rec.get("exit", 0))
        entry_fill = float(trade_rec.get("entry_fill") or trade_rec.get("entry", 0))
        shares = float(trade_rec.get("shares", 0))
        reason = str(trade_rec.get("exit_reason", ""))
        regime = str(trade_rec.get("regime", ""))
        hold_sec = float(trade_rec.get("hold_sec", 0))
        entry_slip = float(trade_rec.get("entry_slippage_pct", 0))
        exit_slip = float(trade_rec.get("exit_slippage_pct", 0))
        stop_px = float(trade_rec.get("stop", 0))
        target_px = float(trade_rec.get("target", 0))

        append_fill_ledger({**trade_rec, "event": "round_trip"})
        try:
            from core.slow_coach import observe_round_trip
            observe_round_trip(
                self.cfg, trade_rec,
                equity=float(self._war_account_equity() or self.bot_nav or 1000),
            )
        except Exception:
            pass
        try:
            from core.war_account import record_exit, war_account_enabled
            if war_account_enabled(self.cfg):
                record_exit(
                    self.cfg,
                    ticker=ticker,
                    shares=int(shares),
                    ib_fill=exit_fill,
                    quote=float(trade_rec.get("quote_exit", exit_fill)),
                    pnl_usd_ib=pnl,
                    exit_reason=reason,
                    spread_pct=abs(exit_slip),
                )
        except Exception as exc:
            log.debug(f"War account exit: {exc}")
        log_round_trip_fills(
            ticker=ticker,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            quote_entry=float(trade_rec.get("quote_entry", entry_fill)),
            quote_exit=float(trade_rec.get("quote_exit", exit_fill)),
            shares=shares,
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            result=result,
            exit_reason=reason,
            entry_slippage_pct=entry_slip,
            exit_slippage_pct=exit_slip,
            regime=regime,
            hold_sec=hold_sec,
            entry_mode=str(trade_rec.get("entry_mode", "")),
            limit_px=trade_rec.get("limit_px"),
        )
        self.trade_journal.append(trade_rec)
        if len(self.trade_journal) > self._trade_journal_max:
            self.trade_journal = self.trade_journal[-self._trade_journal_max:]
        if self.ai_commander:
            try:
                self.ai_commander.record_trade(trade_rec)
            except Exception:
                pass
        try:
            from core.halim_ppo_coevolution import attach_trade_outcome
            attach_trade_outcome(
                ticker,
                pnl=float(pnl),
                win=(result == "win"),
                cfg=self.cfg,
                trade_rec=trade_rec,
            )
        except Exception:
            pass
        try:
            from core.halim_outcome_gold import record_trade_outcome
            record_trade_outcome(trade_rec, cfg=self.cfg)
        except Exception:
            pass
        try:
            self.account_evaluator.evaluate(
                self, "trade_closed", ai_commander=self.ai_commander,
            )
        except Exception:
            pass
        observe_trade_everywhere(
            trade_rec, self.autopilot, self.consciousness, self.pilot, cfg=self.cfg,
        )
        exit_type = "other"
        if stop_px > 0 and exit_fill <= stop_px * 1.003:
            exit_type = "stop_hit"
        elif target_px > 0 and exit_fill >= target_px * 0.997:
            exit_type = "target_hit"
        elif pnl > 0:
            exit_type = "profit_exit"
        elif pnl < 0:
            exit_type = "loss_exit"
        if "stop" in reason.lower():
            exit_type = "stop_hit"
        atr = float(self._last_entry_telemetry.get("atr", 0) or 0)
        noise_sec = float(getattr(self.cfg, "REGIME_ATR_NOISE_STOP_SEC", 120.0))
        noise_stop = exit_type == "stop_hit" and hold_sec < noise_sec
        from core.trade_telemetry import _raw_rr
        log_regime_atr_outcome(
            ticker=ticker,
            regime=regime,
            exit_type=exit_type,
            entry=entry_fill,
            exit_px=exit_fill,
            stop=stop_px,
            target=target_px,
            atr=atr,
            hold_sec=hold_sec,
            pnl_usd=pnl,
            planned_rr=_raw_rr(entry_fill, stop_px, target_px),
            noise_stop=noise_stop,
        )
        log_exit_postmortem(
            ticker=ticker,
            entry=entry_fill,
            exit_px=exit_fill,
            shares=shares,
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            result=result,
            regime=regime,
            hold_sec=hold_sec,
            entry_slippage_pct=entry_slip,
            exit_reason=reason,
        )
        self._observe_runtime(
            "trade_closed",
            ticker=ticker,
            reason=reason or result,
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            won=(pnl > 0),
            exit_type=exit_type,
            hold_sec=hold_sec,
            regime=regime,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
            entry_slippage_pct=entry_slip,
            exit_slippage_pct=exit_slip,
            market_state=get_market_state(self.cfg),
        )
        if pnl < 0:
            self._observe_runtime(
                "loss_streak",
                ticker=ticker,
                reason=reason,
                pnl_usd=pnl,
                consecutive_losses=int(getattr(self.risk, "_consecutive_losses", 0)),
                market_state=get_market_state(self.cfg),
            )
            try:
                from core.live_trade_guard import on_trade_closed as guard_trade_closed
                guard_trade_closed(ticker, pnl, self.cfg, exit_reason=reason)
            except Exception:
                pass
        try:
            from core.ppo_entry_learning import record_ppo_trade_close
            record_ppo_trade_close(
                self.cfg,
                ticker=ticker,
                pnl_usd=pnl,
                pnl_pct=float(trade_rec.get("pnl_pct", 0) or 0),
                result=str(result),
                exit_reason=reason,
            )
        except Exception:
            pass
        slot = self._position_slots.get(ticker, {})
        combined_slip = abs(entry_slip) + abs(exit_slip)
        try:
            buffer_append({
                "source": "live_trade",
                "ticker": ticker,
                "action": "SELL",
                "entry_price": entry_fill,
                "exit_price": exit_fill,
                "quote_entry": trade_rec.get("quote_entry"),
                "quote_exit": trade_rec.get("quote_exit"),
                "entry_slippage_pct": entry_slip,
                "exit_slippage_pct": exit_slip,
                "pnl_usd": round(pnl, 2),
                "win": 1 if pnl > 0 else 0,
                "reward": reward_from_trade(
                    pnl, self.cfg,
                    slippage_pct=combined_slip,
                    spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                    pnl_pct=float(trade_rec.get("pnl_pct", 0) or 0),
                    peak_pct=float(trade_rec.get("peak_pct", 0) or 0),
                    entry_fill=float(entry_fill or 0),
                    exit_fill=float(exit_fill or 0),
                    shares=float(trade_rec.get("shares", 0) or 0),
                ),
                "regime": regime,
                "confidence": getattr(self, "_last_ai_confidence", 0.5),
                "vision_read": (slot.get("vision_read") or "")[:800],
                "features": snapshot_features(self._feature_buffer, self.cfg),
                "exit_reason": reason[:200],
                "hold_sec": hold_sec,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        if getattr(self.cfg, "LEARNING_PUSH_ON_TRADE", True):
            try:
                push_learning_checkpoint_async(f"trade_closed_{ticker}")
            except Exception:
                pass
        try:
            maybe_refresh_session_limits(self, min_interval_sec=300.0)
        except Exception:
            pass

    def _record_early_exit_learning(
        self,
        ticker: str,
        entry: float,
        exit_px: float,
        shares: float,
        pnl: float,
        reason: str,
        *,
        flatten_trade=None,
        trade_rec: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Early exits — resolve IB exit fill and feed all learning hooks."""
        if trade_rec is None:
            trade_rec = self._build_trade_close_record(
                ticker, exit_px, reason, flatten_trade=flatten_trade,
            )
        self._apply_trade_close_learning(trade_rec, ticker)

    def _exit_position(
        self,
        current_px: float,
        reason: str,
        ticker: Optional[str] = None,
        *,
        defer: bool = False,
    ):
        """Manually exit position — submit flatten, reconcile IB fill async."""
        ticker = (ticker or self.current_ticker or "").upper()
        if defer:
            self._request_deferred_exit(ticker, current_px, reason)
            return
        can_trade, market_state = can_trade_now(self.cfg)
        if not can_trade:
            log.debug(
                f"Exit deferred — session {market_state} (no orders outside "
                f"{allowed_trading_sessions_label(self.cfg)})"
            )
            self._request_deferred_exit(ticker, current_px, reason)
            return
        if ticker and ticker in self._position_slots:
            self._load_position_context(ticker)
        if self.shares <= 0:
            return
        quantity = int(self.shares)
        handle = self._bracket_by_ticker.get(ticker) or self.bracket_handle
        slot_snap = snapshot_slot(self._position_slots.get(ticker, {}))
        flatten_trade = None
        try:
            flatten_trade = self.broker.flatten_position(
                quantity, handle=handle, urgent=True, symbol=ticker,
            )
            log.info(
                f"⚡ EXIT submitted: SELL {quantity} {ticker} @ market "
                f"(quote ${current_px:.4f}) | {reason[:80]}"
            )
            self._enqueue_pending_close(
                ticker, reason, current_px,
                event="early_exit",
                flatten_trade=flatten_trade,
                bracket=handle,
                slot=slot_snap,
                shares=float(quantity),
            )
            self.shares = 0.0
            self._prev_shares = 0.0
            self.bracket_handle = None
            if self.risk.plan:
                self.risk.close_position()
            self._reset_profit_hunt_state()
            self._clear_closed_position_state(ticker)
            self._clear_pending_entry(ticker, cooldown_sec=30.0)
            self._clear_ai_councils(ticker)
        except Exception as exc:
            log.error(f"Early exit failed: {exc}")

    def commander_positions_intel(self) -> Dict[str, Any]:
        from core.position_intel import collect_positions
        return collect_positions(self)

    def commander_risk_summary(self) -> Dict[str, Any]:
        from core.position_intel import collect_risk
        return collect_risk(self)

    def commander_exit_ticker(self, ticker: str, reason: str = "commander_exit") -> Dict[str, Any]:
        """Exit one position from Telegram / AI commander (bot slot or IB-only)."""
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return {"ok": False, "error": "no ticker"}

        self._sync_all_positions_from_ib()
        px = self._live_price_for(ticker, 0.0)

        if ticker in self._position_slots:
            if px <= 0:
                px = float(self._position_slots[ticker].get("entry_price", 0) or 0)
            if px <= 0:
                return {"ok": False, "error": f"no price for {ticker}"}
            self._exit_position(px, reason, ticker=ticker)
            return {"ok": True, "ticker": ticker, "price": px, "reason": reason, "source": "bot_slot"}

        qty = 0
        entry = 0.0
        try:
            self.ib.reqPositions()
            self.ib.sleep(0.3)
            for p in self.ib.positions():
                sym = (getattr(p.contract, "symbol", "") or "").upper()
                if sym == ticker:
                    qty = int(float(p.position))
                    entry = float(getattr(p, "avgCost", 0) or 0)
                    break
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if qty <= 0:
            return {"ok": False, "error": f"no open long position for {ticker}"}
        if px <= 0:
            px = entry or self._live_price_for(ticker, entry)

        old_ticker = self.cfg.TICKER
        try:
            self.cfg.TICKER = ticker
            self.conn._contract = None
            self.broker.cancel_open_orders_for_symbol(ticker)
            self.broker.flatten_position(qty, urgent=True, symbol=ticker)
            self.ib.sleep(1)
            pnl = (px - entry) * qty if entry > 0 else 0.0
            log.info(f"⚡ COMMANDER EXIT: {ticker} @ ${px:.2f} | {reason} | P&L ${pnl:+.2f}")
            if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                from core.notify import send_dynamic_notification
                send_dynamic_notification(
                    self.notifier, self.autopilot, "commander_exit",
                    self._notify_context({
                        "ticker": ticker, "price": px, "pnl_usd": round(pnl, 2),
                        "reason": reason, "entry": entry,
                    }),
                    f"⚡ COMMANDER EXIT\n{ticker} @ ${px:.2f}\nReason: {reason}\nP&L: ${pnl:+.2f}",
                    ai_commander=self.ai_commander,
                    consciousness=self.consciousness,
                    pilot=self.pilot,
                )
            else:
                self.notifier.info(f"⚡ COMMANDER EXIT\n{ticker} @ ${px:.2f}\nReason: {reason}\nP&L: ${pnl:+.2f}")
            try:
                from core.telegram_broadcast import broadcast_ops
                broadcast_ops(
                    self.cfg,
                    "commander_exit",
                    {
                        "ticker": ticker, "price": px, "pnl": round(pnl, 2),
                        "reason": reason, "entry": entry,
                    },
                    f"EXIT {ticker} @ ${px:.2f} | {reason} | P&L ${pnl:+.2f}",
                )
            except Exception:
                pass
            return {"ok": True, "ticker": ticker, "price": px, "pnl": round(pnl, 2), "reason": reason, "source": "ib_only"}
        except Exception as exc:
            log.error(f"Commander exit failed {ticker}: {exc}")
            return {"ok": False, "error": str(exc)}
        finally:
            self.cfg.TICKER = old_ticker
            self.conn._contract = None

    def commander_exit_filtered(self, mode: str, reason: str = "commander_bulk_exit") -> Dict[str, Any]:
        """Exit positions: mode = profit | loss | all."""
        mode = (mode or "all").lower().strip()
        intel = self.commander_positions_intel()
        results: List[Dict[str, Any]] = []
        for p in intel.get("positions", []):
            pnl = float(p.get("unrealized_pnl", 0) or 0)
            if mode == "profit" and pnl <= 0:
                continue
            if mode == "loss" and pnl >= 0:
                continue
            results.append(self.commander_exit_ticker(p["ticker"], reason))
        ok_n = sum(1 for r in results if r.get("ok"))
        return {"ok": ok_n > 0, "mode": mode, "exited": ok_n, "results": results}

    def _schedule_self_train(self):
        """Local weight update only — no git push until session shutdown."""
        try:
            from core.async_utils import get_background_worker
            get_background_worker()._executor.submit(self._daily_self_train)
        except Exception:
            try:
                self._daily_self_train()
            except Exception:
                pass
    
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
                    ticker = r.get("ticker", "?")
                    px = self._live_price_for(ticker, float(r.get("price", 0) or 0))
                    scan_data.append({
                        "ticker": ticker,
                        "price": round(px, 4) if px > 0 else r.get("price", 0),
                        "score": round(float(r.get("total_score", 0)), 1),
                        "reason": str(r.get("reasons", ""))[:30],
                    })
                else:
                    px = self._live_price_for(r.ticker, float(r.price or 0))
                    scan_data.append({
                        "ticker": r.ticker, "price": round(px, 4) if px > 0 else r.price,
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

    def _maybe_resume_ib_from_shadow(self) -> None:
        """Paper: clear shadow gate on startup so orders reach IB Gateway."""
        paper_bypass = (
            getattr(self.cfg, "PAPER_TRADING", False)
            and not getattr(self.cfg, "SHADOW_ON_PAPER", False)
        )
        if not paper_bypass and not getattr(self.cfg, "SHADOW_RESUME_ON_START", True):
            return
        reason = (
            "paper account — shadow sim disabled (SHADOW_ON_PAPER=false)"
            if paper_bypass
            else "SHADOW_RESUME_ON_START"
        )
        if self.shadow_circuit.force_resume_live(reason=reason):
            msg = (
                "☀️ SHADOW CLEARED — entries will place **real IB paper orders** "
                "(you will get IB app notifications again)."
            )
            log.info(msg)
            try:
                self.notifier.info(msg)
            except Exception:
                pass

    def _log_tick_stream_config(self) -> None:
        """Startup audit — tick-by-tick vs 5s fallback and IB stream budget."""
        from core.data import tick_by_tick_type

        tbt = tick_by_tick_type(self.cfg)
        use_tick = bool(getattr(self.cfg, "USE_TICK_STREAM", True))
        paper_rt_only = bool(
            getattr(self.cfg, "PAPER_TRADING", False)
            and getattr(self.cfg, "PAPER_REALTIME_BARS_ONLY", False)
        )
        if paper_rt_only:
            mode = "5s bars only (PAPER_REALTIME_BARS_ONLY)"
        elif use_tick:
            try:
                from core.sniper_execution import sniper_tick_stream_count, sniper_tick_streams_enabled
                if sniper_tick_streams_enabled(self.cfg):
                    n = sniper_tick_stream_count(self.cfg) or 0
                    mode = f"sniper top-{n} tick + 5s on rest"
                else:
                    mode = f"tick-by-tick ({tbt})"
            except Exception:
                mode = f"tick-by-tick ({tbt})"
        else:
            mode = "5s bars (USE_TICK_STREAM=false)"
        n_tick = tick_stream_count(self.cfg)
        n_rt = max_realtime_bar_streams(self.cfg)
        log.info(
            f"📡 Market data: {mode} | IB budget {n_tick} tick + {n_rt} 5s-bars "
            f"(cap ~5 each — extras deferred)"
        )

    def _log_startup_banner(self) -> None:
        """One structured boot summary — details at DEBUG when STARTUP_LOG_COMPACT=true."""
        from core.startup_log import log_block, sinfo, startup_compact
        from core.data import tick_by_tick_type
        from core.market_hours import allowed_trading_sessions_label
        from core.ram_tier import ram_tier_summary
        from core.memory_guard import memory_status
        from core.ai_session_limits import format_limits_log, should_ai_define_limits

        acct_vals = self.conn.ib.accountValues()
        account = acct_vals[0].account if acct_vals else "unknown"
        mode = "PAPER" if self.cfg.PAPER_TRADING else "LIVE"
        market_state = get_market_state(self.cfg)
        can_trade, _ = can_trade_now(self.cfg)
        sessions = allowed_trading_sessions_label(self.cfg)

        paper_rt = bool(
            getattr(self.cfg, "PAPER_TRADING", False)
            and getattr(self.cfg, "PAPER_REALTIME_BARS_ONLY", False)
        )
        if paper_rt:
            md_mode = "5s bars (paper)"
        elif getattr(self.cfg, "USE_TICK_STREAM", True):
            md_mode = f"tick ({tick_by_tick_type(self.cfg)})"
        else:
            md_mode = "5s bars"

        defer = getattr(self.cfg, "SCAN_DEFER_IB_ON_STARTUP", False)
        warmup = int(getattr(self.cfg, "IB_SCANNER_WARMUP_SEC", 5))
        from core.scanner_session import scanner_session_log_line
        scan_mode = f"deferred curated" if defer else scanner_session_log_line(self.cfg)

        council_on = getattr(self.cfg, "COUNCIL_ENABLED", False)
        council = (
            f"{getattr(self.cfg, 'COUNCIL_BACKEND', 'groq')}"
            if council_on else "off"
        )

        lines = [
            f"{mode} | {account} | ${self.account_equity:,.0f}",
            f"Market: {market_state} | tradable={'yes' if can_trade else 'no'} | sessions: {sessions}",
            f"Scanner: {scan_mode} | MD: {md_mode} | Council: {council}",
            f"PPO: {'loaded' if not getattr(self, '_model_fresh', True) else 'fresh'} | "
            f"tick budget {tick_stream_count(self.cfg)}+{max_realtime_bar_streams(self.cfg)} 5s",
        ]
        if hasattr(self, "pilot"):
            vs = self.pilot.get_veteran_status()
            lines.append(
                f"Pilot: {vs.get('level', '?')} XP={vs.get('total_xp', 0)} "
                f"conf={vs.get('confidence_threshold', 0):.0%}"
            )
        if should_ai_define_limits(self.cfg):
            lines.append(format_limits_log(self.cfg, self.account_equity))

        log_block("HANOON STARTUP", lines)

        if startup_compact(self.cfg):
            return

        mem = memory_status(self.cfg)
        tier_info = ram_tier_summary(self.cfg)
        sinfo(
            self.cfg,
            f"🧠 Cloud council detail: groq={getattr(self.cfg, 'GROQ_MODEL', '?')} | "
            f"gemini={getattr(self.cfg, 'GEMINI_MODEL', '?')} | "
            f"RAM {mem['total_ram_mb']}MB tier={tier_info['label']}",
            force=True,
        )
        discipline_log = startup_log_line(self.cfg)
        if discipline_log:
            sinfo(self.cfg, discipline_log, force=True)
        try:
            from core.smart_stack import startup_banner_line
            ss_line = startup_banner_line(self.cfg)
            if ss_line:
                sinfo(self.cfg, ss_line, force=True)
        except Exception:
            pass

    def run(self):
        self._register_shutdown_signals()
        from core.startup_log import set_quiet_phase
        set_quiet_phase(getattr(self.cfg, "STARTUP_LOG_COMPACT", True))
        from core.shutdown_control import (
            clear_shutdown_request,
            remove_pid_file,
            shutdown_requested,
            write_pid,
        )
        clear_shutdown_request()
        write_pid()
        replay_mode = os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
        try:
            from core.learning_persistence import start_learning_guard
            self._learning_guard = start_learning_guard(
                self.cfg,
                lambda: getattr(self, "model", None),
                mode="replay" if replay_mode else "live",
                runner=self,
            )
        except Exception as exc:
            log.debug(f"Learning persistence: {exc}")
            self._learning_guard = None
        try:
            from core.trader_directives import seed_default_directives_if_empty
            seed_default_directives_if_empty()
        except Exception:
            pass
        from core.architecture_epoch import apply_full_epoch_reset
        apply_full_epoch_reset(
            self.cfg,
            consciousness=getattr(self, "consciousness", None),
            autopilot=getattr(self, "autopilot", None),
            shadow_circuit=getattr(self, "shadow_circuit", None),
            force=bool(getattr(self.cfg, "ARCHITECTURE_EPOCH_RESET", False)),
        )
        # Full initialization report (pushed to git — not echoed to console)
        self._write_init_report()
        self._refresh_account_balance()
        self._log_startup_banner()
        if os.getenv("REPLAY_LIVE", "").lower() not in ("1", "true", "yes"):
            try:
                from core.brain_maturity import apply_maturity_to_config, log_maturity_banner
                apply_maturity_to_config(self.cfg)
                log_maturity_banner(self.cfg)
            except Exception as exc:
                log.debug(f"Brain maturity init: {exc}")
            try:
                from core.halim_runtime import init_halim_runtime
                self._halim_runtime = init_halim_runtime(self.cfg)
                if self._halim_runtime:
                    self._halim_runtime.attach_runner(self)
            except Exception as exc:
                log.debug(f"Halim runtime init: {exc}")
                self._halim_runtime = None
        try:
            from core.halim_developer import enable_halim_developer_mode
            enable_halim_developer_mode(self.cfg)
        except Exception as exc:
            log.debug(f"Halim developer mode: {exc}")
        if getattr(self.cfg, "COUNCIL_ENABLED", getattr(self.cfg, "OLLAMA_ENABLED", False)):
            try:
                from core.ollama_models import text_model_startup_warnings
                for warn in text_model_startup_warnings(self.cfg):
                    log.warning(f"⚠️ {warn}")
            except Exception:
                pass
        bootstrap_ai_session_limits(self)
        try:
            from core.commander_runtime import ensure_commander_runtime
            ensure_commander_runtime(self.cfg, replay=replay_mode)
        except Exception as exc:
            log.debug(f"Commander runtime: {exc}")
        try:
            from core.war_account import ensure_war_account
            ensure_war_account(self.cfg)
        except Exception as exc:
            log.debug(f"War account: {exc}")
        try:
            from core.sniper_execution import sniper_timing_log_line
            sline = sniper_timing_log_line(self.cfg)
            if sline:
                log.info(f"  🎯 {sline}")
        except Exception as exc:
            log.debug(f"Sniper profile: {exc}")
        try:
            from core.market_context import warm_macro_context_background
            warm_macro_context_background()
        except Exception as exc:
            log.debug(f"Macro warm: {exc}")
        try:
            from core.lottery_bank import ensure_lottery_bank
            ensure_lottery_bank(self.cfg)
        except Exception as exc:
            log.debug(f"Lottery bank: {exc}")
        if self._ib_starting_balance:
            try:
                self.shadow_circuit.reset_daily(self._ib_starting_balance)
            except Exception:
                pass

        if getattr(self.cfg, "SHADOW_CIRCUIT_ENABLED", True):
            self._maybe_resume_ib_from_shadow()
        if getattr(self.cfg, "SHADOW_CIRCUIT_ENABLED", True) and self.shadow_circuit.in_shadow:
            if self.shadow_circuit.block_broker():
                st = self.shadow_circuit.shadow_stats()
                log.warning(
                    f"🌑 SHADOW MODE — IB orders BLOCKED | "
                    f"closed={st.get('count', 0)} open={st.get('open', 0)} | "
                    f"./scripts/resume_ib_trading.sh"
                )
            else:
                from core.startup_log import sinfo
                sinfo(self.cfg, "☀️ Shadow state ignored on paper — real IB orders enabled")

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

        try:
            from core.halim_companion import companion_session_ping
            companion_session_ping(self, self.cfg, trigger="session_startup")
        except Exception as exc:
            log.debug(f"Halim companion startup ping: {exc}")

        # Clear orphaned bracket orders from previous sessions before trading
        try:
            self.broker.cancel_stale_open_orders()
            n = self.broker.flatten_orphan_short_positions()
            if n:
                log.info(f"🧹 Covered {n} orphan short position(s) on paper account")
        except Exception:
            pass

        # Market clock — one line; extended detail only when verbose boot
        market_state = get_market_state(self.cfg)
        self._last_market_state = market_state
        from core.startup_log import sinfo, startup_compact
        log.info(f"🕐 {market_status_line(self.cfg)}")
        rth_line = rth_status_line(self.cfg)
        if rth_line and not startup_compact(self.cfg):
            log.info(f"  📡 {rth_line}")
        if market_state == "open":
            self._on_rth_open("startup")
        elif market_state == "pre_market":
            sinfo(self.cfg, "⏳ Pre-market — auto RTH alert at 09:30 ET")
        if getattr(self.cfg, "AI_ACCOUNT_EVAL_ON_STARTUP", False):
            try:
                self._worker._executor.submit(
                    lambda: self._run_account_eval("session_startup", force=True)
                )
            except Exception:
                self._run_account_eval("session_startup", force=True)
        if market_state != "open":
            if is_extended_session(market_state):
                can_now, _ = can_trade_now(self.cfg)
                if can_now:
                    sinfo(
                        self.cfg,
                        f"📊 {market_state.upper()} trading enabled ({allowed_trading_sessions_label(self.cfg)})",
                        force=True,
                    )
                else:
                    self._day_session_ended = True
                    log.info(
                        f"📊 {market_state.upper()} — training mode "
                        f"(sessions: {allowed_trading_sessions_label(self.cfg)})"
                    )
            else:
                log.info(f"📊 {market_state.upper()} — no session")

        # Defer blocking IB scanner to first main-loop tick (avoids silent hang at startup)
        self._needs_initial_scan = bool(self.conn.is_connected())
        if not self._needs_initial_scan:
            from core.startup_log import set_quiet_phase
            set_quiet_phase(False)
            log.warning("IB Gateway not connected at startup — skipping initial scan until connection is live")
        
        try:
            while True:
                if self._shutdown_abort():
                    if not getattr(self, "_shutdown_requested_flag", False):
                        log.info("🛑 Shutdown requested — closing session gracefully...")
                    break

                if getattr(self, "_needs_initial_scan", False):
                    self._needs_initial_scan = False
                    self.ib.sleep(0.2)  # drain IB event queue before scan
                    defer_scan = getattr(self.cfg, "SCAN_DEFER_IB_ON_STARTUP", False)
                    can_boot, boot_state = can_trade_now(self.cfg)
                    use_curated = (
                        not can_boot
                        and getattr(self.cfg, "STARTUP_CURATED_WHEN_NOT_TRADABLE", True)
                        and not defer_scan
                    )
                    if use_curated:
                        log.info(
                            f"🔍 Startup scan: curated lock ({boot_state} — "
                            f"training mode, skip live IB scanner)…"
                        )
                        try:
                            self._scan_and_rank(startup=True, skip_ib_scanner=True)
                        except Exception as exc:
                            log.error(f"Initial scan failed: {exc}")
                    elif defer_scan:
                        log.info("🔍 Startup scan: curated lock (IB scanner deferred)…")
                        try:
                            self._scan_and_rank(startup=True, skip_ib_scanner=True)
                        except Exception as exc:
                            log.error(f"Initial scan failed: {exc}")
                        if getattr(self.cfg, "SCAN_RUN_DEFERRED_IB", True):
                            self._deferred_ib_scan = True
                            sinfo(
                                self.cfg,
                                f"🔍 Deferred IB scanner queued (warmup "
                                f"{getattr(self.cfg, 'IB_SCANNER_WARMUP_SEC', 5):.0f}s)",
                            )
                    else:
                        log.info(
                            f"🔍 Startup scan: live IB scanner "
                            f"(warmup {getattr(self.cfg, 'IB_SCANNER_WARMUP_SEC', 5):.0f}s)…"
                        )
                        try:
                            self._scan_and_rank(startup=True, skip_ib_scanner=False)
                        except Exception as exc:
                            log.error(f"Initial scan failed: {exc}")
                    log.info("✅ Startup lock complete")
                    self._last_scan_time = time.time()
                    can_boot, boot_state = can_trade_now(self.cfg)
                    if not can_boot and getattr(self.cfg, "OFF_HOURS_SUSPEND_MARKET_DATA", True):
                        self._suspend_off_hours_market_data(boot_state)
                    from core.startup_log import set_quiet_phase
                    set_quiet_phase(False)

                in_position = self._in_any_position()
                have_targets = bool(self._locked_targets)
                in_profit = self._any_position_in_profit() if in_position else False
                loop_sec = main_loop_sec(
                    self.cfg,
                    in_position=in_position,
                    have_targets=have_targets,
                    in_profit=in_profit,
                )
                if self._entry_poll_states:
                    loop_sec = min(
                        loop_sec,
                        float(getattr(self.cfg, "ENTRY_PENDING_LOOP_SEC", 0.05)),
                    )
                self.ib.sleep(loop_sec)

                # Fill polls first — don't let councils/scans delay IB bracket confirmation
                if getattr(self.cfg, "PARALLEL_ENTRY_EXIT", True):
                    can_trade_pe, _ = can_trade_now(self.cfg)
                    if can_trade_pe:
                        for _ in range(min(4, max(1, len(self._entry_poll_states)))):
                            self._service_pending_entry()
                            if not self._entry_poll_states:
                                break

                self._service_stream_repairs()
                can_trade_md, _md_state = can_trade_now(self.cfg)
                if can_trade_md:
                    self._resume_tradable_market_data()
                    self.conn.run_pending_session_reclaim()
                    if self.conn.consume_resubscribe_pending():
                        self._resubscribe_all_streams(force=True)
                elif getattr(self.cfg, "OFF_HOURS_SUSPEND_MARKET_DATA", True):
                    self._suspend_off_hours_market_data(_md_state)

                if getattr(self, "_bootstrap_entry_due", False):
                    self._bootstrap_entry_due = False
                    try:
                        self._attempt_scan_bootstrap_entry()
                    except Exception as exc:
                        log.debug(f"Bootstrap entry: {exc}")

                if getattr(self, "_lock_review_due", False) and self._lock_review_picks:
                    self._lock_review_due = False
                    picks = self._lock_review_picks
                    self._lock_review_picks = []
                    try:
                        self._generative_review_locks(picks)
                    except Exception as exc:
                        log.debug(f"Lock review: {exc}")
                    try:
                        lock_names = ", ".join(t.ticker for t in self._locked_targets)
                        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
                            send_dynamic_notification(
                                self.notifier, self.autopilot, "targets_locked",
                                self._notify_context({
                                    "targets": lock_names,
                                    "top_score": self.top_pick.rank_score if self.top_pick else 0,
                                    "scan_ms": getattr(self, "_last_lock_elapsed_ms", 0),
                                }),
                                f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {lock_names}\n"
                                f"Top score: {self.top_pick.rank_score:.0f}",
                                ai_commander=self.ai_commander,
                                consciousness=self.consciousness,
                                pilot=self.pilot,
                            )
                        else:
                            self.notifier.info(
                                f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {lock_names}\n"
                                f"Top score: {self.top_pick.rank_score:.0f}"
                            )
                    except Exception as exc:
                        log.debug(f"Lock notify: {exc}")

                if getattr(self, "_deferred_ib_scan", False) and self.conn.is_connected():
                    self._deferred_ib_scan = False
                    can_defer, _ms = can_trade_now(self.cfg)
                    if can_defer and not self._in_any_position():
                        warmup = float(getattr(self.cfg, "IB_SCANNER_WARMUP_SEC", 3.0))
                        if warmup > 0:
                            log.info(
                                f"🔍 Deferred IB live scanner — warmup {warmup:.0f}s…"
                            )
                            self.ib.sleep(warmup)
                        log.info("🔍 Running deferred IB live scanner…")
                        try:
                            self._scan_and_rank(startup=False)
                            self._last_scan_time = time.time()
                        except Exception as exc:
                            log.warning(f"Deferred IB scanner failed: {exc}")

                if in_position:
                    self._sync_all_positions_from_ib()
                current_px = self._latest_price()
                
                if not self.conn.is_connected():
                    log.warning("IB connection lost. Reconnecting...")
                    if not self.conn.reconnect():
                        break
                    self._refresh_account_balance()
                
                # Update AI buffers periodically (throttled to every 5s)
                now = time.time()
                self._heal_stale_stream_prices(now)
                if now - getattr(self, '_last_ai_update', 0) > 5.0:
                    self._last_ai_update = now
                    try:
                        from core.trading_copilot import maybe_refresh_copilot
                        maybe_refresh_copilot(self)
                    except Exception:
                        pass
                    try:
                        from core.market_context import tick_macro_context_if_due
                        tick_macro_context_if_due()
                    except Exception:
                        pass
                    try:
                        fast_df = self.data.get_fast_bar_dataframe(n=60)
                        if fast_df is not None and len(fast_df) >= 30:
                            self._ai_update_buffers(fast_df, current_px)
                    except Exception:
                        pass
                
                # Detect exits (bracket orders hitting stop/target)
                self._detect_all_exits()
                self._service_deferred_exits()
                self._service_pending_closes()

                market_state = get_market_state(self.cfg)
                if self._last_market_state != market_state:
                    old_state = self._last_market_state or market_state
                    try:
                        self.account_evaluator.on_market_transition(
                            self, old_state, market_state,
                            self.notifier, self.ai_commander, self.autopilot,
                            self.consciousness, self.pilot,
                        )
                    except Exception as exc:
                        log.debug(f"Market transition eval: {exc}")
                    if market_state == "open" and old_state != "open":
                        self._on_rth_open(old_state)
                    if old_state in ("open", "pre_market") and market_state in (
                        "after_hours", "overnight", "closed",
                    ):
                        self._on_day_session_end(market_state)
                    if market_state in ("pre_market", "open"):
                        self._day_session_ended = False
                    self._last_market_state = market_state
                can_trade, market_state = can_trade_now(self.cfg)
                if not can_trade:
                    self._halt_trading_for_closed_market(market_state)

                self._service_pending_ai_councils()
                self._service_shadow_positions()
                if getattr(self.cfg, "PARALLEL_ENTRY_EXIT", True) and can_trade:
                    self._service_pending_entry()

                if can_trade:
                    try:
                        self._service_tick_spike_queue()
                    except Exception as exc:
                        log.error(f"Tick spike monitor failed: {exc}")

                # AI-driven early exit check (when in position, non-blocking)
                if in_position and self.model is not None and can_trade:
                    ai_exit_interval = ai_exit_check_sec(self.cfg, self._any_position_in_profit())
                    if now - getattr(self, "_last_ai_exit_check", 0) >= ai_exit_interval:
                        self._last_ai_exit_check = now
                        for ticker in list(self._position_slots.keys()):
                            try:
                                if not self._load_position_context(ticker):
                                    continue
                                px = self._live_price_for(ticker, self._entry_price)
                                if self._has_ai_council(ticker, "exit_decision"):
                                    continue
                                if self._has_ai_council(ticker, "position_manage"):
                                    continue
                                ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(px)
                                if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                                    pnl_pct = (
                                        ((px / self._entry_price) - 1) * 100
                                        if self._entry_price else 0
                                    )
                                    exit_ctx = {
                                        "ticker": ticker,
                                        "price": px,
                                        "pnl_pct": round(pnl_pct, 2),
                                        "entry": self._entry_price,
                                        "stop": self._position_stop,
                                        "target": self._position_target,
                                    }
                                    obs = self._build_ppo_obs(px)
                                    bar_df = (
                                        pd.DataFrame(self._bar_df_buffer)
                                        if self._bar_df_buffer else None
                                    )
                                    ai_dec = self.ai_commander.decide_exit(
                                        exit_ctx, obs=self._build_ppo_obs(px), bar_df=bar_df,
                                    )
                                    if ai_dec.get("pending"):
                                        self._set_ai_council(ticker, "exit_decision", {
                                            "fingerprint": ai_dec["fingerprint"],
                                            "ppo_exit": ppo_exit,
                                            "ppo_conf": ppo_conf,
                                            "ppo_reason": ppo_reason,
                                            "min_conf": float(
                                                getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)
                                            ),
                                            "ctx": exit_ctx,
                                            "current_px": px,
                                        })
                                        log.info(
                                            f"  🧠 COUNCIL exit {ticker}: "
                                            f"{(ai_dec.get('reason') or 'deliberating')[:80]}"
                                        )
                                        continue
                                    should_exit = bool(ai_dec.get("exit"))
                                    ai_conf = float(ai_dec.get("confidence", ppo_conf))
                                    ai_reason = str(ai_dec.get("reason", ppo_reason))
                                else:
                                    should_exit = ppo_exit
                                    ai_conf = ppo_conf
                                    ai_reason = ppo_reason
                                if should_exit and ai_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                                    log.info(
                                        f"  🧠 AI EARLY EXIT {ticker}: confidence={ai_conf:.0%} "
                                        f"— {ai_reason[:80]}"
                                    )
                                    self._exit_position(px, "ai_early_exit", ticker=ticker)
                                    self._save_position_context(ticker)
                            except Exception:
                                pass
                        self._refresh_aggregate_position_state()
                
                if can_trade:
                    if self.conn.is_connected():
                        now = time.time()
                        time_since_scan = now - self._last_scan_time
                        
                        # FOCUS MODE: when targets are locked, do NOT rescan the full
                        # universe (87s blocking scan kills tick monitoring + entries).
                        # Only rescan after 30 min with no entry, or when flat with no targets.
                        have_targets = len(self._locked_targets) > 0
                        in_position = self._in_any_position()

                        if have_targets and not in_position:
                            self._maybe_soft_rotate_lock(now)
                            self._maybe_merge_lock_from_scanner(now)
                            if self._maybe_release_stale_lock(now):
                                have_targets = False

                        if in_position:
                            need_rescan = False
                        elif have_targets:
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
                            if now - getattr(self, "_last_bg_watch", 0) >= background_watch_sec(self.cfg):
                                self._last_bg_watch = now
                                self._silent_background_watch()

                        # Entry scout + exit monitor run in parallel (never pause scouting while holding)
                        parallel = getattr(self.cfg, "PARALLEL_ENTRY_EXIT", True)
                        at_max = self._open_position_count() >= self._max_concurrent()
                        if self._locked_targets and (not in_position or parallel):
                            if self.risk.is_halted():
                                self._service_loss_streak_learning()
                            if not in_position:
                                self._maybe_rotate_locked_focus(now)
                            monitor_iv = fast_monitor_interval(self.cfg)
                            if now - getattr(self, '_last_fast_monitor', 0) > monitor_iv:
                                self._last_fast_monitor = now
                                try:
                                    self._fast_monitor_locked(scout_only=at_max)
                                except Exception as exc:
                                    log.error(f"Fast monitor failed: {exc}")
                            self._drain_bar_prefetch_queue()
                            self._tick_bar_warm_on_main()
                            prefetch_iv = float(getattr(self.cfg, "LIVE_AI_PREFETCH_SEC", 1.0))
                            if now - getattr(self, "_last_ai_prefetch", 0) >= prefetch_iv:
                                self._last_ai_prefetch = now
                                try:
                                    self._prefetch_live_ai_hotline()
                                except Exception:
                                    pass
                            if not in_position:
                                self._log_flat_heartbeat()
                        
                        # LIVE POSITION: sub-second monitoring + AI trail (never idle after entry)
                        if in_position:
                            self._monitor_all_open_positions()
                else:
                    if now - getattr(self, "_last_market_closed_log", 0) >= 60.0:
                        self._last_market_closed_log = now
                        log.info(
                            f"⏸ NO TRADING SESSION ({market_state}) — "
                            f"enabled: {allowed_trading_sessions_label(self.cfg)} | training mode"
                        )
                    train_iv = float(getattr(self.cfg, "OFF_HOURS_TRAIN_INTERVAL_SEC", 3600))
                    if now - getattr(self, "_last_off_hours_train", 0) >= train_iv:
                        self._last_off_hours_train = now
                        self._train_off_hours()
                
                self._refresh_account_balance()
                maybe_refresh_session_limits(self)
                self._write_live_metrics()
                rt = getattr(self, "_halim_runtime", None)
                if rt is not None:
                    try:
                        rt.tick(self)
                    except Exception as exc:
                        log.debug(f"Halim runtime tick: {exc}")
                self._maybe_daily_push()
                if getattr(self.cfg, "LEARNING_SYNC_INTERVAL_SEC", 1800) > 0:
                    sync_iv = float(getattr(self.cfg, "LEARNING_SYNC_INTERVAL_SEC", 1800))
                    if now - getattr(self, "_last_learning_push", 0) >= sync_iv:
                        self._last_learning_push = now
                        try:
                            push_learning_checkpoint_async("periodic")
                        except Exception:
                            pass

                cleanup_iv = float(getattr(self.cfg, "PERIODIC_CLEANUP_SEC", 1800))
                _now = time.time()
                replay_mode = os.getenv("REPLAY_LIVE", "").lower() in ("1", "true", "yes")
                if cleanup_iv > 0 and not replay_mode and _now - getattr(self, "_last_periodic_cleanup", 0) >= cleanup_iv:
                    self._last_periodic_cleanup = _now
                    try:
                        from core.memory_guard import is_memory_pressured
                        from core.smart_stack import live_ram_only
                        ram_only_live = live_ram_only(self.cfg) and can_trade
                        if ram_only_live:
                            if is_memory_pressured(
                                int(getattr(self.cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024))
                            ) and _now - getattr(self, "_last_ram_live_warn", 0) >= 300.0:
                                self._last_ram_live_warn = _now
                                log.warning(
                                    "  ⚠️ RAM pressure during live session — "
                                    "RAM_LIVE_ONLY: no disk sweep (off-hours cleanup)"
                                )
                        elif is_memory_pressured(
                            int(getattr(self.cfg, "OLLAMA_MIN_FREE_RAM_MB", 1024))
                        ):
                            run_periodic_cleanup(self.cfg, force=True)
                        elif not can_trade:
                            run_periodic_cleanup(self.cfg, force=False)
                    except Exception as exc:
                        log.debug(f"Periodic cleanup: {exc}")
                
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — graceful shutdown...")
        finally:
            self._shutdown()

    def _register_shutdown_signals(self):
        import signal

        def _handler(signum, _frame):
            log.info(f"Signal {signum} received — graceful shutdown...")
            try:
                from core.learning_persistence import emergency_snapshot
                emergency_snapshot(self.cfg, model=getattr(self, "model", None), runner=self)
            except Exception:
                pass
            self._shutdown_requested_flag = True
            try:
                self.ib.sleep(0)
            except Exception:
                pass
            if getattr(self, "_shutdown_done", False):
                import os
                os._exit(0)

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def _shutdown_abort(self) -> bool:
        """True when stop script or signal requested exit."""
        if getattr(self, "_shutdown_requested_flag", False):
            return True
        try:
            from core.shutdown_control import shutdown_requested
            return shutdown_requested()
        except Exception:
            return False
    
    def _scan_one(self, ticker: str, fast: bool = False) -> Optional[Dict]:
        """
        Scan one ticker. fast=True: 30min 1m bars only (HFT scan pass).
        Full pass adds MTF + AI scoring on refine phase.
        """
        if ticker in self._contract_blacklist:
            return None
        blocked, md_reason = is_market_data_blocked(self.cfg, ticker)
        if blocked:
            log.debug(f"  ⏭ {ticker}: MD blocked — {md_reason[:80]}")
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
                    self._store_scan_cache(ticker, hist_1m)

            if score and score.get("total_score", 0) > 0:
                log.debug(f"  ✅ {ticker}: score={score['total_score']:.1f} | {score.get('reasons', '')[:60]}")
            else:
                reason = score.get('reasons', 'no_data') if score else 'no_data'
                log.debug(f"  ❌ {ticker}: {reason}")

            return score if score and score.get("total_score", 0) > 0 else None
        except Exception as exc:
            msg = str(exc)
            record_fetch_failure(self.cfg, ticker, exc, bar_size="1 min")
            if "Could not qualify" in msg or "No security definition" in msg:
                if should_permanent_blacklist(self.cfg, "no IB contract"):
                    self._contract_blacklist.add(ticker)
                record_failure_for_learning(
                    self.cfg, ticker=ticker, reason=msg[:200], event="scan_contract",
                )
                log.debug(f"  ⏭ {ticker}: no IB contract (recorded for learning)")
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
    
    def _scan_and_rank(self, startup: bool = False, skip_ib_scanner: bool = False):
        t0 = time.perf_counter()
        from core.startup_log import sinfo
        sinfo(self.cfg, "🔍 HANOON scan: fetching live IB universe…")
        screen_list, universe_source = get_live_scan_universe(
            self.scanner, self.conn, self.cfg,
            startup=startup, skip_ib_scanner=skip_ib_scanner,
        )
        self._last_universe_source = universe_source
        if not screen_list:
            log.warning("⏸ Scan skipped — no tickers in universe")
            return

        if getattr(self.cfg, "FAST_SCANNER_LOCK", True):
            locked = self._scan_and_rank_fast_lock(screen_list, t0)
            if locked or not getattr(self.cfg, "FAST_SCANNER_LOCK_FALLBACK", False):
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
            defer_mtf = not getattr(self.cfg, "SCAN_MTF_DURING_RTH", False)
            market_open = get_market_state() == "open"
            if not (defer_mtf and market_open):
                results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
                top_n = int(getattr(self.cfg, "SCAN_REFINE_TOP_N", 12))
                refine_pool = results[:top_n]
                log.info(f"🔬 Refining top {len(refine_pool)} with MTF + AI...")
                results = self._refine_scan_candidates(refine_pool) + results[top_n:]
            else:
                log.info("⚡ MTF refine deferred during RTH — bars prefetched after lock")
        
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(f"Scan: {len(results)}/{scan_count} qualified in {elapsed_ms:.0f}ms")
        
        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            results = self.ai_commander.rank_scan_results(results)
        try:
            from core.war_account import adjust_scan_results
            results = adjust_scan_results(self.cfg, results)
        except Exception:
            pass
        
        # Debug: log score distribution
        if results:
            scores = [r["total_score"] for r in results[:5]]
            log.debug(f"Score distribution: top5={scores}")
        
        self._commit_scan_lock(results, elapsed_ms)

    def _scan_and_rank_fast_lock(self, screen_list: List[str], t0: float) -> bool:
        """
        Lock from IB scanner metadata only (no per-ticker historical fetch).
        Returns True if targets were locked or lock was attempted (skip slow path).
        """
        hits = self.scanner.get_scanner_hits()
        results: List[Dict] = []
        for idx, ticker in enumerate(screen_list):
            if ticker in self._contract_blacklist:
                continue
            hit = hits.get(ticker)
            if hit is None:
                hit = ScannerHit(ticker=ticker, rank=idx, scan_code="live")
            scored = StockScanner.score_scanner_hit(hit, list_index=idx)
            if scored.get("total_score", 0) > 0:
                results.append(scored)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        src = getattr(self, "_last_universe_source", "ib_live")
        src_labels = {
            "ib_live": "IB live scanner",
            "startup_curated": "startup curated list",
            "session_curated": "session curated list",
            "emergency_fallback": "emergency fallback",
        }
        src_label = src_labels.get(src, src)
        from core.startup_log import startup_compact
        lock_line = (
            f"⚡ FAST LOCK: {len(results)}/{len(screen_list)} from {src_label} "
            f"in {elapsed_ms:.0f}ms"
        )
        if startup_compact(self.cfg):
            log.info(lock_line)
        else:
            log.info(
                f"⚡ SCAN FAST LOCK: {len(results)}/{len(screen_list)} ranked "
                f"from {src_label} in {elapsed_ms:.0f}ms (no bar fetch)"
            )

        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander and results:
            results = self.ai_commander.rank_scan_results(results)
        try:
            from core.war_account import adjust_scan_results
            results = adjust_scan_results(self.cfg, results)
        except Exception:
            pass

        min_lock_score = effective_min_lock_score(self.cfg)
        min_candidates = effective_min_lock_candidates(self.cfg)
        qualified = [r for r in results if r.get("total_score", 0) >= min_lock_score]
        if len(qualified) < min_candidates:
            top_hint = ""
            if results:
                best = results[0]
                top_hint = f" | best={best['ticker']}@{best.get('total_score', 0):.0f}"
            log.info(
                f"🔍 Fast lock skipped — {len(qualified)}/{min_candidates} names above "
                f"score {min_lock_score:.0f}{top_hint}"
            )
            return False

        return self._commit_scan_lock(qualified, elapsed_ms, fast_lock=True)

    def _commit_scan_lock(
        self,
        results: List[Dict],
        elapsed_ms: float,
        fast_lock: bool = False,
    ) -> bool:
        """Apply lock pool, notify, stream, and optional bar prefetch."""
        min_lock_score = effective_min_lock_score(self.cfg)
        min_candidates = effective_min_lock_candidates(self.cfg)
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
            return False
        
        self.scan_results = qualified[: self._max_locked()]

        max_price = getattr(self.cfg, "PENNY_STOCK_MAX_PRICE", 500.0)
        hits = self.scanner.get_scanner_hits()
        from core.universe_filter import passes_profit_hunt_universe
        pool = [
            r for r in qualified
            if r.get("price", 0.0) <= max_price
            and passes_profit_hunt_universe(
                self.cfg,
                r["ticker"],
                str((hits.get(r["ticker"]) or ScannerHit(ticker=r["ticker"])).primary_exchange),
                price=float(r.get("price", 0) or 0),
            )[0]
            and r["ticker"] not in self._contract_blacklist
        ]
        pool.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        tradeable = set(filter_tradeable_tickers(self.cfg, [r["ticker"] for r in pool]))
        pool = [r for r in pool if r["ticker"] in tradeable]
        pool_note = ""
        try:
            from core.scan_lock_pools import build_kill_fit_lock_pool, tier_pool_summary
            locked = build_kill_fit_lock_pool(
                self.cfg, pool, self._max_locked(), hits,
            )
            pool_note = tier_pool_summary(locked, hits, self.cfg)
        except Exception:
            locked = pool[: self._max_locked()]
        try:
            from core.war_account import filter_locked_pool
            locked_objs = [
                ScanResult(
                    ticker=r["ticker"], price=r.get("price", 0.0), volume=r.get("volume", 0),
                    avg_volume=r.get("avg_volume", 0), relative_volume=r.get("rel_vol", 1.0),
                    rank_score=r["total_score"], reason=r.get("reasons", ""),
                )
                for r in locked
            ]
            locked_objs = filter_locked_pool(self.cfg, locked_objs)
            locked_tickers = {p.ticker for p in locked_objs}
            locked = [r for r in locked if r["ticker"] in locked_tickers]
        except Exception:
            pass
        if not locked and qualified:
            locked = sorted(qualified, key=lambda x: x.get("total_score", 0), reverse=True)[:3]

        penny_results = locked
        
        if not penny_results:
            self.top_pick = None
            self._locked_targets = []
            log.info(f"🔍 No setups found in full universe scan ({elapsed_ms:.0f}ms)")
            return False

        self._locked_targets = []
        for r in penny_results:
            hit = hits.get(r["ticker"])
            px = float(r.get("price", 0) or 0)
            if px <= 0 and hit is not None:
                px = float(getattr(hit, "price", 0) or 0)
            pick = ScanResult(
                ticker=r["ticker"], price=px, volume=r.get("volume", 0),
                avg_volume=r.get("avg_volume", 0), relative_volume=r.get("rel_vol", 1.0),
                rank_score=r["total_score"], reason=r.get("reasons", ""),
            )
            self._locked_targets.append(pick)
        self._locked_targets = prioritize_locked_targets(
            self._locked_targets,
            self.cfg,
            self._locked_targets[0].ticker if self._locked_targets else None,
            hits=hits,
        )
        self.top_pick = self._locked_targets[0] if self._locked_targets else None
        self._targets_locked_at = time.time()
        self._focus_target_index = 0
        self._last_focus_rotate = 0.0
        names = ", ".join([p.ticker for p in self._locked_targets])
        lock_tag = "FAST" if fast_lock else "FULL"
        from core.startup_log import startup_compact, sinfo
        log.info(
            f"🎯 LOCKED ({len(self._locked_targets)}): {names} | {lock_tag} {elapsed_ms:.0f}ms{pool_note}"
        )
        self._last_lock_elapsed_ms = elapsed_ms
        if not startup_compact(self.cfg):
            log.info(
                f"🔒 COMMITTED LOCK: scores≥{min_lock_score:.0f} | "
                + (
                    f"priority focus ({warm_priority_count(self.cfg)} warm + "
                    f"{stream_priority_count(self.cfg)} stream)"
                    if ai_fast_execution(self.cfg)
                    else f"rotate every {getattr(self.cfg, 'LOCK_FOCUS_ROTATE_SEC', 0):.0f}s"
                )
                + f" | stale release {getattr(self.cfg, 'LOCK_STALE_RELEASE_SEC', 600):.0f}s"
            )
            if ai_fast_execution(self.cfg):
                priority = self._priority_tickers()
                log.info(
                    f"⚡ AI FAST EXEC: {len(priority)} tickers "
                    f"[{','.join(priority[:8])}{'…' if len(priority) > 8 else ''}] | "
                    f"monitor {fast_monitor_interval(self.cfg):.2f}s"
                )
        self._ensure_locked_streams(quiet=True)
        self._schedule_bar_prefetch([p.ticker for p in self._locked_targets])
        self._bar_warm_due = True
        self._bar_warm_idx = 0

        if getattr(self.cfg, "DEFER_LOCK_AI_REVIEW", True):
            self._lock_review_due = True
            self._lock_review_picks = list(penny_results)
        else:
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
            elif not getattr(self.cfg, "DEFER_LOCK_AI_REVIEW", True):
                self.notifier.info(
                    f"🎯 LOCKED TARGETS ({len(self._locked_targets)}): {names}\n"
                    f"Top score: {self.top_pick.rank_score:.0f}"
                )

        if getattr(self.cfg, "SCAN_BOOTSTRAP_ENTRY", True):
            self._bootstrap_entry_due = True

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
        return True

    def _schedule_bar_prefetch(self, tickers: List[str]):
        """Queue 1-min bar prefetch — priority names at front of queue."""
        priority = self._priority_tickers() if self._locked_targets else []
        priority_set = {t.upper() for t in priority}
        ordered = [t for t in priority if t in tickers]
        ordered += [t for t in tickers if t.upper() not in priority_set]
        for ticker in ordered:
            if (
                ticker
                and ticker not in self._scan_data_cache
                and ticker not in self._bar_prefetch_queue
            ):
                blocked, _ = is_market_data_blocked(self.cfg, ticker)
                if blocked:
                    continue
                self._bar_prefetch_queue.append(ticker)
        if self._bar_prefetch_queue:
            log.debug(f"Bar prefetch queued: {self._bar_prefetch_queue[:12]}")

    def _priority_tickers(self) -> List[str]:
        """All top-priority names monitored simultaneously (not a single rotating focus)."""
        if not self._locked_targets:
            return []
        return monitor_ticker_list(self._locked_targets, self.cfg)

    def _priority_ticker_set(self) -> set:
        return {t.upper() for t in self._priority_tickers()}

    def _build_ppo_obs(self, current_px: float) -> Optional[np.ndarray]:
        return build_ppo_observation(
            self._feature_buffer,
            self.cfg,
            current_px,
            float(self.bot_cash),
            float(self.shares),
        )

    def _min_bars_for(self, ticker: str) -> int:
        return min_bars_for_ticker(
            self.cfg,
            ticker,
            priority_names=self._priority_tickers() if self._locked_targets else None,
        )

    def _prefetch_one_ticker_bars(self, ticker: str, quiet: bool = True) -> Optional[pd.DataFrame]:
        """Fetch bars for one ticker — live stream first; HMDS only when allowed."""
        if not ticker or ticker in self._contract_blacklist:
            return None
        blocked, _ = is_market_data_blocked(self.cfg, ticker)
        if blocked:
            return None
        need = self._min_bars_for(ticker)
        cached = self._scan_data_cache.get(ticker)
        if cached is not None and len(cached) >= need:
            return cached

        if getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
            live_df = self._bars_from_stream(ticker, need)
            if live_df is not None:
                return live_df

        if skip_historical_prefetch(self.cfg) and self._stream_has_price(ticker):
            return None
        soft_skip = (
            bool(getattr(self.cfg, "MD_SOFT_FAIL_HMDS", True))
            and ticker in self._target_monitors
        )
        try:
            from core.sniper_execution import sniper_active, sniper_force_bar_prefetch
            if (
                soft_skip
                and sniper_force_bar_prefetch(self.cfg)
                and sniper_active(self.cfg)
            ):
                prio = {n.upper() for n in (self._priority_tickers() or [])}
                if ticker.upper() in prio:
                    soft_skip = False
        except Exception:
            pass
        if soft_skip:
            return None
        try:
            from core.rth_session import historical_prefetch_allowed
            if not historical_prefetch_allowed(self.cfg):
                return None
        except Exception:
            return None

        cfg_ticker = self.cfg.TICKER
        try:
            self.cfg.TICKER = ticker
            dm = DataManager(self.conn, self.cfg)
            duration = getattr(self.cfg, "SCAN_BAR_DURATION", "1800 S")
            if getattr(self.cfg, "PAPER_TRADING", False):
                duration = getattr(self.cfg, "PAPER_SCAN_BAR_DURATION", "420 S")
            fresh = dm.fetch_historical(
                duration=duration, bar_size="1 min", use_rth=False, quiet=quiet,
            )
            min_accept = need if ai_fast_execution(self.cfg) else 20
            if fresh is not None and len(fresh) >= min_accept:
                self._store_scan_cache(ticker, fresh)
                if fresh["close"].iloc[-1] > 0:
                    for target in self._locked_targets:
                        if target.ticker == ticker and target.price <= 0:
                            target.price = float(fresh["close"].iloc[-1])
                return fresh
            if fresh is not None and len(fresh) >= 3 and ai_fast_execution(self.cfg):
                self._store_scan_cache(ticker, fresh)
                return fresh
        except Exception as exc:
            record_fetch_failure(self.cfg, ticker, exc, bar_size="1 min")
            log.debug(f"Bar prefetch {ticker}: {exc}")
        finally:
            self.cfg.TICKER = cfg_ticker
        return None

    def _drain_bar_prefetch_queue(self):
        """Non-blocking bar prefetch — priority names first when fast execution on."""
        per_loop = prefetch_per_loop(self.cfg)
        if ai_fast_execution(self.cfg) and self._locked_targets:
            priority = self._priority_tickers()
            for ticker in priority:
                cached = self._scan_data_cache.get(ticker)
                need = self._min_bars_for(ticker)
                if cached is not None and len(cached) >= need:
                    continue
                self._prefetch_one_ticker_bars(ticker, quiet=True)
                per_loop -= 1
                if per_loop <= 0:
                    return
        for _ in range(max(1, per_loop)):
            if not self._bar_prefetch_queue:
                return
            ticker = self._bar_prefetch_queue.pop(0)
            if ticker in self._scan_data_cache:
                continue
            self._prefetch_one_ticker_bars(ticker, quiet=True)

    def _warm_locked_bar_cache(self):
        """Fetch 1-min bars for ALL priority locked names — spike monitor ready ASAP."""
        budget = warm_budget_sec(self.cfg)
        t0 = time.perf_counter()
        warmed = 0
        priority = self._priority_tickers()
        for ticker in priority:
            if time.perf_counter() - t0 > budget:
                break
            if self._prefetch_one_ticker_bars(ticker, quiet=True) is not None:
                warmed += 1
        remaining = [
            t.ticker for t in self._locked_targets
            if t.ticker not in self._scan_data_cache
        ]
        if remaining:
            self._schedule_bar_prefetch(remaining)
        priority_ready = sum(
            1 for t in priority
            if t in self._scan_data_cache
            and len(self._scan_data_cache[t]) >= self._min_bars_for(t)
        )
        total_ready = sum(
            1 for t in self._locked_targets
            if t.ticker in self._scan_data_cache
            and len(self._scan_data_cache[t.ticker]) >= self._min_bars_for(t.ticker)
        )
        log.info(
            f"📊 Bar cache: {priority_ready}/{len(priority)} priority ready | "
            f"{total_ready}/{len(self._locked_targets)} total locked"
        )

    def _tick_bar_warm_on_main(self) -> None:
        """Prefetch IB bars on main loop — multiple tickers per tick when configured."""
        if not self._bar_warm_due or not self._locked_targets:
            return
        priority = self._priority_tickers()
        idx = self._bar_warm_idx
        per_loop = int(getattr(self.cfg, "BAR_WARM_PER_LOOP", 4))
        warmed = 0
        while idx < len(priority) and warmed < per_loop:
            ticker = priority[idx]
            need = self._min_bars_for(ticker)
            cached = self._scan_data_cache.get(ticker)
            if cached is not None and len(cached) >= need:
                idx += 1
                continue
            if self._stream_has_price(ticker):
                self._bars_from_stream(ticker, need)
                idx += 1
                warmed += 1
                continue
            self._prefetch_one_ticker_bars(ticker, quiet=True)
            idx += 1
            warmed += 1
        self._bar_warm_idx = idx
        if idx >= len(priority):
            self._bar_warm_due = False
            self._bar_warm_idx = 0
            priority_ready = sum(
                1 for t in priority
                if t in self._scan_data_cache
                and len(self._scan_data_cache[t]) >= self._min_bars_for(t)
            )
            total_ready = sum(
                1 for t in self._locked_targets
                if t.ticker in self._scan_data_cache
                and len(self._scan_data_cache[t.ticker]) >= self._min_bars_for(t.ticker)
            )
            log.info(
                f"📊 Bar cache: {priority_ready}/{len(priority)} priority ready | "
                f"{total_ready}/{len(self._locked_targets)} total locked"
            )

    def _locked_target_rows(self) -> List[Dict]:
        return [
            {
                "ticker": t.ticker,
                "price": t.price,
                "volume": t.volume,
                "avg_volume": t.avg_volume,
                "rel_vol": t.relative_volume,
                "total_score": t.rank_score,
                "reasons": t.reason,
            }
            for t in self._locked_targets
        ]

    def _apply_lock_row_merge(
        self,
        merged_rows: List[Dict],
        added: List[str],
        removed: List[str],
        tag: str = "MERGE",
    ) -> bool:
        if not merged_rows:
            return False
        hits = self.scanner.get_scanner_hits()
        self._locked_targets = []
        for r in merged_rows:
            hit = hits.get(r["ticker"])
            px = float(r.get("price", 0) or 0)
            if px <= 0 and hit is not None:
                px = float(getattr(hit, "price", 0) or 0)
            self._locked_targets.append(
                ScanResult(
                    ticker=r["ticker"],
                    price=px,
                    volume=r.get("volume", 0),
                    avg_volume=r.get("avg_volume", 0),
                    relative_volume=r.get("rel_vol", 1.0),
                    rank_score=r["total_score"],
                    reason=r.get("reasons", ""),
                )
            )
        self._locked_targets = prioritize_locked_targets(
            self._locked_targets, self.cfg, hits=hits,
        )
        self.top_pick = self._locked_targets[0] if self._locked_targets else None
        names = ", ".join(t.ticker for t in self._locked_targets)
        from core.scan_lock_pools import tier_pool_summary
        pool_note = tier_pool_summary(merged_rows, hits, self.cfg)
        change = ""
        if added or removed:
            change = f" +{','.join(added)}" if added else ""
            if removed:
                change += f" -{','.join(removed)}"
        log.info(f"🔄 LOCK {tag}: {names}{pool_note}{change}")
        for tk in removed:
            if tk in self._target_monitors:
                self._stop_target_stream(tk)
        self._ensure_locked_streams(quiet=True)
        self._schedule_bar_prefetch([p.ticker for p in self._locked_targets])
        return True

    def _maybe_soft_rotate_lock(self, now: float) -> bool:
        """Drop weakest stale tail slots — keeps top names; opens room for scanner merge."""
        rotate_sec = float(os.getenv("SCAN_SOFT_ROTATE_SEC", "180"))
        if rotate_sec <= 0 or self._in_any_position() or not self._locked_targets:
            return False
        if now - self._last_soft_rotate < rotate_sec:
            return False
        self._last_soft_rotate = now

        drop_n = int(os.getenv("SCAN_SOFT_ROTATE_DROP", "2"))
        protect_n = int(os.getenv("SCAN_SOFT_ROTATE_PROTECT", "5"))
        if drop_n <= 0:
            return False

        hits = self.scanner.get_scanner_hits()
        from core.scan_lock_pools import kill_fit_score

        scored: List[Tuple[float, ScanResult, bool]] = []
        for target in self._locked_targets:
            row = {
                "ticker": target.ticker,
                "price": target.price,
                "total_score": target.rank_score,
            }
            kfs = kill_fit_score(row, hits, self.cfg)
            last_touch = self._lock_spike_touch_at.get(target.ticker, 0.0)
            stale = (now - last_touch) > rotate_sec if last_touch > 0 else True
            scored.append((kfs, target, stale))
        scored.sort(key=lambda x: x[0], reverse=True)

        dropped: List[ScanResult] = []
        for kfs, target, stale in scored[protect_n:]:
            if len(dropped) >= drop_n:
                break
            if not stale:
                continue
            dropped.append(target)
        if not dropped:
            return False

        drop_set = {t.ticker for t in dropped}
        self._locked_targets = [t for t in self._locked_targets if t.ticker not in drop_set]
        for t in dropped:
            self._stop_target_stream(t.ticker)
        self.top_pick = self._locked_targets[0] if self._locked_targets else None
        log.info(
            f"🔄 Soft rotate — dropped [{', '.join(t.ticker for t in dropped)}] | "
            f"keeping {len(self._locked_targets)}"
        )
        self._soft_merge_due = True
        return True

    def _maybe_merge_lock_from_scanner(self, now: float) -> bool:
        """Light IB scanner refresh — fill open slots or upgrade weak tail without full rescan."""
        merge_sec = float(os.getenv("SCAN_MERGE_SEC", "120"))
        slots_open = len(self._locked_targets) < self._max_locked()
        if not self._soft_merge_due:
            if self._in_any_position() or not self._locked_targets:
                return False
            if not slots_open and now - self._last_merge_scan < merge_sec:
                return False
        if now - self._last_merge_scan < 15.0:
            return False

        self._last_merge_scan = now
        self._soft_merge_due = False

        screen_list = self.scanner.get_dynamic_universe(self.conn, force=False)
        if not screen_list:
            return False

        hits = self.scanner.get_scanner_hits()
        fresh: List[Dict] = []
        for idx, ticker in enumerate(screen_list[:50]):
            if ticker in self._contract_blacklist:
                continue
            hit = hits.get(ticker)
            if hit is None:
                hit = ScannerHit(ticker=ticker, rank=idx, scan_code="live")
            scored = StockScanner.score_scanner_hit(hit, list_index=idx)
            if scored.get("total_score", 0) > 0:
                fresh.append(scored)
        if not fresh:
            return False

        from core.scan_lock_pools import merge_kill_fit_lock_pool
        merged, added, removed = merge_kill_fit_lock_pool(
            self.cfg,
            self._locked_target_rows(),
            fresh,
            self._max_locked(),
            hits,
        )
        if not added and not removed and not slots_open:
            return False
        return self._apply_lock_row_merge(merged, added, removed)

    def _maybe_release_stale_lock(self, now: float) -> bool:
        """Last resort — full clear only after long quiet (soft rotate handles churn)."""
        if not self._locked_targets or self._in_any_position():
            return False
        stale_sec = float(getattr(self.cfg, "LOCK_STALE_RELEASE_SEC", 900.0))
        if stale_sec <= 0:
            return False
        locked_for = now - self._targets_locked_at
        if locked_for < stale_sec:
            return False
        names = ", ".join(t.ticker for t in self._locked_targets)
        log.info(
            f"🔓 Stale lock release — no entry in {locked_for:.0f}s | "
            f"clearing [{names}] → rescan"
        )
        for t in list(self._target_monitors.keys()):
            self._stop_target_stream(t)
        self._locked_targets = []
        self.top_pick = None
        self._targets_locked_at = 0.0
        self._bar_prefetch_queue.clear()
        self._last_scan_time = 0.0
        return True

    def _maybe_rotate_locked_focus(self, now: float):
        """Rotate live tick stream across locked names — disabled when all priority watched."""
        if ai_fast_execution(self.cfg) or not focus_rotation_enabled(self.cfg):
            return
        if len(self._locked_targets) < 2:
            return
        rotate_sec = float(getattr(self.cfg, "LOCK_FOCUS_ROTATE_SEC", 60.0))
        if rotate_sec <= 0:
            return
        if now - self._last_focus_rotate < rotate_sec:
            return
        self._last_focus_rotate = now
        self._focus_target_index = (self._focus_target_index + 1) % len(self._locked_targets)
        pick = self._locked_targets[self._focus_target_index]
        if not getattr(self.cfg, "FOCUS_PIN_TOP_PICK", False):
            self.top_pick = pick
        self._ensure_locked_streams(quiet=True)
        log.info(
            f"🔄 Focus rotate → {pick.ticker} "
            f"({self._focus_target_index + 1}/{len(self._locked_targets)})"
        )
    
    def _generative_review_locks(self, picks: List[Dict]):
        """AI council ranks and comments on locked targets."""
        if not picks or not getattr(self.cfg, "GENERATIVE_THINKING_ENABLED", True):
            return
        if is_ai_council_mode(self.cfg) and self.ai_commander:
            try:
                review = self.ai_commander.review_lock_watchlist(picks)
                thought = review.get("commentary", "")
                if not thought and not review.get("pending"):
                    thought = f"Gut pick: {review.get('gut_pick', '')}"
                if thought:
                    log.info(f"🧠 COUNCIL watchlist: {thought[:400]}")
            except Exception:
                pass
            return
        names = ", ".join(r["ticker"] for r in picks[:5])
        log.info(f"🎯 LOCKED watchlist (no ambient API): {names}")
        return

    def _focused_ticker(self) -> Optional[str]:
        """Best-ranked pick for entry context — NOT the only monitored ticker."""
        if self.top_pick:
            return self.top_pick.ticker
        if not self._locked_targets:
            return None
        priority = self._priority_tickers()
        return priority[0] if priority else self._locked_targets[0].ticker

    def _service_stream_repairs(self) -> None:
        """Restart streams outside IB error callbacks (avoids nested event loop)."""
        if self._md_suspended or not self._stream_repair:
            return
        for ticker, mode in list(self._stream_repair.items()):
            self._stream_repair.pop(ticker, None)
            if mode == "realtime" and self._stream_modes.get(ticker) == "realtime":
                dm = self._target_monitors.get(ticker)
                if dm is not None and dm.has_live_stream():
                    continue
            if ticker in self._target_monitors:
                self._stop_target_stream(ticker)
            log.debug(f"  📡 {ticker}: switching to 5s bars")
            self._start_target_stream(ticker, quiet=True, stream_mode=mode)

    def _ensure_focus_stream(self, quiet: bool = False):
        """Backward-compatible alias — starts all locked streams when enabled."""
        self._ensure_locked_streams(quiet=quiet)

    def _on_tick_stream_limit(self, ticker: str, error_code: int, message: str):
        """IB 10189/10190 — tick-by-tick unavailable; fall back to 5s bars on next loop tick."""
        if ticker:
            self._tick_limit_denied.add(ticker.upper())
            dm = self._target_monitors.get(ticker)
            if dm is not None and dm.has_live_stream() and dm._realtime_handle is not None:
                return
            self._stream_repair[ticker.upper()] = "realtime"

    def _active_tick_stream_count(self) -> int:
        return sum(1 for mode in self._stream_modes.values() if mode == "tick")

    def _ensure_locked_streams(self, quiet: bool = False):
        """
        Keep live data on priority locked tickers.
        Top N get tick-by-tick (IB cap ~5); rest get 5-second real-time bars.
        """
        if not self._locked_targets:
            return
        watch_all = getattr(self.cfg, "WATCH_ALL_LOCKED_STREAMS", True)
        if watch_all and ai_fast_execution(self.cfg):
            ordered = prioritize_locked_targets(
                self._locked_targets,
                self.cfg,
                hits=self.scanner.get_scanner_hits(),
            )
            wanted = [t.ticker for t in ordered[: self._max_locked()]]
        elif watch_all:
            wanted = [
                t.ticker for t in self._locked_targets[: self._max_locked()]
            ]
        else:
            wanted = self._priority_tickers()[: stream_priority_count(self.cfg)]
        wanted = filter_tradeable_tickers(self.cfg, wanted)

        if not watch_all and not ai_fast_execution(self.cfg):
            focus = self._focused_ticker()
            for t in list(self._target_monitors.keys()):
                if t != focus:
                    self._stop_target_stream(t)
            if focus:
                self._ensure_target_stream(focus, mode="tick", quiet=quiet)
            return

        for t in list(self._target_monitors.keys()):
            if t not in wanted:
                self._stop_target_stream(t)

        held = set(self._held_tickers())
        modes = assign_stream_modes(
            wanted, self.cfg, held=held, tick_denied=self._tick_limit_denied,
        )
        n_tick = n_rt = n_skip = 0
        for ticker, mode in modes.items():
            if mode == "skip":
                n_skip += 1
                if ticker in self._target_monitors:
                    self._stop_target_stream(ticker)
                continue
            self._ensure_target_stream(ticker, mode=mode, quiet=quiet)
            if mode == "tick":
                n_tick += 1
            else:
                n_rt += 1

        if wanted:
            tickers = ",".join(wanted[:8]) + ("…" if len(wanted) > 8 else "")
            body = (
                f"{n_tick} tick + {n_rt} 5s-bars"
                + (f" ({n_skip} deferred)" if n_skip else "")
                + f" [{tickers}]"
            )
            if body != getattr(self, "_last_stream_log_body", ""):
                self._last_stream_log_body = body
                prefix = "📡 Streams:" if quiet else "  📡 PRIORITY STREAMS:"
                log.info(f"{prefix} {body}")
                try:
                    from core.sniper_execution import sniper_tick_streams_enabled
                    if sniper_tick_streams_enabled(self.cfg):
                        tick_names = [t for t, m in modes.items() if m == "tick"]
                        if tick_names:
                            log.info(f"  🎯 Sniper tick sensors: {', '.join(tick_names)}")
                except Exception:
                    pass

    def _ensure_target_stream(self, ticker: str, mode: str = "realtime", quiet: bool = False):
        """Start or switch stream mode for one locked ticker."""
        current = self._stream_modes.get(ticker)
        if ticker in self._target_monitors and current == mode:
            return
        if ticker in self._target_monitors and current != mode:
            self._stop_target_stream(ticker)
        if ticker not in self._target_monitors:
            self._start_target_stream(ticker, quiet=quiet, stream_mode=mode)

    def _start_target_stream(
        self, ticker: str, quiet: bool = False, stream_mode: str = "tick",
    ):
        """Start live stream for a locked target."""
        blocked, reason = is_market_data_blocked(self.cfg, ticker)
        if blocked:
            log.debug(f"  ⏭ stream skip {ticker}: {reason[:80]}")
            return
        if ticker in self._target_monitors:
            return
        if stream_mode == "tick":
            if ticker.upper() in self._tick_limit_denied:
                stream_mode = "realtime"
            elif self._active_tick_stream_count() >= tick_stream_count(self.cfg):
                stream_mode = "realtime"
        try:
            cfg = BotConfig(TICKER=ticker)
            dm = DataManager(self.conn, cfg)
            cached = self._scan_data_cache.get(ticker)
            n_cached = len(cached) if cached is not None else 0
            if cached is not None and n_cached > 0:
                dm.seed_buffer_from_dataframe(cached, n_bars=60)
            dm.start_tick_stream(realtime_only=(stream_mode == "realtime"), quiet=quiet)
            if tick_spike_monitor_enabled(self.cfg):
                sym = ticker
                dm.on_tick(lambda px, ts, t=sym: self._on_locked_stream_tick(t, px, ts))
            self._target_monitors[ticker] = dm
            self._stream_modes[ticker] = stream_mode
            self._target_last_bar_count[ticker] = n_cached
            kind = "5s" if stream_mode == "realtime" else "tick"
            warm = "warming" if n_cached < self._min_bars_for(ticker) else f"{n_cached} bars"
            msg = f"  📡 LIVE STREAM {kind} {ticker} ({warm})"
            (log.debug if quiet else log.info)(msg)
        except Exception as exc:
            record_fetch_failure(self.cfg, ticker, exc, bar_size=f"stream:{stream_mode}")
            log.warning(f"  Stream start failed for {ticker}: {exc}")

    def _log_flat_heartbeat(self):
        """One-line alive pulse while flat — confirms watch loop without stream spam."""
        if self._in_any_position() or not self._locked_targets:
            return
        now = time.time()
        pulse_sec = float(getattr(self.cfg, "FLAT_PULSE_SEC", 15.0))
        if now - self._last_flat_pulse < pulse_sec:
            return
        self._last_flat_pulse = now
        focus = self._focused_ticker() or "?"
        locked = ",".join(t.ticker for t in self._locked_targets[: self._max_locked()])
        n_streams = len(self._target_monitors)
        nxt = self._next_best_pick.ticker if self._next_best_pick else "-"
        priority = self._priority_tickers()
        bars_ready = sum(
            1 for t in priority
            if t in self._scan_data_cache
            and len(self._scan_data_cache[t]) >= self._min_bars_for(t)
        )
        priced = sum(1 for t in priority if self._stream_has_price(t))
        if priced > 0:
            self.conn._10197_reclaim_attempts = 0
            self.conn._10197_storm_until = 0.0
        warm_note = ""
        if bars_ready < len(priority) and priced > 0:
            warm_note = f" | bars {bars_ready}/{len(priority)} warming from live streams"
        quality = ""
        if capital_discipline_enabled(self.cfg):
            quality = " | full AI — no entry caps"
        log.info(
            f"👁 WATCHING: {n_streams} streams | priced {priced}/{len(priority)} | "
            f"priority=[{','.join(priority[:10]) or focus}] | pool=[{locked}] | "
            f"next_best={nxt}{warm_note}{quality}"
        )

    def _detect_tick_volume_burst(self, dm: DataManager, df: pd.DataFrame) -> Tuple[bool, float]:
        """Detect volume burst from live tick prints or 5s bar accumulation."""
        ticks = list(getattr(dm, "_tick_buffer", []))
        if len(ticks) >= 5:
            recent_vol = sum(int(t.get("size", 0)) for t in ticks[-100:])
            avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 20 else 1.0
            if avg_vol > 0:
                ratio = recent_vol / avg_vol
                return ratio >= self.cfg.VOLUME_SPIKE_MIN_RATIO, ratio
        fast = dm.get_fast_bar_dataframe(n=12)
        if fast is not None and len(fast) >= 3:
            recent_vol = float(fast["volume"].tail(3).sum())
            avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 20 else 1.0
            if avg_vol > 0:
                ratio = recent_vol / avg_vol
                return ratio >= self.cfg.VOLUME_SPIKE_MIN_RATIO, ratio
        return False, 1.0
    
    def _stop_all_target_streams(self) -> None:
        """Stop every live tick/bar stream (used on shutdown and IB session reclaim)."""
        for ticker in list(self._target_monitors.keys()):
            self._stop_target_stream(ticker)

    def _on_ib_connectivity(self, event: str) -> None:
        """IB 1100/1102 — pause/resume bar warm while socket is down."""
        if event == "connectivity_lost":
            self._ib_connectivity_paused = True
        elif event == "data_ok":
            self._ib_connectivity_paused = False

    def _resubscribe_all_streams(self, force: bool = False) -> None:
        """Re-request live streams after IB reconnect, 1101, or 10197 reclaim."""
        if not self._locked_targets:
            return
        try:
            from core.market_data_learning import (
                clear_competing_session_blocks,
                clear_reconnect_transient_blocks,
            )
            cleared = clear_competing_session_blocks() + clear_reconnect_transient_blocks()
            if cleared:
                log.info(f"  🔓 MD blocks cleared before re-subscribe ({cleared} ticker(s))")
        except Exception:
            pass
        if force:
            for ticker in list(self._target_monitors.keys()):
                self._stop_target_stream(ticker)
        self._queue_locked_stream_repairs()
        self._ensure_locked_streams(quiet=False)
        n = len(self._target_monitors)
        log.info(f"  📡 Re-subscribed {n} live stream(s) after IB reconnect")

    def _on_ib_session_reclaim(self) -> None:
        """Cancel streams before IB disconnect/reconnect so zombie MD slots are released."""
        n = len(self._target_monitors)
        if n:
            log.info(f"IB session reclaim: stopping {n} live stream(s)")
        self._stop_all_target_streams()
        self._queue_locked_stream_repairs()

    def _stop_target_stream(self, ticker: str):
        """Stop live tick stream for a target."""
        dm = self._target_monitors.pop(ticker, None)
        self._stream_modes.pop(ticker, None)
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
        df = dm.get_live_decision_bars(min_bars=6)
        if df is None:
            df = dm.get_bar_dataframe(min_bars=10)
        if df is None or len(df) < 6:
            return None
        last_count = self._target_last_bar_count.get(ticker, 0)
        if len(df) <= last_count:
            return None  # No new bars
        new_bars = df.iloc[last_count:]
        self._target_last_bar_count[ticker] = len(df)
        return new_bars
    
    def _attempt_scan_bootstrap_entry(self):
        """Enter on scanner-confirmed momentum right after lock (don't wait for a new tick spike)."""
        if not self._locked_targets:
            return
        if self._open_position_count() >= self._max_concurrent():
            return
        pick = self._locked_targets[0]
        if pick.ticker in self._held_tickers():
            return
        min_lock = effective_min_lock_score(self.cfg)
        if pick.rank_score < min_lock:
            return
        min_bars = self._min_bars_for(pick.ticker)
        df, live_px, _, forecast = self._resolve_live_bars(pick.ticker, min_bars=min_bars)
        if df is None or len(df) < min_bars:
            return
        is_spike, spike_ratio = self._detect_volume_spike(df)
        vol_ratio = float(df["volume"].tail(3).mean()) / (float(df["volume"].tail(20).mean()) + 1e-9)
        if not is_spike and vol_ratio >= 1.15:
            is_spike, spike_ratio = True, vol_ratio
        is_spike, spike_ratio = apply_micro_spike_boost(
            is_spike, spike_ratio, forecast,
            cfg=self.cfg, scan_score=float(pick.rank_score),
        )
        if not is_spike:
            return
        self.top_pick = pick
        log.info(
            f"📊 SCAN MOMENTUM: {pick.ticker} score={pick.rank_score:.0f} vol={spike_ratio:.1f}x "
            f"micro={forecast.get('spike_likelihood', 0):.0%} pred→${(forecast.get('pred_1bar') or live_px):.2f}"
        )
        self._attempt_entry()

    def _refresh_locked_bars(self, quiet: bool = False):
        """Refresh 1min bars for priority targets so volume/uptrend checks stay current."""
        if ai_fast_execution(self.cfg):
            targets = [
                t for t in self._locked_targets
                if t.ticker.upper() in self._priority_ticker_set()
            ]
        else:
            targets = self._locked_targets
        for target in targets:
            ticker = target.ticker
            blocked, _ = is_market_data_blocked(self.cfg, ticker)
            if blocked:
                continue
            need = self._min_bars_for(ticker)
            if getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
                if self._bars_from_stream(ticker, need) is not None:
                    continue
            if ticker in self._target_monitors and bool(
                getattr(self.cfg, "MD_SOFT_FAIL_HMDS", True),
            ):
                continue
            if skip_historical_prefetch(self.cfg) and self._stream_has_price(ticker):
                continue
            try:
                from core.rth_session import historical_prefetch_allowed
                if not historical_prefetch_allowed(self.cfg):
                    continue
            except Exception:
                continue
            cfg_ticker = self.cfg.TICKER
            try:
                self.cfg.TICKER = ticker
                dm = DataManager(self.conn, self.cfg)
                fresh = dm.fetch_historical(
                    duration="1800 S", bar_size="1 min", use_rth=False, quiet=quiet,
                )
                min_bars = self._min_bars_for(ticker)
                if fresh is not None and len(fresh) >= min_bars:
                    self._store_scan_cache(ticker, fresh)
            except Exception as exc:
                record_fetch_failure(self.cfg, ticker, exc, bar_size="1 min")
            finally:
                self.cfg.TICKER = cfg_ticker

    def _silent_background_watch(self):
        """Rank other locked targets for next entry — no log noise while holding."""
        if not self._in_any_position() or len(self._locked_targets) < 2:
            return
        holding = self._held_tickers()
        best: Optional[ScanResult] = None
        best_opp = 0.0
        cfg_ticker = self.cfg.TICKER
        try:
            for target in self._locked_targets:
                if target.ticker in holding:
                    continue
                ticker = target.ticker
                blocked, _ = is_market_data_blocked(self.cfg, ticker)
                if blocked:
                    continue
                need = self._min_bars_for(ticker)
                if getattr(self.cfg, "SCALPER_LIVE_BARS_FIRST", True):
                    fresh = self._bars_from_stream(ticker, need)
                    if fresh is not None and len(fresh) >= need:
                        pass
                    elif skip_historical_prefetch(self.cfg):
                        continue
                    else:
                        fresh = None
                else:
                    fresh = None
                try:
                    if fresh is None and not skip_historical_prefetch(self.cfg):
                        self.cfg.TICKER = ticker
                        dm = DataManager(self.conn, self.cfg)
                        fresh = dm.fetch_historical(
                            duration="1800 S", bar_size="1 min", use_rth=False, quiet=True,
                        )
                    if fresh is None or len(fresh) < need:
                        continue
                    self._store_scan_cache(ticker, fresh)
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
        """Council prefetch — off in nanny mode to preserve RPM for live entries."""
        from core.council_nanny import prefetch_enabled
        if not prefetch_enabled(self.cfg):
            return
        if not self.ai_commander or not getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True):
            return
        targets = self._locked_targets or []
        top_n = effective_prefetch_top_n(self.cfg)
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
                    df=df,
                )
            except Exception as exc:
                log.debug(f"Prefetch {ticker}: {exc}")

    def _fast_monitor_locked(self, scout_only: bool = False):
        """
        Scan all locked tickers for spikes.
        scout_only=True: track next_best while holding a position (no new entry).
        """
        if not self._locked_targets:
            return
        if self._open_position_count() >= self._max_concurrent() and not scout_only:
            return
        now = time.time()
        refresh_sec = float(getattr(self.cfg, "LOCK_BAR_REFRESH_SEC", 180.0))
        if now - getattr(self, '_last_bar_refresh', 0) > refresh_sec:
            self._last_bar_refresh = now
            self._refresh_locked_bars(quiet=True)

        # Keep all priority streams alive — simultaneous monitor (no single-ticker rotation)
        if ai_fast_execution(self.cfg) or getattr(self.cfg, "WATCH_ALL_LOCKED_STREAMS", True):
            self._ensure_locked_streams(quiet=True)
        elif self.top_pick:
            self._ensure_locked_streams(quiet=True)

        best_spike: Optional[Tuple[ScanResult, float, float, pd.DataFrame]] = None
        spike_candidates: List[Tuple[float, ScanResult, float, float, pd.DataFrame]] = []
        best_priority = 0.0

        holding = self._held_tickers()
        priority_names = self._priority_ticker_set()
        scan_targets = self._locked_targets[: self._max_locked()]

        for target in scan_targets:
            ticker = target.ticker
            if ticker in self._entry_poll_states or ticker in holding:
                continue
            min_bars = self._min_bars_for(ticker)
            df, live_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=min_bars)
            min_ok = min_bars
            if dm and live_px > 0 and bool(getattr(self.cfg, "MD_SOFT_FAIL_HMDS", True)):
                min_ok = max(3, min_bars // 2)
            if df is None or len(df) < min_ok:
                if dm and ticker.upper() in priority_names:
                    burst, burst_ratio = self._detect_tick_volume_burst(dm, df if df is not None else pd.DataFrame())
                    if burst:
                        if live_px <= 0:
                            live_px = float(dm.get_latest_price() or 0)
                        if live_px > 0:
                            priority = float(target.rank_score) * float(burst_ratio) * 1.5
                            work_df = df.tail(60).copy() if df is not None and len(df) else pd.DataFrame()
                            spike_candidates.append((priority, target, live_px, burst_ratio, work_df))
                continue

            if live_px <= 0:
                live_px = float(df["close"].iloc[-1])

            work_df = df.tail(60).copy()

            if forecast.get("dir", 0) < 0 and not forecast.get("breakout"):
                continue

            spike_fast_ok = should_spike_fast_entry(
                self.cfg, 1.0, float(target.rank_score),
            )
            uptrend_ok = _only_uptrend(work_df, live_px, min_bars=min_bars)
            if not uptrend_ok and not (
                ai_fast_execution(self.cfg)
                and ticker.upper() in priority_names
                and spike_fast_ok
            ):
                if forecast.get("spike_likelihood", 0) < 0.5:
                    continue

            is_spike, spike_ratio = self._detect_volume_spike(work_df, min_period=min(20, max(6, min_bars)))
            min_spike = float(getattr(self.cfg, "LOCKED_SPIKE_MIN_RATIO", 1.15))
            if not is_spike and spike_ratio >= min_spike:
                is_spike, spike_ratio = True, spike_ratio

            is_spike, spike_ratio = apply_micro_spike_boost(
                is_spike, spike_ratio, forecast,
                cfg=self.cfg, scan_score=float(target.rank_score), live_px=float(live_px),
            )

            if dm and ticker.upper() in priority_names:
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

            self._lock_spike_touch_at[ticker] = now
            boost = 1.0 + float(forecast.get("spike_likelihood", 0)) * float(
                getattr(self.cfg, "MICRO_SPIKE_BOOST", 0.35)
            )
            priority = float(target.rank_score) * float(spike_ratio) * boost
            spike_candidates.append((priority, target, live_px, spike_ratio, work_df))
            if priority > best_priority:
                best_priority = priority
                best_spike = (target, live_px, spike_ratio, work_df)

        if best_spike is None and not spike_candidates:
            return

        if scout_only:
            if best_spike:
                target, live_px, spike_ratio, work_df = best_spike
                self._next_best_pick = target
                self._next_best_score = best_priority
                if int(time.time()) % 30 == 0:
                    log.debug(
                        f"  👀 Scout while holding: next={target.ticker} "
                        f"vol={spike_ratio:.1f}x score={target.rank_score:.0f}"
                    )
            return

        spike_candidates.sort(key=lambda x: x[0], reverse=True)
        max_attempts = max_spike_attempts_per_cycle(self.cfg)
        attempted = 0

        for priority, target, live_px, spike_ratio, work_df in spike_candidates[:max_attempts]:
            ticker = target.ticker
            if time.time() < self._spike_attempt_until.get(ticker, 0):
                continue
            if time.time() < self._spike_skip_until.get(ticker, 0):
                continue
            if time.time() < self._entry_cooldown_until.get(ticker, 0):
                continue
            if self.risk.is_halted():
                return
            if self._open_position_count() >= self._max_concurrent():
                return
            if self._pending_entry_ticker and time.time() < self._pending_entry_until:
                if self._pending_entry_ticker == ticker:
                    continue

            self._store_scan_cache(ticker, work_df)
            self.top_pick = target
            self._last_entry_attempt_at = time.time()
            self._spike_attempt_until[ticker] = time.time() + spike_entry_cooldown_sec(self.cfg)
            fc = self._last_micro_forecast.get(ticker, {})
            q_prob = fc.get("profit_probability", "")
            q_setup = fc.get("setup_type", "")
            q_extra = ""
            if q_prob != "":
                q_extra = f" | profit_prob={float(q_prob):.0%} setup={q_setup}"
            log.info(
                f"⚡ SPIKE: {ticker} @ ${live_px:.2f} | vol={spike_ratio:.1f}x | "
                f"score={target.rank_score:.0f} | micro={fc.get('spike_likelihood', 0):.0%} "
                f"pred→${(fc.get('pred_1bar') or live_px):.2f}{q_extra} | attempting entry..."
            )
            from core.entry_quality import (
                assess_entry_quality, quality_blocks_entry, regime_blocks_entry, mtf_blocks_entry,
            )
            quality = assess_entry_quality(
                self.cfg, fc,
                spike_ratio=spike_ratio,
                scan_score=float(target.rank_score),
                live_px=live_px,
            )
            fc.update(quality)
            self._last_micro_forecast[ticker] = fc
            if not quality.get("enter_ok", True):
                log.info(
                    f"  📊 QUALITY advisory {ticker}: {quality.get('reason', '')[:100]}"
                )
            spike_regime = "unknown"
            fast_df, _, _, _ = self._resolve_live_bars(ticker, min_bars=10)
            if fast_df is not None and len(fast_df) >= 10:
                try:
                    rr = self.regime_detector.classify(fast_df)
                    if rr is not None:
                        raw = getattr(rr, "regime", "unknown")
                        spike_regime = getattr(raw, "value", str(raw))
                except Exception:
                    pass
            df_5m = df_15m = None
            from core.entry_quality import mtf_fetch_skipped
            if not mtf_fetch_skipped(
                self.cfg,
                scan_score=float(target.rank_score),
                spike_ratio=float(spike_ratio),
            ):
                df_5m, df_15m = self._resolve_mtf_bars(
                    ticker, float(target.rank_score), float(spike_ratio),
                )
            try:
                from core.smart_stack import (
                    collect_spike_gate_advisories,
                    mechanical_gates_advisory_only,
                )
                gate_adv = collect_spike_gate_advisories(
                    self.cfg,
                    ticker=ticker,
                    quality=quality,
                    spike_regime=spike_regime,
                    df_5m=df_5m,
                    df_15m=df_15m,
                    scan_score=float(target.rank_score),
                    spike_ratio=float(spike_ratio),
                )
                self._smart_gate_context[ticker.upper()] = gate_adv
                if mechanical_gates_advisory_only(self.cfg):
                    for gkey, gval in gate_adv.items():
                        if gkey == "ticker" or not isinstance(gval, dict):
                            continue
                        if not gval.get("ok", True):
                            log.info(
                                f"  📊 GATE advisory {ticker}: {gkey} — "
                                f"{gval.get('reason', '')[:80]}"
                            )
                else:
                    if quality_blocks_entry(self.cfg, quality):
                        log.info(
                            f"  ⏭ QUALITY veto {ticker}: {quality.get('reason', '')[:100]}"
                        )
                        self._spike_skip_until[ticker] = time.time() + float(
                            getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                        )
                        continue
                    if regime_blocks_entry(self.cfg, spike_regime):
                        log.info(
                            f"  ⏭ REGIME block {ticker}: {spike_regime} — skip new entry"
                        )
                        self._spike_skip_until[ticker] = time.time() + float(
                            getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                        )
                        continue
                    if mtf_blocks_entry(
                        self.cfg, df_5m, df_15m,
                        scan_score=float(target.rank_score),
                        spike_ratio=float(spike_ratio),
                    ):
                        log.info(
                            f"  ⏭ MTF block {ticker}: 5m/15m not aligned — skip entry"
                        )
                        self._spike_skip_until[ticker] = time.time() + float(
                            getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                        )
                        continue
            except Exception:
                if quality_blocks_entry(self.cfg, quality):
                    log.info(
                        f"  ⏭ QUALITY veto {ticker}: {quality.get('reason', '')[:100]}"
                    )
                    self._spike_skip_until[ticker] = time.time() + float(
                        getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                    )
                    continue
                if regime_blocks_entry(self.cfg, spike_regime):
                    log.info(
                        f"  ⏭ REGIME block {ticker}: {spike_regime} — skip new entry"
                    )
                    self._spike_skip_until[ticker] = time.time() + float(
                        getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                    )
                    continue
                if mtf_blocks_entry(
                    self.cfg, df_5m, df_15m,
                    scan_score=float(target.rank_score),
                    spike_ratio=float(spike_ratio),
                ):
                    log.info(f"  ⏭ MTF block {ticker}: 5m/15m not aligned — skip entry")
                    self._spike_skip_until[ticker] = time.time() + float(
                        getattr(self.cfg, "SPIKE_SKIP_SEC", 12.0)
                    )
                    continue
            result = self._attempt_entry()
            attempted += 1
            if ticker in self._held_tickers():
                self._active_stream_ticker = ticker
                self._ensure_position_stream(ticker)
                return
            if result == "entered":
                return
            if result in ("permanent_skip", "learn_skip"):
                self._locked_targets = [t for t in self._locked_targets if t.ticker != ticker]
                self._stop_target_stream(ticker)
                if not self._locked_targets:
                    self._last_scan_time = 0
                    log.info("🔓 All locked targets cleared — will rescan universe")
                elif self.top_pick and self.top_pick.ticker == ticker:
                    self.top_pick = self._locked_targets[0]
                    self._ensure_focus_stream(quiet=True)
            if result == "waiting" and attempted >= max_attempts:
                break
    
    def _detect_volume_spike(self, df: pd.DataFrame, min_period: int = 20) -> Tuple[bool, float]:
        """
        Detect volume spike: current volume vs recent average.
        Uses shorter window when fewer bars available (fast execution).
        """
        n = len(df)
        if n < 6:
            return False, 1.0
        period = min(min_period, n - 1)
        volumes = df["volume"].values[-period:]
        avg_vol = np.mean(volumes[:-1]) if len(volumes) > 1 else float(volumes[0])
        current_vol = volumes[-1]
        if avg_vol <= 0:
            return False, 1.0
        spike_ratio = current_vol / avg_vol
        threshold = getattr(self.cfg, "VOLUME_SPIKE_MIN_RATIO", 1.25)
        if ai_fast_execution(self.cfg):
            threshold = min(threshold, float(getattr(self.cfg, "AI_SPIKE_FAST_MIN_RATIO", 1.15)))
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
    
    def _evaluate_profit_hunt_exit(self, current_px: float) -> Tuple[bool, str]:
        """Spike-top + spike-fade opportunistic exits while in profit."""
        if self.shares <= 0 or self._entry_price <= 0:
            return False, ""

        ticker = self.current_ticker or ""
        entry_px = self._entry_price
        pnl_pct = (current_px / entry_px) - 1

        min_hold = effective_min_hold_for_exit(self.cfg, pnl_pct)
        opened = getattr(self, "_position_opened_at", 0.0)
        if min_hold > 0 and opened and (time.time() - opened) < min_hold:
            return False, ""

        extended = is_extended_session(get_market_state(self.cfg))

        fast_df, live_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=6)
        if fast_df is None:
            dm = dm or self._dm_for_ticker(ticker)
            fast_df = coalesce_bars(
                dm.get_live_decision_bars(min_bars=6) if dm else None,
                dm.get_bar_dataframe(min_bars=10) if dm else None,
                self._scan_data_cache.get(ticker),
                min_len=3,
            )
        if live_px <= 0:
            live_px = current_px

        fade_thr = float(getattr(self.cfg, "MICRO_FADE_EXIT", 0.55))
        if (
            getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True)
            and pnl_pct > 0.002
            and forecast.get("fade_risk", 0) >= fade_thr
            and forecast.get("dir", 0) <= 0
        ):
            return True, (
                f"micro_fade: risk={forecast['fade_risk']:.2f} "
                f"pred↓${(forecast.get('pred_1bar') or live_px):.2f}"
            )

        should_exit, reason, ctx = evaluate_spike_top_exit(
            self.cfg, fast_df, dm, current_px, entry_px,
            pnl_pct, self._position_peak, extended=extended,
        )
        if ctx.get("spike_detected"):
            self._profit_hunt_spike_ctx = ctx
            self._profit_hunt_spike_peak = max(self._profit_hunt_spike_peak, current_px)
            self._profit_hunt_spike_at = time.time()
            track_profit_hunt_event(
                self.cfg, "spike_detected", ticker,
                {**ctx, "price": current_px, "pnl_pct": round(pnl_pct * 100, 3)},
                pnl_usd=(current_px - entry_px) * self.shares,
                pnl_pct=pnl_pct,
                record_buffer=True,
                push_git=False,
            )

        if should_exit:
            track_profit_hunt_event(
                self.cfg, "hunt_signal", ticker,
                {**ctx, "reason": reason, "price": current_px},
                pnl_usd=(current_px - entry_px) * self.shares,
                pnl_pct=pnl_pct,
                record_buffer=True,
            )
            return True, reason

        fade_exit, fade_reason = evaluate_wave_end_on_spike_fade(
            self.cfg, fast_df, current_px, entry_px, self._position_peak, pnl_pct,
        )
        if fade_exit:
            return True, fade_reason

        missed = check_missed_profit_hunt(
            self.cfg,
            {
                "spike_peak": self._profit_hunt_spike_peak,
                "spike_seen_at": self._profit_hunt_spike_at,
                "spike_ctx": self._profit_hunt_spike_ctx,
                "shares": self.shares,
            },
            current_px,
            entry_px,
            ticker,
        )
        if missed and not self._profit_hunt_missed_logged:
            self._profit_hunt_missed_logged = True
            missed["reason"] = (
                f"Missed spike-top exit on {ticker}: peak ${missed['spike_peak']:.2f} "
                f"left ~${missed['left_on_table_usd']:.0f} on table"
            )
            track_profit_hunt_event(
                self.cfg, "missed_profit_hunt", ticker, missed,
                pnl_usd=-float(missed.get("left_on_table_usd", 0)),
                pnl_pct=pnl_pct,
                record_buffer=True,
                push_git=True,
            )
            teach_profit_hunt_lesson(
                self.autopilot, self.consciousness,
                missed["reason"],
            )
            self._observe_runtime(
                "missed_profit_hunt",
                ticker=ticker,
                reason=missed["reason"],
                pnl_usd=-float(missed.get("left_on_table_usd", 0)),
                market_state=get_market_state(self.cfg),
                **{k: v for k, v in missed.items() if k != "reason"},
            )
            log.warning(f"  📚 {missed['reason']}")

        return False, ""

    def _ai_profit_decision_stalled(self, pnl_pct: float = 0.0) -> bool:
        """True when AI/council has not acted on a green position within the wait window."""
        if pnl_pct <= 0:
            return False
        from core.green_profit_lock import ai_wait_sec

        wait = ai_wait_sec(self.cfg)
        now = time.time()
        ticker = self.current_ticker or ""

        for task in ("exit_decision", "position_manage", "stagnation_check", "risk_exit"):
            if self._has_ai_council(ticker, task):
                st = self._ai_councils.get(self._council_key(ticker, task), {})
                if now - float(st.get("started_at", now)) >= wait:
                    return True

        if self.ai_commander:
            for task in ("exit_decision", "position_manage", "risk_exit"):
                try:
                    st = self.ai_commander._live_line.status(ticker, task)
                    if st.get("in_flight") and float(st.get("age_sec", 0) or 0) >= wait:
                        return True
                except Exception:
                    pass

        if getattr(self.cfg, "AI_FULL_CONTROL", True) and not self.ai_commander:
            return True

        ride_at = getattr(self, "_profit_ride_started_at", 0.0)
        if ride_at and now - ride_at >= wait:
            return True

        return False

    def _enforce_green_profit_lock(self, current_px: float) -> bool:
        """Mechanical quick green scalp when AI stalls — never let profit bleed to red."""
        from core.green_profit_lock import (
            evaluate_green_lock,
            green_profit_lock_enabled,
            min_green_pnl_pct,
            is_green_lock_reason,
        )

        if not green_profit_lock_enabled(self.cfg):
            return False
        if self.shares <= 0 or self._entry_price <= 0:
            return False

        entry_px = self._entry_price
        pnl_pct = ((current_px / entry_px) - 1) if entry_px else 0.0
        if pnl_pct <= 0:
            return False

        peak_pct = ((self._position_peak / entry_px) - 1) if entry_px else 0.0
        giveback = max(0.0, peak_pct - pnl_pct)
        if pnl_pct >= min_green_pnl_pct(self.cfg):
            self._was_in_profit = True

        stalled = self._ai_profit_decision_stalled(pnl_pct)
        should_lock, reason = evaluate_green_lock(
            self.cfg,
            pnl_pct=pnl_pct,
            peak_pct=peak_pct,
            ai_stalled=stalled,
            giveback_from_peak=giveback,
            was_green=self._was_in_profit,
        )
        if not should_lock:
            return False

        log.info(f"  🔒 GREEN LOCK: {reason}")
        if is_green_lock_reason(reason):
            ticker = self.current_ticker or ""
            pnl = pnl_pct * self.shares * entry_px
            track_profit_hunt_event(
                self.cfg, "green_profit_lock", ticker,
                {"reason": reason, "price": current_px, "ai_stalled": stalled},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, reason)
            return True
        return self._execute_mechanical_profit_exit(current_px, reason)

    def _execute_mechanical_profit_exit(
        self, current_px: float, reason: str, *, defer: bool = False,
    ) -> bool:
        """Profit hunt signal — AI council decides exit vs ride for higher profit."""
        if not reason:
            return False
        from core.green_profit_lock import is_green_lock_reason

        ticker = self.current_ticker or ""
        entry_px = self._entry_price
        pnl_pct = ((current_px / entry_px) - 1) if entry_px else 0.0
        pnl = pnl_pct * self.shares * entry_px if entry_px else 0.0

        if is_green_lock_reason(reason):
            log.info(f"  🔒 GREEN LOCK: {reason[:100]}")
            track_profit_hunt_event(
                self.cfg, "green_profit_lock", ticker,
                {**self._profit_hunt_spike_ctx, "reason": reason, "price": current_px},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, reason, ticker=ticker, defer=defer)
            return True

        from core.profit_hunting import ai_profit_full_power

        stalled = self._ai_profit_decision_stalled(pnl_pct)

        if (
            ai_profit_full_power(self.cfg)
            and pnl_pct > 0
            and self.ai_commander
            and not stalled
        ):
            log.info(f"  🧠 AI PROFIT SIGNAL: {reason[:80]} — council decides exit vs ride")
            self._last_ai_position_manage = 0.0
            self._ai_manage_position(current_px)
            if self._deliberate_exit_council(
                ticker, current_px, True, 0.65, reason,
                {"signal": "profit_hunt", "mechanical": True, "ride_ok": True},
            ):
                track_profit_hunt_event(
                    self.cfg, reason.split(":")[0].strip(), ticker,
                    {**self._profit_hunt_spike_ctx, "reason": reason, "price": current_px},
                    pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
                )
                return True
            track_profit_hunt_event(
                self.cfg, "ai_ride", ticker,
                {"reason": reason, "price": current_px, "pnl_pct": pnl_pct},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=False,
            )
            log.info(f"  🧠 AI RIDING {ticker}: holding for higher profit — {reason[:60]}")
            self._profit_ride_started_at = time.time()
            return False

        if profit_exit_bypasses_council(
            self.cfg, reason, pnl_pct, ai_stalled=stalled,
        ):
            log.info(f"  🎯 PROFIT HUNT: {reason}")
            if self.ai_commander:
                ppo_exit, ppo_conf, ppo_reason = (True, 0.65, reason)
                obs = self._build_ppo_obs(current_px)
                if obs is not None and self.ai_commander.model is not None:
                    action, conf, ppo_reason = self.ai_commander.ppo_action(obs)
                    ppo_exit = action == 2
                    ppo_conf = conf
                self.ai_commander.ring_exit_for_deferred_learning(
                    {
                        "ticker": ticker, "price": current_px,
                        "pnl_pct": round(pnl_pct * 100, 2),
                        "entry": entry_px, "reason": reason,
                    },
                    ppo_exit=ppo_exit, ppo_conf=ppo_conf, ppo_reason=ppo_reason,
                    executed_exit=True, pipeline="ppo:profit_lock",
                )
            track_profit_hunt_event(
                self.cfg, reason.split(":")[0].strip(), ticker,
                {**self._profit_hunt_spike_ctx, "reason": reason, "price": current_px},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=True,
            )
            teach_profit_hunt_lesson(
                self.autopilot, self.consciousness,
                f"Spike-top hunt on {ticker}: {reason}",
            )
            self._exit_position(current_px, reason, ticker=ticker, defer=defer)
            return True
        if is_ai_council_mode(self.cfg) and self.ai_commander:
            if self._deliberate_exit_council(
                ticker, current_px, True, 0.65, reason,
                {"signal": "profit_hunt", "mechanical": True},
            ):
                return True
            track_profit_hunt_event(
                self.cfg, "council_hold", ticker,
                {"reason": reason, "price": current_px},
                pnl_usd=pnl, pnl_pct=pnl_pct, record_buffer=True, push_git=False,
            )
            return False
        self._exit_position(current_px, reason, ticker=ticker, defer=defer)
        return True

    def _reset_profit_hunt_state(self):
        self._profit_hunt_spike_peak = 0.0
        self._profit_hunt_spike_at = 0.0
        self._profit_hunt_spike_ctx = {}
        self._profit_hunt_missed_logged = False
        self._profit_ride_started_at = 0.0
        self._was_in_profit = False

    def _live_position_monitor(self, current_px: float):
        """Continuous post-entry tracking: pulse log, AI manage, trail, exit."""
        if self.shares <= 0 or self._entry_price <= 0:
            return

        ticker = self.current_ticker or getattr(self.cfg, "TICKER", "")
        price_eps = max(self._entry_price * 0.0001, 0.0001)
        now = time.time()

        if self._last_pulse_price <= 0:
            self._last_pulse_price = current_px
            self._last_price_change_at = now

        if abs(current_px - self._last_pulse_price) > price_eps:
            self._last_pulse_price = current_px
            self._last_price_change_at = now
        else:
            frozen_for = now - self._last_price_change_at
            ai_snap = bool(self._last_stagnation_decision.get("force_snapshot"))
            stale_sec = float(getattr(self.cfg, "STALE_PRICE_REFRESH_SEC", 20.0))
            snap_gap = max(stale_sec, 5.0)
            if (
                ticker
                and (now - self._last_price_snapshot_at) >= snap_gap
                and (ai_snap or frozen_for >= stale_sec)
            ):
                snap_px = self._force_price_snapshot(ticker)
                self._last_price_snapshot_at = now
                if snap_px > 0 and abs(snap_px - current_px) > price_eps:
                    current_px = snap_px
                    self._last_pulse_price = current_px
                    self._last_price_change_at = now

        stagnant_sec = now - self._last_price_change_at
        frozen_sec = stagnant_sec

        if current_px > self._position_peak:
            self._position_peak = current_px
        if self.risk.plan:
            self.risk.plan.peak_price = max(self.risk.plan.peak_price, current_px)

        pnl_frac = ((current_px / self._entry_price) - 1) if self._entry_price else 0.0
        if (
            pnl_frac > 0
            and getattr(self.cfg, "DYNAMIC_TRAILING_ENABLED", False)
            and self.risk.plan
        ):
            try:
                _, ppo_conf, _ = self._ai_gate_exit(current_px)
                obs = self._build_ppo_obs(current_px)
                overrides = self.risk.update_ai_dynamic_trailing(
                    ai_confidence=float(ppo_conf),
                    regime_trend_strength=0.0,
                    regime_label="unknown",
                    observation=obs,
                )
                if overrides.get("early_loss_exit_threshold_pct") is not None:
                    self.risk._early_loss_threshold_pct = overrides[
                        "early_loss_exit_threshold_pct"
                    ]
            except Exception:
                pass

        pulse_ctx = {
            "ticker": ticker,
            "price": current_px,
            "pnl_usd": round((current_px - self._entry_price) * self.shares, 2),
            "pnl_pct": round(((current_px / self._entry_price) - 1) * 100, 2),
            "stop": self._position_stop,
            "target": self._position_target,
            "peak": self._position_peak,
            "stagnant_sec": round(stagnant_sec, 1),
            "price_frozen_sec": round(frozen_sec, 1),
        }
        ai_check_sec = float(getattr(self.cfg, "AI_STAGNATION_CHECK_SEC", 30.0))
        if (
            getattr(self.cfg, "AI_FULL_CONTROL", True)
            and self.ai_commander
            and stagnant_sec >= ai_check_sec
        ):
            try:
                self.ai_commander.prefetch_stagnation(pulse_ctx)
            except Exception:
                pass

        fingerprint = (
            f"{pulse_ctx['price']:.4f}|{pulse_ctx['pnl_usd']:.2f}|"
            f"{pulse_ctx['stop']:.4f}|{pulse_ctx['target']:.4f}"
        )
        unchanged = fingerprint == self._last_pulse_fingerprint
        pulse_verbose = bool(self._last_stagnation_decision.get("pulse_verbose"))
        if unchanged and not pulse_verbose:
            pulse_sec = float(getattr(self.cfg, "POSITION_PULSE_UNCHANGED_SEC", 30.0))
        else:
            pulse_sec = float(getattr(self.cfg, "POSITION_PULSE_SEC", 5.0))
        if now - self._last_position_pulse >= pulse_sec:
            self._last_position_pulse = now
            if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                self.ai_commander.ai_log("LIVE_PULSE", pulse_ctx)
            else:
                log.info(
                    f"📡 LIVE {ticker}: ${current_px:.4f} | "
                    f"P&L ${pulse_ctx['pnl_usd']:+.2f} ({pulse_ctx['pnl_pct']:+.2f}%) | "
                    f"Stop ${self._position_stop:.4f} | TP ${self._position_target:.4f} | "
                    f"Peak ${self._position_peak:.4f}"
                )
            self._last_pulse_fingerprint = fingerprint

        ai_sec = float(getattr(self.cfg, "AI_POSITION_MANAGE_SEC", 10.0))
        if pnl_frac > float(getattr(self.cfg, "IN_PROFIT_MANAGE_PNL_PCT", 0.003)):
            ai_sec = float(getattr(self.cfg, "AI_POSITION_MANAGE_IN_PROFIT_SEC", 1.0))
        min_hold = effective_min_position_hold_sec(self.cfg)
        opened = getattr(self, "_position_opened_at", 0.0)
        if now - self._last_ai_position_manage >= ai_sec:
            self._last_ai_position_manage = now
            if not opened or (now - opened) >= min_hold:
                self._ai_manage_position(current_px)

        # Opportunistic profit hunt — AI full power decides exit vs ride
        hunt_exit, hunt_reason = self._evaluate_profit_hunt_exit(current_px)
        if hunt_exit:
            if self._execute_mechanical_profit_exit(current_px, hunt_reason):
                self._active_stream_ticker = None
                return

        # Green profit lock — quick scalp if AI stalls while in profit
        if self._enforce_green_profit_lock(current_px):
            self._active_stream_ticker = None
            return

        self._update_trailing_stops(current_px)

        # Risk engine tick exits — AI council on profit; mechanical only on loss
        if self.risk.plan:
            prev_stop = self.risk.plan.current_stop_price
            should_risk_exit, risk_reason = self.risk.evaluate_tick(current_px)
            if self.risk.plan.current_stop_price != prev_stop:
                self._apply_stop_update(
                    self.risk.plan.current_stop_price,
                    f"risk trail ({risk_reason or 'ratchet'})",
                )
            if should_risk_exit and risk_reason:
                ticker = self.current_ticker or ""
                entry_px = self._entry_price
                pnl_pct = ((current_px / entry_px) - 1) if entry_px else 0.0
                stalled = self._ai_profit_decision_stalled(pnl_pct)
                if profit_exit_bypasses_council(
                    self.cfg, risk_reason, pnl_pct, ai_stalled=stalled,
                ):
                    log.info(f"  ⚡ MECHANICAL RISK EXIT: {risk_reason}")
                    track_profit_hunt_event(
                        self.cfg, risk_reason, ticker,
                        {"reason": risk_reason, "price": current_px},
                        pnl_usd=(current_px - entry_px) * self.shares if entry_px else 0,
                        pnl_pct=pnl_pct, record_buffer=True, push_git=True,
                    )
                    self._exit_position(current_px, risk_reason)
                    self._active_stream_ticker = None
                    return
                if is_ai_council_mode(self.cfg) and self.ai_commander:
                    if self._deliberate_risk_exit(ticker, current_px, risk_reason):
                        self._active_stream_ticker = None
                        return
                else:
                    log.info(f"  ⚡ RISK EXIT: {risk_reason}")
                    self._exit_position(current_px, risk_reason)
                    self._active_stream_ticker = None
                    return

        if self._enforce_green_profit_lock(current_px):
            self._active_stream_ticker = None
            return

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
            self._position_risk_budget(),
            stagnant_sec=now - self._last_price_change_at,
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
        ticker_dm = self._dm_for_ticker(self.current_ticker or "")
        fast_df, _, _, forecast = self._resolve_live_bars(self.current_ticker or "", min_bars=6)
        if fast_df is None and ticker_dm is not None:
            fast_df = ticker_dm.get_live_decision_bars(min_bars=6)
        if fast_df is not None and len(fast_df) >= 10:
            _, vol_ratio = self._detect_volume_spike(fast_df)
            try:
                rr = self.regime_detector.classify(fast_df)
                if rr is not None:
                    raw = getattr(rr, "regime", "unknown")
                    regime = getattr(raw, "value", str(raw))
            except Exception:
                pass

        if getattr(self.cfg, "DYNAMIC_TRAILING_ENABLED", False) and self.risk.plan:
            try:
                _, ppo_conf, _ = self._ai_gate_exit(current_px)
                obs = self._build_ppo_obs(current_px)
                overrides = self.risk.update_ai_dynamic_trailing(
                    ai_confidence=float(ppo_conf),
                    regime_trend_strength=0.0,
                    regime_label=str(regime),
                    observation=obs,
                )
                if overrides.get("early_loss_exit_threshold_pct") is not None:
                    self.risk._early_loss_threshold_pct = overrides["early_loss_exit_threshold_pct"]
            except Exception:
                pass

        pos_ctx = {
            "ticker": self.current_ticker,
            "entry": entry,
            "price": current_px,
            "peak": self._position_peak,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "peak_pct": round(
                ((self._position_peak / entry) - 1) * 100 if self._position_peak > entry else pnl_pct,
                2,
            ),
            "stop": self._position_stop,
            "target": self._position_target,
            "hard_floor": self._hard_stop_floor,
            "vol_ratio": round(vol_ratio, 2),
            "regime": str(regime),
            "stagnant_sec": round(max(0.0, time.time() - self._last_price_change_at), 1),
            "price_frozen_sec": round(max(0.0, time.time() - self._last_price_change_at), 1),
        }
        ppo_exit, ppo_conf, ppo_reason = False, 0.5, ""
        try:
            ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
        except Exception:
            pass
        mech_stop, mech_target = self._compute_mechanical_trail(current_px)
        ticker = self.current_ticker or ""
        if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
            if self._has_ai_council(ticker, "position_manage"):
                return
            if self._has_ai_council(ticker, "exit_decision"):
                return
            if ppo_exit:
                return
            decision = self.ai_commander.decide_position_manage(
                pos_ctx, ppo_exit, ppo_conf, ppo_reason, mech_stop, mech_target,
            )
            if decision.get("pending"):
                self._set_ai_council(ticker, "position_manage", {
                    "fingerprint": decision["fingerprint"],
                    "ppo_exit": ppo_exit,
                    "ppo_conf": ppo_conf,
                    "ppo_reason": ppo_reason,
                    "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                    "ctx": pos_ctx,
                    "mechanical_stop": mech_stop,
                    "mechanical_target": mech_target,
                    "current_px": current_px,
                })
                log.info(
                    f"  🧠 COUNCIL manage {ticker}: "
                    f"{(decision.get('reason') or 'deliberating')[:100]} | "
                    f"{decision.get('pipeline', '')}"
                )
                return
        else:
            decision = generative_position_decision(self.cfg, self.autopilot, pos_ctx)

        self._apply_position_manage_decision(decision, current_px)

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
                           unrealized_pnl: float, risk_usd: float,
                           stagnant_sec: float = 0.0) -> Tuple[bool, str]:
        """
        Exit when profit gives back from peak, AI says exit, slippage risk high,
        or position is stagnant (flat/losing with no price progress).
        """
        if self.shares <= 0 or entry_px <= 0:
            return False, "no position"

        pnl_pct = (current_px / entry_px) - 1
        min_hold = effective_min_hold_for_exit(self.cfg, pnl_pct)
        opened = getattr(self, "_position_opened_at", 0.0)
        if min_hold > 0 and opened and (time.time() - opened) < min_hold:
            return False, "hold (min hold)"

        peak_pct = (self._position_peak / entry_px) - 1 if self._position_peak > 0 else pnl_pct

        if getattr(self.cfg, "SCALPER_MICRO_PREDICT_ENABLED", True):
            _, _, _, forecast = self._resolve_live_bars(self.current_ticker or "", min_bars=6)
            loss_thr = float(getattr(self.cfg, "MICRO_LOSS_EXIT", 0.58))
            if pnl_pct < -0.002 and forecast.get("loss_pressure", 0) >= loss_thr and forecast.get("dir", 0) < 0:
                return True, (
                    f"micro_loss: pressure={forecast['loss_pressure']:.2f} "
                    f"pred↓${(forecast.get('pred_1bar') or current_px):.2f}"
                )
            fade_thr = float(getattr(self.cfg, "MICRO_FADE_EXIT", 0.55))
            if (
                pnl_pct > 0.004
                and peak_pct > 0.008
                and forecast.get("fade_risk", 0) >= fade_thr
                and forecast.get("profit_run", 1.0) < 0.35
            ):
                return True, (
                    f"micro_profit_fade: fade={forecast['fade_risk']:.2f} "
                    f"peak +{peak_pct:.2%} now +{pnl_pct:.2%}"
                )
        
        # Dead trade: Ollama + PPO decide (rules are guardrail fallback only)
        ai_check_sec = float(getattr(self.cfg, "AI_STAGNATION_CHECK_SEC", 30.0))
        if getattr(self.cfg, "STAGNATION_EXIT_ENABLED", True) and stagnant_sec >= ai_check_sec:
            flat_band = float(getattr(self.cfg, "STAGNATION_FLAT_BAND_PCT", 0.008))
            max_peak = float(getattr(self.cfg, "STAGNATION_MAX_PEAK_PCT", 0.003))
            loss_cut = float(getattr(self.cfg, "STAGNATION_LOSS_CUT_PCT", -0.005))
            stagnation_sec = float(getattr(self.cfg, "STAGNATION_EXIT_SEC", 90.0))
            never_ran = peak_pct < max_peak
            in_flat_band = abs(pnl_pct) <= flat_band
            losing_flat = pnl_pct <= loss_cut and abs(pnl_pct) <= flat_band * 2
            if never_ran and (in_flat_band or losing_flat or pnl_pct <= loss_cut):
                stagnation_ctx = {
                    "ticker": self.current_ticker,
                    "price": current_px,
                    "entry": entry_px,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "peak_pct": round(peak_pct * 100, 2),
                    "stagnant_sec": round(stagnant_sec, 1),
                    "price_frozen_sec": round(stagnant_sec, 1),
                    "stop": self._position_stop,
                    "target": self._position_target,
                }
                ppo_exit, ppo_conf, ppo_reason = False, 0.5, ""
                try:
                    ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
                except Exception:
                    pass
                if getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                    if self._has_ai_council(self.current_ticker or "", "stagnation_check"):
                        return False, "council_deliberating"
                    ai_dec = self.ai_commander.decide_stagnation(
                        stagnation_ctx, ppo_exit, ppo_conf, ppo_reason,
                    )
                    self._last_stagnation_decision = ai_dec
                    if ai_dec.get("pending"):
                        self._set_ai_council(self.current_ticker or "", "stagnation_check", {
                            "fingerprint": ai_dec["fingerprint"],
                            "ppo_exit": ppo_exit,
                            "ppo_conf": ppo_conf,
                            "ppo_reason": ppo_reason,
                            "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                            "stagnant_sec": stagnant_sec,
                            "stagnation_sec": stagnation_sec,
                            "ctx": stagnation_ctx,
                            "current_px": current_px,
                        })
                        log.info(
                            f"  🧠 COUNCIL stagnation {self.current_ticker}: "
                            f"{(ai_dec.get('reason') or 'deliberating')[:100]} | "
                            f"{ai_dec.get('pipeline', '')}"
                        )
                        return False, "council_deliberating"
                    if ai_dec.get("force_snapshot") and self.current_ticker:
                        snap_px = self._force_price_snapshot(self.current_ticker)
                        self._last_price_snapshot_at = time.time()
                        if snap_px > 0:
                            current_px = snap_px
                    min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
                    if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf * 0.9:
                        return True, f"ai_stagnation: {ai_dec.get('reason', '')[:100]}"
                # Hard guardrail only when council mode is off
                if (
                    not is_ai_unlimited(self.cfg)
                    and not is_ai_council_mode(self.cfg)
                    and stagnant_sec >= stagnation_sec
                ):
                    if never_ran and (in_flat_band or losing_flat):
                        return True, (
                            f"stagnation_guard: {stagnant_sec:.0f}s flat "
                            f"P&L {pnl_pct:+.2%} peak {peak_pct:+.2%}"
                        )
                    if pnl_pct < loss_cut and never_ran:
                        return True, (
                            f"dead_momentum_guard: {pnl_pct:+.2%} for {stagnant_sec:.0f}s "
                            f"(peak {peak_pct:+.2%})"
                        )

        # Lock profit — AI first; green lock quick-scalp if AI stalls
        if peak_pct > 0.015:
            giveback = peak_pct - pnl_pct
            if giveback > peak_pct * 0.4 and pnl_pct > 0.003:
                ticker = self.current_ticker or ""
                reason = f"profit_lock: peak +{peak_pct:.2%} now +{pnl_pct:.2%}"
                stalled = self._ai_profit_decision_stalled(pnl_pct)
                from core.green_profit_lock import evaluate_green_lock

                should_lock, lock_reason = evaluate_green_lock(
                    self.cfg,
                    pnl_pct=pnl_pct,
                    peak_pct=peak_pct,
                    ai_stalled=stalled,
                    giveback_from_peak=giveback,
                    was_green=getattr(self, "_was_in_profit", False),
                )
                if should_lock:
                    return True, lock_reason
                if is_ai_council_mode(self.cfg) and self.ai_commander and not stalled:
                    ppo_exit, ppo_conf, ppo_reason = False, 0.55, reason
                    try:
                        ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
                        ppo_conf = max(ppo_conf, 0.55)
                    except Exception:
                        pass
                    if self._deliberate_exit_council(
                        ticker, current_px, True, ppo_conf, ppo_reason or reason,
                        {"signal": "profit_lock"},
                    ):
                        return False, "council_deliberating"
                else:
                    return True, reason

        try:
            ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
            ticker = self.current_ticker or ""
            if is_ai_council_mode(self.cfg) and self.ai_commander:
                if self._deliberate_exit_council(
                    ticker, current_px, ppo_exit, ppo_conf, ppo_reason,
                ):
                    return False, "council_deliberating"
            elif getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander:
                if self._has_ai_council(ticker, "exit_decision"):
                    return False, "council_deliberating"
                exit_ctx = {
                    "ticker": ticker,
                    "price": current_px,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "entry": entry_px,
                    "stop": self._position_stop,
                    "target": self._position_target,
                }
                ai_dec = self.ai_commander.decide_exit(
                    exit_ctx, obs=self._build_ppo_obs(current_px),
                )
                if ai_dec.get("pending"):
                    self._set_ai_council(ticker, "exit_decision", {
                        "fingerprint": ai_dec["fingerprint"],
                        "ppo_exit": ppo_exit,
                        "ppo_conf": ppo_conf,
                        "ppo_reason": ppo_reason,
                        "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                        "ctx": exit_ctx,
                        "current_px": current_px,
                    })
                    return False, "council_deliberating"
                if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= self.cfg.CONFIDENCE_THRESHOLD:
                    return True, f"AI_exit: conf={float(ai_dec.get('confidence', 0)):.2f} | {ai_dec.get('reason', '')[:80]}"
            elif ppo_exit and ppo_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                return True, f"AI_exit: conf={ppo_conf:.2f} | {ppo_reason[:80]}"
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
                ticker = self.current_ticker or ""
                if slippage > 0.75 and pnl_pct > 0.005:
                    reason = f"slippage_risk: {slippage:.0%}"
                    if is_ai_council_mode(self.cfg) and self.ai_commander:
                        if self._deliberate_exit_council(
                            ticker, current_px, True, 0.6, reason, {"signal": "slippage"},
                        ):
                            return False, "council_deliberating"
                    else:
                        return True, reason
                is_spike, _ = self._detect_volume_spike(fast_df)
                fade_exit, fade_reason = evaluate_wave_end_on_spike_fade(
                    self.cfg, fast_df, current_px, entry_px, self._position_peak, pnl_pct,
                )
                if fade_exit:
                    reason = fade_reason
                    if is_ai_council_mode(self.cfg) and self.ai_commander:
                        if mechanical_bypass_council(self.cfg):
                            return True, reason
                        if self._deliberate_exit_council(
                            ticker, current_px, True, 0.55, reason, {"signal": "wave_end_spike_fade"},
                        ):
                            return False, "council_deliberating"
                    else:
                        return True, reason
                if not is_spike and pnl_pct > 0.012:
                    reason = f"wave_end: profit {pnl_pct:.2%} volume fading"
                    if is_ai_council_mode(self.cfg) and self.ai_commander:
                        if self._deliberate_exit_council(
                            ticker, current_px, True, 0.55, reason, {"signal": "wave_end"},
                        ):
                            return False, "council_deliberating"
                    else:
                        return True, reason
        except Exception:
            pass
        
        if unrealized_pnl > 0 and unrealized_pnl < 2.0 and risk_usd > 35 and pnl_pct < 0.008:
            if getattr(self.cfg, "USE_FIXED_RISK_CAP", False):
                return True, f"low_profit_high_risk: ${unrealized_pnl:.2f}"
        
        return False, "hold"
    
    def _update_trailing_stops(self, current_px: float):
        """Ratchet stop / extend TP — always applies mechanical trail; prefetches council when AI on."""
        if self.shares <= 0 or self._entry_price <= 0 or not self.bracket_handle:
            return

        mech_stop, mech_target = self._compute_mechanical_trail(current_px)
        entry = self._entry_price
        pnl_pct = ((current_px / entry) - 1) * 100 if entry else 0
        peak_pct = (
            ((self._position_peak / entry) - 1) * 100
            if self._position_peak > entry else pnl_pct
        )

        pipeline_on = getattr(self.cfg, "LIVE_AI_PIPELINE_ENABLED", True)
        ai_full = getattr(self.cfg, "AI_FULL_CONTROL", True) and self.ai_commander
        if pipeline_on and ai_full:
            ticker = self.current_ticker or ""
            if ticker:
                try:
                    from core.council_nanny import prefetch_enabled
                    if prefetch_enabled(self.cfg):
                        self.ai_commander.prefetch_position_manage({
                            "ticker": ticker,
                            "price": current_px,
                            "pnl_pct": round(pnl_pct, 2),
                            "peak_pct": round(peak_pct, 2),
                            "stop": self._position_stop,
                            "target": self._position_target,
                            "mechanical_stop": mech_stop,
                            "mechanical_target": mech_target,
                        })
                except Exception:
                    pass

        if mech_stop:
            self._apply_stop_update(mech_stop, f"trail locked +{peak_pct / 100:.2%}")
        if mech_target:
            self._apply_target_update(mech_target, "momentum TP extension")
    
    def _store_scan_cache(self, ticker: str, df: pd.DataFrame) -> None:
        """Bounded LRU-style scan bar cache — avoids unbounded DataFrame RAM."""
        key = str(ticker or "").upper()
        if not key:
            return
        try:
            slim = df.tail(self._scan_cache_max_bars).copy()
        except Exception:
            slim = df
        self._scan_data_cache[key] = slim
        if len(self._scan_data_cache) <= self._scan_cache_max_tickers:
            return
        locked = set()
        if self.current_ticker:
            locked.add(str(self.current_ticker).upper())
        for t in getattr(self, "_locked_target_names", []) or []:
            locked.add(str(t).upper())
        if self.top_pick and getattr(self.top_pick, "ticker", None):
            locked.add(str(self.top_pick.ticker).upper())
        for cache_key in list(self._scan_data_cache.keys()):
            if len(self._scan_data_cache) <= self._scan_cache_max_tickers:
                break
            if cache_key not in locked:
                self._scan_data_cache.pop(cache_key, None)

    def _score_ticker(self, ticker: str, df: pd.DataFrame) -> Dict:
        closes = df["close"].values
        volumes = df["volume"].values
        current_px = float(closes[-1])
        if not _only_uptrend(df, current_px):
            return {"ticker": ticker, "total_score": 0, "price": current_px, "volume": int(volumes[-1]), "avg_volume": int(np.mean(volumes[-20:])), "rel_vol": 1.0, "reasons": "not_uptrend"}
        score = 1.0
        reasons = ["uptrend"]
        weights = self._load_weights()
        w_mom = float(weights.get("momentum", 2.0))
        w_vol = float(weights.get("volume", 15.0))
        w_inst = float(weights.get("institutional", 20.0))
        w_vwap = float(weights.get("vwap_slope", 5.0))
        w_atr = float(weights.get("atr_bonus", 5.0))
        w_mr = float(weights.get("mean_reversion", 5.0))
        ret_5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        ret_10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) > 10 else 0
        ret_20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else 0
        mom_score = ret_5 * 0.5 + ret_10 * 0.3 + ret_20 * 0.2
        score += mom_score * w_mom
        if mom_score > 2:
            reasons.append(f"strong_mom_{mom_score:.1f}")
        vol_avg20 = np.mean(volumes[-20:])
        vol_avg5 = np.mean(volumes[-5:])
        vol_ratio = vol_avg5 / (vol_avg20 + 1e-9)
        score += max(0, vol_ratio - 1.0) * w_vol
        if vol_ratio > 1.3:
            reasons.append(f"vol_{vol_ratio:.1f}x")
        inst = InstitutionalDetector()
        for i in range(-20, 0):
            inst.feed_bar(float(volumes[i]), float(closes[i]))
        sig = inst.scan()
        if sig.direction == "accumulating" and sig.strength > 0.5:
            score += sig.strength * w_inst
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
        score += max(0, vwap_slope) * w_vwap
        if vwap_slope > 0.5:
            reasons.append(f"vwap_up_{vwap_slope:.2f}%")
        atr = compute_atr(df, period=10)
        atr_pct = (atr / current_px) * 100
        if 0.3 < atr_pct < 3.0:
            score += w_atr
        ema9 = pd.Series(closes).ewm(span=9, adjust=False).mean().iloc[-1]
        dist = (current_px - ema9) / (pd.Series(closes).diff().rolling(20).std().iloc[-1] + 1e-9)
        if abs(dist) < 1.5:
            score += w_mr
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
    
    def _attempt_hot_swap_entry(self):
        """Enter the best pre-scouted ticker immediately after an exit."""
        if self._open_position_count() >= self._max_concurrent():
            return
        if not getattr(self.cfg, "HOT_SWAP_ON_EXIT", True):
            return
        if self._pending_entry_ticker or self._entry_poll_states:
            return
        pick = self._next_best_pick or self.top_pick
        if not pick:
            return
        df = self._scan_data_cache.get(pick.ticker)
        if df is None or len(df) < 20:
            return
        px = float(df["close"].iloc[-1])
        if not _only_uptrend(df.tail(60), px):
            return
        is_spike, vol = self._detect_volume_spike(df.tail(60))
        if not is_spike:
            vol = float(df["volume"].tail(3).mean()) / (float(df["volume"].tail(20).mean()) + 1e-9)
            if vol < 1.15:
                return
        self.top_pick = pick
        log.info(
            f"⚡ HOT SWAP: {pick.ticker} vol={vol:.1f}x score={pick.rank_score:.0f} "
            f"— entering right after exit"
        )
        self._attempt_entry()

    def _open_position_from_fill(
        self, ticker: str, shares: int, fill_px: float, plan: TradePlan,
    ) -> str:
        """Bookkeeping after IB confirms an entry fill."""
        from core.fill_tracker import _sane_fill_ratio, position_avg_cost

        planned_entry = float(plan.entry_price)
        fill_bracket = self._bracket_for_entry_fill(ticker)
        if not _sane_fill_ratio(fill_px, planned_entry):
            avg = position_avg_cost(self.ib, ticker)
            if avg > 0 and _sane_fill_ratio(avg, planned_entry):
                log.warning(
                    f"  🔧 Fill price corrected {ticker}: ${fill_px:.4f} → ${avg:.4f} "
                    f"(planned ${planned_entry:.4f})"
                )
                fill_px = avg
        old_stop = float(plan.initial_stop_price)
        old_target = float(plan.take_profit_price)
        adapt = adapt_bracket_to_fill(
            self.cfg, planned_entry, fill_px,
            old_stop, old_target, shares, float(plan.atr_at_entry or 0),
        )
        log_post_fill_adapt(
            ticker=ticker,
            planned_entry=planned_entry,
            fill_px=fill_px,
            old_stop=old_stop,
            old_target=old_target,
            new_stop=adapt.stop,
            new_target=adapt.target,
            shares=shares,
            slippage_pct=adapt.slippage_pct,
            adjusted=adapt.adjusted,
            aborted=adapt.abort,
            reason=adapt.reason,
        )
        if adapt.abort:
            log.warning(
                f"  🛑 SLIPPAGE ABORT {ticker}: flattening {shares}sh @ ${fill_px:.4f} | "
                f"{adapt.reason}"
            )
            try:
                buffer_append({
                    "source": "fill_slippage_abort",
                    "ticker": ticker,
                    "action": "ABORT",
                    "reward": reward_from_bracket_reject(
                        self.cfg,
                        spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                        inverted=fill_px >= old_target,
                    ),
                    "reason": adapt.reason[:200],
                    "fill_px": fill_px,
                    "planned_entry": planned_entry,
                    "slippage_pct": adapt.slippage_pct,
                })
            except Exception:
                pass
            handle = fill_bracket
            try:
                self.broker.flatten_position(
                    int(shares), handle=handle, urgent=True, symbol=ticker,
                )
                self.ib.sleep(0.15)
            except Exception as exc:
                log.warning(f"  Flatten after slippage abort failed: {exc}")
            self.broker.cancel_open_orders_for_symbol(ticker)
            if self.bracket_handle is handle:
                self.bracket_handle = None
            self._clear_pending_entry(ticker, cooldown_sec=60.0)
            for task in ("entry_decision", "exit_decision", "position_manage"):
                self._ai_councils.pop(self._council_key(ticker, task), None)
            self._position_slots.pop(ticker, None)
            self._refresh_aggregate_position_state()
            return "aborted_slippage"

        if adapt.adjusted or adapt.ok:
            plan = TradePlan(
                side="LONG",
                entry_price=fill_px,
                shares=float(shares),
                initial_stop_price=adapt.stop,
                take_profit_price=adapt.target,
                risk_usd=adapt.risk_usd or plan.risk_usd,
                atr_at_entry=plan.atr_at_entry,
            )
            handle = fill_bracket
            if handle and adapt.adjusted:
                try:
                    self.broker.update_stop_price(handle, adapt.stop)
                    self.broker.update_target_price(handle, adapt.target)
                    log.info(
                        f"  🔧 IB bracket updated for fill slip: "
                        f"stop ${adapt.stop:.4f} tp ${adapt.target:.4f}"
                    )
                except Exception as exc:
                    log.warning(f"  IB bracket re-anchor failed: {exc}")

        self._clear_pending_entry(ticker)
        opened_at = time.time()
        tel = getattr(self, "_last_entry_telemetry", {}) or {}
        limit_px = tel.get("limit_px")
        parent_trade = None
        if fill_bracket and fill_bracket.parent_trade:
            parent_trade = fill_bracket.parent_trade
        entry_fill = resolve_entry_fill(
            self.ib, symbol=ticker, parent_trade=parent_trade, quote_px=fill_px,
            max_wait=0.0, cache=self._fill_cache(),
        )
        if entry_fill > 0 and _sane_fill_ratio(entry_fill, planned_entry):
            fill_px = entry_fill
        cost = shares * fill_px * (1 + self.cfg.TRANSACTION_COST_PCT)
        self.bot_cash -= cost
        slippage_pct = 0.0
        if limit_px and float(limit_px) > 0:
            slippage_pct = (fill_px - float(limit_px)) / float(limit_px)
        vision_read = ""
        if self.ai_commander:
            try:
                vision_read = self.ai_commander.chart_read_for(
                    ticker, fill_px,
                    float(getattr(self, "_last_spike_ratio", 1.0)),
                    float(getattr(self, "_last_scan_score", 0.0)),
                )
            except Exception:
                pass
        slot = {
            "shares": float(shares),
            "entry_price": fill_px,
            "entry_fill_px": fill_px,
            "limit_px": float(limit_px) if limit_px else None,
            "entry_slippage_pct": round(slippage_pct, 6),
            "entry_mode": str(tel.get("entry_mode", "market")),
            "regime": str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "peak": fill_px,
            "hard_floor": plan.initial_stop_price,
            "opened_at": opened_at,
            "prev_shares": float(shares),
            "last_pulse_price": fill_px,
            "last_price_change_at": opened_at,
            "last_price_snapshot_at": 0.0,
            "last_pulse_fingerprint": "",
            "last_position_pulse": 0.0,
            "last_ai_position_manage": 0.0,
            "last_stagnation_decision": {},
            "vision_read": vision_read[:800],
        }
        lot_meta = self._pending_lottery_meta.pop(ticker.upper(), {})
        if lot_meta.get("lottery_bank"):
            slot.update({
                k: lot_meta[k] for k in (
                    "lottery_bank", "lottery_tier", "lottery_conviction", "lottery_reason",
                ) if k in lot_meta
            })
        self._position_slots[ticker] = slot
        try:
            from core.war_account import record_entry, war_account_enabled
            if war_account_enabled(self.cfg):
                record_entry(
                    self.cfg,
                    ticker=ticker,
                    shares=int(shares),
                    ib_fill=float(fill_px),
                    quote=float(fill_px),
                    pipeline=str(getattr(self, "_last_entry_pipeline", "")),
                    spread_pct=abs(slippage_pct),
                )
        except Exception as exc:
            log.debug(f"War account entry: {exc}")
        if lot_meta.get("lottery_bank"):
            try:
                from core.lottery_bank import notify_lottery_event, record_entry
                row = record_entry(
                    self.cfg,
                    ticker=ticker,
                    shares=float(shares),
                    fill_px=float(fill_px),
                    meta=lot_meta,
                )
                notify_lottery_event(self.notifier, self.cfg, "lottery_entry", row)
            except Exception as exc:
                log.debug(f"Lottery bank entry record: {exc}")
        if fill_bracket:
            self._bracket_by_ticker[ticker] = fill_bracket
        self._load_position_context(ticker)
        self._recalc_bot_nav()
        self._ensure_position_stream(ticker)
        self._risk_plans[ticker] = plan
        self.risk.open_position(plan)
        self._reset_profit_hunt_state()
        self._active_stream_ticker = ticker
        slot["last_ai_position_manage"] = 0.0
        slot["last_position_pulse"] = 0.0
        self._last_ai_position_manage = 0.0
        self._last_position_pulse = 0.0
        if self.risk.plan:
            self.risk.plan.peak_price = max(self.risk.plan.peak_price, fill_px)
        try:
            self._update_trailing_stops(fill_px)
        except Exception:
            pass
        log.info(f"  📡 POST-ENTRY: live monitor + trailing armed on {ticker}")
        if not hasattr(self, "_active_positions"):
            self._active_positions = []
        self._active_positions.append({
            "ticker": ticker,
            "entry_price": fill_px,
            "shares": shares,
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "entry_time": time.time(),
        })
        try:
            from core.capital_discipline import capital_discipline_enabled
            from core.smart_stack import count_hourly_filled_entry
            if capital_discipline_enabled(self.cfg) and count_hourly_filled_entry(self.cfg):
                self._entries_this_hour = getattr(self, "_entries_this_hour", 0) + 1
        except Exception:
            pass
        self.trades_today += 1
        self.current_ticker = ticker
        log.info(
            f"🎯 ENTRY: {shares}x {ticker} @ ${fill_px:.2f} | "
            f"Stop ${plan.initial_stop_price:.2f} | TP ${plan.take_profit_price:.2f} | "
            f"Deployed: ${cost:,.0f}"
        )
        entry_ctx = {
            "ticker": ticker, "shares": shares, "entry": fill_px,
            "stop": plan.initial_stop_price, "target": plan.take_profit_price,
            "pilot_level": self.pilot.state.level,
            "deployed": cost,
        }
        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, "trade_opened",
                self._notify_context(entry_ctx),
                f"🎯 ENTRY {shares}x {ticker} @ ${fill_px:.2f} | "
                f"Stop ${plan.initial_stop_price:.2f} | TP ${plan.take_profit_price:.2f}",
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info(
                f"🎯 HANOON ENTRY\nTicker: {ticker}\nQty: {shares}\n"
                f"Entry: ${fill_px:.2f}\nStop: ${plan.initial_stop_price:.2f}\n"
                f"Target: ${plan.take_profit_price:.2f}\nDeployed: ${cost:,.0f}"
            )
        push_trade(ticker, "BUY", fill_px, shares)
        append_fill_ledger({
            "event": "entry_fill",
            "ticker": ticker,
            "entry_fill": round(fill_px, 4),
            "limit_px": float(limit_px) if limit_px else None,
            "entry_slippage_pct": round(slippage_pct, 6),
            "shares": shares,
            "stop": plan.initial_stop_price,
            "target": plan.take_profit_price,
            "entry_mode": str(tel.get("entry_mode", "market")),
            "regime": str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
        })
        snap_parsed = {}
        if self.ai_commander:
            snap = self.ai_commander.ollama_audit_snapshot(ticker)
            snap_parsed = snap.get("parsed") or {}
        log_entry_execution(
            ticker=ticker,
            limit_px=float(limit_px) if limit_px else None,
            fill_px=fill_px,
            entry_mode=str(tel.get("entry_mode", "market")),
            shares=shares,
            stop=plan.initial_stop_price,
            target=plan.take_profit_price,
            regime=str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
            spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
            council_decision=tel.get("council"),
            ollama_raw=str(tel.get("ollama_raw", "")),
            ollama_parsed=snap_parsed,
            shadow=False,
        )
        self._last_entry_telemetry["slippage_pct"] = slippage_pct
        self._last_entry_telemetry["atr"] = float(plan.atr_at_entry or 0)
        try:
            buffer_append({
                "source": "live_entry",
                "ticker": ticker,
                "action": "BUY",
                "entry_price": fill_px,
                "shares": shares,
                "stop": plan.initial_stop_price,
                "target": plan.take_profit_price,
                "reward": reward_from_trade(
                    0.0, self.cfg,
                    slippage_pct=slippage_pct,
                    spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                ),
                "regime": getattr(self, "_last_entry_regime", ""),
                "confidence": getattr(self, "_last_ai_confidence", 0.5),
                "features": snapshot_features(self._feature_buffer, self.cfg),
                "spike_ratio": float(getattr(self, "_last_spike_ratio", 1.0)),
                "scan_score": float(getattr(self, "_last_scan_score", 0)),
                "volume_ratio": float(
                    getattr(self, "_last_market_ctx", {}).get("recent_volume", 0)
                    / (getattr(self, "_last_market_ctx", {}).get("avg_volume", 1) + 1e-9)
                ),
                "slippage_pct": round(slippage_pct, 6),
                "cash_ratio": self.bot_cash / (self.bot_cash + self.shares * fill_px + 1e-9),
                "pos_ratio": (self.shares * fill_px) / (self.bot_cash + self.shares * fill_px + 1e-9),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        try:
            from core.ppo_entry_learning import on_entry_fill
            from core.pilot_mode import snapshot_features

            features = snapshot_features(self._feature_buffer, self.cfg)
            council = tel.get("council") or {}
            obs = None
            if len(self._feature_buffer) >= self.cfg.WINDOW_SIZE:
                window = np.array(
                    list(self._feature_buffer)[-self.cfg.WINDOW_SIZE:],
                    dtype=np.float32,
                ).flatten()
                total = self.bot_cash + self.shares * fill_px
                c_rat = self.bot_cash / (total + 1e-9)
                p_rat = (self.shares * fill_px) / (total + 1e-9)
                obs = np.concatenate([window, [c_rat, p_rat]]).astype(np.float32)
            entry_id = on_entry_fill(
                self.cfg,
                ticker=ticker,
                entry_price=fill_px,
                shares=shares,
                features=features,
                ai_commander=self.ai_commander,
                council_decision=council,
                spike_ratio=float(getattr(self, "_last_spike_ratio", 1.0)),
                scan_score=float(getattr(self, "_last_scan_score", 0)),
                slippage_pct=slippage_pct,
                regime=str(tel.get("regime", getattr(self, "_last_entry_regime", ""))),
                model=self.model,
                obs=obs,
            )
            if ticker in self._position_slots:
                self._position_slots[ticker]["ppo_entry_id"] = entry_id
        except Exception as exc:
            log.debug(f"PPO entry learning: {exc}")
        if self.ai_commander:
            council = tel.get("council") or {}
            try:
                self.ai_commander.ring_post_fill_learning(
                    ticker,
                    fill_px,
                    float(getattr(self, "_last_spike_ratio", 1.0)),
                    float(getattr(self, "_last_scan_score", 0)),
                    council,
                    account=self._account_context_for_ai(),
                    market_ctx=getattr(self, "_last_market_ctx", None),
                    df=self._scan_data_cache.get(ticker),
                )
            except Exception as exc:
                log.debug(f"Post-fill distill ring: {exc}")
        return "entered"

    def _service_shadow_positions(self) -> None:
        """Mark shadow trades to stop/target while IB routing is blocked."""
        if not self.shadow_circuit.in_shadow or not self.shadow_circuit.shadow_open:
            return
        for ticker in list(self.shadow_circuit.shadow_open.keys()):
            df = self._scan_data_cache.get(ticker)
            if df is None or len(df) < 1:
                continue
            try:
                px = self._live_price_for(ticker, float(df["close"].iloc[-1]))
                rec = self.shadow_circuit.update_shadow_price(ticker, px)
                if not rec:
                    continue
                log_exit_postmortem(
                    ticker=ticker,
                    entry=float(rec.get("entry", 0)),
                    exit_px=float(rec.get("exit", px)),
                    shares=float(rec.get("shares", 0)),
                    pnl_usd=float(rec.get("pnl_usd", 0)),
                    pnl_pct=0.0,
                    result=str(rec.get("result", "")),
                    regime=str(rec.get("regime", "")),
                    hold_sec=float(rec.get("hold_sec", 0)),
                    exit_reason=str(rec.get("reason", "")),
                    shadow=True,
                )
                buffer_append({
                    "source": "shadow_trade",
                    "ticker": ticker,
                    "pnl_usd": rec.get("pnl_usd", 0),
                    "win": 1 if float(rec.get("pnl_usd", 0)) > 0 else 0,
                    "reward": reward_from_trade(float(rec.get("pnl_usd", 0)), self.cfg),
                    "regime": rec.get("regime", ""),
                })
            except Exception:
                pass

    def _service_pending_entry(self):
        """Non-blocking IB fill polls — one pass per pending ticker."""
        if not self._entry_poll_states:
            return
        for ticker in list(self._entry_poll_states.keys()):
            self._service_one_pending_entry(ticker)

    def _service_one_pending_entry(self, ticker: str):
        """Poll a single ticker's pending bracket fill."""
        st = self._entry_poll_states.get(ticker)
        bracket = (st or {}).get("bracket") or self._pending_brackets_by_ticker.get(ticker)
        if not st or not bracket:
            self._entry_poll_states.pop(ticker, None)
            return
        shares = int(st["shares"])
        plan: TradePlan = st["plan"]
        fill_px = float(st["fill_px"])
        min_fill_ratio = float(st["min_fill_ratio"])
        fail_cd = float(st["fail_cd"])
        self.ib.sleep(0.05)
        parent_trade = getattr(bracket, "parent_trade", None)
        parent_id = bracket.parent_order_id
        parent_status = (
            parent_trade.orderStatus.status
            if parent_trade and parent_trade.orderStatus else "Unknown"
        )
        ierr = self.conn.pop_order_error(parent_id)
        if ierr:
            st["last_ib_error"] = ierr
        if ierr and ierr.get("code") == 2161:
            log.warning(f"  IB 2161 regulatory cap on {ticker} — will retry smaller limit")
            self._observe_runtime(
                "ib_failure",
                ticker=ticker,
                reason=str((ierr or {}).get("message", ""))[:200],
                ib_code=2161,
                price_cap=(ierr or {}).get("price_cap"),
                parent_status=parent_status,
                market_state=get_market_state(self.cfg),
            )
        if parent_status in ("Cancelled", "Inactive", "ApiCancelled"):
            block_reason = parse_ib_order_block(ierr)
            if block_reason:
                self._entry_poll_states.pop(ticker, None)
                self._ai_skip_ticker_permanent(ticker, block_reason)
                return
            if (
                st["attempt"] == 0
                and getattr(self.cfg, "ENTRY_RETRY_ON_IB2161", True)
                and (ierr or {}).get("code") == 2161
            ):
                self.broker.cancel_open_orders_for_symbol(ticker)
                st["attempt"] = 1
                st["polls"] = 0
                cap = (ierr or {}).get("price_cap")
                retry_sh = max(1, shares // 2)
                st["shares"] = retry_sh
                plan = TradePlan(
                    side="LONG", entry_price=fill_px, shares=float(retry_sh),
                    initial_stop_price=plan.initial_stop_price,
                    take_profit_price=plan.take_profit_price,
                    risk_usd=plan.risk_usd,
                    atr_at_entry=plan.atr_at_entry,
                )
                st["plan"] = plan
                entry_px = cap if cap and cap > 0 else None
                new_bracket = self.broker.place_bracket_buy(
                    quantity=retry_sh, limit_or_market_price=entry_px,
                    stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                    symbol=ticker,
                )
                st["bracket"] = new_bracket
                self._pending_brackets_by_ticker[ticker] = new_bracket
                log.info(f"  🔄 IB2161 retry: {retry_sh} sh limit @ ${entry_px or fill_px:.4f}")
                return
            log.warning(f"Entry order rejected by IB ({parent_status}) — not opening position")
            self._observe_runtime(
                "order_canceled",
                ticker=ticker,
                reason=str((ierr or {}).get("message", parent_status)),
                ib_code=(ierr or {}).get("code"),
                parent_status=parent_status,
                market_state=get_market_state(self.cfg),
            )
            self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
            return
        filled_shares = 0.0
        filled = float(parent_trade.orderStatus.filled) if parent_trade and parent_trade.orderStatus else 0.0
        if filled > 0:
            filled_shares = filled
            avg = float(parent_trade.orderStatus.avgFillPrice or fill_px)
            if avg > 0:
                fill_px = avg
                st["fill_px"] = fill_px
        if filled_shares < 1:
            for p in self.ib.positions():
                if getattr(p.contract, "symbol", "") == ticker and float(p.position) > 0:
                    pos_shares = float(p.position)
                    if pos_shares >= shares * min_fill_ratio:
                        filled_shares = pos_shares
                        avg_cost = float(getattr(p, "avgCost", 0) or 0)
                        if avg_cost > 0:
                            fill_px = avg_cost
                            st["fill_px"] = fill_px
                        break
        if filled_shares >= shares * min_fill_ratio or parent_status == "Filled":
            self._open_position_from_fill(ticker, int(filled_shares), fill_px, plan)
            return
        if parent_status == "PendingSubmit":
            since = st.get("pending_submit_since")
            if since is None:
                st["pending_submit_since"] = time.time()
            max_ps = float(getattr(self.cfg, "PENDING_SUBMIT_MAX_SEC", 4.0))
            if (
                since is not None
                and (time.time() - since) >= max_ps
                and not st.get("market_retry_done")
            ):
                st["market_retry_done"] = True
                log.warning(
                    f"  ⚡ {ticker} stuck PendingSubmit >{max_ps:.0f}s — cancel + MARKET retry"
                )
                self.broker.cancel_open_orders_for_symbol(ticker)
                self.ib.sleep(0.3)
                retry_sh = int(shares)
                if getattr(self.cfg, "PAPER_TRADING", False):
                    retry_sh = min(
                        retry_sh,
                        int(getattr(self.cfg, "PAPER_MAX_ENTRY_SHARES", 5000)),
                    )
                try:
                    new_bracket = self.broker.place_bracket_buy(
                        quantity=retry_sh,
                        limit_or_market_price=None,
                        stop_price=plan.initial_stop_price,
                        target_price=plan.take_profit_price,
                        symbol=ticker,
                    )
                    st["bracket"] = new_bracket
                    self._pending_brackets_by_ticker[ticker] = new_bracket
                    st["shares"] = retry_sh
                    st["polls"] = 0
                    st["pending_submit_since"] = None
                    st["limit_px"] = None
                except Exception as exc:
                    log.warning(f"  Market retry failed for {ticker}: {exc}")
                    self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
                return
        else:
            st["pending_submit_since"] = None
        st["polls"] = int(st.get("polls", 0)) + 1
        polls = st["polls"]
        max_polls = int(st["max_polls"])
        now_ts = time.time()
        last_hb = float(st.get("last_heartbeat", 0))
        if polls == 1 or polls % 5 == 0 or (now_ts - last_hb) >= 3.0:
            st["last_heartbeat"] = now_ts
            live_px = self._live_price_for(ticker, fill_px)
            limit_px = float(st.get("limit_px") or fill_px)
            elapsed = now_ts - float(st.get("started_at", now_ts))
            log.info(
                f"  ⏳ PENDING ENTRY {ticker}: limit ${limit_px:.4f} | "
                f"market ${live_px:.4f} | poll {polls}/{max_polls} "
                f"({parent_status}) | {elapsed:.1f}s"
            )
        chase_pct = float(getattr(self.cfg, "ENTRY_LIMIT_CHASE_PCT", 0.006))
        if polls >= 5 and parent_trade and parent_trade.order:
            live_px = self._live_price_for(ticker, fill_px)
            limit_px = float(getattr(parent_trade.order, "lmtPrice", 0) or st.get("limit_px") or 0)
            if live_px > 0 and limit_px > 0 and live_px > limit_px * (1 + chase_pct):
                new_limit = round(live_px * (1 + chase_pct * 0.5), 4)
                try:
                    parent_trade.order.lmtPrice = new_limit
                    self.ib.placeOrder(parent_trade.contract, parent_trade.order)
                    st["limit_px"] = new_limit
                    log.info(
                        f"  🏃 CHASE LIMIT {ticker}: ${limit_px:.4f} → ${new_limit:.4f} "
                        f"(market ${live_px:.4f})"
                    )
                except Exception as exc:
                    log.debug(f"Limit chase failed: {exc}")
        if polls >= max_polls:
            if filled_shares >= 1:
                log.warning(
                    f"Partial fill {int(filled_shares)}/{shares} below "
                    f"{min_fill_ratio:.0%} — flattening and skipping entry"
                )
                self.broker.flatten_position(
                    int(filled_shares), handle=bracket, urgent=True, symbol=ticker,
                )
                self.ib.sleep(0.1)
            elif parent_status in ("Submitted", "PreSubmitted", "PendingSubmit"):
                log.info(f"Entry order timed out for {ticker} ({parent_status})")
                self._observe_runtime(
                    "order_timeout",
                    ticker=ticker,
                    reason=parent_status,
                    ib_code=(st.get("last_ib_error") or {}).get("code"),
                    parent_status=parent_status,
                    market_state=get_market_state(self.cfg),
                )
            else:
                log.info(f"Entry not filled for {ticker} (status={parent_status})")
            self.broker.cancel_open_orders_for_symbol(ticker)
            self._clear_pending_entry(ticker, cooldown_sec=fail_cd)

    def _council_key(self, ticker: str, task: str) -> str:
        return f"{ticker}:{task}"

    def _clear_ai_councils(self, ticker: str, tasks: Optional[List[str]] = None) -> None:
        """Drop pending Ollama deliberation for a ticker (e.g. after exit)."""
        if not ticker:
            return
        if tasks:
            for task in tasks:
                self._ai_councils.pop(self._council_key(ticker, task), None)
            return
        prefix = f"{ticker}:"
        for key in list(self._ai_councils.keys()):
            if key.startswith(prefix):
                self._ai_councils.pop(key, None)

    def _set_ai_council(self, ticker: str, task: str, state: Dict[str, Any]) -> None:
        # One Ollama slot per ticker — exit beats manage; avoid dual in_flight deadlock
        if task == "position_manage" and self._has_ai_council(ticker, "exit_decision"):
            return
        if task == "entry_decision" and (
            self._has_ai_council(ticker, "exit_decision")
            or self._has_ai_council(ticker, "position_manage")
        ):
            return
        if task == "exit_decision":
            self._clear_ai_councils(ticker, ["position_manage", "entry_decision"])
        state["ticker"] = ticker
        state["task"] = task
        state.setdefault("started_at", time.time())
        self._ai_councils[self._council_key(ticker, task)] = state

    def _service_stale_councils(self) -> None:
        """Last-resort clear — resolve runs first so timeout paths can enter/exit."""
        max_wait = council_max_wait_sec(self.cfg)
        now = time.time()
        for key in list(self._ai_councils.keys()):
            st = self._ai_councils.get(key)
            if not st:
                continue
            age = now - float(st.get("started_at", now))
            if age <= max_wait * 1.5:
                continue
            ticker = str(st.get("ticker", "?"))
            task = str(st.get("task", "?"))
            log.info(
                f"  ⏱️ COUNCIL force-clear {ticker}/{task} ({age:.0f}s) — "
                f"mechanical rules resume"
            )
            if task == "entry_decision":
                self._spike_attempt_until[ticker] = 0.0
            self._ai_councils.pop(key, None)

    def _has_ai_council(self, ticker: str, task: str) -> bool:
        return self._council_key(ticker, task) in self._ai_councils

    def _deliberate_exit_council(
        self,
        ticker: str,
        current_px: float,
        ppo_exit: bool,
        ppo_conf: float,
        ppo_reason: str,
        extra_ctx: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Run exit council. Returns True if deliberating (pending) or position was exited.
        False means council says hold — caller continues other checks.
        """
        entry_px = self._entry_price
        pnl_pct_frac = ((current_px / entry_px) - 1) if entry_px else 0.0
        pnl_pct = pnl_pct_frac * 100

        if profit_exit_bypasses_council(
            self.cfg, ppo_reason or "", pnl_pct_frac,
            ai_stalled=self._ai_profit_decision_stalled(pnl_pct_frac),
        ) and ppo_exit:
            log.info(f"  🎯 PROFIT HUNT bypass council: {ppo_reason[:80]}")
            track_profit_hunt_event(
                self.cfg, "profit_hunt_exit", ticker,
                {"reason": ppo_reason, "price": current_px, "bypass": "council"},
                pnl_usd=(current_px - entry_px) * self.shares if entry_px else 0,
                pnl_pct=pnl_pct_frac, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, ppo_reason[:120])
            return True

        if not self.ai_commander or not is_ai_council_mode(self.cfg):
            if ppo_exit and ppo_conf >= self.cfg.CONFIDENCE_THRESHOLD:
                self._exit_position(current_px, f"ppo_exit: {ppo_reason[:80]}")
                return True
            return False
        if self._has_ai_council(ticker, "exit_decision"):
            return True
        exit_ctx = {
            "ticker": ticker,
            "price": current_px,
            "pnl_pct": round(pnl_pct, 2),
            "entry": entry_px,
            "stop": self._position_stop,
            "target": self._position_target,
            **(extra_ctx or {}),
        }
        ai_dec = self.ai_commander.decide_exit(
            exit_ctx, obs=self._build_ppo_obs(current_px),
        )
        if ai_dec.get("pending"):
            self._set_ai_council(ticker, "exit_decision", {
                "fingerprint": ai_dec["fingerprint"],
                "ppo_exit": ppo_exit,
                "ppo_conf": ppo_conf,
                "ppo_reason": ppo_reason,
                "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                "ctx": exit_ctx,
                "current_px": current_px,
            })
            log.info(
                f"  🧠 COUNCIL exit {ticker}: "
                f"{(ai_dec.get('reason') or 'deliberating')[:80]} | {ai_dec.get('pipeline', '')}"
            )
            return True
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
                f"{ai_dec.get('pipeline', '')}"
            )
            self._exit_position(current_px, f"council_exit: {ai_dec.get('reason', '')[:80]}")
            return True
        return False

    def _deliberate_risk_exit(self, ticker: str, current_px: float, risk_signal: str) -> bool:
        """Risk-engine exit via council. True = pending or exited."""
        entry_px = self._entry_price
        pnl_pct_frac = ((current_px / entry_px) - 1) if entry_px else 0.0
        pnl_pct = pnl_pct_frac * 100

        if profit_exit_bypasses_council(
            self.cfg, risk_signal, pnl_pct_frac,
            ai_stalled=self._ai_profit_decision_stalled(pnl_pct_frac),
        ):
            log.info(f"  ⚡ PROFIT HUNT risk bypass: {risk_signal}")
            track_profit_hunt_event(
                self.cfg, risk_signal, ticker,
                {"reason": risk_signal, "price": current_px, "bypass": "council"},
                pnl_usd=(current_px - entry_px) * self.shares if entry_px else 0,
                pnl_pct=pnl_pct_frac, record_buffer=True, push_git=True,
            )
            self._exit_position(current_px, risk_signal)
            return True

        if not self.ai_commander or not is_ai_council_mode(self.cfg):
            log.info(f"  ⚡ RISK EXIT: {risk_signal}")
            self._exit_position(current_px, risk_signal)
            return True
        if self._has_ai_council(ticker, "risk_exit"):
            return True
        ppo_exit, ppo_conf, ppo_reason = False, 0.5, ""
        try:
            ppo_exit, ppo_conf, ppo_reason = self._ai_gate_exit(current_px)
        except Exception:
            pass
        ctx = {
            "ticker": ticker,
            "price": current_px,
            "pnl_pct": round(pnl_pct, 2),
            "risk_signal": risk_signal,
            "stop": self._position_stop,
            "target": self._position_target,
        }
        ai_dec = self.ai_commander.decide_risk_exit(
            ctx, risk_signal, ppo_exit, ppo_conf, ppo_reason,
        )
        if ai_dec.get("pending"):
            self._set_ai_council(ticker, "risk_exit", {
                "fingerprint": ai_dec["fingerprint"],
                "risk_signal": risk_signal,
                "ppo_exit": ppo_exit,
                "ppo_conf": ppo_conf,
                "ppo_reason": ppo_reason,
                "min_conf": float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55)),
                "ctx": ctx,
                "current_px": current_px,
            })
            log.info(
                f"  🧠 COUNCIL risk {ticker}: {risk_signal} | "
                f"{(ai_dec.get('reason') or 'deliberating')[:80]}"
            )
            return True
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL risk exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
                f"{ai_dec.get('pipeline', '')}"
            )
            self._exit_position(current_px, f"council_risk: {risk_signal}")
            return True
        return False

    def _service_pending_ai_councils(self):
        """Poll all Ollama+PPO councils without blocking the main loop."""
        if self.ai_commander:
            try:
                self.ai_commander.service_deferred_learning()
            except Exception as exc:
                log.debug(f"Deferred council learning: {exc}")
        if not self.ai_commander or not self._ai_councils:
            return
        for key in list(self._ai_councils.keys()):
            st = self._ai_councils.get(key)
            if not st:
                continue
            task = str(st.get("task", ""))
            ticker = str(st.get("ticker", ""))
            try:
                if task == "entry_decision":
                    self._resolve_entry_council(key, st)
                elif task == "stagnation_check":
                    self._resolve_stagnation_council(key, st)
                elif task == "position_manage":
                    self._resolve_position_council(key, st)
                elif task == "exit_decision":
                    self._resolve_exit_council(key, st)
                elif task == "risk_exit":
                    self._resolve_risk_exit_council(key, st)
            except Exception as exc:
                log.debug(f"Council poll {key}: {exc}")
        self._service_stale_councils()
        if self._ai_councils:
            now = time.time()
            if now - getattr(self, "_last_council_backlog_log", 0) >= 15.0:
                self._last_council_backlog_log = now
                pending = [
                    f"{st.get('ticker', '?')}/{st.get('task', '?')}"
                    for st in self._ai_councils.values()
                ]
                log.info(
                    f"  ⏳ Council backlog ({len(pending)}): {', '.join(pending[:6])}"
                    + (" …" if len(pending) > 6 else "")
                    + " — mechanical stops still active"
                )

    def _resolve_entry_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if ticker in self._contract_blacklist:
            self._ai_councils.pop(key, None)
            return
        if ticker in self._held_tickers():
            if self.ai_commander and deferred_learning_enabled(self.cfg):
                executed = {
                    "enter": True,
                    "pipeline": "ppo:executed_before_council",
                    "reason": "position already open",
                }
                self.ai_commander._deferred.schedule(
                    ticker=ticker,
                    task="entry_decision",
                    fingerprint=str(st.get("fingerprint", "")),
                    executed=executed,
                    ppo_signal=int(st.get("ppo_action", 0)),
                    ppo_conf=float(st.get("ppo_conf", 0.5)),
                    ppo_reason=str(st.get("ppo_reason", "")),
                    market_ctx=st.get("market_ctx") or {},
                )
            self._ai_councils.pop(key, None)
            return
        if self._open_position_count() >= self._max_concurrent():
            return
        if self._pending_entry_ticker and time.time() < self._pending_entry_until:
            return
        df_fast = self._scan_data_cache.get(ticker)
        min_bars = self._min_bars_for(ticker)
        if df_fast is None or len(df_fast) < min_bars:
            dm = self._target_monitors.get(ticker)
            if dm and should_spike_fast_entry(
                self.cfg,
                float(st.get("spike_ratio", 0) or 0),
                float(st.get("scan_score", 0) or 0),
            ):
                df_fast = coalesce_bars(
                    dm.get_fast_bar_dataframe(n=24) if dm else None,
                    dm.get_bar_dataframe() if dm else None,
                    min_len=3,
                )
            if df_fast is None or len(df_fast) < max(3, min_bars // 2):
                return
        current_px = self._live_price_for(ticker, float(df_fast["close"].iloc[-1]))
        st["current_px"] = current_px
        st["account"] = self._account_context_for_ai()
        micro_fc = st.get("micro_forecast") or self._last_micro_forecast.get(ticker, {})
        from core.entry_quality import assess_entry_quality
        st["account"]["entry_quality"] = assess_entry_quality(
            self.cfg, micro_fc,
            spike_ratio=float(st.get("spike_ratio", 1.0)),
            scan_score=float(st.get("scan_score", 0)),
            ppo_action=int(st.get("ppo_action", 0)),
            ppo_conf=float(st.get("ppo_conf", 0.5)),
            live_px=current_px,
        )
        st["micro_forecast"] = micro_fc
        ai_dec = self.ai_commander.poll_entry_council(st, df=df_fast)
        if ai_dec.get("pending"):
            return
        self._ai_councils.pop(key, None)
        pipeline = str(ai_dec.get("pipeline", ""))
        if "timeout" in pipeline:
            self._observe_runtime(
                "council_timeout",
                ticker=ticker,
                pipeline=pipeline,
                reason=(ai_dec.get("reason") or "")[:200],
                spike_ratio=float(st.get("spike_ratio", 0) or 0),
                scan_score=float(st.get("scan_score", 0) or 0),
                confidence=float(ai_dec.get("confidence", 0) or 0),
                market_state=get_market_state(self.cfg),
            )
        if not ai_dec.get("enter"):
            log.info(
                f"  🧠 COUNCIL skip {ticker}: {(ai_dec.get('reason') or '')[:80]} | {pipeline}"
            )
            if "timeout" in pipeline:
                self._spike_attempt_until[ticker] = 0.0
            return
        log.info(
            f"  🧠 COUNCIL enter {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
            f"conf={float(ai_dec.get('confidence', 0)):.0%} | {pipeline}"
        )
        self._last_ai_confidence = float(ai_dec.get("confidence", 0.5))
        self._submit_ai_entry(
            ticker, df_fast, ai_dec, st.get("market_ctx") or {}, current_px,
        )

    def _resolve_stagnation_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_stagnation_council(st)
        if ai_dec.get("pending"):
            self._last_stagnation_decision = ai_dec
            return
        self._ai_councils.pop(key, None)
        self._last_stagnation_decision = ai_dec
        pipeline = str(ai_dec.get("pipeline", ""))
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf * 0.9:
            log.info(
                f"  🧠 COUNCIL stagnation exit {ticker}: "
                f"{(ai_dec.get('reason') or '')[:80]} | {pipeline}"
            )
            self._exit_position(px, f"ai_stagnation: {ai_dec.get('reason', '')[:100]}")
            self._save_position_context(ticker)

    def _resolve_position_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        ctx = dict(st.get("ctx") or {})
        ctx["price"] = px
        ctx["pnl_usd"] = round((px - self._entry_price) * self.shares, 2)
        ctx["pnl_pct"] = round(((px / self._entry_price) - 1) * 100, 2) if self._entry_price else 0
        ctx["stop"] = self._position_stop
        ctx["target"] = self._position_target
        st["ctx"] = ctx
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_position_council(st, df=self._scan_data_cache.get(ticker))
        if ai_dec.get("pending"):
            max_wait = council_max_wait_sec(self.cfg)
            if time.time() - float(st.get("started_at", time.time())) > max_wait:
                self._ai_councils.pop(key, None)
            return
        self._ai_councils.pop(key, None)
        pipeline = str(ai_dec.get("pipeline", ""))
        log.info(
            f"  🧠 COUNCIL manage {ticker}: {ai_dec.get('action', 'HOLD')} | "
            f"{(ai_dec.get('reason') or '')[:80]} | {pipeline}"
        )
        self._apply_position_manage_decision(ai_dec, px)
        self._save_position_context(ticker)

    def _resolve_exit_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_exit_council(st)
        if ai_dec.get("pending"):
            max_wait = council_max_wait_sec(self.cfg)
            age = time.time() - float(st.get("started_at", time.time()))
            if age > max_wait:
                self._ai_councils.pop(key, None)
                ppo_reason = str(st.get("ppo_reason", "") or "")
                if "shape" in ppo_reason.lower() or "observation" in ppo_reason.lower():
                    log.warning(
                        f"  ⚠️ COUNCIL exit {ticker}: clearing stuck council (bad PPO obs)"
                    )
                return
            return
        self._ai_councils.pop(key, None)
        pipeline = str(ai_dec.get("pipeline", ""))
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | {pipeline}"
            )
            self._exit_position(px, f"council_exit: {ai_dec.get('reason', '')[:80]}")
            self._save_position_context(ticker)

    def _resolve_risk_exit_council(self, key: str, st: Dict[str, Any]):
        ticker = str(st["ticker"])
        if not self._load_position_context(ticker):
            self._ai_councils.pop(key, None)
            return
        px = self._live_price_for(ticker, float(st.get("current_px", self._entry_price)))
        st["current_px"] = px
        ai_dec = self.ai_commander.poll_risk_exit_council(st)
        if ai_dec.get("pending"):
            return
        self._ai_councils.pop(key, None)
        min_conf = float(getattr(self.cfg, "CONFIDENCE_THRESHOLD", 0.55))
        if ai_dec.get("exit") and float(ai_dec.get("confidence", 0)) >= min_conf:
            log.info(
                f"  🧠 COUNCIL risk exit {ticker}: {(ai_dec.get('reason') or '')[:80]} | "
                f"{ai_dec.get('pipeline', '')}"
            )
            self._exit_position(px, f"council_risk: {st.get('risk_signal', 'risk')}")
            self._save_position_context(ticker)

    def _apply_position_manage_decision(self, decision: Dict[str, Any], current_px: float):
        action = str(decision.get("action", "HOLD")).upper()
        reason = decision.get("reason", "")
        if action == "EXIT":
            log.info(f"  🧠 AI EXIT: {reason}")
            self._exit_position(current_px, f"ai_position: {reason}")
            return
        if action == "WIDEN_STOP":
            new_stop = decision.get("stop")
            if new_stop and float(new_stop) < self._position_stop - 0.0001:
                self._apply_stop_update(float(new_stop), f"AI widen (ATR): {reason}")
        elif action == "TIGHTEN_STOP":
            new_stop = decision.get("stop")
            if new_stop and float(new_stop) > self._position_stop + 0.0001:
                self._apply_stop_update(float(new_stop), f"AI tighten (ATR): {reason}")
        elif action == "RAISE_TP":
            new_target = decision.get("target")
            if new_target and float(new_target) > self._position_target + 0.0001:
                self._apply_target_update(float(new_target), f"AI raise TP (ATR): {reason}")

    def _compute_mechanical_trail(
        self, current_px: float,
    ) -> Tuple[Optional[float], Optional[float]]:
        """PPO-side mechanical trail stop / TP extension for council input."""
        if self.shares <= 0 or self._entry_price <= 0:
            return None, None
        entry = self._entry_price
        pnl_pct = (current_px / entry) - 1
        peak_pct = (self._position_peak / entry) - 1 if self._position_peak > entry else pnl_pct
        if peak_pct <= 0 and pnl_pct > 0:
            peak_pct = pnl_pct
        if peak_pct <= 0:
            return None, None
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
        mech_stop = trail_stop if trail_stop > self._position_stop + 0.0001 else None
        mech_target = None
        if current_px >= self._position_target * 0.98:
            extension = (current_px - entry) * 0.35
            new_tp = round(current_px + extension, 4)
            if new_tp > self._position_target + 0.0001:
                mech_target = new_tp
        return mech_stop, mech_target

    def _reanchor_bracket_to_limit(
        self,
        plan: TradePlan,
        ai_dec: Dict[str, Any],
        limit_px: Optional[float],
        df_fast: pd.DataFrame,
        shares: int,
        current_px: float,
    ) -> Tuple[TradePlan, Dict[str, Any]]:
        """Recompute stop/TP from limit entry — avoids inverted brackets on wide spreads."""
        if limit_px is None or float(limit_px) <= 0:
            return plan, ai_dec
        anchor = float(limit_px)
        if abs(anchor - current_px) / max(current_px, 1e-9) < 0.001:
            return plan, ai_dec
        from core.bracket_validator import compute_atr_bracket
        from core.pilot_mode import get_ai_deploy_budget

        atr = float(plan.atr_at_entry or compute_atr(df_fast, period=5))
        deploy = get_ai_deploy_budget(
            self.cfg,
            self.pilot,
            float(self.account_equity),
            self._deployable_cash(),
            int(self._open_position_count()),
        )
        reb = compute_atr_bracket(
            self.cfg,
            anchor,
            atr,
            equity=float(self.account_equity),
            cash=self._deployable_cash(),
            deploy_cap=deploy,
            shares_hint=shares,
            is_penny=anchor < float(getattr(self.cfg, "PENNY_STOCK_THRESHOLD", 5.0)),
            avg_vol=float(df_fast["volume"].tail(20).mean()) if len(df_fast) else 0.0,
        )
        if not reb.ok:
            return plan, ai_dec
        ai_dec = dict(ai_dec)
        ai_dec["stop"] = reb.stop
        ai_dec["target"] = reb.target
        ai_dec["shares"] = reb.shares
        ai_dec["risk_usd"] = reb.risk_usd
        plan = TradePlan(
            side="LONG",
            entry_price=anchor,
            shares=float(reb.shares),
            initial_stop_price=reb.stop,
            take_profit_price=reb.target,
            risk_usd=reb.risk_usd,
            atr_at_entry=atr,
        )
        log.debug(
            f"  📐 Bracket re-anchored to limit ${anchor:.4f}: "
            f"stop ${reb.stop:.4f} target ${reb.target:.4f}"
        )
        return plan, ai_dec

    def _apply_war_sizing(
        self,
        ticker: str,
        decision: Dict[str, Any],
        entry_px: float,
    ) -> Dict[str, Any]:
        try:
            from core.war_account import rescale_decision_for_war, war_account_enabled
            if war_account_enabled(self.cfg):
                return rescale_decision_for_war(
                    self.cfg, decision, entry_px, ticker=ticker,
                )
        except Exception as exc:
            log.debug(f"War sizing: {exc}")
        return decision

    def _apply_lottery_bank_sizing(
        self,
        ticker: str,
        decision: Dict[str, Any],
        entry_px: float,
        df_fast: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """Cap lottery setups to virtual $1k bank; main paper account unchanged."""
        try:
            from core.lottery_bank import (
                assess_lottery_setup,
                lottery_bank_enabled,
                rescale_entry_for_lottery_bank,
            )
            if not lottery_bank_enabled(self.cfg):
                return decision
            forecast = dict(self._last_micro_forecast.get(ticker, {}))
            assess = assess_lottery_setup(
                self.cfg,
                scan_score=float(getattr(self, "_last_scan_score", 0)),
                spike_ratio=float(getattr(self, "_last_spike_ratio", 0)),
                forecast=forecast,
            )
            if not assess.eligible:
                return decision
            out = rescale_entry_for_lottery_bank(self.cfg, decision, entry_px, assess)
            self._pending_lottery_meta[ticker.upper()] = out
            return out
        except Exception as exc:
            log.debug(f"Lottery bank sizing: {exc}")
            return decision

    def _submit_ai_entry(
        self,
        ticker: str,
        df_fast: pd.DataFrame,
        ai_dec: Dict[str, Any],
        market_ctx: Dict[str, Any],
        current_px: float,
    ) -> str:
        """Place bracket entry from an AI/council decision (non-blocking poll path)."""
        bid = market_ctx.get("bid")
        ask = market_ctx.get("ask")
        avg_volume = float(market_ctx.get("avg_volume", 0))
        current_px = self._live_price_for(ticker, current_px)
        gate_dec = dict(ai_dec)
        gate_dec["ticker"] = ticker
        gate_dec["entry"] = current_px
        ok, gate_dec, err = validate_decision_bracket(
            self.cfg, gate_dec, fallback_entry=current_px,
        )
        if not ok:
            if learn_dont_block(self.cfg):
                from core.bracket_validator import compute_atr_bracket
                from core.pilot_mode import get_ai_deploy_budget

                atr = compute_atr(df_fast, period=5)
                deploy = get_ai_deploy_budget(
                    self.cfg,
                    self.pilot,
                    float(self.account_equity),
                    self._deployable_cash(),
                    int(self._open_position_count()),
                )
                reb = compute_atr_bracket(
                    self.cfg,
                    current_px,
                    atr,
                    equity=float(self.account_equity),
                    cash=self._deployable_cash(),
                    deploy_cap=deploy,
                    shares_hint=int(ai_dec.get("shares", 0) or 0),
                )
                if reb.ok:
                    gate_dec = {
                        **ai_dec,
                        "entry": current_px,
                        "stop": reb.stop,
                        "target": reb.target,
                        "shares": reb.shares,
                        "risk_usd": reb.risk_usd,
                        "reward_risk": reb.reward_risk,
                    }
                    ok, gate_dec, err = validate_decision_bracket(
                        self.cfg, gate_dec, fallback_entry=current_px,
                    )
                    if ok:
                        log.info(f"  🔧 BRACKET REPAIRED {ticker} — ATR math (learn mode)")
        if not ok:
            log.warning(f"  🛑 BRACKET REJECTED {ticker}: {err}")
            spike = float(getattr(self, "_last_spike_ratio", 1.0))
            snap = (
                self.ai_commander.ollama_audit_snapshot(ticker)
                if self.ai_commander else {}
            )
            log_bracket_reject(
                self.cfg, ticker=ticker, reason=err,
                entry=current_px,
                stop=float(gate_dec.get("stop", ai_dec.get("stop", 0))),
                target=float(gate_dec.get("target", ai_dec.get("target", 0))),
                shares=int(gate_dec.get("shares", ai_dec.get("shares", 0))),
                council_decision=ai_dec,
                ollama_raw=snap.get("raw", ""),
                ollama_parsed=snap.get("parsed"),
                spike_ratio=spike,
                pipeline="pre_broker_gate",
            )
            try:
                buffer_append({
                    "source": "bracket_reject",
                    "ticker": ticker,
                    "action": "REJECT",
                    "reward": reward_from_bracket_reject(
                        self.cfg, spike_ratio=spike,
                        inverted="INVERTED" in err.upper(),
                    ),
                    "reason": err[:200],
                    "spike_ratio": spike,
                    "ollama_had_prices": bool(snap.get("parsed", {}).get("stop")),
                })
            except Exception:
                pass
            self._observe_runtime(
                "bracket_reject",
                ticker=ticker,
                reason=err[:200],
                spike_ratio=spike,
                market_state=get_market_state(self.cfg),
            )
            self._clear_pending_entry(ticker, cooldown_sec=30.0)
            return "waiting"
        ai_dec = self._apply_war_sizing(ticker, gate_dec, current_px)
        ai_dec = self._apply_lottery_bank_sizing(ticker, ai_dec, current_px, df_fast)
        shares = int(ai_dec["shares"])
        shares = self._liquidity_cap_shares(shares, current_px, df_fast)
        shares = self._clamp_entry_shares(shares, current_px)
        if shares < 1:
            return "waiting"
        spread_pct = (ask - bid) / current_px if bid and ask and current_px > 0 else 0.0
        max_spread = float(getattr(self.cfg, "MAX_ENTRY_SPREAD_PCT", 0.05))
        if spread_pct > max_spread and not learn_dont_block(self.cfg):
            log.info(f"  ⏭ Skip {ticker}: spread {spread_pct:.1%} > {max_spread:.0%} (IB 2161 risk)")
            self._clear_pending_entry(ticker, cooldown_sec=60.0)
            return "waiting"
        now = time.time()
        fail_cd = float(getattr(self.cfg, "ENTRY_FAILURE_COOLDOWN_SEC", 30.0))
        fill_wait = entry_fill_poll_sec(self.cfg)
        max_wait = float(getattr(self.cfg, "ENTRY_FILL_MAX_WAIT_SEC", 30.0))
        fill_polls = max(5, int(max_wait / fill_wait))
        n_cancelled = self.broker.cancel_open_orders_for_symbol(ticker)
        if n_cancelled:
            log.info(f"  🧹 Cleared {n_cancelled} stale {ticker} order(s) before entry")
        self._pending_entry_ticker = ticker
        block_sec = entry_pending_block_sec(self.cfg)
        if ai_fast_execution(self.cfg):
            block_sec = min(block_sec, 20.0)
        self._pending_entry_until = now + block_sec
        regime_result = (
            self.regime_detector.classify(df_fast)
            if hasattr(self.regime_detector, "classify") else None
        )
        vix_level = 0.0
        try:
            ctx = summarize_market_context()
            vix_level = float(ctx.get("vix_level", 0.0))
        except Exception:
            pass
        self.pilot.start_flight(ticker, current_px, regime_result, 0.5, vix_level=vix_level)
        spike = float(getattr(self, "_last_spike_ratio", 1.0))
        vol_ratio = float(market_ctx.get("recent_volume", 0)) / (avg_volume + 1e-9)
        regime_label = regime_tag(regime_result, spike_ratio=spike, vol_ratio=vol_ratio)
        self._last_entry_regime = regime_label
        snap = (
            self.ai_commander.ollama_audit_snapshot(ticker)
            if self.ai_commander else {}
        )
        plan = TradePlan(
            side="LONG", entry_price=current_px, shares=float(shares),
            initial_stop_price=float(ai_dec["stop"]),
            take_profit_price=float(ai_dec["target"]),
            risk_usd=float(ai_dec.get("risk_usd", 50.0)),
            atr_at_entry=compute_atr(df_fast, period=5),
        )
        entry_parent_px, entry_mode = self._entry_price_mode(
            current_px, bid, ask, shares, avg_volume,
        )
        plan, ai_dec = self._reanchor_bracket_to_limit(
            plan, ai_dec, entry_parent_px, df_fast, shares, current_px,
        )
        if self.shadow_circuit.block_broker():
            log.warning(
                f"  🌑 SHADOW — simulating {ticker} entry (NO IB order — no mobile notification)"
            )
            self.shadow_circuit.open_shadow_trade(
                ticker, current_px, plan.initial_stop_price,
                plan.take_profit_price, shares, regime=regime_label,
            )
            log_entry_execution(
                ticker=ticker, limit_px=entry_parent_px, fill_px=current_px,
                entry_mode=entry_mode, shares=shares,
                stop=plan.initial_stop_price, target=plan.take_profit_price,
                regime=regime_label, spike_ratio=spike,
                council_decision=ai_dec,
                ollama_raw=snap.get("raw", ""),
                ollama_parsed=snap.get("parsed"),
                shadow=True,
            )
            self._last_entry_telemetry = {
                "limit_px": entry_parent_px, "slippage_pct": 0.0, "shadow": True,
            }
            self._clear_pending_entry(ticker, cooldown_sec=15.0)
            return "shadow"
        min_fill_ratio = float(getattr(self.cfg, "MIN_ENTRY_FILL_RATIO", 0.85))
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
                plan, ai_dec = self._reanchor_bracket_to_limit(
                    plan, ai_dec, entry_parent_px, df_fast, shares, current_px,
                )
                log.info(f"  🔄 IB2161 retry: {shares} sh limit @ ${entry_parent_px:.4f}")
            else:
                entry_parent_px, entry_mode = self._entry_price_mode(
                    current_px, bid, ask, shares, avg_volume,
                )
                plan, ai_dec = self._reanchor_bracket_to_limit(
                    plan, ai_dec, entry_parent_px, df_fast, shares, current_px,
                )
            self._last_entry_telemetry = {
                "limit_px": entry_parent_px,
                "entry_mode": entry_mode,
                "council": ai_dec,
                "ollama_raw": snap.get("raw", ""),
                "regime": regime_label,
                "atr": float(plan.atr_at_entry or 0),
            }
            bracket = self.broker.place_bracket_buy(
                quantity=shares, limit_or_market_price=entry_parent_px,
                stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                symbol=ticker,
            )
            self._pending_brackets_by_ticker[ticker] = bracket
            try:
                from core.smart_stack import count_hourly_filled_entry
                count_on_submit = not count_hourly_filled_entry(self.cfg)
            except Exception:
                count_on_submit = True
            if capital_discipline_enabled(self.cfg) and count_on_submit:
                self._entries_this_hour = getattr(self, "_entries_this_hour", 0) + 1
            if not self._position_slots:
                self.bracket_handle = bracket
            mode_label = "MARKET" if entry_parent_px is None else f"LIMIT@${entry_parent_px:.4f}"
            log.info(f"  📥 Entry mode: {entry_mode} ({mode_label}) | {shares} sh @ ~${current_px:.4f}")
            if getattr(self.cfg, "PARALLEL_ENTRY_EXIT", True):
                self._entry_poll_states[ticker] = {
                    "ticker": ticker,
                    "shares": shares,
                    "plan": plan,
                    "fill_px": current_px,
                    "limit_px": entry_parent_px,
                    "polls": 0,
                    "max_polls": fill_polls,
                    "min_fill_ratio": min_fill_ratio,
                    "fail_cd": fail_cd,
                    "attempt": attempt,
                    "last_ib_error": last_ib_error,
                    "bracket": bracket,
                    "started_at": time.time(),
                    "last_heartbeat": 0.0,
                }
                log.info(
                    f"  ⏳ Awaiting IB fill {ticker}: {shares} sh "
                    f"parent#{bracket.parent_order_id} ({mode_label})"
                )
                return "waiting"
        return "waiting"

    def _attempt_entry(self) -> str:
        """
        Attempt entry on self.top_pick.
        Returns: 'entered', 'permanent_skip', or 'waiting'
        """
        can_trade, market_state = can_trade_now(self.cfg)
        if not can_trade:
            return "waiting"

        if not self.top_pick:
            return 'waiting'
        ticker = self.top_pick.ticker

        if ticker in self._contract_blacklist:
            return "waiting"

        if self._has_ai_council(ticker, "entry_decision"):
            return "waiting"

        if ticker in self._held_tickers():
            return 'waiting'
        now = time.time()
        if ticker in self._entry_poll_states:
            return "waiting"
        if self._pending_entry_ticker == ticker and now < self._pending_entry_until:
            return "waiting"
        if now < self._entry_cooldown_until.get(ticker, 0):
            return 'waiting'

        try:
            from core.live_trade_guard import check_ticker_cooldown
            cd_block = check_ticker_cooldown(ticker)
            if cd_block:
                if now - getattr(self, "_last_quality_watch_log", 0) >= 45.0:
                    self._last_quality_watch_log = now
                    log.info(f"  👁 {cd_block}")
                return "waiting"
        except Exception:
            pass

        if self.risk.is_halted():
            return 'waiting'

        if self._open_position_count() >= self._max_concurrent():
            return 'waiting'

        if now - getattr(self, "_hour_window_start", 0) >= 3600:
            self._hour_window_start = now
            self._entries_this_hour = 0
        rate_ok, rate_msg = check_entry_rate_limit(
            getattr(self, "_entries_this_hour", 0),
            getattr(self, "_hour_window_start", now),
            self.cfg,
        )
        if not rate_ok:
            if now - getattr(self, "_last_quality_watch_log", 0) >= float(
                getattr(self.cfg, "QUALITY_WATCH_HEARTBEAT_SEC", 45)
            ):
                self._last_quality_watch_log = now
                log.info(f"  👁 {rate_msg}")
            return "waiting"
        
        try:
            self.cfg.TICKER = ticker
            min_bars = self._min_bars_for(ticker)

            scan_score = self.top_pick.rank_score if self.top_pick else 0.0
            df_fast, current_px, dm, forecast = self._resolve_live_bars(ticker, min_bars=min_bars)
            tick_burst_ratio = 0.0
            if df_fast is None or len(df_fast) < min_bars:
                if dm:
                    burst, burst_ratio = self._detect_tick_volume_burst(
                        dm, df_fast if df_fast is not None else pd.DataFrame(),
                    )
                    from core.capital_discipline import is_strong_spike_setup
                    tick_ok = burst and (
                        should_spike_fast_entry(self.cfg, burst_ratio, scan_score)
                        or is_strong_spike_setup(self.cfg, scan_score, burst_ratio)
                    )
                    if tick_ok:
                        tick_burst_ratio = burst_ratio
                        df_fast = dm.get_fast_bar_dataframe(n=24)
                        current_px = float(dm.get_latest_price() or 0)
                        if df_fast is None or len(df_fast) < 3 or current_px <= 0:
                            return 'waiting'
                        min_bars = min(min_bars, 3)
                    else:
                        return 'waiting'
                else:
                    return 'waiting'
            if not forecast:
                forecast = dict(self._last_micro_forecast.get(ticker, {}))
            avg_volume = float(df_fast["volume"].tail(20).mean())
            bid, ask = self._get_bid_ask(ticker)
            spread_pct = (ask - bid) / current_px if bid and ask and current_px > 0 else 0.0
            market_ctx = {
                "bid": bid, "ask": ask, "spread_pct": spread_pct,
                "avg_volume": avg_volume,
                "recent_volume": float(df_fast["volume"].iloc[-1]),
            }

            is_spike, spike_ratio = self._detect_volume_spike(df_fast)
            vol_ratio = float(df_fast["volume"].tail(3).mean()) / (
                float(df_fast["volume"].tail(20).mean()) + 1e-9
            )
            if not is_spike and vol_ratio >= 1.15:
                is_spike, spike_ratio = True, vol_ratio
            if dm:
                burst, burst_ratio = self._detect_tick_volume_burst(dm, df_fast)
                if burst:
                    is_spike, spike_ratio = True, max(spike_ratio, burst_ratio)
            elif tick_burst_ratio > 0:
                is_spike, spike_ratio = True, max(spike_ratio, tick_burst_ratio)
            is_spike, spike_ratio = apply_micro_spike_boost(
                is_spike, spike_ratio, forecast, cfg=self.cfg, scan_score=scan_score,
                live_px=float(current_px or 0),
            )

            try:
                from core.commander_replay import shadow_would_skip_entry
                from core.slow_coach import coach_lane_enabled, log_shadow_skip
                if coach_lane_enabled(self.cfg):
                    prob = float(forecast.get("profit_probability", 0) or 0)
                    fade = float(
                        forecast.get("fakeout_risk", 0)
                        or forecast.get("fade_risk", 0)
                        or 0
                    )
                    would_skip, shadow_reason = shadow_would_skip_entry(
                        self.cfg,
                        ticker=ticker,
                        scan_score=scan_score,
                        spike_ratio=spike_ratio,
                        profit_probability=prob,
                        fakeout_risk=fade,
                    )
                    if would_skip:
                        log_shadow_skip(
                            self.cfg,
                            ticker=ticker,
                            reason=shadow_reason,
                            scan_score=scan_score,
                            spike_ratio=spike_ratio,
                        )
            except Exception:
                pass

            from core.smart_stack import evaluate_pre_entry_advisories
            gate_ok, gate_msg, gate_adv = evaluate_pre_entry_advisories(
                self.cfg,
                scan_score=scan_score,
                spike_ratio=spike_ratio,
                forecast=forecast,
                live_px=float(current_px or 0),
            )
            if gate_adv:
                self._smart_gate_context[ticker.upper()] = {
                    **self._smart_gate_context.get(ticker.upper(), {}),
                    **gate_adv,
                }
            if not gate_ok:
                cd = entry_cooldown_after_skip(self.cfg)
                self._spike_skip_until[ticker] = now + cd
                if gate_msg and now - getattr(self, "_last_quality_watch_log", 0) >= float(
                    getattr(self.cfg, "QUALITY_WATCH_HEARTBEAT_SEC", 45)
                ):
                    self._last_quality_watch_log = now
                    log.info(f"  👁 WATCH {ticker}: {gate_msg}")
                return "waiting"

            if not is_ai_unlimited(self.cfg) or capital_discipline_enabled(self.cfg):
                from core.capital_discipline import is_strong_spike_setup
                from core.sniper_execution import sniper_vol_flash
                uptrend_ok = _only_uptrend(df_fast, current_px, min_bars=min_bars)
                if not uptrend_ok and not (
                    should_spike_fast_entry(self.cfg, spike_ratio, scan_score)
                    or is_strong_spike_setup(self.cfg, scan_score, spike_ratio)
                    or sniper_vol_flash(self.cfg, scan_score, spike_ratio)
                ):
                    log.debug(f"Entry skip {ticker}: not uptrend")
                    return 'waiting'

            if (
                forecast.get("dir", 0) < 0
                and forecast.get("spike_likelihood", 0) < 0.55
                and not forecast.get("breakout")
            ):
                log.debug(f"Entry skip {ticker}: micro bearish forecast")
                return 'waiting'
            if not is_spike and (not is_ai_unlimited(self.cfg) or capital_discipline_enabled(self.cfg)):
                log.debug(f"Entry skip {ticker}: no volume spike (ratio={spike_ratio:.2f})")
                return 'waiting'
            
            self._ai_update_buffers(df_fast, current_px)
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
                    account={
                        **self._account_context_for_ai(),
                        "micro_forecast": forecast,
                    },
                    obs=obs, bar_df=bar_df, pilot=self.pilot, market_ctx=market_ctx,
                )
                if ai_dec.get("pending"):
                    ppo_a = int(ai_dec.get("ppo_action", 0))
                    ppo_c = float(ai_dec.get("ppo_conf", 0.5))
                    min_c = float(ai_dec.get("min_conf", 0.55))
                    ppo_lead = (
                        allows_ppo_lead_while_pending(
                            self.cfg,
                            scan_score=scan_score,
                            spike_ratio=spike_ratio,
                        )
                        and ai_fast_execution(self.cfg)
                        and int(getattr(self.risk, "_consecutive_losses", 0) or 0)
                        < int(os.getenv("LOSS_STREAK_BLOCK_BYPASS_AT", "2"))
                        and (
                            (ppo_a == 1 and ppo_c >= min_c * 0.72)
                            or (
                                ppo_a == 1
                                and should_spike_fast_entry(
                                    self.cfg, spike_ratio, scan_score, ppo_a, ppo_c,
                                )
                            )
                        )
                    )
                    if ppo_lead:
                        lead = self.ai_commander.execute_ppo_led_entry_while_pending(
                            ticker, df_fast, current_px, spike_ratio, scan_score,
                            account={
                                **self._account_context_for_ai(),
                                "micro_forecast": forecast,
                            },
                            ppo_action=ppo_a, ppo_conf=ppo_c,
                            ppo_reason=str(ai_dec.get("ppo_reason", "")),
                            min_conf=min_c, pilot=self.pilot, market_ctx=market_ctx,
                            fingerprint=str(ai_dec.get("fingerprint", "")),
                            micro=forecast,
                        )
                        if lead.get("enter"):
                            log.info(
                                f"  ⚡ PPO ENTER {ticker} (council still thinking — logging async)"
                            )
                            self._last_ai_confidence = float(lead.get("confidence", 0.5))
                            return self._submit_ai_entry(
                                ticker, df_fast, lead, market_ctx, current_px,
                            )
                    self._set_ai_council(ticker, "entry_decision", {
                        "fingerprint": ai_dec["fingerprint"],
                        "ppo_action": ai_dec["ppo_action"],
                        "ppo_conf": ai_dec["ppo_conf"],
                        "ppo_reason": ai_dec["ppo_reason"],
                        "min_conf": ai_dec["min_conf"],
                        "spike_ratio": spike_ratio,
                        "scan_score": scan_score,
                        "market_ctx": market_ctx,
                        "micro_forecast": forecast,
                        "pilot": self.pilot,
                        "started_at": now,
                    })
                    log.info(
                        f"  🧠 COUNCIL {ticker}: {(ai_dec.get('reason') or 'deliberating')[:100]} | "
                        f"{ai_dec.get('pipeline', '')}"
                    )
                    return "waiting"
                if not ai_dec.get("enter"):
                    reason = (ai_dec.get("reason") or "")[:80]
                    pipeline = ai_dec.get("pipeline", "")
                    log.info(
                        f"  🧠 AI skip {ticker}: {reason}"
                        + (f" | {pipeline}" if pipeline else "")
                    )
                    if not is_ai_unlimited(self.cfg) or capital_discipline_enabled(self.cfg):
                        from core.smart_stack import smart_stack_enabled
                        if (
                            not smart_stack_enabled(self.cfg)
                            and pipeline == "sniper:ppo_hold_skip"
                        ):
                            from core.sniper_execution import sniper_ppo_hold_skip_sec
                            cd = sniper_ppo_hold_skip_sec(self.cfg)
                        else:
                            cd = entry_cooldown_after_skip(self.cfg)
                        self._spike_skip_until[ticker] = time.time() + cd
                    return "waiting"
                pipeline = str(ai_dec.get("pipeline", ""))
                if "timeout" in pipeline:
                    self._observe_runtime(
                        "council_timeout",
                        ticker=ticker,
                        pipeline=pipeline,
                        reason=(ai_dec.get("reason") or "")[:200],
                        spike_ratio=spike_ratio,
                        scan_score=scan_score,
                        confidence=float(ai_dec.get("confidence", 0) or 0),
                        market_state=get_market_state(self.cfg),
                    )
                self._last_ai_confidence = float(ai_dec.get("confidence", 0.5))
                return self._submit_ai_entry(ticker, df_fast, ai_dec, market_ctx, current_px)
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
                stop_usd = get_trade_risk_usd(self.cfg, self.account_equity)
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

            ai_dec = self._apply_war_sizing(ticker, ai_dec, current_px)
            ai_dec = self._apply_lottery_bank_sizing(ticker, ai_dec, current_px, df_fast)

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
            fill_wait = entry_fill_poll_sec(self.cfg)
            max_wait = float(getattr(self.cfg, "ENTRY_FILL_MAX_WAIT_SEC", 30.0))
            fill_polls = max(5, int(max_wait / fill_wait))

            # One bracket per symbol — cancel any resting orders before submit
            n_cancelled = self.broker.cancel_open_orders_for_symbol(ticker)
            if n_cancelled:
                log.info(f"  🧹 Cleared {n_cancelled} stale {ticker} order(s) before entry")
            self._pending_entry_ticker = ticker
            block_sec = entry_pending_block_sec(self.cfg)
            if ai_fast_execution(self.cfg):
                block_sec = min(block_sec, 20.0)
            self._pending_entry_until = now + block_sec

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

                bracket = self.broker.place_bracket_buy(
                    quantity=shares, limit_or_market_price=entry_parent_px,
                    stop_price=plan.initial_stop_price, target_price=plan.take_profit_price,
                    symbol=ticker,
                )
                self._pending_brackets_by_ticker[ticker] = bracket
                if not self._position_slots:
                    self.bracket_handle = bracket
                mode_label = "MARKET" if entry_parent_px is None else f"LIMIT@${entry_parent_px:.4f}"
                log.info(f"  📥 Entry mode: {entry_mode} ({mode_label}) | {shares} sh @ ~${current_px:.4f}")

                if getattr(self.cfg, "PARALLEL_ENTRY_EXIT", True):
                    self._entry_poll_states[ticker] = {
                        "ticker": ticker,
                        "shares": shares,
                        "plan": plan,
                        "fill_px": current_px,
                        "limit_px": entry_parent_px,
                        "polls": 0,
                        "max_polls": fill_polls,
                        "min_fill_ratio": min_fill_ratio,
                        "fail_cd": fail_cd,
                        "attempt": attempt,
                        "last_ib_error": last_ib_error,
                        "bracket": bracket,
                        "started_at": time.time(),
                        "last_heartbeat": 0.0,
                    }
                    log.info(
                        f"  ⏳ Awaiting IB fill {ticker}: {shares} sh "
                        f"parent#{bracket.parent_order_id} ({mode_label})"
                    )
                    return "waiting"

                filled_shares = 0.0
                parent_trade = getattr(bracket, "parent_trade", None)
                parent_id = bracket.parent_order_id
                cancelled = False
                for _ in range(fill_polls):
                    self.ib.sleep(fill_wait)
                    parent_trade = getattr(bracket, "parent_trade", None)
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
                        block_reason = parse_ib_order_block(ierr)
                        if block_reason:
                            return self._ai_skip_ticker_permanent(ticker, block_reason)
                        if (
                            attempt == 0
                            and getattr(self.cfg, "ENTRY_RETRY_ON_IB2161", True)
                            and (ierr or {}).get("code") == 2161
                        ):
                            self.broker.cancel_open_orders_for_symbol(ticker)
                            break
                        log.warning(f"Entry order rejected by IB ({parent_status}) — not opening position")
                        self._pending_brackets_by_ticker.pop(ticker, None)
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
                    self._pending_brackets_by_ticker.pop(ticker, None)
                    continue
                break

            if filled_shares < shares * min_fill_ratio:
                parent_status = (
                    parent_trade.orderStatus.status
                    if parent_trade and parent_trade.orderStatus else "Unknown"
                )
                block_reason = parse_ib_order_block(last_ib_error)
                if block_reason:
                    return self._ai_skip_ticker_permanent(ticker, block_reason)
                if filled_shares >= 1:
                    log.warning(
                        f"Partial fill {int(filled_shares)}/{shares} below "
                        f"{min_fill_ratio:.0%} — flattening and skipping entry"
                    )
                    self.broker.flatten_position(
                        int(filled_shares), handle=bracket,
                        urgent=True, symbol=ticker,
                    )
                    self.ib.sleep(0.5)
                elif parent_status in ("Submitted", "PreSubmitted", "PendingSubmit"):
                    log.info(f"Entry order pending for {ticker} ({parent_status}) — waiting for IB fill")
                else:
                    log.info(f"Entry not filled for {ticker} (status={parent_status})")
                self.broker.cancel_open_orders_for_symbol(ticker)
                self._pending_brackets_by_ticker.pop(ticker, None)
                self._clear_pending_entry(ticker, cooldown_sec=fail_cd)
                return 'waiting'

            shares = int(filled_shares)
            return self._open_position_from_fill(ticker, shares, fill_px, plan)
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

            # Technical momentum override — disabled when council owns decisions
            if not should_enter and action != 2 and not is_ai_council_mode(self.cfg):
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
            
            # Full IB yesterday bundle → Ollama analyze + PPO (beat yesterday goal)
            if getattr(self.cfg, "DAILY_IB_LEARNING_ENABLED", True):
                try:
                    from core.daily_ib_learning import run_daily_ib_learning_cycle
                    from core.market_hours import learning_day_for_trigger
                    ib_day = learning_day_for_trigger("off_hours")
                    run_daily_ib_learning_cycle(
                        self.cfg, self,
                        connector=self.conn,
                        trigger="off_hours",
                        day_str=ib_day,
                        train_ppo=True,
                    )
                except Exception as exc:
                    log.debug(f"Off-hours IB learning: {exc}")
            
            # Update market regime from broader context (lightweight, stays in-process)
            self._update_market_context()
            
            # Train weights on historical data (lightweight, stays in-process)
            self._daily_self_train()
            
            # Launch heavy training (Transformer + PPO + LSTM) in isolated subprocess
            if getattr(self.cfg, "OFF_HOURS_HEAVY_TRAINING", True):
                try:
                    from core.memory_guard import is_low_ram_machine
                    light = is_low_ram_machine()
                    timesteps = "40000" if light else "100000"
                    session_id = launch_training([
                        sys.executable, "-m", "core.advanced_training",
                        "--mode", "full",
                        "--ticker", self.cfg.TICKER,
                        "--ppo-timesteps", timesteps,
                        "--epochs", "12" if timesteps == "40000" else "20",
                        "--save-model", "models/transformer_model.pth",
                    ], timeout_minutes=30)

                    if session_id:
                        log.info(f"🏋️ Training subprocess launched: {session_id}")
                        self.notifier.info(f"🏋️ OFF-HOURS TRAINING\nIsolated subprocess launched.\nSession: {session_id}")
                    else:
                        log.warning("Training subprocess failed to launch")
                except Exception as exc:
                    log.debug(f"Subprocess training launch failed: {exc}")
            else:
                log.info("🏋️ Off-hours heavy training skipped (OFF_HOURS_HEAVY_TRAINING=false — 8GB mode)")
            
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

            # Commander chat + session → guardrailed mutations & lessons
            try:
                if getattr(self.cfg, "COMMANDER_LEARNING_ENABLED", True) and self.ai_commander:
                    cl = run_commander_learning_cycle(
                        self.cfg,
                        self,
                        think_fn=self.ai_commander.compose_telegram,
                        trigger="off_hours_review",
                        apply=True,
                    )
                    if cl.get("applied", {}).get("applied"):
                        from core.commander_learning import format_apply_report
                        self.notifier.info(format_apply_report(cl)[:1200])
            except Exception as exc:
                log.debug(f"Commander learning cycle: {exc}")
            
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
        log.debug(f"Weights file changed — hot-reloading from disk")
        try:
            weights = self._load_weights()
            log.debug(f"Hot-reload complete | {len(weights.get('win_history', []))} trade samples")
        except Exception as exc:
            log.debug(f"Weights hot-reload failed: {exc}")
    
    def _load_weights(self) -> Dict:
        try:
            with open(self._weights_file, "r") as f:
                return json.load(f)
        except Exception:
            return {"momentum": 2.0, "volume": 15.0, "institutional": 20.0, "vwap_slope": 5.0, "atr_bonus": 5.0, "mean_reversion": 5.0, "win_history": []}
    
    def _save_weights(self, weights: Dict):
        os.makedirs("models", exist_ok=True)
        if self._weights_watcher:
            self._weights_watcher.suppress_for(20.0)
        AtomicFileWriter.write_json(self._weights_file, weights)
        log.debug(f"Learned weights saved -> {self._weights_file}")
    
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
        except Exception as exc:
            log.debug(f"Self-train skipped: {exc}")
    
    def _update_market_context(self):
        """Refresh Yahoo macro cache and update regime detector."""
        try:
            from core.market_context import refresh_macro_context
            ctx = refresh_macro_context(force=True)
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
                try:
                    from core.daily_self_evaluation import schedule_daily_self_evaluation
                    schedule_daily_self_evaluation(
                        self.cfg,
                        self,
                        notifier=self.notifier,
                        ai_commander=self.ai_commander,
                        autopilot=self.autopilot,
                        consciousness=self.consciousness,
                        pilot=self.pilot,
                        connector=self.conn,
                    )
                except Exception as exc:
                    log.debug(f"Daily self-eval schedule: {exc}")
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

        guard = getattr(self, "_learning_guard", None)
        if guard is not None:
            try:
                guard.stop(trigger="session_shutdown")
            except Exception as exc:
                log.debug(f"Learning guard stop: {exc}")
            self._learning_guard = None

        try:
            from core.graceful_shutdown import flush_halim_data
            flush_halim_data(self.cfg, trigger="session_shutdown")
        except Exception as exc:
            log.debug(f"Halim shutdown flush: {exc}")

        if os.getenv("REPLAY_LIVE", "").lower() not in ("1", "true", "yes"):
            log.info("🛑 Live shutdown — flushing Halim + evolution + git…")
            try:
                from core.graceful_shutdown import flush_owned_brain
                flush_owned_brain(
                    self.cfg,
                    model=getattr(self, "model", None),
                    trigger="live_session_end",
                    push_git=False,
                )
            except Exception as exc:
                log.debug(f"Owned brain evolution: {exc}")

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

        if os.getenv("REPLAY_LIVE", "").lower() not in ("1", "true", "yes"):
            try:
                from core.graceful_shutdown import flush_git_sync
                git_r = flush_git_sync(
                    replay=False,
                    nav=self.bot_nav,
                    pnl_pct=pnl_pct,
                    report_path=str(report_path or ""),
                )
                log.info(f"📤 Live git shutdown complete — {git_r}")
            except Exception as exc:
                log.error(f"Shutdown git sync failed: {exc}")
                try:
                    push_daily_summary(self.bot_nav, self.account_equity)
                except Exception:
                    pass

        try:
            if os.getenv("REPLAY_LIVE", "").lower() not in ("1", "true", "yes"):
                cleanup_local_workspace(aggressive=True)
            else:
                log.debug("Replay shutdown — skipping aggressive cleanup (teardown flush handles learning)")
        except Exception as exc:
            log.debug(f"Local cleanup: {exc}")

        self._stop_all_target_streams()

        if self.autopilot:
            try:
                self.autopilot.stop()
            except Exception:
                pass
        if getattr(self, "_telegram_listener", None):
            try:
                self._telegram_listener.stop()
            except Exception:
                pass
        self.conn.disconnect()
        try:
            from core.shutdown_control import clear_shutdown_request, remove_pid_file
            clear_shutdown_request()
            remove_pid_file()
        except Exception:
            pass
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