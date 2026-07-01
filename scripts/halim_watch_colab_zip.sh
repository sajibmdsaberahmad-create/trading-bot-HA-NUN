#!/usr/bin/env bash
# Watch Downloads + Google Drive Halim/ for new halim_toddler_vN.zip → auto install.
# Run while downloading v4 from Colab/Drive: ./scripts/halim_watch_colab_zip.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INTERVAL="${HALIM_COLAB_WATCH_SEC:-15}"
MAX_MIN="${HALIM_COLAB_WATCH_MAX_MIN:-180}"

echo "👀 Watching for halim_toddler_v*.zip (every ${INTERVAL}s, max ${MAX_MIN}m)"
echo "   Dirs: ~/Downloads + Google Drive …/Halim/"
echo "   Ctrl+C to stop"
echo ""

end=$((SECONDS + MAX_MIN * 60))
while [[ $SECONDS -lt $end ]]; do
  out=$("$ROOT/scripts/halim_apply_colab_checkpoint.sh" --if-new 2>&1) || true
  if echo "$out" | grep -q '✅ Halim v'; then
    echo "$out"
    echo ""
    echo "✅ Watcher: install complete"
    exit 0
  fi
  sleep "$INTERVAL"
done

echo "⏱ Watch timeout — drop zip in Downloads and run:"
echo "   ./scripts/halim_apply_colab_checkpoint.sh"
exit 1
