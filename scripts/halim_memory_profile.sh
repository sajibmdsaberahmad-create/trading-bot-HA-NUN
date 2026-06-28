#!/usr/bin/env bash
# Shared memory/CPU profile for live + replay on constrained Macs (≤12GB RAM).
# Halim (M. A. Halim) is your owned model — this keeps trading fast while gold
# still collects at session end. Set HALIM_LOW_MEMORY=false to opt out.
#
# Usage: source from start_hanoon.sh / start_replay_live.sh (already wired).

_RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}' || echo 8192)
_LOW=false
if [[ "${HALIM_LOW_MEMORY:-}" == "false" ]]; then
  :
elif [[ "${HALIM_LOW_MEMORY:-}" == "true" ]] || [[ "${HALIM_LOW_MEMORY:-}" == "1" ]]; then
  _LOW=true
elif [[ "$(uname -s)" == "Darwin" ]] && [[ "$_RAM_MB" -le 12288 ]]; then
  _LOW=true
fi

if [[ "$_LOW" != "true" ]]; then
  return 0 2>/dev/null || true
fi

export HALIM_LOW_MEMORY_ACTIVE=true

# ── PPO: never block entries with 512-step sync learns ─────────────────────
export PPO_ENTRY_MICRO_ASYNC="${PPO_ENTRY_MICRO_ASYNC:-true}"
export PPO_ENTRY_MICRO_DEBOUNCE_SEC="${PPO_ENTRY_MICRO_DEBOUNCE_SEC:-120}"
export PPO_ENTRY_MICRO_STEPS="${PPO_ENTRY_MICRO_STEPS:-96}"
export PPO_LEARN_EVERY_ENTRY="${PPO_LEARN_EVERY_ENTRY:-true}"
export INCREMENTAL_TRAINING_ENABLED="${INCREMENTAL_TRAINING_ENABLED:-false}"
export INCREMENTAL_TRAIN_EVERY_N_TRADES="${INCREMENTAL_TRAIN_EVERY_N_TRADES:-0}"

# ── Halim LM: one copy via serve, MLX on Apple Silicon (not HF merged weights) ─
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  export HALIM_LM_BACKEND="${HALIM_LM_BACKEND:-mlx}"
  export HALIM_LM_BACKEND_LOCKED="${HALIM_LM_BACKEND_LOCKED:-true}"
fi
export HALIM_MODEL_PATH="${HALIM_MODEL_PATH:-halim/data/checkpoints/toddler_v1}"
export HALIM_INLINE_LM_FALLBACK="${HALIM_INLINE_LM_FALLBACK:-false}"
export HALIM_DIALOGUE_DURING_TRADING="${HALIM_DIALOGUE_DURING_TRADING:-false}"
export HALIM_ENTRY_LM_ENABLED="${HALIM_ENTRY_LM_ENABLED:-true}"
export HALIM_ENTRY_LM_TIMEOUT_SEC="${HALIM_ENTRY_LM_TIMEOUT_SEC:-6}"
export HALIM_ENTRY_LM_MIN_RING_SEC="${HALIM_ENTRY_LM_MIN_RING_SEC:-2.0}"
export HALIM_ENTRY_MAX_TOKENS="${HALIM_ENTRY_MAX_TOKENS:-72}"
export HALIM_ENTRY_BLEND_WEIGHT="${HALIM_ENTRY_BLEND_WEIGHT:-0.30}"
export HALIM_ENTRY_SOFT_VETO="${HALIM_ENTRY_SOFT_VETO:-true}"
export HALIM_ENTRY_VETO_MIN_CONF="${HALIM_ENTRY_VETO_MIN_CONF:-0.85}"
export HALIM_PPO_GENERATIVE_REFLECT="${HALIM_PPO_GENERATIVE_REFLECT:-false}"
export HALIM_PPO_DIALOGUE_THROTTLE_SEC="${HALIM_PPO_DIALOGUE_THROTTLE_SEC:-180}"
export HALIM_REASONING_TIMEOUT_SEC="${HALIM_REASONING_TIMEOUT_SEC:-20}"
export HALIM_CHAT_INFERENCE_TIMEOUT_SEC="${HALIM_CHAT_INFERENCE_TIMEOUT_SEC:-45}"

# Replay-only overrides (live keeps teardown training via replay_training / shutdown)
export REPLAY_PPO_INCREMENTAL_STEPS="${REPLAY_PPO_INCREMENTAL_STEPS:-0}"

# Ollama is not used (Groq/Gemini + Halim LM) — stop Homebrew autostart if present
if command -v brew >/dev/null 2>&1; then
  brew services stop ollama 2>/dev/null || true
fi
if pgrep -f "ollama serve" >/dev/null 2>&1; then
  echo "  🧹 Stopping Ollama (not used — Halim + cloud council)…"
  pkill -f "ollama serve" 2>/dev/null || true
fi

echo "  💾 Halim memory profile ON (${_RAM_MB}MB RAM) — entry LM advisory, async PPO, MLX, dialogue deferred"
