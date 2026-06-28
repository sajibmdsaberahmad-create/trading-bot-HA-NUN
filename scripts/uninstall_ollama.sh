#!/usr/bin/env bash
# Remove local Ollama entirely — HANOON uses M. A. Halim LM + Groq/Gemini council.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/purge_ollama_local.sh" 2>/dev/null || true

if command -v brew >/dev/null 2>&1; then
  brew services stop ollama 2>/dev/null || true
  if brew list ollama >/dev/null 2>&1; then
    echo "🗑  Uninstalling Ollama via Homebrew…"
    brew uninstall ollama 2>/dev/null || true
  fi
fi

pkill -f "ollama serve" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/homebrew.mxcl.ollama.plist" 2>/dev/null || true

echo "✅ Ollama removed — trading uses Halim LM (halim/serve) + cloud council only."
