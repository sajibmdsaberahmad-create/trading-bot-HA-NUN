#!/usr/bin/env bash
# Replay entry gates aligned with live paper (scripts/m2_8gb_live_profile.sh).
# Sourced from start_replay_live.sh when REPLAY_MATCH_LIVE=true (default).
# Quality gold > volume — same green / profit-prob / capital rails as RTH paper.

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && {
  echo "Source this file: source scripts/replay_match_live_profile.sh" >&2
  exit 1
}

export REPLAY_MATCH_LIVE="${REPLAY_MATCH_LIVE:-true}"
export REPLAY_RELAX_COUNCIL="${REPLAY_RELAX_COUNCIL:-false}"
export REPLAY_RELAX_COPILOT="${REPLAY_RELAX_COPILOT:-false}"
export REPLAY_RELAX_WAR="${REPLAY_RELAX_WAR:-true}"

# ── Smart stack (same hard rails as live) ───────────────────────────────────
export SMART_STACK="${SMART_STACK:-true}"
export SMART_STACK_STRICT_PROFIT_PROB="${SMART_STACK_STRICT_PROFIT_PROB:-true}"
export SMART_STACK_AI_SURE_ENTRY="${SMART_STACK_AI_SURE_ENTRY:-false}"
export SMART_STACK_WAR_POSTURE="${SMART_STACK_WAR_POSTURE:-true}"
export SMART_STACK_ADVISORY_GATES="${SMART_STACK_ADVISORY_GATES:-true}"
export GREEN_DOCTRINE_ENTRY="${GREEN_DOCTRINE_ENTRY:-true}"
export GREEN_VERDICT_RECHECK="${GREEN_VERDICT_RECHECK:-false}"

# ── PPO wheel / confidence (live M2) ────────────────────────────────────────
export PPO_LEAD_WHILE_COUNCIL_PENDING="${PPO_LEAD_WHILE_COUNCIL_PENDING:-true}"
export WAR_ENTRY_ADVISORY_ONLY="${WAR_ENTRY_ADVISORY_ONLY:-true}"
export PPO_DEPLOY_TIERS_ENABLED="${PPO_DEPLOY_TIERS_ENABLED:-true}"
export LEARN_APPROVAL_REQUIRED="${LEARN_APPROVAL_REQUIRED:-true}"
export CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.58}"
export MIN_PROFIT_PROBABILITY="${MIN_PROFIT_PROBABILITY:-0.58}"
export CAPITAL_MIN_PROFIT_PROBABILITY="${CAPITAL_MIN_PROFIT_PROBABILITY:-0.58}"
export CAPITAL_DISCIPLINE="${CAPITAL_DISCIPLINE:-true}"
export WAR_MIN_PROFIT_PROBABILITY="${WAR_MIN_PROFIT_PROBABILITY:-0.58}"
export WAR_PAPER_MIN_PROFIT_PROBABILITY="${WAR_PAPER_MIN_PROFIT_PROBABILITY:-0.58}"

# ── Commander lottery floors (live PPO wheel disables 80% override) ─────────
export COMMANDER_RUNTIME_ENABLED="${COMMANDER_RUNTIME_ENABLED:-false}"
export COMMANDER_LOTTERY_MIN_PROFIT_PROB="${COMMANDER_LOTTERY_MIN_PROFIT_PROB:-0.58}"
export GREEN_SPIKE_PRECHECK="${GREEN_SPIKE_PRECHECK:-true}"

# ── Entry blocks (live defaults — advisory where smart stack says so) ───────
export REGIME_ENTRY_BLOCK="${REGIME_ENTRY_BLOCK:-true}"
export MTF_ENTRY_BLOCK="${MTF_ENTRY_BLOCK:-true}"
export USE_ACCOUNT_LOSS_HALT="${USE_ACCOUNT_LOSS_HALT:-true}"

# ── Halim LM (same peek / timeout as live M2) ───────────────────────────────
export HALIM_FORCE_LM="${HALIM_FORCE_LM:-true}"
export HALIM_ENTRY_LM_ENABLED="${HALIM_ENTRY_LM_ENABLED:-true}"
export HALIM_ENTRY_AWAIT_ENABLED="${HALIM_ENTRY_AWAIT_ENABLED:-true}"
export HALIM_ENTRY_AWAIT_LIVE="${HALIM_ENTRY_AWAIT_LIVE:-true}"
export HALIM_ENTRY_AWAIT_SEC="${HALIM_ENTRY_AWAIT_SEC:-1.0}"
export HALIM_ENTRY_LM_TIMEOUT_SEC="${HALIM_ENTRY_LM_TIMEOUT_SEC:-12}"
export HALIM_ENTRY_MAX_TOKENS="${HALIM_ENTRY_MAX_TOKENS:-48}"
export HALIM_ENTRY_TEMPERATURE="${HALIM_ENTRY_TEMPERATURE:-0.04}"
export HALIM_INFERENCE_TIMEOUT_SEC="${HALIM_INFERENCE_TIMEOUT_SEC:-90}"
export HALIM_SMART_SPRINT="${HALIM_SMART_SPRINT:-true}"
export HALIM_SPRINT_BLOCK_MICRO_FAST="${HALIM_SPRINT_BLOCK_MICRO_FAST:-true}"

_ROOT="${HANOON_DEVICE_PROFILE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [[ -f "$_ROOT/halim/data/checkpoints/toddler_v1/merged/model.safetensors" ]]; then
  export HALIM_SERVE_PREFER_ADAPTER="${HALIM_SERVE_PREFER_ADAPTER:-false}"
  export HALIM_MODEL_PATH="${HALIM_MODEL_PATH:-halim/data/checkpoints/toddler_v1}"
fi

# ── Gold quality (skip low-edge replay entries in buffer) ───────────────────
export REPLAY_GOLD_QUALITY_FILTER="${REPLAY_GOLD_QUALITY_FILTER:-true}"
export REPLAY_GOLD_MIN_PROFIT_PROB="${REPLAY_GOLD_MIN_PROFIT_PROB:-0.58}"
