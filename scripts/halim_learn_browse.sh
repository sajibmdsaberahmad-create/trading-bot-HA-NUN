#!/usr/bin/env bash
# Halim off-hours learn — browse Wikipedia + allowlisted sites → action gold.
#
# Best while IB Gateway is in maintenance / market closed.
# Usage:
#   ./scripts/halim_learn_browse.sh              # continuous loop (default for LEARN_START)
#   ./scripts/halim_learn_browse.sh --once       # single batch then exit
#   ./scripts/halim_learn_browse.sh wiki:Risk_management
#   HALIM_LEARN_BATCH_MAX=12 ./scripts/halim_learn_browse.sh --once
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

export HALIM_WEB_LEARN=true
export HALIM_OPERATOR_SETTINGS=true
export HALIM_GOOGLE_AI_SEARCH="${HALIM_GOOGLE_AI_SEARCH:-true}"
export HALIM_LEARN_INCLUDE_GENERAL="${HALIM_LEARN_INCLUDE_GENERAL:-true}"
export HALIM_LEARN_GOOGLE_SNIPPETS="${HALIM_LEARN_GOOGLE_SNIPPETS:-true}"

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

if pgrep -f "main.py --mode (scalper|replay-live)" >/dev/null 2>&1 \
   && [[ "${HALIM_LEARN_DURING_TRADING:-false}" != "true" ]]; then
  echo "🎯 Trading/replay is running — learn browse skipped (algo has full focus)."
  echo "   Stop HANOON/replay first, or: HALIM_LEARN_DURING_TRADING=true $0"
  if [[ -t 0 ]]; then
    read -r -p "Press Enter to close…" _ || true
  fi
  exit 0
fi

MODE="${1:-}"
TOPIC=""
LOOP="${HALIM_LEARN_LOOP:-true}"

if [[ "$MODE" == "--once" ]]; then
  LOOP=false
  shift || true
  TOPIC="${1:-}"
elif [[ "$MODE" == "--loop" ]]; then
  LOOP=true
  shift || true
  TOPIC="${1:-}"
elif [[ -n "$MODE" && "$MODE" != wiki:* && "$MODE" != http* ]]; then
  TOPIC="$MODE"
  LOOP=false
elif [[ -n "$MODE" ]]; then
  TOPIC="$MODE"
  LOOP=false
fi

echo "══════════════════════════════════════════════════════════════"
echo "  M. A. HALIM — learn browse (read-only → action gold)"
echo "  Mode: $([[ "$LOOP" == true ]] && echo 'continuous loop' || echo 'single batch')"
echo "  Max pages/batch: ${HALIM_LEARN_BATCH_MAX:-8} | Google snippets: ${HALIM_LEARN_GOOGLE_SNIPPETS:-true}"
echo "  Sources: wiki · investopedia/SEC · RSS · market hours · coding docs"
if [[ "$LOOP" == true ]]; then
  if [[ "${HALIM_LEARN_LOOP_PAUSE_SEC:-0}" != "0" ]] && [[ -n "${HALIM_LEARN_LOOP_PAUSE_SEC:-}" ]]; then
    echo "  Pause between batches: ${HALIM_LEARN_LOOP_PAUSE_SEC}s | Ctrl+C to stop"
  else
    echo "  Back-to-back batches (rotate sources, no wait) | Ctrl+C to stop"
  fi
fi
echo "══════════════════════════════════════════════════════════════"

if [[ "$LOOP" == true && -z "$TOPIC" ]]; then
  PYTHONPATH="$ROOT/halim:$ROOT" python3 <<'PY' || true
import sys
from core.config import BotConfig
from core.halim_learn_browse import run_learn_browse_loop

try:
    run_learn_browse_loop(BotConfig())
except KeyboardInterrupt:
    sys.exit(0)
PY
  if [[ -t 0 ]]; then
    echo ""
    read -r -p "Press Enter to close…" _ || true
  fi
  exit 0
fi

PYTHONPATH="$ROOT/halim:$ROOT" python3 - "$TOPIC" <<'PY'
import json
import sys
from core.config import BotConfig
from core.halim_learn_browse import run_learn_browse_cycle

cfg = BotConfig()
arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if arg:
    if arg.startswith("wiki:") or arg.startswith("http"):
        topics = [arg]
    else:
        topics = [f"wiki:{arg.replace(' ', '_')}"]
    r = run_learn_browse_cycle(cfg, topics=topics, max_pages=1)
else:
    r = run_learn_browse_cycle(cfg)
print(json.dumps(r, indent=2))
added = (r.get("export_gold") or {}).get("added", 0)
skipped = (r.get("export_gold") or {}).get("skipped", 0)
print(f"\n✅ Done — {r.get('pages_ok', 0)} pages, gold +{added} pairs ({skipped} already known)", file=sys.stderr)
PY

if [[ -t 0 ]]; then
  echo ""
  read -r -p "Press Enter to close…" _ || true
fi
