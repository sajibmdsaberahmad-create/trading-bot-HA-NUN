#!/usr/bin/env bash
# Free RAM for HANOON + Halim on low-memory Macs (8GB).
# Stops non-essential bot/IDE sidecars; never touches IB Gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}' || echo 8192)
AGGRESSIVE="${HALIM_FREE_RAM_AGGRESSIVE:-auto}"
if [[ "$AGGRESSIVE" == "auto" ]]; then
  [[ "$RAM_MB" -le 12288 ]] && AGGRESSIVE=true || AGGRESSIVE=false
fi

freed_msg() { echo "  🧹 $1"; }

echo "🧠 Free RAM for trading (${RAM_MB}MB system, aggressive=${AGGRESSIVE})…"

# Permanent removal of Amazon Q / Gemini / Cloud Code (idempotent)
if [[ -x "$ROOT/scripts/remove_ide_ram_hogs.sh" ]]; then
  "$ROOT/scripts/remove_ide_ram_hogs.sh" || true
fi

# ── Halim learn loop (LEARN_START.command) — competes with MLX during RTH ──
if pgrep -f "halim_learn_browse" >/dev/null 2>&1; then
  pkill -TERM -f "halim_learn_browse" 2>/dev/null || true
  sleep 1
  pkill -KILL -f "halim_learn_browse" 2>/dev/null || true
  freed_msg "Stopped Halim learn-browse loop"
fi

# ── Ollama (not used — Halim MLX only) ──
if pgrep -f "ollama serve" >/dev/null 2>&1; then
  pkill -TERM -f "ollama serve" 2>/dev/null || true
  freed_msg "Stopped Ollama serve"
fi

# ── Stale pid files (dead processes holding confusion, not RAM) ──
for pf in "$LOG_DIR/hanoon.pid" "$LOG_DIR/halim_serve.pid" "$LOG_DIR/git_sync.pid" "$LOG_DIR/halim_telegram.pid" "$LOG_DIR/halim_watchdog.pid"; do
  if [[ -f "$pf" ]]; then
    pid=$(tr -d '[:space:]' <"$pf" 2>/dev/null || true)
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pf"
      freed_msg "Cleared stale $(basename "$pf")"
    fi
  fi
done

# ── Orphan duplicate bot processes (not the active start_hanoon parent) ──
_hanoon_keep_pids() {
  local p
  for p in "${HANOON_START_PID:-}" "$$" "${PPID:-}"; do
    [[ -n "$p" ]] && echo "$p"
  done
}
_is_kept_hanoon_pid() {
  local pid="$1" keep
  for keep in $(_hanoon_keep_pids); do
    [[ "$pid" == "$keep" ]] && return 0
  done
  return 1
}
# Match only the real launcher script — not nohup/zsh wrappers whose cmdline embeds the path.
_hanoon_launch_pgrep() {
  pgrep -f '[./]scripts/start_hanoon\.sh([[:space:]]|$)' 2>/dev/null || true
}
if [[ -n "${HANOON_START_PID:-}" ]]; then
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    _is_kept_hanoon_pid "$pid" && continue
    kill -TERM "$pid" 2>/dev/null || true
    freed_msg "Stopped stale start_hanoon.sh (pid $pid)"
  done < <(_hanoon_launch_pgrep)
else
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    kill -TERM "$pid" 2>/dev/null || true
    freed_msg "Stopped orphan start_hanoon.sh (pid $pid)"
  done < <(_hanoon_launch_pgrep)
fi
for pattern in "grandmaster_push_" "halim-auto-lm"; do
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    kill -TERM "$pid" 2>/dev/null || true
    freed_msg "Stopped orphan $pattern (pid $pid)"
  done < <(pgrep -f "$pattern" 2>/dev/null || true)
done

# ── IDE sidecars (should be gone after remove_ide_ram_hogs; kill zombies) ──
if [[ "$AGGRESSIVE" == "true" ]]; then
  for _pass in 1 2; do
    for pattern in \
      "Amazon Q Helper" \
      "cloudcode_cli duet" \
      "geminicodeassist.*/agent/a2a-server" \
      ; do
      if pgrep -f "$pattern" >/dev/null 2>&1; then
        pkill -TERM -f "$pattern" 2>/dev/null || true
        freed_msg "Stopped IDE sidecar: $pattern"
      fi
    done
    [[ "$_pass" -eq 1 ]] && sleep 2
  done
fi

# ── Ensure single PPO path (avoid duplicate model copies in RAM later) ──
if [[ -f "$ROOT/models/ppo_trader.zip" && ! -e "$ROOT/ppo_trader.zip" ]]; then
  ln -sf models/ppo_trader.zip "$ROOT/ppo_trader.zip"
  freed_msg "Linked ppo_trader.zip → models/ (no duplicate on disk)"
fi

echo "✅ RAM prep done — start HANOON/Halim now"
