#!/usr/bin/env bash
# Remove local Ollama models (~8GB) — bot uses Groq/Gemini cloud council now.
set -euo pipefail

echo "🧹 Purging local Ollama models (cloud council is primary)..."

if command -v ollama >/dev/null 2>&1; then
  ollama stop 2>/dev/null || true
  while read -r name _; do
    [ -z "$name" ] || [ "$name" = "NAME" ] && continue
    echo "  Removing model: $name"
    ollama rm "$name" 2>/dev/null || true
  done < <(ollama list 2>/dev/null || true)
else
  echo "  ollama CLI not found — removing ~/.ollama/models directly"
fi

if [ -d "$HOME/.ollama/models" ]; then
  rm -rf "$HOME/.ollama/models/blobs" "$HOME/.ollama/models/manifests" 2>/dev/null || true
  mkdir -p "$HOME/.ollama/models/blobs" "$HOME/.ollama/models/manifests"
fi

BEFORE="${1:-}"
AFTER=$(du -sh "$HOME/.ollama" 2>/dev/null | cut -f1 || echo "?")
echo ""
echo "✅ Ollama models cleared — ~/.ollama now: $AFTER"
echo "   PPO weights in tradingbot/models/ were NOT touched."
echo ""
echo "Optional — uninstall Ollama app entirely:"
echo "  brew uninstall ollama"
