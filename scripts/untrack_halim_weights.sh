#!/usr/bin/env bash
# Stop Cursor first — it holds .git/index.lock while diffing safetensors.
# Untrack Halim weight blobs from git/LFS index; files stay on disk for MLX serve.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if pgrep -fi "Cursor" >/dev/null 2>&1; then
  echo "⚠️  Cursor is still running — quit Cursor first, then re-run this script."
  exit 1
fi

if [[ -f .git/index.lock ]]; then
  echo "⚠️  .git/index.lock present — wait a few seconds or remove after confirming no git process:"
  echo "    rm -f .git/index.lock"
  exit 1
fi

export PYTHONPATH="$ROOT"
export HALIM_GIT_LFS_PRUNE=true

echo "Untracking Halim checkpoints from git index (weights stay on disk)…"
python3 - <<'PY'
from core.local_cleanup import prune_git_lfs_halim_blobs, _path_size
from pathlib import Path

freed = prune_git_lfs_halim_blobs()
git_lfs = Path(".git/lfs")
print(f"  LFS cache: ~{_path_size(git_lfs) / (1024**3):.2f} GB")
print(f"  LFS prune reclaimed: ~{freed / (1024**2):.0f} MB")
PY

echo ""
echo "Tracked safetensors remaining:"
git ls-files 'halim/data/checkpoints/**/*.safetensors' | wc -l | xargs echo "  count:"
git ls-files 'halim/data/checkpoints/**/*.safetensors' || true

echo ""
du -sh .git .git/lfs halim/data/checkpoints/toddler_v1 2>/dev/null || true
echo ""
echo "✅ Done — restart Cursor; Halim serve unchanged if merged/ + lora_adapter/ exist."
