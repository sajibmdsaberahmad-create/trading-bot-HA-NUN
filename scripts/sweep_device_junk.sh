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

# 3b. Halim Colab checkpoint junk (optimizer shards — not needed for MLX inference)
echo ""
echo "🧹 Halim Colab training junk…"
python3 - <<PY
import sys
from pathlib import Path
sys.path.insert(0, str(Path("$ROOT").resolve()))
from core.local_cleanup import prune_halim_colab_artifacts
freed = prune_halim_colab_artifacts()
print(f"  freed ~{freed / (1024*1024):.1f}MB")
PY

# 3c. Git LFS prune — OPT-IN only (can reclaim GB but also confuses git/Cursor)
if [[ "${HALIM_GIT_LFS_PRUNE:-false}" == "true" ]] && git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo ""
  echo "🧹 Git LFS + index — drop Halim weight blobs (HALIM_GIT_LFS_PRUNE=true)…"
  PYTHONPATH="$ROOT" python3 - <<PY
from core.local_cleanup import prune_git_lfs_halim_blobs, _prune_downloads_halim_extras, prune_halim_colab_artifacts
lfs = prune_git_lfs_halim_blobs()
dl = _prune_downloads_halim_extras() if "${DEEP_SWEEP_PRUNE_DOWNLOADS:-false}" == "true" else 0
colab = prune_halim_colab_artifacts()
print(f"  LFS/index ~{lfs/(1024*1024):.0f}MB | Downloads ~{dl/(1024*1024):.0f}MB | Colab junk ~{colab/(1024*1024):.0f}MB")
PY
fi

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
