#!/usr/bin/env bash
# Shared Halim + PPO distillation env — source from start scripts.
# PPO knowledge always flows into Halim (coevolution gold, dialogue, proxy, teacher).

_HALIM_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export HALIM_REPO_ROOT="$_HALIM_SCRIPT_ROOT"
export PYTHONPATH="$HALIM_REPO_ROOT/halim:$HALIM_REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# M. A. Halim toddler checkpoint (your owned LM — trained from open scaffold weights).
# HALIM_BASE_MODEL is the training scaffold only (HuggingFace id); never the product name.
_RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}' || echo 8192)
_MERGED_CKPT="$_HALIM_SCRIPT_ROOT/halim/data/checkpoints/toddler_v1/merged/model.safetensors"
if [[ -f "$_MERGED_CKPT" ]] && [[ "${HALIM_LM_BACKEND_LOCKED:-}" != "true" ]] \
    && [[ "${HALIM_LOW_MEMORY_ACTIVE:-}" != "true" ]] \
    && [[ "$_RAM_MB" -gt 12288 ]]; then
  HALIM_LM_BACKEND=hf
elif [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
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
# ≤12GB Mac: LoRA + 4bit base (~500MB) — merged safetensors (~1GB) OOM-kills serve under HANOON
if [[ "$_RAM_MB" -le 12288 ]]; then
  export HALIM_SERVE_PREFER_ADAPTER="${HALIM_SERVE_PREFER_ADAPTER:-true}"
fi
if [[ "$HALIM_LM_BACKEND" == "mlx" ]]; then
  # Scaffold registry id (HF hub) — not the Halim product name
  export HALIM_BASE_MODEL="${HALIM_BASE_MODEL:-mlx-community/Qwen2.5-0.5B-Instruct-4bit}"
else
  export HALIM_BASE_MODEL="${HALIM_BASE_MODEL:-Qwen/Qwen2.5-0.5B-Instruct}"
fi
export HALIM_DISPLAY_NAME="${HALIM_DISPLAY_NAME:-M. A. Halim}"
export HALIM_REASONING_VIA_SERVER="${HALIM_REASONING_VIA_SERVER:-auto}"
export HALIM_FORCE_LM="${HALIM_FORCE_LM:-true}"
# Toddler LM on 8GB Mac needs >2.5s (cold load + generate ~10–15s first reply)
export HALIM_INFERENCE_TIMEOUT_SEC="${HALIM_INFERENCE_TIMEOUT_SEC:-90}"
# Chat off while live/replay trading — full CPU/RAM for algo (see core/trading_focus_guard.py)
export HALIM_CHAT_DURING_TRADING="${HALIM_CHAT_DURING_TRADING:-false}"
# Device dedicates RAM to trading during market hours on ≤12GB Macs
export DEVICE_TRADING_FOCUS="${DEVICE_TRADING_FOCUS:-true}"
export HALIM_REMOVE_IDE_HOGS="${HALIM_REMOVE_IDE_HOGS:-true}"
export HALIM_DEVICE_FOCUS_SEC="${HALIM_DEVICE_FOCUS_SEC:-90}"
if [[ "$_RAM_MB" -le 12288 ]]; then
  export HALIM_LEARN_OFF_HOURS_ONLY="${HALIM_LEARN_OFF_HOURS_ONLY:-true}"
  export HALIM_SERVE_WATCHDOG_SEC="${HALIM_SERVE_WATCHDOG_SEC:-30}"
  export HALIM_WATCHDOG_INTERVAL_SEC="${HALIM_WATCHDOG_INTERVAL_SEC:-30}"
fi
export HALIM_SERVE_WATCHDOG="${HALIM_SERVE_WATCHDOG:-true}"
export HALIM_STANDALONE_WATCHDOG="${HALIM_STANDALONE_WATCHDOG:-true}"

# Off-hours web learn (read-only Wikipedia + allowlist → action gold)
export HALIM_WEB_LEARN="${HALIM_WEB_LEARN:-true}"
export HALIM_LEARN_INCLUDE_GENERAL="${HALIM_LEARN_INCLUDE_GENERAL:-true}"
export HALIM_LEARN_INCLUDE_TRADING="${HALIM_LEARN_INCLUDE_TRADING:-true}"
export HALIM_LEARN_INCLUDE_CHARTS="${HALIM_LEARN_INCLUDE_CHARTS:-true}"
export HALIM_LEARN_INCLUDE_MACRO="${HALIM_LEARN_INCLUDE_MACRO:-true}"
export HALIM_LEARN_INCLUDE_SENTIMENT="${HALIM_LEARN_INCLUDE_SENTIMENT:-true}"
export HALIM_LEARN_INCLUDE_CODING="${HALIM_LEARN_INCLUDE_CODING:-true}"
export HALIM_LEARN_INCLUDE_LANGUAGE="${HALIM_LEARN_INCLUDE_LANGUAGE:-true}"
export HALIM_LEARN_INCLUDE_GENERATIVE="${HALIM_LEARN_INCLUDE_GENERATIVE:-true}"
export HALIM_LEARN_INCLUDE_URLS="${HALIM_LEARN_INCLUDE_URLS:-true}"
export HALIM_LEARN_PACKAGE_ON_STOP="${HALIM_LEARN_PACKAGE_ON_STOP:-true}"
export HALIM_GOOGLE_AI_DAILY_CAP="${HALIM_GOOGLE_AI_DAILY_CAP:-150}"
export HALIM_LEARN_INCLUDE_RSS="${HALIM_LEARN_INCLUDE_RSS:-true}"
export HALIM_LEARN_INCLUDE_MARKET_HOURS="${HALIM_LEARN_INCLUDE_MARKET_HOURS:-true}"
export HALIM_LEARN_GOOGLE_SNIPPETS="${HALIM_LEARN_GOOGLE_SNIPPETS:-true}"
export HALIM_LEARN_BATCH_MAX="${HALIM_LEARN_BATCH_MAX:-8}"
export HALIM_LEARN_BATCH_PAUSE_SEC="${HALIM_LEARN_BATCH_PAUSE_SEC:-1}"
export HALIM_LEARN_DURING_TRADING="${HALIM_LEARN_DURING_TRADING:-false}"
export HALIM_LEARN_LOOP="${HALIM_LEARN_LOOP:-true}"
export HALIM_LEARN_LOOP_PAUSE_SEC="${HALIM_LEARN_LOOP_PAUSE_SEC:-30}"

# Today-only raised learn cap (auto-off when UTC date != HALIM_LEARN_UNCAPPED_DATE)
export HALIM_LEARN_FETCH_DAILY_CAP="${HALIM_LEARN_FETCH_DAILY_CAP:-500}"
export HALIM_LEARN_UNCAPPED_DATE="${HALIM_LEARN_UNCAPPED_DATE:-2026-06-28}"
export HALIM_LEARN_UNCAPPED_MAX_FETCHES="${HALIM_LEARN_UNCAPPED_MAX_FETCHES:-1200}"
export HALIM_LEARN_UNCAPPED_MAX_GOLD="${HALIM_LEARN_UNCAPPED_MAX_GOLD:-40}"

# Learn cache RAG + auto LM retrain (wired in core/halim_learn_rag.py, core/halim_auto_lm.py)
export HALIM_LEARN_RAG="${HALIM_LEARN_RAG:-true}"
export HALIM_AUTO_LM_RETRAIN="${HALIM_AUTO_LM_RETRAIN:-true}"
export HALIM_AUTO_LM_MIN_NEW_PAIRS="${HALIM_AUTO_LM_MIN_NEW_PAIRS:-150}"
export HALIM_AUTO_LM_MIN_TOTAL_PAIRS="${HALIM_AUTO_LM_MIN_TOTAL_PAIRS:-400}"
export HALIM_AUTO_LM_ITERS="${HALIM_AUTO_LM_ITERS:-150}"
export HALIM_AUTO_LM_BATCH_SIZE="${HALIM_AUTO_LM_BATCH_SIZE:-1}"
export HALIM_AUTO_LM_STOP_SERVE="${HALIM_AUTO_LM_STOP_SERVE:-true}"
export HALIM_AUTO_LM_OFF_HOURS_ONLY="${HALIM_AUTO_LM_OFF_HOURS_ONLY:-true}"
export HALIM_AUTO_LM_RESTART_SERVE="${HALIM_AUTO_LM_RESTART_SERVE:-true}"
export HALIM_STANDALONE_MAINT="${HALIM_STANDALONE_MAINT:-true}"

# PPO ↔ Halim mutual distillation — always on
export HALIM_PPO_COEVOLUTION="${HALIM_PPO_COEVOLUTION:-true}"
export HALIM_PPO_DIALOGUE="${HALIM_PPO_DIALOGUE:-true}"
export HALIM_PPO_GENERATIVE_REFLECT="${HALIM_PPO_GENERATIVE_REFLECT:-true}"
export HALIM_ENTRY_LM_ENABLED="${HALIM_ENTRY_LM_ENABLED:-true}"
export HALIM_ENTRY_LM_TIMEOUT_SEC="${HALIM_ENTRY_LM_TIMEOUT_SEC:-6}"
export HALIM_ENTRY_LM_MIN_RING_SEC="${HALIM_ENTRY_LM_MIN_RING_SEC:-2.0}"
export HALIM_ENTRY_MAX_TOKENS="${HALIM_ENTRY_MAX_TOKENS:-72}"
export HALIM_ENTRY_BLEND_WEIGHT="${HALIM_ENTRY_BLEND_WEIGHT:-0.30}"
export HALIM_ENTRY_SOFT_VETO="${HALIM_ENTRY_SOFT_VETO:-true}"
export HALIM_ENTRY_VETO_MIN_CONF="${HALIM_ENTRY_VETO_MIN_CONF:-0.85}"
export HALIM_INLINE_LM_FALLBACK="${HALIM_INLINE_LM_FALLBACK:-false}"
export HALIM_PPO_DIALOGUE_TELEGRAM="${HALIM_PPO_DIALOGUE_TELEGRAM:-false}"
export HALIM_ACTION_LEARN="${HALIM_ACTION_LEARN:-true}"
export HALIM_COMPANION_LEARN="${HALIM_COMPANION_LEARN:-true}"
export HALIM_LIVE_GOLD_COLLECT="${HALIM_LIVE_GOLD_COLLECT:-true}"
export HALIM_PREPARE_SFT_ON_SHUTDOWN="${HALIM_PREPARE_SFT_ON_SHUTDOWN:-true}"

# Single canonical Colab zip — rebuilt whenever SFT changes (halim_sft.zip only)
export HALIM_AUTO_PACKAGE_COLAB="${HALIM_AUTO_PACKAGE_COLAB:-true}"
export HALIM_LEARN_PACKAGE_ON_STOP="${HALIM_LEARN_PACKAGE_ON_STOP:-true}"

# PPO teacher + sklearn proxy distillation — always on
export PPO_TEACHER_ENABLED="${PPO_TEACHER_ENABLED:-true}"
export HYBRID_DISTILL_AUTO_FAST_PATH="${HYBRID_DISTILL_AUTO_FAST_PATH:-true}"
export HYBRID_DISTILL_MIN_TRADES="${HYBRID_DISTILL_MIN_TRADES:-10}"
export OWNED_BRAIN_GIT_PUSH="${OWNED_BRAIN_GIT_PUSH:-true}"
