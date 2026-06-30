#!/usr/bin/env bash
# Auto-download IB replay CSVs when farm is missing or fully trained.
# Used by start_replay_live.sh (and weekend loop). Disable: REPLAY_AUTO_DOWNLOAD=false
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REPLAY_AUTO_DOWNLOAD="${REPLAY_AUTO_DOWNLOAD:-true}"
REPLAY_IB_DAYS="${REPLAY_IB_DAYS:-60}"
REPLAY_ROOT="${REPLAY_DATA_DIR:-$ROOT/data/replay}"
IB_HOST="${IB_HOST:-127.0.0.1}"
IB_PORT="${IB_PORT:-4002}"
CLIENT_ID="${CLIENT_ID:-${IB_CLIENT_ID:-1}}"

if [[ "$REPLAY_AUTO_DOWNLOAD" != "true" && "$REPLAY_AUTO_DOWNLOAD" != "1" ]]; then
  exit 0
fi

if pgrep -f "main.py.*--mode scalper" >/dev/null 2>&1; then
  echo "❌ Live HANOON (scalper) is running — stop it first: ./stop.sh"
  exit 1
fi

if pgrep -f "main.py --mode replay-live" >/dev/null 2>&1; then
  echo "❌ Replay is already running — stop first: ./stop_replay.sh"
  exit 1
fi

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi
export PYTHONPATH="${ROOT}/halim:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export REPLAY_DATA_DIR="$REPLAY_ROOT"

NEED_DL="$(python3 -c "
import os
from pathlib import Path
root = Path(os.getenv('REPLAY_DATA_DIR', 'data/replay'))
intraday = root / 'intraday'
has_csv = any(intraday.glob('*_1min.csv')) if intraday.is_dir() else False
if not has_csv:
    print('1')
else:
    try:
        from core.replay_consumption import farm_has_unconsumed_data
        print('0' if farm_has_unconsumed_data() else '1')
    except Exception:
        print('1')
" 2>/dev/null || echo "1")"

if [[ "$NEED_DL" != "1" ]]; then
  echo "✓ Replay IB farm ready (unconsumed bars — skip download)"
  exit 0
fi

# Farm empty or fully consumed — fresh download; drop stale consumption ledger so bars aren't skipped
if [[ -f "$ROOT/models/replay_consumption.jsonl" ]]; then
  BK="$ROOT/models/replay_consumption.jsonl.bak.$(date +%s)"
  cp "$ROOT/models/replay_consumption.jsonl" "$BK" 2>/dev/null || true
  : > "$ROOT/models/replay_consumption.jsonl"
  echo "  🔄 Cleared replay consumption ledger (full re-download)"
fi

echo ""
echo "▶ Auto-downloading IB replay data (${REPLAY_IB_DAYS} days)…"
echo "   Gateway: ${IB_HOST}:${IB_PORT}  client_id=${CLIENT_ID}"
echo "   Output:  ${REPLAY_ROOT}/intraday/"
echo ""

if ! PYTHONPATH=. python3 -u "$ROOT/scripts/download_ib_replay_data.py" \
    --days "$REPLAY_IB_DAYS" \
    --client-id "$CLIENT_ID" \
    --port "$IB_PORT" \
    --merge; then
  echo ""
  echo "❌ IB replay download failed."
  echo "   • Start IB Gateway (paper) and log in"
  echo "   • Stop live scalper if running: ./stop.sh"
  echo "   • Retry: ./scripts/start_replay_live.sh"
  echo ""
  exit 1
fi

if [[ -z "$(find "$REPLAY_ROOT/intraday" -maxdepth 1 -name '*_1min.csv' -print -quit 2>/dev/null)" ]]; then
  echo "❌ Download finished but no *_1min.csv files found under ${REPLAY_ROOT}/intraday"
  exit 1
fi

echo "✅ Replay IB farm ready — starting replay…"
echo ""
