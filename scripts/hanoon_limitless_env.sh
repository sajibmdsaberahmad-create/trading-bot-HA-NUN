#!/usr/bin/env bash
# Limitless profile — decisions uncapped; only war pool / settled cash sizes entries.
# Sourced last (after ppo_wheel_env + hanoon_profit_learn_env) in start_hanoon.sh.
# Disable: HANOON_LIMITLESS_WAR_ONLY=false ./scripts/start_hanoon.sh

[[ "${HANOON_LIMITLESS_WAR_ONLY:-true}" == "true" ]] || return 0 2>/dev/null || exit 0

# ── No API / maturity decision caps ───────────────────────────────────────────
export BRAIN_MATURITY_FORCE_API=true
export LIVE_DECISION_API_DAILY=99999
export REPLAY_DECISION_API_DAILY=99999
export LIVE_COUNCIL_SAMPLE=false
export REPLAY_COUNCIL_SAMPLE=false
export COUNCIL_BUDGET_ENABLED=false
export HALIM_GOOGLE_AI_DAILY_CAP=99999
export SMART_STACK_TEACHER_HARD_ONLY=false

# ── PPO wheel: Halim + quality + spike paths (not PPO-hold-only) ───────────────
export PPO_WHEEL_PROFILE_LOCK=false
export PPO_ONLY_EXECUTION=false
export PPO_LEAD_EXITS=true
export COUNCIL_EXECUTION_ADVISORY_ONLY=true
export HALIM_ENTRY_SOFT_VETO=false
export HALIM_ENTRY_AWAIT_SEC=0
export HALIM_ENTRY_VETO_MIN_CONF=0.99
export PPO_BYPASS_REQUIRES_BUY=false
export CAPITAL_DISCIPLINE=false
export TREAT_PAPER_AS_LIVE=false

# ── Low floors — war cash is the real gate ────────────────────────────────────
export CONFIDENCE_THRESHOLD=0.40
export MIN_PROFIT_PROBABILITY=0.32
export CAPITAL_MIN_CONFIDENCE=0.32
export CAPITAL_MIN_PROFIT_PROBABILITY=0.32
export WAR_MIN_PROFIT_PROBABILITY=0.32
export WAR_PAPER_MIN_PROFIT_PROBABILITY=0.32
export CAPITAL_MIN_ENTRY_SCAN_SCORE=30
export CAPITAL_MIN_ENTRY_SPIKE_RATIO=1.08
export PPO_BYPASS_MIN_CONF=0.38
export CAPITAL_STRONG_MIN_PPO_CONF=0.38
export ENTRY_QUALITY_HARDNESS=0.20
export SMART_STACK_STRICT_PROFIT_PROB=false
export SMART_STACK_AI_SURE_ENTRY=false
export REGIME_ENTRY_BLOCK=false

# ── Spike / council fast paths ────────────────────────────────────────────────
export AI_SPIKE_FAST_ENTRY=true
export SPIKE_FAST_RELAX_CONF=true
export AI_SPIKE_FAST_MIN_RATIO=1.08
export AI_SPIKE_FAST_MIN_SCORE=28
export PPO_LEAD_WHILE_COUNCIL_PENDING=true
export CAPITAL_PPO_LEAD_STRONG_SPIKE=true
export CAPITAL_STRONG_SPIKE_FAST=true
export PROFIT_HUNT_MECHANICAL_BYPASS_COUNCIL=true
export SPIKE_ENTRY_ATTEMPT_COOLDOWN_SEC=8
export CAPITAL_ENTRY_COOLDOWN_SEC=0
export MAX_ENTRIES_PER_HOUR=0
export WAR_MAX_ENTRIES_PER_HOUR=0
export WAR_PAPER_MAX_ENTRIES_PER_HOUR=0

# ── War pool only (balance-driven bullets, no trip clock) ───────────────────
export WAR_BALANCE_DRIVEN_TRIPS=true
export WAR_AI_SIZING=true
export WAR_ENTRY_ADVISORY_ONLY=true
export WAR_PAPER_MAX_ROUND_TRIPS_PER_DAY=999
export WAR_MAX_ROUND_TRIPS_PER_DAY=999
export AI_FULL_CAPITAL_ACCESS=true
export AI_UNLIMITED_MODE=true
export MAX_CONCURRENT_POSITIONS=20
export AI_MAX_CONCURRENT_POSITIONS=50

# ── Learning + retrain always available ─────────────────────────────────────
export LEARN_APPROVAL_REQUIRED=false
export LEARNING_LIVE_MICRO_PPO=true
export PPO_TEACHER_ENABLED=true
export PPO_LEARN_EVERY_ENTRY=true
export HALIM_AUTO_LM_RETRAIN=true
export AI_RUNTIME_AUTO_APPLY=true
export AI_LEARN_ON_LOSS_STREAK=true
export INCREMENTAL_TRAINING_ENABLED=false
export DAILY_IB_LEARNING_ENABLED=true
