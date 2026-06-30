#!/usr/bin/env python3
"""Shared imports for scalper_runner mixin modules (extracted methods)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.agent import predict_with_reasoning
from core.ai_learning_policy import (
    failure_cooldown_sec,
    is_ib_structural_reject,
    learn_dont_block,
    record_failure_for_learning,
    should_permanent_blacklist,
)
from core.bracket_validator import adapt_bracket_to_fill, validate_decision_bracket
from core.broker import BracketHandle, parse_ib_order_block
from core.async_utils import get_background_worker
from core.capital_discipline import allows_ppo_lead_while_pending
from core.commander_learning import run_commander_learning_cycle
from core.data import DataManager, coalesce_bars
from core.deferred_council_learning import deferred_learning_enabled
from core.config import BotConfig
from core.entry_pipeline import (
    confirm_entry_fill_from_ib as _confirm_entry_fill,
    entry_price_mode_for_session,
    new_entry_poll_state,
    stuck_entry_limit_px,
)
from core.experience_buffer import append as buffer_append
from core.fast_execution import (
    ai_exit_check_sec,
    ai_fast_execution,
    apply_micro_spike_boost,
    assign_stream_modes,
    background_watch_sec,
    council_max_wait_sec,
    entry_fill_poll_sec,
    entry_pending_block_sec,
    focus_rotation_enabled,
    is_priority_ticker,
    main_loop_sec,
    max_realtime_bar_streams,
    max_spike_attempts_per_cycle,
    monitor_ticker_list,
    priority_tick_streams,
    should_micro_fast_entry,
    should_spike_fast_entry,
    skip_historical_prefetch,
    spike_entry_cooldown_sec,
    stream_priority_count,
    stream_watch_cap,
    tick_spike_debounce_sec,
    tick_spike_monitor_enabled,
    tick_stream_count,
)
from core.market_data_learning import (
    clear_transient_md_blocks,
    filter_tradeable_tickers,
    is_market_data_blocked,
    record_fetch_failure,
)
from core.fill_reconciler import (
    PendingClose,
    build_close_record,
    resolve_entry_from_ib,
    resolve_exit_from_ib,
    snapshot_slot,
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
from core.git_sync import (
    push_daily_summary,
    push_learning_checkpoint_async,
    push_model_release,
    push_trade,
    sync_all_learning_artifacts,
)
from core.institutional import InstitutionalDetector
from core.local_cleanup import cleanup_local_workspace
from core.market_context import summarize_market_context
from core.market_hours import (
    allowed_trading_sessions_label,
    can_trade_now,
    get_market_state,
    is_extended_session,
    market_status_line,
    now_et,
    should_defer_bracket_children,
)
from core.market_regime import resolve_regime
from core.notify import log
from core.pilot_experience import pilot_experience_to_git
from core.pilot_mode import (
    effective_max_concurrent_positions,
    effective_max_shares_per_trade,
    effective_min_cash_reserve_pct,
    effective_min_hold_for_exit,
    effective_min_lock_candidates,
    effective_min_lock_score,
    effective_prefetch_top_n,
    generative_think,
    get_ai_deploy_budget,
    get_deploy_usd,
    get_effective_confidence_threshold,
    get_trade_risk_usd,
    is_ai_council_mode,
    is_ai_unlimited,
    mtf_score_bonus,
    observe_trade_everywhere,
    send_dynamic_notification,
    snapshot_features,
)
from core.scanner import PENNY_STOCK_UNIVERSE, ScanResult, ScannerHit, StockScanner
from core.self_improver import generate_self_improvement_plan
from core.train_subprocess import launch_training
from core.position_sync import repair_slot_entry_price, sync_position_slots_from_ib
from core.profit_hunting import (
    evaluate_spike_top_exit,
    evaluate_wave_end_on_spike_fade,
    is_mechanical_profit_exit,
    mechanical_bypass_council,
    profit_exit_bypasses_council,
    profit_exit_bypasses_hold,
    record_profit_hunt_learning,
    teach_profit_hunt_lesson,
    track_profit_hunt_event,
)
from core.reward_shaping import reward_from_bracket_reject, reward_from_trade
from core.risk import TradePlan, compute_atr, compute_momentum_score, safe_vwap
from core.rth_session import ai_session_context_block, is_rth, rth_status_line, rth_tier
from core.scalper_micro_predict import bars_with_live_tick, micro_forecast
from core.trade_telemetry import (
    log_bracket_reject,
    log_entry_execution,
    log_exit_postmortem,
    log_post_fill_adapt,
    log_regime_atr_outcome,
    log_round_trip_fills,
    regime_tag,
)

__all__ = [
    name for name in dir() if not name.startswith("_")
]
