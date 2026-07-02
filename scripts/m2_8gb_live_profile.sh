#!/usr/bin/env bash
# Canonical live profile — MacBook Air / any Mac with ≤12 GB RAM.
# Sourced LAST from start_hanoon.sh — FORCE exports win over limitless/sprint/ppo_wheel.
#
# See: docs/PERFECTION_ROADMAP_M2_8GB.md · docs/SYSTEM_ASSESSMENT_2026-07-01.md

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && {
  echo "Source this file: source scripts/m2_8gb_live_profile.sh" >&2
  exit 1
}

export HANOON_DEVICE_PROFILE=m2_8gb_live
export HANOON_M2_CANONICAL_LIVE=true

# ── RAM-live / disk ─────────────────────────────────────────────────────────
export RAM_LIVE_ONLY=true
export PERIODIC_CLEANUP_SEC=0
export LEARNING_SYNC_INTERVAL_SEC=0
export AUTO_DISK_CLEANUP=false
export HALIM_DEVICE_SWEEP_ON_START=false

# ── Smart stack quality (hard rails) ────────────────────────────────────────
export SMART_STACK=true
export SMART_STACK_STRICT_PROFIT_PROB=true
export SMART_STACK_AI_SURE_ENTRY=false
export SMART_STACK_WAR_POSTURE=true
export SMART_STACK_ADVISORY_GATES=true
export SMART_STACK_TEACHER_HARD_ONLY=true
export GREEN_DOCTRINE_ENTRY=true
export GREEN_DOCTRINE_EXIT=true
export GREEN_VERDICT_RECHECK=false
export GREEN_SPIKE_PRECHECK=true

# ── Pre-market entry mode (relaxed gates + tight risk for AM opportunities) ──
export PRE_MARKET_ENTRY_ENABLED=true
export PRE_MARKET_PROFIT_PROB_FLOOR=0.50
export PRE_MARKET_MIN_CONFIDENCE=0.55
export PRE_MARKET_POSITION_SIZE_PCT=0.50
export PRE_MARKET_STOP_ATR_MULT=0.8
export PRE_MARKET_TP_ATR_MULT=1.2
export PRE_MARKET_EXIT_BEFORE_RTH=true
export PRE_MARKET_EXIT_CUSHION_SEC=120
export PRE_MARKET_MIN_SPIKE_RATIO=1.20
export PRE_MARKET_MIN_SCAN_SCORE=40
export PRE_MARKET_PPO_MIN_CONF=0.40
export PRE_MARKET_COUNCIL_FAST=true
# Existing MIN_CONFIDENCE_PRE_MARKET lowered for AM entry support
export MIN_CONFIDENCE_PRE_MARKET=0.55

# ── Commander lottery (no silent 80% floor) ─────────────────────────────────
export COMMANDER_RUNTIME_ENABLED=false
export COMMANDER_LOTTERY_MIN_PROFIT_PROB=0.58
export COMMANDER_LOTTERY_MIN_SPIKE_RATIO=1.25
export COMMANDER_LOTTERY_MIN_SCAN_SCORE=55

# ── PPO wheel / war (paper-as-live discipline) ──────────────────────────────
export PPO_WHEEL_PROFILE_LOCK=true
export PPO_ONLY_EXECUTION=true
export PPO_LEAD_WHILE_COUNCIL_PENDING=true
export PPO_LEAD_EXITS=true
export COUNCIL_EXECUTION_ADVISORY_ONLY=true
export WAR_ENTRY_ADVISORY_ONLY=true
export PPO_DEPLOY_TIERS_ENABLED=true
export LEARN_APPROVAL_REQUIRED=true
export CONFIDENCE_THRESHOLD=0.58
export CAPITAL_MIN_CONFIDENCE=0.58
export MIN_PROFIT_PROBABILITY=0.58
export CAPITAL_MIN_PROFIT_PROBABILITY=0.58
export WAR_MIN_PROFIT_PROBABILITY=0.58
export WAR_PAPER_MIN_PROFIT_PROBABILITY=0.58
export CAPITAL_DISCIPLINE=true
export TREAT_PAPER_AS_LIVE=true
export REGIME_ENTRY_BLOCK=true
export MTF_ENTRY_BLOCK=true

# ── Halim LM (8 GB: merged MLX, 1s micro-peek, 12s timeout) ───────────────
export HALIM_FORCE_LM=true
export HALIM_ENTRY_LM_ENABLED=true
export HALIM_LIVE_GOLD_COLLECT=true
export HALIM_ENTRY_AWAIT_ENABLED=true
export HALIM_ENTRY_AWAIT_LIVE=true
export HALIM_ENTRY_AWAIT_SEC=1.0
export HALIM_ENTRY_LM_TIMEOUT_SEC=12
export HALIM_ENTRY_MAX_TOKENS=48
export HALIM_ENTRY_TEMPERATURE=0.04
export HALIM_INFERENCE_TIMEOUT_SEC=90
export HALIM_ENTRY_SOFT_VETO=false
export HALIM_SMART_SPRINT=true
export HALIM_SPRINT_BLOCK_MICRO_FAST=true

_ROOT="${HANOON_DEVICE_PROFILE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [[ -f "$_ROOT/halim/data/checkpoints/toddler_v1/merged/model.safetensors" ]]; then
  export HALIM_SERVE_PREFER_ADAPTER=false
  export HALIM_MODEL_PATH="${HALIM_MODEL_PATH:-halim/data/checkpoints/toddler_v1}"
fi

# ── Learning (capture gold; no RTH micro-PPO on 8 GB) ───────────────────────
export LEARNING_LIVE_MICRO_PPO=false
export LEARNING_DEFER_DURING_RTH=true
export INCREMENTAL_TRAINING_ENABLED=false

# ── IB paper safety ─────────────────────────────────────────────────────────
export CONNECTIVITY_WAIT_ON_IB_LOSS=true
