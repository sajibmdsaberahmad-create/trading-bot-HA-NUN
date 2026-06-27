#!/usr/bin/env bash
# Run HANOON with M. A. Halim native mode — no external LLM (Groq/Gemini/Ollama).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export HALIM_NATIVE=true
export HALIM_NO_EXTERNAL_LLM=true
export HALIM_AUTO_PUSH=true
export GIT_PUSH_DURING_SESSION=true
export OWNED_BRAIN_GIT_PUSH=true
export COUNCIL_ENABLED=false
export GENERATIVE_THINKING_ENABLED=false
export TRADING_COPILOT_ENABLED=false
export PPO_TEACHER_ENABLED=true
export OWNED_BRAIN_DEVICE="${OWNED_BRAIN_DEVICE:-m2_8gb}"

echo "══════════════════════════════════════════════════════════════"
echo "  M. A. Halim NATIVE — owned students only, no external LLM"
echo "══════════════════════════════════════════════════════════════"

exec ./scripts/start_replay_live.sh "${1:-day}"
