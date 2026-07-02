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

# ── Smart stack quality (AI sovereignty — all trades decided by AI) ──────────
export SMART_STACK=true
export SMART_STACK_STRICT_PROFIT_PROB=false     # Sprint forces ON during toddler stage
export SMART_STACK_AI_SURE_ENTRY=false          # PPO can lead without Halim alignment
export SMART_STACK_WAR_POSTURE=true
export SMART_STACK_ADVISORY_GATES=true
export SMART_STACK_TEACHER_HARD_ONLY=true
export GREEN_DOCTRINE_ENTRY=true
export GREEN_DOCTRINE_EXIT=true
export GREEN_VERDICT_RECHECK=false
export GREEN_SPIKE_PRECHECK=true
export HYBRID_DISTILL_FAST_PATH=false           # never let student proxy bypass Halim/council
export SNIPER_HALIM_FAST_SEC=1.5                # give Halim time before fast-path bypass

# ── Pre-market entry mode (relaxed gates + tight risk for AM opportunities) ──
export PRE_MARKET_ENTRY_ENABLED=true
export PRE_MARKET_PROFIT_PROB_FLOOR=0.50
export PRE_MARKET_MIN_CONFIDENCE=0.48
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
export MIN_CONFIDENCE_PRE_MARKET=0.48

# ── Commander lottery (no silent 80% floor) ─────────────────────────────────
export COMMANDER_RUNTIME_ENABLED=false
export COMMANDER_LOTTERY_MIN_PROFIT_PROB=0.58
export COMMANDER_LOTTERY_MIN_SPIKE_RATIO=1.25
export COMMANDER_LOTTERY_MIN_SCAN_SCORE=55

# ── PPO wheel / war (unlimited bullets — use available balance, AI sizes) ────
export PPO_WHEEL_PROFILE_LOCK=true
export PPO_ONLY_EXECUTION=true
export PPO_LEAD_WHILE_COUNCIL_PENDING=true
export PPO_LEAD_EXITS=true
export COUNCIL_EXECUTION_ADVISORY_ONLY=true
export WAR_ENTRY_ADVISORY_ONLY=true              # war is context for AI, not a hard block
export WAR_BALANCE_DRIVEN_TRIPS=true             # use settled cash, not bullet count
export WAR_AI_SIZING=true                        # AI determines position size
export WAR_MAX_ROUND_TRIPS_PER_DAY=999           # effectively unlimited
export PPO_DEPLOY_TIERS_ENABLED=true
export LEARN_APPROVAL_REQUIRED=true
export CONFIDENCE_THRESHOLD=0.58
export CAPITAL_MIN_CONFIDENCE=0.58
export MIN_PROFIT_PROBABILITY=0.52
export CAPITAL_MIN_PROFIT_PROBABILITY=0.52
export WAR_MIN_PROFIT_PROBABILITY=0.52
export WAR_PAPER_MIN_PROFIT_PROBABILITY=0.52

# ── Technical override (momentum entries when PPO hesitates) ──────────────────
# When PPO says HOLD on a real spike, this override forces entry
export TECH_OVERRIDE_SPIKE_MIN=1.3               # spike ratio threshold (was 1.5)
export TECH_OVERRIDE_SCORE_MIN=30                # scan score threshold (was 35)

# ── Halim serve periodic restart (reclaims swapped MLX model memory) ─────────
export HALIM_SERVE_RESTART_SEC=900               # restart every 15min to avoid memory decay

# ── Halim overseer (advisory only — Halim observes, never blocks) ──────────
export OVERSEER_ENABLED=true                      # enable system overseer
export OVERSEER_INTERVAL_SEC=60                   # digest every 60s
export OVERSEER_MAX_EVENTS=200                    # rolling event window
export CAPITAL_DISCIPLINE=true
export TREAT_PAPER_AS_LIVE=true
export REGIME_ENTRY_BLOCK=true
export MTF_ENTRY_BLOCK=true

# ── Halim LM (8 GB: async coach mode — never awaited for entries) ──────────
export HALIM_FORCE_LM=true
export HALIM_ENTRY_LM_ENABLED=true
export HALIM_LIVE_GOLD_COLLECT=true
export HALIM_ENTRY_AWAIT_ENABLED=false          # Halim is async coach — never blocks entries
export HALIM_ENTRY_AWAIT_LIVE=false
export HALIM_ENTRY_AWAIT_SEC=0.0                # No wait — PPO acts instantly
export HALIM_ENTRY_LM_TIMEOUT_SEC=90            # Long timeout for background teaching
export HALIM_ENTRY_MAX_TOKENS=32                # Small output for mem pressure
export HALIM_ENTRY_TEMPERATURE=0.04
export HALIM_INFERENCE_TIMEOUT_SEC=90
export HALIM_ENTRY_SOFT_VETO=false
export HALIM_SMART_SPRINT=true
export HALIM_SPRINT_BLOCK_MICRO_FAST=true
export HALIM_LM_BACKEND=mlx
export HALIM_MODEL_PATH=halim/data/checkpoints/latest
export HALIM_SERVE_PREFER_ADAPTER=false

# Local MLX reasoning is free — unlimited reasoning per session
export LIVE_DECISION_API_DAILY=9999
export REPLAY_DECISION_API_DAILY=9999

# If toddler_v2_lora adapter exists, prefer it for fine-tuned reasoning
_LORA="${HANOON_DEVICE_PROFILE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}/halim/data/checkpoints/toddler_v2_lora"
if [[ -d "$_LORA" && -f "$_LORA/config.json" ]]; then
  export HALIM_MODEL_PATH=halim/data/checkpoints/toddler_v2_lora
fi

# ── Learning (PPO learns in-flight; async micro-updates, no blocking) ───────
export LEARNING_LIVE_MICRO_PPO=true              # PPO learns during live sessions
export LEARNING_DEFER_DURING_RTH=false           # learn during market hours (async)
export PPO_ENTRY_MICRO_ASYNC=true                # never block trading loop
export PPO_ENTRY_MICRO_STEPS=256                 # lighter per-step cost
export PPO_LIVE_MICRO_STEPS_MAX=128              # cap at 128 for 8GB safety
export INCREMENTAL_TRAINING_ENABLED=true         # learn between sessions too
export PPO_LEARN_EVERY_ENTRY=true                # learn on every fill

# ── IB paper safety ─────────────────────────────────────────────────────────
export CONNECTIVITY_WAIT_ON_IB_LOSS=true

# ── Cloud council budget (PPO+Halim are pilots; API is senior expert) ───────
export COUNCIL_NANNY_MODE=true                     # API only for critical/hard cases
export COUNCIL_NANNY_RESERVE_PCT=0.75              # keep 75% budget reserved for risk exits
export COUNCIL_NANNY_MIN_SPIKE=2.0                 # only call API on extreme spikes (2x+)
export COUNCIL_NANNY_MIN_SCORE=70                  # only call API on very high scan scores
export COUNCIL_NANNY_MIN_RING_SEC=15.0             # at most 1 ring per ticker per 15s
export COUNCIL_LEARNING_RING_ENABLED=false          # no deferred learning rings
export COUNCIL_LEARNING_RING_STRONG_SPIKE_ONLY=false  # no learning rings at all
export LIVE_AI_GLOBAL_RING_SEC=30                  # at most 1 council call per 30s globally
