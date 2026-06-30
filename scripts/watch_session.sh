#!/usr/bin/env bash
# Watch HANOON.log until RTH session end (~16:00 ET) or bot shutdown.
set -uo pipefail
LOG="/Users/mdsabersajib/Downloads/tradingbot/logs/HANOON.log"
OUT="/Users/mdsabersajib/Downloads/tradingbot/logs/session_watch.log"
MARKER="2026-06-30 15:12"
POLL=30
MAX_LOOPS=120  # ~60 min

echo "=== SESSION WATCH START $(date -u '+%Y-%m-%d %H:%M:%S UTC') ===" >> "$OUT"
echo "marker=$MARKER poll=${POLL}s" >> "$OUT"

last_line=""
errors=0
hunts=0
green=0
war_exits=0

for ((i=1; i<=MAX_LOOPS; i++)); do
  if ! pgrep -f "main.py --mode scalper" >/dev/null 2>&1; then
    echo "[$(date -u '+%H:%M:%S')] BOT STOPPED" >> "$OUT"
    break
  fi

  chunk=$(rg "2026-06-30 15:" "$LOG" 2>/dev/null | tail -n 400 || true)

  # Session end signals
  if echo "$chunk" | rg -qi "session_shutdown|market closed|RTH close|Graceful shutdown|stop_hanoon|day session ended|after.hours"; then
    echo "[$(date -u '+%H:%M:%S')] SESSION END DETECTED" >> "$OUT"
    echo "$chunk" | rg -i "session_shutdown|market closed|Graceful shutdown|stop_hanoon|day session ended" | tail -n 5 >> "$OUT"
    break
  fi

  # After 16:05 ET in log timestamps
  if echo "$chunk" | tail -1 | rg -q "2026-06-30 16:0[5-9]|2026-06-30 16:[1-5]"; then
    echo "[$(date -u '+%H:%M:%S')] PAST 16:05 ET — ending watch" >> "$OUT"
    break
  fi

  new=$(echo "$chunk" | rg -i "ERROR|Traceback|monitor failed|Wrong tick|PROFIT HUNT|GREEN LOCK|WAR EXIT|no open slot|146\.[0-9]+%|Fatal error" || true)
  if [[ -n "$new" ]]; then
    while IFS= read -r line; do
      [[ "$line" == "$last_line" ]] && continue
      echo "$line" >> "$OUT"
      echo "$line" | rg -qi "ERROR|Traceback|monitor failed|Fatal" && ((errors++)) || true
      echo "$line" | rg -qi "PROFIT HUNT" && ((hunts++)) || true
      echo "$line" | rg -qi "GREEN LOCK" && ((green++)) || true
      echo "$line" | rg -qi "WAR EXIT" && ((war_exits++)) || true
      last_line="$line"
    done <<< "$new"
  fi

  # Heartbeat every 5 min
  if (( i % 10 == 0 )); then
    tail -1 "$LOG" >> "$OUT"
    echo "[heartbeat $i] errors=$errors hunts=$hunts green=$green war_exits=$war_exits" >> "$OUT"
  fi

  sleep "$POLL"
done

echo "=== SESSION WATCH END $(date -u '+%Y-%m-%d %H:%M:%S UTC') errors=$errors hunts=$hunts green=$green war_exits=$war_exits ===" >> "$OUT"
