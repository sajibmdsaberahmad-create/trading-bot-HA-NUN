#!/usr/bin/env bash
# Mac Storage Cleaner — standalone (not HANOON). Use anytime on your Mac.
#
# Install global shortcut (optional):
#   ln -sf "$(cd "$(dirname "$0")/.." && pwd)/scripts/mac_clean.sh" ~/bin/mac-clean
#
# Examples:
#   ./scripts/mac_clean.sh                    # scan reclaimable space
#   ./scripts/mac_clean.sh --unload           # unload Ollama from RAM
#   ./scripts/mac_clean.sh --clean --yes      # safe default categories
#   ./scripts/mac_clean.sh --clean caches trash pip --yes
#   ./scripts/mac_clean.sh --clean downloads --older-than 90 --yes
#   ./scripts/mac_clean.sh --unload --purge --yes
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/tools/mac_cleaner/clean.py" "$@"
