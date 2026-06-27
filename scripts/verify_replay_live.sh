#!/usr/bin/env bash
# Quick verification that replay-live pipeline is healthy (fast mode, ~2 min).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export TZ="America/New_York"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [[ -d venv ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

export PYTHONPATH=.
export REPLAY_DATA_DIR="${REPLAY_DATA_DIR:-$ROOT/data/replay}"
export REPLAY_LIVE=true
export REPLAY_BLOCK_IB=true
export REPLAY_REALTIME_PACE=false
export REPLAY_TIME_DILATION_MS=0
export REPLAY_START="${REPLAY_START:-2026-06-25}"
export REPLAY_END="${REPLAY_END:-2026-06-26}"
export COUNCIL_ENABLED=false

PASS=0
FAIL=0

check() {
  local name="$1"
  shift
  if "$@"; then
    echo "  ✅ $name"
    PASS=$((PASS + 1))
  else
    echo "  ❌ $name"
    FAIL=$((FAIL + 1))
  fi
}

echo "══════════════════════════════════════════════════════════════"
echo "  REPLAY-LIVE VERIFICATION"
echo "══════════════════════════════════════════════════════════════"

check "intraday data dir" test -d "$REPLAY_DATA_DIR/intraday"
check "SOFI 1-min CSV" test -f "$REPLAY_DATA_DIR/intraday/SOFI_1min.csv"
check "live PPO model" test -f "$ROOT/ppo_trader.zip"

python -c "
from core.replay_data import load_replay_intraday, resolve_replay_dir
from core.replay_clock import activate, set_replay_time, deactivate
from core.market_hours import now_et, get_market_state
from core.config import BotConfig
import pandas as pd
root = resolve_replay_dir()
df = load_replay_intraday('SOFI', root=root, start='2026-06-25', end='2026-06-25')
assert len(df) >= 300, f'expected >=300 bars, got {len(df)}'
activate()
set_replay_time(pd.Timestamp('2026-06-25 10:30:00', tz='America/New_York').to_pydatetime())
assert get_market_state(BotConfig()) == 'open'
deactivate()
print('  ✅ replay imports + date filter + virtual clock')
" && PASS=$((PASS + 1)) || FAIL=$((FAIL + 1))

run_replay() {
  local ticker="$1"
  local out
  out=$(PYTHONPATH=. python main.py --mode replay-live --ticker "$ticker" --cash 1000 2>&1) || true
  if echo "$out" | grep -q "REPLAY-LIVE SESSION COMPLETE"; then
    echo "  ✅ replay-live run $ticker"
    return 0
  fi
  echo "  ❌ replay-live run $ticker"
  echo "$out" | tail -6
  return 1
}

for T in SOFI PLTR MARA SPY; do
  if run_replay "$T"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
  fi
done

out=$(bash scripts/start_replay_live.sh SOFI 2>&1) || true
if echo "$out" | grep -q "REPLAY-LIVE SESSION COMPLETE"; then
  echo "  ✅ start_replay_live.sh"
  PASS=$((PASS + 1))
else
  echo "  ❌ start_replay_live.sh"
  echo "$out" | tail -8
  FAIL=$((FAIL + 1))
fi

echo
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
echo "All replay-live checks passed."
