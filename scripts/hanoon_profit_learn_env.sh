#!/usr/bin/env bash
# HANOON Profit + Learn profile — lock profit in flight, capture gold, upgrade off-hours.
#
# Sourced last in start_hanoon.sh (after ppo_wheel_env.sh) so these win over .env.
# Disable: HANOON_PROFIT_LEARN_PROFILE=false ./scripts/start_hanoon.sh
#
# Flywheel:
#   in-flight  → green lock + profit hunt + bounded micro-PPO + buffer capture
#   off-hours  → PPO teacher + reward train + owned-brain evolution (on graceful stop)

[[ "${HANOON_PROFIT_LEARN_PROFILE:-true}" == "true" ]] || return 0 2>/dev/null || exit 0

# ── Profit lock (in flight) ───────────────────────────────────────────────────
export GREEN_PROFIT_LOCK_ENABLED=true
export GREEN_PROFIT_LOCK_MIN_PNL_PCT="${GREEN_PROFIT_LOCK_MIN_PNL_PCT:-0.0020}"
export GREEN_PROFIT_LOCK_QUICK_SCALP_PCT="${GREEN_PROFIT_LOCK_QUICK_SCALP_PCT:-0.0030}"
export GREEN_PROFIT_LOCK_AI_WAIT_SEC="${GREEN_PROFIT_LOCK_AI_WAIT_SEC:-1.5}"
export GREEN_PROFIT_LOCK_GIVEBACK_PCT="${GREEN_PROFIT_LOCK_GIVEBACK_PCT:-0.18}"
export GREEN_PROFIT_LOCK_FADE_FLOOR_PCT="${GREEN_PROFIT_LOCK_FADE_FLOOR_PCT:-0.0012}"
export PROFIT_HUNT_ENABLED=true
export PROFIT_HUNT_PRIMARY_GOAL=true
export SPIKE_TOP_EXIT_ENABLED=true
export SPIKE_TOP_INTRABAR_ENABLED=true
export SPIKE_TOP_MIN_GAIN_PCT="${SPIKE_TOP_MIN_GAIN_PCT:-0.004}"
export TRAILING_PROFIT_GIVEBACK_PCT="${TRAILING_PROFIT_GIVEBACK_PCT:-0.40}"
export STAGNATION_EXIT_SEC="${STAGNATION_EXIT_SEC:-75}"
export STAGNATION_LOSS_CUT_PCT="${STAGNATION_LOSS_CUT_PCT:-0.004}"

# ── Learning capture (bounded live — 8GB-safe) ────────────────────────────────
export LEARNING_LIVE_MICRO_PPO=true
export PPO_LIVE_MICRO_STEPS_MAX="${PPO_LIVE_MICRO_STEPS_MAX:-48}"
export PPO_LIVE_MICRO_STEPS_MIN="${PPO_LIVE_MICRO_STEPS_MIN:-32}"
export LEARNING_LIVE_STEP_SCALE="${LEARNING_LIVE_STEP_SCALE:-0.12}"
export LEARNING_ASYNC_PPO_INTERVAL_SEC="${LEARNING_ASYNC_PPO_INTERVAL_SEC:-600}"
export LEARNING_MEMORY_MAX_PCT="${LEARNING_MEMORY_MAX_PCT:-78}"
export LEARNING_DEFER_DURING_RTH=true
export INCREMENTAL_TRAINING_ENABLED=false
export PPO_LEARN_EVERY_ENTRY=true
export LEARNING_LIVE_WEIGHT_EVERY_N_TRADES="${LEARNING_LIVE_WEIGHT_EVERY_N_TRADES:-6}"
export LEARNING_LIVE_WEIGHT_MIN_SEC="${LEARNING_LIVE_WEIGHT_MIN_SEC:-240}"
export LEARNING_HEAVY_EVERY_N_TRADES="${LEARNING_HEAVY_EVERY_N_TRADES:-3}"
export LEARN_APPROVAL_REQUIRED=true

# ── Off-hours upgrade (graceful ./stop.sh flushes queue) ──────────────────────
export OFF_HOURS_HEAVY_TRAINING=true
export DAILY_IB_LEARNING_ENABLED=true
export DAILY_IB_LEARNING_ON_SESSION_END=true
export DAILY_IB_LEARNING_ON_MARKET_OPEN=true
export DAILY_IB_PPO_TRAIN_STEPS="${DAILY_IB_PPO_TRAIN_STEPS:-18000}"
export PPO_TEACHER_ENABLED=true
export PPO_TEACHER_EVERY_N_TRADES="${PPO_TEACHER_EVERY_N_TRADES:-3}"
export PPO_TEACHER_MIN_INTERVAL_SEC="${PPO_TEACHER_MIN_INTERVAL_SEC:-120}"
export AI_LEARN_ON_LOSS_STREAK=true
export AI_RUNTIME_OBSERVER_ENABLED=true
export AI_RUNTIME_AUTO_APPLY=true

# ── Ops hygiene ───────────────────────────────────────────────────────────────
export TELEGRAM_STRUCTURED_ONLY=true
export HALIM_TELEGRAM_TRADE_NOTIFY=false
export CAPITAL_PHASES_ENABLED=true
export RAM_LIVE_ONLY=true
