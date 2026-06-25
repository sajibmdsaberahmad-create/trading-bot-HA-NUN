#!/usr/bin/env bash
# Recommended Ollama models for MacBook Air M2 8GB (HANOON compact tier).
set -euo pipefail

echo "=== HANOON 8GB Ollama setup ==="
echo "Pulling text models (reasoning → speed)..."
ollama pull phi4-mini    || echo "phi4-mini unavailable — skip"
ollama pull phi3:mini    || true
ollama pull qwen2.5:1.5b || true
ollama pull qwen2.5:0.5b  || true

echo "Pulling quantized vision (optional chart reads)..."
ollama pull llava-phi3:3.8b || echo "llava-phi3 unavailable — vision stays off until installed"

if ollama list 2>/dev/null | grep -qE '^llava:latest|^llava[[:space:]]'; then
  echo ""
  echo "WARNING: llava:latest uses ~4.7GB — too heavy alongside text model on 8GB."
  echo "Remove it after llava-phi3 is ready:  ollama rm llava:latest"
fi

if ollama list 2>/dev/null | grep -q '^qwen2.5:3b'; then
  echo ""
  echo "Note: qwen2.5:3b (~2GB) works but phi3:mini often reasons better at similar size."
fi

echo ""
echo "Installed models:"
ollama list
echo ""
echo "Set in .env (optional — RAM auto-tune picks best installed):"
echo "  OLLAMA_DYNAMIC_MODEL=true"
echo "  OLLAMA_MODEL=phi3:mini"
echo ""
echo "Check RAM while running:  ollama ps"
