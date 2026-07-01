#!/usr/bin/env bash
# Canonical live profile — MacBook Air / any Mac with ≤12 GB RAM.
# Sourced LAST from start_hanoon.sh so these values win over inline duplicates.
# Override in .env before launch if you need experiments.
#
# See: docs/PERFECTION_ROADMAP_M2_8GB.md · docs/SYSTEM_ASSESSMENT_2026-07-01.md

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && {
  echo "Source this file: source scripts/m2_8gb_live_profile.sh" >&2
  exit 1
}

export HANOON_DEVICE_PROFILE="${HANOON_DEVICE_PROFILE:-m2_8gb_live}"

# ── RAM-live / disk ─────────────────────────────────────────────────────────
export RAM_LIVE_ONLY="${RAM_LIVE_ONLY:-true}"
export PERIODIC_CLEANUP_SEC="${PERIODIC_CLEANUP_SEC:-0}"
export LEARNING_SYNC_INTERVAL_SEC="${LEARNING_SYNC_INTERVAL_SEC:-0}"
export AUTO_DISK_CLEANUP="${AUTO_DISK_CLEANUP:-false}"
export HALIM_DEVICE_SWEEP_ON_START="${HALIM_DEVICE_SWEEP_ON_START:-false}"

# ── Smart stack quality (hard rails) ────────────────────────────────────────
export SMART_STACK="${SMART_STACK:-true}"
export SMART_STACK_STRICT_PROFIT_PROB="${SMART_STACK_STRICT_PROFIT_PROB:-true}"
export SMART_STACK_AI_SURE_ENTRY="${SMART_STACK_AI_SURE_ENTRY:-false}"
export SMART_STACK_WAR_POSTURE="${SMART_STACK_WAR_POSTURE:-true}"
export SMART_STACK_ADVISORY_GATES="${SMART_STACK_ADVISORY_GATES:-true}"
export GREEN_DOCTRINE_ENTRY="${GREEN_DOCTRINE_ENTRY:-true}"
export GREEN_VERDICT_RECHECK="${GREEN_VERDICT_RECHECK:-false}"

# ── PPO wheel / war (speed + advisory war) ──────────────────────────────────
export PPO_LEAD_WHILE_COUNCIL_PENDING="${PPO_LEAD_WHILE_COUNCIL_PENDING:-true}"
export WAR_ENTRY_ADVISORY_ONLY="${WAR_ENTRY_ADVISORY_ONLY:-true}"
export PPO_DEPLOY_TIERS_ENABLED="${PPO_DEPLOY_TIERS_ENABLED:-true}"
export LEARN_APPROVAL_REQUIRED="${LEARN_APPROVAL_REQUIRED:-true}"
export CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.58}"
export MIN_PROFIT_PROBABILITY="${MIN_PROFIT_PROBABILITY:-0.58}"
export CAPITAL_MIN_PROFIT_PROBABILITY="${CAPITAL_MIN_PROFIT_PROBABILITY:-0.58}"

# ── Halim LM (8 GB: merged MLX, micro-peek, longer timeout) ─────────────────
export HALIM_FORCE_LM="${HALIM_FORCE_LM:-true}"
export HALIM_ENTRY_LM_ENABLED="${HALIM_ENTRY_LM_ENABLED:-true}"
export HALIM_LIVE_GOLD_COLLECT="${HALIM_LIVE_GOLD_COLLECT:-true}"
export HALIM_ENTRY_AWAIT_ENABLED="${HALIM_ENTRY_AWAIT_ENABLED:-true}"
export HALIM_ENTRY_AWAIT_LIVE="${HALIM_ENTRY_AWAIT_LIVE:-true}"
export HALIM_ENTRY_AWAIT_SEC="${HALIM_ENTRY_AWAIT_SEC:-1.0}"
export HALIM_ENTRY_LM_TIMEOUT_SEC="${HALIM_ENTRY_LM_TIMEOUT_SEC:-12}"
export HALIM_ENTRY_MAX_TOKENS="${HALIM_ENTRY_MAX_TOKENS:-48}"
export HALIM_ENTRY_TEMPERATURE="${HALIM_ENTRY_TEMPERATURE:-0.04}"
export HALIM_INFERENCE_TIMEOUT_SEC="${HALIM_INFERENCE_TIMEOUT_SEC:-90}"

# Prefer merged weights when Colab export exists (more stable than adapter-only on MLX)
_ROOT="${HANOON_DEVICE_PROFILE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [[ -f "$_ROOT/halim/data/checkpoints/toddler_v1/merged/model.safetensors" ]]; then
  export HALIM_SERVE_PREFER_ADAPTER="${HALIM_SERVE_PREFER_ADAPTER:-false}"
  export HALIM_MODEL_PATH="${HALIM_MODEL_PATH:-halim/data/checkpoints/toddler_v1}"
fi

# ── Sprint blocks toddler micro_fast; quality gates stay on ─────────────────
export HALIM_SMART_SPRINT="${HALIM_SMART_SPRINT:-true}"
export HALIM_SPRINT_BLOCK_MICRO_FAST="${HALIM_SPRINT_BLOCK_MICRO_FAST:-true}"

# ── IB paper default safety ───────────────────────────────────────────────────
export TREAT_PAPER_AS_LIVE="${TREAT_PAPER_AS_LIVE:-true}"
export CONNECTIVITY_WAIT_ON_IB_LOSS="${CONNECTIVITY_WAIT_ON_IB_LOSS:-true}"
