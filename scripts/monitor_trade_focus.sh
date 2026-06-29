#!/usr/bin/env bash
# Lightweight RAM/trading focus monitor — read-only; tails logs/HANOON.log for trading events.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

interval="${MONITOR_INTERVAL_SEC:-30}"
max_rounds="${MONITOR_MAX_ROUNDS:-0}"
LOG_FILE="${HANOON_LOG_PATH:-$ROOT/logs/HANOON.log}"

round=0
while true; do
  round=$((round + 1))
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  ram_free_mb="$(vm_stat 2>/dev/null | awk '/Pages free/ {printf "%.0f", $3*4096/1024/1024}')"
  ram_active_mb="$(vm_stat 2>/dev/null | awk '/Pages active/ {printf "%.0f", $3*4096/1024/1024}')"
  hanoon_pid="$(pgrep -f 'main.py --mode scalper' | head -1)"
  halim_pid="$(pgrep -f 'halim/serve.py' | head -1)"
  learn_pid="$(pgrep -f 'halim_learn_browse' | head -1)"
  ollama_pid="$(pgrep -f 'ollama serve' | head -1)"

  status="OK"
  [[ -z "$hanoon_pid" ]] && status="NO_HANOON"
  [[ -n "$learn_pid" ]] && status="LEARN_COMPETING"
  [[ -n "$ollama_pid" ]] && status="OLLAMA_HOG"

  echo "[$ts] focus=$status | HANOON=${hanoon_pid:-—} Halim=${halim_pid:-—} | free=${ram_free_mb:-?}MB active=${ram_active_mb:-?}MB | log=$LOG_FILE"

  if [[ -f models/loss_streak_review.jsonl ]]; then
    tail -1 models/loss_streak_review.jsonl 2>/dev/null | python3 -c "
import json,sys
try:
    r=json.load(sys.stdin)
    print(f\"  loss_streak: streak={r.get('streak')} applied={r.get('mutations_applied')} conf={r.get('resume_confidence')}\")
except Exception: pass
" 2>/dev/null || true
  fi

  if [[ -f "$LOG_FILE" ]]; then
    echo "  recent trading:"
    tail -400 "$LOG_FILE" 2>/dev/null | grep -v "📡 Streams" | grep -E \
      "ENTRY|EXIT|WATCH|cooldown|guard:|loss_pressure|bypass|PPO HOLD|Halim soft|COUNCIL|SPIKE|QUALITY|IB connected|Shutdown" \
      | tail -6 | sed 's/^/    /' || true
  else
    echo "  (log not found yet — start HANOON with ./scripts/start_hanoon.sh)"
  fi

  if [[ "$max_rounds" -gt 0 && "$round" -ge "$max_rounds" ]]; then
    break
  fi
  sleep "$interval"
done
