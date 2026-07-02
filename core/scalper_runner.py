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

from core.market_hours import (
    get_market_state,
    market_status_line,
    now_et,
    can_trade_now,
    is_extended_session,
    allowed_trading_sessions_label,
    should_defer_bracket_children,
)
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
from core.market_regime import MarketRegimeDetector, resolve_regime
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
from core.account_view import day_pnl_ib as _day_pnl_ib_view
from core.entry_pipeline import (
    confirm_entry_fill_from_ib as _confirm_entry_fill,
    entry_price_mode_for_session,
    new_entry_poll_state,
    stuck_entry_limit_px,
)
from core.position_sync import (
    adopt_ib_positions_into_slots,
    repair_slot_entry_price,
    sync_position_slots_from_ib,
)
from core.position_context import (
    bind_risk_plan_for_ticker,
    risk_plan_sane_for_tick,
    slot_entry_price,
)
from core.fill_tracker import (
    append_fill_ledger,
    build_round_trip_record,
    ib_fill_strict,
    ib_position_shares,
    require_ib_fill_sync,
    resolve_entry_fill,
    resolve_exit_fill,
)
from core.fill_reconciler import (
    PendingClose, build_close_record, snapshot_slot,
    resolve_entry_from_ib, resolve_exit_from_ib,
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

from core.scalper_filters import only_uptrend as _only_uptrend
from core.scalper_exit_executor import ScalperExitMixin
from core.scalper_entry_executor import ScalperEntryMixin
from core.scalper_session import ScalperSessionMixin
from core.scalper_spike_loop import ScalperSpikeMixin


class ScalperRunner(ScalperExitMixin, ScalperEntryMixin, ScalperSessionMixin, ScalperSpikeMixin):
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
        try:
            from core.ppo_wheel_profile import log_ppo_wheel_banner
            log_ppo_wheel_banner()
        except Exception:
            pass
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
        self._rth_starting_balance: Optional[float] = None
        
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
        self._snapshot_cooldown_until: Dict[str, float] = {}
        self._last_snapshot_px: Dict[str, float] = {}
        self._last_pulse_fingerprint: str = ""
        self._last_stagnation_decision: Dict[str, Any] = {}
        self._last_entry_telemetry: Dict[str, Any] = {}
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
        self._position_ctx_lock = threading.RLock()
        self._ctx_slot_shares: float = 0.0
        
        self.trade_journal: List[Dict] = []
        self._trade_journal_max = int(os.getenv("TRADE_JOURNAL_MAX", "500"))
        self.trades_today: int = 0
        self._current_day: Optional[str] = None
        self._last_daily_push_date: Optional[str] = None
        self._last_market_state: Optional[str] = None
        self._last_market_closed_log: float = 0.0
        self._day_session_ended: bool = False
        self._rth_open_day: Optional[str] = None
        self._pre_market_open_day: Optional[str] = None
        self._entries_this_hour: int = 0
        self._smart_gate_context: Dict[str, Dict[str, Any]] = {}
        self._hour_window_start: float = time.time()
        self._last_quality_watch_log: float = 0.0
        self._pending_closes: Dict[str, PendingClose] = {}
        self._recently_exited: Dict[str, float] = {}
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
    def _notify_context(
        self,
        extra: Optional[Dict[str, Any]] = None,
        *,
        event_type: str = "",
    ) -> Dict[str, Any]:
        """Telegram context — IB Truth only (see core/notify_ib_context.py)."""
        from core.notify_ib_context import telegram_notify_context
        return telegram_notify_context(self, self.cfg, extra, event_type=event_type)
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
        """Pull live balance from IB Hub — truth + extended + macro in one pass."""
        try:
            from core.ib_hub import ib_hub_enabled, refresh_services_for_runner
            from core.ib_truth import ib_truth_enabled

            if ib_hub_enabled() and ib_truth_enabled(self.cfg):
                refresh_services_for_runner(self)
            elif ib_truth_enabled(self.cfg):
                from core.ib_truth import apply_to_runner, refresh
                snap = refresh(self.ib, self.cfg)
                apply_to_runner(self, snap)
            else:
                values = self.ib.accountValues()
                for v in values:
                    if v.tag in ("NetLiquidation", "TotalCashValue"):
                        if v.currency == self.cfg.CURRENCY:
                            self.account_equity = float(v.value)
                            if v.tag == "TotalCashValue":
                                self.available_cash = float(v.value)
        except Exception as exc:
            log.debug(f"Could not fetch IB account balance: {exc}")
            try:
                values = self.ib.accountValues()
                for v in values:
                    if v.tag in ("NetLiquidation", "TotalCashValue"):
                        if v.currency == self.cfg.CURRENCY:
                            self.account_equity = float(v.value)
                            if v.tag == "TotalCashValue":
                                self.available_cash = float(v.value)
            except Exception:
                pass
        if self.available_cash is None:
            self.available_cash = self.account_equity
        self.cash = self.available_cash
        if self._ib_starting_balance is None and self.account_equity > 0:
            self._ib_starting_balance = self.account_equity
            if ai_full_capital_access(self.cfg):
                self.bot_cash = float(self.available_cash or self.account_equity)
                self.bot_nav = self.account_equity
        try:
            from core.rth_session import is_rth
            if is_rth(self.cfg) and self.account_equity > 0:
                if self._rth_starting_balance is None:
                    self._rth_starting_balance = self.account_equity
        except Exception:
            pass
        self.cfg._latest_account_balance = self.account_equity
        if getattr(self.cfg, "USE_MULTI_POSITION", True) and self._position_slots:
            if not self._ib_sync_enabled():
                self._recalc_bot_nav()
        else:
            if not self._ib_sync_enabled():
                self.bot_nav = self.bot_cash + self.shares * self._latest_price()
        self._sync_bot_nav_from_ib()

    def _maybe_sync_war_from_ib(self, *, force: bool = False) -> None:
        """Throttled war ledger sync — not every balance poll."""
        try:
            from core.war_ib_sync import sync_war_from_ib, war_ib_sync_enabled
            if war_ib_sync_enabled(self.cfg):
                sync_war_from_ib(self.ib, self.cfg, apply=True, force=force)
        except Exception as exc:
            log.debug(f"War IB sync: {exc}")
    def _deployable_cash(self) -> float:
        """Cash for new entries — war settled cash only during RTH war phase."""
        try:
            from core.capital_phase import uses_war_sizing
            from core.war_account import war_account_enabled, war_settled_cash
            if war_account_enabled(self.cfg) and uses_war_sizing(self.cfg, self):
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
        try:
            from core.market_hours import can_trade_now
            can_trade, _ = can_trade_now(self.cfg)
            if not can_trade:
                return
        except Exception:
            pass
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
        """Best available price: live tick stream, then cache, then IB snapshot — never cross-ticker."""
        from core.fill_tracker import sanitize_quote_price
        from core.position_context import slot_price_sane

        t = (ticker or "").upper()
        fb = float(fallback or 0)

        def _ok(px: float) -> bool:
            return px > 0 and (fb <= 0 or slot_price_sane(fb, px))

        def _clean(px: float) -> float:
            if px <= 0:
                return 0.0
            fc = self._last_micro_forecast.get(t, {})
            pred = float(fc.get("pred_1bar") or 0)
            return sanitize_quote_price(px, ref_px=fb, pred_px=pred, symbol=t)

        dm = self._target_monitors.get(t)
        if dm:
            live = dm.get_latest_price()
            if live:
                px = _clean(float(live))
                if _ok(px):
                    return px
        df = self._scan_data_cache.get(t)
        if df is not None and len(df) > 0:
            px = _clean(float(df["close"].iloc[-1]))
            if _ok(px):
                return px
        snap = self._force_price_snapshot(t, ref_px=fb)
        if _ok(snap):
            return snap
        return fb if fb > 0 else 0.0
    def _get_bid_ask(self, ticker: str) -> Tuple[Optional[float], Optional[float]]:
        """Snapshot bid/ask from IB for smart limit entries."""
        try:
            contract = self.conn.get_contract(ticker)
            ticks = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(0.12)
            bid = float(ticks.bid) if ticks.bid and ticks.bid > 0 else None
            ask = float(ticks.ask) if ticks.ask and ticks.ask > 0 else None
            self.ib.cancelMktData(contract)
            return bid, ask
        except Exception as exc:
            log.debug(f"Bid/ask snapshot {ticker}: {exc}")
            return None, None
    def _force_price_snapshot(self, ticker: str, *, ref_px: float = 0.0) -> float:
        """IB market snapshot when tick stream appears frozen — rate-limited per ticker."""
        t = (ticker or "").upper()
        if not t:
            return 0.0
        held = t in self._held_tickers() or t in (self._position_slots or {})
        can_trade = True
        try:
            from core.market_hours import can_trade_now
            can_trade, _ = can_trade_now(self.cfg)
            if not can_trade and not held:
                return float(self._last_snapshot_px.get(t, 0) or 0)
        except Exception:
            pass
        now = time.time()
        cooldown = float(getattr(self.cfg, "PRICE_SNAPSHOT_COOLDOWN_SEC", 8.0))
        if now < self._snapshot_cooldown_until.get(t, 0):
            cached = self._last_snapshot_px.get(t, 0.0)
            if cached > 0:
                return cached
        try:
            from core.fill_tracker import sanitize_quote_price, snapshot_market_price
            raw = snapshot_market_price(self.ib, t)
            if raw <= 0:
                contract = self.conn.get_contract(t)
                ticks = self.ib.reqMktData(contract, "", False, False)
                self.ib.sleep(0.12)
                for attr in ("last", "close", "marketPrice"):
                    val = getattr(ticks, attr, None)
                    if val and float(val) > 0:
                        raw = float(val)
                        break
                else:
                    bid = float(ticks.bid) if ticks.bid and ticks.bid > 0 else 0.0
                    ask = float(ticks.ask) if ticks.ask and ticks.ask > 0 else 0.0
                    raw = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
                self.ib.cancelMktData(contract)
            if raw <= 0:
                return 0.0
            bar_ref = ref_px
            if bar_ref <= 0:
                df = self._scan_data_cache.get(t)
                if df is not None and len(df) > 0:
                    bar_ref = float(df["close"].iloc[-1])
            fc = self._last_micro_forecast.get(t, {})
            pred = float(fc.get("pred_1bar") or 0)
            px = sanitize_quote_price(raw, ref_px=bar_ref, pred_px=pred, symbol=t)
            self._snapshot_cooldown_until[t] = now + cooldown
            prev = self._last_snapshot_px.get(t, 0.0)
            self._last_snapshot_px[t] = px
            if px > 0:
                dm = self._target_monitors.get(t)
                if dm is not None:
                    dm.last_tick_price = px
                if prev <= 0 or abs(px - prev) / max(prev, 0.01) > 0.002:
                    if held or can_trade:
                        log.info(f"  📡 Price snapshot refresh {t}: ${px:.4f}")
                    else:
                        log.debug(f"  📡 Price snapshot refresh {t}: ${px:.4f} (watchlist)")
                else:
                    log.debug(f"  📡 Price snapshot refresh {t}: ${px:.4f} (unchanged)")
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
    def _slot_entry_price(self, slot: Dict[str, Any]) -> float:
        return slot_entry_price(slot)

    def _bind_risk_plan_for_ticker(self, ticker: str) -> bool:
        return bind_risk_plan_for_ticker(
            ticker,
            position_slots=self._position_slots,
            risk_plans=self._risk_plans,
            risk=self.risk,
        )

    def _save_position_context(self, ticker: str):
        with self._position_ctx_lock:
            slots = getattr(self, "_position_slots", {})
            if ticker not in slots:
                return
            if (self.current_ticker or "").upper() != (ticker or "").upper():
                return
            slot_sh = float(slots[ticker].get("shares", 0) or self._ctx_slot_shares or 0)
            save_shares = float(self._ctx_slot_shares or slot_sh or 0)
            if save_shares <= 0:
                save_shares = slot_sh
            slots[ticker].update({
                "shares": save_shares,
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
            self._ctx_slot_shares = save_shares
    def _load_position_context(self, ticker: str) -> bool:
        with self._position_ctx_lock:
            s = self._position_slots.get(ticker)
            if not s:
                return False
            t_up = (ticker or "").upper()
            self._repair_slot_entry_price(t_up)
            s = self._position_slots.get(t_up)
            if not s:
                return False
            slot_sh = float(s.get("shares", 0) or 0)
            if slot_sh <= 0:
                return False
            self.risk.close_position()
            self.current_ticker = t_up
            self.shares = slot_sh
            self._ctx_slot_shares = slot_sh
            self._entry_price = self._slot_entry_price(s)
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
            self._bind_risk_plan_for_ticker(ticker)
            return True
    def _resolve_monitor_price(self, ticker: str, entry_px: float) -> Tuple[float, bool]:
        """Per-ticker price for monitor — IB Truth mark when snapshot fresh."""
        try:
            from core.ib_truth import get_snapshot, ib_truth_enabled, position_pulse
            if ib_truth_enabled(self.cfg):
                snap = get_snapshot()
                if snap.refreshed_at > 0:
                    pulse = position_pulse(ticker, snap)
                    if pulse.get("ok"):
                        px = float(pulse["price"])
                        if entry_px <= 0 or px > 0:
                            return px, True
        except Exception:
            pass

        from core.position_context import slot_price_sane

        fallback = entry_px if entry_px > 0 else 0.0
        px = self._live_price_for(ticker, fallback)
        if entry_px <= 0 or px <= 0:
            return px, px > 0
        if slot_price_sane(entry_px, px):
            return px, True
        snap = self._force_price_snapshot(ticker)
        if snap > 0 and slot_price_sane(entry_px, snap):
            return snap, True
        log.warning(
            f"  ⚠️ Wrong tick {ticker}: ${px:.4f} vs entry ${entry_px:.4f} — "
            "mechanical exits paused this pulse"
        )
        return px, False
    def _record_war_adoptions(self, adopted: list[str]) -> None:
        """Register IB-recovered slots on war ledger without debiting settled cash."""
        try:
            from core.war_account import adopt_war_ib_recovery, war_account_enabled
            if not war_account_enabled(self.cfg) or not adopted:
                return
            for ticker in adopted:
                slot = self._position_slots.get(ticker)
                if not slot:
                    continue
                sh = int(float(slot.get("shares", 0) or 0))
                entry = self._slot_entry_price(slot)
                if sh <= 0 or entry <= 0:
                    continue
                adopt_war_ib_recovery(
                    self.cfg,
                    ticker=ticker,
                    shares=sh,
                    ib_fill=entry,
                    quote=entry,
                )
        except Exception as exc:
            log.debug(f"War adopt entry: {exc}")
    def _repair_slot_entry_price(self, ticker: str) -> None:
        slot = self._position_slots.get(ticker)
        if not slot:
            return
        live = self._live_price_for(ticker, float(slot.get("entry_price", 0) or 0))
        repair_slot_entry_price(self.ib, ticker, slot, live)
    def _dm_for_ticker(self, ticker: str) -> Optional[DataManager]:
        """Live bar stream for a held ticker — never borrow another symbol's stream."""
        return self._target_monitors.get(ticker or "")
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
        if self._ib_sync_enabled():
            self._sync_bot_nav_from_ib()
            return
        total_pos = 0.0
        for t, s in self._position_slots.items():
            if not s.get("ib_fill_confirmed", True):
                continue
            px = self._live_price_for(t, float(s.get("entry_price", 0)))
            total_pos += float(s.get("shares", 0)) * px
        self.bot_nav = self.bot_cash + total_pos
        self._sync_bot_nav_from_ib()
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
        try:
            pending = {
                p.ticker.upper()
                for p in getattr(self, "_pending_closes", {}).values()
            }
            adopted = adopt_ib_positions_into_slots(
                self.ib,
                self._position_slots,
                exclude_tickers=pending,
                recently_exited=self._recently_exited,
            )
            for ticker in adopted:
                self._bind_risk_plan_for_ticker(ticker)
                try:
                    self._ensure_position_stream(ticker)
                except Exception:
                    pass
            self._record_war_adoptions(adopted)
            if self._position_slots:
                sync_position_slots_from_ib(
                    self.ib,
                    self._position_slots,
                    short_warned=self._short_warned,
                )
            self._refresh_aggregate_position_state()
        except Exception as exc:
            log.debug(f"Multi position sync: {exc}")
    def _position_risk_budget(self) -> float:
        """Risk budget for exit heuristics — AI stop distance or fixed $50 cap."""
        if self._position_stop > 0 and self._entry_price > 0 and self.shares > 0:
            stop_risk = (self._entry_price - self._position_stop) * self.shares
            if stop_risk > 0:
                return stop_risk
        if getattr(self.cfg, "USE_FIXED_RISK_CAP", False):
            return float(getattr(self.cfg, "HARD_STOP_USD", 50.0))
        return get_trade_risk_usd(self.cfg, self.account_equity)
    def _day_pnl_ib(self) -> Tuple[float, float]:
        return _day_pnl_ib_view(self)
    def _sync_bot_nav_from_ib(self) -> None:
        """Keep displayed NAV aligned with IB when fill sync is on."""
        if not self._ib_sync_enabled():
            return
        if self.account_equity > 0:
            self.bot_nav = float(self.account_equity)
    def _sync_position_from_ib(self):
        """Keep local shares in sync with IB (detect bracket fills/exits)."""
        if not self.current_ticker:
            return
        try:
            found = False
            ib_shares = 0.0
            try:
                from core.ib_truth import get_snapshot, ib_truth_enabled
                snap = get_snapshot()
                if ib_truth_enabled(self.cfg) and snap.refreshed_at > 0:
                    pos = snap.long_positions().get(self.current_ticker.upper())
                    if pos is not None:
                        ib_shares = float(pos.qty)
                        found = ib_shares > 0
            except Exception:
                pass
            if not found:
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
    def _fill_cache(self):
        return getattr(self.conn, "fill_cache", None)
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
        try:
            from core.ib_truth_checklist import (
                log_startup_checklist,
                run_startup_checklist,
                startup_block_on_fail,
            )
            _chk = run_startup_checklist(self, self.ib, self.cfg, wait=True)
            log_startup_checklist(_chk)
            if _chk.get("block") and not _chk.get("ok"):
                if startup_block_on_fail(self.cfg):
                    log.error("HANOON halted: IB Truth checklist failed — fix Gateway and restart")
                    from core.shutdown_control import remove_pid_file
                    remove_pid_file()
                    return
        except Exception as exc:
            log.debug(f"IB Truth startup checklist: {exc}")
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
        self._defer_war_ib_sync = False
        try:
            from core.war_account import ensure_war_account
            ensure_war_account(self.cfg, sync_ib=False)
            if self.ib is not None and self.conn.is_connected():
                self._defer_war_ib_sync = True
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
            warm_macro_context_background(getattr(self, "conn", None))
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

        if self._shutdown_abort():
            self._shutdown()
            return

        # IB housekeeping before notify — bounded so startup cannot hang on orphan covers
        import time as _time
        hk_budget = float(os.getenv("STARTUP_IB_HOUSEKEEPING_SEC", "12"))
        hk_deadline = _time.monotonic() + hk_budget
        log.info(f"🧹 Startup IB housekeeping (≤{hk_budget:.0f}s) — stale orders + orphan shorts…")
        try:
            self.broker.cancel_stale_open_orders(deadline=hk_deadline)
            n = 0
            if _time.monotonic() < hk_deadline:
                n = self.broker.flatten_orphan_short_positions(deadline=hk_deadline)
            if n:
                log.info(f"🧹 Covered {n} orphan short position(s) on paper account")
            pending = {
                p.ticker.upper()
                for p in getattr(self, "_pending_closes", {}).values()
            }
            adopted = adopt_ib_positions_into_slots(
                self.ib,
                self._position_slots,
                exclude_tickers=pending,
                recently_exited=self._recently_exited,
            )
            for ticker in adopted:
                self._bind_risk_plan_for_ticker(ticker)
                try:
                    self._ensure_position_stream(ticker)
                except Exception:
                    pass
            self._record_war_adoptions(adopted)
            if adopted:
                self._refresh_aggregate_position_state()
        except Exception as exc:
            log.warning(f"Startup IB housekeeping incomplete: {exc}")

        if self._shutdown_abort():
            self._shutdown()
            return

        if getattr(self.cfg, "DYNAMIC_AI_NOTIFICATIONS", True):
            send_dynamic_notification(
                self.notifier, self.autopilot, "startup",
                self._notify_context({"ib_balance": self._ib_starting_balance}, event_type="startup"),
                "🚀 Life engine running (scalp + swing)",
                ai_commander=self.ai_commander,
                consciousness=self.consciousness,
                pilot=self.pilot,
            )
        else:
            self.notifier.info("🚀 Life engine running (scalp + swing)")

        try:
            from core.halim_companion import companion_session_ping
            if not self._shutdown_abort():
                companion_session_ping(self, self.cfg, trigger="session_startup")
        except Exception as exc:
            log.debug(f"Halim companion startup ping: {exc}")

        if self._shutdown_abort():
            self._shutdown()
            return

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

                if getattr(self, "_defer_war_ib_sync", False):
                    self._defer_war_ib_sync = False
                    try:
                        self._maybe_sync_war_from_ib(force=False)
                    except Exception as exc:
                        log.debug(f"Deferred war IB sync: {exc}")

                if getattr(self, "_needs_initial_scan", False):
                    self._needs_initial_scan = False
                    if self._interruptible_ib_sleep(0.2):
                        break
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
                if self._interruptible_ib_sleep(loop_sec):
                    break

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
                            if self._interruptible_ib_sleep(warmup):
                                break
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
                    held = len(self._held_tickers())
                    log.warning(
                        f"IB connection lost ({held} open position(s)) — "
                        "entering connectivity wait (no new entries)"
                    )
                    if not self.conn.reconnect():
                        log.error("IB reconnect ended — shutdown or finite attempts exhausted")
                        break
                    log.info("IB link restored — refreshing account balance and streams")
                    self._refresh_account_balance()
                    if self.conn.consume_resubscribe_pending():
                        self._resubscribe_all_streams(force=True)
                
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
                        tick_macro_context_if_due(getattr(self, "conn", None))
                    except Exception:
                        pass
                    try:
                        from core.swing_executor import monitor_swing_ib_slots, run_swing_ib_cycle
                        monitor_swing_ib_slots(self, self.cfg)
                        run_swing_ib_cycle(self, self.cfg)
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
                    if market_state == "pre_market" and old_state in ("overnight", "closed"):
                        self._on_pre_market_open(old_state)
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

                # Sweep pre-market positions before RTH open
                try:
                    self._sweep_pre_market_positions()
                except Exception as exc:
                    log.debug(f"Pre-market sweep loop: {exc}")

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
                                px, trusted = self._resolve_monitor_price(ticker, self._entry_price)
                                if not trusted or px <= 0:
                                    continue
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
                    if (
                        not self._shutdown_abort()
                        and now - getattr(self, "_last_off_hours_train", 0) >= train_iv
                    ):
                        self._last_off_hours_train = now
                        self._train_off_hours()
                        if self._shutdown_abort():
                            break
                        if not self._shutdown_abort():
                            try:
                                from core.swing_shadow import run_swing_shadow_scan
                                from core.trade_horizon import update_scalp_gate_from_ib
                                from core.ib_hub import refresh_all_ib_services
                                from core.ib_extended import ib_extended_enabled
                                from core.swing_paper import sync_swing_paper_from_shadow_verdicts
                                from core.ppo_swing_train import train_ppo_swing_from_shadow
                                from core.swing_learning import ingest_ib_swing_round_trips
                                from core.swing_web_learn import run_swing_web_learn_cycle
                                from core.swing_train import train_swing_policy

                                run_swing_shadow_scan(self, self.cfg)
                                ingest_ib_swing_round_trips(self.cfg)
                                run_swing_web_learn_cycle(self.cfg)
                                train_swing_policy(self.cfg)
                                update_scalp_gate_from_ib(self.cfg)
                                if ib_extended_enabled():
                                    refresh_all_ib_services(
                                        self.ib, self.cfg, self.conn,
                                        full=True, force=True, runner=self,
                                    )
                                sync_swing_paper_from_shadow_verdicts(self, self.cfg)
                                train_ppo_swing_from_shadow(self.cfg)
                            except Exception as exc:
                                log.debug(f"off-hours horizon: {exc}")
                
                self._refresh_account_balance()
                self._maybe_sync_war_from_ib()
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

                # ── Periodic Halim serve restart (reclaims swapped MLX model) ──
                halim_restart_iv = float(getattr(self.cfg, "HALIM_SERVE_RESTART_SEC", 900))
                if halim_restart_iv > 0 and not replay_mode:
                    last = getattr(self, "_last_halim_restart", 0)
                    if last == 0:
                        self._last_halim_restart = now  # don't fire immediately
                    elif now - last >= halim_restart_iv:
                        self._last_halim_restart = now
                        try:
                            import subprocess, signal
                            old = subprocess.run(
                                ["pgrep", "-f", "halim/halim/serve.py"],
                                capture_output=True, text=True, timeout=5,
                            )
                            pids = [int(p) for p in old.stdout.strip().split() if p.strip()]
                            for pid in pids:
                                os.kill(pid, signal.SIGTERM)
                            log.info(f"🔄 Halim serve restart: killed PID(s) {pids} (stale memory)")
                            # Start fresh Halim serve in background
                            root = getattr(self.cfg, "HANOON_DEVICE_PROFILE_ROOT", "")
                            serve_script = os.path.join(root, "halim", "halim", "serve.py") if root else "halim/halim/serve.py"
                            env = os.environ.copy()
                            env.pop("HALIM_SERVE_RESTART_SEC", None)  # prevent restart loop
                            subprocess.Popen(
                                ["python3", "-u", serve_script],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                env=env,
                            )
                            log.info(f"  🚀 New Halim serve launched (PID check after warmup)")
                        except Exception as exc:
                            log.debug(f"Halim periodic restart: {exc}")

                # ── Periodic Halim overseer digest (advisory observations) ──
                overseer_iv = float(os.getenv("OVERSEER_INTERVAL_SEC", "60"))
                if overseer_iv > 0 and not replay_mode:
                    last_ov = getattr(self, "_last_overseer_run", 0)
                    if last_ov == 0:
                        self._last_overseer_run = now
                    elif now - last_ov >= overseer_iv:
                        self._last_overseer_run = now
                        try:
                            from core.halim_overseer import get_digest, run_overseer_digest
                            digest_events = get_digest().consume()
                            tickers = len(getattr(self, "_locked_targets", []) or [])
                            run_overseer_digest(digest_events, tickers, cfg=self.cfg)
                        except Exception as exc:
                            log.debug(f"Overseer digest: {exc}")

                # ── Periodic parameter self-tuning (from Halim observations) ──
                if not replay_mode:
                    try:
                        from core.halim_self_tune import tune_cycle
                        result = tune_cycle(self.cfg)
                        if result.get("ok") and result.get("changes"):
                            pass  # tuned — already logged by tune_cycle
                    except Exception as exc:
                        log.debug(f"Self-tune: {exc}")

                # ── Drawdown guard (reads P&L from IB truth — no local tracking) ──
                if not replay_mode:
                    try:
                        from core.halim_drawdown_guard import check_drawdown
                        result = check_drawdown(self.cfg)
                        if result.get("rollback"):
                            pass  # rollback + code review + overseer already handled inside
                    except Exception as exc:
                        log.debug(f"Drawdown guard: {exc}")

                # ── Periodic code review (fire pending requests via council) ──
                if not replay_mode:
                    try:
                        from core.halim_code_review import try_review
                        try_review(self.cfg)
                    except Exception as exc:
                        log.debug(f"Code review: {exc}")

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
            if task == "entry_decision" and self.ai_commander:
                try:
                    st = dict(st)
                    st["force_timeout"] = True
                    self._resolve_entry_council(key, st)
                except Exception as exc:
                    log.debug(f"Council force-resolve {ticker}: {exc}")
            if task == "entry_decision":
                self._spike_attempt_until[ticker] = 0.0
            self._ai_councils.pop(key, None)
    def _has_ai_council(self, ticker: str, task: str) -> bool:
        return self._council_key(ticker, task) in self._ai_councils
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
    def _update_market_context(self):
        """Refresh Yahoo macro cache and update regime detector."""
        try:
            from core.market_context import refresh_macro_context
            from core.market_regime import regime_from_macro
            ctx = refresh_macro_context(force=True, connector=getattr(self, "conn", None))
            bar_df = (
                self.data.get_bar_dataframe()
                if hasattr(self.data, "get_bar_dataframe") else None
            )
            if bar_df is not None and len(bar_df) >= 5:
                regime = self.regime_detector.classify(bar_df)
            else:
                regime = regime_from_macro(ctx or {})
            from core.trade_telemetry import regime_tag
            regime_label = regime_tag(regime)
            buffer_append({
                "source": "market_context",
                "ticker": "MARKET",
                "action": "REGIME",
                "regime": regime_label,
                "confidence": getattr(regime, 'confidence', 0.0),
                "features": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            log.info(
                f"🌍 Market context: {ctx.get('spy_trend', 'unknown')} SPY, "
                f"{ctx.get('vix_regime', 'unknown')} VIX, regime={regime_label}"
            )
        except Exception as exc:
            log.debug(f"Market context update failed: {exc}")
    def _generate_guidelines(self) -> str:
        try:
            from core.scalper_guidelines import generate_scalper_guidelines
            return generate_scalper_guidelines(
                self._load_weights(),
                self.scan_results or [],
                self.bot_nav,
                float(self.cfg.INITIAL_CASH),
            )
        except Exception as exc:
            log.debug(f"Guidelines generation failed: {exc}")
            return ""


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