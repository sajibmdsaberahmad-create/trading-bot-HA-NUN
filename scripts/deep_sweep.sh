#!/usr/bin/env bash
# Deep sweep — safe by default. Aggressive system wipe is OPT-IN only.
#
# Safe (default):
#   ./scripts/deep_sweep.sh
# Aggressive (+ homebrew prune, all caches, git gc, git lfs prune):
#   DEEP_SWEEP_AGGRESSIVE=true HALIM_GIT_LFS_PRUNE=true ./scripts/deep_sweep.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"
if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

AGGRESSIVE="${DEEP_SWEEP_AGGRESSIVE:-false}"

echo "══════════════════════════════════════════════════════════════"
echo "  HANOON deep sweep (aggressive=${AGGRESSIVE})"
echo "══════════════════════════════════════════════════════════════"

if [[ "$AGGRESSIVE" == "true" ]]; then
  export HALIM_GIT_LFS_PRUNE=true
  export DEEP_SWEEP_PRUNE_DOWNLOADS=true
  export CLEANUP_DOWNLOAD_DMGS=true
fi

"$ROOT/scripts/sweep_device_junk.sh"

echo ""
echo "🧹 Workspace cleanup (jsonl trim only when aggressive or off-hours)…"
python3 - <<'PY'
from core.local_cleanup import cleanup_local_workspace, prune_halim_colab_artifacts
from core.smart_stack import live_ram_only
from core.config import BotConfig
import os

cfg = BotConfig()
aggressive = os.getenv("DEEP_SWEEP_AGGRESSIVE", "false").lower() in ("1", "true", "yes")
skip_jsonl = not aggressive
if live_ram_only(cfg) and os.getenv("MARKET_OPEN", "").lower() in ("1", "true", "yes"):
    skip_jsonl = True
ws = cleanup_local_workspace(aggressive=aggressive, skip_jsonl_trim=skip_jsonl)
colab = prune_halim_colab_artifacts()
total = (sum(ws.values()) + colab) / (1024 * 1024)
print(f"  Workspace ~{sum(ws.values())/(1024**2):.1f}MB | Colab ~{colab/(1024**2):.1f}MB | total ~{total:.1f}MB")
PY

if [[ "$AGGRESSIVE" == "true" ]]; then
  echo ""
  echo "⚠️  AGGRESSIVE: mac-cleaner all (homebrew prune, caches, git gc)…"
  python3 "$ROOT/mac-cleaner/clean.py" --clean all --yes 2>&1 | sed 's/^/  /'
else
  echo ""
  echo "ℹ️  Skipped mac-cleaner 'all' (set DEEP_SWEEP_AGGRESSIVE=true to enable)."
fi

echo ""
du -sh "$ROOT/.git" "$ROOT/.git/lfs" 2>/dev/null || true
du -sh "$ROOT/halim/data/checkpoints/toddler_v1" 2>/dev/null || true
echo ""
echo "✅ Deep sweep complete — reload Cursor if git/LFS was pruned"
