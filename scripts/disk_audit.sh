#!/usr/bin/env bash
# Read-only disk audit — what is using space (no deletes).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "══════════════════════════════════════════════════════════════"
echo "  HANOON disk audit (read-only)"
echo "══════════════════════════════════════════════════════════════"
df -h / | tail -1
echo ""
echo "── Repo ──"
du -sh "$ROOT" "$ROOT/.git" "$ROOT/.git/lfs" "$ROOT/venv" \
  "$ROOT/halim/data/checkpoints" "$ROOT/models" "$ROOT/logs" 2>/dev/null || true
echo ""
echo "── Halim checkpoint ──"
du -sh "$ROOT/halim/data/checkpoints/toddler_v1/"* 2>/dev/null | sort -hr | head -8
echo ""
echo "── Git LFS tracked (should be 0 after ignore) ──"
git -C "$ROOT" ls-files 'halim/data/checkpoints/**/*.safetensors' 2>/dev/null | wc -l | xargs echo "safetensors files in index:"
echo ""
echo "── Auto-cleanup env (should be off during live) ──"
echo "  PERIODIC_CLEANUP_SEC=${PERIODIC_CLEANUP_SEC:-<unset>}"
echo "  AUTO_DISK_CLEANUP=${AUTO_DISK_CLEANUP:-<unset>}"
echo "  RAM_LIVE_ONLY=${RAM_LIVE_ONLY:-<unset>}"
echo "  HALIM_DEVICE_SWEEP_ON_START=${HALIM_DEVICE_SWEEP_ON_START:-false}"
echo ""
echo "── ~/Downloads large dirs ──"
du -sh "$HOME/Downloads"/* 2>/dev/null | sort -hr | head -12
