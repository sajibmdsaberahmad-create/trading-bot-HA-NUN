#!/usr/bin/env bash
# Install Halim LM runtime deps for this machine (MLX on Apple Silicon Mac, HF elsewhere).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

if [[ -d "$ROOT/venv" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
fi

case "${HALIM_LM_BACKEND:-mlx}" in
  mlx)
    echo "📦 Halim LM (Mac): installing mlx-lm + mlx…"
    pip install -q "mlx-lm>=0.19" "mlx>=0.21" 2>/dev/null || pip install "mlx-lm>=0.19" "mlx>=0.21"
    python3 -c "from mlx_lm import generate, load; print('✅ mlx-lm ready')" 2>/dev/null || {
      echo "⚠️  mlx-lm import failed — Halim chat needs Apple Silicon (arm64 Mac)."
      exit 1
    }
    ;;
  hf)
    echo "📦 Halim LM (HF): installing torch + transformers + peft…"
    pip install -q torch transformers peft 2>/dev/null || pip install torch transformers peft
    pip uninstall -y torchao 2>/dev/null || true
    python3 -c "import torch, transformers; print('✅ HF stack ready')" 2>/dev/null || {
      echo "⚠️  transformers import failed."
      exit 1
    }
    ;;
  *)
    echo "ℹ️  HALIM_LM_BACKEND=${HALIM_LM_BACKEND} — no LM packages installed."
    ;;
esac
