#!/usr/bin/env bash
# Watch HANOON.log until RTH session end (~16:00 ET) or bot shutdown.
set -uo pipefail
LOG="/Users/mdsabersajib/Downloads/tradingbot/logs/HANOON.log"
OUT="/Users/mdsabersajib/Downloads/tradingbot/logs/session_watch.log"
POLL=25
MAX_LOOPS=150

echo "=== SESSION WATCH START $(date -u '+%Y-%m-%d %H:%M:%S UTC') ===" >> "$OUT"

line_count=$(wc -l < "$LOG" | tr -d ' ')
errors=0; hunts=0; green=0; war_exits=0; wrong=0

for ((i=1; i<=MAX_LOOPS; i++)); do
  if ! pgrep -f "main.py --mode scalper" >/dev/null 2>&1; then
    echo "[$(date -u '+%H:%M:%S')] BOT STOPPED" >> "$OUT"
    break
  fi

  total=$(wc -l < "$LOG" | tr -d ' ')
  if (( total > line_count )); then
    new_lines=$(tail -n +$((line_count + 1)) "$LOG")
    while IFS= read -r line; do
      echo "$line" | rg -qi "ERROR|Traceback|monitor failed|Fatal error" && { echo "ERR|$line" >> "$OUT"; ((errors++)) || true; }
      echo "$line" | rg -qi "PROFIT HUNT" && { echo "HUNT|$line" >> "$OUT"; ((hunts++)) || true; }
      echo "$line" | rg -qi "GREEN LOCK" && { echo "LOCK|$line" >> "$OUT"; ((green++)) || true; }
      echo "$line" | rg -qi "WAR EXIT" && { echo "WAR|$line" >> "$OUT"; ((war_exits++)) || true; }
      echo "$line" | rg -qi "Wrong tick|no open slot|146\.[0-9]+%" && { echo "WARN|$line" >> "$OUT"; ((wrong++)) || true; }
      echo "$line" | rg -qi "session_shutdown|Graceful shutdown|Signal 15 received|day session ended|Market: CLOSED" && echo "END|$line" >> "$OUT"
    done <<< "$new_lines"
    line_count=$total
  fi

  if tail -n 30 "$LOG" | rg -qi "session_shutdown|Graceful shutdown|Signal 15 received|day session ended"; then
    echo "[$(date -u '+%H:%M:%S')] SESSION END DETECTED" >> "$OUT"
    break
  fi
  if tail -1 "$LOG" | rg -q "2026-06-30 16:0[5-9]|2026-06-30 16:[1-5]"; then
    echo "[$(date -u '+%H:%M:%S')] PAST 16:05 ET" >> "$OUT"
    break
  fi

  if (( i % 8 == 0 )); then
    echo "[hb $(date -u '+%H:%M:%S')] $(tail -1 "$LOG" | cut -c1-80) | e=$errors h=$hunts g=$green w=$war_exits x=$wrong" >> "$OUT"
  fi
  sleep "$POLL"
done

echo "=== WATCH END $(date -u '+%Y-%m-%d %H:%M:%S UTC') errors=$errors hunts=$hunts green=$green war_exits=$war_exits wrong=$wrong ===" >> "$OUT"
