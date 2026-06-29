#!/bin/bash
# Life Engine deep monitor — verdicts, smart stack health, anti-patterns
# Usage: ./scripts/monitor_life_engine.sh [minutes]   (default 30)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MINUTES="${1:-30}"
LOG="${HANOON_LOG:-$ROOT/HANOON.log}"
VERDICTS="$ROOT/models/smart_stack_verdicts.jsonl"
OUT="$ROOT/runtime/life_engine_monitor_$(date +%Y%m%d_%H%M%S).txt"
INTERVAL=300
SNAPS=$(( (MINUTES * 60) / INTERVAL ))
[[ "$SNAPS" -lt 1 ]] && SNAPS=1

START_LINES=$(wc -l < "$LOG" 2>/dev/null || echo 0)
START_VERDICTS=$(wc -l < "$VERDICTS" 2>/dev/null || echo 0)
START_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

report_section() {
  local title="$1"
  shift
  echo ""
  echo "=== $title ==="
  "$@" 2>/dev/null || echo "(none)"
}

snapshot() {
  local n="$1"
  local NOW LINES NEW VNEW
  NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  LINES=$(wc -l < "$LOG" 2>/dev/null || echo 0)
  NEW=$((LINES - START_LINES))
  VNEW=$(( $(wc -l < "$VERDICTS" 2>/dev/null || echo 0) - START_VERDICTS ))

  echo ""
  echo "--- SNAPSHOT $n @ $NOW ---"
  echo "New log lines: $NEW | New verdicts: $VNEW"

  if [[ -f "$LOG" && "$NEW" -gt 0 ]]; then
    local TAIL
    TAIL=$(tail -n "$NEW" "$LOG")

    echo "$TAIL" | grep -c "LIFE ENGINE\|SMART STACK" | xargs -I{} echo "Life engine banners: {}"
    echo "$TAIL" | grep -c "escalating to Halim" | xargs -I{} echo "PPO HOLD escalations: {}"
    echo "$TAIL" | grep -c "ppo_hold_skip" | xargs -I{} echo "ppo_hold_skip (BAD if >0): {}"
    echo "$TAIL" | grep -c "GATE advisory" | xargs -I{} echo "Gate advisories: {}"
    echo "$TAIL" | grep -c "teacher skip" | xargs -I{} echo "Teacher skips: {}"
    echo "$TAIL" | grep -c "COUNCIL" | xargs -I{} echo "Council events: {}"
    echo "$TAIL" | grep -c "attempting entry" | xargs -I{} echo "Entry attempts: {}"
    echo "$TAIL" | grep -c "🎯 ENTRY\|POST-ENTRY" | xargs -I{} echo "Fills/opened: {}"
    echo "$TAIL" | grep -c "AI skip" | xargs -I{} echo "AI skips: {}"
    echo "$TAIL" | grep -c "war:posture" | xargs -I{} echo "War posture: {}"
    echo "$TAIL" | grep -c "RAM_LIVE_ONLY\|RAM pressure during live" | xargs -I{} echo "RAM-live notes: {}"
    echo "$TAIL" | grep -c "Local cleanup done" | xargs -I{} echo "Disk cleanup (BAD if live): {}"
    echo "$TAIL" | grep -ciE "error|traceback" | xargs -I{} echo "Errors: {}"

    echo "Last pipeline skips:"
    echo "$TAIL" | grep "AI skip" | tail -5 || echo "(none)"
  fi

  if [[ -f "$VERDICTS" && "$VNEW" -gt 0 ]]; then
    echo "Recent verdicts (last 5 new):"
    tail -n "$VNEW" "$VERDICTS" | tail -5 | python3 -c "
import sys, json
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    try:
        r=json.loads(line)
        print(f\"  {r.get('ticker')} enter={r.get('enter')} pipe={r.get('pipeline','')[:40]} ppo={r.get('ppo_action')}@{r.get('ppo_conf',0):.0%} halim={r.get('halim_status','')}\")
    except Exception:
        print('  (parse err)', line[:80])
" 2>/dev/null || tail -3 "$VERDICTS"
  fi
}

{
  echo "HANOON LIFE ENGINE MONITOR"
  echo "Started: $START_TS | Duration: ${MINUTES}m | Snapshots: $SNAPS"
  echo "Log: $LOG (from line $START_LINES)"
  echo "Verdicts: $VERDICTS (from line $START_VERDICTS)"
  echo "========================================"
} > "$OUT"

for i in $(seq 1 "$SNAPS"); do
  snapshot "$i" >> "$OUT"
  [[ "$i" -lt "$SNAPS" ]] && sleep "$INTERVAL"
done

{
  report_section "FINAL VERDICT SUMMARY" bash -c "
    if [[ -f '$VERDICTS' ]]; then
      tail -n +\$((START_VERDICTS+1)) '$VERDICTS' | python3 -c \"
import sys, json
from collections import Counter
rows=[json.loads(l) for l in sys.stdin if l.strip()]
if not rows:
    print('No new verdicts')
    raise SystemExit
enter=sum(1 for r in rows if r.get('enter'))
skip=len(rows)-enter
print(f'Total: {len(rows)} | enter={enter} skip={skip}')
pipes=Counter(str(r.get('pipeline',''))[:50] for r in rows)
print('Pipelines:', dict(pipes.most_common(8)))
ppo_hold=sum(1 for r in rows if r.get('ppo_action')==0)
ppo_buy=sum(1 for r in rows if r.get('ppo_action')==1)
print(f'PPO: HOLD={ppo_hold} BUY={ppo_buy}')
halim=Counter(str(r.get('halim_status','')) for r in rows)
print('Halim status:', dict(halim.most_common(6)))
bad=[r for r in rows if 'ppo_hold_skip' in str(r.get('pipeline',''))]
print(f'ppo_hold_skip verdicts (BAD): {len(bad)}')
\"
    else
      echo 'Verdict file missing'
    fi
  "

  report_section "ANTI-PATTERN SCAN (log)" bash -c "
    NEW=\$(( \$(wc -l < '$LOG') - START_LINES ))
    [[ \$NEW -gt 0 ]] || exit 0
    T=\$(tail -n \$NEW '$LOG')
    echo \"ppo_hold_skip: \$(echo \"\$T\" | grep -c ppo_hold_skip || true)\"
    echo \"disk cleanup during session: \$(echo \"\$T\" | grep -c 'Local cleanup done' || true)\"
    echo \"MTF hard block (legacy): \$(echo \"\$T\" | grep -c 'MTF block' || true)\"
    echo \"REGIME hard block (legacy): \$(echo \"\$T\" | grep -c 'REGIME block' || true)\"
  "

  echo ""
  echo "Report: $OUT"
  echo "Ended: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$OUT"

echo "Life engine monitor done → $OUT"
cat "$OUT"
