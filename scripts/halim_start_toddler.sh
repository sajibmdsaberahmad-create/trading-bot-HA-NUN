#!/usr/bin/env bash
# Install Colab toddler checkpoint + start Halim serve (LoRA or merged).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/halim_env.sh"

if [[ -n "${1:-}" ]]; then
  force=()
  [[ "${2:-}" == "--force" ]] && force=(--force)
  "$ROOT/scripts/halim_apply_colab_checkpoint.sh" "${force[@]}" "$1"
  exit $?
fi

"$ROOT/scripts/halim_apply_colab_checkpoint.sh" --if-new || true

CKPT="$ROOT/halim/data/checkpoints/toddler_v1"
if [[ ! -f "$CKPT/lora_adapter/adapter_model.safetensors" ]] && [[ ! -f "$CKPT/merged/model.safetensors" ]]; then
  echo "No checkpoint on disk — trying latest zip in Downloads/Drive…"
  "$ROOT/scripts/halim_apply_colab_checkpoint.sh" || exit 1
fi

exec "$ROOT/scripts/ensure_halim_active.sh" --serve-only --restart
