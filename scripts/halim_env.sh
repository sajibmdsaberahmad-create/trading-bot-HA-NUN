#!/usr/bin/env bash
# Shared Halim + PPO distillation env — source from start scripts.
# PPO knowledge always flows into Halim (coevolution gold, dialogue, proxy, teacher).

_HALIM_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export HALIM_REPO_ROOT="$_HALIM_SCRIPT_ROOT"
export PYTHONPATH="$HALIM_REPO_ROOT/halim:$HALIM_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Toddler LM (Colab-trained checkpoint)
# Apple Silicon Mac → MLX (Metal, 4-bit, low RAM). Linux/Colab → HuggingFace.
# On arm64 Mac we always prefer MLX unless HALIM_LM_BACKEND_LOCKED=true (e.g. Colab export testing).
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  if [[ "${HALIM_LM_BACKEND_LOCKED:-}" != "true" ]]; then
    HALIM_LM_BACKEND=mlx
  elif [[ -z "${HALIM_LM_BACKEND:-}" ]]; then
    HALIM_LM_BACKEND=mlx
  fi
elif [[ -z "${HALIM_LM_BACKEND:-}" ]]; then
  HALIM_LM_BACKEND=hf
fi
export HALIM_LM_BACKEND
export HALIM_MODEL_PATH="${HALIM_MODEL_PATH:-halim/data/checkpoints/latest}"
if [[ "$HALIM_LM_BACKEND" == "mlx" ]]; then
  export HALIM_BASE_MODEL="${HALIM_BASE_MODEL:-mlx-community/Qwen2.5-0.5B-Instruct-4bit}"
else
  export HALIM_BASE_MODEL="${HALIM_BASE_MODEL:-Qwen/Qwen2.5-0.5B-Instruct}"
fi
export HALIM_REASONING_VIA_SERVER="${HALIM_REASONING_VIA_SERVER:-auto}"
export HALIM_FORCE_LM="${HALIM_FORCE_LM:-true}"

# PPO ↔ Halim mutual distillation — always on
export HALIM_PPO_COEVOLUTION="${HALIM_PPO_COEVOLUTION:-true}"
export HALIM_PPO_DIALOGUE="${HALIM_PPO_DIALOGUE:-true}"
export HALIM_PPO_GENERATIVE_REFLECT="${HALIM_PPO_GENERATIVE_REFLECT:-true}"
export HALIM_PPO_DIALOGUE_TELEGRAM="${HALIM_PPO_DIALOGUE_TELEGRAM:-false}"
export HALIM_ACTION_LEARN="${HALIM_ACTION_LEARN:-true}"
export HALIM_COMPANION_LEARN="${HALIM_COMPANION_LEARN:-true}"

# PPO teacher + sklearn proxy distillation — always on
export PPO_TEACHER_ENABLED="${PPO_TEACHER_ENABLED:-true}"
export HYBRID_DISTILL_AUTO_FAST_PATH="${HYBRID_DISTILL_AUTO_FAST_PATH:-true}"
export HYBRID_DISTILL_MIN_TRADES="${HYBRID_DISTILL_MIN_TRADES:-10}"
export OWNED_BRAIN_GIT_PUSH="${OWNED_BRAIN_GIT_PUSH:-true}"
