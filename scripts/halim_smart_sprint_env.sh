#!/usr/bin/env bash
# Halim Smart Sprint — all phases (A–D) for M2 8GB: smarter LM, fewer entry mistakes.
#
# Sourced last in start_hanoon.sh / replay scripts so these override limitless/PPO wheel.
# Disable: HALIM_SMART_SPRINT=false ./scripts/start_hanoon.sh
#
# Phases:
#   A — JSON gold + SFT + MLX retrain + Colab zip
#   B — Halim await, block toddler micro_fast, strict profit_prob
#   C — Replay API budget + gold collection toward child (200 council pairs)
#   D — Colab package + auto-install path

[[ "${HALIM_SMART_SPRINT:-true}" == "true" ]] || return 0 2>/dev/null || exit 0

# ── Phase B: fewer live mistakes (toddler → child) ───────────────────────────
# Phase B: 2.5s peek — MLX on M2 8GB usually answers in 0.6–3s; 6s felt log-slow with little gain
export HALIM_ENTRY_AWAIT_ENABLED=true
export HALIM_ENTRY_AWAIT_SEC="${HALIM_ENTRY_AWAIT_SEC:-2.5}"
export HALIM_ENTRY_AWAIT_LIVE=true
export HALIM_ENTRY_AWAIT_REPLAY=true
export HALIM_ENTRY_LM_ENABLED=true
export HALIM_ENTRY_LM_TIMEOUT_SEC="${HALIM_ENTRY_LM_TIMEOUT_SEC:-10}"
export HALIM_ENTRY_MAX_TOKENS="${HALIM_ENTRY_MAX_TOKENS:-48}"
export HALIM_ENTRY_TEMPERATURE="${HALIM_ENTRY_TEMPERATURE:-0.04}"
export HALIM_REASONING_VIA_SERVER="${HALIM_REASONING_VIA_SERVER:-auto}"
export HALIM_FORCE_LM=true
export HALIM_SERVE_PREFER_ADAPTER=true
export HALIM_INLINE_LM_FALLBACK=false

# Block ppo:micro_fast until child (proxy 92%+ can lead entries)
export HALIM_SPRINT_BLOCK_MICRO_FAST="${HALIM_SPRINT_BLOCK_MICRO_FAST:-true}"
export SPIKE_FAST_REQUIRES_QUALITY=true
export SMART_STACK_STRICT_PROFIT_PROB=true
export REPEAT_LOSER_MICRO_FAST_GATE=true

# Echo → teacher escalation (lower bar during sprint)
export HALIM_SPRINT_ECHO_TEACHER=true
export HALIM_SPRINT_ECHO_MIN_PROFIT_PROB="${HALIM_SPRINT_ECHO_MIN_PROFIT_PROB:-0.55}"
export HALIM_SPRINT_ECHO_MIN_SCAN="${HALIM_SPRINT_ECHO_MIN_SCAN:-35}"

# ── Phase A: gold + retrain ───────────────────────────────────────────────────
export HALIM_JSON_ENTRY_API="${HALIM_JSON_ENTRY_API:-true}"
export HALIM_JSON_ENTRY_API_MAX="${HALIM_JSON_ENTRY_API_MAX:-200}"
export HALIM_V5_PREP=true
export HALIM_ACTION_LEARN=true
export HALIM_PPO_COEVOLUTION=true
export HALIM_PPO_DIALOGUE=true
export HALIM_OUTCOME_GOLD=true
export HALIM_PREPARE_SFT_ON_SHUTDOWN=true
export HALIM_AUTO_LM_RETRAIN=true
export HALIM_AUTO_LM_MIN_NEW_PAIRS="${HALIM_AUTO_LM_MIN_NEW_PAIRS:-80}"
export HALIM_AUTO_LM_MIN_TOTAL_PAIRS="${HALIM_AUTO_LM_MIN_TOTAL_PAIRS:-300}"
export HALIM_AUTO_LM_ITERS="${HALIM_AUTO_LM_ITERS:-200}"
export HALIM_AUTO_LM_BATCH_SIZE="${HALIM_AUTO_LM_BATCH_SIZE:-1}"
export HALIM_AUTO_LM_OFF_HOURS_ONLY=true
export HALIM_AUTO_LM_RESTART_SERVE=true
export HALIM_AUTO_PACKAGE_COLAB=true

# Learn off-hours only (8GB — no browse during RTH)
export HALIM_LEARN_DURING_TRADING=false
export HALIM_LEARN_OFF_HOURS_ONLY=true
export HALIM_WEB_LEARN=true
export HALIM_LEARN_RAG=true

# ── Phase C: child stage acceleration ─────────────────────────────────────────
export HALIM_REPLAY_GOLD_COLLECT=true
export REPLAY_DECISION_API_DAILY="${REPLAY_DECISION_API_DAILY:-64}"
export LIVE_DECISION_API_DAILY="${LIVE_DECISION_API_DAILY:-24}"
export BRAIN_CHILD_DATASET_TARGET="${BRAIN_CHILD_DATASET_TARGET:-200}"
# Keep ai_sure gated by maturity — child unlock is the reward
export BRAIN_MATURITY_AI_SURE_AUTO=false

# ── Phase D: Colab path ───────────────────────────────────────────────────────
export HALIM_AUTO_INSTALL_COLAB=true
export HALIM_COLAB_WATCH_SEC="${HALIM_COLAB_WATCH_SEC:-15}"
