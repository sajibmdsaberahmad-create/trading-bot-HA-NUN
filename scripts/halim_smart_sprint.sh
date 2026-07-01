#!/usr/bin/env bash
# Halim Smart Sprint — run all phases A–D in one shot (best off-hours on M2 8GB).
#
# Usage:
#   ./scripts/halim_smart_sprint.sh              # full pipeline (off-hours) or safe subset (RTH)
#   ./scripts/halim_smart_sprint.sh --env-only   # print status; wire env for next start
#   ./scripts/halim_smart_sprint.sh --with-replay  # also start weekend replay loop (background)
#   ./scripts/halim_smart_sprint.sh --force-retrain  # MLX LoRA even if cooldown
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export TZ="America/New_York"

# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_smart_sprint_env.sh"

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi
PY="${ROOT}/venv/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"

ENV_ONLY=false
WITH_REPLAY=false
FORCE_RETRAIN=false
SKIP_V5=false
for arg in "$@"; do
  case "$arg" in
    --env-only) ENV_ONLY=true ;;
    --with-replay) WITH_REPLAY=true ;;
    --force-retrain) FORCE_RETRAIN=true ;;
    --skip-v5) SKIP_V5=true ;;
  esac
done

HANOON_LIVE=false
if pgrep -f "main.py --mode scalper" >/dev/null 2>&1; then
  HANOON_LIVE=true
fi

MARKET_OPEN=false
MARKET_OPEN="$("$PY" -c "
from core.config import BotConfig
from core.market_hours import can_trade_now
ok, _ = can_trade_now(BotConfig())
print('true' if ok else 'false')
" 2>/dev/null || echo false)"

echo "══════════════════════════════════════════════════════════════"
echo "  M. A. HALIM — Smart Sprint (all phases A–D)"
echo "  HANOON live: $HANOON_LIVE | market open: $MARKET_OPEN"
echo "══════════════════════════════════════════════════════════════"

"$PY" -c "
from core.halim_smart_sprint import print_sprint_status
from core.config import BotConfig
print_sprint_status(BotConfig())
"

if [[ "$ENV_ONLY" == "true" ]]; then
  echo ""
  echo "Env wired. Restart with: ./scripts/start_hanoon.sh"
  echo "  HALIM_SMART_SPRINT=true (default) | micro_fast blocked until child"
  exit 0
fi

# ── Phase A: v5 JSON gold + browse (off-hours or no HANOON) ─────────────────
if [[ "$SKIP_V5" != "true" ]]; then
  echo ""
  echo "▶ Phase A: v5 gold pack…"
  if [[ "$HANOON_LIVE" == "true" ]] || [[ "$MARKET_OPEN" == "true" ]]; then
    echo "  (RTH / HANOON live — skip-learn, API JSON gold only)"
    "$ROOT/scripts/halim_v5_ready.sh" --skip-learn || true
  else
    "$ROOT/scripts/halim_v5_ready.sh" || true
  fi
fi

# ── Phase A+D: export gold + SFT + Colab zip ────────────────────────────────
echo ""
echo "▶ Phase A+D: export gold + SFT + halim_sft.zip…"
export HALIM_JSON_ENTRY_API=true
"$ROOT/scripts/halim_colab_ready.sh" || true

# ── Phase A: MLX LoRA retrain (off-hours only) ───────────────────────────────
echo ""
if [[ "$MARKET_OPEN" == "true" ]] && [[ "$FORCE_RETRAIN" != "true" ]]; then
  echo "▶ Phase A: MLX retrain deferred (market open — use --force-retrain or run ./stop.sh)"
else
  echo "▶ Phase A: MLX LoRA retrain…"
  FORCE_FLAG=""
  [[ "$FORCE_RETRAIN" == "true" ]] && FORCE_FLAG=", force=True"
  "$PY" -c "
from core.config import BotConfig
from core.halim_auto_lm import run_auto_retrain_sync
r = run_auto_retrain_sync(BotConfig(), trigger='smart_sprint'${FORCE_FLAG})
import json
print(json.dumps({k: r.get(k) for k in ('ok','reason','trigger') if k in r}, indent=2))
" || echo "  ⚠️  retrain skipped or failed — check models/halim_lm_evolve_state.json"
fi

# ── Phase C: replay gold toward child ─────────────────────────────────────────
COUNCIL_N="$("$PY" -c "
from pathlib import Path
p = Path('models/council_training_dataset.jsonl')
print(sum(1 for ln in p.read_text().splitlines() if ln.strip()) if p.is_file() else 0)
" 2>/dev/null || echo 0)"
TARGET="${BRAIN_CHILD_DATASET_TARGET:-200}"
GAP=$((TARGET - COUNCIL_N))
if [[ "$GAP" -gt 0 ]]; then
  echo ""
  echo "▶ Phase C: council dataset $COUNCIL_N / $TARGET (need $GAP more for child)"
  if [[ "$WITH_REPLAY" == "true" ]] && [[ "$HANOON_LIVE" != "true" ]]; then
    echo "  Starting weekend replay loop in background…"
    nohup "$ROOT/scripts/weekend_replay_train.sh" train >>"$ROOT/logs/SMART_SPRINT_REPLAY.log" 2>&1 &
    echo "  tail -f logs/SMART_SPRINT_REPLAY.log"
  else
    echo "  Run when HANOON stopped: ./scripts/halim_smart_sprint.sh --with-replay"
    echo "  Or: ./scripts/weekend_replay_train.sh train"
  fi
else
  echo ""
  echo "▶ Phase C: council dataset ≥ $TARGET — child stage eligible on next evolution"
fi

# ── Ensure halim serve ────────────────────────────────────────────────────────
if ! curl -sf "http://127.0.0.1:8765/v1/status" >/dev/null 2>&1; then
  echo ""
  echo "▶ Restarting halim serve…"
  "$ROOT/scripts/halim_stop.sh" 2>/dev/null || true
  "$ROOT/scripts/halim_start.sh" &
  sleep 8
fi

echo ""
"$PY" -c "
from core.halim_smart_sprint import print_sprint_status
from core.config import BotConfig
print_sprint_status(BotConfig())
"

echo ""
echo "Done. Live trading: ./scripts/start_hanoon.sh  (sprint env ON by default)"
echo "Colab: upload halim_sft.zip → Drive → halim/colab/halim_toddler_train.ipynb"

if [[ -t 0 ]]; then
  read -r -p "Press Enter to close…" _ || true
fi
