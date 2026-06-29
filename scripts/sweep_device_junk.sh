#!/usr/bin/env bash
# Thorough device sweep — removed IDE hogs, piled-up junk, HANOON cruft.
# Safe during trading: never touches IB Gateway, venv, active Halim/HANOON processes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "══════════════════════════════════════════════════════════════"
echo "  Device sweep — trading-safe junk removal"
echo "══════════════════════════════════════════════════════════════"

# 1. Permanent IDE hog removal + settings cleanup
if [[ -x "$ROOT/scripts/remove_ide_ram_hogs.sh" ]]; then
  "$ROOT/scripts/remove_ide_ram_hogs.sh" || true
fi

# 2. Mac cleaner — HANOON + IDE junk + stale Cursor logs
echo ""
echo "🧹 Mac cleaner (HANOON + IDE junk)…"
python3 "$ROOT/mac-cleaner/clean.py" hanoon --clean --yes 2>&1 | sed 's/^/  /'

# 3. Light caches safe while trading (pip, cursor shipit)
echo ""
echo "🧹 Light system caches…"
python3 "$ROOT/mac-cleaner/clean.py" pip cursor_shipit --clean --yes 2>&1 | sed 's/^/  /'

# 4. Ollama disk prune if installed (Halim uses MLX, not Ollama)
if command -v ollama >/dev/null 2>&1; then
  echo ""
  echo "🧹 Ollama prune (unused — Halim is MLX)…"
  ollama prune -f 2>/dev/null || true
  pkill -TERM -f "ollama serve" 2>/dev/null || true
fi

# 5. Ensure trading stack pid files match live processes
for pf in hanoon halim_serve git_sync; do
  f="$ROOT/logs/${pf}.pid"
  if [[ -f "$f" ]]; then
    pid=$(tr -d '[:space:]' <"$f")
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$f"
      echo "  🧹 Cleared stale logs/${pf}.pid"
    fi
  fi
done

# 6. ppo symlink if cleanup removed root copy
if [[ -f "$ROOT/models/ppo_trader.zip" && ! -e "$ROOT/ppo_trader.zip" ]]; then
  ln -sf models/ppo_trader.zip "$ROOT/ppo_trader.zip"
  echo "  🔗 Linked ppo_trader.zip → models/"
fi

echo ""
echo "✅ Device sweep complete"
